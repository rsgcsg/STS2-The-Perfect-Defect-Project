using System.Text;
using STS2Connector.Authority;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector.Tests;

public sealed class ClientLifetimeTests
{
    private static MutationClientRegistrationRequest Registration(string instance = "A") =>
        new(instance, "test", "Test client", "1.0");

    [Fact]
    public void InvalidControlCannotExtendTheOriginalIdleDeadline()
    {
        long mono = 0; DateTimeOffset wall = DateTimeOffset.UnixEpoch;
        var authority = new MutationControllerCoordinator("runtime", clock: () => wall,
            monotonicClock: () => mono, clientIdleTtlMs: 100, enableDeadlineTimer: false);
        var client = authority.Register(Registration()).Client;
        var lease = authority.Acquire(new(client.ClientSessionId, null, null)).Controller!;
        var admitted = authority.TryAdmitRequest(new(client.ClientSessionId, lease.ControllerLeaseId, lease.ControllerGeneration));
        Assert.True(admitted.Accepted);
        mono = 90; wall += TimeSpan.FromSeconds(1);
        var stale = new MutationLeaseRequest(client.ClientSessionId, lease.ControllerLeaseId, lease.ControllerGeneration + 1);
        Assert.Equal("controller_lease_stale", authority.Renew(stale).Status);
        Assert.Equal("controller_lease_stale", authority.Release(stale).Status);
        Assert.False(authority.Authorize(new(stale.ClientSessionId, stale.ControllerLeaseId, stale.ControllerGeneration)).Accepted);
        Assert.Equal(client.LastSeenAt, authority.Snapshot().Clients.Single().LastSeenAt);
        mono = 100; authority.TickClientDeadlines();
        Assert.True(admitted.Client!.IsClosed);
        Assert.Equal("client_session_not_found", authority.Acquire(new(client.ClientSessionId, null, null)).Status);
    }

    [Fact]
    public void PassiveTouchCannotReviveAndDoesNotGrantOrRenewControl()
    {
        long mono = 0;
        var authority = new MutationControllerCoordinator("runtime", monotonicClock: () => mono,
            clientIdleTtlMs: 100, enableDeadlineTimer: false);
        var client = authority.Register(Registration()).Client;
        var lease = authority.Acquire(new(client.ClientSessionId, null, null)).Controller!;
        var lifetime = authority.TryAdmitRequest(new(client.ClientSessionId, lease.ControllerLeaseId, lease.ControllerGeneration)).Client!;
        mono = 90;
        Assert.True(authority.TryTouchActiveClient(client.ClientSessionId));
        Assert.Equal(lease.ExpiresAt, authority.Snapshot().Controller!.ExpiresAt);
        mono = 189; authority.TickClientDeadlines(); Assert.False(lifetime.IsClosed);
        mono = 190;
        Assert.False(authority.TryTouchActiveClient(client.ClientSessionId));
        Assert.True(lifetime.IsClosed);
        Assert.Null(authority.Snapshot().Controller);
        Assert.False(authority.TryTouchActiveClient(client.ClientSessionId));
    }

    [Fact]
    public void WallClockChangesDoNotExpireOrExtendMonotonicClientIdle()
    {
        long mono = 0; DateTimeOffset wall = DateTimeOffset.UnixEpoch;
        var authority = new MutationControllerCoordinator("runtime", clock: () => wall,
            monotonicClock: () => mono, clientIdleTtlMs: 100, enableDeadlineTimer: false);
        var client = authority.Register(Registration()).Client;
        wall += TimeSpan.FromDays(10); mono = 99;
        Assert.Single(authority.Snapshot().Clients);
        wall -= TimeSpan.FromDays(20); mono = 100;
        Assert.Empty(authority.Snapshot().Clients);
        Assert.Equal("client_session_not_found", authority.Acquire(new(client.ClientSessionId, null, null)).Status);
    }

    [Fact]
    public void PermanentClosurePreservesOtherClientsControllerAndRegistersFreshIdentity()
    {
        var authority = new MutationControllerCoordinator("runtime", enableDeadlineTimer: false);
        var a = authority.Register(Registration("A")).Client;
        var b = authority.Register(Registration("B")).Client;
        var lease = authority.Acquire(new(b.ClientSessionId, null, null)).Controller!;
        var closed = authority.Revoke(new("runtime", a.ClientSessionId));
        Assert.True(closed.Closed);
        Assert.Equal("client_revoked", closed.Status);
        Assert.Equal(lease, authority.Snapshot().Controller);
        Assert.Equal(closed, authority.Revoke(new("runtime", a.ClientSessionId)));
        Assert.Equal("client_session_not_found", authority.Acquire(new(a.ClientSessionId, null, null)).Status);
        var fresh = authority.Register(Registration("A")).Client;
        Assert.NotEqual(a.ClientSessionId, fresh.ClientSessionId);
        Assert.Equal(lease, authority.Snapshot().Controller);
    }

    [Fact]
    public void WrongRuntimeOrUnknownClientIsNeverAClosureAcknowledgement()
    {
        var authority = new MutationControllerCoordinator("runtime", enableDeadlineTimer: false);
        var client = authority.Register(Registration()).Client;
        var lease = authority.Acquire(new(client.ClientSessionId, null, null)).Controller!;
        var wrong = authority.Revoke(new("other_runtime", client.ClientSessionId));
        Assert.False(wrong.Closed); Assert.Equal("runtime_instance_mismatch", wrong.Status);
        var missing = authority.Revoke(new("runtime", "client_unknown"));
        Assert.False(missing.Closed); Assert.Equal("client_session_not_found", missing.Status);
        Assert.Equal(lease, authority.Snapshot().Controller);
    }

    [Fact]
    public void RequestCapacityRemainsSpentThroughClosure()
    {
        var authority = new MutationControllerCoordinator("runtime", enableDeadlineTimer: false,
            maxClientSpentRequests: 2);
        var client = authority.Register(Registration()).Client;
        var lease = authority.Acquire(new(client.ClientSessionId, null, null)).Controller!;
        var request = new MutationAuthorizationRequest(client.ClientSessionId, lease.ControllerLeaseId, lease.ControllerGeneration);
        var first = authority.TryAdmitRequest(request);
        Assert.True(first.Accepted); Assert.True(authority.TryAdmitRequest(request).Accepted);
        Assert.Equal("request_capacity_exceeded", authority.TryAdmitRequest(request).Admission.ErrorCode);
        Assert.Equal(2, first.Client!.SpentRequestCount);
        authority.Revoke(new("runtime", client.ClientSessionId));
        Assert.Equal(2, first.Client.SpentRequestCount);
        Assert.True(first.Client.IsClosed);
    }

    [Fact]
    public async Task TimerAloneClosesOriginalIdentityAndNotifiesWithoutReadOrSecondTick()
    {
        var authority = new MutationControllerCoordinator("runtime", clientIdleTtlMs: 80);
        var client = authority.Register(Registration()).Client;
        var lease = authority.Acquire(new(client.ClientSessionId, null, null)).Controller!;
        var lifetime = authority.TryAdmitRequest(new(client.ClientSessionId, lease.ControllerLeaseId, lease.ControllerGeneration)).Client!;
        var completion = new TaskCompletionSource<MutationClientClosure>(TaskCreationOptions.RunContinuationsAsynchronously);
        authority.ClientClosed += closure => completion.TrySetResult(closure);
        var closed = await completion.Task.WaitAsync(TimeSpan.FromSeconds(5));
        Assert.Equal(client.ClientSessionId, closed.ClientSessionId);
        Assert.Equal("client_session_expired", closed.Reason);
        Assert.True(lifetime.IsClosed);
        // No GET, TickClientDeadlines or second timer trigger caused closure.
        Assert.Null(authority.Snapshot().Controller);
    }

    [Theory]
    [InlineData("{\"runtime_instance_id\":\"r\",\"client_session_id\":\"c\",\"extra\":1}")]
    [InlineData("{\"runtime_instance_id\":\"r\",\"client_session_id\":null}")]
    [InlineData("{\"runtime_instance_id\":\"r\",\"client_session_id\":\"c\",\"client_session_id\":\"d\"}")]
    [InlineData("{\"runtime_instance_id\":\"r\"}")]
    [InlineData("{\"runtime_instance_id\":\"wrong runtime\",\"client_session_id\":\"c\"}")]
    public void FinalRevocationDecoderRejectsAmbiguousOrUnboundedIdentity(string json) =>
        Assert.Throws<System.Text.Json.JsonException>(() => ClientRevocationWire.Decode(Encoding.UTF8.GetBytes(json)));

    [Fact]
    public void FinalRevocationDecoderAcceptsOnlyOriginalBoundedIds()
    {
        var request = ClientRevocationWire.Decode(Encoding.UTF8.GetBytes(
            "{\"runtime_instance_id\":\"runtime_A-1\",\"client_session_id\":\"client_A\"}"));
        Assert.Equal("runtime_A-1", request.RuntimeInstanceId);
        Assert.Equal("client_A", request.ClientSessionId);
        Assert.Throws<System.Text.Json.JsonException>(() => ClientRevocationWire.Decode(new byte[1025]));
        Assert.Throws<System.Text.Json.JsonException>(() => ClientRevocationWire.Decode(Encoding.UTF8.GetBytes(
            "{\"runtime_instance_id\":\"" + new string('r', 129) + "\",\"client_session_id\":\"client_A\"}")));
    }

    [Fact]
    public void AuthorityOwnsRegistrationBoundsForEveryCaller()
    {
        var authority = new MutationControllerCoordinator("runtime", enableDeadlineTimer: false);
        Assert.Throws<ArgumentException>(() => authority.Register(Registration(new string('i', 129))));
        Assert.Throws<ArgumentException>(() => authority.Register(new("A", "product", "bad\nlabel", "v1")));
        Assert.Throws<ArgumentException>(() => authority.Register(new("A", "product", "bad\ud800", "v1")));
        Assert.Empty(authority.Snapshot().Clients);
    }
}
