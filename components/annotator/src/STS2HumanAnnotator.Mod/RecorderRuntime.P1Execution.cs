using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.GameActions;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Connector.PlayerEnvironment.Witness;
using STS2HumanAnnotator.Core;
using STS2Platform.NativeFoundation;

namespace STS2HumanAnnotator.Mod;

internal static partial class RecorderRuntime
{
    private static RecordedBoundAction? ToP1RecordedBoundAction(PlayerEnvironmentBoundAction? bound) => bound == null ? null :
        new(bound.BoundActionId, bound.Verb, bound.SubjectReferentId,
            bound.Arguments.ToDictionary(argument => argument.Role, argument => argument.ReferentId, StringComparer.Ordinal), bound.Label);

    private static RecordedNativeInput? ToP1RecordedNativeInput(ProcessLocalNativeInputAction? input) => input == null ? null :
        new(input.ActionKey, input.Verb, input.SubjectReferentId, input.Arguments, input.Label);

    private static void ObserveP1BeforeActionExecution(GameAction action)
    {
        if (_store == null || !NativeActionSubscriptions.TryGetValue(action, out var subscription)
            || subscription.P1Input is not { } accepted) return;
        if (action.State.ToString() == "ReadyToResumeExecuting")
        {
            _store.AppendP1NativeEvent(new JsonObject { ["kind"] = "lifecycle", ["run_id"] = accepted.RunId,
                ["decision_id"] = accepted.RecordId, ["action_witness_id"] = accepted.ActionWitnessId,
                ["phase"] = "before_execution_resume", ["successor_basis"] = null,
                ["successor_missing_reason"] = "player_choice_resume_not_a_new_decision" });
            return;
        }
        if (subscription.P1ExecutionCaptured) return;
        subscription.P1ExecutionCaptured = true;
        P1NativeBasisReference? basis = null;
        ProcessLocalNativeSemanticCapture? capture = null;
        ExecutionSemanticActionSpaceEvidence? space = null;
        PlayerEnvironmentBoundAction? currentBound = null;
        SemanticActionReference selected = accepted with { BoundAction = null, NativeInput = subscription.P1NativeInput };
        bool complete = false;
        string? failure = null;
        DateTimeOffset capturedAt = DateTimeOffset.UtcNow;
        try
        {
            // Both observations run synchronously before the exact GameAction
            // body. PB1 freezes every advertised Read from one BuildSnapshot;
            // mature rc4 supplies native A(S), without granting public delivery.
            var frame = CaptureSemanticFrame();
            basis = PersistP1Basis(frame);
            complete = frame.PublicBasis is { } original && P1NativeRecordingContract.CompleteBasis(original.Bytes);
            capture = PlayerEnvironmentNativeSemanticWitness.Capture("before_execution", action, frame,
                semanticNativeActionType: subscription.SemanticNativeActionType,
                semanticSelection: subscription.NativeSemanticSelection);
            if (subscription.NativeSemanticSelection is { } selection)
            {
                var match = frame.Resolve(selection);
                if (match is { Status: "exact_unique", MatchCount: 1, BoundAction: not null })
                    currentBound = match.BoundAction;
            }
            selected = selected with {
                BoundAction = ToP1RecordedBoundAction(currentBound),
                NativeInput = currentBound == null ? subscription.P1NativeInput : null,
                Mapping = currentBound == null && subscription.P1NativeInput != null
                    ? new("exact_native_input", 1, "scoped_native_input_reference_equality", null) : accepted.Mapping
            };
            space = ToExecutionSemanticActionSpace(accepted.ActionWitnessId, capture,
                currentBound?.BoundActionId, selected.NativeInput?.ActionKey);
        }
        catch (Exception exception) { failure = "execution_capture_failed:" + exception.GetType().Name; }
        try
        {
            var row = P1NativeExecutionRecording.Create(selected, subscription.P1AcceptedAt, capturedAt, basis, complete, space,
                JsonSerializer.SerializeToNode(currentBound, EvidenceJson.Options),
                JsonSerializer.SerializeToNode(capture, EvidenceJson.Options), failure);
            _store.AppendP1NativeEvent(row);
            if (row["missing_reason"] is { } reason)
                P1Issue(reason.GetValue<string>(), accepted.NativeActionType,
                    new { action_witness_id = accepted.ActionWitnessId, decision_id = accepted.RecordId });
        }
        catch (Exception exception)
        {
            _runtimeState = "p1_native_recording_failed"; _detail = exception.Message;
            NativeUiObservationSafety.Report("p1_native.execution", exception);
        }
    }
}
