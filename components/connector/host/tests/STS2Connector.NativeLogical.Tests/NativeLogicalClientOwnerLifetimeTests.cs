using STS2Connector.Authority;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;
namespace STS2Connector.Tests;
public sealed class NativeLogicalClientOwnerLifetimeTests
{
    private sealed class Lifetime(MutationControllerCoordinator owner) : INativeLogicalClientLifetimeDependency
    { public bool TryTouchActiveClient(string client) => owner.TryTouchActiveClient(client); }
    [Fact]
    public void ValidOwnedPublicRenewMustTouchOnlyTheOriginalIdleDeadline()
    {
        long mono = 0;
        var authority = new MutationControllerCoordinator("runtime", monotonicClock: () => mono,
            clientIdleTtlMs: 100, enableDeadlineTimer: false);
        var client = authority.Register(new("reader", "review", "Review", "1")).Client;
        var store = new NativeLogicalCaptureStore();
        using var hub = new NativeLogicalPublicationHub(store,
            new[] { new NativeLogicalSeamCoverage("source", "1", "complete_at_seam") }, monotonicMs: () => mono);
        hub.BindClientLifetime(new Lifetime(authority));
        var attached = hub.Attach(new(client.ClientSessionId, new[] { "persistent" },
            new[] { new NativeLogicalSeamCoverage("source", "1", "complete_at_seam") }, "scoped")).Subscription!;
        mono = 90;
        Assert.Equal("renewed", hub.Renew(client.ClientSessionId, attached.SubscriptionId,
            attached.ScopeId, attached.StartingCursor).Status);
        mono = 100;
        Assert.True(authority.IsActiveClient(client.ClientSessionId));
        Assert.Null(authority.Snapshot().Controller);
    }

    [Fact]
    public void ClosedOriginalPublicRenewExpiresOnlyItsOwnedSubscriptionAndLeavesOtherLease()
    {
        var authority = new MutationControllerCoordinator("runtime", enableDeadlineTimer: false);
        string a = authority.Register(new("a", "test", "A", "1")).Client.ClientSessionId;
        string b = authority.Register(new("b", "test", "B", "1")).Client.ClientSessionId;
        var lease = authority.Acquire(new(b, null, null)).Controller!;
        var seam = new NativeLogicalSeamCoverage("source", "1", "complete_at_seam");
        var store = new NativeLogicalCaptureStore();
        using var hub = new NativeLogicalPublicationHub(store, new[] { seam });
        hub.BindClientLifetime(new Lifetime(authority), authority.IsActiveClient);
        NativeLogicalSubscription Attach(string client) => hub.Attach(new(client, new[] { "persistent" }, new[] { seam }, "scoped")).Subscription!;
        var sa = Attach(a); var sb = Attach(b);
        Assert.True(authority.Revoke(new("runtime", a)).Closed); // Deliberately do not run Hub cleanup yet.
        Assert.Equal("subscription_expired", hub.Renew(a, sa.SubscriptionId, sa.ScopeId, sa.StartingCursor).Status);
        Assert.Equal("subscription_expired", hub.Renew(a, sa.SubscriptionId, sa.ScopeId, sa.StartingCursor).Status);
        Assert.Equal("renewed", hub.Renew(b, sb.SubscriptionId, sb.ScopeId, sb.StartingCursor).Status);
        Assert.Equal(lease.ControllerLeaseId, authority.Snapshot().Controller!.ControllerLeaseId);
        Assert.Equal("subscription_expired", Assert.Throws<NativeLogicalException>(() => hub.Events(a, sa.SubscriptionId, sa.ScopeId, sa.StartingCursor)).Code);
        Assert.Empty(hub.Events(b, sb.SubscriptionId, sb.ScopeId, sb.StartingCursor).Events);
    }
    [Theory]
    [InlineData("client")] [InlineData("scope")] [InlineData("cursor")] [InlineData("subscription")]
    public void InvalidPublicRenewCannotExtendOriginalIdleDeadline(string error)
    {
        long mono = 0;
        var authority = new MutationControllerCoordinator("runtime", monotonicClock: () => mono,
            clientIdleTtlMs: 100, enableDeadlineTimer: false);
        string a = authority.Register(new("a", "test", "A", "1")).Client.ClientSessionId;
        var seam = new NativeLogicalSeamCoverage("source", "1", "complete_at_seam");
        using var hub = new NativeLogicalPublicationHub(new(), new[] { seam }, monotonicMs: () => mono);
        hub.BindClientLifetime(new Lifetime(authority), authority.IsActiveClient);
        var sub = hub.Attach(new(a, new[] { "persistent" }, new[] { seam }, "scoped")).Subscription!;
        mono = 90;
        if (error == "subscription") Assert.Equal("subscription_expired", hub.Renew(a, "foreign", sub.ScopeId, sub.StartingCursor).Status);
        else Assert.Throws<NativeLogicalException>(() => hub.Renew(error == "client" ? "foreign" : a,
            sub.SubscriptionId, error == "scope" ? "foreign" : sub.ScopeId, error == "cursor" ? "malformed" : sub.StartingCursor));
        mono = 100; Assert.False(authority.IsActiveClient(a));
    }
    private static NativeLogicalPublicFrame Frame() => new("generation", new("runtime", "fingerprint"),
        new("owner", "occurrence", "binding", null, null), "interactive", null,
        new("interaction", "held", "ready", null, "surface", new(new System.Text.Json.Nodes.JsonObject(), new System.Text.Json.Nodes.JsonObject()), Array.Empty<PlayerEnvironmentInteractionCapability>()),
        Array.Empty<PlayerEnvironmentReferent>(), new("fair", "current", false, "explicit"),
        Array.Empty<NativeLogicalLeaf>(), new("complete", Array.Empty<string>()));
    [Fact]
    public void ClosedStoreAdmissionAllocatesNoCurrentOrPublicReaderPinAndNeverRenews()
    {
        var authority = new MutationControllerCoordinator("runtime", enableDeadlineTimer: false);
        string a = authority.Register(new("a", "test", "A", "1")).Client.ClientSessionId;
        var store = new NativeLogicalCaptureStore();
        store.BindClientLifetime(new Lifetime(authority), authority.IsActiveClient);
        var projector = new NativeLogicalProjector();
        var source = projector.Capture(Frame(), NativeLogicalProjector.ScopeFields, "source", DateTimeOffset.UnixEpoch, 100_000, () => 0, store).Capture;
        long sourceCharge = store.ChargedBytes;
        authority.Revoke(new("runtime", a)); store.ExpireClient(a);
        Assert.Equal("client_session_expired", Assert.Throws<NativeLogicalException>(() => store.RetainPublic(new(a, source.CaptureId))).Code);
        var denied = projector.Current(Frame(), new(a, NativeLogicalProjector.ScopeFields, null), DateTimeOffset.UnixEpoch, 100_000, () => 0, store);
        Assert.Equal("failed", denied.Status); Assert.Equal("client_session_expired", denied.Reason); Assert.Null(denied.Capture);
        Assert.Equal(sourceCharge, store.ChargedBytes); Assert.True(store.Read(source.CaptureId, source.ReadCursor).Complete);
    }
    [Fact]
    public async Task AdmissionWinningBeforeClosureKeepsForeignPinAndCatalogLoanChargedAndSourceCaptureUntouched()
    {
        var authority = new MutationControllerCoordinator("runtime", enableDeadlineTimer: false);
        string a = authority.Register(new("a", "test", "A", "1")).Client.ClientSessionId;
        string b = authority.Register(new("b", "test", "B", "1")).Client.ClientSessionId;
        var store = new NativeLogicalCaptureStore();
        var admitted = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var completeAdmission = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        int checks = 0;
        bool Check(string client)
        {
            bool active = authority.IsActiveClient(client);
            if (client == a && active && Interlocked.Increment(ref checks) == 2)
            { admitted.SetResult(); completeAdmission.Task.GetAwaiter().GetResult(); }
            return active;
        }
        store.BindClientLifetime(new Lifetime(authority), Check);
        var projector = new NativeLogicalProjector();
        var source = projector.Capture(Frame(), NativeLogicalProjector.ScopeFields, "source", DateTimeOffset.UnixEpoch, 100_000, () => 0, store).Capture;
        long sourceCharge = store.ChargedBytes;
        var allowCleanup = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var cleanupDone = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        authority.ClientClosed += closure => { allowCleanup.Task.GetAwaiter().GetResult(); store.ExpireClient(closure.ClientSessionId); cleanupDone.SetResult(); };
        var current = Task.Run(() => projector.Current(Frame(), new(a, NativeLogicalProjector.ScopeFields, null), DateTimeOffset.UnixEpoch, 100_000, () => 0, store));
        await admitted.Task;
        authority.Revoke(new("runtime", a)); completeAdmission.SetResult();
        var capture = (await current).Capture!;
        Assert.NotNull(capture);
        var retained = store.RetainPublic(new(b, capture.CaptureId)).Retention!;
        var catalogLoan = store.BorrowCatalog(store.Catalog(capture.CaptureId));
        long allCharge = store.ChargedBytes;
        allowCleanup.SetResult(); await cleanupDone.Task;
        Assert.Equal(allCharge, store.ChargedBytes);
        Assert.True(store.Read(capture.CaptureId, retained.ReadCursor).Complete);
        Assert.True(store.Read(source.CaptureId, source.ReadCursor).Complete);
        store.ReleasePublic(new(b, retained.RetentionHandleId));
        Assert.True(store.ChargedBytes > sourceCharge); // The admitted catalog reader is still borrowing its actual bytes.
        catalogLoan.Dispose(); Assert.Equal(sourceCharge, store.ChargedBytes);
        Assert.True(store.Read(source.CaptureId, source.ReadCursor).Complete);
    }
}
