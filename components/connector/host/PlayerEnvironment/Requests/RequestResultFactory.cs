using System;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

internal static class RequestResultFactory
{
    internal static bool Native(PlayerEnvironmentActionRequest request) => request.InputProfile == NativeLogicalContract.Profile;
    internal static object BeforeInput(PlayerEnvironmentActionRequest request, string reason)
    {
        const string detail = "The original request ended before any input or text-state effect began.";
        if (Native(request)) return new NativeLogicalResult(PlayerEnvironmentContract.ProtocolVersion,
            NativeLogicalContract.ResultSchema, NativeLogicalContract.Profile, request.RequestId!, request.ExpectedSnapshotId!,
            null, "not_started", "unknown", "unknown", "unknown", Array.Empty<NativeLogicalInputStage>(), reason,
            "never_automatic", null, null);
        if (request.InputProfile == TextMenuV2Contract.Profile) return new TextMenuV2ActionResult(
            PlayerEnvironmentContract.ProtocolVersion, TextMenuV2Contract.ResultSchema, TextMenuV2Contract.Profile,
            request.RequestId!, "not_applied", null, null, null, reason, detail, "reobserve", null, null);
        if (request.InputProfile == TextMenuContract.Profile) return new TextMenuActionResult(
            PlayerEnvironmentContract.ProtocolVersion, TextMenuContract.ResultSchema, TextMenuContract.Profile,
            request.RequestId!, "not_applied", null, null, null, reason, detail, "reobserve", null, null);
        return new PlayerEnvironmentActionReceipt(PlayerEnvironmentContract.ProtocolVersion,
            PlayerEnvironmentContract.ReceiptSchema, request.RequestId!, "not_delivered",
            new(request.BoundActionId!, "activate", null, Array.Empty<PlayerEnvironmentBoundActionArgument>()),
            reason, detail, new(true, "reobserve"), null) { InputProfile = request.InputProfile };
    }
    internal static int HttpStatus(object result) => result switch
    {
        NativeLogicalResult native => native.Delivery is "unknown" or "partially_delivered" ? 202
            : native.Delivery == "delivered" ? 200 : 409,
        TextMenuActionResult text => text.Status == "applied" ? 200 : text.Status == "unknown" ? 202 : 409,
        TextMenuV2ActionResult text => text.Status == "applied" ? 200 : text.Status == "unknown" ? 202 : 409,
        PlayerEnvironmentActionReceipt receipt => receipt.Delivery == "delivered" ? 200 : receipt.Delivery == "unknown" ? 202 : 409,
        _ => throw new ArgumentException("Unknown original terminal contract.", nameof(result))
    };
}
