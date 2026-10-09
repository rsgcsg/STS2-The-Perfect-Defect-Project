using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector.Tests;

public sealed class NativeLogicalOwnedCurrentTests
{
    private sealed class Lifetime : INativeLogicalClientLifetimeDependency
    { public bool TryTouchActiveClient(string clientSessionId) => true; }

    [Fact]
    public void ReleasedLegacyReaderStillFillsInitialCapturePoolBeforeExpiry()
    {
        long now = 0;
        var limits = new NativeLogicalLimits(MaxCaptures: 4);
        var store = new NativeLogicalCaptureStore(() => now, limits);
        var projector = new NativeLogicalProjector(limits);
        var request = new NativeLogicalCurrentRequest("reader", NativeLogicalProjector.ScopeFields, null);
        for (int i = 0; i < 4; i++)
        {
            var reply = projector.Current(NativeLogicalCoreTests.Frame(), request, DateTimeOffset.UnixEpoch, now + limits.RetentionMs, () => now, store);
            Assert.Equal("captured", reply.Status); Assert.Null(reply.Retention);
            string handle = store.Retain("reader", reply.Capture!.CaptureId);
            Assert.True(store.Read(reply.Capture.CaptureId, reply.Capture.ReadCursor).Complete);
            store.Release("reader", handle);
            Assert.True(store.IsAvailable(reply.Capture.CaptureId)); now += 250;
        }
        Assert.True(store.ChargedBytes > 0);
        Assert.Equal("capacity_exceeded", projector.Current(NativeLogicalCoreTests.Frame(), request, DateTimeOffset.UnixEpoch, now + limits.RetentionMs, () => now, store).Status);
        now += limits.RetentionMs; store.Sweep(); Assert.Equal(0, store.ChargedBytes);
    }

    [Fact]
    public void OwnedDiscardReadsPastPoolCapacityWithActualStorageReclaimed()
    {
        long now = 0;
        var limits = new NativeLogicalLimits(MaxCaptures: 4);
        var store = new NativeLogicalCaptureStore(() => now, limits);
        var projector = new NativeLogicalProjector(limits);
        var request = new NativeLogicalCurrentRequest("reader", NativeLogicalProjector.ScopeFields, null);
        string? snapshot = null, previousScope = null;
        for (int i = 0; i < 100; i++)
        {
            var reply = projector.CurrentOwned(NativeLogicalCoreTests.Frame(), request, DateTimeOffset.UnixEpoch, now + limits.RetentionMs, () => now, store);
            Assert.Equal("captured", reply.Status); Assert.NotNull(reply.Retention);
            Assert.Equal(reply.Capture, reply.Retention!.Capture);
            snapshot ??= reply.Capture!.SnapshotId; Assert.Equal(snapshot, reply.Capture!.SnapshotId);
            Assert.NotEqual(previousScope, reply.Capture.ScopeId); previousScope = reply.Capture.ScopeId;
            Assert.True(store.Read(reply.Capture.CaptureId, reply.Retention.ReadCursor).Complete);
            Assert.Single(store.Catalog(reply.Capture.CaptureId).List(reply.Capture.StreamGeneration).Actions);
            NativeLogicalDecoder.Decode<NativeLogicalCurrentReply>(NativeLogicalWire.Encode(reply));
            store.Release("reader", reply.Retention.RetentionHandleId);
            Assert.False(store.IsAvailable(reply.Capture.CaptureId));
            Assert.Equal(0, store.ChargedBytes); Assert.Equal(0, store.ChargedBuffers); now += 250;
        }
    }

    [Fact]
    public void OwnedHandleAdmissionRollbackPreservesOtherReaderStorage()
    {
        var limits = new NativeLogicalLimits(MaxRetentionHandles: 1);
        var store = new NativeLogicalCaptureStore(() => 0, limits); var projector = new NativeLogicalProjector(limits);
        var request = new NativeLogicalCurrentRequest("reader", NativeLogicalProjector.ScopeFields, null);
        var first = projector.CurrentOwned(NativeLogicalCoreTests.Frame(), request, DateTimeOffset.UnixEpoch, limits.RetentionMs, () => 0, store);
        long before = store.ChargedBytes; int buffers = store.ChargedBuffers;
        var failed = projector.CurrentOwned(NativeLogicalCoreTests.Frame(), request, DateTimeOffset.UnixEpoch, limits.RetentionMs, () => 0, store);
        Assert.Equal("capacity_exceeded", failed.Status); Assert.Null(failed.Capture); Assert.Null(failed.Retention);
        Assert.Equal(before, store.ChargedBytes); Assert.Equal(buffers, store.ChargedBuffers);
        Assert.True(store.IsAvailable(first.Capture!.CaptureId));
        store.Release("reader", first.Retention!.RetentionHandleId); Assert.Equal(0, store.ChargedBytes);
    }

    [Fact]
    public void OriginalClientClosureDuringTransferRollsBackCaptureAndHandle()
    {
        var store = new NativeLogicalCaptureStore(() => 0);
        int checks = 0;
        store.BindClientLifetime(new Lifetime(), _ => ++checks < 4);
        var reply = new NativeLogicalProjector().CurrentOwned(NativeLogicalCoreTests.Frame(),
            new("reader", NativeLogicalProjector.ScopeFields, null), DateTimeOffset.UnixEpoch, 120000, () => 0, store);
        Assert.Equal("failed", reply.Status); Assert.Equal("client_session_expired", reply.Reason);
        Assert.Null(reply.Capture); Assert.Null(reply.Retention); Assert.Equal(0, store.ChargedBytes);
    }

    [Fact]
    public void OwnedReleaseDoesNotReleaseIndependentReaderAndLegacyPins()
    {
        long now = 0;
        var store = new NativeLogicalCaptureStore(() => now); var projector = new NativeLogicalProjector();
        var owned = projector.CurrentOwned(NativeLogicalCoreTests.Frame(), new("reader", NativeLogicalProjector.ScopeFields, null),
            DateTimeOffset.UnixEpoch, 120000, () => now, store);
        var other = store.RetainReference("other", owned.Capture!.CaptureId);
        store.Release("reader", owned.Retention!.RetentionHandleId);
        Assert.True(store.IsAvailable(owned.Capture.CaptureId));
        Assert.Throws<NativeLogicalException>(() => store.ReleasePublic(new("reader", other.RetentionHandleId)));
        Assert.Single(store.Catalog(owned.Capture.CaptureId).List(owned.Capture.StreamGeneration).Actions);
        store.Release("other", other.RetentionHandleId); Assert.Equal(0, store.ChargedBytes);
        var legacy = projector.Current(NativeLogicalCoreTests.Frame(), new("reader", NativeLogicalProjector.ScopeFields, null),
            DateTimeOffset.UnixEpoch, 120000, () => now, store);
        Assert.Null(legacy.Retention); Assert.True(store.IsAvailable(legacy.Capture!.CaptureId));
        now = 120000; store.Sweep(); Assert.Equal(0, store.ChargedBytes);
    }
}
