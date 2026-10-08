using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment.NativeLogical;

/// <summary>Frozen public bytes only. Shared references charge one actual owned buffer until its last owner releases.</summary>
public sealed class NativeLogicalCaptureStore
{
    internal sealed class Buffer(byte[] bytes)
    { internal byte[] Bytes = bytes; internal int References = 1; }
    public sealed class EncodingLease : IDisposable
    {
        private NativeLogicalCaptureStore? store;
        internal Buffer Value;
        internal EncodingLease(NativeLogicalCaptureStore store, Buffer value) { this.store = store; Value = value; }
        public int ByteCount => Value.Bytes.Length;
        public void Dispose() { Interlocked.Exchange(ref store, null)?.Drop(Value); }
        internal bool BelongsTo(NativeLogicalCaptureStore owner) => ReferenceEquals(store, owner);
    }
    private sealed record CatalogStorage(Buffer Values, Buffer Index, NativeLogicalCatalog Catalog);
    private sealed record Entry(Buffer Buffer, NativeLogicalCapture Capture, NativeLogicalCatalog? Catalog, CatalogStorage? CatalogStorage, long Deadline);
    private sealed record Handle(string Client, Entry Entry, long Deadline);
    private readonly object gate = new();
    private readonly Dictionary<string, Entry> captures = new(StringComparer.Ordinal);
    private readonly Dictionary<NativeLogicalCatalog, CatalogStorage> catalogStorage = new();
    private readonly Dictionary<string, Handle> handles = new(StringComparer.Ordinal);
    private readonly NativeLogicalCursor cursors = new();
    private readonly NativeLogicalLimits limits;
    private readonly Func<long> clock;
    private long bytes;
    private ulong ordinal;
    private int buffers, catalogReads;
    public long ChargedBytes { get { lock (gate) return bytes; } }
    public int InFlightCatalogReads { get { lock (gate) return catalogReads; } }
    public int ChargedBuffers { get { lock (gate) return buffers + catalogStorage.Count * 2; } }
    public NativeLogicalCaptureStore(Func<long>? monotonicMs = null, NativeLogicalLimits? limits = null)
    { this.clock = monotonicMs ?? (() => Environment.TickCount64); this.limits = limits ?? new(); }
    public EncodingLease AcquireEncoding(ReadOnlySpan<byte> frozenBytes)
    {
        lock (gate)
        {
            SweepLocked();
            if (frozenBytes.Length <= 0 || frozenBytes.Length > limits.MaxCaptureBytes
                || bytes + frozenBytes.Length > limits.MaxRetainedBytes || buffers >= limits.MaxCaptures)
                throw new NativeLogicalException("capacity_exceeded", "Frozen buffer admission exceeds announced capacity.");
            byte[] owned = frozenBytes.ToArray();
            bytes += owned.Length; buffers++;
            return new(this, new(owned));
        }
    }
    public NativeLogicalCapturedProjection SealProjection(NativeLogicalFrozenProjection projection,
        PlayerEnvironmentSessionReference session, string generation, string scopeId, DateTimeOffset capturedAt)
    {
        bool hasCatalog = projection.Observation.Catalog.Status == "complete";
        byte[] owned = projection.DetachPayload();
        EncodingLease encoding;
        lock (gate)
        {
            SweepLocked();
            if (owned.Length <= 0 || owned.Length > limits.MaxCaptureBytes || bytes + owned.Length > limits.MaxRetainedBytes || buffers >= limits.MaxCaptures)
                throw new NativeLogicalException("capacity_exceeded", "Owned projection exceeds frozen buffer admission.");
            bytes += owned.Length; buffers++; encoding = new(this, new(owned));
        }
        using (encoding)
        {
            NativeLogicalCatalog? retainedCatalog = hasCatalog ? projection.Catalog : null;
            NativeLogicalCapture capture = Seal(encoding, projection.SnapshotId, session, generation, scopeId, capturedAt, retainedCatalog);
            return new(capture, retainedCatalog);
        }
    }
    public NativeLogicalCapture Seal(EncodingLease lease, string snapshotId,
        PlayerEnvironmentSessionReference session, string generation, string scopeId,
        DateTimeOffset capturedAt, NativeLogicalCatalog? catalog = null)
    {
        lock (gate)
        {
            SweepLocked();
            if (!lease.BelongsTo(this)) throw new NativeLogicalException("invalid_capture", "Encoding lease has already released its actual bytes.");
            if (captures.Keys.Concat(handles.Values.Select(h => h.Entry.Capture.CaptureId)).Distinct().Count() >= limits.MaxCaptures)
                throw new NativeLogicalException("capacity_exceeded", "Live capture identity count exceeded.");
            foreach (string field in new[] { snapshotId, generation, scopeId, session.RuntimeInstanceId, session.EnvironmentFingerprint }) NativeLogicalWire.Text(field, limits.MaxFieldBytes);
            if (catalog is not null && (catalog.Descriptor.SnapshotId != snapshotId || catalog.Descriptor.ScopeId != scopeId || catalog.Descriptor.StreamGeneration != generation))
                throw new NativeLogicalException("invalid_capture", "Catalog identity does not match the coherent capture.");
            if (ordinal == ulong.MaxValue) throw new NativeLogicalException("generation_changed", "Capture ordinal exhausted.");
            long deadline = checked(clock() + limits.RetentionMs);
            string id = NativeLogicalWire.Id("capture");
            string digest = NativeLogicalWire.Hash(lease.Value.Bytes);
            var envelope = new NativeLogicalCapture(NativeLogicalContract.CaptureSchema, id, snapshotId,
                NativeLogicalContract.Profile, session, generation, scopeId,
                (++ordinal).ToString(System.Globalization.CultureInfo.InvariantCulture), capturedAt,
                DateTimeOffset.UtcNow.AddMilliseconds(limits.RetentionMs), lease.Value.Bytes.Length,
                digest, cursors.Create(Binding(id, digest, generation, scopeId), 0, deadline));
            CatalogStorage? relation = null;
            if (catalog is not null)
            {
                if (lease.Value.Bytes.Length + catalog.StorageByteCount > limits.MaxCaptureBytes) throw new NativeLogicalException("capacity_exceeded", "Observation and full catalog exceed per-capture capacity.");
                if (catalogStorage.TryGetValue(catalog, out relation))
                { relation.Values.References++; relation.Index.References++; }
                else
                {
                    var buffers = catalog.StorageBuffers;
                    long relationBytes = (long)buffers.Values.Length + buffers.Index.Length;
                    if (bytes + relationBytes > limits.MaxRetainedBytes || lease.Value.Bytes.Length + relationBytes > limits.MaxCaptureBytes)
                        throw new NativeLogicalException("capacity_exceeded", "Observation and full retained catalog exceed the announced byte admission.");
                    relation = new(new(buffers.Values), new(buffers.Index), catalog);
                    bytes += relationBytes;
                    catalogStorage.Add(catalog, relation);
                }
            }
            lease.Value.References++;
            captures.Add(id, new(lease.Value, envelope, catalog, relation, deadline));
            catalog?.BindRetention(() => IsCatalogAvailable(catalog), () => BorrowCatalog(catalog));
            return envelope;
        }
    }
    private static string Binding(string id, string digest, string generation, string scope) => id + "|" + digest + "|" + generation + "|" + scope;
    public NativeLogicalReadChunk Read(string captureId, string cursor, int? maxBytes = null)
    {
        int budget = maxBytes ?? limits.MaxReadBytes;
        if (budget <= 0 || budget > limits.MaxReadBytes) throw new NativeLogicalException("invalid_limit", "Read budget is decoded capture bytes.");
        Entry entry; int offset, count, next; long continuationDeadline;
        lock (gate)
        {
            SweepLocked(); entry = Find(captureId);
            ulong position = cursors.Parse(cursor, Binding(captureId, entry.Capture.Sha256, entry.Capture.StreamGeneration, entry.Capture.ScopeId), clock());
            if (position >= (ulong)entry.Buffer.Bytes.Length) throw new NativeLogicalException("cursor_mismatch", "Read position is outside the capture.");
            offset = (int)position; count = Math.Min(budget, entry.Buffer.Bytes.Length - offset); next = offset + count;
            continuationDeadline = ActiveDeadline(entry);
            entry.Buffer.References++; // Actual buffer stays charged while the encoder reads it.
        }
        try
        {
            var response = new NativeLogicalReadChunk(captureId, entry.Capture.Sha256, offset,
                entry.Buffer.Bytes.Length, Convert.ToBase64String(entry.Buffer.Bytes, offset, count),
                next == entry.Buffer.Bytes.Length ? null : cursors.Create(Binding(captureId, entry.Capture.Sha256, entry.Capture.StreamGeneration, entry.Capture.ScopeId), (ulong)next, continuationDeadline), next == entry.Buffer.Bytes.Length);
            if (NativeLogicalWire.Encode(response).Length > limits.MaxEncodedReadBytes)
                throw new NativeLogicalException("capacity_exceeded", "Encoded read envelope exceeds announced limit.");
            return response;
        }
        finally { Drop(entry.Buffer); }
    }
    // A passive recorder receives copies of one coherent retained occurrence, never live state.
    public NativeLogicalFrozenInput ExportFrozen(string captureId, bool catalogRequired = true)
    {
        Entry entry;
        lock (gate)
        {
            SweepLocked(); entry = Find(captureId);
            if (catalogRequired && entry.Catalog is null) throw new NativeLogicalException("not_captured", "Full-reference export requires its retained complete catalog.");
            entry.Buffer.References++;
            if (entry.CatalogStorage is { } relation) { relation.Values.References++; relation.Index.References++; }
        }
        try
        {
            var observation = NativeLogicalDecoder.Decode<NativeLogicalObservation>(entry.Buffer.Bytes);
            if (catalogRequired && !observation.Completeness.FullReferenceComplete) throw new NativeLogicalException("not_captured", "A scoped omission cannot be exported as full-reference input.");
            return new(entry.Capture, entry.Buffer.Bytes, entry.Catalog is null ? null : new NativeLogicalFrozenCatalog(entry.Catalog.Descriptor, entry.CatalogStorage!.Values.Bytes));
        }
        finally { lock (gate) DropEntryLocked(entry); }
    }
    public NativeLogicalCatalog Catalog(string captureId)
    { lock (gate) { SweepLocked(); return Find(captureId).Catalog ?? throw new NativeLogicalException("not_captured", "No catalog was retained for this scope."); } }
    public NativeLogicalCatalog CatalogByReference(string catalogRef)
    {
        lock (gate)
        {
            SweepLocked();
            var catalog = captures.Values.Select(entry => entry.Catalog)
                .Concat(handles.Values.Select(handle => handle.Entry.Catalog))
                .FirstOrDefault(catalog => catalog?.Descriptor.CatalogRef == catalogRef);
            return catalog ?? throw new NativeLogicalException("expired", "No live capture or reader handle retains this catalog reference.");
        }
    }
    public bool IsAvailable(string captureId)
    { lock (gate) { SweepLocked(); return TryFind(captureId) is not null; } }
    public string Retain(string clientSessionId, string captureId)
    {
        lock (gate)
        {
            SweepLocked(); Entry entry = Find(captureId);
            if (handles.Count >= limits.MaxRetentionHandles || handles.Values.Count(h => h.Client == clientSessionId) >= limits.MaxClientRetentionHandles)
                throw new NativeLogicalException("capacity_exceeded", "Reader retention handle count exceeded.");
            string handle = NativeLogicalWire.Id("retention");
            entry.Buffer.References++;
            if (entry.CatalogStorage is { } relation) { relation.Values.References++; relation.Index.References++; }
            handles.Add(handle, new(clientSessionId, entry, checked(clock() + limits.RetentionMs)));
            return handle;
        }
    }
    public NativeLogicalRetentionReference RetainReference(string clientSessionId, string captureId)
    {
        lock (gate)
        {
            string handle = Retain(clientSessionId, captureId);
            Handle value = handles[handle];
            var capture = value.Entry.Capture;
            return new(handle, capture, cursors.Create(Binding(captureId, capture.Sha256, capture.StreamGeneration, capture.ScopeId), 0, value.Deadline), DateTimeOffset.UtcNow.AddMilliseconds(limits.RetentionMs));
        }
    }
    public NativeLogicalRetainReply RetainPublic(NativeLogicalRetainRequest request)
    {
        try { return new(NativeLogicalContract.RetainSchema, NativeLogicalContract.Profile, "retained", RetainReference(request.ClientSessionId, request.CaptureId), null); }
        catch (NativeLogicalException e) when (e.Code is "payload_expired" or "capacity_exceeded")
        { return new(NativeLogicalContract.RetainSchema, NativeLogicalContract.Profile, e.Code, null, e.Code); }
    }
    public NativeLogicalReleaseReply ReleasePublic(NativeLogicalReleaseRequest request)
    {
        lock (gate)
        {
            SweepLocked();
            if (handles.TryGetValue(request.RetentionHandleId, out var handle) && handle.Client != request.ClientSessionId)
                throw new NativeLogicalException("cursor_mismatch", "This retention handle belongs to another client.");
            Release(request.ClientSessionId, request.RetentionHandleId);
            return new(NativeLogicalContract.ReleaseSchema, NativeLogicalContract.Profile, "released", request.RetentionHandleId, true, null);
        }
    }
    public void Release(string clientSessionId, string retentionHandle)
    {
        lock (gate)
        {
            if (handles.TryGetValue(retentionHandle, out Handle? handle) && handle.Client == clientSessionId)
            { handles.Remove(retentionHandle); DropEntryLocked(handle.Entry); }
        }
    }
    // Owner-only release of the store's initial capture pin. Reader pins remain independent.
    public void ReleaseCapture(string captureId)
    { lock (gate) if (captures.Remove(captureId, out Entry? entry)) DropEntryLocked(entry); }
    public void Sweep() { lock (gate) SweepLocked(); }
    public NativeLogicalCapture CaptureDescriptor(string captureId)
    { lock (gate) { SweepLocked(); return Find(captureId).Capture; } }
    private Entry Find(string id) => TryFind(id) ?? throw new NativeLogicalException("payload_expired", "Original capture identity is immutable; its payload is no longer retained.");
    private Entry? TryFind(string id) => captures.GetValueOrDefault(id) ?? handles.Values.FirstOrDefault(h => h.Entry.Capture.CaptureId == id)?.Entry;
    private long ActiveDeadline(Entry entry) => Math.Max(entry.Deadline, handles.Values.Where(h => ReferenceEquals(h.Entry, entry)).Select(h => h.Deadline).DefaultIfEmpty(0).Max());
    private void SweepLocked()
    {
        long now = clock();
        foreach (var item in handles.Where(p => now >= p.Value.Deadline).ToArray()) { handles.Remove(item.Key); DropEntryLocked(item.Value.Entry); }
        foreach (var item in captures.Where(p => now >= p.Value.Deadline).ToArray()) { captures.Remove(item.Key); DropEntryLocked(item.Value); }
    }
    private bool IsCatalogAvailable(NativeLogicalCatalog catalog)
    { lock (gate) { SweepLocked(); return CatalogAvailableLocked(catalog); } }
    private bool CatalogAvailableLocked(NativeLogicalCatalog catalog) => captures.Values.Any(e => ReferenceEquals(e.Catalog, catalog)) || handles.Values.Any(h => ReferenceEquals(h.Entry.Catalog, catalog));
    internal IDisposable BorrowCatalog(NativeLogicalCatalog catalog)
    {
        lock (gate)
        {
            SweepLocked();
            if (!CatalogAvailableLocked(catalog) || !catalogStorage.TryGetValue(catalog, out var storage)) throw new NativeLogicalException("expired", "Catalog capture has no live retention.");
            if (catalogReads >= limits.MaxInFlightCatalogReads) throw new NativeLogicalException("capacity_exceeded", "In-flight catalog readers exceeded announced admission.");
            catalogReads++; storage.Values.References++; storage.Index.References++;
            return new CatalogReadLease(() => { lock (gate) { catalogReads--; DropCatalogLocked(storage); } });
        }
    }
    private sealed class CatalogReadLease(Action release) : IDisposable
    {
        private Action? callback = release;
        public void Dispose() => Interlocked.Exchange(ref callback, null)?.Invoke();
    }
    private void DropCatalogLocked(CatalogStorage relation)
    {
        if (--relation.Values.References == 0)
        {
            bytes -= relation.Values.Bytes.Length + relation.Index.Bytes.Length;
            relation.Index.References = 0;
            relation.Values.Bytes = relation.Index.Bytes = Array.Empty<byte>();
            catalogStorage.Remove(relation.Catalog); relation.Catalog.ExpireStorage();
        }
        else relation.Index.References--;
    }
    private void DropEntryLocked(Entry entry)
    {
        DropLocked(entry.Buffer);
        if (entry.CatalogStorage is { } relation) DropCatalogLocked(relation);
    }
    private void Drop(Buffer value) { lock (gate) DropLocked(value); }
    private void DropLocked(Buffer value)
    {
        if (--value.References != 0) return;
        bytes -= value.Bytes.Length; buffers--; value.Bytes = Array.Empty<byte>();
    }
}

public sealed class NativeLogicalFrozenInput
{
    private readonly byte[] captureBytes;
    public NativeLogicalCapture Capture { get; }
    public NativeLogicalFrozenCatalog? Catalog { get; }
    internal NativeLogicalFrozenInput(NativeLogicalCapture capture, byte[] bytes, NativeLogicalFrozenCatalog? catalog)
    { Capture = capture; captureBytes = (byte[])bytes.Clone(); Catalog = catalog; }
    public byte[] CopyCaptureBytes() => (byte[])captureBytes.Clone();
}
