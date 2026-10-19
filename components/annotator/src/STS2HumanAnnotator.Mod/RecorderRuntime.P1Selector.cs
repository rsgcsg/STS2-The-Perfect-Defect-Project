using System.Text.Json;
using System.Text.Json.Nodes;
using STS2HumanAnnotator.Core;

namespace STS2HumanAnnotator.Mod;

internal static partial class RecorderRuntime
{
    internal sealed record P1SelectorCapture(string ActionWitnessId, string DecisionId, string Mechanism,
        P1NativeBasisReference Basis, JsonNode BoundAction, JsonObject NativeWitness);

    internal static JsonObject? P1ExplicitSelectorCancellation(P1SelectorCapture input,
        bool terminal, bool nativeAcceptanceProven)
    {
        if (!terminal || !nativeAcceptanceProven || input.BoundAction["verb"]?.GetValue<string>() != "cancel"
            || input.NativeWitness["parent_action_witness_id"]?.GetValue<string>() is not { Length: > 0 }
            || input.NativeWitness["parent_owner_witness_id"]?.GetValue<string>() is not { Length: > 0 }
            || input.NativeWitness["selector_owner_witness_id"]?.GetValue<string>() is not { Length: > 0 }) return null;
        var proof = (JsonObject)input.NativeWitness.DeepClone();
        proof["child_decision_id"] = input.DecisionId;
        proof["child_action_witness_id"] = input.ActionWitnessId;
        proof["native_callback"] = input.Mechanism;
        proof["native_acceptance_proven"] = true;
        proof["terminal"] = true;
        return proof;
    }

    internal static string P1UiCompletionPhase(NativePostCommitCompletion completion,
        NativeTaskCompletion task, JsonObject? cancellation)
    {
        if (completion.Succeeded) return "finished";
        return completion.Kind is "MerchantCardRemovalEntry.OnTryPurchaseWrapper" or "RestSiteSynchronizer.ChooseLocalOption"
            && task.NativeTaskStatus == "RanToCompletion" && task.NativeBooleanResult == false
            && cancellation?["parent_action_witness_id"]?.GetValue<string>() == completion.ActionWitnessId
            && cancellation?["parent_owner_witness_id"]?.GetValue<string>() == completion.NativeOperandWitnessId
            && cancellation?["native_acceptance_proven"]?.GetValue<bool>() == true
            && cancellation?["terminal"]?.GetValue<bool>() == true
            ? "cancelled" : "unknown";
    }

    // Original selector prefix and exact native callback evidence, independent of legacy canonical parents.
    private static void AppendP1SelectorAccepted(P1SelectorCapture input, object nativeReceipt)
    {
        _store!.AppendP1NativeEvent(new JsonObject { ["kind"] = "decision_accepted", ["run_id"] = _currentRunId,
            ["decision_id"] = input.DecisionId, ["action_witness_id"] = input.ActionWitnessId,
            ["native_action_type"] = input.Mechanism, ["native_mechanism"] = "native_selector_input",
            ["basis"] = JsonSerializer.SerializeToNode(input.Basis, EvidenceJson.Options),
            ["bound_action"] = input.BoundAction.DeepClone(), ["native_witness"] = input.NativeWitness.DeepClone(),
            ["native_receipt"] = new JsonObject { ["kind"] = "semantic_ui_accepted", ["carrier_ref"] = input.ActionWitnessId,
                ["selector_callback"] = JsonSerializer.SerializeToNode(nativeReceipt, EvidenceJson.Options) }, ["missing_reason"] = null });
    }
}
