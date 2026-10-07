using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Public information subjects assembled only from this capture's already
/// frozen visible facts. Native source membership is checked by the owning adapter;
/// public IDs never create operands or dispatch authority.</summary>
internal sealed class PublicInformationBindings
{
    private readonly PlayerEnvironmentSnapshot source;
    private readonly Dictionary<string, PlayerEnvironmentReferent> visible;
    private readonly HashSet<string> missing = new(StringComparer.Ordinal);

    internal PublicInformationBindings(PlayerEnvironmentSnapshot source)
    {
        this.source = source;
        visible = source.Referents.GroupBy(value => value.ReferentId, StringComparer.Ordinal)
            .ToDictionary(group => group.Key, group => group.First(), StringComparer.Ordinal);
        if (visible.Count != source.Referents.Count) Missing("duplicate_referents");
    }
    internal void Missing(string scope) => missing.Add("public_information_binding_" + scope);
    internal bool Complete => missing.Count == 0;
    internal PlayerEnvironmentSnapshot Page => source with
    {
        Referents = visible.Values.ToArray(),
        Completeness = missing.Count == 0 ? source.Completeness : source.Completeness with
        {
            Status = "partial",
            Missing = source.Completeness.Missing.Concat(missing).Distinct(StringComparer.Ordinal)
                .Order(StringComparer.Ordinal).ToArray()
        }
    };

    private JsonNode? Context => source.Interaction.Content.Context;
    private JsonNode? Hud => source.Persistent?.Content;
    private static string? Text(JsonNode? value) => value is JsonValue scalar
        && scalar.TryGetValue<string>(out string? text) ? text : null;
    private static int? Integer(JsonNode? value) => value is JsonValue scalar
        && scalar.TryGetValue<int>(out int number) ? number : null;
    private static JsonObject? Find(JsonNode? values, string field, string id)
    {
        JsonObject[] matches = values is JsonArray array ? array.OfType<JsonObject>()
            .Where(value => Text(value[field]) == id).ToArray() : Array.Empty<JsonObject>();
        return matches.Length == 1 ? matches[0] : null;
    }
    private JsonObject? OwnerFacts(string id)
    {
        if (Context?["player"] is JsonObject player && Text(player["player_entity_id"]) == id) return player;
        return Find(Context?["enemies"], "entity_id", id)
            ?? Find(Context?["player"]?["companions"], "entity_id", id);
    }
    internal PlayerEnvironmentReferent? Existing(string id, string scope, params string[] roles)
    {
        if (visible.TryGetValue(id, out var referent) && referent.State.Visible
            && (roles.Length == 0 || roles.Contains(referent.Role, StringComparer.Ordinal))) return referent;
        Missing(scope);
        return null;
    }
    private PlayerEnvironmentReferent? Declare(string id, string role, string? label,
        JsonObject properties, string? owner = null)
    {
        if (string.IsNullOrWhiteSpace(label) || owner != null && Existing(owner, role + "_owner") == null)
        {
            Missing(role + "_facts");
            return null;
        }
        properties = (JsonObject)properties.DeepClone();
        if (owner != null) properties["owner_referent_id"] = owner;
        var referent = new PlayerEnvironmentReferent(id, role, "entity", label,
            new(true, true, false, false, "native_visible_fact"),
            $"sts2.player-environment/referent/{role}-1", properties);
        visible[id] = referent;
        return referent;
    }
    internal string OwnerLabel(string id) => visible.GetValueOrDefault(id)?.Label
        ?? Text(OwnerFacts(id)?["name"])
        ?? (Text(Hud?["player"]?["entity_id"]) == id ? Text(Hud?["player"]?["character_name"]) : null)
        ?? (visible.GetValueOrDefault(id)?.Role switch
            { "enemy" => "Enemy", "companion" => "Companion", "player" => "Player", _ => "Creature" });

    internal PlayerEnvironmentReferent? Card(string id)
    {
        var card = Existing(id, "card_subject", "card", "playable_card", "hand");
        if (card == null) return null;
        // Playable-card referents may hold only a name/target relation; attach the
        // already frozen same-ID hand card so duplicate names retain cost/upgrade facts.
        JsonObject? facts = Find(Context?["player"]?["hand"], "entity_id", id);
        // Hand is an optional source of richer same-ID facts, not a membership
        // requirement: pile/generated/other selectors also carry combat context.
        // Preserve an already full current-page card instead of replacing its
        // owner-specific display with the contextual hand's representation.
        bool fullPageCard = card.Properties is JsonObject shown
            && shown["definition_id"] != null && shown["cost"] != null;
        if (facts != null && !fullPageCard)
            visible[id] = card = card with
            {
                PropertiesSchema = $"sts2.player-environment/referent/{card.Role}-1",
                Properties = facts.DeepClone()
            };
        if (string.IsNullOrWhiteSpace(card.Label)) { Missing("card_label"); return null; }
        return card;
    }
    internal PlayerEnvironmentReferent? Relic(string id)
    {
        JsonObject? facts = Find(Hud?["player"]?["relics"], "entity_id", id);
        if (facts == null) { Missing("relic_subject"); return null; }
        // SanitizePage has already removed unopened bodies/previews; explicitly
        // retain only current inventory identity/title/counter in this new subject.
        var shown = Copy(facts, "definition_id", "name", "counter");
        return Declare(id, "relic", Text(facts["name"]), shown);
    }
    internal PlayerEnvironmentReferent? Orb(string id, string owner)
    {
        var orb = Existing(id, "orb_subject", "orb");
        JsonObject? facts = Find(OwnerFacts(owner)?["orbs"], "entity_id", id);
        if (orb == null || facts == null || Existing(owner, "orb_owner") == null)
        { Missing("orb_membership"); return null; }
        return Declare(id, "orb", orb.Label, Copy(facts, "definition_id", "name", "passive_value",
            "evoke_value", "queue_index", "is_next_to_evoke"), owner);
    }
    internal PlayerEnvironmentReferent? EmptyOrb(string id, string owner, int slot, int nativeSlots)
    {
        JsonObject? facts = OwnerFacts(owner);
        if (Existing(owner, "orb_slot_owner") == null || Integer(facts?["orb_slots"]) != nativeSlots
            || slot < 0 || slot >= nativeSlots)
        { Missing("orb_slot_membership"); return null; }
        // Slot order is the native visible orb queue/slot order, not a candidate ordinal.
        return Declare(id, "orb_slot", "Empty orb slot", new JsonObject
            { ["occupied"] = false, ["slot_order"] = slot, ["slot_count"] = nativeSlots }, owner);
    }
    internal PlayerEnvironmentReferent? Power(string id, string owner, string definition,
        decimal amount, bool nativeMembership)
    {
        var statuses = OwnerFacts(owner)?["statuses"] as JsonArray;
        JsonObject[] matches = statuses?.OfType<JsonObject>().Where(value => Text(value["definition_id"]) == definition
            && value["amount"] is JsonValue number && number.TryGetValue<decimal>(out decimal frozenAmount)
            && frozenAmount == amount).ToArray() ?? Array.Empty<JsonObject>();
        if (!nativeMembership || matches.Length != 1 || Existing(owner, "power_owner") == null)
        { Missing("power_membership"); return null; }
        return Declare(id, "power", Text(matches[0]["name"]), Copy(matches[0], "definition_id", "name", "amount", "type"), owner);
    }
    internal PlayerEnvironmentReferent? Intent(string id, string owner, int nativeOrder,
        int nativeCount, bool nativeMembership)
    {
        var intents = OwnerFacts(owner)?["intents"] as JsonArray;
        if (!nativeMembership || Existing(owner, "intent_owner") == null || intents == null
            || intents.Count != nativeCount || nativeOrder < 0 || nativeOrder >= intents.Count
            || intents[nativeOrder] is not JsonObject facts)
        { Missing("intent_membership"); return null; }
        string? type = Text(facts["type"]);
        string? value = Text(facts["label"]);
        return Declare(id, "intent", type == null ? null : type + (string.IsNullOrEmpty(value) ? "" : " " + value),
            Copy(facts, "type", "label"), owner);
    }
    internal PlayerEnvironmentReferent? Topbar(string id, string role)
    {
        if (Hud?["run"] is not JsonObject run || Hud?["player"] is not JsonObject player)
        { Missing("topbar_hud"); return null; }
        JsonObject properties;
        string label;
        switch (role)
        {
            case "deck": label = "Deck"; properties = new(); break;
            case "map": label = "Map"; properties = Copy(run, "act", "floor"); break;
            case "floor": label = "Floor"; properties = Copy(run, "act", "floor"); break;
            case "boss": label = "Boss"; properties = Copy(run, "boss_icons"); break;
            case "gold": label = "Gold"; properties = Copy(player, "gold"); break;
            case "hp": label = "HP"; properties = Copy(player, "hp", "max_hp"); break;
            default: Missing("topbar_role"); return null;
        }
        properties["control_role"] = role;
        var result = Declare(id, "topbar_" + role, label, properties);
        if (result != null) visible[id] = result = result with { Kind = "control" };
        return result;
    }
    private static JsonObject Copy(JsonObject facts, params string[] fields)
    {
        var result = new JsonObject();
        foreach (string field in fields)
            if (facts[field] is { } value) result[field] = value.DeepClone();
        return result;
    }

    internal static NativeTextMenuInformationLeaf Leaf(string key, string group, string verb,
        PlayerEnvironmentReferent subject, Func<STS2Connector.NativeUi.NativeInputResult> dispatch,
        string? owner = null, string? ownerLabel = null) => new(key, group, verb,
            "Show " + subject.Label + (ownerLabel == null ? "" : " on " + ownerLabel) + " tips",
            subject.ReferentId, owner == null ? Array.Empty<PlayerEnvironmentBoundActionArgument>()
                : new[] { new PlayerEnvironmentBoundActionArgument("owner", owner) }, dispatch);

    // Reward pages use their own public projection. Merge only subjects/owners of
    // appended information leaves, never an unrelated underlying room's referents.
    internal static PlayerEnvironmentSnapshot MergeRequired(PlayerEnvironmentSnapshot target,
        PlayerEnvironmentSnapshot information, IEnumerable<NativeTextMenuInformationLeaf> leaves)
    {
        var needed = leaves.SelectMany(leaf => leaf.Arguments.Select(value => value.ReferentId)
            .Concat(leaf.SubjectReferentId is { } subject ? new[] { subject } : Array.Empty<string>()))
            .ToHashSet(StringComparer.Ordinal);
        var map = target.Referents.ToDictionary(value => value.ReferentId, StringComparer.Ordinal);
        foreach (var referent in information.Referents.Where(value => needed.Contains(value.ReferentId)))
            map.TryAdd(referent.ReferentId, referent);
        string[] unresolved = needed.Where(id => !map.TryGetValue(id, out var referent) || !referent.State.Visible)
            .Select(_ => "public_information_binding_reward_merge").Distinct(StringComparer.Ordinal).ToArray();
        return target with { Referents = map.Values.ToArray(), Completeness = information.Completeness.Status == "partial" || unresolved.Length != 0
            ? target.Completeness with { Status = "partial", Missing = target.Completeness.Missing
                .Concat(information.Completeness.Missing).Concat(unresolved).Distinct(StringComparer.Ordinal).ToArray() }
            : target.Completeness };
    }
}
