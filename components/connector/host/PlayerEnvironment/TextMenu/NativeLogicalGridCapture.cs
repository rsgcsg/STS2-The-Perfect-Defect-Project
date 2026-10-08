using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using Godot;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Entities.UI;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.addons.mega_text;
using STS2Connector.Authority;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Full logical list capture for the six registered native grid owners.
/// This is independent of the legacy holder-window projection, and does not
/// create a card/target cursor, holder, model operand or native preview clone.</summary>
internal static class NativeLogicalGridCapture
{
    internal static TextMenuFrame? TryCapture(SnapshotBuildResult native,
        PlayerEnvironmentSnapshot informationPage, IReadOnlyList<TextMenuLeaf> informationLeaves,
        NativeEntityRegistry entities)
    {
        if (NOverlayStack.Instance?.Peek() is not NCardGridSelectionScreen owner
            || !NativeLogicalGridCallbacks.Supported(owner.GetType())) return null;
        if (!EnvironmentIdentityRuntime.ExecutionAvailable(native.HostObservation.Game))
            return Partial(informationPage, owner, "native_logical_grid_execution_identity_unavailable");
        NativeLogicalGridState? state = NativeLogicalGridState.Capture(owner);
        if (state == null) return Partial(informationPage, owner, "native_logical_grid_current_binding_missing");
        if (state.Cards.Count > NativeLogicalCapture.MaximumActions)
            return Partial(informationPage, owner, "native_logical_action_capacity_exceeded");
        string ownerId = entities.GetId(owner, "screen");
        var rows = new JsonArray();
        var referents = informationPage.Referents.ToDictionary(value => value.ReferentId, StringComparer.Ordinal);
        var missing = new HashSet<string>(StringComparer.Ordinal);
        var leaves = informationLeaves.ToList();
        foreach (CardModel card in state.Cards)
        {
            string cardId = entities.GetId(card, "card");
            bool selected = state.IsSelected(card);
            NGridCardHolder? holder = state.Grid.GetCardHolder(card);
            bool nativeDisplayed = holder != null && ConnectorMod.IsNodeVisible(holder)
                && holder.CardNode?.Visibility == ModelVisibility.Visible;
            CardModel displayed = state.UpgradeView && nativeDisplayed && holder!.IsShowingUpgradedCard
                ? holder.CardNode!.Model ?? card : card;
            // Native MutableClone invokes copied enchantment/affliction events
            // before AfterCloned clears them. A read must not synthesize it.
            if (state.Stage == "selecting" && state.UpgradeView && card.IsUpgradable && (!nativeDisplayed || !holder!.IsShowingUpgradedCard))
                missing.Add("native_logical_offscreen_upgrade_presentation_unavailable");
            var visible = STS2Connector.LiveHost.LiveContextReader.BuildCard(displayed, cardId,
                selected, displayPile: state.DisplayPile);
            if (visible.ExistingEnchantment != null)
                visible = visible with { ExistingEnchantment = visible.ExistingEnchantment with { Description = null } };
            JsonObject row = JsonSerializer.SerializeToNode(visible, ConnectorMod._jsonOptions) as JsonObject
                ?? throw new InvalidOperationException("Native public card serialization failed.");
            row["observation_basis"] = nativeDisplayed ? "native_displayed_grid_card" : "publicly_available_at_seam";
            row["native_node_displayed"] = nativeDisplayed;
            row["upgrade_preview_requested"] = state.UpgradeView;
            rows.Add(row);
            referents[cardId] = new(cardId, "card", "entity", visible.Name,
                new(true, state.CanToggle(card), selected, false, "native_logical_public_list"),
                "sts2.player-environment/referent/native_logical_grid_card-1", row.DeepClone());
            if (state.CanInspect(card))
            {
                CardModel inspectCard = card;
                leaves.Add(new("logical_grid_inspect:" + cardId, "root", "inspect_card",
                    "Inspect " + (visible.Name ?? visible.DefinitionId), cardId,
                    Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => state.Inspect(inspectCard),
                    new TextMenuNativeWitnessBinding(state.Owner, inspectCard, new Dictionary<string, object>(StringComparer.Ordinal))));
            }
            if (state.CanToggle(card))
            {
                CardModel exact = card;
                string verb = selected ? "deselect" : "select";
                leaves.Add(new("logical_grid_card:" + cardId + ":" + verb, "root", verb,
                    (selected ? "Deselect " : "Select ") + (visible.Name ?? visible.DefinitionId), cardId,
                    Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => state.Toggle(exact, selected),
                    new TextMenuNativeWitnessBinding(state.Owner, exact, new Dictionary<string, object>(StringComparer.Ordinal))));
            }
        }
        if (state.Stage != "completed")
            foreach ((string verb, NButton button) in state.Controls)
            {
                string controlId = entities.GetId(button, "selector_control");
                string label = ControlLabel(verb);
                referents[controlId] = new(controlId, "selector_control", "control", label,
                    new(true, true, false, false, "native_visible_fact"), null, null);
                NButton exact = button;
                string exactVerb = verb;
                leaves.Add(new("logical_grid_control:" + controlId + ":" + verb, "root", verb, label, controlId,
                    Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => state.Click(exactVerb, exact)));
            }
        var selectedIds = state.Selected.Select(card => entities.GetId(card, "card")).ToArray();
        var surface = new JsonObject
        {
            ["kind"] = SurfaceKind(owner), ["stage"] = state.Stage,
            ["cards"] = rows, ["min_select"] = state.Preferences.MinSelect,
            ["max_select"] = state.Preferences.MaxSelect,
            ["require_manual_confirmation"] = state.Preferences.RequireManualConfirmation,
            ["selected_count"] = state.Selected.Count,
            ["selected_card_referent_ids"] = new JsonArray(selectedIds.Select(id => (JsonNode?)JsonValue.Create(id)).ToArray()),
            ["is_peeking"] = state.Peek.IsPeeking,
            ["showing_upgrade_previews"] = state.UpgradeView,
            ["ordering_basis"] = "exact_current_native_grid_logical_list",
            ["native_preview_text"] = CapturePreviewText(state)
        };
        PlayerEnvironmentSnapshot page = informationPage with
        {
            Status = state.Stage == "completed" ? "observed" : state.Stage == "settling" ? "settling" : "interactive",
            Referents = referents.Values.ToArray(),
            Completeness = new(missing.Count == 0 ? "complete" : "partial",
                "complete_entered_native_logical_grid_list",
                "registered_native_card_model_callbacks_and_current_controls",
                missing.Order(StringComparer.Ordinal).ToArray(), informationPage.Completeness.HiddenByPolicy),
            Interaction = informationPage.Interaction with
            {
                InteractionId = ownerId, Kind = SurfaceKind(owner), Stage = state.Stage,
                Prompt = owner.GetNodeOrNull<MegaRichTextLabel>("%BottomLabel")?.Text,
                ContentSchema = "sts2.player-environment/surface/native_logical_grid_selector-1",
                Content = informationPage.Interaction.Content with { Surface = surface },
                Capabilities = Array.Empty<PlayerEnvironmentInteractionCapability>()
            }
        };
        // Unknown/missing required presentation remains partial. The source
        // binding proof certifies full membership, not those missing values.
        return new(page, "logical_grid:" + ownerId + ":" + entities.GetId(state.Completion, "native_selector_request"), leaves)
        { LogicalGridProof = state };
    }

    private static TextMenuFrame Partial(PlayerEnvironmentSnapshot page, NCardGridSelectionScreen owner, string missing) =>
        new(page with { Status = "settling", Completeness = page.Completeness with
        { Status = "partial", Missing = page.Completeness.Missing.Append(missing).Distinct(StringComparer.Ordinal).ToArray() } },
            "logical_grid_unresolved:" + RuntimeHelpers.GetHashCode(owner), Array.Empty<TextMenuLeaf>());

    private static JsonArray CapturePreviewText(NativeLogicalGridState state)
    {
        var text = new JsonArray();
        if (state.Stage != "preview") return text;
        // Freeze only already-native rendered preview labels. This never calls
        // transformation/enchantment factories or creates future preview values.
        foreach (MegaLabel label in ConnectorMod.FindAll<MegaLabel>(state.Owner))
            if (ConnectorMod.IsNodeVisible(label)) text.Add(label.Text);
        foreach (MegaRichTextLabel label in ConnectorMod.FindAll<MegaRichTextLabel>(state.Owner))
            if (ConnectorMod.IsNodeVisible(label)) text.Add(label.Text);
        return text;
    }

    internal static string SurfaceKind(NCardGridSelectionScreen owner) => owner switch
    {
        NDeckCardSelectScreen => "native_deck_card_selection",
        NDeckUpgradeSelectScreen => "deck_upgrade_selection",
        NDeckTransformSelectScreen => "deck_transform_selection",
        NDeckEnchantSelectScreen => "deck_enchant_selection",
        NSimpleCardSelectScreen => "native_simple_card_selection",
        NCombatPileCardSelectScreen => "native_combat_pile_selection",
        _ => throw new InvalidOperationException("Unregistered native logical grid owner.")
    };
    private static string ControlLabel(string verb) => verb switch
    {
        "confirm" => "Confirm selection", "confirm_selection" => "Confirm selected cards",
        "open_preview" => "Preview selected cards", "cancel_preview" => "Return to card selection",
        "cancel_selection" => "Cancel card selection", "toggle_upgrade_view" => "Toggle upgrade view",
        _ => throw new InvalidOperationException("Unregistered native selector control.")
    };
}
