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
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.addons.mega_text;
using STS2Connector.Authority;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Platform.NativeFoundation;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Acquires the existing full native grid once, before either Source
/// projection. Later native composition consumes frozen facts and the same
/// exact input specs; it cannot obtain a newer roster or rerender a candidate.</summary>
internal static class NativeLogicalGridCapture
{
    internal static NativeLogicalGridCaptureResult CaptureCurrent(NativeEntityRegistry entities, GameBuildIdentity game)
    {
        if (NOverlayStack.Instance?.Peek() is not NCardGridSelectionScreen owner
            || !NativeLogicalGridCallbacks.Supported(owner.GetType()))
            return new(true, null, Array.Empty<string>());
        if (!EnvironmentIdentityRuntime.ExecutionAvailable(game))
            return new(true, null, new[] { "native_logical_grid_execution_identity_unavailable" });
        NativeLogicalGridState? state = NativeLogicalGridState.Capture(owner);
        if (state == null)
            return new(true, null, new[] { "native_logical_grid_current_binding_missing" });
        if (state.Cards.Count > NativeLogicalCapture.MaximumActions)
            return new(true, null, new[] { "native_logical_action_capacity_exceeded" });
        return new(true, Freeze(state, entities), Array.Empty<string>());
    }

    private static NativeLogicalCapturedGrid Freeze(NativeLogicalGridState state, NativeEntityRegistry entities)
    {
        string ownerId = entities.GetId(state.Owner, "screen");
        string requestId = entities.GetId(state.Completion, "native_selector_request");
        string kind = SurfaceKind(state.Owner);
        string? prompt = state.Owner.GetNodeOrNull<MegaRichTextLabel>("%BottomLabel")?.Text;
        bool isPeeking = state.Peek.IsPeeking;
        var nativeMissing = new HashSet<string>(StringComparer.Ordinal);
        var inputs = new List<NativeLogicalGridInputSpec>();
        var nativeReferents = new List<PlayerEnvironmentReferent>();
        var neutralReferents = new List<PlayerEnvironmentReferent>();
        var nativeRows = new JsonArray();
        var cache = new List<(CardModel Model, PileType Pile, CardPreviewMode Mode, NativeSelectorPublicProjection.Presentation Value)>();
        NativeSelectorPublicProjection.Presentation Render(CardModel card, PileType pile, CardPreviewMode mode)
        {
            foreach (var entry in cache)
                if (ReferenceEquals(entry.Model, card) && entry.Pile == pile && entry.Mode == mode) return entry.Value;
            NativePublicCardDetails.Result captured = NativePublicCardDetails.Capture(card, pile, mode);
            var value = new NativeSelectorPublicProjection.Presentation(captured.PublicCard, captured.Missing);
            cache.Add((card, pile, mode, value));
            return value;
        }
        NativeSelectorPreviewFacts facts = NativeSelectorPublicFacts.Capture(state);
        if (!facts.Request.MechanicsQualified) nativeMissing.UnionWith(facts.Request.Missing);
        NativeSelectorPublicProjection.Result projected = NativeSelectorPublicProjection.Project(facts, ownerId, requestId,
            card => entities.GetId(card, "card"), state.DisplayPile, Render);
        var fullCards = new List<VisibleCard>();
        var displays = new List<NativeLogicalGridCardDisplay>();
        for (int index = 0; index < state.Cards.Count; index++)
        {
            CardModel original = state.Cards[index];
            string cardId = entities.GetId(original, "card");
            bool selected = state.IsSelected(original);
            JsonObject option = (JsonObject)projected.Options[index]!;
            JsonObject before = (JsonObject)option["before"]!;
            NativeSelectorPublicProjection.Presentation beforeCapture = Render(original, state.DisplayPile, CardPreviewMode.Normal);
            VisibleCard full = Visible(before, cardId, selected, beforeCapture.Missing);
            fullCards.Add(full);
            displays.Add(new(full, "selector_original:" + facts.Request.Family));
            if (option["after"] is JsonObject after)
            {
                NativeDetachedCardPreview detached = facts.Candidates[index].Detached!;
                var afterCapture = Render(detached.Display, detached.DisplayPile, detached.PreviewMode);
                displays.Add(new(Visible(after, cardId, false, afterCapture.Missing), facts.Candidates[index].DisplayContext));
            }
            bool canInspect = facts.Request.MechanicsQualified && state.CanInspect(original);
            bool canToggle = facts.Request.MechanicsQualified && state.CanToggle(original);
            NGridCardHolder? holder = state.Grid.GetCardHolder(original);
            bool displayed = holder != null && ConnectorMod.IsNodeVisible(holder)
                && holder.CardNode?.Visibility == ModelVisibility.Visible;
            CardModel display = state.UpgradeView && displayed && holder!.IsShowingUpgradedCard
                ? holder.CardNode!.Model ?? original : original;
            if (state.Stage == "selecting" && state.UpgradeView && (!displayed || !holder!.IsShowingUpgradedCard))
                nativeMissing.Add("native_logical_offscreen_upgrade_presentation_unavailable");
            var legacyCapture = Render(display, state.DisplayPile, CardPreviewMode.Normal);
            nativeMissing.UnionWith(legacyCapture.Missing);
            JsonObject legacyPublic = JsonSerializer.SerializeToNode(legacyCapture.Card) as JsonObject ?? new();
            VisibleCard visible = Visible(legacyPublic, cardId, selected, legacyCapture.Missing) with { TargetType = null };
            if (visible.Description != null)
                visible = visible with { Description = ConnectorMod.StripRichTextTags(visible.Description).Replace("\n", " ") };
            if (visible.ExistingEnchantment != null)
                visible = visible with { ExistingEnchantment = visible.ExistingEnchantment with { Description = null } };
            JsonObject row = JsonSerializer.SerializeToNode(visible, ConnectorMod._jsonOptions) as JsonObject
                ?? throw new InvalidOperationException("Native public card serialization failed.");
            row["observation_basis"] = displayed ? "native_displayed_grid_card" : "publicly_available_at_seam";
            row["native_node_displayed"] = displayed;
            row["upgrade_preview_requested"] = state.UpgradeView;
            nativeRows.Add(row);
            nativeReferents.Add(CardReferent(cardId, visible.Name, selected, canToggle, row));
            neutralReferents.Add(CardReferent(cardId, full.Name, selected, canToggle, before));
            string label = full.Name ?? full.DefinitionId ?? cardId;
            if (canInspect)
                inputs.Add(Spec("logical_grid_inspect:" + cardId, "inspect_card", "inspect", "Inspect " + label,
                    cardId, NativeLogicalGridInputKind.Inspect, original));
            if (canToggle)
            {
                string verb = selected ? "deselect" : "select";
                inputs.Add(Spec("logical_grid_card:" + cardId + ":" + verb, verb, verb,
                    (selected ? "Deselect " : "Select ") + label, cardId, NativeLogicalGridInputKind.Toggle, original, selected));
            }
        }
        if (state.Stage != "completed" && facts.Request.MechanicsQualified)
            foreach ((string verb, NButton button) in state.Controls)
            {
                string controlId = entities.GetId(button, "selector_control");
                string label = ControlLabel(verb);
                var referent = new PlayerEnvironmentReferent(controlId, "selector_control", "control", label,
                    new(true, true, false, false, "native_visible_fact"), null, null);
                nativeReferents.Add(referent); neutralReferents.Add(referent);
                inputs.Add(Spec("logical_grid_control:" + controlId + ":" + verb, verb, PublicControlVerb(verb), label,
                    controlId, NativeLogicalGridInputKind.Control, control: button));
            }
        var selectedIds = state.Selected.Select(card => entities.GetId(card, "card")).ToArray();
        var nativeSurface = new JsonObject
        {
            ["kind"] = kind, ["stage"] = state.Stage, ["cards"] = nativeRows,
            ["min_select"] = facts.Request.MinSelect, ["max_select"] = facts.Request.MaxSelect,
            ["require_manual_confirmation"] = facts.Request.RequireManualConfirmation,
            ["selected_count"] = state.Selected.Count, ["selected_card_referent_ids"] = Strings(selectedIds),
            ["is_peeking"] = isPeeking, ["showing_upgrade_previews"] = state.UpgradeView,
            ["ordering_basis"] = "exact_current_native_grid_logical_list", ["native_preview_text"] = CapturePreviewText(state)
        };
        JsonObject neutral = (JsonObject)nativeSurface.DeepClone();
        neutral["cards"] = new JsonArray(projected.Options.Select(option => option!["before"]!.DeepClone()).ToArray());
        neutral["selector_request"] = projected.Request.DeepClone();
        neutral["preview_options"] = projected.Options.DeepClone();
        neutral["enchantment_summary"] = projected.EnchantmentSummary?.DeepClone();
        neutral["prompt"] = prompt;
        var neutralSurface = new NativePublicBasisGridSurface(kind, state.Stage, ownerId, requestId, fullCards.ToArray())
        {
            PublicSurface = neutral, CardDisplays = displays.ToArray(), Referents = neutralReferents.ToArray(),
            Inputs = inputs.ToArray(), Missing = projected.Missing.ToArray()
        };
        return new(state, ownerId, requestId, nativeSurface, nativeReferents.ToArray(), inputs.ToArray(),
            nativeMissing.Order(StringComparer.Ordinal).ToArray(), prompt, neutralSurface);

        NativeLogicalGridInputSpec Spec(string leaf, string verb, string publicVerb, string label, string subject,
            NativeLogicalGridInputKind inputKind, CardModel? card = null, bool? expected = null, NButton? control = null) =>
            new("logical_grid_request:" + requestId + ":" + leaf, leaf, requestId, verb, publicVerb, state.Stage,
                label, subject, inputKind, state, card, expected, control);
    }

    /// <summary>Pure composition: no current-screen lookup, State.Capture,
    /// model formatting or native getter is permitted below this boundary.</summary>
    internal static TextMenuFrame? ComposeNativeFrame(SnapshotBuildResult native,
        PlayerEnvironmentSnapshot informationPage, IReadOnlyList<TextMenuLeaf> informationLeaves,
        NativeLogicalGridCaptureResult capture)
    {
        if (!capture.Attempted) throw new InvalidOperationException("Native logical grid acquisition was not attempted.");
        if (capture.Grid is not { } grid)
        {
            if (capture.Missing.Count == 0) return null;
            TextMenuFrame failed = NativeLogicalCapturePolicy.Partial(informationPage, "logical_grid_unresolved", capture.Missing[0]);
            foreach (string missing in capture.Missing.Skip(1))
                failed = NativeLogicalCapturePolicy.Partial(failed.Page, failed.OwnerKey, missing);
            return failed;
        }
        var referents = informationPage.Referents.ToDictionary(value => value.ReferentId, StringComparer.Ordinal);
        foreach (PlayerEnvironmentReferent referent in grid.NativeReferents)
            referents[referent.ReferentId] = referent with { Properties = referent.Properties?.DeepClone() };
        var leaves = informationLeaves.Concat(grid.Inputs.Select(spec => new TextMenuLeaf(spec.NativeLeafKey,
            "root", spec.NativeVerb, spec.Label, spec.SubjectEntityId, Array.Empty<PlayerEnvironmentBoundActionArgument>(), spec.Dispatch,
            spec.Card == null ? null : new TextMenuNativeWitnessBinding(spec.State.Owner, spec.Card,
                new Dictionary<string, object>(StringComparer.Ordinal))))).ToArray();
        PlayerEnvironmentCompleteness completeness = NativeLogicalCapturePolicy.Replace(informationPage.Completeness,
            NativeLogicalProjectionReplacement.Grid,
            NativeLogicalCapturePolicy.DisplacesLegacyProjection(native.HostObservation.Surface, grid.State.Owner.GetType().Name,
                grid.NeutralSurface.Kind, grid.OwnerEntityId), grid.NativeMissing,
            "complete_entered_native_logical_grid_list", "registered_native_card_model_callbacks_and_current_controls");
        PlayerEnvironmentSnapshot page = informationPage with
        {
            Status = NativeLogicalCapturePolicy.ProjectionStatus(grid.NeutralSurface.Stage, informationPage.Status, completeness),
            Referents = referents.Values.ToArray(), Completeness = completeness,
            Interaction = informationPage.Interaction with
            {
                InteractionId = grid.OwnerEntityId, Kind = grid.NeutralSurface.Kind, Stage = grid.NeutralSurface.Stage,
                Prompt = grid.Prompt, ContentSchema = "sts2.player-environment/surface/native_logical_grid_selector-1",
                Content = informationPage.Interaction.Content with { Surface = grid.NativeSurface.DeepClone() },
                Capabilities = Array.Empty<PlayerEnvironmentInteractionCapability>()
            }
        };
        return new(page, "logical_grid:" + grid.OwnerEntityId + ":" + grid.RequestEntityId, leaves)
        { LogicalGridProof = grid.State };
    }

    private static PlayerEnvironmentReferent CardReferent(string id, string? name, bool selected, bool canToggle, JsonObject row) =>
        new(id, "card", "entity", name, new(true, canToggle, selected, false, "native_logical_public_list"),
            "sts2.player-environment/referent/native_logical_grid_card-1", row.DeepClone());

    private static VisibleCard Visible(JsonObject value, string id, bool selected, IReadOnlyList<string> missing)
    {
        string? Text(string name) => value[name]?.GetValueKind() == JsonValueKind.String ? value[name]!.GetValue<string>() : null;
        VisibleEnchantment? enchantment = value["existing_enchantment"] is JsonObject attached
            && attached["amount"] is JsonValue amount && amount.TryGetValue<int>(out int actual)
            ? new(attached["definition_id"]?.GetValue<string>()!, attached["name"]?.GetValue<string>(),
                attached["description"]?.GetValue<string>(), actual, "card_hover_semantics") : null;
        return new(id, Text("definition_id")!, Text("name"), Text("type")!, Text("cost")!, Text("star_cost"),
            Text("description"), Text("rarity")!, value["is_upgraded"]?.GetValue<bool>() ?? false, selected,
            enchantment, Text("target_type"))
        {
            CurrentPublicDetails = new JsonObject { ["keywords"] = value["keywords"]?.DeepClone(), ["card_previews"] = value["card_previews"]?.DeepClone() },
            CurrentPublicDetailsMissing = missing.ToArray()
        };
    }

    private static JsonArray Strings(IEnumerable<string> values) => new(values.Select(id => (JsonNode?)JsonValue.Create(id)).ToArray());

    private static JsonArray CapturePreviewText(NativeLogicalGridState state)
    {
        var text = new JsonArray();
        if (state.Stage != "preview") return text;
        foreach (MegaLabel label in ConnectorMod.FindAll<MegaLabel>(state.Owner))
            if (ConnectorMod.IsNodeVisible(label)) text.Add(label.Text);
        foreach (MegaRichTextLabel label in ConnectorMod.FindAll<MegaRichTextLabel>(state.Owner))
            if (ConnectorMod.IsNodeVisible(label)) text.Add(label.Text);
        return text;
    }

    internal static string SurfaceKind(NCardGridSelectionScreen owner) => owner switch
    {
        NDeckCardSelectScreen => "native_deck_card_selection", NDeckUpgradeSelectScreen => "deck_upgrade_selection",
        NDeckTransformSelectScreen => "deck_transform_selection", NDeckEnchantSelectScreen => "deck_enchant_selection",
        NSimpleCardSelectScreen => "native_simple_card_selection", NCombatPileCardSelectScreen => "native_combat_pile_selection",
        _ => throw new InvalidOperationException("Unregistered native logical grid owner.")
    };
    private static string PublicControlVerb(string verb) => verb switch
    {
        "confirm" or "confirm_selection" => "confirm", "open_preview" or "toggle_upgrade_view" => "open",
        "cancel_preview" or "cancel_selection" => "cancel", _ => throw new InvalidOperationException("Unregistered native selector control.")
    };
    private static string ControlLabel(string verb) => verb switch
    {
        "confirm" => "Confirm selection", "confirm_selection" => "Confirm selected cards", "open_preview" => "Preview selected cards",
        "cancel_preview" => "Return to card selection", "cancel_selection" => "Cancel card selection", "toggle_upgrade_view" => "Toggle upgrade view",
        _ => throw new InvalidOperationException("Unregistered native selector control.")
    };
}
