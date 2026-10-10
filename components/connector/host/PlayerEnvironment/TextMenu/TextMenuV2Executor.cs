using System;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

internal sealed class TextMenuV2Executor(object submissionGate, RequestNamespace requests,
    Func<TextMenuFrame> capture, Func<string?>? currentController = null)
{
    internal RequestNamespace Requests => requests;
    private readonly TextMenuV2Session session = new();
    private string? previousController;
    private void SynchronizeControl()
    {
        if (currentController == null) return;
        string? controller = currentController();
        if (controller != previousController) session.ResetSelection();
        previousController = controller;
    }
    internal TextMenuV2Snapshot Observe()
    {
        lock (submissionGate) { SynchronizeControl(); return session.Observe(capture()).Snapshot; }
    }
    internal TextMenuV2ObservationContext ObserveContext()
    {
        lock (submissionGate)
        {
            SynchronizeControl(); TextMenuFrame frame = capture();
            return new(TextMenuV2Contract.ObservationContextSchema, session.Observe(frame).Snapshot, frame.GameContinuityId);
        }
    }
    internal bool RunAdmitted(PlayerEnvironmentActionRequest request)
    {
        using var preparation = requests.PrepareOriginal(request);
        if (preparation is null) return false;
        TextMenuAction? action = null;
        TextMenuV2ActionResult Result(string status, string? delivery, string? code, string message,
            TextMenuV2Snapshot? successor) => new(PlayerEnvironmentContract.ProtocolVersion, TextMenuV2Contract.ResultSchema,
                TextMenuV2Contract.Profile, request.RequestId!, status, action?.EffectDomain, delivery, action, code,
                message, status == "not_applied" ? "reobserve" : "never", successor, preparation.AdmissionAttribution);
        void Reject(string code, string message, TextMenuV2Snapshot? successor = null)
        {
            object rejected = Result("not_applied", action?.Kind == "native_input" ? "not_delivered" : null, code, message, successor);
            try { preparation.Seal(rejected); }
            catch (ResultPayloadCapacityException)
            {
                preparation.Reservation.ResetEncoding();
                if (successor is not null)
                    preparation.Seal(Result("not_applied", action?.Kind == "native_input" ? "not_delivered" : null,
                        code, message + " Current diagnostic snapshot omitted: successor_payload_capacity_exceeded.", null));
                else throw;
            }
        }
        TextMenuFrame frame;
        TextMenuV2Projection projection;
        TextMenuV2Choice? choice;
        try
        {
            SynchronizeControl(); frame = capture(); projection = session.Observe(frame);
            projection.Choices.TryGetValue(request.BoundActionId!, out choice);
            action = choice?.Action;
            if (request.ExpectedSnapshotId != projection.Snapshot.SnapshotId)
            { Reject("stale_snapshot", "The native page or text selection changed; observe again.", projection.Snapshot); return true; }
            if (projection.Snapshot.MenuActions.Status != "complete" || choice == null)
            { Reject("menu_action_not_current", "This action is not in the current complete menu.", projection.Snapshot); return true; }
            if (choice.Action.Kind != "native_input")
            {
                var change = session.PrepareSystemApply(frame, projection.Snapshot.SnapshotId, choice.Action.ActionId);
                // Mandatory complete successor bytes are immutable before any
                // selection/revision change. A failed prepare leaves state intact.
                using var frozen = preparation.PrepareTerminal(Result("applied", null, null,
                    "Only the text menu selection changed; no native input was delivered.", change.Snapshot));
                var admission = preparation.TryBegin();
                if (!admission.Accepted)
                { frozen.Dispose(); Reject(admission.ErrorCode ?? "controller_rejected", admission.Detail ?? "Controller rejected this original request."); return true; }
                change.Commit(session);
                preparation.SealFrozen(Result("applied", null, null,
                    "Only the text menu selection changed; no native input was delivered.", change.Snapshot), frozen.Commit());
                return true;
            }
            LegacyTerminalBounds.Preflight(preparation, Result("unknown", "unknown",
                LegacyTerminalBounds.MaximumField, LegacyTerminalBounds.MaximumField, null));
            var started = preparation.TryBegin();
            if (!started.Accepted) { Reject(started.ErrorCode ?? "controller_rejected", started.Detail ?? "Controller rejected this original request."); return true; }
        }
        catch (ResultPayloadCapacityException)
        { Reject("result_core_capacity_exceeded", "The full mandatory result exceeded capacity before any text or native effect."); return true; }
            preparation.ReleasePreparation();
            NativeInputResult native;
            try { native = choice.Leaf!.Dispatch(); }
            catch (Exception)
            {
                session.ResetSelection();
                preparation.Seal(Result("unknown", "unknown", "input_delivery_unknown",
                    "Native input may have been delivered; never retry this request.", null)); return true;
            }
            if (native.LegacyDisposition == LegacyNativeInputDisposition.Unknown)
            {
                session.ResetSelection();
                preparation.Seal(Result("unknown", "unknown", "input_delivery_unknown",
                    "The native input has a partial, unconfirmed or unknown outcome; never retry.", null)); return true;
            }
            if (native.LegacyDisposition == LegacyNativeInputDisposition.NotDelivered)
            { Reject(native.ErrorCode ?? "native_input_rejected", native.Detail ?? "Native execute-time validation rejected this input."); return true; }
            TextMenuV2Snapshot? observed = null;
            try { observed = session.Observe(capture()).Snapshot; } catch (Exception) { }
            object applied = Result("applied", "delivered", observed == null ? "successor_observation_unavailable" : null,
                "Native input was delivered. The successor is an immediate observation, not causal settlement.", observed);
            try { preparation.Seal(applied); }
            catch (ResultPayloadCapacityException)
            {
                preparation.Reservation.ResetEncoding();
                preparation.Seal(Result("applied", "delivered", "successor_payload_capacity_exceeded",
                    "Native input was delivered; its optional immediate observation exceeded result capacity.", null));
            }
            return true;
    }
}
