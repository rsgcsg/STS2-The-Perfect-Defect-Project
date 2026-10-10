using System;

namespace STS2Connector.Authority;

/// <summary>
/// The one process-local mutation controller. Transport registrations are
/// attribution and coordination, never game legality or authentication.
/// </summary>
internal static class MutationControlRuntime
{
    private static readonly MutationControllerCoordinator Coordinator = new(
        EnvironmentIdentityRuntime.HostIdentity().RuntimeInstanceId);

    public static MutationClientRegistrationResult Register(
        MutationClientRegistrationRequest request) => Coordinator.Register(request);

    public static MutationControlSnapshot Snapshot() => Coordinator.Snapshot();

    public static MutationLeaseResult Acquire(MutationLeaseRequest request) =>
        Coordinator.Acquire(request);

    public static MutationLeaseResult Renew(MutationLeaseRequest request) =>
        Coordinator.Renew(request);

    public static MutationLeaseResult Release(MutationLeaseRequest request) =>
        Coordinator.Release(request);

    public static MutationAdmission Authorize(MutationAuthorizationRequest request) =>
        Coordinator.Authorize(request);

    public static MutationAdmission TryBegin(MutationAuthorizationRequest request) => Coordinator.TryBegin(request);

    internal static MutationRequestAdmission TryAdmitRequest(MutationAuthorizationRequest request) =>
        Coordinator.TryAdmitRequest(request);

    internal static MutationAdmission ValidateActiveControl(MutationAuthorizationRequest request) =>
        Coordinator.ValidateActiveControl(request);

    internal static bool IsActiveClient(string clientSessionId) => Coordinator.IsActiveClient(clientSessionId);

    internal static bool TryTouchActiveClient(string clientSessionId) => Coordinator.TryTouchActiveClient(clientSessionId);

    internal static MutationClientRevocationResult Revoke(MutationClientRevocationRequest request) => Coordinator.Revoke(request);

    internal static event Action<MutationClientClosure> ClientClosed
    { add => Coordinator.ClientClosed += value; remove => Coordinator.ClientClosed -= value; }

    public static bool TryWatch(MutationAuthorizationRequest request, Action lost, out IDisposable? watch) =>
        Coordinator.TryWatch(request, lost, out watch);

    public static MutationControlCapability Capability() => Coordinator.Capability();
}
