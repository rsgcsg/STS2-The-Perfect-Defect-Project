using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.GameActions;
using STS2Connector.PlayerEnvironment.Witness;
using STS2HumanAnnotator.Core;
using STS2Platform.NativeFoundation;

namespace STS2HumanAnnotator.Mod;

internal static partial class RecorderRuntime
{
    private static readonly Dictionary<string, (string Decision, string Run, string NativeType)> P1UiPending = new(StringComparer.Ordinal);
    internal static bool IsP1NativeProfile => IsP1Recording;
    internal static bool ShouldObserveBossRelicCommit => !IsP1Recording
        || P1UiPending.Values.Any(value => value.NativeType.StartsWith("NChooseARelicSelection.", StringComparison.Ordinal))
        || HumanActionScope.Current?.ExpectedNativeActionType.StartsWith("NChooseARelicSelection.", StringComparison.Ordinal) == true;

    // Administrative command under Gate; no game action, lifecycle transition or client-selected output path.
    private static RecordingCommandResult ExecuteP1NativePrefixCommand(RecordingCommand command)
    {
        RecordingCommandResult result;
        if (!_initialized || !IsP1Recording || _store == null || _lifecycle.State is not (RecordingLifecycleState.Recording or RecordingLifecycleState.Paused)
            || command.CaptureProfileId != null || command.P1NativeOrigin != null)
            result = new(false, false, "p1_native_open_session_required", "Prefix export requires an open P1 native session.", _lifecycle);
        else
        {
            try
            {
                var prefix = _store.ExportP1NativePrefix();
                result = new(true, false, "p1_native_prefix_exported", "Original durable recording prefix exported; recording remains open.", _lifecycle)
                    { P1NativePrefix = prefix };
            }
            catch (Exception exception)
            {
                result = new(false, false, "p1_native_prefix_export_failed", exception.Message, _lifecycle);
            }
        }
        CommandLedger.Remember(command.CommandId, result);
        return result;
    }

    private static P1NativeBasisReference? PersistP1Basis(ProcessLocalNativeWitnessFrame? frame) =>
        frame?.PublicBasis is { } basis ? _store!.PersistP1NativeBasis(basis.SnapshotId, basis.Bytes) : null;

    private static void ObserveP1AcceptedAction(GameAction action)
    {
        SubmittedInputs.TryGet(action, out SubmittedHumanInput? submitted);
        HumanActionContext? context = submitted?.Context ?? HumanActionScope.Current;
        DeferredHumanActionFailure? failure = submitted?.Failure ?? HumanActionScope.CurrentDeferredFailure;
        if (context == null && failure == null) return; // Native/internal actions are not manufactured player decisions.
        string type = action.GetType().Name;
        if (submitted != null && (submitted.SessionId != SessionId || submitted.TimelineId != TimelineId || submitted.RunId != _currentRunId))
        { P1Issue("submitted_human_input_session_mismatch", type); return; }
        if (context != null && !context.TryClaimRootAction(type)) return;
        if (context == null && !failure!.TryClaim(type)) return;
        try
        {
            ProcessLocalNativeMatch? match = null;
            NativeWitnessEvidence? witness = null;
            string? error = failure?.ReasonCode;
            if (context != null && TryDescribeAction(action, context, out var observed, out witness,
                out var reason, out _, out _)) match = context.Frame.Resolve(observed!);
            else error ??= "accepted_native_action_mapping_unavailable";
            string id = NativeWitnessIdentity.Get(action, "game_action"), decision = $"p1-decision-{Guid.NewGuid():N}";
            var basis = PersistP1Basis(context?.Frame);
            bool complete = context?.Frame.PublicBasis is { } original && P1NativeRecordingContract.CompleteBasis(original.Bytes);
            if (!complete) error ??= "accepted_action_original_p1_basis_incomplete";
            if (match?.BoundAction == null || match.MatchCount != 1 || match.Status != "exact_unique")
                error ??= "accepted_action_original_bound_action_unproven";
            var originalWitness = JsonSerializer.SerializeToNode(witness, EvidenceJson.Options);
            if (context?.P1CaptureDiagnostics is { } diagnostics && originalWitness is JsonObject witnessed)
                witnessed["capture_diagnostics"] = diagnostics.DeepClone();
            _store!.AppendP1NativeEvent(new JsonObject { ["kind"] = "decision_accepted", ["run_id"] = _currentRunId,
                ["decision_id"] = decision, ["action_witness_id"] = id, ["native_action_type"] = type,
                ["native_queue_id"] = action.Id, ["native_mechanism"] = "game_action",
                ["basis"] = JsonSerializer.SerializeToNode(basis, EvidenceJson.Options),
                ["bound_action"] = JsonSerializer.SerializeToNode(match?.BoundAction, EvidenceJson.Options),
                ["native_witness"] = originalWitness,
                ["native_receipt"] = new JsonObject { ["kind"] = "game_action_accepted", ["carrier_ref"] = id },
                ["missing_reason"] = error });
            if (error != null) P1Issue(error, type, witness);
            if (!NativeUiCompletionRootBindings.Remember(action, id))
                P1Issue("accepted_native_parent_binding_conflict", type, new { action_witness_id = id });
            var subscription = new NativeActionLifecycleSubscription(action, id, 0, decision, match?.BoundActionId,
                ObserveP1Lifecycle, nativeSemanticSelection: context?.ExpectedAction, semanticNativeActionType: type);
            NativeActionSubscriptions.Add(action, subscription);
        }
        catch (Exception exception)
        {
            _detail = exception.Message;
            _runtimeState = "p1_native_recording_failed";
            NativeUiObservationSafety.Report("p1_native.accepted", exception);
            try { P1Issue("accepted_native_recording_failed:" + exception.GetType().Name, type); }
            catch { /* Store append health and durable Close failure remain explicit. */ }
        }
    }

    private static bool ObserveP1AcceptedUi(string nativeType, ProcessLocalObservedAction observed,
        NativeWitnessEvidence witness, bool immediateCompletion, string? originalRoot)
    {
        HumanActionContext? context = HumanActionScope.Current;
        if (context != null && !context.TryClaimRootAction(nativeType)) return false;
        try
        {
            string id = originalRoot ?? context?.ActionWitnessId ?? $"unbound-ui-{Guid.NewGuid():N}";
            string decision = $"p1-decision-{Guid.NewGuid():N}";
            var match = context?.Frame.Resolve(observed);
            var basis = PersistP1Basis(context?.Frame);
            string? missing = context?.Frame.PublicBasis is { } original && P1NativeRecordingContract.CompleteBasis(original.Bytes)
                ? null : "accepted_ui_original_p1_basis_incomplete";
            if (match?.BoundAction == null || match.MatchCount != 1 || match.Status != "exact_unique")
                missing ??= "accepted_ui_original_bound_action_unproven";
            _store!.AppendP1NativeEvent(new JsonObject { ["kind"] = "decision_accepted", ["run_id"] = _currentRunId,
                ["decision_id"] = decision, ["action_witness_id"] = id, ["native_action_type"] = nativeType,
                ["native_mechanism"] = witness.Origin, ["basis"] = JsonSerializer.SerializeToNode(basis, EvidenceJson.Options),
                ["bound_action"] = JsonSerializer.SerializeToNode(match?.BoundAction, EvidenceJson.Options),
                ["native_witness"] = JsonSerializer.SerializeToNode(witness, EvidenceJson.Options),
                ["native_receipt"] = new JsonObject { ["kind"] = "semantic_ui_accepted", ["carrier_ref"] = id },
                ["missing_reason"] = missing });
            P1UiPending.Add(id, (decision, _currentRunId, nativeType));
            if (missing != null) P1Issue(missing, nativeType, witness);
            // A Task-backed choice retains its existing exact completion owner.
            // A proven synchronous accepted callback is its own native completion.
            if (context?.CompletionExpectation == null && immediateCompletion)
                CompleteP1Ui(id, "finished", witness);
            return true;
        }
        catch (Exception exception)
        {
            _runtimeState = "p1_native_recording_failed"; _detail = exception.Message;
            NativeUiObservationSafety.Report("p1_native.ui_accepted", exception);
            try { P1Issue("accepted_ui_recording_failed:" + exception.GetType().Name, nativeType, witness); }
            catch { /* Store health prevents a successful Close after an append failure. */ }
            return false;
        }
    }

    private static bool CompleteP1Ui(string id, string phase, object evidence)
    {
        if (_store == null || !P1UiPending.TryGetValue(id, out var pending)) return false;
        P1NativeBasisReference? successor = null; string? missing = null;
        try
        {
            var frame = PlayerEnvironmentNativeWitness.CaptureP1(); successor = PersistP1Basis(frame);
            if (frame.PublicBasis is not { } captured || !P1NativeRecordingContract.CompleteBasis(captured.Bytes))
                missing = "native_ui_terminal_public_basis_partial";
        }
        catch (Exception exception) { missing = "native_ui_terminal_capture_failed:" + exception.GetType().Name; }
        _store.AppendP1NativeEvent(new JsonObject { ["kind"] = "lifecycle", ["run_id"] = pending.Run,
            ["decision_id"] = pending.Decision, ["action_witness_id"] = id, ["phase"] = phase,
            ["native_completion"] = JsonSerializer.SerializeToNode(evidence, EvidenceJson.Options),
            ["successor_basis"] = JsonSerializer.SerializeToNode(successor, EvidenceJson.Options), ["successor_missing_reason"] = missing });
        P1UiPending.Remove(id); return true;
    }

    private static void ObserveP1Lifecycle(NativeActionLifecycleSubscription subscription, string phase)
    {
        if (_store == null || !NativeActionSubscriptions.TryGetValue(subscription.Action, out var current)
            || !ReferenceEquals(current, subscription)) return;
        bool terminal = phase is "finished" or "cancelled" or "aborted";
        try
        {
            P1NativeBasisReference? successor = null;
            string? missing = terminal ? null : "native_lifecycle_not_terminal";
            if (terminal)
            {
                try
                {
                    var frame = PlayerEnvironmentNativeWitness.CaptureP1();
                    successor = PersistP1Basis(frame);
                    if (frame.PublicBasis is not { } captured || !P1NativeRecordingContract.CompleteBasis(captured.Bytes))
                        missing = "native_terminal_public_basis_partial";
                }
                catch (Exception exception) { missing = "native_terminal_capture_failed:" + exception.GetType().Name; }
            }
            _store.AppendP1NativeEvent(new JsonObject { ["kind"] = "lifecycle", ["run_id"] = _currentRunId,
                ["decision_id"] = subscription.RecordId, ["action_witness_id"] = subscription.ActionWitnessId,
                ["phase"] = phase, ["successor_basis"] = JsonSerializer.SerializeToNode(successor, EvidenceJson.Options),
                ["successor_missing_reason"] = missing });
            if (terminal) { subscription.Dispose(); NativeActionSubscriptions.Remove(subscription.Action); }
        }
        catch (Exception exception)
        { _runtimeState = "p1_native_recording_failed"; _detail = exception.Message; NativeUiObservationSafety.Report("p1_native.lifecycle", exception); }
    }

    private static void ObserveP1Boundary(string reason, object? nativeWitness = null)
    {
        if (_store == null || _lifecycle.State is not (RecordingLifecycleState.Recording or RecordingLifecycleState.Closing)) return;
        try
        {
            var frame = PlayerEnvironmentNativeWitness.CaptureP1();
            _store.AppendP1NativeEvent(new JsonObject { ["kind"] = "observation", ["run_id"] = _currentRunId,
                ["reason"] = reason, ["basis"] = JsonSerializer.SerializeToNode(PersistP1Basis(frame), EvidenceJson.Options),
                ["native_witness"] = JsonSerializer.SerializeToNode(nativeWitness, EvidenceJson.Options) });
        }
        catch (Exception exception) { P1Issue("observation_capture_failed:" + exception.GetType().Name, witness: nativeWitness); }
    }

    private static void FinalizeP1Close()
    {
        lock (Gate)
        {
            if (_store == null || _lifecycle.State != RecordingLifecycleState.Closing) return;
            try
            {
                AppendJournal("session_closed", null, _lastSnapshotId, "P1 native recording closed; unfinished accepted actions remain unknown.");
                _store.Dispose();
                _lastStoreSnapshot = _store.GetSnapshot(); _store = null;
                ResetNativeActionTrackingUnsafe(); TerminalSeal.Reset(); Continuous.MarkSealed();
                P1UiPending.Clear();
                _sessionClosedAt = DateTimeOffset.UtcNow;
                _lifecycle = RecordingLifecycleStateMachine.MarkClosed(_lifecycle, _sessionClosedAt.Value);
                _closeout = new("closed", _closeout.RequestedAt, _sessionClosedAt, "P1 native original streams flushed and closed.");
                _runtimeState = "recording_closed"; _detail = _closeout.Detail;
                Interlocked.Increment(ref _cardStageGeneration);
            }
            catch (Exception exception)
            { _runtimeState = "p1_native_close_failed"; _detail = exception.Message; _closeout = _closeout with { State = "closing", Detail = exception.Message }; }
        }
    }
}
