using System.Reflection;
using System.Text.Json.Nodes;
using System.Threading;
using Godot;
using HarmonyLib;
using MegaCrit.Sts2.Core.ControllerInput;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Combat;
using STS2Connector.PlayerEnvironment.Witness;
using STS2HumanAnnotator.Core;

namespace STS2HumanAnnotator.Mod;

internal static partial class RecorderRuntime
{
    internal sealed class HumanTextContinuationScope
    {
        internal HumanTextContinuationScope(HumanTextContinuationScope? previous,
            RecordingSessionStore store, string sessionId, string timelineId,
            string runId, NControllerCardPlay carrier, string verb, string mechanism,
            NTargetManager? manager, NCreature? target, bool requestedCancel)
        {
            Previous = previous;
            Store = store;
            SessionId = sessionId;
            TimelineId = timelineId;
            RunId = runId;
            Carrier = carrier;
            Verb = verb;
            Mechanism = mechanism;
            Manager = manager;
            Target = target;
            RequestedCancel = requestedCancel;
            ObservedAt = DateTimeOffset.UtcNow;
        }

        internal HumanTextContinuationScope? Previous { get; }
        internal RecordingSessionStore Store { get; }
        internal string SessionId { get; }
        internal string TimelineId { get; }
        internal string RunId { get; }
        internal NControllerCardPlay Carrier { get; }
        internal CardModel? Card { get; set; }
        internal string Verb { get; }
        internal string Mechanism { get; }
        internal NTargetManager? Manager { get; }
        internal NCreature? Target { get; }
        internal bool RequestedCancel { get; }
        internal DateTimeOffset ObservedAt { get; set; }
        internal ProcessLocalTextMenuWitnessFrame? Frame { get; set; }
        internal RecorderEnvironmentIdentity? Environment { get; set; }
        internal ProcessLocalTextMenuMatch? Match { get; set; }
        internal string? CaptureFailureReason { get; set; }
        internal bool Suppressed { get; set; }
        internal bool Finished { get; set; }
        internal bool NativeFinishMatched { get; set; }
        internal bool NativeContinuationCalled { get; set; }
    }

    private sealed record HumanTextTargetBinding(
        NTargetManager Manager, NControllerCardPlay Carrier,
        string SessionId, string TimelineId);

    private static readonly AsyncLocal<NControllerCardPlay?> HumanTextStartingController = new();
    private static readonly AsyncLocal<HumanTextContinuationScope?> HumanTextContinuationCurrent = new();
    private static readonly Dictionary<NTargetManager, HumanTextTargetBinding> HumanTextTargetBindings =
        new(ReferenceEqualityComparer.Instance);
    private static readonly PropertyInfo? HumanTextHoveredNodeProperty =
        AccessTools.Property(typeof(NTargetManager), "HoveredNode");

    internal static NControllerCardPlay? BeginHumanTextTargetSetup(NControllerCardPlay carrier)
    {
        NControllerCardPlay? previous = HumanTextStartingController.Value;
        HumanTextStartingController.Value = carrier;
        return previous;
    }

    internal static void EndHumanTextTargetSetup(NControllerCardPlay? previous) =>
        HumanTextStartingController.Value = previous;

    /// <summary>Called only from the native StartTargeting(control) invocation
    /// inside this carrier's Start. No ambient current card is inferred.</summary>
    internal static void BindHumanTextTargetManager(
        NTargetManager manager, Control control, TargetMode mode)
    {
        try
        {
            // Every new manager operation invalidates the previous carrier,
            // including starts owned by a different native UI family.
            lock (Gate) HumanTextTargetBindings.Remove(manager);
            NControllerCardPlay? carrier = HumanTextStartingController.Value;
            if (carrier == null || mode != TargetMode.Controller
                || !ReferenceEquals(control, carrier.Holder.CardNode)
                || !IsCurrentHumanTextController(carrier)) return;
            lock (Gate)
            {
                if (_lifecycle.State != RecordingLifecycleState.Recording
                    || SessionId == null || TimelineId == null) return;
                HumanTextTargetBindings[manager] = new(
                    manager, carrier, SessionId, TimelineId);
            }
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("human_text_input.target_binding", exception);
        }
    }

    internal static HumanTextContinuationScope? BeginHumanTextControllerInput(
        NControllerCardPlay carrier, InputEvent input)
    {
        try
        {
            if (input is not InputEventAction action || !IsCurrentHumanTextController(carrier))
                return null;
            CardModel? card = carrier.Holder.CardModel;
            if (card == null || card.TargetType is TargetType.AnyEnemy or TargetType.AnyAlly)
                return null;
            bool confirm = action.IsActionPressed(MegaInput.select);
            bool cancel = action.IsActionPressed(MegaInput.cancel)
                || action.IsActionPressed(MegaInput.pauseAndBack)
                || action.IsActionPressed(MegaInput.topPanel);
            if (!confirm && !cancel) return null;
            return BeginHumanTextContinuation(carrier,
                confirm ? "confirm_card" : "cancel_card_play",
                confirm ? HumanTextInputObservationContract.ControllerConfirmedInputSignal
                    : HumanTextInputObservationContract.ControllerCanceledInputSignal,
                null, null, !confirm);
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("human_text_input.controller_prefix", exception);
            return null;
        }
    }

    internal static HumanTextContinuationScope? BeginHumanTextTargetInput(
        NTargetManager manager, InputEvent input)
    {
        try
        {
            if (input is not InputEventAction action || !manager.IsInSelection)
                return null;
            bool confirm = action.IsActionPressed(MegaInput.select);
            bool cancel = action.IsActionPressed(MegaInput.cancel)
                || action.IsActionPressed(MegaInput.pauseAndBack)
                || action.IsActionPressed(MegaInput.topPanel);
            if (!confirm && !cancel) return null;
            HumanTextTargetBinding? binding;
            lock (Gate) HumanTextTargetBindings.TryGetValue(manager, out binding);
            if (binding == null || !ReferenceEquals(binding.Manager, manager)
                || binding.SessionId != SessionId || binding.TimelineId != TimelineId
                || !IsCurrentHumanTextController(binding.Carrier)) return null;
            Node? hovered = HumanTextHoveredNodeProperty?.GetValue(manager) as Node;
            NCreature? target = hovered as NCreature;
            return BeginHumanTextContinuation(binding.Carrier,
                confirm ? "confirm_target" : "cancel_card_play",
                confirm ? HumanTextInputObservationContract.ControllerTargetFinishInput
                    : HumanTextInputObservationContract.ControllerTargetCanceledInput,
                manager, confirm ? target : null, !confirm);
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("human_text_input.target_prefix", exception);
            return null;
        }
    }

    private static HumanTextContinuationScope? BeginHumanTextContinuation(
        NControllerCardPlay carrier, string verb, string mechanism,
        NTargetManager? manager, NCreature? target, bool requestedCancel)
    {
        try
        {
            HumanTextContinuationScope scope;
            lock (Gate)
            {
                if (!_initialized || _lifecycle.State != RecordingLifecycleState.Recording
                    || _store == null || !_humanTextInputHealthy
                    || SessionId == null || TimelineId == null) return null;
                scope = new(HumanTextContinuationCurrent.Value, _store,
                    SessionId, TimelineId, _currentRunId, carrier, verb,
                    mechanism, manager, target, requestedCancel);
                _humanTextInputPendingScopes++;
            }
            HumanTextContinuationCurrent.Value = scope;
            try
            {
                if (PlayerEnvironmentTextMenuWitness.IsExternalControllerActive)
                {
                    scope.Suppressed = true;
                    return scope;
                }
                scope.Card = carrier.Holder.CardModel;
                if (scope.Card == null)
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
                IReadOnlyDictionary<string, object> arguments = target == null
                    ? new Dictionary<string, object>(StringComparer.Ordinal)
                    : new Dictionary<string, object>(StringComparer.Ordinal) { ["target"] = target };
                scope.Match = frame.Resolve(new ProcessLocalObservedTextMenuAction(
                    verb, carrier, scope.Card, arguments));
            }
            catch (Exception exception)
            {
                scope.CaptureFailureReason = "text_menu_capture_failed_" + exception.GetType().Name;
                NativeUiObservationSafety.Report("human_text_input.continuation_capture", exception);
            }
            return scope;
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("human_text_input.continuation_prefix", exception);
            return null;
        }
    }

    internal static void ObserveHumanTextTargetFinish(NTargetManager manager, bool cancel)
    {
        lock (Gate) HumanTextTargetBindings.Remove(manager);
        HumanTextContinuationScope? scope = HumanTextContinuationCurrent.Value;
        if (scope == null || scope.Finished || !ReferenceEquals(scope.Manager, manager)) return;
        try
        {
            Node? hovered = HumanTextHoveredNodeProperty?.GetValue(manager) as Node;
            scope.NativeFinishMatched = scope.RequestedCancel
                ? cancel
                : !cancel && ReferenceEquals(hovered, scope.Target);
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("human_text_input.target_finish", exception);
        }
    }

    internal static void ObserveHumanTextTryPlay(NCardPlay carrier, Creature? target)
    {
        try
        {
            HumanTextContinuationScope? scope = HumanTextContinuationCurrent.Value;
            if (scope == null || scope.Finished || scope.RequestedCancel
                || scope.Manager != null || !ReferenceEquals(scope.Carrier, carrier)) return;
            scope.NativeContinuationCalled = target == null;
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("human_text_input.try_play", exception);
        }
    }

    internal static void ObserveHumanTextCancel(NCardPlay carrier)
    {
        try
        {
            HumanTextContinuationScope? scope = HumanTextContinuationCurrent.Value;
            if (scope == null || scope.Finished || !scope.RequestedCancel
                || scope.Manager != null || !ReferenceEquals(scope.Carrier, carrier)) return;
            scope.NativeContinuationCalled = true;
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("human_text_input.cancel", exception);
        }
    }

    internal static void FinishHumanTextContinuation(
        HumanTextContinuationScope? scope, Exception? nativeException)
    {
        if (scope == null || scope.Finished) return;
        scope.Finished = true;
        try
        {
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
            // Target manager finishes the input synchronously. Its async
            // SelectionFinished consumer may run later, so it is not an input
            // acceptance requirement and cannot establish Commit or S'.
            else if (nativeException != null || (scope.Manager == null
                ? !scope.NativeContinuationCalled : !scope.NativeFinishMatched))
            {
                disposition = HumanTextInputObservationContract.RejectedOrCancelled;
                reason = nativeException != null
                    ? "native_input_threw_" + nativeException.GetType().Name
                    : "same_input_native_continuation_unproved";
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

            JsonObject? snapshot = null, chosenAction = null;
            try
            {
                if (scope.Frame != null && scope.Environment != null)
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
                NativeUiObservationSafety.Report("human_text_input.continuation_serialize", exception);
            }
            if (scope.Environment == null || disposition == HumanTextInputObservationContract.CaptureFailed)
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
                        or RecordingLifecycleState.Closed) return;
                long sequence = _humanTextInputSequence + 1;
                var observation = new HumanTextInputObservation(
                    HumanTextInputObservationContract.SchemaVersion,
                    HumanTextInputObservationContract.Schema,
                    sequence, $"human-text-{Guid.NewGuid():N}",
                    scope.SessionId, scope.TimelineId, scope.RunId,
                    scope.ObservedAt, DateTimeOffset.UtcNow,
                    scope.Environment, snapshot,
                    snapshot == null ? null : EvidenceIdentity.Sha256Json(snapshot),
                    chosenAction, scope.Match?.Status ?? "capture_failed",
                    scope.Match?.MatchCount ?? 0,
                    HumanTextInputObservationContract.ExactMappingBasis,
                    NativeWitnessIdentity.Get(scope.Carrier, "text_carrier"),
                    scope.Card == null ? null : NativeWitnessIdentity.Get(scope.Card, "text_card"),
                    NativeWitnessIdentity.Get(scope.Carrier, "text_carrier"),
                    scope.Mechanism, disposition, reason, false);
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
                NativeUiObservationSafety.Report("human_text_input.continuation_append", appendFailure);
        }
        catch (Exception exception)
        {
            lock (Gate) MarkHumanTextInputFailureUnsafe(exception.Message);
            NativeUiObservationSafety.Report("human_text_input.continuation_finalize", exception);
        }
        finally
        {
            if (ReferenceEquals(HumanTextContinuationCurrent.Value, scope))
                HumanTextContinuationCurrent.Value = scope.Previous;
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
                    lock (Gate) MarkHumanTextInputFailureUnsafe(exception.Message);
                    NativeUiObservationSafety.Report("human_text_input.continuation_close", exception);
                }
            }
        }
    }

    private static bool IsCurrentHumanTextController(NControllerCardPlay carrier)
    {
        try
        {
            NPlayerHand? hand = NPlayerHand.Instance;
            return hand != null && hand.InCardPlay
                && ReferenceEquals(carrier.GetParent(), hand)
                && !carrier.IsQueuedForDeletion()
                && ReferenceEquals(carrier.Holder.CardModel, carrier.Holder.CardNode?.Model)
                && hand.GetChildren().OfType<NCardPlay>()
                    .Where(child => GodotObject.IsInstanceValid(child)
                        && !child.IsQueuedForDeletion())
                    .SingleOrDefault() is { } current
                && ReferenceEquals(current, carrier);
        }
        catch (Exception) { return false; }
    }
}
