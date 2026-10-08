using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Godot;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.NativeUi;

/// <summary>
/// Source-free adapter for the native random deck-transform selector. Current
/// visible controls determine affordances; STS2 remains responsible for the
/// transformation source, legality, RNG and effects.
/// </summary>
internal static class NativeDeckTransformSelection
{
    internal const string SurfaceKind = "deck_transform_selection";
    private const BindingFlags Flags = BindingFlags.Instance | BindingFlags.NonPublic;

    private static readonly FieldInfo? ClickableField =
        typeof(NCardHolder).GetField("_isClickable", Flags);

    internal static LiveObservation? TryBuild(
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        ActiveSurfaceSnapshot active = ActiveInputResolver.Capture();
        if (active.TopOverlay is not NDeckTransformSelectScreen screen)
            return null;

        ILiveContext context = LiveContextReader.Build(entities);
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        if (runState == null
            || runState.Players.Count != 1
            || LocalContext.GetMe(runState) is not { } player)
        {
            return BindingUnavailable(
                game,
                context,
                "The exact local single-player transform owner is unavailable.",
                new[] { "transform_owner", "legal_actions" });
        }

        return Build(screen, context, player, entities, game);
    }

    private static LiveObservation Build(
        NDeckTransformSelectScreen screen,
        ILiveContext context,
        Player player,
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        if (!BoundedCardSelectionFacts.TryRead(
                screen,
                out CardSelectorPrefs prefs,
                out IReadOnlyList<CardModel> selectedCards,
                out string? bindingError)
            || ClickableField == null)
        {
            return BindingUnavailable(
                game,
                context,
                bindingError ?? "The exact deck-transform selection binding is unavailable.",
                new[] { "selection_constraints", "selected_cards", "legal_actions" });
        }

        NCardGrid? grid = ConnectorMod.FindFirst<NCardGrid>(screen);
        NConfirmButton? previewButton = screen.GetNodeOrNull<NConfirmButton>("Confirm");
        Control? previewContainer = screen.GetNodeOrNull<Control>("%PreviewContainer");
        NBackButton? previewCancel = previewContainer?.GetNodeOrNull<NBackButton>("Cancel");
        NConfirmButton? previewConfirm = previewContainer?.GetNodeOrNull<NConfirmButton>("Confirm");
        NBackButton? close = screen.GetNodeOrNull<NBackButton>("%Close");
        NTickbox? upgrades = screen.GetNodeOrNull<NTickbox>("%Upgrades");
        if (grid == null
            || previewButton == null
            || previewContainer == null
            || previewCancel == null
            || previewConfirm == null
            || close == null
            || upgrades == null)
        {
            return BindingUnavailable(
                game,
                context,
                "One or more exact transform selector controls are unavailable.",
                new[] { "stage_controls", "upgrade_view_control", "legal_actions" });
        }

        string? prompt = ReadNodeText(screen, "%BottomLabel");
        if (string.IsNullOrWhiteSpace(prompt))
        {
            return BindingUnavailable(
                game,
                context,
                "The player-visible transform prompt is unavailable.",
                new[] { "prompt", "legal_actions" });
        }

        string stage = ConnectorMod.IsNodeVisible(previewContainer) ? "preview" : "selecting";
        NGridCardHolder[] holders = ConnectorMod.FindAllSortedByPosition<NGridCardHolder>(screen)
            .Where(holder => ConnectorMod.IsNodeVisible(holder) && holder.CardModel != null)
            .ToArray();
        HashSet<CardModel> selected = selectedCards.ToHashSet();
        var cardIds = new Dictionary<CardModel, string>();
        var cards = new List<VisibleCard>(holders.Length);
        foreach (NGridCardHolder holder in holders)
        {
            CardModel original = holder.CardModel;
            string id = entities.GetId(original, "card");
            cardIds[original] = id;
            CardModel? displayed = holder.IsShowingUpgradedCard
                ? holder.CardNode?.Model
                : original;
            if (displayed == null)
            {
                return BindingUnavailable(
                    game,
                    context,
                    "A visible transform card's exact displayed model is unavailable.",
                    new[] { "visible_cards", "legal_actions" });
            }
            cards.Add(LiveContextReader.BuildCard(
                displayed,
                id,
                selected.Contains(original),
                displayPile: PileType.Deck));
        }
        string[] selectedIds = selected
            .Select(card => cardIds.TryGetValue(card, out string? id) ? id : null)
            .Where(id => id != null)
            .Cast<string>()
            .OrderBy(id => id, StringComparer.Ordinal)
            .ToArray();
        if (selectedIds.Length != selected.Count
            || holders.Any(holder => !ReferenceEquals(holder.CardModel.Owner, player)))
        {
            return BindingUnavailable(
                game,
                context,
                "The transform selection is not an exact visible subset of the local player's deck.",
                new[] { "selected_cards", "transform_owner", "legal_actions" });
        }

        bool upgradeToggleVisible = ConnectorMod.IsNodeVisible(upgrades);
        string[] selectableIds = stage == "selecting"
            ? holders.Where(holder => IsHolderClickable(holder)
                                      && !selected.Contains(holder.CardModel)
                                      && selected.Count < prefs.MaxSelect)
                .Select(holder => cardIds[holder.CardModel])
                .OrderBy(id => id, StringComparer.Ordinal)
                .ToArray()
            : Array.Empty<string>();
        string[] deselectableIds = stage == "selecting"
            ? holders.Where(holder => IsHolderClickable(holder)
                                      && selected.Contains(holder.CardModel))
                .Select(holder => cardIds[holder.CardModel])
                .OrderBy(id => id, StringComparer.Ordinal)
                .ToArray()
            : Array.Empty<string>();
        bool canPreview = stage == "selecting"
                          && previewButton.IsEnabled
                          && ConnectorMod.IsNodeVisible(previewButton);
        bool canCancelSelection = stage == "selecting"
                                  && prefs.Cancelable
                                  && close.IsEnabled
                                  && ConnectorMod.IsNodeVisible(close);
        bool canToggleUpgradeView = stage == "selecting"
                                    && upgradeToggleVisible
                                    && upgrades.IsEnabled;
        bool canCancelPreview = stage == "preview"
                                && previewCancel.IsEnabled
                                && ConnectorMod.IsNodeVisible(previewCancel);
        bool canConfirm = stage == "preview"
                          && NativeSelectorControl.Available(previewConfirm,
                              control => control.IsEnabled && ConnectorMod.IsNodeVisible(control));
        var surface = new DeckTransformSelectionSurface(
            SurfaceKind,
            stage,
            entities.GetId(screen, "screen"),
            prompt,
            prefs.MinSelect,
            prefs.MaxSelect,
            selected.Count,
            selectedIds,
            prefs.Cancelable,
            upgradeToggleVisible,
            grid.IsShowingUpgrades,
            stage == "preview" ? "random_uncommitted_cycle" : "none",
            false,
            cards)
        {
            SelectableCardEntityIds = selectableIds,
            DeselectableCardEntityIds = deselectableIds,
            CanPreview = canPreview,
            CanCancelSelection = canCancelSelection,
            CanCancelPreview = canCancelPreview,
            CanConfirm = canConfirm,
            CanToggleUpgradeView = canToggleUpgradeView
        };

        bool hasCurrentCommand = selectableIds.Length > 0
                                 || deselectableIds.Length > 0
                                 || canPreview
                                 || canCancelSelection
                                 || canCancelPreview
                                 || canConfirm
                                 || canToggleUpgradeView;
        string readiness = hasCurrentCommand ? "ready" : "settling";
        var completeness = new StateCompleteness(
            "complete_current_random_transform_selector",
            hasCurrentCommand
                ? "derived_from_same_current_transform_controls_as_execution"
                : "temporarily_empty_while_transform_ui_settles",
            new[]
            {
                "NDeckTransformSelectScreen exact controls and bounded selection fields",
                "NCardGrid visible holders and current upgrade-preview mode",
                "NTransformPreview random uncommitted cycle presentation"
            },
            Array.Empty<string>());
        string signature = StableIdentityHash.Object(new
        {
            game.Version,
            context,
            surface
        });
        return new LiveObservation(
            signature,
            readiness,
            context,
            surface,
            completeness,
            game,
            new[]
            {
                "The preview cycles possible cards for player presentation only; it does not reveal or predict the committed random replacement."
            });
    }

    private static NativeInputResult StartToggle(
        NDeckTransformSelectScreen expectedScreen,
        NGridCardHolder expectedHolder,
        CardModel expectedCard,
        bool wasSelected)
    {
        if (!IsCurrent(expectedScreen)
            || IsPreviewVisible(expectedScreen)
            || !ConnectorMod.FindAll<NGridCardHolder>(expectedScreen).Any(holder => ReferenceEquals(holder, expectedHolder))
            || !ReferenceEquals(expectedHolder.CardModel, expectedCard)
            || !ConnectorMod.IsNodeVisible(expectedHolder)
            || !IsHolderClickable(expectedHolder)
            || !expectedCard.IsTransformable
            || BoundedCardSelectionFacts.IsSelected(expectedScreen, expectedCard) != wasSelected)
        {
            return NativeInputResult.Rejected(
                "deck_transform_card_changed",
                "The advertised card is no longer an exact selectable transform candidate.");
        }

        NCardGrid? grid = ConnectorMod.FindFirst<NCardGrid>(expectedScreen);
        if (grid == null)
            return NativeInputResult.Rejected("transform_grid_changed", "The exact transform grid is unavailable.");
        grid.EmitSignal(NCardGrid.SignalName.HolderPressed, expectedHolder);
        return NativeInputResult.Delivered("native_transform_card_holder_pressed");
    }

    private static NativeInputResult StartPreview(
        NDeckTransformSelectScreen expectedScreen,
        NConfirmButton expectedButton)
    {
        if (!IsCurrent(expectedScreen)
            || IsPreviewVisible(expectedScreen)
            || !expectedButton.IsEnabled
            || !ConnectorMod.IsNodeVisible(expectedButton))
        {
            return NativeInputResult.Rejected("transform_preview_changed", "The transform preview is no longer available.");
        }
        expectedButton.ForceClick();
        return NativeInputResult.Delivered("native_transform_preview_button_clicked");
    }

    private static NativeInputResult StartPreviewCancel(
        NDeckTransformSelectScreen expectedScreen,
        NBackButton expectedButton)
    {
        if (!IsCurrent(expectedScreen)
            || !IsPreviewVisible(expectedScreen)
            || !expectedButton.IsEnabled
            || !ConnectorMod.IsNodeVisible(expectedButton))
        {
            return NativeInputResult.Rejected("transform_preview_changed", "The random transform preview can no longer be cancelled.");
        }
        expectedButton.ForceClick();
        return NativeInputResult.Delivered("native_transform_preview_cancel_clicked");
    }

    private static NativeInputResult StartConfirm(
        NDeckTransformSelectScreen expectedScreen,
        NConfirmButton expectedButton,
        IReadOnlyCollection<CardModel> selectedCards,
        Player player)
    {
        IReadOnlyList<CardModel> currentSelection = BoundedCardSelectionFacts.ReadSelectedCards(expectedScreen);
        if (!IsCurrent(expectedScreen)
            || !IsPreviewVisible(expectedScreen)
            || !expectedButton.IsEnabled
            || !ConnectorMod.IsNodeVisible(expectedButton)
            || selectedCards.Count == 0
            || currentSelection.Count != selectedCards.Count
            || selectedCards.Any(expected => !currentSelection.Any(current => ReferenceEquals(current, expected)))
            || selectedCards.Any(card => !card.IsTransformable
                                         || !ReferenceEquals(card.Owner, player)
                                         || !player.Deck.Cards.Any(deckCard => ReferenceEquals(deckCard, card))))
        {
            return NativeInputResult.Rejected(
                "deck_transform_commit_changed",
                "The selected cards are no longer an exact commit-ready random transform set.");
        }

        expectedButton.ForceClick();
        return NativeInputResult.Delivered("native_transform_confirm_clicked");
    }

    private static NativeInputResult StartClose(
        NDeckTransformSelectScreen expectedScreen,
        NBackButton expectedButton)
    {
        if (!IsCurrent(expectedScreen)
            || IsPreviewVisible(expectedScreen)
            || !expectedButton.IsEnabled
            || !ConnectorMod.IsNodeVisible(expectedButton))
        {
            return NativeInputResult.Rejected("transform_close_changed", "The transform selector is no longer cancelable.");
        }
        expectedButton.ForceClick();
        return NativeInputResult.Delivered("native_transform_close_clicked");
    }

    private static NativeInputResult StartUpgradeToggle(
        NDeckTransformSelectScreen expectedScreen,
        NTickbox expectedTickbox,
        NCardGrid expectedGrid,
        bool wasShowingUpgrades)
    {
        if (!IsCurrent(expectedScreen)
            || IsPreviewVisible(expectedScreen)
            || !expectedTickbox.IsEnabled
            || !ConnectorMod.IsNodeVisible(expectedTickbox)
            || expectedGrid.IsShowingUpgrades != wasShowingUpgrades)
        {
            return NativeInputResult.Rejected(
                "transform_upgrade_view_changed",
                "The transform upgrade-preview control changed before execution.");
        }
        expectedTickbox.ForceToggleTick();
        return NativeInputResult.Delivered("native_transform_upgrade_toggle_clicked");
    }

    internal static NativeInputResult StartDirectToggle(
        NativeEntityRegistry entities,
        string screenId,
        string cardId,
        bool expectedSelected)
    {
        if (!TryResolveDirect(
                entities,
                screenId,
                out NDeckTransformSelectScreen? screen,
                out _)
            || !entities.TryResolve(cardId, out CardModel? card)
            || card == null)
        {
            return NativeInputResult.Rejected(
                "deck_transform_binding_stale",
                "The exact transform screen, control, or card no longer resolves.");
        }
        NGridCardHolder? holder = ConnectorMod.FindAll<NGridCardHolder>(screen!)
            .FirstOrDefault(candidate => ReferenceEquals(candidate.CardModel, card));
        return holder == null
            ? NativeInputResult.Rejected(
                "deck_transform_card_changed",
                "The exact transform card is no longer in the current grid.")
            : StartToggle(screen!, holder, card, expectedSelected);
    }

    internal static NativeInputResult StartDirectPreview(
        NativeEntityRegistry entities,
        string screenId)
    {
        if (!TryResolveDirect(entities, screenId, out NDeckTransformSelectScreen? screen, out _)
            || screen!.GetNodeOrNull<NConfirmButton>("Confirm") is not { } button)
        {
            return NativeInputResult.Rejected("deck_transform_binding_stale", "The exact transform preview control no longer resolves.");
        }
        return StartPreview(screen, button);
    }

    internal static NativeInputResult StartDirectCancelSelection(
        NativeEntityRegistry entities,
        string screenId)
    {
        if (!TryResolveDirect(entities, screenId, out NDeckTransformSelectScreen? screen, out _)
            || screen!.GetNodeOrNull<NBackButton>("%Close") is not { } close)
        {
            return NativeInputResult.Rejected("deck_transform_binding_stale", "The exact transform close control no longer resolves.");
        }
        return StartClose(screen, close);
    }

    internal static NativeInputResult StartDirectCancelPreview(
        NativeEntityRegistry entities,
        string screenId)
    {
        if (!TryResolveDirect(entities, screenId, out NDeckTransformSelectScreen? screen, out _)
            || screen!.GetNodeOrNull<Control>("%PreviewContainer")?.GetNodeOrNull<NBackButton>("Cancel") is not { } cancel)
        {
            return NativeInputResult.Rejected("deck_transform_binding_stale", "The exact transform preview cancel control no longer resolves.");
        }
        return StartPreviewCancel(screen, cancel);
    }

    internal static NativeInputResult StartDirectConfirm(
        NativeEntityRegistry entities,
        string screenId,
        IReadOnlyList<string> selectedCardIds)
    {
        if (!TryResolveDirect(entities, screenId, out NDeckTransformSelectScreen? screen, out Player? player)
            || screen!.GetNodeOrNull<Control>("%PreviewContainer")?.GetNodeOrNull<NConfirmButton>("Confirm") is not { } confirm)
        {
            return NativeInputResult.Rejected("deck_transform_binding_stale", "The exact transform confirmation no longer resolves.");
        }
        var selected = new List<CardModel>(selectedCardIds.Count);
        foreach (string cardId in selectedCardIds)
        {
            if (!entities.TryResolve(cardId, out CardModel? card) || card == null)
                return NativeInputResult.Rejected("deck_transform_selection_changed", "An exact selected transform card no longer resolves.");
            selected.Add(card);
        }
        return StartConfirm(screen, confirm, selected, player!);
    }

    internal static NativeInputResult StartDirectToggleUpgradeView(
        NativeEntityRegistry entities,
        string screenId,
        bool expectedShowingUpgrades)
    {
        if (!TryResolveDirect(entities, screenId, out NDeckTransformSelectScreen? screen, out _)
            || ConnectorMod.FindFirst<NCardGrid>(screen!) is not { } grid
            || screen!.GetNodeOrNull<NTickbox>("%Upgrades") is not { } upgrades)
        {
            return NativeInputResult.Rejected("deck_transform_binding_stale", "The exact transform upgrade-view control no longer resolves.");
        }
        return StartUpgradeToggle(screen, upgrades, grid, expectedShowingUpgrades);
    }

    private static bool TryResolveDirect(
        NativeEntityRegistry entities,
        string screenId,
        out NDeckTransformSelectScreen? screen,
        out Player? player)
    {
        screen = null;
        player = null;
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        player = runState == null ? null : LocalContext.GetMe(runState);
        return entities.TryResolve(screenId, out screen)
               && screen != null
               && player != null
               && IsCurrent(screen);
    }

    private static bool IsHolderClickable(NCardHolder holder) =>
        ClickableField?.GetValue(holder) is true;

    private static bool IsPreviewVisible(NDeckTransformSelectScreen screen) =>
        screen.GetNodeOrNull<Control>("%PreviewContainer") is { } preview
        && ConnectorMod.IsNodeVisible(preview);

    private static bool IsCurrent(NDeckTransformSelectScreen screen) =>
        ActiveInputResolver.IsVisibleActiveOverlay(screen)
        && ReferenceEquals(NOverlayStack.Instance?.Peek(), screen);

    private static string? ReadNodeText(Node root, string path)
    {
        try
        {
            Node? node = root.GetNodeOrNull(path);
            if (node == null)
                return null;
            Variant text = node.Get("text");
            return text.VariantType == Variant.Type.Nil
                ? null
                : ConnectorMod.StripRichTextTags(text.AsString()).Replace("\n", " ");
        }
        catch
        {
            return null;
        }
    }

    private static LiveObservation BindingUnavailable(
        GameBuildIdentity game,
        ILiveContext context,
        string reason,
        IReadOnlyList<string> missing)
    {
        return NativeUiFailClosedObservation.BindingUnavailable(
            game,
            context,
            nameof(NDeckTransformSelectScreen),
            reason,
            new[] { "NDeckTransformSelectScreen exact current UI binding" },
            missing,
            "deck_transform_binding_unavailable",
            "host.surface.deck_transform.binding_unavailable",
            "The current random-transform UI cannot be bound exactly.");
    }
}
