using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment.NativeLogical;

internal sealed record NativeLogicalRevalidationDetail(string[] ChangedMembers,
    string[] ChangedPaths, bool Truncated, string[] TruncationReasons);

// Bounded explanation of the already compared public frames, never an
// acceptance predicate. Paths expose only these closed schema tokens.
internal sealed class NativeLogicalRevalidationDetailBuilder
{
    internal const int NodeLimit = 4096, DepthLimit = 8, PathLimit = 32,
        PathByteLimit = 128, ReferentLimit = 256;
    private const string RenderedCardSchema = "sts2.player-environment/referent/native_displayed_card-1";
    private static readonly HashSet<string> PublicFields = new(StringComparer.Ordinal)
    {
        "kind", "stage", "can_end_turn", "playable_cards", "usable_potions", "actual_displayed_cards",
        "actual_displayed_summary_text", "card_referent_id", "displayed_title", "displayed_cost",
        "displayed_description", "star_cost_visible", "displayed_star_cost", "enchantment_visible",
        "displayed_enchantment", "focused_target_referent_id", "held_card_referent_id", "target_count",
        "potion_referent_id", "encounter_type", "round", "turn_owner", "is_play_phase", "player",
        "enemies", "hp", "max_hp", "block", "energy", "max_energy", "stars", "gold", "hand",
        "entity_id", "player_entity_id", "owner_referent_id", "combat_id", "definition_id", "name",
        "cost", "description", "can_play", "unplayable_reason", "target_type", "upgraded", "rarity",
        "type", "statuses", "intents", "orbs", "orb_slots", "potion_states", "companions",
        "draw_pile_count", "discard_pile_count", "exhaust_pile_count", "amount", "damage", "hits",
        "label", "control_role", "presentation_basis", "displayed_text", "logical_stars",
        "displayed_passive_text", "evoke_text_visible", "evoke_value", "health_bar_visible",
        "hp_display_mode", "hp_numbers_available", "is_next_to_evoke", "occupied", "passive_text_visible",
        "passive_value", "queue_index", "slot_count", "slot_order", "act", "floor", "boss_icons"
    };
    private readonly List<string> members = new(), paths = new(), reasons = new();
    private int nodes;
    internal static NativeLogicalRevalidationDetail Describe(NativeLogicalPublicFrame a, NativeLogicalPublicFrame b)
    {
        var detail = new NativeLogicalRevalidationDetailBuilder();
        detail.Interaction(a.Interaction, b.Interaction);
        detail.Referents(a.Referents, b.Referents);
        return new(detail.members.ToArray(), detail.paths.ToArray(), detail.reasons.Count != 0, detail.reasons.ToArray());
    }
    private void Member(string value) { if (!members.Contains(value)) members.Add(value); }
    private void Truncate(string reason) { if (!reasons.Contains(reason)) reasons.Add(reason); }
    private void Path(string value)
    {
        if (paths.Contains(value)) return;
        if (Encoding.UTF8.GetByteCount(value) > PathByteLimit) { Truncate("path_byte_limit"); return; }
        if (paths.Count == PathLimit) { Truncate("path_count_limit"); return; }
        paths.Add(value);
        if (value.StartsWith("interaction.surface.actual_displayed_cards", StringComparison.Ordinal))
            Member("surface_rendered_cards");
    }
    private bool Visit()
    { if (nodes >= NodeLimit) { Truncate("node_limit"); return false; } nodes++; return true; }
    private static string Field(string key) => PublicFields.Contains(key) ? key : "<other_field>";
    private void Scalar(bool changed, string member, string path)
    { if (changed) { Member(member); Path(path); } }
    private void Interaction(PlayerEnvironmentInteraction a, PlayerEnvironmentInteraction b)
    {
        Scalar(a.InteractionId != b.InteractionId, "interaction_id", "interaction.interaction_id");
        Scalar(a.Kind != b.Kind, "interaction_kind", "interaction.kind");
        Scalar(a.Stage != b.Stage, "interaction_stage", "interaction.stage");
        Scalar(a.Prompt != b.Prompt, "interaction_prompt", "interaction.prompt");
        Scalar(a.ContentSchema != b.ContentSchema, "interaction_content_schema", "interaction.content_schema");
        if (Json(a.Content.Surface, b.Content.Surface, "interaction.surface", 0)) Member("interaction_surface");
        if (Json(a.Content.Context, b.Content.Context, "interaction.context", 0)) Member("interaction_context");
    }
    private void Referents(IReadOnlyList<PlayerEnvironmentReferent> a, IReadOnlyList<PlayerEnvironmentReferent> b)
    {
        if (a.Count > ReferentLimit || b.Count > ReferentLimit) { Truncate("referent_limit"); return; }
        var old = new Dictionary<string, PlayerEnvironmentReferent>(StringComparer.Ordinal);
        var current = new Dictionary<string, PlayerEnvironmentReferent>(StringComparer.Ordinal);
        foreach (var r in a) if (!old.TryAdd(r.ReferentId, r)) { Truncate("referent_identity_ambiguous"); return; }
        foreach (var r in b) if (!current.TryAdd(r.ReferentId, r)) { Truncate("referent_identity_ambiguous"); return; }
        bool membership = false;
        foreach (var r in a.Concat(b))
            if (!old.ContainsKey(r.ReferentId) || !current.ContainsKey(r.ReferentId))
            {
                membership = true;
                Member(r.PropertiesSchema == RenderedCardSchema ? "referents_rendered_card_membership" : "referents_other_membership");
            }
        Scalar(membership, "referents_membership", "referents.<membership>");
        Scalar(!membership && !a.Select(r => r.ReferentId).SequenceEqual(b.Select(r => r.ReferentId)),
            "referents_order", "referents.<order>");
        foreach (var x in a)
        {
            if (!current.TryGetValue(x.ReferentId, out var y)) continue;
            Scalar(x.Role != y.Role || x.Kind != y.Kind, "referents_role_kind", "referents[*].role_or_kind");
            Scalar(x.Label != y.Label, "referents_labels", "referents[*].label");
            Scalar(x.PropertiesSchema != y.PropertiesSchema, "referents_properties_schema", "referents[*].properties_schema");
            Scalar(x.State.Visible != y.State.Visible, "referents_state", "referents[*].state.visible");
            Scalar(x.State.Enabled != y.State.Enabled, "referents_state", "referents[*].state.enabled");
            Scalar(x.State.Selected != y.State.Selected, "referents_state", "referents[*].state.selected");
            Scalar(x.State.Focused != y.State.Focused, "referents_state", "referents[*].state.focused");
            Scalar(x.State.ObservationBasis != y.State.ObservationBasis, "referents_state", "referents[*].state.observation_basis");
            if (Json(x.Properties, y.Properties, "referents[*].properties", 0))
            {
                Member("referents_properties");
                Member(x.PropertiesSchema == RenderedCardSchema || y.PropertiesSchema == RenderedCardSchema
                    ? "referents_rendered_card_properties" : "referents_other_properties");
            }
        }
    }
    private bool Json(JsonNode? a, JsonNode? b, string path, int depth)
    {
        if (!Visit()) return false;
        if (ReferenceEquals(a, b)) return false;
        if (a is null || b is null) { Path(path); return true; }
        if (a is JsonValue && b is JsonValue)
        { if (JsonNode.DeepEquals(a, b)) return false; Path(path); return true; }
        if (a.GetType() != b.GetType()) { Path(path); return true; }
        if (depth == DepthLimit) { Truncate("depth_limit"); return false; }
        bool changed = false;
        if (a is JsonObject x && b is JsonObject y)
        {
            foreach (var property in x)
            {
                if (nodes >= NodeLimit) { Truncate("node_limit"); break; }
                string next = path + "." + Field(property.Key);
                if (!y.TryGetPropertyValue(property.Key, out var value)) { Visit(); Path(next); changed = true; }
                else changed |= Json(property.Value, value, next, depth + 1);
            }
            foreach (var property in y)
            {
                if (!Visit()) break;
                if (!x.ContainsKey(property.Key)) { Path(path + "." + Field(property.Key)); changed = true; }
            }
        }
        else if (a is JsonArray old && b is JsonArray current)
        {
            if (old.Count != current.Count) { Path(path + ".<length>"); changed = true; }
            for (int i = 0; i < Math.Min(old.Count, current.Count); i++)
            {
                if (nodes >= NodeLimit) { Truncate("node_limit"); break; }
                changed |= Json(old[i], current[i], path + "[*]", depth + 1);
            }
        }
        return changed;
    }
}
