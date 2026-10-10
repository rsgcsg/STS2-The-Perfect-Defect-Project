using System;
using System.Collections.Generic;
using System.Linq;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment.NativeLogical;

using NativeLogicalCapture = global::STS2Connector.PlayerEnvironment.Protocol.NativeLogicalCapture;

/// <summary>Input already extracted at an actual native seam; no getters or dispatch callbacks.</summary>
public sealed record NativeLogicalSourceCompleteness(string Status, IReadOnlyList<string> Missing);

public sealed record NativeLogicalPublicFrame(string StreamGeneration,
    PlayerEnvironmentSessionReference Session, NativeLogicalOwnerOccurrence OwnerOccurrence,
    string Status, PlayerEnvironmentContent? Persistent, PlayerEnvironmentInteraction Interaction,
    IReadOnlyList<PlayerEnvironmentReferent> Referents,
    PlayerEnvironmentInformationPolicy InformationPolicy, IReadOnlyList<NativeLogicalLeaf> Leaves,
    NativeLogicalSourceCompleteness SourceCompleteness);

public sealed class NativeLogicalFrozenProjection
{
    private byte[]? payload;
    public string SnapshotId { get; }
    public long Revision { get; }
    public NativeLogicalCatalog Catalog { get; }
    // This is a copy, never the store's frozen buffer.
    public byte[] CopyPayload() => (byte[])Bytes.Clone();
    private byte[] Bytes => payload ?? throw new InvalidOperationException("Projection payload ownership was transferred to its capture store.");
    internal byte[] DetachPayload() => System.Threading.Interlocked.Exchange(ref payload, null) ?? throw new InvalidOperationException("Projection already transferred.");
    public NativeLogicalObservation Observation => System.Text.Json.JsonSerializer.Deserialize<NativeLogicalObservation>(Bytes, NativeLogicalWire.Options)!;
    internal ReadOnlySpan<byte> Payload => Bytes;
    internal NativeLogicalFrozenProjection(NativeLogicalObservation observation, NativeLogicalCatalog catalog, int limit)
    { SnapshotId = observation.SnapshotId; Revision = observation.Revision; Catalog = catalog; payload = NativeLogicalWire.EncodeBounded(observation, limit); }
}

/// <summary>Single source owner serializes calls. Scope projections share snapshot and action identity.</summary>
public sealed class NativeLogicalProjector
{
    private readonly object gate = new();
    private readonly NativeLogicalLimits limits;
    private string? signature, snapshotId;
    private NativeLogicalAction[] currentActions = Array.Empty<NativeLogicalAction>();
    private long revision;
    public NativeLogicalProjector(NativeLogicalLimits? limits = null) { this.limits = limits ?? new(); }
    public NativeLogicalFrozenProjection Freeze(NativeLogicalPublicFrame frame, IReadOnlyList<string> scope,
        string scopeId, DateTimeOffset observedAt, long retentionDeadline, Func<long> clock,
        Func<bool>? retained = null, int? maxEncodingBytes = null)
    {
        int byteLimit = maxEncodingBytes ?? limits.MaxCaptureBytes;
        if (byteLimit is < 1 || byteLimit > limits.MaxCaptureBytes) throw new NativeLogicalException("invalid_limit", "Optional producer encoding limit must stay inside normal capture admission.");
        if (frame.SourceCompleteness is null || frame.SourceCompleteness.Status != "complete" || frame.SourceCompleteness.Missing is null || frame.SourceCompleteness.Missing.Count != 0)
            throw new NativeLogicalException("source_capture_incomplete", "The native producer has not certified complete public fields and the full native relation.");
        ValidateScope(scope);
        ValidateFrameScalars(frame);
        // Freeze nested JsonNodes and mutable input collections before deriving identity.
        if (frame.Leaves.Count > limits.MaxActions) throw new NativeLogicalException("capacity_exceeded", "The native relation exceeds its announced limit.");
        byte[] facts = NativeLogicalWire.EncodeBounded(frame, byteLimit);
        NativeLogicalPublicFrame frozen = System.Text.Json.JsonSerializer.Deserialize<NativeLogicalPublicFrame>(facts, NativeLogicalWire.Options)!;
        if (frozen.Leaves.Count > limits.MaxActions) throw new NativeLogicalException("capacity_exceeded", "The native relation exceeds its announced limit.");

        if (facts.Length > byteLimit) throw new NativeLogicalException("capacity_exceeded", "Public facts exceed capture capacity.");
        var keys = frame.Leaves.Select(a => a.BindingKey).ToArray();
        foreach (string key in keys) NativeLogicalWire.Text(key, limits.MaxFieldBytes);
        string changed = NativeLogicalWire.Hash(facts) + NativeLogicalWire.Hash(NativeLogicalWire.EncodeBounded(keys, byteLimit));
        lock (gate)
        {
            if (signature != changed)
            {
                var publicIds = frozen.Referents.Select(r => r.ReferentId).ToHashSet(StringComparer.Ordinal);
                var actions = new List<NativeLogicalAction>();
                foreach (var leaf in frozen.Leaves)
                {
                    if (leaf.SubjectReferentId is not null && !publicIds.Contains(leaf.SubjectReferentId)
                        || leaf.Arguments.Any(a => !publicIds.Contains(a.ReferentId)))
                        throw new NativeLogicalException("invalid_expression", "Every action operand must be an authorized current public referent.");
                    actions.Add(NativeLogicalCatalog.Freeze(new(NativeLogicalWire.Id("action"), "native_input",
                        leaf.Verb, leaf.Label, leaf.SubjectReferentId, leaf.Arguments, leaf.EffectDomain), limits.MaxFieldBytes));
                }
                snapshotId = NativeLogicalWire.Id("snapshot"); currentActions = actions.ToArray(); signature = changed;
                revision = checked(revision + 1);
            }
            bool catalogIncluded = scope.Contains("catalog");
            var catalog = new NativeLogicalCatalog(snapshotId!, frame.StreamGeneration, scopeId,
                catalogIncluded ? currentActions : Array.Empty<NativeLogicalAction>(), retentionDeadline, clock, retained, limits with { MaxCaptureBytes = byteLimit });
            var missing = ScopeFields.Where(s => !scope.Contains(s)).ToArray();
            var descriptor = catalogIncluded ? catalog.Descriptor : catalog.Descriptor with { Status = "not_captured", TotalCount = null, Digest = null, AccessMethods = Array.Empty<string>() };
            var observation = new NativeLogicalObservation(PlayerEnvironmentContract.ProtocolVersion,
                NativeLogicalContract.ObservationSchema, NativeLogicalContract.Profile, snapshotId!, revision,
                observedAt, frozen.Status, scope.Contains("persistent") ? frozen.Persistent : null,
                scope.Contains("interaction") ? frozen.Interaction : null,
                scope.Contains("referents") ? frozen.Referents : Array.Empty<PlayerEnvironmentReferent>(),
                new(missing.Length == 0 ? "complete" : "partial", Array.AsReadOnly(scope.ToArray()), Array.AsReadOnly(missing), missing.Length == 0),
                frozen.Session, frozen.InformationPolicy, frozen.OwnerOccurrence, descriptor);
            var projection = new NativeLogicalFrozenProjection(observation, catalog, byteLimit);
            if (projection.Payload.Length > byteLimit) throw new NativeLogicalException("capacity_exceeded", "Encoded capture exceeds capacity.");
            return projection;
        }
    }
    // Consumes the standalone projection's actual buffer; no duplicate retained byte array remains.
    public NativeLogicalCapturedProjection Capture(NativeLogicalPublicFrame frame, IReadOnlyList<string> scope,
        string scopeId, DateTimeOffset observedAt, long retentionDeadline, Func<long> clock,
        NativeLogicalCaptureStore store, int? maxEncodingBytes = null)
    {
        var projection = Freeze(frame, scope, scopeId, observedAt, retentionDeadline, clock, maxEncodingBytes: maxEncodingBytes);
        return store.SealProjection(projection, frame.Session, frame.StreamGeneration, scopeId, observedAt);
    }
    // Source owner supplies an already-frozen current frame; this performs no native getter or input.
    public NativeLogicalCurrentReply Current(NativeLogicalPublicFrame frame,
        NativeLogicalCurrentRequest request, DateTimeOffset observedAt, long retentionDeadline,
        Func<long> clock, NativeLogicalCaptureStore store, string? gameContinuityId = null)
        => CurrentCore(frame, request, observedAt, retentionDeadline, clock, store, gameContinuityId, false);
    public NativeLogicalCurrentReply CurrentOwned(NativeLogicalPublicFrame frame,
        NativeLogicalCurrentRequest request, DateTimeOffset observedAt, long retentionDeadline,
        Func<long> clock, NativeLogicalCaptureStore store, string? gameContinuityId = null)
        => CurrentCore(frame, request, observedAt, retentionDeadline, clock, store, gameContinuityId, true);
    private NativeLogicalCurrentReply CurrentCore(NativeLogicalPublicFrame frame,
        NativeLogicalCurrentRequest request, DateTimeOffset observedAt, long retentionDeadline,
        Func<long> clock, NativeLogicalCaptureStore store, string? gameContinuityId, bool readerOwned)
    {
        try
        {
            NativeLogicalWire.Text(request.ClientSessionId, limits.MaxMetadataFieldBytes);
            if (gameContinuityId is not null) NativeLogicalWire.Text(gameContinuityId, limits.MaxMetadataFieldBytes);
            string scopeId = NativeLogicalWire.Id("scope");
            var projection = Freeze(frame, request.EagerScope, scopeId, observedAt, retentionDeadline, clock);
            if (request.ExpectedSnapshotId is not null && request.ExpectedSnapshotId != projection.SnapshotId)
                return new(NativeLogicalContract.CurrentSchema, NativeLogicalContract.Profile, "stale", null, null, null, "stale_snapshot");
            NativeLogicalCapturedProjection sealedProjection;
            NativeLogicalRetentionReference? retention = null;
            if (readerOwned)
                (sealedProjection, retention) = store.SealOwnedProjection(projection, frame.Session, frame.StreamGeneration, scopeId, observedAt, request.ClientSessionId);
            else sealedProjection = store.SealProjection(projection, frame.Session, frame.StreamGeneration, scopeId, observedAt, request.ClientSessionId);
            var context = new NativeLogicalObservationContext(NativeLogicalContract.ContextSchema,
                NativeLogicalContract.Profile, sealedProjection.Capture.SnapshotId, sealedProjection.Capture.CaptureId,
                gameContinuityId, frame.StreamGeneration, null);
            string status = request.EagerScope.Count == 4 ? "captured" : "partial";
            return new(NativeLogicalContract.CurrentSchema, NativeLogicalContract.Profile, status, context,
                sealedProjection.Capture, retention, status == "partial" ? "scope_omission" : null);
        }
        catch (NativeLogicalException e)
        {
            string status = e.Code is "capacity_exceeded" or "source_capture_incomplete" ? e.Code : "failed";
            return new(NativeLogicalContract.CurrentSchema, NativeLogicalContract.Profile, status, null, null, null, e.Code);
        }
    }
    private void ValidateFrameScalars(NativeLogicalPublicFrame frame)
    {
        void Text(string? value) { if (value is not null) NativeLogicalWire.Text(value, limits.MaxFieldBytes); }
        void Node(System.Text.Json.Nodes.JsonNode? node)
        {
            if (node is System.Text.Json.Nodes.JsonObject obj)
                foreach (var field in obj) { Text(field.Key); Node(field.Value); }
            else if (node is System.Text.Json.Nodes.JsonArray array) foreach (var child in array) Node(child);
            else if (node is System.Text.Json.Nodes.JsonValue scalar && scalar.TryGetValue<string>(out string? value)) Text(value);
        }
        foreach (string? value in new[] { frame.StreamGeneration, frame.Status, frame.Session.RuntimeInstanceId,
            frame.Session.EnvironmentFingerprint, frame.OwnerOccurrence.OwnerId, frame.OwnerOccurrence.OccurrenceId,
            frame.OwnerOccurrence.BindingRevision, frame.OwnerOccurrence.FocusReferentId, frame.OwnerOccurrence.FocusOccurrence,
            frame.InformationPolicy.Id, frame.InformationPolicy.Scope, frame.InformationPolicy.UnknownFieldBehavior,
            frame.Persistent?.ContentSchema, frame.Interaction.InteractionId, frame.Interaction.Kind, frame.Interaction.Stage,
            frame.Interaction.Prompt, frame.Interaction.ContentSchema }) Text(value);
        Node(frame.Persistent?.Content); Node(frame.Interaction.Content.Surface); Node(frame.Interaction.Content.Context);
        foreach (var referent in frame.Referents)
        {
            foreach (string? value in new[] { referent.ReferentId, referent.Role, referent.Kind, referent.Label,
                referent.State.ObservationBasis, referent.PropertiesSchema }) Text(value);
            Node(referent.Properties);
        }
        foreach (var capability in frame.Interaction.Capabilities)
        {
            Text(capability.Verb); Text(capability.SubjectRole); Text(capability.AvailabilityBasis);
            foreach (var arg in capability.Arguments) Text(arg.Role);
        }
        foreach (var leaf in frame.Leaves)
        {
            Text(leaf.Verb); Text(leaf.Label); Text(leaf.SubjectReferentId); Text(leaf.EffectDomain); Text(leaf.BindingKey);
            foreach (var arg in leaf.Arguments) { Text(arg.Role); Text(arg.ReferentId); }
        }
    }
    public static readonly IReadOnlyList<string> ScopeFields = Array.AsReadOnly(new[] { "persistent", "interaction", "referents", "catalog" });
    public static void ValidateScope(IReadOnlyList<string> scope)
    {
        int previous = -1;
        foreach (string field in scope)
        {
            int index = Array.IndexOf(ScopeFields.ToArray(), field);
            if (index <= previous || index < 0) throw new NativeLogicalException("unsupported_scope", "Scope must be a duplicate-free canonical ordered subset.");
            previous = index;
        }
    }
}

public sealed record NativeLogicalCapturedProjection(NativeLogicalCapture Capture, NativeLogicalCatalog? Catalog);
