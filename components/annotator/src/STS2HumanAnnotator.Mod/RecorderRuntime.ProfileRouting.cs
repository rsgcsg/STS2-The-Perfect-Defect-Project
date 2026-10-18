using System.Text.Json;
using System.Text.Json.Nodes;
using STS2HumanAnnotator.Core;

namespace STS2HumanAnnotator.Mod;

internal static partial class RecorderRuntime
{
    private static bool IsP1Recording => _activeCaptureProfileId == P1NativeRecordingContract.ProfileId;

    private static void P1Issue(string reason, string? nativeType = null, object? witness = null)
    {
        if (_store == null) return;
        _store.AppendP1NativeEvent(new JsonObject { ["kind"] = "issue", ["run_id"] = _currentRunId,
            ["reason"] = reason, ["native_action_type"] = nativeType,
            ["native_witness"] = witness == null ? null : JsonSerializer.SerializeToNode(witness, EvidenceJson.Options) });
    }

    // Old diagnostic callbacks are observations; only a witnessed unsupported Human effect is an issue.
    private static bool TryRouteP1LegacyRecord(bool issue, string reason, string? nativeType, object evidence)
    {
        if (!IsP1Recording) return false;
        if (_store != null)
        {
            var row = new JsonObject { ["kind"] = issue ? "issue" : "observation", ["run_id"] = _currentRunId,
                ["reason"] = reason, ["native_action_type"] = nativeType,
                ["native_witness"] = JsonSerializer.SerializeToNode(evidence, EvidenceJson.Options) };
            if (!issue)
            {
                row["basis"] = null;
                row["basis_missing_reason"] = "native_diagnostic_not_decision_boundary";
            }
            _store.AppendP1NativeEvent(row);
        }
        return true;
    }
}
