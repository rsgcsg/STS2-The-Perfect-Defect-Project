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
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.NativeUi;

internal sealed record NativeCombatPileSelectionSurface(
    string Kind,
    string Stage,
    string ScreenEntityId,
    string? Prompt,
    string PileType,
    int MinSelect,
    int MaxSelect,
    int SelectedCount,
    IReadOnlyList<string> SelectedCardEntityIds,
    IReadOnlyList<string> SelectableCardEntityIds,
    IReadOnlyList<string> DeselectableCardEntityIds,
    bool Cancelable,
    bool CanCancel,
    bool CanConfirm,
    IReadOnlyList<VisibleCard> Cards) : ILiveSurface;

/// <summary>
/// Source-free native adapter for the visible combat-pile selector. It knows
/// current UI mechanics, not which card/relic/event opened the selector or the
/// eventual game effect.
/// </summary>
internal static class NativeCombatPileSelection
{
    internal const string SurfaceKind = "native_combat_pile_selection";
    internal const string SelectOperation = "select_native_combat_pile_card";
    internal const string DeselectOperation = "deselect_native_combat_pile_card";
    internal const string CancelOperation = "cancel_native_combat_pile_selection";
    internal const string ConfirmOperation = "confirm_native_combat_pile_selection";

    private const BindingFlags Flags = BindingFlags.Instance | BindingFlags.NonPublic;
    private static readonly FieldInfo? ClickableField =
        typeof(NCardHolder).GetField("_isClickable", Flags);

    internal static LiveObservation? TryBuild(
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        ActiveSurfaceSnapshot active = ActiveInputResolver.Capture();
        if (active.TopOverlay is not NCombatPileCardSelectScreen screen)
            return null;

        ILiveContext context = LiveContextReader.Build(entities);
        if (!BoundedCardSelectionFacts.TryRead(
                screen,
                out CardSelectorPrefs preferences,
                out IReadOnlyList<CardModel> selectedCards,
                out string? bindingError)
            || ReadField(screen, "_pile") is not CardPile pile
            || ClickableField == null)
        {
            return BindingUnavailable(
                game,
                context,
                bindingError ?? "The combat-pile selector's current UI binding is unavailable.");
        }

        NGridCardHolder[] holders = ConnectorMod.FindAllSortedByPosition<NGridCardHolder>(screen)
            .Where(holder => ConnectorMod.IsNodeVisible(holder) && holder.CardModel != null)
            .ToArray();
        if (holders.Length == 0)
            return BindingUnavailable(game, context, "The current combat-pile selector has no visible card holders.");

        HashSet<CardModel> selected = selectedCards.ToHashSet();
        var cardIds = new Dictionary<CardModel, string>();
        var cards = new List<VisibleCard>(holders.Length);
        foreach (NGridCardHolder holder in holders)
        {
            CardModel card = holder.CardModel;
            string cardId = entities.GetId(card, "card");
            cardIds[card] = cardId;
            cards.Add(LiveContextReader.BuildCard(
                card,
                cardId,
                selected.Contains(card),
                displayPile: pile.Type));
        }

        string[] selectedIds = selected
            .Select(card => cardIds.TryGetValue(card, out string? id) ? id : null)
            .Where(id => id != null)
            .Cast<string>()
            .OrderBy(id => id, StringComparer.Ordinal)
            .ToArray();
        if (selectedIds.Length != selected.Count)
            return BindingUnavailable(game, context, "A selected card is absent from the visible grid.");

        string[] selectableIds = holders
            .Where(holder => IsHolderClickable(holder)
                             && !selected.Contains(holder.CardModel)
                             && selected.Count < preferences.MaxSelect)
            .Select(holder => cardIds[holder.CardModel])
            .OrderBy(id => id, StringComparer.Ordinal)
            .ToArray();
        string[] deselectableIds = holders
            .Where(holder => IsHolderClickable(holder) && selected.Contains(holder.CardModel))
            .Select(holder => cardIds[holder.CardModel])
            .OrderBy(id => id, StringComparer.Ordinal)
            .ToArray();
        bool canConfirm = NativeSelectorControl.Available(FindConfirm(screen),
            control => control.IsEnabled && ConnectorMod.IsNodeVisible(control));
        bool canCancel = preferences.Cancelable && FindClose(screen) != null;
        string? prompt = ReadNodeText(screen, "%BottomLabel");
        var surface = new NativeCombatPileSelectionSurface(
            SurfaceKind,
            "selecting",
            entities.GetId(screen, "screen"),
            prompt,
            pile.Type.ToString().ToLowerInvariant(),
            preferences.MinSelect,
            preferences.MaxSelect,
            selected.Count,
            selectedIds,
            selectableIds,
            deselectableIds,
            preferences.Cancelable,
            canCancel,
            canConfirm,
            cards);
        bool actionable = DescribeCommands(surface).Count > 0;
        return new LiveObservation(
            StableIdentityHash.Object(new { game.Version, context, surface }),
            actionable ? "ready" : "settling",
            context,
            surface,
            new StateCompleteness(
                string.IsNullOrWhiteSpace(prompt)
                    ? "partial_current_structured_ui"
                    : "complete_current_structured_ui",
                actionable
                    ? "derived_from_current_visible_enabled_controls"
                    : "temporarily_empty_while_native_ui_settles",
                new[]
                {
                    "NCombatPileCardSelectScreen visible overlay and grid",
                    "current native selection preferences and controls"
                },
                string.IsNullOrWhiteSpace(prompt) ? new[] { "visible_prompt_text" } : Array.Empty<string>()),
            Game: game,
            Warnings: new[]
            {
                "The opening source and eventual pile mutation are intentionally not inferred by Player Environment C."
            })
        {
            InputOwnership = new InputOwnership(
                "current_ui_owned",
                SurfaceKind,
                "The exact current native selector owns input; no business source grants authority.")
        };
    }

    internal static IReadOnlyList<NativeUiActionDescriptor> DescribeCommands(
        NativeCombatPileSelectionSurface surface)
    {
        if (surface.Kind != SurfaceKind)
            return Array.Empty<NativeUiActionDescriptor>();

        Dictionary<string, VisibleCard> cards = surface.Cards
            .ToDictionary(card => card.EntityId, StringComparer.Ordinal);
        var commands = new List<NativeUiActionDescriptor>();
        ActionEntityBinding screen = new("screen", surface.ScreenEntityId);
        foreach (string cardId in surface.SelectableCardEntityIds)
        {
            if (cards.TryGetValue(cardId, out VisibleCard? card))
                commands.Add(Descriptor(
                    $"select_native_combat_pile_card:{surface.ScreenEntityId}:{cardId}",
                    SelectOperation,
                    "selection",
                    $"Select {card.Name ?? card.DefinitionId}",
                    new[] { screen, new ActionEntityBinding("card", cardId) }));
        }
        foreach (string cardId in surface.DeselectableCardEntityIds)
        {
            if (cards.TryGetValue(cardId, out VisibleCard? card))
                commands.Add(Descriptor(
                    $"deselect_native_combat_pile_card:{surface.ScreenEntityId}:{cardId}",
                    DeselectOperation,
                    "selection",
                    $"Deselect {card.Name ?? card.DefinitionId}",
                    new[] { screen, new ActionEntityBinding("card", cardId) }));
        }
        if (surface.CanCancel)
            commands.Add(Descriptor(
                $"cancel_native_combat_pile_selection:{surface.ScreenEntityId}",
                CancelOperation,
                "cancel",
                "Cancel card selection",
                new[] { screen }));
        if (surface.CanConfirm)
            commands.Add(Descriptor(
                $"confirm_native_combat_pile_selection:{surface.ScreenEntityId}",
                ConfirmOperation,
                "confirm",
                "Confirm selected cards",
                new[] { screen }));
        return commands;
    }

    internal static NativeInputResult Start(
        NativeEntityRegistry entities,
        NativeCombatPileSelectionSurface surface,
        NativeUiBoundAction binding,
        IReadOnlyDictionary<string, string> parameters)
    {
        string? bindingError = null;
        if (!parameters.TryGetValue("screen_id", out string? screenId)
            || !string.Equals(screenId, surface.ScreenEntityId, StringComparison.Ordinal)
            || !entities.TryResolve(screenId, out NCombatPileCardSelectScreen? screen)
            || screen == null
            || !IsCurrent(screen)
            || !BoundedCardSelectionFacts.TryRead(
                screen,
                out CardSelectorPrefs preferences,
                out IReadOnlyList<CardModel> selectedCards,
                out bindingError))
        {
            return Rejected(bindingError ?? "The exact combat-pile selector is no longer current.");
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
                || (!currentlySelected && selectedCards.Count >= preferences.MaxSelect))
            {
                return Rejected("The card's current selected or selectable state changed.");
            }
            NGridCardHolder[] holders = ConnectorMod.FindAllSortedByPosition<NGridCardHolder>(screen)
                .Where(candidate => ReferenceEquals(candidate.CardModel, card)
                                    && ConnectorMod.IsNodeVisible(candidate)
                                    && IsHolderClickable(candidate))
                .Take(2)
                .ToArray();
            NCardGrid? grid = ConnectorMod.FindFirst<NCardGrid>(screen);
            if (holders.Length != 1 || grid == null)
                return Rejected("The exact current card holder is no longer actionable.");
            grid.EmitSignal(NCardGrid.SignalName.HolderPressed, holders[0]);
            return Delivered("native_combat_pile_holder_pressed");
        }

        if (operation == ConfirmOperation)
            return NativeSelectorControl.Click(FindConfirm(screen), () => IsCurrent(screen),
                control => control.IsEnabled && ConnectorMod.IsNodeVisible(control),
                control => control.ForceClick(), "native_combat_pile_confirm_clicked");
        if (operation == CancelOperation && preferences.Cancelable && FindClose(screen) is { } close)
        {
            close.ForceClick();
            return Delivered("native_combat_pile_cancel_clicked");
        }
        return Rejected("The exact advertised selector control is no longer current.");
    }

    private static NativeUiActionDescriptor Descriptor(
        string key,
        string operation,
        string category,
        string label,
        IReadOnlyList<ActionEntityBinding> bindings) => new(
            key,
            operation,
            category,
            label,
            $"NCombatPileCardSelectScreen current UI input delivery:{operation}",
            bindings);

    private static NativeInputResult Delivered(string evidence) =>
        NativeInputResult.Delivered(evidence);

    private static NativeInputResult Rejected(string detail) =>
        NativeInputResult.Rejected("player_environment_target_not_actionable", detail);

    private static bool IsHolderClickable(NCardHolder holder) =>
        ClickableField?.GetValue(holder) is true;

    private static NConfirmButton? FindConfirm(NCombatPileCardSelectScreen screen)
    {
        NConfirmButton? confirm = screen.GetNodeOrNull<NConfirmButton>("%Confirm");
        return confirm is { IsEnabled: true } && ConnectorMod.IsNodeVisible(confirm) ? confirm : null;
    }

    private static NBackButton? FindClose(NCombatPileCardSelectScreen screen)
    {
        NBackButton? close = screen.GetNodeOrNull<NBackButton>("%Close");
        return close is { IsEnabled: true } && ConnectorMod.IsNodeVisible(close) ? close : null;
    }

    private static object? ReadField(object source, string fieldName)
    {
        const BindingFlags searchFlags = BindingFlags.Instance
                                         | BindingFlags.Public
                                         | BindingFlags.NonPublic
                                         | BindingFlags.DeclaredOnly;
        for (Type? type = source.GetType(); type != null; type = type.BaseType)
        {
            FieldInfo? field = type.GetField(fieldName, searchFlags);
            if (field != null)
                return field.GetValue(source);
        }
        return null;
    }

    private static bool IsCurrent(NCombatPileCardSelectScreen screen) =>
        ActiveInputResolver.IsVisibleActiveOverlay(screen)
        && ReferenceEquals(NOverlayStack.Instance?.Peek(), screen);

    private static string? ReadNodeText(Node screen, string path)
    {
        try
        {
            Node? node = screen.GetNodeOrNull(path);
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

    private static LiveObservation BindingUnavailable(
        GameBuildIdentity game,
        ILiveContext context,
        string detail) =>
        NativeUiFailClosedObservation.BindingUnavailable(
            game,
            context,
            nameof(NCombatPileCardSelectScreen),
            detail,
            new[] { "NCombatPileCardSelectScreen current visible UI mechanics" },
            new[] { "visible_cards", "current_controls" },
            "player_environment_combat_pile_selection_binding_unavailable",
            "native-ui.combat-pile-selection.binding-unavailable",
            "The current UI cannot be represented without guessing a target.");
}
