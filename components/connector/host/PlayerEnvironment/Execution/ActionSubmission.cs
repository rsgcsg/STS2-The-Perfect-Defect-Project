using STS2Connector.Authority;
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Connector.NativeUi;

namespace STS2Connector.PlayerEnvironment;

internal static partial class PlayerEnvironmentService
{
    internal static bool RunAdmittedLegacy(PlayerEnvironmentActionRequest request)
    {
        using var preparation = Requests.PrepareOriginal(request);
        if (preparation is null) return false;
        string requestId = request.RequestId!;
        IReadOnlyDictionary<string, string> parameters = new Dictionary<string, string>(StringComparer.Ordinal);
            SnapshotBuildResult snapshot = BuildSnapshot(inputProfile: request.InputProfile);
            string boundActionId = request.BoundActionId ?? string.Empty;
            PlayerEnvironmentBoundAction? boundAction = snapshot.Snapshot.BoundActions.Actions
                .SingleOrDefault(candidate => string.Equals(
                    candidate.BoundActionId,
                    boundActionId,
                    StringComparison.Ordinal));
            PlayerEnvironmentNativeBinding? binding = boundAction == null
                || !snapshot.Bindings.TryGetValue(boundActionId, out PlayerEnvironmentNativeBinding? found)
                    ? null
                    : found;
            parameters = binding?.ExactOperands
                ?? new Dictionary<string, string>(StringComparer.Ordinal);
            string action = boundAction?.Verb ?? "activate";
            string? subjectReferentId = boundAction?.SubjectReferentId;
            IReadOnlyList<PlayerEnvironmentBoundActionArgument> arguments =
                boundAction?.Arguments ?? Array.Empty<PlayerEnvironmentBoundActionArgument>();

            bool Fail(string code, string detail)
            {
                PlayerEnvironmentActionReceipt failed = BuildReceipt(
                    requestId,
                    boundActionId,
                    action,
                    subjectReferentId,
                    arguments,
                    "not_delivered",
                    "not_delivered",
                    code,
                    detail,
                    snapshot.Snapshot,
                    null,
                    request.InputProfile);
                try { preparation.Seal(failed); }
                catch (ResultPayloadCapacityException)
                {
                    preparation.Reservation.ResetEncoding();
                    preparation.Seal(BuildReceipt(requestId, boundActionId, action, subjectReferentId, arguments,
                        "not_delivered", "not_delivered", code, detail + " Current diagnostic snapshot omitted: successor_payload_capacity_exceeded.", null, null, request.InputProfile));
                }
                return false;
            }

            if (!IsCurrentRequestSnapshot(snapshot.Snapshot, request))
            {
                return Fail(
                    "stale_snapshot",
                    "The exact Player Environment snapshot changed; obtain a fresh observation.");
            }
            if (snapshot.Snapshot.BoundActions.Status != "complete")
                return Fail("bound_action_projection_incomplete", "The finite bound-action projection is not complete and cannot authorize input.");
            if (boundAction == null || binding == null)
                return Fail("bound_action_not_current", "The exact advertised bound action is no longer current.");
            var mutationRequest = new MutationAuthorizationRequest(
                request.ClientSessionId,
                request.ControllerLeaseId,
                request.ControllerGeneration);
            LegacyTerminalBounds.Preflight(preparation, BuildReceipt(requestId, boundActionId, action,
                subjectReferentId, arguments, "unknown", "unknown", LegacyTerminalBounds.MaximumField,
                LegacyTerminalBounds.MaximumField, null, preparation.OriginalAttribution, request.InputProfile));
            MutationAdmission admission = preparation.TryBegin();
            if (!admission.Accepted)
                return Fail(admission.ErrorCode ?? "controller_rejected", admission.Detail ?? "Mutation control was rejected.");

            preparation.ReleasePreparation();
            NativeInputResult started;
            try
            {
                started = StartPlayerEnvironmentInput(
                    snapshot,
                    binding.NativeAction,
                    parameters);
            }
            catch (Exception exception)
            {
                PlayerEnvironmentActionReceipt unknown = BuildReceipt(
                    requestId,
                    boundActionId,
                    action,
                    subjectReferentId,
                    arguments,
                    "unknown",
                    "unknown",
                    "input_delivery_unknown",
                    $"Native input may have been delivered before {exception.GetType().Name}; do not retry.",
                    null,
                    admission.Attribution,
                    request.InputProfile);
                preparation.Seal(unknown);
                return true;
            }
            if (started.LegacyDisposition == LegacyNativeInputDisposition.Unknown)
            {
                PlayerEnvironmentActionReceipt unknown = BuildReceipt(
                    requestId, boundActionId, action, subjectReferentId, arguments,
                    "unknown", "unknown", "input_delivery_unknown",
                    "The native input has a partial, unconfirmed or unknown outcome that this legacy receipt cannot represent; never retry.",
                    null, admission.Attribution, request.InputProfile);
                preparation.Seal(unknown);
                return true;
            }
            if (started.LegacyDisposition == LegacyNativeInputDisposition.NotDelivered)
                return Fail(
                    started.ErrorCode ?? "native_input_rejected",
                    started.Detail ?? "The native UI rejected this exact input.");

            PlayerEnvironmentSnapshot? postDeliveryObservation = null;
            string evidence = string.IsNullOrWhiteSpace(started.DeliveryEvidence)
                ? "native_ui_input_delivered"
                : started.DeliveryEvidence;
            string detail = $"Native UI input was delivered ({evidence}); the attached successor is an immediate post-delivery observation, not causal settlement.";
            try
            {
                postDeliveryObservation = Observe(request.InputProfile);
            }
            catch (Exception exception)
            {
                // Delivery is already known. A failed read must not be relabelled
                // as uncertain mutation; Re can safely obtain a fresh snapshot.
                detail = $"Native UI input was delivered; immediate successor read failed with {exception.GetType().Name}.";
            }
            PlayerEnvironmentActionReceipt applied = BuildReceipt(
                requestId,
                boundActionId,
                action,
                subjectReferentId,
                arguments,
                "delivered",
                "delivered",
                postDeliveryObservation == null ? "successor_observation_unavailable" : null,
                detail,
                postDeliveryObservation,
                admission.Attribution,
                request.InputProfile);
            try { preparation.Seal(applied); }
            catch (ResultPayloadCapacityException)
            {
                preparation.Reservation.ResetEncoding();
                preparation.Seal(BuildReceipt(requestId, boundActionId, action, subjectReferentId, arguments,
                    "delivered", "delivered", "successor_payload_capacity_exceeded",
                    "Native input was delivered; its optional immediate observation exceeded result capacity.",
                    null, admission.Attribution, request.InputProfile));
            }
            return true;
    }

    internal static string ActionRequestFingerprint(PlayerEnvironmentActionRequest request)
    {
        string fingerprint = StableIdentityHash.Object(new
        {
            request.ExpectedSnapshotId,
            request.BoundActionId,
            request.ClientSessionId,
            request.ControllerLeaseId,
            request.ControllerGeneration
        });
        return request.InputProfile == null
            ? fingerprint
            : StableIdentityHash.Object(new { request.InputProfile, fingerprint });
    }

    internal static bool IsCurrentRequestSnapshot(
        PlayerEnvironmentSnapshot snapshot,
        PlayerEnvironmentActionRequest request) =>
        string.Equals(snapshot.InputProfile, request.InputProfile, StringComparison.Ordinal)
        && string.Equals(snapshot.SnapshotId, request.ExpectedSnapshotId, StringComparison.Ordinal);

    internal static bool ReceiptMatchesInputProfile(
        PlayerEnvironmentActionReceipt receipt,
        string? inputProfile) =>
        string.Equals(receipt.InputProfile, inputProfile, StringComparison.Ordinal);

    private static NativeInputResult StartPlayerEnvironmentInput(
        SnapshotBuildResult snapshot,
        NativeUiBoundAction binding,
        IReadOnlyDictionary<string, string> parameters)
    {
        string operation = binding.Candidate.Operation;
        if (operation == NativeGeneratedCardChoice.SelectOperation
            && parameters.TryGetValue("card_id", out string? cardId)
            && parameters.TryGetValue("screen_id", out string? screenId))
        {
            return NativeGeneratedCardChoice.StartSelect(Entities, screenId, cardId);
        }
        if (operation == NativeGeneratedCardChoice.SkipOperation
            && parameters.TryGetValue("screen_id", out screenId))
            return NativeGeneratedCardChoice.StartSkip(Entities, screenId);
        if (snapshot.HostObservation.Surface is NativeBossRelicSelectionSurface bossRelicSelection)
        {
            return NativeBossRelicSelection.Start(
                Entities,
                bossRelicSelection,
                binding,
                parameters);
        }
        if (snapshot.HostObservation.Surface is NativeDeckCardSelectionSurface deckSelection)
        {
            return NativeDeckCardSelection.Start(
                Entities,
                deckSelection,
                binding,
                parameters);
        }
        if (snapshot.HostObservation.Surface is NativeDeckUpgradeSelectionSurface deckUpgradeSelection)
        {
            return NativeDeckUpgradeSelection.Start(
                Entities,
                deckUpgradeSelection,
                binding,
                parameters);
        }
        if (snapshot.HostObservation.Surface is NativeSimpleCardSelectionSurface simpleSelection)
        {
            return NativeSimpleCardSelection.Start(
                Entities,
                simpleSelection,
                binding,
                parameters);
        }
        if (snapshot.HostObservation.Surface is NativeCombatPileSelectionSurface combatPileSelection)
        {
            return NativeCombatPileSelection.Start(
                Entities,
                combatPileSelection,
                binding,
                parameters);
        }
        if (snapshot.HostObservation.Surface is RestSiteSurface restSite)
        {
            return NativeRestSite.Start(
                Entities,
                restSite,
                binding,
                parameters);
        }

        return NativeUiActionRuntime.StartNativeUiInput(
            snapshot.HostObservation,
            new NativeUiInput(binding.Candidate.Command, parameters),
            binding);
    }

    private static PlayerEnvironmentActionReceipt BuildReceipt(
        string requestId,
        string boundActionId,
        string action,
        string? subjectReferentId,
        IReadOnlyList<PlayerEnvironmentBoundActionArgument> arguments,
        string status,
        string delivery,
        string? reasonCode,
        string? detail,
        PlayerEnvironmentSnapshot? successor,
        MutationAttribution? attribution,
        string? inputProfile = null) =>
        new(
            PlayerEnvironmentContract.ProtocolVersion,
            PlayerEnvironmentContract.ReceiptSchema,
            requestId,
            delivery,
            new PlayerEnvironmentActionSummary(boundActionId, action, subjectReferentId, arguments),
            reasonCode,
            detail,
            new PlayerEnvironmentRetryPolicy(
                status == "not_delivered",
                status == "unknown" ? "unknown_delivery_never_retry" : "fresh_snapshot_required"),
            successor)
        {
            Attribution = attribution == null ? null : ToAttribution(attribution),
            InputProfile = inputProfile
        };

}
