using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

internal sealed class SealedObservationException(string code, string detail) : Exception(detail)
{
    internal string Code { get; } = code;
}

internal sealed record FrozenPublicObservation(
    byte[] Bytes, string SnapshotId, PlayerEnvironmentSessionReference Session,
    string? GameContinuityId, DateTimeOffset CapturedAt);

/// <summary>Only frozen public bytes. No native objects, callbacks or controller state are retained.</summary>
internal sealed class SealedObservationStore
{
    private sealed record Entry(byte[] Bytes, SealedObservationCapture Capture, long Deadline);
    private readonly object gate = new();
    private readonly Dictionary<string, Entry> entries = new(StringComparer.Ordinal);
    private readonly byte[] cursorKey = RandomNumberGenerator.GetBytes(32);
    private readonly string generationId = "sealed-generation-" + Guid.NewGuid().ToString("N");
    private readonly Func<long> monotonicMs;
    private readonly int maxCapsuleBytes, maxRetainedBytes, maxCapsules, ttlMs;
    private long captureCount;
    private int retainedBytes;

    internal SealedObservationStore(Func<long>? monotonicMs = null,
        int maxCapsuleBytes = SealedObservationContract.MaxCapsuleBytes,
        int maxRetainedBytes = SealedObservationContract.MaxRetainedBytes,
        int maxCapsules = SealedObservationContract.MaxCapsules,
        int ttlMs = SealedObservationContract.TtlMs)
    {
        if (maxCapsuleBytes <= 0 || maxRetainedBytes <= 0 || maxCapsules <= 0 || ttlMs <= 0)
            throw new ArgumentOutOfRangeException(nameof(maxCapsuleBytes));
        this.monotonicMs = monotonicMs ?? (() => Environment.TickCount64);
        this.maxCapsuleBytes = maxCapsuleBytes;
        this.maxRetainedBytes = maxRetainedBytes;
        this.maxCapsules = maxCapsules;
        this.ttlMs = ttlMs;
    }

    internal long CaptureCount => Interlocked.Read(ref captureCount);

    // Called synchronously on the game main thread; the callback serializes before returning.
    internal SealedObservationCapture Capture(Func<FrozenPublicObservation> capture,
        string? expectedSnapshotId = null)
    {
        long ordinal = Interlocked.Increment(ref captureCount);
        FrozenPublicObservation frozen = capture();
        if (expectedSnapshotId != null && frozen.SnapshotId != expectedSnapshotId)
            throw new SealedObservationException("stale_snapshot", "The expected public snapshot is no longer current.");
        if (frozen.Bytes.Length == 0 || frozen.Bytes.Length > maxCapsuleBytes)
            throw new SealedObservationException("too_large", "The public snapshot exceeds the capsule byte limit.");
        // Own an independent byte array even if a caller later mutates its buffer.
        byte[] bytes = (byte[])frozen.Bytes.Clone();
        string digest = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        string id = "capture-" + Guid.NewGuid().ToString("N");
        lock (gate)
        {
            long now = monotonicMs();
            PruneExpired(now);
            if (entries.Count >= maxCapsules || bytes.Length > maxRetainedBytes - retainedBytes)
                throw new SealedObservationException("capacity", "Release retained capsules or wait for expiration.");
            var envelope = new SealedObservationCapture(SealedObservationContract.Schema,
                SealedObservationContract.ReadProfile, TextMenuV2Contract.Profile, id,
                frozen.SnapshotId, frozen.Session, generationId, frozen.GameContinuityId,
                frozen.CapturedAt, DateTimeOffset.UtcNow.AddMilliseconds(ttlMs), bytes.Length,
                digest, Cursor(id, digest, 0), ordinal);
            entries.Add(id, new Entry(bytes, envelope, now + ttlMs));
            retainedBytes += bytes.Length;
            return envelope;
        }
    }

    internal SealedObservationChunk Read(string id, string cursor,
        int maxBytes = SealedObservationContract.DefaultChunkBytes)
    {
        if (maxBytes < SealedObservationContract.MinChunkBytes || maxBytes > SealedObservationContract.MaxChunkBytes)
            throw new SealedObservationException("invalid_limit", "Chunk size is outside the advertised limits.");
        lock (gate)
        {
            if (!entries.TryGetValue(id, out Entry? entry))
                throw new SealedObservationException("not_found", "No retained capsule has this ID.");
            if (monotonicMs() >= entry.Deadline)
            {
                Remove(id, entry);
                throw new SealedObservationException("expired", "The capsule retention deadline expired.");
            }
            int offset = DecodeCursor(cursor, id, entry.Capture.Sha256);
            if (offset < 0 || offset >= entry.Bytes.Length)
                throw new SealedObservationException("cursor_mismatch", "The cursor does not address a retained chunk.");
            int count = Math.Min(maxBytes, entry.Bytes.Length - offset);
            int next = offset + count;
            return new SealedObservationChunk(SealedObservationContract.ChunkSchema, id,
                entry.Capture.Sha256, offset, entry.Bytes.Length,
                Convert.ToBase64String(entry.Bytes, offset, count),
                next == entry.Bytes.Length ? null : Cursor(id, entry.Capture.Sha256, next),
                next == entry.Bytes.Length);
        }
    }

    internal SealedObservationRelease Release(string id)
    {
        lock (gate)
            if (entries.TryGetValue(id, out Entry? entry)) Remove(id, entry);
        return new(SealedObservationContract.ReleaseSchema, id, true);
    }

    private void PruneExpired(long now)
    {
        foreach (var pair in new List<KeyValuePair<string, Entry>>(entries))
            if (now >= pair.Value.Deadline) Remove(pair.Key, pair.Value);
    }
    private void Remove(string id, Entry entry)
    {
        entries.Remove(id);
        retainedBytes -= entry.Bytes.Length;
    }
    private string Cursor(string id, string digest, int offset)
    {
        string body = id + ":" + digest + ":" + offset.ToString(CultureInfo.InvariantCulture);
        string signature = Convert.ToHexString(HMACSHA256.HashData(cursorKey, Encoding.UTF8.GetBytes(body)));
        return Convert.ToBase64String(Encoding.UTF8.GetBytes(body + ":" + signature))
            .TrimEnd('=').Replace('+', '-').Replace('/', '_');
    }
    private int DecodeCursor(string cursor, string id, string digest)
    {
        try
        {
            if (cursor.Length == 0 || cursor.Length > 512) throw new FormatException();
            string padded = cursor.Replace('-', '+').Replace('_', '/');
            padded = padded.PadRight((padded.Length + 3) / 4 * 4, '=');
            string[] parts = Encoding.UTF8.GetString(Convert.FromBase64String(padded)).Split(':');
            if (parts.Length != 4 || parts[0] != id || parts[1] != digest
                || !int.TryParse(parts[2], NumberStyles.None, CultureInfo.InvariantCulture, out int offset))
                throw new FormatException();
            // Compare the whole canonical token: aliases and forged offsets are rejected.
            if (!CryptographicOperations.FixedTimeEquals(Encoding.ASCII.GetBytes(cursor),
                Encoding.ASCII.GetBytes(Cursor(id, digest, offset)))) throw new FormatException();
            return offset;
        }
        catch (FormatException)
        {
            throw new SealedObservationException("cursor_mismatch", "The opaque cursor is not valid for this capsule.");
        }
    }
}

/// <summary>Reject during serialization rather than allocating an unbounded snapshot.</summary>
internal sealed class BoundedObservationStream(int limit) : MemoryStream
{
    public override void Write(byte[] buffer, int offset, int count)
    {
        Check(count);
        base.Write(buffer, offset, count);
    }
    public override void Write(ReadOnlySpan<byte> buffer)
    {
        Check(buffer.Length);
        base.Write(buffer);
    }
    private void Check(int count)
    {
        if (count > limit - Length)
            throw new SealedObservationException("too_large", "The public snapshot exceeds the capsule byte limit.");
    }
}
