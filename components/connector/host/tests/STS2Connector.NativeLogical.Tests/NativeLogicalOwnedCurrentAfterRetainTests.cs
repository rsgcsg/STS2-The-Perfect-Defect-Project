using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector.Tests;

public sealed class NativeLogicalOwnedCurrentAfterRetainTests
{
    private sealed class Lifetime : INativeLogicalClientLifetimeDependency
    { public bool TryTouchActiveClient(string clientSessionId) => true; }

    [Fact]
    public void ClosureAfterRetainRollsBackNewHandleAndPreservesIndependentReader()
    {
        var store = new NativeLogicalCaptureStore(() => 0); var projector = new NativeLogicalProjector();
        var otherCapture = projector.Current(NativeLogicalCoreTests.Frame(), new("other", NativeLogicalProjector.ScopeFields, null),
            DateTimeOffset.UnixEpoch, 120000, () => 0, store).Capture!;
        var otherReader = store.RetainReference("other", otherCapture.CaptureId);
        store.ReleaseCapture(otherCapture.CaptureId);
        long before = store.ChargedBytes; int buffers = store.ChargedBuffers, checks = 0;
        // Check4 is after Seal; check5 is after the real RetainReference.
        store.BindClientLifetime(new Lifetime(), client => client == "other" || ++checks < 5);
        var reply = projector.CurrentOwned(NativeLogicalCoreTests.Frame(), new("reader", NativeLogicalProjector.ScopeFields, null),
            DateTimeOffset.UnixEpoch, 120000, () => 0, store);
        Assert.Equal(5, checks); Assert.Equal("failed", reply.Status); Assert.Equal("client_session_expired", reply.Reason);
        Assert.Null(reply.Retention); Assert.Null(reply.Capture);
        Assert.Equal(before, store.ChargedBytes); Assert.Equal(buffers, store.ChargedBuffers);
        Assert.True(store.Read(otherCapture.CaptureId, otherReader.ReadCursor).Complete);
        Assert.Single(store.Catalog(otherCapture.CaptureId).List(otherCapture.StreamGeneration).Actions);
        store.Release("other", otherReader.RetentionHandleId); Assert.Equal(0, store.ChargedBytes); Assert.Equal(0, store.ChargedBuffers);
    }
}
