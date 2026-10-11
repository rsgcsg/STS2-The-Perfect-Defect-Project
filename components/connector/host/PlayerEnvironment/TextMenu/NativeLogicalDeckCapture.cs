using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.UI;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

internal static class NativeLogicalDeckCapture
{
    internal static TextMenuFrame Capture(NDeckViewScreen owner, PlayerEnvironmentSnapshot page,
        string ownerKey, IReadOnlyList<NativeTextMenuInformationLeaf> informationLeaves, NativeEntityRegistry entities)
    {
        NativeLogicalDeckState? state = NativeLogicalDeckState.Capture(owner);
        if (state == null) return NativeLogicalCapturePolicy.Partial(page, ownerKey, "native_logical_deck_binding_missing");
        var leaves = informationLeaves.Select(leaf => new TextMenuLeaf(leaf.Key, leaf.Group,
            leaf.Verb, leaf.Label, leaf.SubjectReferentId, leaf.Arguments, leaf.Dispatch)).ToList();
        var refs = page.Referents.ToDictionary(referent => referent.ReferentId, StringComparer.Ordinal);
        var cards = new JsonArray();
        var missing = page.Completeness.Missing.ToHashSet(StringComparer.Ordinal);
        foreach (CardModel model in state.Cards)
        {
            string id = entities.GetId(model, "card");
            NGridCardHolder? holder = state.Grid.GetCardHolder(model);
            bool displayed = holder != null && ConnectorMod.IsNodeVisible(holder)
                && holder.CardNode?.Visibility == ModelVisibility.Visible;
            CardModel representation = state.UpgradeView && displayed && holder!.IsShowingUpgradedCard
                ? holder.CardNode!.Model ?? model : model;
            if (state.UpgradeView && model.IsUpgradable && (!displayed || !holder!.IsShowingUpgradedCard))
                missing.Add("native_logical_offscreen_upgrade_presentation_unavailable");
            VisibleCard visible = LiveContextReader.BuildCard(representation, id, displayPile: PileType.Deck);
            if (visible.ExistingEnchantment != null)
                visible = visible with { ExistingEnchantment = visible.ExistingEnchantment with { Description = null } };
            JsonObject row = (JsonSerializer.SerializeToNode(visible, ConnectorMod._jsonOptions) as JsonObject)!;
            row["observation_basis"] = displayed ? "native_displayed_grid_card" : "publicly_available_at_seam";
            row["native_node_displayed"] = displayed;
            row["upgrade_preview_requested"] = state.UpgradeView;
            cards.Add(row);
            refs[id] = new(id, "card", "entity", visible.Name, new(true, true, false, false, "native_logical_public_list"),
                "sts2.player-environment/referent/native_logical_grid_card-1", row.DeepClone());
            if (state.CanInspect(model))
            {
                CardModel exact = model;
                leaves.Add(new("logical_deck_inspect:" + id, "root", "inspect_deck_card",
                    "Inspect " + (visible.Name ?? visible.DefinitionId), id, Array.Empty<PlayerEnvironmentBoundActionArgument>(),
                    () => state.Inspect(exact), new TextMenuNativeWitnessBinding(owner, exact,
                        new Dictionary<string, object>(StringComparer.Ordinal))));
            }
        }
        foreach ((string path, string verb, string label) in new[]
        {
            ("%ObtainedSorter", "sort_deck_obtained", "Sort by obtained order"),
            ("%CardTypeSorter", "sort_deck_type", "Sort by card type"),
            ("%CostSorter", "sort_deck_cost", "Sort by cost"),
            ("%AlphabeticalSorter", "sort_deck_alphabet", "Sort alphabetically"),
            ("%Upgrades", "toggle_deck_upgrade_view", "Toggle upgrade view")
        })
            if (owner.GetNodeOrNull<NButton>(path) is { } button && NativeLogicalGridState.VisibleEnabled(button))
            {
                string id = entities.GetId(button, "deck_control");
                refs[id] = new(id, "deck_control", "control", label, new(true, true, false, false, "native_visible_fact"), null, null);
                NButton exact = button;
                string exactPath = path;
                leaves.Add(new("logical_deck_control:" + id, "root", verb, label, id,
                    Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => state.Click(exactPath, exact)));
            }
        var surface = new JsonObject { ["kind"] = "run_deck", ["cards"] = cards,
            ["card_count"] = cards.Count, ["ordering_basis"] = "exact_current_native_deck_grid_order",
            ["showing_upgrade_previews"] = state.UpgradeView };
        PlayerEnvironmentCompleteness completeness = NativeLogicalCapturePolicy.Replace(page.Completeness,
            NativeLogicalProjectionReplacement.Grid, false, missing,
            page.Completeness.VisibleInformation, page.Completeness.InteractionDiscovery);
        return new(page with { Referents = refs.Values.ToArray(), Completeness = completeness,
            Interaction = page.Interaction with { Content = page.Interaction.Content with { Surface = surface } } }, ownerKey, leaves)
        { LogicalDeckProof = state };
    }
}
