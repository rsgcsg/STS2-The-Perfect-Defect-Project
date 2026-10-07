using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

public sealed class SealedObservationTests
{
    private static FrozenPublicObservation Frozen(byte[] bytes, string snapshot = "snapshot") =>
        new(bytes, snapshot, new("runtime", "environment"), "run", DateTimeOffset.UtcNow);

    [Fact]
    public void RepeatedAndPagedReadsRetainBytesWithoutCapturingNativeState()
    {
        var store = new SealedObservationStore();
        byte[] original = Encoding.UTF8.GetBytes(new string('x', 1023) + "中文" + new string('y', 2000));
        byte[] expected = (byte[])original.Clone();
        int nativeCaptures = 0;
        SealedObservationCapture capture = store.Capture(() => { nativeCaptures++; return Frozen(original); });
        Array.Fill(original, (byte)'z');
        var first = store.Read(capture.CaptureId, capture.FirstCursor, 1024);
        Assert.Equal(first, store.Read(capture.CaptureId, capture.FirstCursor, 1024));
        var bytes = new List<byte>();
        string? cursor = capture.FirstCursor;
        while (cursor != null)
        {
            var page = store.Read(capture.CaptureId, cursor, 1024);
            Assert.Equal(bytes.Count, page.Offset);
            bytes.AddRange(Convert.FromBase64String(page.DataBase64));
            Assert.Equal(page.End, page.NextCursor == null);
            cursor = page.NextCursor;
        }
        Assert.Equal(expected, bytes.ToArray());
        Assert.Equal(1, nativeCaptures);
        Assert.Equal(1, store.CaptureCount);
        Assert.Equal(1, capture.CaptureOrdinal);
        Assert.True(store.Release(capture.CaptureId).Released);
        Assert.True(store.Release(capture.CaptureId).Released);
        Assert.Equal("not_found", Assert.Throws<SealedObservationException>(() =>
            store.Read(capture.CaptureId, capture.FirstCursor)).Code);
        Assert.Equal(1, nativeCaptures);
    }

    [Fact]
    public void CurrentFreezeCopiesMutablePublicJsonAndExcludesContinuityMetadata()
    {
        var surface = new JsonObject { ["kind"] = "combat_turn", ["public_value"] = "original 中文" };
        TextMenuV2Snapshot snapshot = new("1.0.0", TextMenuV2Contract.SnapshotSchema,
            TextMenuV2Contract.Profile, "snapshot", 1, DateTimeOffset.UtcNow, "interactive", null,
            new("interaction", "combat_turn", "ready", null, "surface-schema",
                new(surface, new JsonObject { ["kind"] = "combat" }), Array.Empty<PlayerEnvironmentInteractionCapability>()),
            Array.Empty<PlayerEnvironmentReferent>(),
            new("complete", "public", "current", Array.Empty<string>(), Array.Empty<string>()),
            new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"),
            new("root", 1, "native", Array.Empty<TextMenuV2Selection>()),
            new("complete", 0, 0, "native", Array.Empty<TextMenuAction>()));
        var options = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
        var frozen = PlayerEnvironmentService.FreezePublicTextMenuV2(
            new(TextMenuV2Contract.ObservationContextSchema, snapshot, "run-private-metadata"), options);
        surface["public_value"] = "changed";
        JsonNode saved = JsonNode.Parse(frozen.Bytes)!;
        Assert.Equal("original 中文", saved["interaction"]!["content"]!["surface"]!["public_value"]!.GetValue<string>());
        Assert.Null(saved["game_continuity_id"]);
        Assert.DoesNotContain("run-private-metadata", Encoding.UTF8.GetString(frozen.Bytes));
        Assert.Equal("run-private-metadata", frozen.GameContinuityId);
    }

    [Fact]
    public void CursorCannotCrossCapsulesForgeOffsetsOrBypassLimits()
    {
        var store = new SealedObservationStore();
        var a = store.Capture(() => Frozen(new byte[2048]));
        var b = store.Capture(() => Frozen(new byte[2048]));
        Assert.Equal("cursor_mismatch", Assert.Throws<SealedObservationException>(() =>
            store.Read(b.CaptureId, a.FirstCursor)).Code);
        string forged = a.FirstCursor[..^1] + (a.FirstCursor.EndsWith('A') ? "B" : "A");
        Assert.Equal("cursor_mismatch", Assert.Throws<SealedObservationException>(() =>
            store.Read(a.CaptureId, forged)).Code);
        Assert.Equal("cursor_mismatch", Assert.Throws<SealedObservationException>(() =>
            store.Read(a.CaptureId, "not-a-cursor")).Code);
        foreach (int limit in new[] { -1, 0, 1023, 1048577 })
            Assert.Equal("invalid_limit", Assert.Throws<SealedObservationException>(() =>
                store.Read(a.CaptureId, a.FirstCursor, limit)).Code);
        Assert.Equal(2, store.CaptureCount);
    }

    [Fact]
    public void ExpirationUsesMonotonicClockAndCapacityNeverEvictsLiveCapsules()
    {
        long clock = 0;
        var store = new SealedObservationStore(() => clock, 4096, 4096, 2, 10);
        var a = store.Capture(() => Frozen(new byte[3072]));
        Assert.Equal("capacity", Assert.Throws<SealedObservationException>(() =>
            store.Capture(() => Frozen(new byte[2048]))).Code);
        Assert.NotNull(store.Read(a.CaptureId, a.FirstCursor));
        clock = 10;
        Assert.Equal("expired", Assert.Throws<SealedObservationException>(() =>
            store.Read(a.CaptureId, a.FirstCursor)).Code);
        var b = store.Capture(() => Frozen(new byte[4096]));
        Assert.Equal(4096, b.TotalBytes);
        clock = 20;
        var c = store.Capture(() => Frozen(new byte[4096]));
        Assert.Equal("not_found", Assert.Throws<SealedObservationException>(() =>
            store.Read(b.CaptureId, b.FirstCursor)).Code);
        Assert.NotNull(store.Read(c.CaptureId, c.FirstCursor));
    }

    [Fact]
    public void CountLimitStaleOversizeAndCaptureFailurePublishNoHandle()
    {
        var store = new SealedObservationStore(maxCapsuleBytes: 2048, maxRetainedBytes: 4096, maxCapsules: 1);
        Assert.Equal("stale_snapshot", Assert.Throws<SealedObservationException>(() =>
            store.Capture(() => Frozen(new byte[1024]), "old-snapshot")).Code);
        Assert.Equal("too_large", Assert.Throws<SealedObservationException>(() =>
            store.Capture(() => Frozen(new byte[2049]))).Code);
        Assert.Throws<InvalidOperationException>(() => store.Capture(() => throw new InvalidOperationException("failed capture")));
        var a = store.Capture(() => Frozen(new byte[1024]));
        Assert.Equal(4, a.CaptureOrdinal);
        Assert.Equal("capacity", Assert.Throws<SealedObservationException>(() =>
            store.Capture(() => Frozen(new byte[1024]))).Code);
        Assert.NotNull(store.Read(a.CaptureId, a.FirstCursor));
        store.Release(a.CaptureId);
        Assert.NotNull(store.Capture(() => Frozen(new byte[1024])));
    }

    [Fact]
    public void SerializationByteLimitFailsBeforePublishing()
    {
        using var stream = new BoundedObservationStream(10);
        stream.Write(new byte[8]);
        Assert.Equal("too_large", Assert.Throws<SealedObservationException>(() =>
            stream.Write(new byte[3], 0, 3)).Code);
        Assert.Equal(8, stream.Length);
    }
}
