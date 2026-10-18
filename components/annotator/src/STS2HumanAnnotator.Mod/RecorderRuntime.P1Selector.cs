using System.Text.Json;
using System.Text.Json.Nodes;
using STS2HumanAnnotator.Core;

namespace STS2HumanAnnotator.Mod;

internal static partial class RecorderRuntime
{
    internal sealed record P1SelectorCapture(string ActionWitnessId, string DecisionId, string Mechanism,
        P1NativeBasisReference Basis, JsonNode BoundAction, JsonObject NativeWitness);

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
