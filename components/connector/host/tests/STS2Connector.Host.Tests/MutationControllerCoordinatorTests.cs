using STS2Connector.Authority;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.Tests;

public sealed class MutationControllerCoordinatorTests
{
    [Fact]
    public void OnlyOneClientMayHoldMutationControl()
    {
        var coordinator = new MutationControllerCoordinator("runtime-a");
        MutationClientRegistrationResult first = coordinator.Register(Client("instance-a", "Agent A"));
        MutationClientRegistrationResult second = coordinator.Register(Client("instance-b", "Agent B"));

        MutationLeaseResult acquired = coordinator.Acquire(
            new MutationLeaseRequest(first.Client.ClientSessionId, null, null));
        MutationLeaseResult blocked = coordinator.Acquire(
            new MutationLeaseRequest(second.Client.ClientSessionId, null, null));

        Assert.Equal("controller_acquired", acquired.Status);
        Assert.Equal("controller_lease_held", blocked.Status);
        Assert.Equal(first.Client.ClientSessionId, blocked.Controller?.ClientSessionId);
    }

    [Fact]
    public void ReleaseAllowsTakeoverWithHigherGeneration()
    {
        var coordinator = new MutationControllerCoordinator("runtime-a");
        MutationClientRegistrationResult first = coordinator.Register(Client("instance-a", "Agent A"));
        MutationClientRegistrationResult second = coordinator.Register(Client("instance-b", "Agent B"));
        MutationLease firstLease = Assert.IsType<MutationLease>(
            coordinator.Acquire(new MutationLeaseRequest(
                first.Client.ClientSessionId,
                null,
                null)).Controller);

        MutationLeaseResult released = coordinator.Release(new MutationLeaseRequest(
            first.Client.ClientSessionId,
            firstLease.ControllerLeaseId,
            firstLease.ControllerGeneration));
        MutationLease secondLease = Assert.IsType<MutationLease>(
            coordinator.Acquire(new MutationLeaseRequest(
                second.Client.ClientSessionId,
                null,
                null)).Controller);

        Assert.Equal("controller_released", released.Status);
        Assert.True(secondLease.ControllerGeneration > firstLease.ControllerGeneration);
        Assert.NotEqual(firstLease.ControllerLeaseId, secondLease.ControllerLeaseId);
    }

    [Fact]
    public void ExpiredLeaseRejectsOldGenerationAndAllowsNewAcquire()
    {
        DateTimeOffset now = new(2026, 7, 25, 0, 0, 0, TimeSpan.Zero);
        var coordinator = new MutationControllerCoordinator("runtime-a", 1_000, () => now);
        MutationClientRegistrationResult client = coordinator.Register(Client("instance-a", "Agent A"));
        MutationLease firstLease = Assert.IsType<MutationLease>(
            coordinator.Acquire(new MutationLeaseRequest(
                client.Client.ClientSessionId,
                null,
                null)).Controller);
        now = now.AddMilliseconds(1_001);

        MutationAdmission stale = coordinator.Authorize(Command(client, firstLease));
        MutationLease nextLease = Assert.IsType<MutationLease>(
            coordinator.Acquire(new MutationLeaseRequest(
                client.Client.ClientSessionId,
                null,
                null)).Controller);

        Assert.False(stale.Accepted);
        Assert.Equal("controller_lease_stale", stale.ErrorCode);
        Assert.True(nextLease.ControllerGeneration > firstLease.ControllerGeneration);
    }

    [Fact]
    public void CommandAttributionIsBoundToRuntimeClientAndGeneration()
    {
        var coordinator = new MutationControllerCoordinator("runtime-a");
        MutationClientRegistrationResult client = coordinator.Register(Client("instance-a", "Agent A"));
        MutationLease lease = Assert.IsType<MutationLease>(
            coordinator.Acquire(new MutationLeaseRequest(
                client.Client.ClientSessionId,
                null,
                null)).Controller);

        MutationAdmission admission = coordinator.Authorize(Command(client, lease));

        Assert.True(admission.Accepted);
        Assert.Equal("runtime-a", admission.Attribution?.RuntimeInstanceId);
        Assert.Equal("instance-a", admission.Attribution?.ClientInstanceId);
        Assert.Equal(lease.ControllerGeneration, admission.Attribution?.ControllerGeneration);
    }

    [Fact]
    public void RegistrationIdentityIsIdempotentButMetadataCannotDrift()
    {
        var coordinator = new MutationControllerCoordinator("runtime-a");
        MutationClientRegistrationResult first = coordinator.Register(Client("instance-a", "Agent A"));
        MutationClientRegistrationResult duplicate = coordinator.Register(Client("instance-a", "Agent A"));

        Assert.Equal(first.Client.ClientSessionId, duplicate.Client.ClientSessionId);
        Assert.Throws<InvalidOperationException>(() =>
            coordinator.Register(Client("instance-a", "Different Agent")));
    }

    [Fact]
    public void ExpiryNotifiesControlWatchWithoutAStatusReadOrAnotherCommand()
    {
        DateTimeOffset epoch = DateTimeOffset.UtcNow; int elapsed = 0;
        var coordinator = new MutationControllerCoordinator("runtime-expiry", 1000,
            () => epoch.AddMilliseconds(Volatile.Read(ref elapsed)));
        var client = coordinator.Register(Client("expiry", "Expiry"));
        var lease = coordinator.Acquire(new(client.Client.ClientSessionId, null, null)).Controller!;
        using var lost = new ManualResetEventSlim();
        Assert.True(coordinator.TryWatch(Command(client, lease), lost.Set, out var watch));
        using (watch)
        {
            Volatile.Write(ref elapsed, 1001);
            Assert.True(lost.Wait(2000));
            Assert.False(coordinator.TryBegin(Command(client, lease)).Accepted);
        }
    }

    private static MutationClientRegistrationRequest Client(string instanceId, string name) =>
        new(instanceId, "test-agent", name, "1.0.0");

    private static MutationAuthorizationRequest Command(
        MutationClientRegistrationResult client,
        MutationLease lease) =>
        new(
            client.Client.ClientSessionId,
            lease.ControllerLeaseId,
            lease.ControllerGeneration);
}
