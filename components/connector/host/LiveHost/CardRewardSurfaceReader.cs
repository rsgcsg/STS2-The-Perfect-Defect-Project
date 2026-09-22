using STS2Connector.NativeUi;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Godot;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using STS2Connector.LiveHost.Contracts;
using STS2Platform.NativeFoundation;

namespace STS2Connector.LiveHost;

internal sealed class CardRewardSurfaceReader : ILiveSurfaceReader
{
    private const string SurfaceKind = "card_reward_selection";
    internal const string SelectCardDeliveryEvidence = "native_card_reward_holder_pressed";
    internal const string AlternativeDeliveryEvidence = "native_card_reward_alternative_clicked";
    private const BindingFlags Flags = BindingFlags.Instance | BindingFlags.NonPublic;
    private static readonly FieldInfo? ClickableField =
        typeof(NCardHolder).GetField("_isClickable", Flags);

    public string Kind => SurfaceKind;

    public InputOwnerLayer Layer => InputOwnerLayer.Overlay;

    public LiveObservation? TryBuild(
        ActiveSurfaceSnapshot snapshot,
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        if (snapshot.TopOverlay is not NCardRewardSelectionScreen screen)
            return null;
        return Build(screen, entities, game);
    }

    private static LiveObservation Build(
        NCardRewardSelectionScreen screen,
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        NativeCardRewardDecision nativeDecision =
            NativeCardRewardDecisionProvider.Capture(screen, entities);
        if (nativeDecision.Status != "captured")
        {
            return BindingUnavailable(
                game,
                nativeDecision.Detail
                ?? "The exact native card reward option owner is unavailable.",
                new[] { "native_card_reward_owner", "legal_actions" });
        }

        Control? cardRow = screen.GetNodeOrNull<Control>("UI/CardRow");
        Control? alternativesContainer = screen.GetNodeOrNull<Control>("UI/RewardAlternatives");
        if (cardRow == null || alternativesContainer == null || ClickableField == null)
            return BindingUnavailable(
                game,
                "The exact card reward UI binding is unavailable.",
                new[] { "card_row", "reward_alternatives", "card_selectability", "legal_actions" });

        NGridCardHolder[] holders = VisibleCardHolders(cardRow);
        NCardRewardAlternativeButton[] currentButtons = AlternativeButtons(alternativesContainer);
        NCardRewardAlternativeButton[] visibleButtons = currentButtons
            .Where(ConnectorMod.IsNodeVisible).ToArray();
        IReadOnlyList<CardModel> semanticCards =
            NativeSemanticActionCatalog.Subjects<CardModel>(nativeDecision.Actions, "select");
        IReadOnlyList<CardRewardAlternative> semanticAlternatives =
            NativeSemanticActionCatalog.Subjects<CardRewardAlternative>(
                nativeDecision.Actions,
                "activate");
        var semanticCardSet = new HashSet<CardModel>(
            semanticCards,
            ReferenceEqualityComparer.Instance);
        bool alternativesBoundExactly =
            CardRewardAlternativePresentationBindings.TryCapture(
                screen, semanticAlternatives, currentButtons, out var exactAlternatives)
            && AllCurrentAlternativeButtons(exactAlternatives);
        bool catalogBoundExactly = NativeDecisionProjection.HasExactReferenceBijection(
                                       semanticCards,
                                       holders.Select(holder => holder.CardModel))
                                   && alternativesBoundExactly;
        NCardRewardAlternativeButton[] buttons = alternativesBoundExactly
            ? exactAlternatives.Select(pair => (NCardRewardAlternativeButton)pair.Button).ToArray()
            : visibleButtons;
        string?[] alternativeLabels = buttons.Select(ReadAlternativeLabel).ToArray();
        if (alternativeLabels.Any(string.IsNullOrWhiteSpace))
        {
            return BindingUnavailable(
                game,
                "A visible card reward alternative has no readable player-facing label.",
                new[] { "reward_alternatives.visible_label", "legal_actions" });
        }

        VisibleCard[] cards = holders.Select(holder =>
            LiveContextReader.BuildCard(
                holder.CardModel,
                entities.GetId(holder.CardModel, "card")))
            .ToArray();
        VisibleCardRewardAlternative[] alternatives = buttons
            .Select((button, index) => new VisibleCardRewardAlternative(
                catalogBoundExactly
                    ? entities.GetId(
                        exactAlternatives[index].Alternative,
                        "card_reward_alternative")
                    : entities.GetId(
                        button,
                        "card_reward_alternative_binding"),
                index,
                alternativeLabels[index]!,
                catalogBoundExactly && button.IsEnabled && !button.IsQueuedForDeletion()))
            .ToArray();

        var surface = new CardRewardSelectionSurface(
            SurfaceKind,
            entities.GetId(screen, "screen"),
            cards,
            alternatives)
        {
            SelectableCardEntityIds = holders
                .Where(holder => catalogBoundExactly
                                 && IsHolderClickable(holder)
                                 && semanticCardSet.Contains(holder.CardModel))
                .Select(holder => entities.GetId(holder.CardModel, "card"))
                .ToArray()
        };
        bool hasVisibleOptions = cards.Length > 0 || alternatives.Length > 0;
        bool hasActionableOption = surface.SelectableCardEntityIds.Count > 0
                                   || alternatives.Any(option => option.Enabled);
        bool visibleCardsStillMounting = cards.Length > 0
                                         && surface.SelectableCardEntityIds.Count == 0;
        string readiness = !catalogBoundExactly
            ? "settling"
            : ClassifyReadiness(
                cards.Length,
                surface.SelectableCardEntityIds.Count,
                alternatives.Length,
                alternatives.Count(option => option.Enabled));
        var missing = hasVisibleOptions
            ? Array.Empty<string>()
            : new[] { "surface.cards_or_alternatives" };
        var completeness = new StateCompleteness(
            hasVisibleOptions && catalogBoundExactly
                ? "contract_complete_for_card_reward_selection"
                : "partial",
            !catalogBoundExactly
                ? "native_option_catalog_waiting_for_exact_presentation_binding"
                : visibleCardsStillMounting
                ? "visible_cards_waiting_for_current_clickability"
                : hasActionableOption
                ? "native_option_catalog_intersected_with_current_delivery_controls"
                : "temporarily_empty_while_ui_settles",
            new[]
            {
                "NCardRewardSelectionScreen.ShowScreen/RefreshOptions native option owner",
                "CardCreationResult.Card+CardRewardAlternative native membership",
                "NCardRewardSelectionScreen.RefreshOptions/Create exact alternative-button identity",
                "NCardRewardSelectionScreen.UI.CardRow presentation binding",
                "NGridCardHolder.CardModel presentation binding",
                "NCardRewardSelectionScreen.UI.RewardAlternatives presentation binding",
                "NCardRewardAlternativeButton.visible_label",
                "NCardHolder._isClickable exact-version delivery binding"
            },
            catalogBoundExactly ? missing : new[] { "native_card_reward_presentation_bijection" });
        string signature = StableIdentityHash.Object(new
        {
            game.Version,
            surface
        });

        return new LiveObservation(
            signature,
            readiness,
            new RewardFlowLiveContext("reward_flow", "card_reward"),
            surface,
            completeness,
            game,
            Array.Empty<string>());
    }

    internal static string ClassifyReadiness(
        int visibleCards,
        int selectableCards,
        int visibleAlternatives,
        int enabledAlternatives)
    {
        if (visibleCards == 0 && visibleAlternatives == 0)
            return "degraded";
        // Card holders become visible before their mount animation enables
        // input. An enabled Skip button cannot make that partial catalog stable.
        if (visibleCards > 0 && selectableCards == 0)
            return "settling";
        return selectableCards > 0 || enabledAlternatives > 0
            ? "ready"
            : "settling";
    }

    private static LiveObservation BindingUnavailable(
        GameBuildIdentity game,
        string reason,
        IReadOnlyList<string> missing)
    {
        var unavailable = new UnsupportedSurface(
            SurfaceKind,
            nameof(NCardRewardSelectionScreen),
            reason);
        var completeness = new StateCompleteness(
            "degraded",
            "empty_fail_closed",
            new[] { "NCardRewardSelectionScreen exact-version binding" },
            missing);
        string signature = StableIdentityHash.Object(new { game.Version, unavailable, missing });
        return new LiveObservation(
            signature,
            "degraded",
            new RewardFlowLiveContext("reward_flow", "card_reward"),
            unavailable,
            completeness,
            game,
            new[] { "card_reward_binding_unavailable" })
        {
            Diagnostics = new[]
            {
                HostDiagnostics.Create(
                    "host.surface.card_reward.binding_unavailable",
                    "error",
                    "surface",
                    "actions_suppressed",
                    "update_host_adapter",
                    reason)
            }
        };
    }

    private static NativeInputResult StartCardSelection(
        NativeEntityRegistry entities,
        NCardRewardSelectionScreen expectedScreen,
        Control expectedCardRow,
        NGridCardHolder expectedHolder,
        CardModel expectedCard)
    {
        NativeCardRewardDecision decision =
            NativeCardRewardDecisionProvider.Capture(expectedScreen, entities);
        if (!IsCurrent(expectedScreen)
            || !NativeSemanticActionCatalog.ContainsExactlyOnce(
                decision.Actions,
                "select",
                expectedCard)
            || expectedScreen.GetNodeOrNull<Control>("UI/CardRow") is not { } currentRow
            || !ReferenceEquals(currentRow, expectedCardRow)
            || !currentRow.GetChildren().OfType<NGridCardHolder>().Any(holder => ReferenceEquals(holder, expectedHolder))
            || !ReferenceEquals(expectedHolder.CardModel, expectedCard)
            || !ConnectorMod.IsNodeVisible(expectedHolder)
            || !IsHolderClickable(expectedHolder))
        {
            return NativeInputResult.Rejected(
                "card_reward_card_changed",
                "The advertised card reward option is no longer selectable.");
        }

        expectedHolder.EmitSignal(NCardHolder.SignalName.Pressed, expectedHolder);
        return NativeInputResult.Delivered(SelectCardDeliveryEvidence);
    }

    internal static NativeInputResult StartCardSelection(
        NativeEntityRegistry entities,
        string expectedScreenId,
        string expectedCardId)
    {
        if (!entities.TryResolve(expectedScreenId, out NCardRewardSelectionScreen? screen)
            || screen == null
            || !entities.TryResolve(expectedCardId, out CardModel? card)
            || card == null
            || screen.GetNodeOrNull<Control>("UI/CardRow") is not { } cardRow)
        {
            return NativeInputResult.Rejected(
                "card_reward_binding_changed",
                "The exact card reward screen, card, or visible containers are no longer available.");
        }

        NGridCardHolder[] holders = VisibleCardHolders(cardRow);
        NGridCardHolder[] matches = holders
            .Where(holder => ReferenceEquals(holder.CardModel, card))
            .ToArray();
        if (matches.Length != 1)
        {
            return NativeInputResult.Rejected(
                "card_reward_card_changed",
                "The exact advertised card no longer has one visible holder.");
        }

        return StartCardSelection(
            entities,
            screen,
            cardRow,
            matches[0],
            card);
    }

    private static NativeInputResult StartAlternative(
        NativeEntityRegistry entities,
        NCardRewardSelectionScreen expectedScreen,
        Control expectedContainer,
        NCardRewardAlternativeButton expectedButton,
        CardRewardAlternative expectedAlternative,
        string expectedLabel)
    {
        NativeCardRewardDecision decision =
            NativeCardRewardDecisionProvider.Capture(expectedScreen, entities);
        CardRewardAlternative[] semanticAlternatives =
            NativeSemanticActionCatalog.Subjects<CardRewardAlternative>(
                decision.Actions, "activate").ToArray();
        if (!IsCurrent(expectedScreen)
            || !NativeSemanticActionCatalog.ContainsExactlyOnce(
                decision.Actions,
                "activate",
                expectedAlternative)
            || expectedScreen.GetNodeOrNull<Control>("UI/RewardAlternatives") is not { } currentContainer
            || !ReferenceEquals(currentContainer, expectedContainer)
            || !CardRewardAlternativePresentationBindings.TryResolveButton(
                expectedScreen, expectedAlternative, semanticAlternatives,
                AlternativeButtons(currentContainer), out var currentButton)
            || !ReferenceEquals(currentButton, expectedButton)
            || !CardRewardAlternativePresentationBindings.TryCapture(
                expectedScreen, semanticAlternatives,
                AlternativeButtons(currentContainer), out var currentPairs)
            || !AllCurrentAlternativeButtons(currentPairs)
            || !GodotObject.IsInstanceValid(expectedButton)
            || expectedButton.IsQueuedForDeletion()
            || !ConnectorMod.IsNodeVisible(expectedButton)
            || !expectedButton.IsEnabled
            || !string.Equals(ReadAlternativeLabel(expectedButton), expectedLabel, StringComparison.Ordinal))
        {
            return NativeInputResult.Rejected(
                "card_reward_alternative_changed",
                "The advertised card reward alternative is no longer enabled.");
        }

        expectedButton.ForceClick();
        return NativeInputResult.Delivered(AlternativeDeliveryEvidence);
    }

    internal static NativeInputResult StartAlternative(
        NativeEntityRegistry entities,
        string expectedScreenId,
        string expectedAlternativeId,
        string expectedLabel)
    {
        if (!entities.TryResolve(expectedScreenId, out NCardRewardSelectionScreen? screen)
            || screen == null
            || !entities.TryResolve(
                expectedAlternativeId,
                out CardRewardAlternative? alternative)
            || alternative == null
            || screen.GetNodeOrNull<Control>("UI/RewardAlternatives") is not { } alternatives)
        {
            return NativeInputResult.Rejected(
                "card_reward_alternative_binding_changed",
                "The exact card reward screen, alternative, or visible containers are no longer available.");
        }

        NativeCardRewardDecision decision =
            NativeCardRewardDecisionProvider.Capture(screen, entities);
        CardRewardAlternative[] semanticAlternatives = decision.Actions
            .Where(action => action.Verb == "activate")
            .Select(action => action.NativeSubject)
            .OfType<CardRewardAlternative>()
            .ToArray();
        if (!CardRewardAlternativePresentationBindings.TryResolveButton(
                screen, alternative, semanticAlternatives,
                AlternativeButtons(alternatives), out var button)
            || button is not NCardRewardAlternativeButton typedButton)
        {
            return NativeInputResult.Rejected(
                "card_reward_alternative_changed",
                "The exact advertised alternative no longer has one native-created control.");
        }

        return StartAlternative(
            entities,
            screen,
            alternatives,
            typedButton,
            alternative,
            expectedLabel);
    }

    private static NGridCardHolder[] VisibleCardHolders(Control cardRow) =>
        cardRow.GetChildren()
            .OfType<NGridCardHolder>()
            .Where(holder => ConnectorMod.IsNodeVisible(holder) && holder.CardModel != null)
            .OrderBy(holder => holder.Position.X)
            .ThenBy(holder => holder.Position.Y)
            .ToArray();

    private static NCardRewardAlternativeButton[] AlternativeButtons(Control container) =>
        container.GetChildren()
            .OfType<NCardRewardAlternativeButton>()
            .ToArray();

    private static bool AllCurrentAlternativeButtons(
        IReadOnlyList<CardRewardAlternativePresentationBindings.Pair> pairs) =>
        pairs.All(pair =>
            pair.Button is NCardRewardAlternativeButton button
            && GodotObject.IsInstanceValid(button)
            && !button.IsQueuedForDeletion()
            && ConnectorMod.IsNodeVisible(button));

    private static bool IsHolderClickable(NCardHolder holder) =>
        ClickableField?.GetValue(holder) is true;

    private static bool IsCurrent(NCardRewardSelectionScreen screen) =>
        ActiveInputResolver.IsVisibleActiveOverlay(screen)
        && ReferenceEquals(NOverlayStack.Instance?.Peek(), screen);

    private static string? ReadAlternativeLabel(NCardRewardAlternativeButton button)
    {
        try
        {
            Node? label = button.GetNodeOrNull("Label");
            if (label == null)
                return null;
            Variant value = label.Get("text");
            return value.VariantType == Variant.Type.Nil
                ? null
                : ConnectorMod.StripRichTextTags(value.AsString()).Replace("\n", " ");
        }
        catch
        {
            return null;
        }
    }
}
