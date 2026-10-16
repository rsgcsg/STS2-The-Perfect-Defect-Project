using System.Text.Json;
using System.Text.Json.Nodes;

namespace STS2HumanAnnotator.Core;

/// <summary>Independent integrity checks on neutral originals. This never declares P1 scene coverage.</summary>
public static class SourcePublicBasisCodecV4
{
    public static void Validate(ReadOnlyMemory<byte> bytes, string snapshotId, RecorderEnvironmentIdentity environment)
    {
        using var document = JsonDocument.Parse(bytes);
        Unique(document.RootElement);
        var root = JsonNode.Parse(bytes.Span)!.AsObject();
        Require(root.Select(p => p.Key).ToHashSet(StringComparer.Ordinal).SetEquals(
            new[] { "schema", "revision", "capture_scope", "capture_status", "snapshot", "reads", "missing" }));
        Require(Text(root, "schema") == SourceSessionContractV4.PublicBasisSchema
            && Text(root, "revision") == SourceSessionContractV4.PublicBasisRevision
            && Text(root, "capture_scope") == SourceSessionContractV4.PublicBasisScope);
        var snapshot = root["snapshot"]!.AsObject();
        Require(Text(snapshot, "schema") == "sts2.player-environment/snapshot-1"
            && Text(snapshot, "snapshot_id") == snapshotId
            && Text(snapshot, "protocol_version") == environment.PlayerEnvironmentProtocol
            && Text(snapshot["session"]!, "runtime_instance_id") == environment.RuntimeInstanceId
            && Text(snapshot["session"]!, "environment_fingerprint") == environment.EnvironmentFingerprint
            && snapshot["information_policy"]?["includes_hidden_information"]?.GetValue<bool>() == false);
        var opportunities = snapshot["reads"]!.AsArray(); var captured = root["reads"]!.AsArray();
        Require(opportunities.Count <= 10_000 && opportunities.Count == captured.Count);
        var advertised = opportunities.ToDictionary(x => Text(x!, "read_id"), StringComparer.Ordinal);
        var seen = new HashSet<string>(StringComparer.Ordinal); var missing = new List<string>();
        foreach (var entry in captured)
        {
            var read = entry!.AsObject();
            Require(read.Select(p => p.Key).ToHashSet(StringComparer.Ordinal).SetEquals(new[] { "read_id", "kind", "response", "error" }));
            string id = Text(read, "read_id");
            Require(!string.IsNullOrWhiteSpace(id) && seen.Add(id));
            Require(advertised.TryGetValue(id, out var opportunity));
            Require(Text(read, "kind") == Text(opportunity!, "kind"));
            if (read["response"] is not { } response)
            {
                string error = Text(read, "error"); Require(!string.IsNullOrWhiteSpace(error));
                missing.Add("read:" + id + ":" + error); continue;
            }
            Require(read["error"] == null && Text(response, "schema") == "sts2.player-environment/read-1"
                && Text(response, "protocol_version") == environment.PlayerEnvironmentProtocol
                && Text(response, "read_id") == id && Text(response, "kind") == Text(read, "kind")
                && Text(response, "expected_snapshot_id") == snapshotId && Text(response, "observed_snapshot_id") == snapshotId
                && JsonNode.DeepEquals(response["session"], snapshot["session"])
                && JsonNode.DeepEquals(response["information_policy"], snapshot["information_policy"]));
            foreach (string field in new[] { "target_referent_id", "content_schema", "ordering_semantics", "visibility_basis" })
                Require(JsonNode.DeepEquals(response[field], opportunity![field]));
            Require(JsonNode.DeepEquals(response["completeness"]?["hidden_by_policy"], opportunity!["hidden_by_policy"]));
            if (!Complete(response["completeness"])) missing.Add("read:" + id + ":partial");
        }
        if (!Complete(snapshot["completeness"])) missing.Add("snapshot:partial");
        Require(Text(root, "capture_status") == (missing.Count == 0 ? "complete" : "partial")
            && root["missing"]!.AsArray().Select(v => v!.GetValue<string>()).SequenceEqual(missing));
    }

    internal static void Validate(SourceNativeFactsV4 facts)
    {
        Require(facts.Origin is "native_input" or "protocol_input" or "missing");
        if (facts.OwnerRef != null) SourceSessionContract.Identifier(facts.OwnerRef);
        if (facts.SubjectRef != null) SourceSessionContract.Identifier(facts.SubjectRef);
        Require(facts.Arguments.Count <= 32 && facts.Carriers.Count <= 8);
        foreach (var (role, reference) in facts.Arguments)
        { SourceSessionContractV2.Text(role); SourceSessionContract.Identifier(reference); }
        var unique = new HashSet<string>(StringComparer.Ordinal);
        foreach (var carrier in facts.Carriers)
        {
            Require(carrier.Kind is "card_play_created" or "game_action_submitted" or "game_action_accepted" or "card_play_canceled");
            SourceSessionContract.Identifier(carrier.CarrierRef);
            Require(unique.Add(carrier.Kind + ":" + carrier.CarrierRef));
        }
        Require((facts.Origin == "missing") == (facts.MissingReason != null));
        if (facts.MissingReason != null) SourceSessionContractV2.Text(facts.MissingReason);
    }

    internal static void Validate(SourceNativeStageV4 stage)
    {
        Require(stage.Kind == "card_play_canceled" && stage.Evidence == "native_finished_false"
            && stage.NoSubmission != false && stage.Arguments.Count == 1 && stage.Arguments.ContainsKey("hand"));
        SourceSessionContract.Identifier(stage.OwnerRef); SourceSessionContract.Identifier(stage.SubjectRef);
        SourceSessionContract.Identifier(stage.Arguments["hand"]);
    }

    private static bool Complete(JsonNode? completeness) => completeness?["status"]?.GetValue<string>() == "complete"
        && completeness["missing"] is JsonArray { Count: 0 };
    private static string Text(JsonNode value, string field) => value[field]?.GetValue<string>()
        ?? throw new InvalidDataException("source_public_basis_invalid");
    private static void Require(bool valid)
    { if (!valid) throw new InvalidDataException("source_public_basis_invalid"); }
    private static void Unique(JsonElement node)
    {
        if (node.ValueKind == JsonValueKind.Object)
        {
            var fields = new HashSet<string>(StringComparer.Ordinal);
            foreach (var property in node.EnumerateObject()) { Require(fields.Add(property.Name)); Unique(property.Value); }
        }
        else if (node.ValueKind == JsonValueKind.Array) foreach (var item in node.EnumerateArray()) Unique(item);
        else if (node.ValueKind == JsonValueKind.Number) Require(double.IsFinite(node.GetDouble()));
    }
}
