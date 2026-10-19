using System.Text.Json;
using System.Text.Json.Nodes;

namespace STS2HumanAnnotator.Core;

// Pure construction/validation of an original execution capture, not a tracker
// or legality engine. Lifecycle and durable writes keep their existing owners.
public static class P1NativeExecutionRecording
{
    public static JsonObject Create(SemanticActionReference action, DateTimeOffset acceptedAt,
        DateTimeOffset capturedAt, P1NativeBasisReference? basis, bool basisComplete,
        ExecutionSemanticActionSpaceEvidence? space, JsonNode? boundAction, JsonNode? nativeCapture,
        string? captureFailure = null)
    {
        var errors = new List<string>();
        if (captureFailure != null) errors.Add(captureFailure);
        if (basis == null || !basisComplete) errors.Add("execution_original_p1_basis_incomplete");
        errors.AddRange(ExecutionSemanticActionSpaceValidator.Validate(space, action));
        string? capturedSnapshot = nativeCapture?["ui_catalog"]?["snapshot_id"]?.GetValue<string>();
        if (basis != null && capturedSnapshot != basis.SnapshotId) errors.Add("execution_capture_snapshot_mismatch");
        if ((action.BoundAction == null) != (boundAction == null)) errors.Add("execution_public_action_reference_mismatch");
        return new JsonObject {
            ["kind"] = "execution", ["phase"] = "before_execution", ["run_id"] = action.RunId,
            ["decision_id"] = action.RecordId, ["action_witness_id"] = action.ActionWitnessId,
            ["native_action_type"] = action.NativeActionType, ["native_queue_id"] = action.NativeQueueId,
            ["accepted_at"] = acceptedAt, ["captured_at"] = capturedAt, ["queued"] = true,
            ["queue_interval_ms"] = (capturedAt - acceptedAt).TotalMilliseconds,
            ["basis"] = JsonSerializer.SerializeToNode(basis, EvidenceJson.Options),
            ["execution_snapshot_id"] = capturedSnapshot,
            ["bound_action"] = boundAction?.DeepClone(),
            ["native_input"] = JsonSerializer.SerializeToNode(action.NativeInput, EvidenceJson.Options),
            ["execution_action_space"] = JsonSerializer.SerializeToNode(space, EvidenceJson.Options),
            ["native_semantic_capture"] = nativeCapture?.DeepClone(),
            ["missing_reason"] = errors.Count == 0 ? null : string.Join(";", errors.Distinct(StringComparer.Ordinal))
        };
    }
}
