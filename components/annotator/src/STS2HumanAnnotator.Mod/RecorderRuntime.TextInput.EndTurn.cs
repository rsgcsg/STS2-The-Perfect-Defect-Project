using System.Text.Json.Nodes;
using System.Threading;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.Nodes.Combat;
using STS2Connector.PlayerEnvironment.Witness;
using STS2HumanAnnotator.Core;

namespace STS2HumanAnnotator.Mod;

internal static partial class RecorderRuntime
{
    internal sealed class HumanTextEndTurnScope
    {
        internal HumanTextEndTurnScope(HumanTextEndTurnScope? previous,
            RecordingSessionStore store, string sessionId, string timelineId,
            string runId, NEndTurnButton button)
        {
            Previous = previous;
            Store = store;
            SessionId = sessionId;
            TimelineId = timelineId;
            RunId = runId;
            Button = button;
            ObservedAt = DateTimeOffset.UtcNow;
        }

        internal HumanTextEndTurnScope? Previous { get; }
        internal RecordingSessionStore Store { get; }
        internal string SessionId { get; }
        internal string TimelineId { get; }
        internal string RunId { get; }
        internal NEndTurnButton Button { get; }
        internal DateTimeOffset ObservedAt { get; set; }
        internal ProcessLocalTextMenuWitnessFrame? Frame { get; set; }
        internal RecorderEnvironmentIdentity? Environment { get; set; }
        internal ProcessLocalTextMenuMatch? Match { get; set; }
        internal EndPlayerTurnAction? Request { get; set; }
        internal string? CaptureFailureReason { get; set; }
        internal bool MultipleRequests { get; set; }
        internal bool Suppressed { get; set; }
        internal bool Finished { get; set; }
    }

    private static readonly AsyncLocal<HumanTextEndTurnScope?> HumanTextEndTurnCurrent = new();

    internal static HumanTextEndTurnScope? BeginHumanTextEndTurnInput(
        NEndTurnButton button)
    {
        try
        {
            HumanTextEndTurnScope scope;
            lock (Gate)
            {
                if (!_initialized || _lifecycle.State != RecordingLifecycleState.Recording
                    || _store == null || !_humanTextInputHealthy
                    || SessionId == null || TimelineId == null)
                    return null;
                scope = new HumanTextEndTurnScope(HumanTextEndTurnCurrent.Value,
                    _store, SessionId, TimelineId, _currentRunId, button);
                _humanTextInputPendingScopes++;
            }
            HumanTextEndTurnCurrent.Value = scope;
            try
            {
                if (PlayerEnvironmentTextMenuWitness.IsExternalControllerActive)
                {
                    scope.Suppressed = true;
                    return scope;
                }
                ProcessLocalTextMenuWitnessFrame frame =
                    PlayerEnvironmentTextMenuWitness.Capture();
                if (frame.ExternalControllerActive)
                {
                    scope.Suppressed = true;
                    return scope;
                }
                scope.Frame = frame;
                scope.ObservedAt = frame.Snapshot.ObservedAt;
                scope.Environment = BuildEnvironment(frame.Capabilities, frame.SourceDigest);
                scope.Match = frame.Resolve(new ProcessLocalObservedTextMenuAction(
                    "end_turn", button, null,
                    new Dictionary<string, object>(StringComparer.Ordinal)));
            }
            catch (Exception exception)
            {
                scope.CaptureFailureReason = "text_menu_capture_failed_" + exception.GetType().Name;
                NativeUiObservationSafety.Report("human_text_input.end_turn_capture", exception);
            }
            return scope;
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("human_text_input.end_turn_prefix", exception);
            return null;
        }
    }

    // Only the exact RequestEnqueue invocation nested inside this button's
    // callback can prove native submission. RequestEnqueue may subsequently
    // defer or relay the action; this is input evidence, not Commit.
    internal static void ObserveHumanTextEndTurnRequest(GameAction action)
    {
        try
        {
            HumanTextEndTurnScope? scope = HumanTextEndTurnCurrent.Value;
            if (scope == null || scope.Finished || scope.Suppressed
                || action is not EndPlayerTurnAction request)
                return;
            if (scope.Request != null)
                scope.MultipleRequests = true;
            else
                scope.Request = request;
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("human_text_input.end_turn_request", exception);
        }
    }

    internal static void FinishHumanTextEndTurnInput(
        HumanTextEndTurnScope? scope, Exception? nativeException)
    {
        if (scope == null) return;
        bool finishStarted = false;
        try
        {
            if (scope.Finished) return;
            scope.Finished = true;
            finishStarted = true;
            if (scope.Suppressed || PlayerEnvironmentTextMenuWitness.IsExternalControllerActive)
                return;

            string? reason = scope.CaptureFailureReason;
            bool captureAvailable = reason == null && scope.Frame != null
                && scope.Environment != null;
            bool exactMenu = scope.Match?.Status == "exact_unique"
                && scope.Match.MatchCount == 1 && scope.Match.Action != null;
            string disposition = HumanTextInputNativeProof.ClassifyEndTurnInput(
                captureAvailable, exactMenu,
                scope.Request != null && !scope.MultipleRequests,
                nativeException == null, scope.RunId != "run-unassigned");
            if (disposition == HumanTextInputObservationContract.CaptureFailed)
                reason ??= "text_menu_capture_unavailable";
            else if (!exactMenu)
                reason = "text_menu_mapping_" + (scope.Match?.Status ?? "unavailable");
            else if (nativeException != null)
                reason = "native_end_turn_threw_" + nativeException.GetType().Name;
            else if (scope.MultipleRequests)
                reason = "multiple_end_turn_requests";
            else if (scope.Request == null)
                reason = "native_end_turn_request_not_submitted";
            else if (scope.RunId == "run-unassigned")
                reason = "run_identity_unassigned_at_prefix";

            JsonObject? snapshot = null;
            JsonObject? chosenAction = null;
            RecorderEnvironmentIdentity? environment = scope.Environment;
            try
            {
                if (scope.Frame != null && environment != null)
                {
                    snapshot = ToNode(scope.Frame.Snapshot) as JsonObject
                        ?? throw new InvalidOperationException("Text snapshot is not an object.");
                    if (scope.Match?.Action != null)
                        chosenAction = ToNode(scope.Match.Action) as JsonObject;
                }
            }
            catch (Exception exception)
            {
                disposition = HumanTextInputObservationContract.CaptureFailed;
                reason = "text_menu_serialization_failed_" + exception.GetType().Name;
                snapshot = null;
                chosenAction = null;
                NativeUiObservationSafety.Report("human_text_input.end_turn_serialize", exception);
            }
            if (environment == null || disposition == HumanTextInputObservationContract.CaptureFailed)
            {
                snapshot = null;
                chosenAction = null;
            }

            Exception? appendFailure = null;
            lock (Gate)
            {
                if (!_humanTextInputHealthy || !ReferenceEquals(_store, scope.Store)
                    || SessionId != scope.SessionId || TimelineId != scope.TimelineId
                    || _lifecycle.State is RecordingLifecycleState.Ready
                        or RecordingLifecycleState.Closed)
                    return;
                long sequence = _humanTextInputSequence + 1;
                var observation = new HumanTextInputObservation(
                    HumanTextInputObservationContract.SchemaVersion,
                    HumanTextInputObservationContract.Schema,
                    sequence, $"human-text-{Guid.NewGuid():N}",
                    scope.SessionId, scope.TimelineId, scope.RunId,
                    scope.ObservedAt, DateTimeOffset.UtcNow,
                    environment, snapshot,
                    snapshot == null ? null : EvidenceIdentity.Sha256Json(snapshot),
                    chosenAction, scope.Match?.Status ?? "capture_failed",
                    scope.Match?.MatchCount ?? 0,
                    HumanTextInputObservationContract.ExactMappingBasis,
                    NativeWitnessIdentity.Get(scope.Button, "text_end_turn_button"),
                    NativeWitnessIdentity.Get(scope.Button, "text_end_turn_button"),
                    scope.Request == null ? null : NativeWitnessIdentity.Get(
                        scope.Request, "text_end_turn_request"),
                    HumanTextInputObservationContract.EndTurnRequestSubmitted,
                    disposition, reason, false);
                try
                {
                    scope.Store.AppendHumanTextInputObservation(observation);
                    _humanTextInputSequence = sequence;
                }
                catch (Exception exception)
                {
                    MarkHumanTextInputFailureUnsafe(exception.Message);
                    appendFailure = exception;
                }
            }
            if (appendFailure != null)
                NativeUiObservationSafety.Report("human_text_input.end_turn_append", appendFailure);
        }
        catch (Exception exception)
        {
            lock (Gate)
                MarkHumanTextInputFailureUnsafe(exception.Message);
            NativeUiObservationSafety.Report("human_text_input.end_turn_finalize", exception);
        }
        finally
        {
            if (finishStarted)
            {
                if (ReferenceEquals(HumanTextEndTurnCurrent.Value, scope))
                    HumanTextEndTurnCurrent.Value = scope.Previous;
                bool resumeClose;
                lock (Gate)
                {
                    _humanTextInputPendingScopes--;
                    resumeClose = _lifecycle.State == RecordingLifecycleState.Closing;
                }
                if (resumeClose)
                {
                    try { FinalizeClose(); }
                    catch (Exception exception)
                    {
                        lock (Gate)
                            MarkHumanTextInputFailureUnsafe(exception.Message);
                        NativeUiObservationSafety.Report("human_text_input.end_turn_close", exception);
                    }
                }
            }
        }
    }
}
