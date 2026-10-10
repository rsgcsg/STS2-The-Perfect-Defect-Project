using STS2Connector.Authority;
using System.Linq;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>
/// Player Environment wire projection over the shared single-controller
/// coordinator. It owns public control DTOs but does not create UI affordances
/// or action authority.
/// </summary>
internal static partial class PlayerEnvironmentService
{
    public static PlayerEnvironmentClientRegistrationResponse RegisterPlayerEnvironmentClient(
        PlayerEnvironmentClientRegistrationRequest request)
    {
        MutationClientRegistrationResult response = MutationControlRuntime.Register(
            new MutationClientRegistrationRequest(
                request.ClientInstanceId,
                request.ProductId,
                request.ProductName,
                request.ProductVersion));
        return new PlayerEnvironmentClientRegistrationResponse(
            PlayerEnvironmentContract.ProtocolVersion,
            PlayerEnvironmentContract.ControlSchema,
            response.RuntimeInstanceId,
            ToPublicClient(response.Client),
            response.Controller == null ? null : ToPublicController(response.Controller));
    }

    public static PlayerEnvironmentControlSnapshot GetPlayerEnvironmentControlSnapshot()
    {
        MutationControlSnapshot snapshot = MutationControlRuntime.Snapshot();
        return new PlayerEnvironmentControlSnapshot(
            PlayerEnvironmentContract.ProtocolVersion,
            PlayerEnvironmentContract.ControlSchema,
            snapshot.RuntimeInstanceId,
            snapshot.Clients.Select(ToPublicClient).ToArray(),
            snapshot.Controller == null ? null : ToPublicController(snapshot.Controller));
    }

    public static PlayerEnvironmentClientRevocationResponse RevokePlayerEnvironmentClient(
        PlayerEnvironmentClientRevocationRequest request)
    {
        MutationClientRevocationResult result = MutationControlRuntime.Revoke(new(
            request.RuntimeInstanceId, request.ClientSessionId));
        return new(PlayerEnvironmentContract.ProtocolVersion, PlayerEnvironmentContract.ClientRevocationSchema,
            result.RuntimeInstanceId, result.ClientSessionId, result.Status, result.Closed, null);
    }

    public static PlayerEnvironmentControllerLeaseResponse AcquirePlayerEnvironmentController(
        PlayerEnvironmentControllerLeaseRequest request) =>
        ToPlayerEnvironmentControlResponse(MutationControlRuntime.Acquire(
            ToMutationLeaseRequest(request)));

    public static PlayerEnvironmentControllerLeaseResponse RenewPlayerEnvironmentController(
        PlayerEnvironmentControllerLeaseRequest request) =>
        ToPlayerEnvironmentControlResponse(MutationControlRuntime.Renew(
            ToMutationLeaseRequest(request)));

    public static PlayerEnvironmentControllerLeaseResponse ReleasePlayerEnvironmentController(
        PlayerEnvironmentControllerLeaseRequest request) =>
        ToPlayerEnvironmentControlResponse(MutationControlRuntime.Release(
            ToMutationLeaseRequest(request)));

    private static MutationLeaseRequest ToMutationLeaseRequest(
        PlayerEnvironmentControllerLeaseRequest request) => new(
            request.ClientSessionId,
            request.ControllerLeaseId,
            request.ControllerGeneration);

    private static PlayerEnvironmentControllerLeaseResponse ToPlayerEnvironmentControlResponse(
        MutationLeaseResult response) => new(
            PlayerEnvironmentContract.ProtocolVersion,
            PlayerEnvironmentContract.ControlSchema,
            response.RuntimeInstanceId,
            response.Status,
            response.Detail,
            response.Client == null ? null : ToPublicClient(response.Client),
            response.Controller == null ? null : ToPublicController(response.Controller));

    private static PlayerEnvironmentClient ToPublicClient(MutationClient value) => new(
        value.ClientSessionId,
        value.ClientInstanceId,
        value.ProductId,
        value.ProductName,
        value.ProductVersion,
        value.RegisteredAt,
        value.LastSeenAt);

    private static PlayerEnvironmentControllerLease ToPublicController(
        MutationLease value) => new(
            value.Status,
            value.ControllerLeaseId,
            value.ControllerGeneration,
            value.ClientSessionId,
            value.AcquiredAt,
            value.ExpiresAt);
}
