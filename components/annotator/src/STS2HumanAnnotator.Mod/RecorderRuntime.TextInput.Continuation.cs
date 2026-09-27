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
            string runId, NCardPlay carrier, InputEvent input, string verb,
            string mechanism, NTargetManager? manager, NCreature? target,
            bool requestedCancel)
        {
            Previous = previous;
            Store = store;
            SessionId = sessionId;
            TimelineId = timelineId;
            RunId = runId;
            Carrier = carrier;
            Input = input;
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
        internal NCardPlay Carrier { get; }
        internal InputEvent Input { get; }
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
        NTargetManager Manager, NCardPlay Carrier,
        string SessionId, string TimelineId);

    private static readonly HumanTextInputClaimGate HumanTextConsumedInputs = new();
    private static readonly AsyncLocal<NCardPlay?> HumanTextStartingCard = new();
    private static readonly AsyncLocal<HumanTextContinuationScope?> HumanTextContinuationCurrent = new();
    private static readonly Dictionary<NTargetManager, HumanTextTargetBinding> HumanTextTargetBindings =
        new(ReferenceEqualityComparer.Instance);
    private static readonly PropertyInfo? HumanTextHoveredNodeProperty =
        AccessTools.Property(typeof(NTargetManager), "HoveredNode");
    private static readonly FieldInfo? HumanTextMouseCancelShortcutField =
        AccessTools.Field(typeof(NMouseCardPlay), "_cancelShortcut");
    private static readonly FieldInfo? HumanTextTargetExitConditionField =
        AccessTools.Field(typeof(NTargetManager), "_exitEarlyCondition");

    internal static NCardPlay? BeginHumanTextTargetSetup(NCardPlay carrier)
    {
        NCardPlay? previous = HumanTextStartingCard.Value;
        HumanTextStartingCard.Value = carrier;
        return previous;
    }

    internal static void EndHumanTextTargetSetup(NCardPlay? previous) =>
        HumanTextStartingCard.Value = previous;

    internal static void InvalidateHumanTextTargetManager(NTargetManager manager)
    {
        lock (Gate) HumanTextTargetBindings.Remove(manager);
    }

    /// <summary>Called only from the native StartTargeting(control) invocation
    /// inside this carrier's synchronous entry. No ambient current card is inferred.</summary>
    internal static void BindHumanTextTargetManager(
        NTargetManager manager, Control control, TargetMode mode)
    {
        try
        {
            NCardPlay? carrier = HumanTextStartingCard.Value;
            if (!ReferenceEquals(NTargetManager.Instance, manager)
                || carrier == null || !((carrier is NControllerCardPlay
                    && mode == TargetMode.Controller)
                || (carrier is NMouseCardPlay && mode is
                    TargetMode.ReleaseMouseToTarget or TargetMode.ClickMouseToTarget))
                || !ReferenceEquals(control, carrier.Holder.CardNode)
                || !IsCurrentHumanTextCardCarrier(carrier)
                || (carrier is NMouseCardPlay
                    && !(HumanTextTargetExitConditionField?.GetValue(manager)
                        is Func<bool> exit && ReferenceEquals(exit.Target, carrier)))) return;
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
            if (input is not InputEventAction action || !IsCurrentHumanTextCardCarrier(carrier))
                return null;
            CardModel? card = carrier.Holder.CardModel;
            if (card == null || card.TargetType is TargetType.AnyEnemy or TargetType.AnyAlly)
                return null;
            bool confirm = action.IsActionPressed(MegaInput.select);
            bool cancel = action.IsActionPressed(MegaInput.cancel)
                || action.IsActionPressed(MegaInput.pauseAndBack)
                || action.IsActionPressed(MegaInput.topPanel);
            if (!confirm && !cancel) return null;
            return BeginHumanTextContinuation(carrier, input,
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

    internal static HumanTextContinuationScope? BeginHumanTextMouseInput(
        NMouseCardPlay carrier, InputEvent input)
    {
        try
        {
            if (!IsCurrentHumanTextCardCarrier(carrier)) return null;
            bool rightPress = input is InputEventMouseButton
                { ButtonIndex: MouseButton.Right } button && button.IsPressed();
            bool shortcut = HumanTextMouseCancelShortcutField?.GetValue(carrier)
                is StringName name && input.IsActionPressed(name);
            if (!rightPress && !shortcut) return null;
            return BeginHumanTextContinuation(carrier, input,
                "cancel_card_play",
                HumanTextInputObservationContract.MouseCanceledInputSignal,
                null, null, requestedCancel: true);
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("human_text_input.mouse_prefix", exception);
            return null;
        }
    }

    internal static HumanTextContinuationScope? BeginHumanTextTargetInput(
        NTargetManager manager, InputEvent input)
    {
        try
        {
            if (!manager.IsInSelection) return null;
            bool mouseEvent = input is InputEventMouseButton;
            if (!mouseEvent && input is not InputEventAction) return null;
            bool confirm;
            bool cancel;
            if (input is InputEventMouseButton button)
            {
                confirm = button.ButtonIndex == MouseButton.Left && button.IsReleased();
                cancel = button.ButtonIndex == MouseButton.Right && button.IsPressed();
            }
            else
            {
                var action = (InputEventAction)input;
                confirm = action.IsActionPressed(MegaInput.select);
                cancel = action.IsActionPressed(MegaInput.cancel)
                    || action.IsActionPressed(MegaInput.pauseAndBack)
                    || action.IsActionPressed(MegaInput.topPanel);
            }
            if (!confirm && !cancel) return null;
            HumanTextTargetBinding? binding;
            lock (Gate) HumanTextTargetBindings.TryGetValue(manager, out binding);
            if (binding == null || !ReferenceEquals(NTargetManager.Instance, manager)
                || !ReferenceEquals(binding.Manager, manager)
                || binding.SessionId != SessionId || binding.TimelineId != TimelineId
                || !IsCurrentHumanTextCardCarrier(binding.Carrier)
                || (binding.Carrier is NMouseCardPlay
                    && !(HumanTextTargetExitConditionField?.GetValue(manager)
                        is Func<bool> exit && ReferenceEquals(exit.Target, binding.Carrier)))
                || (mouseEvent && binding.Carrier is not NMouseCardPlay)) return null;
            bool mouseCarrier = binding.Carrier is NMouseCardPlay;
            Node? hovered = HumanTextHoveredNodeProperty?.GetValue(manager) as Node;
            NCreature? target = hovered as NCreature;
            if (confirm && target == null) return null;
            return BeginHumanTextContinuation(binding.Carrier, input,
                confirm ? "confirm_target" : "cancel_card_play",
                mouseCarrier
                    ? (confirm ? HumanTextInputObservationContract.MouseTargetFinishInput
                        : HumanTextInputObservationContract.MouseTargetCanceledInput)
                    : (confirm ? HumanTextInputObservationContract.ControllerTargetFinishInput
                        : HumanTextInputObservationContract.ControllerTargetCanceledInput),
                manager, confirm ? target : null, !confirm);
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("human_text_input.target_prefix", exception);
            return null;
        }
    }

    private static HumanTextContinuationScope? BeginHumanTextContinuation(
        NCardPlay carrier, InputEvent input, string verb, string mechanism,
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
                    SessionId, TimelineId, _currentRunId, carrier, input, verb,
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
            scope.NativeFinishMatched = HumanTextInputNativeProof.MatchesTargetFinish(
                scope.RequestedCancel, cancel, scope.Target, hovered);
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
                if (disposition == HumanTextInputObservationContract.AcceptedInput
                    && !HumanTextConsumedInputs.TryClaim(scope.Input, scope.SessionId))
                    return;
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

    private static bool IsCurrentHumanTextCardCarrier(NCardPlay carrier)
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
