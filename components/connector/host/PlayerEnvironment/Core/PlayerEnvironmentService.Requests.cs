using System;
using STS2Connector.Authority;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

internal static partial class PlayerEnvironmentService
{
    private static readonly Lazy<RequestNamespace> RequestOwner = new(CreateRequestOwner);
    internal static RequestNamespace Requests => RequestOwner.Value;
    private static RequestNamespace CreateRequestOwner()
    {
        var owner = new RequestNamespace(EnvironmentIdentityRuntime.HostIdentity().RuntimeInstanceId,
            MutationControlRuntime.ValidateActiveControl, MutationControlRuntime.TryAdmitRequest,
            MutationControlRuntime.TryBegin,
            (request, result) => { if (NativeLogicalOwner.IsValueCreated) NativeLogical.NotifyOriginalTerminal(request, result); },
            ActionRequestFingerprint);
        MutationControlRuntime.ClientClosed += closure =>
        {
            owner.ClientClosed(closure);
            if (NativeLogicalOwner.IsValueCreated) NativeLogical.Hub.ExpireClient(closure.ClientSessionId, closure.Reason);
        };
        return owner;
    }
    internal static bool RunAdmittedAction(PlayerEnvironmentActionRequest request)
    {
        try
        {
            return request.InputProfile switch
            {
                NativeLogicalContract.Profile => NativeLogical.RunAdmitted(request),
                TextMenuContract.Profile => TextMenus.Value.RunAdmitted(request),
                TextMenuV2Contract.Profile => TextMenusV2.Value.RunAdmitted(request),
                _ => RunAdmittedLegacy(request)
            };
        }
        catch (Exception)
        {
            // Only a still-queued request can be changed to this known no-input
            // result. A started request is never relabelled by a generic catch.
            if (Requests.CancelQueued(request.RequestId!, "original_preparation_failed")) return false;
            throw;
        }
    }
}
