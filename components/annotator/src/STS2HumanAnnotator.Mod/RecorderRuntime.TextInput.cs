using System.Text.Json.Nodes;
using System.Threading;
using Godot;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using STS2Connector.PlayerEnvironment.Witness;
using STS2HumanAnnotator.Core;

namespace STS2HumanAnnotator.Mod;

internal static partial class RecorderRuntime
{
    internal sealed class HumanTextCardScope
    {
        internal HumanTextCardScope(
            HumanTextCardScope? previous, RecordingSessionStore store,
            string sessionId, string timelineId, string runId,
            NPlayerHand hand, NHandCardHolder holder)
        {
            Previous = previous;
            Store = store;
            SessionId = sessionId;
            TimelineId = timelineId;
            RunId = runId;
            Hand = hand;
            Holder = holder;
            ObservedAt = DateTimeOffset.UtcNow;
        }

        internal HumanTextCardScope? Previous { get; }
        internal RecordingSessionStore Store { get; }
        internal string SessionId { get; }
        internal string TimelineId { get; }
        internal string RunId { get; }
        internal NPlayerHand Hand { get; }
        internal NHandCardHolder Holder { get; }
        internal CardModel? Card { get; set; }
        internal DateTimeOffset ObservedAt { get; set; }
        internal ProcessLocalTextMenuWitnessFrame? Frame { get; set; }
        internal RecorderEnvironmentIdentity? Environment { get; set; }
        internal ProcessLocalTextMenuMatch? Match { get; set; }
        internal NCardPlay? Carrier { get; set; }
        internal string? CaptureFailureReason { get; set; }
        internal string? FactoryIssue { get; set; }
        internal bool Suppressed { get; set; }
        internal bool Finished { get; set; }
    }

    private static readonly AsyncLocal<HumanTextCardScope?> HumanTextCardCurrent = new();
    private static long _humanTextInputSequence;
    private static bool _humanTextInputHealthy = true;
    private static int _humanTextInputPendingScopes;

    /// <summary>Freeze H before the native holder leaves the hand. The existing
    /// card-play recorder path is independent and keeps its own semantics.</summary>
    internal static HumanTextCardScope? BeginHumanTextCardInput(
        NPlayerHand hand, NHandCardHolder holder)
    {
        try
        {
            HumanTextCardScope scope;
            lock (Gate)
            {
                if (!_initialized || _lifecycle.State != RecordingLifecycleState.Recording
                    || _store == null || !_humanTextInputHealthy
                    || SessionId == null || TimelineId == null)
                    return null;
                scope = new HumanTextCardScope(
                    HumanTextCardCurrent.Value, _store, SessionId, TimelineId,
                    _currentRunId, hand, holder);
                _humanTextInputPendingScopes++;
            }
            HumanTextCardCurrent.Value = scope;
            try
            {
                if (PlayerEnvironmentTextMenuWitness.IsExternalControllerActive)
                {
                    scope.Suppressed = true;
                    return scope;
                }
                CardModel? card = holder.CardModel;
                scope.Card = card;
                if (card == null)
                {
                    scope.CaptureFailureReason = "card_model_unavailable_at_prefix";
                    return scope;
                }
                ProcessLocalTextMenuWitnessFrame frame = PlayerEnvironmentTextMenuWitness.Capture();
                if (frame.ExternalControllerActive)
                {
                    scope.Suppressed = true;
                    return scope;
                }
                scope.Frame = frame;
                scope.ObservedAt = frame.Snapshot.ObservedAt;
                scope.Environment = BuildEnvironment(frame.Capabilities, frame.SourceDigest);
                scope.Match = frame.Resolve(new ProcessLocalObservedTextMenuAction(
                    "begin_card_play", hand, card,
                    new Dictionary<string, object>(StringComparer.Ordinal)
                    {
                        ["holder"] = holder
                    }));
            }
            catch (Exception exception)
            {
                scope.CaptureFailureReason = "text_menu_capture_failed_" + exception.GetType().Name;
                NativeUiObservationSafety.Report("human_text_input.capture", exception);
            }
            return scope;
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("human_text_input.prefix", exception);
            return null;
        }
    }

    /// <summary>The factory callback can only bind this invocation's exact
    /// holder and card. It cannot establish accepted Human input by itself.</summary>
    internal static void BindHumanTextCardCarrier(NCardPlay carrier)
    {
        try
        {
            HumanTextCardScope? scope = HumanTextCardCurrent.Value;
            if (scope == null || scope.Finished || scope.Suppressed)
                return;
            if (scope.Card == null
                || !ReferenceEquals(carrier.Holder, scope.Holder)
                || !ReferenceEquals(carrier.Holder.CardModel, scope.Card))
            {
                scope.FactoryIssue ??= "factory_operand_mismatch";
                return;
            }
            if (scope.Carrier != null)
            {
                scope.FactoryIssue ??= "multiple_card_play_factories";
                return;
            }
            scope.Carrier = carrier;
        }
        catch (Exception exception)
        {
            HumanTextCardScope? scope = HumanTextCardCurrent.Value;
            if (scope != null) scope.FactoryIssue ??= "factory_capture_failed";
            NativeUiObservationSafety.Report("human_text_input.factory", exception);
        }
    }

    internal static void FinishHumanTextCardInput(
        HumanTextCardScope? scope, Exception? nativeException)
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
            string disposition;
            if (reason != null || scope.Frame == null || scope.Environment == null)
            {
                disposition = HumanTextInputObservationContract.CaptureFailed;
                reason ??= "text_menu_capture_unavailable";
            }
            else if (scope.Match?.Status != "exact_unique"
                || scope.Match.MatchCount != 1 || scope.Match.Action == null)
            {
                disposition = HumanTextInputObservationContract.NotMapped;
                reason = "text_menu_mapping_" + (scope.Match?.Status ?? "unavailable");
            }
            else if (nativeException != null || scope.FactoryIssue != null
                || scope.Carrier == null
                || !IsCurrentHumanTextCardOperation(scope))
            {
                disposition = HumanTextInputObservationContract.RejectedOrCancelled;
                reason = nativeException != null
                    ? "native_card_play_threw_" + nativeException.GetType().Name
                    : scope.FactoryIssue ?? "native_card_play_not_current_at_return";
            }
            else if (scope.RunId == "run-unassigned")
            {
                disposition = HumanTextInputObservationContract.NotMapped;
                reason = "run_identity_unassigned_at_prefix";
            }
            else
            {
                disposition = HumanTextInputObservationContract.AcceptedInput;
                reason = null;
            }

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
                NativeUiObservationSafety.Report("human_text_input.serialize", exception);
            }

            // A snapshot without its exact environment cannot be admitted.
            if (environment == null)
            {
                snapshot = null;
                chosenAction = null;
            }
            if (disposition == HumanTextInputObservationContract.CaptureFailed)
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
                    NativeWitnessIdentity.Get(scope.Hand, "text_hand"),
                    scope.Card == null ? null : NativeWitnessIdentity.Get(scope.Card, "text_card"),
                    scope.Carrier == null ? null : NativeWitnessIdentity.Get(scope.Carrier, "text_carrier"),
                    HumanTextInputObservationContract.NativeMechanism,
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
                NativeUiObservationSafety.Report("human_text_input.append", appendFailure);
        }
        catch (Exception exception)
        {
            lock (Gate)
                MarkHumanTextInputFailureUnsafe(exception.Message);
            NativeUiObservationSafety.Report("human_text_input.finalize", exception);
        }
        finally
        {
            if (finishStarted)
            {
                if (ReferenceEquals(HumanTextCardCurrent.Value, scope))
                    HumanTextCardCurrent.Value = scope.Previous;
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
                        NativeUiObservationSafety.Report("human_text_input.close", exception);
                    }
                }
            }
        }
    }

    // A failed append may be rejected before the store's write path has a
    // chance to mark itself unhealthy. Never seal such a session cleanly.
    private static void MarkHumanTextInputFailureUnsafe(string detail)
    {
        _humanTextInputHealthy = false;
        _closeDispositionPersistenceFailed = true;
        _runtimeState = "human_text_input_persistence_failed";
        _detail = detail;
        if (_lifecycle.State == RecordingLifecycleState.Closing)
            _closeout = _closeout with
            {
                State = "closing",
                Detail = "Human text input evidence failed; the session cannot be sealed."
            };
    }

    private static bool IsCurrentHumanTextCardOperation(HumanTextCardScope scope)
    {
        try
        {
            NCardPlay carrier = scope.Carrier!;
            return ReferenceEquals(NPlayerHand.Instance, scope.Hand)
                && scope.Hand.InCardPlay
                && ReferenceEquals(carrier.Holder, scope.Holder)
                && ReferenceEquals(carrier.Holder.CardModel, scope.Card)
                && ReferenceEquals(carrier.GetParent(), scope.Hand)
                // CancelPlayCard queues the old child for deletion before a
                // shortcut can synchronously begin the next card play.
                && scope.Hand.GetChildren().OfType<NCardPlay>()
                    .Where(child => GodotObject.IsInstanceValid(child)
                        && !child.IsQueuedForDeletion())
                    .SingleOrDefault() is { } current
                && ReferenceEquals(current, carrier);
        }
        catch (Exception)
        {
            return false;
        }
    }
}
