using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Godot;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.NativeUi;

internal sealed record NativeDeckUpgradeSelectionSurface(
    string Kind,
    string Stage,
    string ScreenEntityId,
    string Prompt,
    int MinSelect,
    int MaxSelect,
    int SelectedCount,
    IReadOnlyList<string> SelectedCardEntityIds,
    IReadOnlyList<string> SelectableCardEntityIds,
    IReadOnlyList<string> DeselectableCardEntityIds,
    bool Cancelable,
    bool ShowingUpgradePreviews,
    bool CanToggleUpgradeView,
    bool CanCancelSelection,
    bool CanCancelPreview,
    bool CanConfirm,
    IReadOnlyList<VisibleCard> Cards,
    IReadOnlyList<VisibleCard> PreviewCards) : ILiveSurface;

/// <summary>
/// Source-free adapter for the native deck-upgrade selector. The visible
/// screen and its exact current controls grant actionability; the event, rest
/// option, relic, or other caller that opened it is intentionally irrelevant.
/// </summary>
internal static class NativeDeckUpgradeSelection
{
    internal const string SurfaceKind = "deck_upgrade_selection";
    internal const string SelectOperation = "select_deck_upgrade_card";
    internal const string DeselectOperation = "deselect_deck_upgrade_card";
    internal const string CancelSelectionOperation = "cancel_deck_upgrade_selection";
    internal const string CancelPreviewOperation = "cancel_deck_upgrade_preview";
    internal const string ConfirmOperation = "confirm_deck_upgrade";
    internal const string ToggleUpgradeViewOperation = "activate_deck_upgrade_view";

    private const BindingFlags Flags = BindingFlags.Instance | BindingFlags.NonPublic;
    private static readonly FieldInfo? ClickableField =
        typeof(NCardHolder).GetField("_isClickable", Flags);
    private static readonly FieldInfo? PreviewAfterField =
        typeof(NUpgradePreview).GetField("_after", Flags);

    internal static LiveObservation? TryBuild(
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        ActiveSurfaceSnapshot active = ActiveInputResolver.Capture();
        if (active.TopOverlay is not NDeckUpgradeSelectScreen screen)
            return null;

        ILiveContext context = LiveContextReader.Build(entities);
        if (!BoundedCardSelectionFacts.TryRead(
                screen,
                out CardSelectorPrefs prefs,
                out IReadOnlyList<CardModel> selectedCards,
                out string? bindingError)
            || ClickableField == null
            || PreviewAfterField == null)
        {
            return BindingUnavailable(
                game,
                context,
                bindingError ?? "The exact deck-upgrade selector binding is unavailable.");
        }

        NCardGrid? grid = ConnectorMod.FindFirst<NCardGrid>(screen);
        Control? singlePreview = screen.GetNodeOrNull<Control>("%UpgradeSinglePreviewContainer");
        Control? multiPreview = screen.GetNodeOrNull<Control>("%UpgradeMultiPreviewContainer");
        NBackButton? close = screen.GetNodeOrNull<NBackButton>("%Close");
        NTickbox? upgrades = screen.GetNodeOrNull<NTickbox>("%Upgrades");
        if (grid == null || singlePreview == null || multiPreview == null
            || close == null || upgrades == null)
        {
            return BindingUnavailable(
                game,
                context,
                "One or more exact deck-upgrade controls are unavailable.");
        }

        bool singleVisible = ConnectorMod.IsNodeVisible(singlePreview);
        bool multiVisible = ConnectorMod.IsNodeVisible(multiPreview);
        if (singleVisible && multiVisible)
        {
            return BindingUnavailable(
                game,
                context,
                "Both mutually exclusive deck-upgrade preview stages are visible.");
        }

        string? prompt = ReadNodeText(screen, "%BottomLabel");
        if (string.IsNullOrWhiteSpace(prompt))
        {
            return BindingUnavailable(
                game,
                context,
                "The current player-visible deck-upgrade prompt is unavailable.");
        }

        string stage = singleVisible || multiVisible ? "preview" : "selecting";
        NGridCardHolder[] holders = ConnectorMod.FindAllSortedByPosition<NGridCardHolder>(screen)
            .Where(holder => ConnectorMod.IsNodeVisible(holder) && holder.CardModel != null)
            .ToArray();
        if (holders.Length == 0)
        {
            return BindingUnavailable(
                game,
                context,
                "The current deck-upgrade selector has no visible card holders.");
        }

        HashSet<CardModel> selected = selectedCards.ToHashSet();
        var cardIds = new Dictionary<CardModel, string>();
        var cards = new List<VisibleCard>(holders.Length);
        foreach (NGridCardHolder holder in holders)
        {
            CardModel original = holder.CardModel;
            string cardId = entities.GetId(original, "card");
            cardIds[original] = cardId;
            CardModel displayed = holder.IsShowingUpgradedCard
                ? holder.CardNode?.Model ?? original
                : original;
            cards.Add(LiveContextReader.BuildCard(
                displayed,
                cardId,
                selected.Contains(original),
                displayPile: PileType.Deck));
        }

        string[] selectedIds = selected
            .Select(card => cardIds.TryGetValue(card, out string? id) ? id : null)
            .Where(id => id != null)
            .Cast<string>()
            .OrderBy(id => id, StringComparer.Ordinal)
            .ToArray();
        if (selectedIds.Length != selected.Count)
        {
            return BindingUnavailable(
                game,
                context,
                "A selected upgrade card is absent from the current visible grid.");
        }

        VisibleCard[] previewCards = stage == "preview"
            ? BuildPreviewCards(singleVisible, singlePreview, multiPreview, entities)
            : Array.Empty<VisibleCard>();
        if (stage == "preview"
            && (selected.Count == 0 || previewCards.Length != selected.Count))
        {
            return BindingUnavailable(
                game,
                context,
                "The visible upgrade preview does not match the exact selected-card count.");
        }

        string[] selectableIds = stage == "selecting"
            ? holders.Where(holder =>
                    IsHolderClickable(holder)
                    && holder.CardModel.IsUpgradable
                    && !selected.Contains(holder.CardModel)
                    && selected.Count < prefs.MaxSelect)
                .Select(holder => cardIds[holder.CardModel])
                .OrderBy(id => id, StringComparer.Ordinal)
                .ToArray()
            : Array.Empty<string>();
        string[] deselectableIds = stage == "selecting"
            ? holders.Where(holder =>
                    IsHolderClickable(holder)
                    && selected.Contains(holder.CardModel))
                .Select(holder => cardIds[holder.CardModel])
                .OrderBy(id => id, StringComparer.Ordinal)
                .ToArray()
            : Array.Empty<string>();
        Control? activePreview = singleVisible ? singlePreview : multiVisible ? multiPreview : null;
        NBackButton? previewCancel = activePreview?.GetNodeOrNull<NBackButton>("Cancel");
        NConfirmButton? previewConfirm = activePreview?.GetNodeOrNull<NConfirmButton>("Confirm");
        bool canCancelSelection = stage == "selecting"
                                  && prefs.Cancelable
                                  && IsVisibleEnabled(close);
        bool canCancelPreview = stage == "preview" && IsVisibleEnabled(previewCancel);
        bool canConfirm = stage == "preview"
                          && NativeSelectorControl.Available(previewConfirm, IsVisibleEnabled);
        bool canToggleUpgradeView = stage == "selecting"
                                    && upgrades.IsEnabled
                                    && ConnectorMod.IsNodeVisible(upgrades);
        var surface = new NativeDeckUpgradeSelectionSurface(
            SurfaceKind,
            stage,
            entities.GetId(screen, "screen"),
            prompt,
            prefs.MinSelect,
            prefs.MaxSelect,
            selected.Count,
            selectedIds,
            selectableIds,
            deselectableIds,
            prefs.Cancelable,
            grid.IsShowingUpgrades,
            canToggleUpgradeView,
            canCancelSelection,
            canCancelPreview,
            canConfirm,
            cards,
            previewCards);
        bool actionable = DescribeCommands(surface).Count > 0;
        return new LiveObservation(
            StableIdentityHash.Object(new { game.Version, context, surface }),
            actionable ? "ready" : "settling",
            context,
            surface,
            new StateCompleteness(
                "complete_current_structured_ui",
                actionable
                    ? "derived_from_current_visible_enabled_controls"
                    : "temporarily_empty_while_native_ui_settles",
                new[]
                {
                    "NDeckUpgradeSelectScreen visible overlay and card grid",
                    "NDeckUpgradeSelectScreen current selection and preview controls",
                    "NUpgradePreview current visible after-card presentation"
                },
                Array.Empty<string>()),
            game,
            new[]
            {
                "The caller and eventual game effect are intentionally not part of Player Environment action authority."
            })
        {
            InputOwnership = new InputOwnership(
                "current_ui_owned",
                SurfaceKind,
                "The exact current native deck-upgrade selector owns input.")
        };
    }

    internal static IReadOnlyList<NativeUiActionDescriptor> DescribeCommands(
        NativeDeckUpgradeSelectionSurface surface)
    {
        if (surface.Kind != SurfaceKind)
            return Array.Empty<NativeUiActionDescriptor>();

        Dictionary<string, VisibleCard> cards = surface.Cards
            .ToDictionary(card => card.EntityId, StringComparer.Ordinal);
        var actions = new List<NativeUiActionDescriptor>();
        ActionEntityBinding screen = new("screen", surface.ScreenEntityId);
        foreach (string cardId in surface.SelectableCardEntityIds)
        {
            if (cards.TryGetValue(cardId, out VisibleCard? card))
            {
                actions.Add(Descriptor(
                    SelectOperation,
                    $"Select {card.Name ?? card.DefinitionId} for upgrade",
                    new[] { screen, new ActionEntityBinding("card", cardId) }));
            }
        }
        foreach (string cardId in surface.DeselectableCardEntityIds)
        {
            if (cards.TryGetValue(cardId, out VisibleCard? card))
            {
                actions.Add(Descriptor(
                    DeselectOperation,
                    $"Deselect {card.Name ?? card.DefinitionId}",
                    new[] { screen, new ActionEntityBinding("card", cardId) }));
            }
        }
        if (surface.CanCancelSelection)
            actions.Add(Descriptor(CancelSelectionOperation, "Cancel card upgrade selection", new[] { screen }));
        if (surface.CanToggleUpgradeView)
        {
            actions.Add(Descriptor(
                ToggleUpgradeViewOperation,
                surface.ShowingUpgradePreviews
                    ? "Show current card versions"
                    : "Show upgraded card previews",
                new[] { screen }));
        }
        if (surface.CanCancelPreview)
            actions.Add(Descriptor(CancelPreviewOperation, "Return to card selection", new[] { screen }));
        if (surface.CanConfirm)
            actions.Add(Descriptor(ConfirmOperation, "Confirm card upgrade", new[] { screen }));
        return actions;
    }

    internal static NativeInputResult Start(
        NativeEntityRegistry entities,
        NativeDeckUpgradeSelectionSurface surface,
        NativeUiBoundAction binding,
        IReadOnlyDictionary<string, string> parameters)
    {
        string? bindingError = null;
        if (!parameters.TryGetValue("screen_id", out string? screenId)
            || !string.Equals(screenId, surface.ScreenEntityId, StringComparison.Ordinal)
            || !entities.TryResolve(screenId, out NDeckUpgradeSelectScreen? screen)
            || screen == null
            || !IsCurrent(screen)
            || !BoundedCardSelectionFacts.TryRead(
                screen,
                out CardSelectorPrefs prefs,
                out IReadOnlyList<CardModel> selectedCards,
                out bindingError))
        {
            return NativeInputResult.Rejected(
                "player_environment_owner_changed",
                bindingError ?? "The exact deck-upgrade selector is no longer current.");
        }

        string operation = binding.Candidate.Operation;
        if (operation is SelectOperation or DeselectOperation
            && parameters.TryGetValue("card_id", out string? cardId)
            && entities.TryResolve(cardId, out CardModel? card)
            && card != null)
        {
            bool currentlySelected = selectedCards.Any(value => ReferenceEquals(value, card));
            bool advertisedSelect = surface.SelectableCardEntityIds.Contains(cardId, StringComparer.Ordinal);
            bool advertisedDeselect = surface.DeselectableCardEntityIds.Contains(cardId, StringComparer.Ordinal);
            if ((operation == SelectOperation && (currentlySelected || !advertisedSelect))
                || (operation == DeselectOperation && (!currentlySelected || !advertisedDeselect))
                || !card.IsUpgradable
                || (!currentlySelected && selectedCards.Count >= prefs.MaxSelect))
            {
                return NotActionable("The card's selected or upgradeable state changed.");
            }
            NGridCardHolder[] matching = ConnectorMod.FindAllSortedByPosition<NGridCardHolder>(screen)
                .Where(holder => ReferenceEquals(holder.CardModel, card)
                                 && ConnectorMod.IsNodeVisible(holder)
                                 && IsHolderClickable(holder))
                .Take(2)
                .ToArray();
            NCardGrid? grid = ConnectorMod.FindFirst<NCardGrid>(screen);
            if (matching.Length != 1 || grid == null || IsPreviewVisible(screen))
                return NotActionable("The exact current upgrade-card holder is no longer actionable.");
            grid.EmitSignal(NCardGrid.SignalName.HolderPressed, matching[0]);
            return NativeInputResult.Delivered("native_deck_upgrade_card_holder_pressed");
        }

        if (operation == CancelSelectionOperation && !IsPreviewVisible(screen) && prefs.Cancelable)
            return Click(screen.GetNodeOrNull<NBackButton>("%Close"), "native_deck_upgrade_cancel_clicked");
        if (operation == ToggleUpgradeViewOperation && !IsPreviewVisible(screen))
        {
            NCardGrid? grid = ConnectorMod.FindFirst<NCardGrid>(screen);
            NTickbox? toggle = screen.GetNodeOrNull<NTickbox>("%Upgrades");
            if (grid == null || toggle == null
                || grid.IsShowingUpgrades != surface.ShowingUpgradePreviews
                || !toggle.IsEnabled || !ConnectorMod.IsNodeVisible(toggle))
            {
                return NotActionable("The exact upgrade-preview toggle changed.");
            }
            toggle.ForceToggleTick();
            return NativeInputResult.Delivered("native_deck_upgrade_view_toggled");
        }

        Control? activePreview = ActivePreview(screen);
        if (operation == CancelPreviewOperation && activePreview != null)
            return Click(activePreview.GetNodeOrNull<NBackButton>("Cancel"), "native_deck_upgrade_preview_cancel_clicked");
        if (operation == ConfirmOperation && activePreview != null)
        {
            IReadOnlyList<CardModel> currentSelection =
                BoundedCardSelectionFacts.ReadSelectedCards(screen);
            if (currentSelection.Count == 0
                || currentSelection.Count != selectedCards.Count
                || currentSelection.Any(card => !card.IsUpgradable)
                || currentSelection.Any(current =>
                    !selectedCards.Any(expected => ReferenceEquals(expected, current))))
            {
                return NotActionable("The exact upgrade selection is no longer commit-ready.");
            }
            return Click(activePreview.GetNodeOrNull<NConfirmButton>("Confirm"), "native_deck_upgrade_confirm_clicked");
        }

        return NotActionable("The exact advertised deck-upgrade control is no longer current.");
    }

    private static NativeUiActionDescriptor Descriptor(
        string operation,
        string label,
        IReadOnlyList<ActionEntityBinding> bindings) => new(
            operation,
            operation,
            operation.Contains("confirm", StringComparison.Ordinal) ? "commit" : "selection",
            label,
            $"NDeckUpgradeSelectScreen current UI input delivery:{operation}",
            bindings);

    private static VisibleCard[] BuildPreviewCards(
        bool singleVisible,
        Control singlePreview,
        Control multiPreview,
        NativeEntityRegistry entities)
    {
        Control? root;
        if (singleVisible)
        {
            NUpgradePreview? preview = singlePreview.GetNodeOrNull<NUpgradePreview>("UpgradePreview");
            root = preview == null ? null : PreviewAfterField?.GetValue(preview) as Control;
        }
        else
        {
            root = multiPreview.GetNodeOrNull<Control>("Cards");
        }
        if (root == null)
            return Array.Empty<VisibleCard>();

        return ConnectorMod.FindAll<NPreviewCardHolder>(root)
            .Where(holder => ConnectorMod.IsNodeVisible(holder) && holder.CardModel != null)
            .Select(holder => holder.CardModel)
            .OfType<CardModel>()
            .Select(card => LiveContextReader.BuildCard(
                card,
                entities.GetId(card, "upgrade_preview_card"),
                displayPile: PileType.Deck))
            .ToArray();
    }

    private static Control? ActivePreview(NDeckUpgradeSelectScreen screen)
    {
        Control? single = screen.GetNodeOrNull<Control>("%UpgradeSinglePreviewContainer");
        if (single != null && ConnectorMod.IsNodeVisible(single))
            return single;
        Control? multi = screen.GetNodeOrNull<Control>("%UpgradeMultiPreviewContainer");
        return multi != null && ConnectorMod.IsNodeVisible(multi) ? multi : null;
    }

    private static bool IsPreviewVisible(NDeckUpgradeSelectScreen screen) =>
        ActivePreview(screen) != null;

    private static bool IsCurrent(NDeckUpgradeSelectScreen screen) =>
        ActiveInputResolver.IsVisibleActiveOverlay(screen)
        && ReferenceEquals(NOverlayStack.Instance?.Peek(), screen);

    private static bool IsHolderClickable(NCardHolder holder) =>
        ClickableField?.GetValue(holder) is true;

    private static bool IsVisibleEnabled(Control? control) => control switch
    {
        NBackButton back => ConnectorMod.IsNodeVisible(back) && back.IsEnabled,
        NConfirmButton confirm => ConnectorMod.IsNodeVisible(confirm) && confirm.IsEnabled,
        _ => false
    };

    private static NativeInputResult Click(Control? control, string evidence)
    {
        if (!IsVisibleEnabled(control))
            return NotActionable("The exact native control is no longer visible and enabled.");
        switch (control)
        {
            case NBackButton back:
                back.ForceClick();
                break;
            case NConfirmButton confirm:
                confirm.ForceClick();
                break;
            default:
                return NotActionable("The exact native control type changed.");
        }
        return NativeInputResult.Delivered(evidence);
    }

    private static string? ReadNodeText(Node root, string path)
    {
        try
        {
            Node? node = root.GetNodeOrNull(path);
            if (node == null)
                return null;
            Variant value = node.Get("text");
            return value.VariantType == Variant.Type.Nil
                ? null
                : ConnectorMod.StripRichTextTags(value.AsString()).Replace("\n", " ");
        }
        catch
        {
            return null;
        }
    }

    private static NativeInputResult NotActionable(string detail) =>
        NativeInputResult.Rejected("player_environment_target_not_actionable", detail);

    private static LiveObservation BindingUnavailable(
        GameBuildIdentity game,
        ILiveContext context,
        string detail) =>
        NativeUiFailClosedObservation.BindingUnavailable(
            game,
            context,
            nameof(NDeckUpgradeSelectScreen),
            detail,
            new[] { "NDeckUpgradeSelectScreen current visible UI mechanics" },
            new[] { "visible_cards", "visible_preview", "current_controls" },
            "player_environment_deck_upgrade_binding_unavailable",
            "native-ui.deck-upgrade.binding-unavailable",
            "The current deck-upgrade UI cannot be represented without guessing a target.");
}
