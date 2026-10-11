using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector.Tests;

public sealed class NativeLogicalRevalidationDetailTests
{
    private static NativeLogicalPublicFrame Frame(JsonNode surface, JsonNode context,
        IReadOnlyList<PlayerEnvironmentReferent>? referents = null)
    {
        var f = NativeLogicalCoreTests.Frame();
        return f with { Interaction = f.Interaction with { Content = new(surface, context) }, Referents = referents ?? f.Referents };
    }
    private static PlayerEnvironmentReferent RenderedCard(string id = "opaque-card-id") => new(id, "card", "entity", "Card",
        new(true, true, false, false, "native_visible_fact"), "sts2.player-environment/referent/native_displayed_card-1",
        new JsonObject { ["card_referent_id"] = id, ["displayed_cost"] = "1", ["displayed_description"] = "original-private-canary" });
    [Fact]
    public void RenderedCardPathsAndSemanticContextPathsRemainSeparateInTheSameFrozenPair()
    {
        var r = RenderedCard();
        var a = Frame(new JsonObject { ["actual_displayed_cards"] = new JsonArray(r.Properties!.DeepClone()) },
            new JsonObject { ["player"] = new JsonObject { ["energy"] = 3 } }, new[] { r });
        var b = Frame(new JsonObject { ["actual_displayed_cards"] = new JsonArray(new JsonObject {
            ["card_referent_id"] = r.ReferentId, ["displayed_cost"] = "2", ["displayed_description"] = "changed-private-canary" }) },
            a.Interaction.Content.Context.DeepClone(), new[] { r with { Properties = new JsonObject {
                ["card_referent_id"] = r.ReferentId, ["displayed_cost"] = "2", ["displayed_description"] = "changed-private-canary" } } });
        string original = JsonSerializer.Serialize(a);
        var changes = NativeLogicalFactComparison.Compare(a, b, collectAll: true);
        var display = NativeLogicalRevalidationDetailBuilder.Describe(a, b);
        Assert.False(display.Truncated); Assert.Empty(display.TruncationReasons);
        Assert.Contains("surface_rendered_cards", display.ChangedMembers);
        Assert.Contains("referents_rendered_card_properties", display.ChangedMembers);
        Assert.DoesNotContain("interaction_context", display.ChangedMembers);
        Assert.Contains("interaction.surface.actual_displayed_cards[*].displayed_cost", display.ChangedPaths);
        Assert.Contains("referents[*].properties.displayed_description", display.ChangedPaths);
        b = b with { Interaction = b.Interaction with { Content = b.Interaction.Content with {
            Context = new JsonObject { ["player"] = new JsonObject { ["energy"] = 1 } } } } };
        var both = NativeLogicalRevalidationDetailBuilder.Describe(a, b);
        Assert.Contains("interaction_context", both.ChangedMembers);
        Assert.Contains("interaction.context.player.energy", both.ChangedPaths);
        Assert.Equal(original, JsonSerializer.Serialize(a));
        Assert.Equal(changes, NativeLogicalFactComparison.Compare(a, b, collectAll: true));
    }
    [Fact]
    public void InteractionMetadataReferentMembershipOrderStateLabelsAndPropertiesAreDistinct()
    {
        var card = RenderedCard(); var other = card with { ReferentId = "other-id", PropertiesSchema = "logical-card" };
        var a = Frame(new JsonObject(), new JsonObject(), new[] { card, other });
        var b = a with { Interaction = a.Interaction with {
            InteractionId = "changed", Kind = "changed", Stage = "changed", Prompt = "changed", ContentSchema = "changed" },
            Referents = new[] { other with { Label = "changed", State = other.State with { Enabled = false },
                Properties = new JsonObject { ["energy"] = 2 } }, card } };
        var detail = NativeLogicalRevalidationDetailBuilder.Describe(a, b);
        Assert.False(detail.Truncated);
        foreach (string member in new[] { "interaction_id", "interaction_kind", "interaction_stage", "interaction_prompt",
            "interaction_content_schema", "referents_order", "referents_labels", "referents_state", "referents_other_properties" })
            Assert.Contains(member, detail.ChangedMembers);
        Assert.DoesNotContain("referents_membership", detail.ChangedMembers);
        Assert.Contains("referents[*].state.enabled", detail.ChangedPaths);
        Assert.Contains("referents[*].properties.energy", detail.ChangedPaths);
        var membership = NativeLogicalRevalidationDetailBuilder.Describe(a,
            a with { Referents = new[] { other, card with { ReferentId = "replacement-id" } } });
        Assert.Contains("referents_membership", membership.ChangedMembers);
        Assert.Contains("referents_rendered_card_membership", membership.ChangedMembers);
        Assert.DoesNotContain("referents_order", membership.ChangedMembers);
    }
    [Fact]
    public void JsonShapeDifferencesAndUnknownKeysNeverDumpIdsKeysOrValues()
    {
        var a = Frame(new JsonObject { ["actual_displayed_cards"] = new JsonArray(),
            ["private-id-key-canary"] = new JsonObject { ["another-private-key"] = "old-secret-value" } }, new JsonObject());
        var b = Frame(new JsonObject { ["actual_displayed_cards"] = new JsonArray(new JsonObject { ["card_referent_id"] = "private-id-value-canary" }),
            ["private-id-key-canary"] = new JsonObject { ["another-private-key"] = "new-secret-value" } }, new JsonObject());
        var lines = new List<string>(); var logger = new NativeLogicalRevalidationDiagnostics(true, lines.Add);
        logger.Reject("request", "snapshot", "action", "snapshot", NativeLogicalRejectionGate.PublicFactsChanged, a, b);
        string line = Assert.Single(lines);
        Assert.DoesNotContain("private-id", line); Assert.DoesNotContain("private-key", line); Assert.DoesNotContain("secret-value", line);
        using var doc = JsonDocument.Parse(line); var detail = doc.RootElement.GetProperty("revalidation_detail");
        Assert.False(detail.GetProperty("truncated").GetBoolean());
        var paths = detail.GetProperty("changed_paths").EnumerateArray().Select(p => p.GetString()).ToArray();
        Assert.Contains("interaction.surface.actual_displayed_cards.<length>", paths);
        Assert.Contains("interaction.surface.<other_field>.<other_field>", paths);
    }
    [Theory]
    [InlineData("nodes", "node_limit")]
    [InlineData("depth", "depth_limit")]
    [InlineData("paths", "path_count_limit")]
    [InlineData("path_bytes", "path_byte_limit")]
    [InlineData("referents", "referent_limit")]
    public void DetailLimitsAreExplicitAndDoNotChangeStrictComparison(string mode, string reason)
    {
        var a = Frame(new JsonObject(), new JsonObject());
        NativeLogicalPublicFrame b;
        if (mode == "nodes")
        {
            a = Frame(new JsonObject { ["actual_displayed_cards"] = new JsonArray(Enumerable.Range(0, 5000).Select(i => (JsonNode?)JsonValue.Create(i)).ToArray()) }, new JsonObject());
            b = Frame(new JsonObject { ["actual_displayed_cards"] = new JsonArray(Enumerable.Range(0, 5000).Select(i => (JsonNode?)JsonValue.Create(i + 1)).ToArray()) }, new JsonObject());
        }
        else if (mode is "depth" or "path_bytes")
        {
            string key = mode == "depth" ? "player" : "focused_target_referent_id";
            JsonNode x = JsonValue.Create(1)!, y = JsonValue.Create(2)!;
            for (int i = 0; i < (mode == "depth" ? 9 : 8); i++) { x = new JsonObject { [key] = x }; y = new JsonObject { [key] = y }; }
            a = Frame(x, new JsonObject()); b = Frame(y, new JsonObject());
        }
        else if (mode == "paths")
        {
            var x = new JsonObject(); var y = new JsonObject();
            foreach (string key in new[] { "kind", "stage", "can_end_turn", "playable_cards", "usable_potions", "actual_displayed_cards",
                "actual_displayed_summary_text", "card_referent_id", "displayed_title", "displayed_cost", "displayed_description", "star_cost_visible",
                "displayed_star_cost", "enchantment_visible", "displayed_enchantment", "focused_target_referent_id", "held_card_referent_id", "target_count",
                "potion_referent_id", "encounter_type", "round", "turn_owner", "is_play_phase", "player", "enemies", "hp", "max_hp", "block",
                "energy", "max_energy", "stars", "gold", "hand", "entity_id", "player_entity_id", "owner_referent_id", "combat_id", "definition_id" })
            { x[key] = 1; y[key] = 2; }
            a = Frame(x, new JsonObject()); b = Frame(y, new JsonObject());
        }
        else b = a with { Referents = Enumerable.Range(0, 257).Select(i => RenderedCard("id-" + i)).ToArray() };
        var before = NativeLogicalFactComparison.Compare(a, b, collectAll: true);
        var detail = NativeLogicalRevalidationDetailBuilder.Describe(a, b);
        Assert.True(detail.Truncated); Assert.Contains(reason, detail.TruncationReasons);
        Assert.True(detail.ChangedPaths.Length <= NativeLogicalRevalidationDetailBuilder.PathLimit);
        Assert.All(detail.ChangedPaths, p => Assert.True(Encoding.UTF8.GetByteCount(p) <= NativeLogicalRevalidationDetailBuilder.PathByteLimit));
        Assert.NotEqual(NativeLogicalFactChange.None, before);
        Assert.Equal(before, NativeLogicalFactComparison.Compare(a, b, collectAll: true));
        var lines = new List<string>(); var logger = new NativeLogicalRevalidationDiagnostics(true, lines.Add);
        string maximumEscapedId = new('\0', 256);
        logger.Reject(maximumEscapedId, maximumEscapedId, maximumEscapedId, maximumEscapedId, NativeLogicalRejectionGate.PublicFactsChanged, a, b);
        string line = Assert.Single(lines);
        Assert.True(Encoding.UTF8.GetByteCount(line) <= NativeLogicalRevalidationDiagnostics.RecordByteLimit);
        using var doc = JsonDocument.Parse(line);
        Assert.True(doc.RootElement.GetProperty("revalidation_detail").GetProperty("truncated").GetBoolean());
    }
}
