using System;
using System.Collections.Concurrent;
using STS2Connector.Authority;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>The same controller and request namespace as native actions, with
/// separate result semantics for presentation changes. A GET never dispatches.</summary>
internal sealed class TextMenuExecutor(
    object submissionGate,
    ConcurrentDictionary<string, string> requestFingerprints,
    Func<TextMenuFrame> capture,
    Func<MutationAuthorizationRequest, MutationAdmission> authorize,
    Func<string?>? currentController = null)
{
    private readonly TextMenuSession session = new();
    private readonly ConcurrentDictionary<string, TextMenuActionResult> results = new(StringComparer.Ordinal);
    private string? previousController;

    private void SynchronizeControl()
    {
        if (currentController == null) return;
        string? controller = currentController();
        if (controller != previousController) session.ResetCursor();
        previousController = controller;
    }

    internal TextMenuSnapshot Observe()
    {
        lock (submissionGate)
        {
            SynchronizeControl();
            return session.Observe(capture()).Snapshot;
        }
    }

    internal TextMenuActionResult? Find(string requestId) =>
        results.TryGetValue(requestId, out var result) ? result : null;

    internal TextMenuActionResult Submit(PlayerEnvironmentActionRequest request)
    {
        string id = request.RequestId ?? "";
        TextMenuActionResult Result(string status, TextMenuAction? action,
            string? delivery, string? code, string detail, TextMenuSnapshot? successor,
            PlayerEnvironmentAttribution? attribution = null) => new(
                PlayerEnvironmentContract.ProtocolVersion, TextMenuContract.ResultSchema,
                TextMenuContract.Profile, id, status, action?.EffectDomain, delivery,
                action, code, detail, status == "not_applied" ? "reobserve" : "never",
                successor, attribution);
        if (request.InputProfile != TextMenuContract.Profile || string.IsNullOrWhiteSpace(id))
            return Result("not_applied", null, null, "invalid_text_menu_request",
                "An exact text-menu profile and request ID are required.", null);

        lock (submissionGate)
        {
            string fingerprint = PlayerEnvironmentService.ActionRequestFingerprint(request);
            if (requestFingerprints.TryGetValue(id, out string? previous))
                return previous == fingerprint && results.TryGetValue(id, out var replay)
                    ? replay
                    : Result("not_applied", null, null, "request_id_conflict",
                        "This request ID already belongs to another exact action or profile.", null);

            SynchronizeControl();
            TextMenuFrame frame = capture();
            TextMenuProjection projection = session.Observe(frame);
            projection.Choices.TryGetValue(request.BoundActionId ?? "", out var choice);
            requestFingerprints[id] = fingerprint;
            TextMenuActionResult Save(TextMenuActionResult result)
            {
                results[id] = result;
                return result;
            }
            TextMenuActionResult Reject(string code, string detail) => Save(Result(
                "not_applied", choice?.Action,
                choice?.Leaf != null ? "not_delivered" : null, code, detail, projection.Snapshot));
            if (request.ExpectedSnapshotId != projection.Snapshot.SnapshotId)
                return Reject("stale_snapshot", "The native page or text cursor changed; observe again and choose a new action.");
            if (projection.Snapshot.MenuActions.Status != "complete" || choice == null)
                return Reject("menu_action_not_current", "This action is not in the current complete menu.");
            MutationAdmission admission = authorize(new MutationAuthorizationRequest(
                request.ClientSessionId, request.ControllerLeaseId, request.ControllerGeneration));
            if (!admission.Accepted)
                return Reject(admission.ErrorCode ?? "controller_rejected",
                    admission.Detail ?? "The current controller did not authorize this request.");
            var source = admission.Attribution;
            PlayerEnvironmentAttribution? attribution = source == null ? null : new(
                source.RuntimeInstanceId, source.ClientSessionId, source.ClientInstanceId,
                source.ProductId, source.ProductName, source.ProductVersion,
                source.ControllerLeaseId, source.ControllerGeneration);

            if (choice.TargetCursor != null)
            {
                TextMenuSnapshot next = session.Navigate(frame,
                    projection.Snapshot.SnapshotId, choice.Action.ActionId);
                return Save(Result("applied", choice.Action, null, null,
                    "Only the text menu cursor changed; no native input was delivered.", next, attribution));
            }
            try
            {
                var native = choice.Leaf!.Dispatch();
                if (!native.Accepted)
                    return Reject(native.ErrorCode ?? "native_input_rejected",
                        native.Detail ?? "Native execute-time validation rejected this input.");
            }
            catch (Exception exception)
            {
                return Save(Result("unknown", choice.Action, "unknown", "input_delivery_unknown",
                    $"Native input may have been delivered before {exception.GetType().Name}; never retry this request.",
                    null, attribution));
            }
            TextMenuSnapshot? observed = null;
            try { observed = session.Observe(capture()).Snapshot; }
            catch (Exception) { /* Delivery is known; failed observation cannot undo it. */ }
            return Save(Result("applied", choice.Action, "delivered",
                observed == null ? "successor_observation_unavailable" : null,
                "Native input was delivered. The successor is an immediate observation, not causal settlement.",
                observed, attribution));
        }
    }
}
