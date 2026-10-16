using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment.NativeLogical;

/// <summary>A detached ordinary PE basis, never a native-logical observation or an Agent record.</summary>
public sealed record NativeLogicalSourcePublicBasis(string SnapshotId, byte[] Bytes, DateTimeOffset CapturedAt);
public sealed record NativeLogicalSourcePayload(string PayloadId, PlayerEnvironmentSessionReference Session,
    string StreamGeneration, string ScopeId,
    global::STS2Connector.PlayerEnvironment.Protocol.NativeLogicalCapture? NativeCapture, string? NativeMissingReason,
    string? PublicSnapshotId, DateTimeOffset CapturedAt, long PublicByteCount, string? PublicSha256, string? PublicMissingReason);

public sealed class NativeLogicalSourceFrozenPayload
{
    private readonly byte[]? publicBytes;
    public NativeLogicalSourcePayload Descriptor { get; }
    public NativeLogicalFrozenInput? Native { get; }
    internal NativeLogicalSourceFrozenPayload(NativeLogicalSourcePayload descriptor, NativeLogicalFrozenInput? native, byte[]? bytes)
    { Descriptor = descriptor; Native = native; publicBytes = bytes == null ? null : (byte[])bytes.Clone(); }
    public byte[]? CopyPublicBytes() => publicBytes == null ? null : (byte[])publicBytes.Clone();
}

public sealed partial class NativeLogicalCaptureStore
{
    // Source payloads share the existing buffer accounting, gate and lifetime owner.
    // No Source resource is admitted by the ordinary native Read/Catalog endpoints.
    private sealed class SourcePayloadEntry(NativeLogicalSourcePayload descriptor, Buffer? basis, Entry? native, long deadline)
    {
        internal readonly NativeLogicalSourcePayload Descriptor = descriptor;
        internal readonly Buffer? Basis = basis;
        internal readonly Entry? Native = native;
        internal readonly long Deadline = deadline;
        internal int References = 1;
    }
    private readonly Dictionary<string, SourcePayloadEntry> sourcePayloads = new(StringComparer.Ordinal);

    public NativeLogicalSourcePayload SealSourcePayload(NativeLogicalCapturedProjection? native,
        string? nativeMissingReason, NativeLogicalSourcePublicBasis? basis, string? publicMissingReason,
        PlayerEnvironmentSessionReference session, string generation, string scope, DateTimeOffset capturedAt)
    {
        lock (gate)
        {
            SweepLocked();
            if (sourcePayloads.Count + captures.Count >= limits.MaxCaptures)
                throw new NativeLogicalException("capacity_exceeded", "Original source resources exceed the existing identity bound.");
            Entry? retainedNative = native == null ? null : Find(native.Capture.CaptureId);
            if (retainedNative != null && (retainedNative.Capture.Session != session
                || retainedNative.Capture.StreamGeneration != generation || retainedNative.Capture.ScopeId != scope))
                throw new NativeLogicalException("source_payload_binding_mismatch", "Native and neutral parts must belong to the same original scope.");
            if (retainedNative != null && nativeMissingReason != null || basis != null && publicMissingReason != null)
                throw new NativeLogicalException("source_payload_binding_mismatch", "A captured part cannot also be missing.");
            long added = basis?.Bytes.LongLength ?? 0;
            long nativeBytes = retainedNative == null ? 0 : retainedNative.Buffer.Bytes.LongLength
                + (retainedNative.CatalogStorage?.Values.Bytes.LongLength ?? 0)
                + (retainedNative.CatalogStorage?.Index.Bytes.LongLength ?? 0);
            if (basis != null && added <= 0 || added + nativeBytes > limits.MaxCaptureBytes
                || bytes + added > limits.MaxRetainedBytes || basis != null && buffers >= limits.MaxCaptures)
                throw new NativeLogicalException("capacity_exceeded", "All original source parts are charged before copying.");
            foreach (string value in new[] { generation, scope, session.RuntimeInstanceId, session.EnvironmentFingerprint })
                NativeLogicalWire.Text(value, limits.MaxFieldBytes);
            if (basis != null) NativeLogicalWire.Text(basis.SnapshotId, limits.MaxFieldBytes);
            nativeMissingReason ??= retainedNative == null ? "source_native_payload_missing" : null;
            publicMissingReason ??= basis == null ? "source_public_basis_missing" : null;
            foreach (string? reason in new[] { nativeMissingReason, publicMissingReason })
                if (reason != null) NativeLogicalWire.Text(reason, limits.MaxMetadataFieldBytes);
            // No allocation occurs before the combined admission above.
            Buffer? frozen = basis == null ? null : new((byte[])basis.Bytes.Clone());
            if (frozen != null) { bytes += frozen.Bytes.Length; buffers++; }
            if (retainedNative != null) AddEntryReferenceLocked(retainedNative);
            string id = NativeLogicalWire.Id("source_payload");
            var descriptor = new NativeLogicalSourcePayload(id, session, generation, scope,
                retainedNative?.Capture, nativeMissingReason, basis?.SnapshotId,
                basis?.CapturedAt ?? capturedAt, added, frozen == null ? null : NativeLogicalWire.Hash(frozen.Bytes), publicMissingReason);
            sourcePayloads.Add(id, new(descriptor, frozen, retainedNative, checked(clock() + limits.RetentionMs)));
            return descriptor;
        }
    }

    public bool IsSourcePayloadAvailable(string id)
    { lock (gate) return sourcePayloads.TryGetValue(id, out var entry) && clock() < entry.Deadline; }

    public IDisposable RetainSourcePayload(string id)
    {
        lock (gate) { var entry = SourcePayloadLocked(id); entry.References++; return new SourcePayloadLease(this, id); }
    }
    private sealed class SourcePayloadLease(NativeLogicalCaptureStore owner, string id) : IDisposable
    {
        private NativeLogicalCaptureStore? remaining = owner;
        public void Dispose() => Interlocked.Exchange(ref remaining, null)?.ReleaseSourcePayload(id);
    }
    public bool ReleaseSourcePayload(string id)
    {
        lock (gate)
        {
            if (!sourcePayloads.TryGetValue(id, out var entry)) return false;
            if (--entry.References != 0) return true;
            sourcePayloads.Remove(id);
            if (entry.Basis != null) DropLocked(entry.Basis);
            if (entry.Native != null) DropEntryLocked(entry.Native);
            return true;
        }
    }
    public (NativeLogicalSourcePayload Descriptor, long ByteCount) SourcePayloadMetadata(string id)
    {
        lock (gate)
        {
            var entry = SourcePayloadLocked(id);
            return (entry.Descriptor, (entry.Basis?.Bytes.LongLength ?? 0)
                + (entry.Native?.Buffer.Bytes.LongLength ?? 0)
                + (entry.Native?.CatalogStorage?.Values.Bytes.LongLength ?? 0));
        }
    }
    public NativeLogicalSourceFrozenPayload ExportSourcePayload(string id)
    {
        SourcePayloadEntry entry;
        lock (gate) { entry = SourcePayloadLocked(id); entry.References++; }
        try
        {
            NativeLogicalFrozenInput? native = entry.Native is not { } original ? null : new(original.Capture,
                original.Buffer.Bytes, original.Catalog == null ? null
                    : new NativeLogicalFrozenCatalog(original.Catalog.Descriptor, original.CatalogStorage!.Values.Bytes));
            return new(entry.Descriptor, native, entry.Basis?.Bytes);
        }
        finally { ReleaseSourcePayload(id); }
    }
    private SourcePayloadEntry SourcePayloadLocked(string id) => sourcePayloads.TryGetValue(id, out var entry)
        && clock() < entry.Deadline ? entry : throw new NativeLogicalException("payload_expired", "The original source payload is no longer retained.");
    private void AddEntryReferenceLocked(Entry entry)
    {
        entry.Buffer.References++;
        if (entry.CatalogStorage is { } relation) { relation.Values.References++; relation.Index.References++; }
    }
}
