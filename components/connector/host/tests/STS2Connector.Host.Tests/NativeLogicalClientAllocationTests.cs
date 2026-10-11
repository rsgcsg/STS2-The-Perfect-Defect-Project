using System.Text.Json.Nodes;
using STS2Connector.Authority;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;
namespace STS2Connector;
public sealed class NativeLogicalClientAllocationTests
{
    private sealed class Fixture : IDisposable
    {
        internal readonly MutationControllerCoordinator Authority = new("runtime", enableDeadlineTimer: false);
        internal readonly MainThreadWorkQueue Queue = new();
        internal readonly RequestNamespace Requests;
        internal readonly NativeLogicalService Owner;
        internal readonly string Reader;
        internal int CheckCount, CloseAtCheck, Captures;
        internal Action<string, int, bool>? OnCheck;
        internal Fixture()
        {
            Reader = Authority.Register(new("reader", "review", "Review", "1")).Client.ClientSessionId;
            Requests = new("runtime", Authority.ValidateActiveControl, Authority.TryAdmitRequest, Authority.TryBegin, enableTimer: false);
            Owner = new(() => { Captures++; return Frame(); }, () => "run", Requests,
                (work, token) => Queue.Enqueue(work, token), () => true, clientActive: Check);
            Owner.Initialize();
            Authority.ClientClosed += closure => Owner.ExpireClient(closure.ClientSessionId, closure.Reason);
        }
        private bool Check(string id)
        {
            bool activeAtLinearization = Authority.IsActiveClient(id);
            int ordinal = Interlocked.Increment(ref CheckCount);
            OnCheck?.Invoke(id, ordinal, activeAtLinearization);
            if (CloseAtCheck != 0 && ordinal == CloseAtCheck && activeAtLinearization)
            {
                Assert.True(Authority.Revoke(new("runtime", id)).Closed);
                // Simulate the actual asynchronous post-Authority notification
                // winning before the caller enters its resource-owner gate.
                Requests.ClientClosed(new("runtime", id, "client_session_revoked"));
                Owner.ExpireClient(id, "client_session_revoked");
            }
            return activeAtLinearization;
        }
        internal NativeLogicalCurrentReply Current()
        {
            var task = Owner.CurrentAsync(new(Reader, NativeLogicalProjector.ScopeFields, null));
            Queue.Drain(1); return task.GetAwaiter().GetResult();
        }
        internal void Arm(int check) { CheckCount = 0; CloseAtCheck = check; }
        private static TextMenuFrame Frame()
        {
            var page = new PlayerEnvironmentSnapshot("1.0.0", PlayerEnvironmentContract.SnapshotSchema, "source", 1,
                DateTimeOffset.UnixEpoch, "interactive", null,
                new("interaction", "held", "ready", null, "surface", new(new JsonObject { ["kind"] = "held" }, new JsonObject()), Array.Empty<PlayerEnvironmentInteractionCapability>()),
                Array.Empty<PlayerEnvironmentReferent>(),
                new("sts2.player-environment/bound-actions-1", "complete", 0, 0, 65536, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
                Array.Empty<PlayerEnvironmentReadOpportunity>(), new("complete", "public", "complete", Array.Empty<string>(), Array.Empty<string>()),
                new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));
            return new(page, "owner", new[] { new TextMenuLeaf("binding", "root", "confirm", "Confirm", null,
                Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => NativeInputResult.Delivered("unused")) }) { GameContinuityId = "run" };
        }
        public void Dispose() { Owner.Dispose(); Requests.Dispose(); }
    }
    [Fact]
    public void ClosureAfterActiveCheckAndBeforeAttachMustNotCreateAnOriginalSubscription()
    {
        using var f = new Fixture(); f.Arm(1);
        Assert.Throws<NativeLogicalException>(() => f.Owner.Attach(new(f.Reader, NativeLogicalProjector.ScopeFields,
            new[] { NativeLogicalService.Coverage[0] }, "full_reference")));
    }
    [Fact]
    public void ClosureAfterActiveCheckAndBeforeRetainMustNotCreateAnOriginalReaderHandle()
    {
        using var f = new Fixture(); var capture = f.Current().Capture!; f.Arm(1);
        Assert.Throws<NativeLogicalException>(() => f.Owner.Retain(new(f.Reader, capture.CaptureId)));
    }
    [Fact]
    public async Task ClosureAfterLastActiveCheckAndBeforeCurrentAllocationMustNotReturnNewCapture()
    {
        using var f = new Fixture(); f.Arm(3);
        var task = f.Owner.CurrentAsync(new(f.Reader, NativeLogicalProjector.ScopeFields, null));
        f.Queue.Drain(1);
        await Assert.ThrowsAsync<NativeLogicalException>(async () => await task);
    }

    [Fact]
    public void AttachAdmissionWinningBeforeClosureIsCleanedAndCannotReturnSuccessAfterCleanup()
    {
        using var f = new Fixture();
        string b = f.Authority.Register(new("b", "review", "B", "1")).Client.ClientSessionId;
        var lease = f.Authority.Acquire(new(b, null, null)).Controller!;
        var foreign = f.Owner.Hub.Attach(new(b, new[] { "persistent" }, new[] { NativeLogicalService.Coverage[0] }, "scoped")).Subscription!;
        using var admission = new ManualResetEventSlim(); using var closed = new ManualResetEventSlim();
        ThreadPool.QueueUserWorkItem(_ =>
        {
            if (!admission.Wait(TimeSpan.FromSeconds(5))) return;
            f.Authority.Revoke(new("runtime", f.Reader)); closed.Set();
        });
        f.Arm(0);
        f.OnCheck = (id, ordinal, active) =>
        {
            if (id == f.Reader && ordinal == 2 && active)
            { admission.Set(); Assert.True(closed.Wait(TimeSpan.FromSeconds(5))); }
        };
        Assert.Throws<NativeLogicalException>(() => f.Owner.Attach(new(f.Reader, NativeLogicalProjector.ScopeFields,
            new[] { NativeLogicalService.Coverage[0] }, "full_reference")));
        Assert.Equal(0, f.Captures); Assert.Equal(0, f.Owner.Store.ChargedBytes);
        Assert.Empty(f.Owner.Hub.Events(b, foreign.SubscriptionId, foreign.ScopeId, foreign.StartingCursor).Events);
        Assert.Equal(lease.ControllerLeaseId, f.Authority.Snapshot().Controller!.ControllerLeaseId);
    }
    [Fact]
    public void RetainAdmissionWinningBeforeClosureReleasesOwnLateHandleButKeepsForeignPinAndLease()
    {
        using var f = new Fixture(); var capture = f.Current().Capture!;
        string b = f.Authority.Register(new("b", "review", "B", "1")).Client.ClientSessionId;
        var lease = f.Authority.Acquire(new(b, null, null)).Controller!;
        var foreign = f.Owner.Retain(new(b, capture.CaptureId)).Retention!;
        long charge = f.Owner.Store.ChargedBytes;
        using var admission = new ManualResetEventSlim(); using var closed = new ManualResetEventSlim();
        ThreadPool.QueueUserWorkItem(_ =>
        {
            if (!admission.Wait(TimeSpan.FromSeconds(5))) return;
            f.Authority.Revoke(new("runtime", f.Reader)); closed.Set();
        });
        f.Arm(0);
        f.OnCheck = (id, ordinal, active) =>
        {
            if (id == f.Reader && ordinal == 2 && active)
            { admission.Set(); Assert.True(closed.Wait(TimeSpan.FromSeconds(5))); }
        };
        Assert.Throws<NativeLogicalException>(() => f.Owner.Retain(new(f.Reader, capture.CaptureId)));
        Assert.Equal(charge, f.Owner.Store.ChargedBytes);
        Assert.True(f.Owner.Store.Read(capture.CaptureId, foreign.ReadCursor).Complete);
        Assert.Equal(lease.ControllerLeaseId, f.Authority.Snapshot().Controller!.ControllerLeaseId);
        f.Owner.Store.ReleasePublic(new(b, foreign.RetentionHandleId));
        Assert.Equal(0, f.Owner.Store.ChargedBytes); // No late A handle or initial pin survived cleanup.
    }
}
