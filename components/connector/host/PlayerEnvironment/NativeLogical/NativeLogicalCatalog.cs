using System;
using System.Buffers.Binary;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.Json;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment.NativeLogical;

/// <summary>One frozen full finite relation. Queries never consult native state.</summary>
public sealed class NativeLogicalCatalog
{
    private byte[]? relationBytes;
    private byte[]? relationIndex;
    private readonly int totalCount;
    private readonly object storageGate = new();
    private readonly NativeLogicalLimits limits;
    private readonly NativeLogicalCursor cursors = new();
    private readonly Func<long> clock;
    private Func<bool> available;
    private Func<IDisposable>? borrow;
    private readonly long deadline;
    public NativeLogicalCatalogDescriptor Descriptor { get; }
    public IReadOnlyList<NativeLogicalAction> Actions
    { get { using var lease = borrow?.Invoke(); Check(Descriptor.StreamGeneration); return Array.AsReadOnly(Values()); } }
    public NativeLogicalFrozenCatalog ExportFrozen()
    {
        using var read = borrow?.Invoke(); Check(Descriptor.StreamGeneration);
        var (bytes, _) = StorageBuffers;
        return new(Descriptor, bytes);
    }
    internal long StorageByteCount { get { lock (storageGate) return (relationBytes?.Length ?? 0) + (relationIndex?.Length ?? 0); } }
    internal (byte[] Values, byte[] Index) StorageBuffers
    { get { lock (storageGate) return (relationBytes ?? throw new NativeLogicalException("expired", "Catalog storage released."), relationIndex!); } }
    internal void ExpireStorage() { lock (storageGate) { relationBytes = null; relationIndex = null; } }
    private NativeLogicalAction ReadAction(byte[] bytes, byte[] index, int position)
    {
        int offset = BinaryPrimitives.ReadInt32BigEndian(index.AsSpan(position * 8, 4));
        int length = BinaryPrimitives.ReadInt32BigEndian(index.AsSpan(position * 8 + 4, 4));
        return Freeze(System.Text.Json.JsonSerializer.Deserialize<NativeLogicalAction>(bytes.AsSpan(offset, length), NativeLogicalWire.Options)!, limits.MaxFieldBytes);
    }
    private static int ActionBytes(byte[] index, int position) => BinaryPrimitives.ReadInt32BigEndian(index.AsSpan(position * 8 + 4, 4));
    private NativeLogicalAction[] Values()
    {
        var (bytes, index) = StorageBuffers;
        var values = new NativeLogicalAction[totalCount];
        for (int i = 0; i < totalCount; i++) values[i] = ReadAction(bytes, index, i);
        return values;
    }

    public NativeLogicalCatalog(string snapshotId, string generation, string scopeId,
        IEnumerable<NativeLogicalAction> members, long deadline, Func<long> clock,
        Func<bool>? available = null, NativeLogicalLimits? limits = null)
    {
        this.limits = limits ?? new(); this.clock = clock; this.deadline = deadline;
        this.available = available ?? (() => clock() < deadline);
        var frozen = new List<NativeLogicalAction>();
        foreach (var member in members)
        {
            if (frozen.Count >= this.limits.MaxActions) throw new NativeLogicalException("capacity_exceeded", "The full native relation exceeds its action limit.");
            frozen.Add(Freeze(member, this.limits.MaxFieldBytes));
        }
        NativeLogicalAction[] actions = frozen.ToArray();
        totalCount = actions.Length;
        string digest = Digest(actions);
        Descriptor = new(NativeLogicalWire.Id("catalog"), snapshotId, "complete", actions.Length,
            digest, "native_public_order", Array.AsReadOnly(new[] { "catalog", "resolve" }), scopeId, generation);
        // One encoded immutable relation plus a fixed 8-byte entry index; no second retained action graph.
        using var relation = new MemoryStream();
        relation.WriteByte((byte)'[');
        relationIndex = new byte[checked(actions.Length * 8)];
        for (int i = 0; i < actions.Length; i++)
        {
            var action = actions[i];
            if (NativeLogicalWire.Encode(Page(new[] { action }, actions.Length, digest,
                cursors.Create(Binding("{}"), (ulong)actions.Length, deadline), null)).Length > this.limits.MaxPageBytes)
                throw new NativeLogicalException("capacity_exceeded", "A member cannot fit an advertised maximum catalog page.");
            byte[] encoded = NativeLogicalWire.Encode(action);
            if (relation.Length + encoded.Length + relationIndex.Length + 2 > this.limits.MaxCaptureBytes)
                throw new NativeLogicalException("capacity_exceeded", "The complete retained relation exceeds capture capacity.");
            if (i > 0) relation.WriteByte((byte)',');
            BinaryPrimitives.WriteInt32BigEndian(relationIndex.AsSpan(i * 8, 4), checked((int)relation.Length));
            BinaryPrimitives.WriteInt32BigEndian(relationIndex.AsSpan(i * 8 + 4, 4), encoded.Length);
            relation.Write(encoded);
        }
        relation.WriteByte((byte)']');
        relationBytes = relation.ToArray();
    }
    internal void BindRetention(Func<bool> availability, Func<IDisposable> acquireRead) { available = availability; borrow = acquireRead; }
    private long CursorDeadline => Math.Max(deadline, checked(clock() + limits.RetentionMs));
    private string Binding(string filter) => Descriptor.StreamGeneration + "|" + Descriptor.CatalogRef + "|" + filter;
    private void Check(string generation)
    {
        if (generation != Descriptor.StreamGeneration) throw new NativeLogicalException("generation_mismatch", "Catalog belongs to another generation.");
        if (!available()) throw new NativeLogicalException("expired", "Catalog capture has no live retention.");
    }
    public NativeLogicalCatalogPage List(string generation, JsonElement? prefix = null,
        string? cursor = null, int? limit = null, int? maxPageBytes = null)
    {
        using var read = borrow?.Invoke();
        Check(generation);
        int pageLimit = limit ?? Math.Min(100, limits.MaxActions);
        if (pageLimit <= 0 || pageLimit > limits.MaxActions) throw new NativeLogicalException("invalid_limit", "Invalid page count.");
        int budget = maxPageBytes ?? limits.MaxPageBytes;
        if (budget <= 0 || budget > limits.MaxPageBytes) throw new NativeLogicalException("invalid_limit", "Invalid encoded page budget.");
        var expression = Expression.Parse(prefix, false, limits.MaxFieldBytes);
        var (relation, index) = StorageBuffers;
        List<int>? matchingRows = null;
        int filteredCount = totalCount; string digest = Descriptor.Digest!;
        if (!expression.Unfiltered)
        {
            matchingRows = new List<int>();
            for (int i = 0; i < totalCount; i++) if (expression.MatchesPrefix(ReadAction(relation, index, i))) matchingRows.Add(i);
            filteredCount = matchingRows.Count;
            digest = DigestSequence(matchingRows.Select(i => ReadAction(relation, index, i)), filteredCount);
        }
        ulong start = cursor is null ? 0 : cursors.Parse(cursor, Binding(expression.Canonical), clock());
        if (start > (ulong)filteredCount) throw new NativeLogicalException("cursor_mismatch", "Cursor position is outside this relation.");
        int position = checked((int)start); var selected = new List<NativeLogicalAction>();
        long memberBytes = 0; string? continuation = null;
        // NativeLogicalAction rows were encoded once into this immutable indexed relation.
        // An empty envelope plus the exact row byte lengths/commas gives the exact wire size.
        while (position < filteredCount && selected.Count < pageLimit)
        {
            int row = matchingRows is null ? position : matchingRows[position];
            string? next = position + 1 < filteredCount ? cursors.Create(Binding(expression.Canonical), (ulong)(position + 1), CursorDeadline) : null;
            long withMember = memberBytes + ActionBytes(index, row) + (selected.Count == 0 ? 0 : 1);
            int emptyEnvelopeBytes = NativeLogicalWire.Encode(Page(Array.Empty<NativeLogicalAction>(), filteredCount, digest, next, null)).Length;
            long required = emptyEnvelopeBytes + withMember;
            if (required > budget)
            {
                if (selected.Count == 0) return Page(Array.Empty<NativeLogicalAction>(), filteredCount, digest, cursor, checked((int)required)) with { Status = "page_budget_too_small" };
                break;
            }
            selected.Add(ReadAction(relation, index, row)); memberBytes = withMember; continuation = next; position++;
        }
        var result = Page(Array.AsReadOnly(selected.ToArray()), filteredCount, digest, continuation, null);
        // One final encoding check protects exact envelope accounting without growing-page reserialization.
        int encodedBytes = NativeLogicalWire.Encode(result).Length;
        if (encodedBytes > budget)
            return Page(Array.Empty<NativeLogicalAction>(), filteredCount, digest, cursor, encodedBytes) with { Status = "page_budget_too_small" };
        return result;
    }

    private NativeLogicalCatalogPage Page(IReadOnlyList<NativeLogicalAction> values, long filteredCount, string filteredDigest, string? cursor, int? minimum) =>
        new(NativeLogicalContract.CatalogPageSchema, "complete", Descriptor.CatalogRef, Descriptor.SnapshotId,
            Descriptor.StreamGeneration, totalCount, Descriptor.Digest!, filteredCount, filteredDigest, values, cursor, minimum);
    public NativeLogicalResolve Resolve(string generation, JsonElement expression)
    {
        try
        {
            using var read = borrow?.Invoke();
            Check(generation);
            var parsed = Expression.Parse(expression, true, limits.MaxFieldBytes);
            NativeLogicalAction? match = null;
            foreach (var action in Values())
            {
                if (!parsed.MatchesExact(action)) continue;
                if (match is not null) return new("ambiguous", null);
                match = action;
            }
            return match is null ? new("no_match", null) : new("unique", match);
        }
        catch (NativeLogicalException e) when (e.Code is "expired" or "generation_mismatch" or "invalid_expression" or "capacity_exceeded")
        { return new(e.Code == "capacity_exceeded" ? "invalid_expression" : e.Code, null); }
    }
    internal static NativeLogicalAction Freeze(NativeLogicalAction action, int maxFieldBytes)
    {
        foreach (string value in new[] { action.ActionId, action.Kind, action.Verb, action.Label, action.EffectDomain }) NativeLogicalWire.Text(value, maxFieldBytes);
        if (action.SubjectReferentId is not null) NativeLogicalWire.Text(action.SubjectReferentId, maxFieldBytes);
        if (action.Kind != "native_input") throw new NativeLogicalException("invalid_expression", "Action kind must be native_input.");
        var roles = new HashSet<string>(StringComparer.Ordinal);
        var args = new List<NativeLogicalArgument>();
        foreach (var arg in action.Arguments)
        {
            NativeLogicalWire.Text(arg.Role, maxFieldBytes); NativeLogicalWire.Text(arg.ReferentId, maxFieldBytes);
            if (!roles.Add(arg.Role)) throw new NativeLogicalException("invalid_expression", "Duplicate argument role.");
            args.Add(new(arg.Role, arg.ReferentId));
        }
        return action with { Arguments = Array.AsReadOnly(args.ToArray()) };
    }
    public static string Digest(IEnumerable<NativeLogicalAction> values)
    {
        var list = values.Select(v => Freeze(v, int.MaxValue)).ToArray();
        if (list.Select(a => a.ActionId).Distinct(StringComparer.Ordinal).Count() != list.Length)
            throw new NativeLogicalException("invalid_expression", "Duplicate action ID.");
        return DigestSequence(list, list.Length);
    }
    private static string DigestSequence(IEnumerable<NativeLogicalAction> values, int count)
    {
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        hash.AppendData(NativeLogicalWire.Utf8.GetBytes("sts2.native-logical.catalog.v1\0"));
        void Count(uint n) { Span<byte> b = stackalloc byte[4]; BinaryPrimitives.WriteUInt32BigEndian(b, n); hash.AppendData(b); }
        void Text(string text) { byte[] b = NativeLogicalWire.Utf8.GetBytes(text); Count(checked((uint)b.Length)); hash.AppendData(b); }
        Count(checked((uint)count));
        foreach (var a in values)
        {
            Text(a.ActionId); Text(a.Kind); Text(a.Verb); Text(a.Label);
            hash.AppendData(new[] { a.SubjectReferentId is null ? (byte)0 : (byte)1 });
            if (a.SubjectReferentId is not null) Text(a.SubjectReferentId);
            Count(checked((uint)a.Arguments.Count));
            foreach (var arg in a.Arguments) { Text(arg.Role); Text(arg.ReferentId); }
            Text(a.EffectDomain);
        }
        return Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
    }
    private sealed record Expression(bool HasVerb, string? Verb, bool HasSubject, string? Subject,
        bool HasArguments, IReadOnlyList<NativeLogicalArgument> Arguments, string Canonical)
    {
        internal bool Unfiltered => !HasVerb && !HasSubject && !HasArguments;
        internal bool MatchesPrefix(NativeLogicalAction a) => (!HasVerb || a.Verb == Verb)
            && (!HasSubject || a.SubjectReferentId == Subject)
            && (!HasArguments || Arguments.Count <= a.Arguments.Count && Arguments.Select((v, i) => v == a.Arguments[i]).All(v => v));
        internal bool MatchesExact(NativeLogicalAction a) => MatchesPrefix(a) && Arguments.Count == a.Arguments.Count;
        internal static Expression Parse(JsonElement? input, bool exact, int maxBytes)
        {
            if (input is null) { if (exact) throw Bad(); return new(false, null, false, null, false, Array.Empty<NativeLogicalArgument>(), "{}"); }
            JsonElement value = input.Value;
            if (value.ValueKind != JsonValueKind.Object) throw Bad();
            var fields = new HashSet<string>(); string? verb = null, subject = null; var args = new List<NativeLogicalArgument>();
            foreach (var field in value.EnumerateObject())
            {
                if (!fields.Add(field.Name)) throw Bad();
                switch (field.Name)
                {
                    case "verb": verb = String(field.Value, maxBytes); break;
                    case "subject_referent_id": subject = field.Value.ValueKind == JsonValueKind.Null ? null : String(field.Value, maxBytes); break;
                    case "arguments":
                        if (field.Value.ValueKind != JsonValueKind.Array) throw Bad();
                        var roles = new HashSet<string>();
                        foreach (var argument in field.Value.EnumerateArray())
                        {
                            if (argument.ValueKind != JsonValueKind.Object) throw Bad();
                            var argFields = new HashSet<string>(); string? role = null, id = null;
                            foreach (var p in argument.EnumerateObject())
                            {
                                if (!argFields.Add(p.Name)) throw Bad();
                                if (p.Name == "role") role = String(p.Value, maxBytes);
                                else if (p.Name == "referent_id") id = String(p.Value, maxBytes);
                                else throw Bad();
                            }
                            if (argFields.Count != 2 || role is null || id is null || !roles.Add(role)) throw Bad();
                            args.Add(new(role, id));
                        }
                        break;
                    default: throw Bad();
                }
            }
            if (exact && fields.Count != 3) throw Bad();
            var canonical = new Dictionary<string, object?>();
            if (fields.Contains("verb")) canonical.Add("verb", verb);
            if (fields.Contains("subject_referent_id")) canonical.Add("subject_referent_id", subject);
            if (fields.Contains("arguments")) canonical.Add("arguments", args);
            return new(fields.Contains("verb"), verb, fields.Contains("subject_referent_id"), subject,
                fields.Contains("arguments"), Array.AsReadOnly(args.ToArray()), System.Text.Encoding.UTF8.GetString(NativeLogicalWire.Encode(canonical)));
        }
        private static string String(JsonElement value, int maxBytes)
        {
            if (value.ValueKind != JsonValueKind.String) throw Bad();
            string text;
            try { text = value.GetString()!; NativeLogicalWire.Text(text, maxBytes); }
            catch (InvalidOperationException) { throw Bad(); }
            return text;
        }
        private static NativeLogicalException Bad() => new("invalid_expression", "Only the registered public structural grammar is accepted.");
    }
}

public sealed class NativeLogicalFrozenCatalog
{
    private readonly byte[] bytes;
    public NativeLogicalCatalogDescriptor Descriptor { get; }
    public string PayloadSha256 { get; }
    public int ByteCount => bytes.Length;
    internal NativeLogicalFrozenCatalog(NativeLogicalCatalogDescriptor descriptor, byte[] frozenBytes)
    { Descriptor = descriptor; bytes = (byte[])frozenBytes.Clone(); PayloadSha256 = NativeLogicalWire.Hash(bytes); }
    public byte[] CopyBytes() => (byte[])bytes.Clone();
}
