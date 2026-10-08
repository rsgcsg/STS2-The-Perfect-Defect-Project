using System.Text;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector.Tests;

public sealed class NativeLogicalPublicationTests
{
    private static readonly NativeLogicalSeamCoverage CompleteSeam = new("owner", "1", "complete_at_seam");
    private static string Id(int i) => i.ToString("x32");
    private static NativeLogicalSubscription Attach(NativeLogicalPublicationHub hub, string client = "client", string[]? scope = null) =>
        hub.Attach(new(client, scope ?? NativeLogicalProjector.ScopeFields.ToArray(), new[] { CompleteSeam }, scope is null ? "full_reference" : "scoped")).Subscription!;
    private static NativeLogicalProjectionOutcome Missing(NativeLogicalSubscription sub, string reason = "not_eager") => new(sub.ScopeId, null, reason);
    [Fact]
    public async Task AttachAboveContiguousWatermarkSkipsAllReservedPreAttachPositions()
    {
        var store = new NativeLogicalCaptureStore(() => 0); using var hub = new NativeLogicalPublicationHub(store, new[] { CompleteSeam }, () => 0);
        var first = hub.Reserve("owner", "enter", "observation");
        var sub = Attach(hub);
        var wait = hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(1), "any_event", 1000);
        Assert.False(wait.IsCompleted); hub.Complete(first, Array.Empty<NativeLogicalProjectionOutcome>()); Assert.False(wait.IsCompleted);
        var next = hub.Reserve("owner", "change", "observation"); hub.Complete(next, new[] { Missing(sub) });
        var reply = await wait; Assert.Equal("2", reply.Event!.Event.PublicationIndex); Assert.Equal("event", reply.Status);
    }
    [Fact]
    public async Task CompletionOrderTimeoutAndMetadataOverflowHaveExplicitAccounting()
    {
        long now = 0; var store = new NativeLogicalCaptureStore(() => now); using var hub = new NativeLogicalPublicationHub(store, new[] { CompleteSeam }, () => now, new(MaxEvents: 2, EncodingDeadlineMs: 100));
        var sub = Attach(hub); var first = hub.Reserve("owner", "enter", "observation"); var second = hub.Reserve("owner", "change", "observation");
        hub.Complete(second, new[] { Missing(sub) }); Assert.Empty(hub.Events("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor).Events);
        now = 100; hub.Tick(); var batch = hub.Events("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor);
        Assert.Equal(new[] { "1", "2" }, batch.Events.Select(e => e.Event.PublicationIndex)); Assert.Equal("encoding_timeout", batch.Events[0].Event.MissingReason);
        Assert.False(hub.Complete(first, new[] { Missing(sub, "late") }));
        var third = hub.Reserve("owner", "change", "observation"); hub.Complete(third, new[] { Missing(sub) });
        batch = hub.Events("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor); Assert.Equal("1", batch.Gap!.FromPublicationIndex); Assert.Equal("1", batch.Gap.ThroughPublicationIndex); Assert.Equal(2, hub.MetadataCount);
        Assert.Equal("gap", (await hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(1), "any_event", 100)).Status);
    }
    [Fact]
    public void SubscriptionScopeCursorBoundariesAndCoverageFailClosed()
    {
        var store = new NativeLogicalCaptureStore(() => 0); using var hub = new NativeLogicalPublicationHub(store, new[] { CompleteSeam, new NativeLogicalSeamCoverage("sample", "1", "sampled") }, () => 0);
        var a = Attach(hub); var b = Attach(hub, scope: new[] { "interaction" });
        Assert.Throws<NativeLogicalException>(() => hub.Events("client", b.SubscriptionId, b.ScopeId, a.StartingCursor));
        Assert.Throws<NativeLogicalException>(() => hub.Events("other", a.SubscriptionId, a.ScopeId, a.StartingCursor));
        Assert.Throws<NativeLogicalException>(() => hub.Events("client", a.SubscriptionId, b.ScopeId, a.StartingCursor));
        Assert.Equal("coverage_insufficient", hub.Attach(new("client", NativeLogicalProjector.ScopeFields, new[] { new NativeLogicalSeamCoverage("sample", "1", "sampled") }, "full_reference")).Status);
        Assert.Equal("unsupported_scope", hub.Attach(new("client", new[] { "catalog", "interaction" }, new[] { CompleteSeam }, "scoped")).Status);
        Assert.Equal("unsupported_seam", hub.Attach(new("client", NativeLogicalProjector.ScopeFields, new[] { CompleteSeam with { Version = "2" } }, "full_reference")).Status);
    }
    [Fact]
    public void ImmutableProjectionIdentitySurvivesPayloadExpiryAndRepeatedRetrieval()
    {
        long now = 0; var store = new NativeLogicalCaptureStore(() => now, new(RetentionMs: 50)); using var hub = new NativeLogicalPublicationHub(store, new[] { CompleteSeam }, () => now, new(RetentionMs: 1000));
        var a = Attach(hub); var b = Attach(hub, "second", new[] { "interaction" });
        using var encoding = store.AcquireEncoding(Encoding.UTF8.GetBytes("frozen-public"));
        var captureA = store.Seal(encoding, "snapshot", new("runtime", "fp"), hub.StreamGeneration, a.ScopeId, DateTimeOffset.UnixEpoch);
        var captureB = store.Seal(encoding, "snapshot", new("runtime", "fp"), hub.StreamGeneration, b.ScopeId, DateTimeOffset.UnixEpoch);
        var reservation = hub.Reserve("owner", "enter", "observation");
        hub.Complete(reservation, new[] { new NativeLogicalProjectionOutcome(a.ScopeId, captureA, null), new NativeLogicalProjectionOutcome(b.ScopeId, captureB, null) });
        var original = hub.Events("client", a.SubscriptionId, a.ScopeId, a.StartingCursor).Events[0];
        var scoped = hub.Events("second", b.SubscriptionId, b.ScopeId, b.StartingCursor).Events[0];
        Assert.Equal(original.Event.PublicationIndex, scoped.Event.PublicationIndex); Assert.NotEqual(original.Event.ScopeId, scoped.Event.ScopeId);
        Assert.Equal(original, hub.Events("client", a.SubscriptionId, a.ScopeId, a.StartingCursor).Events[0]);
        hub.Detach("client", a.SubscriptionId); Assert.True(store.IsAvailable(captureB.CaptureId));
        now = 50; store.Sweep(); var expired = hub.Events("second", b.SubscriptionId, b.ScopeId, b.StartingCursor).Events[0];
        Assert.Equal(scoped.Event, expired.Event); Assert.Equal("payload_expired", expired.Availability);
        Assert.Equal(encoding.ByteCount, store.ChargedBytes); encoding.Dispose(); Assert.Equal(0, store.ChargedBytes);
    }
    [Fact]
    public async Task AwaitAdmissionCountTimeoutCancelDetachAndGeneration()
    {
        long now = 0; var store = new NativeLogicalCaptureStore(() => now); using var hub = new NativeLogicalPublicationHub(store, new[] { CompleteSeam }, () => now, new(MaxWaiters: 2, MaxClientWaiters: 1));
        var sub = Attach(hub); var one = hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(1), "observation", 100);
        Assert.Equal("capacity_exceeded", (await hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(2), "observation", 100)).Status);
        Assert.False(hub.CancelWait("other", sub.SubscriptionId, Id(1))); now = 100; hub.Tick(); Assert.Equal("timeout", (await one).Status); Assert.Equal(0, hub.WaiterCount);
        Assert.Throws<NativeLogicalException>(() => { _ = hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(1), "any_event", 100); });
        using var cts = new CancellationTokenSource(); var cancelled = hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(3), "terminal", 100, cancellationToken: cts.Token); cts.Cancel();
        Assert.Equal("cancelled", (await cancelled).Status); Assert.Equal(0, hub.WaiterCount);
        var detach = hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(4), "terminal", 100); hub.Detach("client", sub.SubscriptionId); Assert.Equal("cancelled", (await detach).Status);
        sub = Attach(hub); var generation = hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(5), "terminal", 100); hub.ChangeGeneration(); Assert.Equal("generation_changed", (await generation).Status);
    }
    [Fact]
    public async Task WaitIdMetadataCapacityRejectsNewIdsButNeverEvictsOldIds()
    {
        var store = new NativeLogicalCaptureStore(() => 0); using var hub = new NativeLogicalPublicationHub(store, new[] { CompleteSeam }, () => 0,
            new(MaxWaitIdsPerSubscription: 2, MaxWaitIdMetadataBytes: 256));
        var sub = Attach(hub);
        Assert.Equal("timeout", (await hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(1), "any_event", 0)).Status);
        Assert.Equal("timeout", (await hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(2), "any_event", 0)).Status);
        Assert.Equal(256, hub.WaitIdMetadataBytes);
        Assert.Equal("capacity_exceeded", (await hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(3), "any_event", 0)).Status);
        Assert.Throws<NativeLogicalException>(() => { _ = hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(1), "any_event", 0); });
        hub.Detach("client", sub.SubscriptionId); Assert.Equal(0, hub.WaitIdMetadataBytes);
    }
    [Fact]
    public async Task ObserverSurvivesControlStopWhileOwnedWaitCancels()
    {
        var control = new TestControls(); var store = new NativeLogicalCaptureStore(() => 0); using var hub = new NativeLogicalPublicationHub(store, new[] { CompleteSeam }, () => 0, controls: control);
        var sub = Attach(hub); var observer = hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(1), "any_event", 1000);
        var owned = hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(2), "any_event", 1000, new("lease", 1));
        control.Lose(); Assert.Equal("control_lost", (await owned).Reason); Assert.False(observer.IsCompleted);
        var reservation = hub.Reserve("owner", "changed", "observation"); hub.Complete(reservation, new[] { Missing(sub) }); Assert.Equal("event", (await observer).Status);
        Assert.Equal("cancelled", (await hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(3), "any_event", 1000, new("lease", 1))).Status);
    }
    [Fact]
    public async Task ConcurrentPublicationAndAwaitNeverMissesOccurrence()
    {
        for (int i = 0; i < 100; i++)
        {
            var store = new NativeLogicalCaptureStore(); using var hub = new NativeLogicalPublicationHub(store, new[] { CompleteSeam }); var sub = Attach(hub);
            var reservation = hub.Reserve("owner", "change", "observation");
            Task publish = Task.Run(() => hub.Complete(reservation, new[] { Missing(sub) }));
            Task<NativeLogicalAwaitReply> wait = Task.Run(async () => await hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(1), "any_event", 1000));
            await publish; Assert.Equal("event", (await wait).Status); Assert.Equal(0, hub.WaiterCount);
        }
    }
    [Fact]
    public async Task SubscriptionExpiryCompletesWaitWithoutSecondExternalTick()
    {
        var store = new NativeLogicalCaptureStore(); using var hub = new NativeLogicalPublicationHub(store, new[] { CompleteSeam }, limits: new(RetentionMs: 60)); var sub = Attach(hub);
        var wait = hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(1), "terminal", 1000);
        Assert.Equal("subscription_expired", (await wait.WaitAsync(TimeSpan.FromSeconds(2))).Status);
    }
    [Fact]
    public async Task AtomicInitialAttachmentReservesOnlyItsOwnFirstObservation()
    {
        var store = new NativeLogicalCaptureStore(() => 0); using var hub = new NativeLogicalPublicationHub(store, new[] { CompleteSeam }, () => 0);
        var old = Attach(hub, "old");
        var initial = hub.AttachWithInitialReservation(new("client", NativeLogicalProjector.ScopeFields, new[] { CompleteSeam }, "full_reference"), "owner");
        var sub = initial.Attach.Subscription!; var reservation = initial.InitialReservation!;
        Assert.Equal("1", reservation.PublicationIndex); Assert.Single(reservation.Subscriptions);
        var wait = hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(1), "observation", 1000);
        hub.Complete(reservation, new[] { Missing(sub, "capture_failed") });
        Assert.Equal("capture_failed", (await wait).Event!.Event.MissingReason);
        Assert.Empty(hub.Events("old", old.SubscriptionId, old.ScopeId, old.StartingCursor).Events);
    }
    [Fact]
    public void InvalidProjectionIsAtomicAndCannotOverwriteAnotherScope()
    {
        var store = new NativeLogicalCaptureStore(() => 0); using var hub = new NativeLogicalPublicationHub(store, new[] { CompleteSeam }, () => 0);
        var a = Attach(hub); var b = Attach(hub, "second"); var reservation = hub.Reserve("owner", "enter", "observation");
        using var encoding = store.AcquireEncoding(new byte[] { 1 });
        var capture = store.Seal(encoding, "snapshot", new("runtime", "fp"), hub.StreamGeneration, a.ScopeId, DateTimeOffset.UnixEpoch);
        Assert.Throws<NativeLogicalException>(() => hub.Complete(reservation, new[] { new NativeLogicalProjectionOutcome(a.ScopeId, capture, null), new NativeLogicalProjectionOutcome(b.ScopeId, capture, null) }));
        Assert.Empty(hub.Events("client", a.SubscriptionId, a.ScopeId, a.StartingCursor).Events);
        Assert.True(hub.Complete(reservation, new[] { Missing(a), Missing(b) }));
        Assert.Equal("not_eager", hub.Events("client", a.SubscriptionId, a.ScopeId, a.StartingCursor).Events.Single().Event.MissingReason);
    }
    [Theory]
    [InlineData(9, "event")]
    [InlineData(10, "timeout")]
    [InlineData(100, "timeout")]
    public async Task AwaitDeadlinePrecedesEncoderTimeoutThatUnblocksPublication(long eligibleAt, string expected)
    {
        long now = 0; var store = new NativeLogicalCaptureStore(() => now);
        using var hub = new NativeLogicalPublicationHub(store, new[] { CompleteSeam }, () => now, new(EncodingDeadlineMs: 100));
        var sub = Attach(hub); var earlierPending = hub.Reserve("owner", "enter", "observation");
        var terminal = hub.Reserve("owner", "terminal", "terminal"); hub.Complete(terminal, new[] { Missing(sub) });
        var wait = hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(1), "terminal", 10);
        Assert.False(wait.IsCompleted);
        now = eligibleAt;
        if (eligibleAt < 100) hub.Complete(earlierPending, new[] { Missing(sub) }); else hub.Tick();
        var reply = await wait; Assert.Equal(expected, reply.Status); Assert.Equal(0, hub.WaiterCount);
        if (expected == "event") Assert.Equal("2", reply.Event!.Event.PublicationIndex); else Assert.Null(reply.Event);
        // A new zero-wait query sees the now-eligible retained terminal without changing the old outcome.
        var retained = await hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(2), "terminal", 0);
        Assert.Equal("event", retained.Status); Assert.Equal("2", retained.Event!.Event.PublicationIndex);
        now = 101; hub.Tick(); Assert.Equal(expected, (await wait).Status);
    }
    [Fact]
    public async Task LateCancellationAndSubscriptionExpiryPreserveEarlierWaitDeadline()
    {
        long now = 0; var store = new NativeLogicalCaptureStore(() => now);
        using var hub = new NativeLogicalPublicationHub(store, new[] { CompleteSeam }, () => now, new(RetentionMs: 60));
        var sub = Attach(hub);
        var shortWait = hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(1), "terminal", 10);
        var longWait = hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(2), "terminal", 1000);
        now = 100; hub.Tick(); Assert.Equal("timeout", (await shortWait).Status); Assert.Equal("subscription_expired", (await longWait).Status);
        sub = Attach(hub); var cancellation = hub.AwaitAsync("client", sub.SubscriptionId, sub.ScopeId, sub.StartingCursor, Id(3), "terminal", 10);
        now = 110; Assert.False(hub.CancelWait("client", sub.SubscriptionId, Id(3))); Assert.Equal("timeout", (await cancellation).Status);
    }
    private sealed class TestControls : INativeLogicalControlDependency
    {
        private readonly object gate = new(); private bool valid = true; private readonly List<Action> callbacks = new();
        public bool TryWatch(string client, NativeLogicalControlBinding binding, Action lost, out IDisposable? watch)
        { lock (gate) { if (!valid || client != "client" || binding != new NativeLogicalControlBinding("lease", 1)) { watch = null; return false; } callbacks.Add(lost); watch = new Watch(() => { lock (gate) callbacks.Remove(lost); }); return true; } }
        internal void Lose() { Action[] notify; lock (gate) { valid = false; notify = callbacks.ToArray(); } foreach (Action callback in notify) callback(); }
        private sealed class Watch(Action release) : IDisposable { public void Dispose() => release(); }
    }
}
