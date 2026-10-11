using System;
using System.Linq;
using System.Text.Json;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Platform.NativeFoundation;

namespace STS2Connector.PlayerEnvironment;

internal sealed class NativeLogicalExecutor(RequestNamespace requests, NativeLogicalService owner)
{
    internal static string Delivery(NativeInputResult input) => input.Delivery switch
    {
        NativeInputDelivery.RejectedBeforeInput => "rejected_before_input",
        NativeInputDelivery.Delivered => "delivered",
        NativeInputDelivery.PartiallyDelivered => "partially_delivered",
        NativeInputDelivery.Unknown => "unknown",
        _ => input.Accepted ? "delivered" : "rejected_before_input"
    };
    internal static NativeLogicalResult MakeResult(PlayerEnvironmentActionRequest request,
        NativeLogicalAction? action, string delivery, string? reason,
        NativeLogicalInputStage[]? stages = null, PlayerEnvironmentAttribution? attribution = null) => new(
            PlayerEnvironmentContract.ProtocolVersion, NativeLogicalContract.ResultSchema, NativeLogicalContract.Profile,
            request.RequestId ?? "", request.ExpectedSnapshotId ?? "", action, delivery, "unknown", "unknown", "unknown",
            Array.AsReadOnly(stages ?? Array.Empty<NativeLogicalInputStage>()), reason, "never_automatic", null, attribution);

    internal bool RunAdmitted(PlayerEnvironmentActionRequest request)
    {
        using var preparation = requests.PrepareOriginal(request);
        if (preparation is null) return false;
        NativeLogicalAction? action = null;
        NativeLogicalResult Result(string delivery, string? reason,
            NativeLogicalInputStage[]? stages = null) => MakeResult(request, action, delivery, reason, stages,
                preparation.AdmissionAttribution);
        // Preparation excludes queue cancellation only while inspecting native
        // bindings and proving the complete terminal core fits. It is released
        // before every native callback and original source observer.
        TextMenuLeaf? leaf;
        try
        {
            leaf = owner.Revalidate(request.ExpectedSnapshotId!, request.BoundActionId!, request.RequestId);
            if (leaf is null) { preparation.Seal(Result("not_started", "stale_snapshot_or_binding")); return true; }
            action = new NativeLogicalAction(request.BoundActionId!, "native_input", leaf.Verb, leaf.Label,
                leaf.SubjectReferentId, Array.AsReadOnly(leaf.Arguments.Select(a => new NativeLogicalArgument(a.Role, a.ReferentId)).ToArray()), "native_ui");
            if (!owner.ExecutionAllowed()) { preparation.Seal(Result("not_started", "exact_game_execution_unavailable")); return true; }
            NativeTerminalBounds.Preflight(preparation, request, action);
            var admission = preparation.TryBegin();
            if (!admission.Accepted) { preparation.Seal(Result("not_started", admission.ErrorCode ?? "controller_rejected")); return true; }
        }
        catch (ResultPayloadCapacityException)
        {
            preparation.Reservation.ResetEncoding();
            action = null; // Existing pre-input shell; original Request retains the submitted bound ID.
            preparation.Seal(Result("not_started", "result_core_capacity_exceeded"));
            return true;
        }
        catch (Exception)
        {
            preparation.Reservation.ResetEncoding();
            action = null;
            preparation.Seal(Result("not_started", "native_revalidation_failed"));
            return true;
        }
        preparation.ReleasePreparation();
            owner.NotifyOriginalInputPrefix(request);
            owner.Publish("connector_input_start", "input_started");
            NativeInputResult input;
            try { using var nativeProtocolScope = NativeSourceInputProvider.ProtocolDispatch(request); input = leaf.Dispatch(); }
            catch { preparation.Seal(Result("unknown", "native_input_boundary_threw")); return true; }
            var stages = (input.Stages ?? Array.Empty<NativeInputStage>()).Select(s => new NativeLogicalInputStage(
                JsonNamingPolicy.SnakeCaseLower.ConvertName(s.Stage.ToString()), s.Delivery switch
                {
                    NativeInputDelivery.Delivered => "delivered", NativeInputDelivery.PartiallyDelivered => "partially_delivered",
                    NativeInputDelivery.RejectedBeforeInput => "rejected_before_input", _ => "unknown"
                }, s.Evidence)).ToArray();
            // These closed native producers use bounded literal reason/evidence
            // strings. The preflight covers every allowed 16 x 128-byte stage and
            // 64-KiB reason, including maximally escaped control characters.
            preparation.Seal(Result(Delivery(input), input.ErrorCode, stages));
            return true;
    }
}
