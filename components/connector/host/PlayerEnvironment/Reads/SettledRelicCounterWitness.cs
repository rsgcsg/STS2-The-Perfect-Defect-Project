using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Models;
using STS2Connector.NativeUi;
using STS2Platform.NativeFoundation;

namespace STS2Connector.PlayerEnvironment;

internal static class SettledRelicCounterWitness
{
    // The caller is still inside the original synchronous capture guard. Only
    // relics already represented in these frozen public carriers are resolved.
    internal static JsonObject Capture(JsonObject snapshot, IEnumerable<JsonNode?> reads, NativeEntityRegistry entities)
    {
        var candidates = new Dictionary<string, (string Definition, int Display)>(StringComparer.Ordinal);
        var missing = new List<string>();
        void Visit(JsonNode? node)
        {
            if (node is JsonArray array) { foreach (var child in array) Visit(child); return; }
            if (node is not JsonObject item) return;
            if (item["definition_id"] is JsonValue definitionNode && definitionNode.TryGetValue<string>(out var definition)
                && NativeSettledRelicCounter.Supports(definition) && item.ContainsKey("counter") && item["counter"] != null)
            {
                if (item["entity_id"] is not JsonValue idNode || !idNode.TryGetValue<string>(out var id)
                    || item["counter"] is not JsonValue counterNode || !counterNode.TryGetValue<int>(out var display))
                    missing.Add("invalid_public_relic_carrier");
                else if (candidates.TryGetValue(id, out var prior) && prior != (definition, display))
                    missing.Add("inconsistent_public_relic_carrier:" + id);
                else candidates[id] = (definition, display);
            }
            foreach (var child in item) Visit(child.Value);
        }
        Visit(snapshot);
        foreach (var read in reads) Visit(read);
        var facts = new JsonArray();
        foreach (var (id, original) in candidates.OrderBy(pair => pair.Key, StringComparer.Ordinal))
        {
            try
            {
                if (!entities.TryResolve<RelicModel>(id, out var relic) || relic == null
                    || NativeSettledRelicCounter.Capture(relic) is not { } value
                    || value.DefinitionId != original.Definition || value.DisplayCounter != original.Display)
                { missing.Add("native_logical_value_unavailable:" + id); continue; }
                facts.Add(new JsonObject { ["entity_id"] = id, ["definition_id"] = value.DefinitionId,
                    ["display_counter"] = value.DisplayCounter, ["logical_counter"] = value.LogicalCounter, ["rule_id"] = value.RuleId });
            }
            catch { missing.Add("native_logical_value_unavailable:" + id); }
        }
        return new JsonObject { ["schema"] = "sts2.player-environment/settled-relic-counters-1",
            ["source_snapshot_id"] = snapshot["snapshot_id"]!.DeepClone(), ["session"] = snapshot["session"]!.DeepClone(),
            ["game_mvid"] = NativeSettledRelicCounter.CurrentGameMvid, ["source_kind"] = "native_same_capture",
            ["facts"] = facts, ["missing"] = new JsonArray(missing.Distinct(StringComparer.Ordinal).Select(value => JsonValue.Create(value)).ToArray()) };
    }
}
