using STS2Connector.NativeUi;
using System;
using System.Collections.Generic;
using System.Linq;
using Godot;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Rewards;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost.Contracts;
using STS2Platform.NativeFoundation;

namespace STS2Connector.LiveHost;

/// <summary>
/// Owns the outer rewards screen only. A card reward claim completes when the
/// UI changes to its separate card-selection surface; it is never flattened
/// into a generic reward index.
/// </summary>
internal sealed class RewardClaimSurfaceReader : ILiveSurfaceReader
{
    private const string SurfaceKind = "reward_claim";

    public string Kind => SurfaceKind;

    public InputOwnerLayer Layer => InputOwnerLayer.Overlay;

    public LiveObservation? TryBuild(
        ActiveSurfaceSnapshot snapshot,
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        if (snapshot.TopOverlay is not NRewardsScreen screen)
            return null;
        return Build(screen, entities, game);
    }

    private static LiveObservation Build(
        NRewardsScreen screen,
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        NativeRewardDecision nativeDecision =
            NativeRewardDecisionProvider.Capture(screen, entities);
        if (nativeDecision.Status != "captured")
        {
            return BindingUnavailable(
                game,
                nativeDecision.Detail
                ?? "The exact game-owned RewardsSet is unavailable.",
                new[] { "native_reward_owner", "legal_actions" });
        }

        NRewardButton[] buttons = ConnectorMod.FindAll<NRewardButton>(screen)
            .Where(button => ConnectorMod.IsNodeVisible(button) && button.Reward != null)
            .OrderBy(button => button.Position.Y)
            .ThenBy(button => button.Position.X)
            .ToArray();
        NLinkedRewardSet[] linkedSets = ConnectorMod.FindAll<NLinkedRewardSet>(screen)
            .Where(ConnectorMod.IsNodeVisible)
            .ToArray();
        NProceedButton? proceedButton = ConnectorMod.FindFirst<NProceedButton>(screen);
        Player? player = RunManager.Instance.DebugOnlyGetState() is { } runState
            ? LocalContext.GetMe(runState)
            : null;

        // Linked reward sets have their own UI protocol. Never omit them and
        // claim that the remaining ordinary buttons form a complete surface.
        if (linkedSets.Length > 0 || proceedButton == null || player == null)
        {
            return BindingUnavailable(
                game,
                linkedSets.Length > 0
                    ? "A visible linked reward set needs its own selection contract."
                    : proceedButton == null
                        ? "The visible rewards screen has no exact proceed-button binding."
                        : "The local player is unavailable while rewards are visible.",
                linkedSets.Length > 0
                    ? new[] { "surface.linked_reward_set", "legal_actions" }
                    : proceedButton == null
                        ? new[] { "surface.proceed_button", "legal_actions" }
                        : new[] { "local_player", "legal_actions" });
        }

        Player exactPlayer = player;
        IReadOnlyList<(int Slot, PotionModel Potion)> occupiedPotions = OccupiedPotions(exactPlayer);
        bool potionSlotsFull = ArePotionSlotsFull(exactPlayer, occupiedPotions.Count);
        IReadOnlyList<Reward> ownedRewards = nativeDecision.Rewards;
        bool catalogBoundExactly = NativeDecisionProjection.HasExactReferenceBijection(
            ownedRewards,
            buttons.Select(button => button.Reward!));
        var claimableRewards = new HashSet<Reward>(
            NativeSemanticActionCatalog.Subjects<Reward>(nativeDecision.Actions, "claim"),
            ReferenceEqualityComparer.Instance);
        var discardablePotionSet = new HashSet<PotionModel>(
            NativeSemanticActionCatalog.Subjects<PotionModel>(nativeDecision.Actions, "discard"),
            ReferenceEqualityComparer.Instance);
        VisibleReward[] rewards = buttons.Select(button =>
            BuildReward(
                button,
                entities,
                catalogBoundExactly
                && claimableRewards.Contains(button.Reward!))).ToArray();
        VisibleCombatPotion[] discardablePotions = catalogBoundExactly
            ? occupiedPotions
                .Where(entry => discardablePotionSet.Contains(entry.Potion))
                .Select(entry => BuildDiscardablePotion(entry.Slot, entry.Potion, entities))
                .ToArray()
            : Array.Empty<VisibleCombatPotion>();
        bool canProceed = catalogBoundExactly
                          && proceedButton.IsEnabled
                          && NativeSemanticActionCatalog.ContainsExactlyOnce(
                              nativeDecision.Actions,
                              "proceed");
        bool hasCurrentCommand = rewards.Any(reward => reward.Enabled)
                                 || discardablePotions.Length > 0
                                    && exactPlayer.CanUseOrRemovePotions
                                 || canProceed;
        string readiness = !catalogBoundExactly
            ? "settling"
            : hasCurrentCommand ? "ready" : "settling";
        // Empty rewards and a disabled bound Proceed are observable state,
        // including between submission and execution of an act-ready vote.
        // Delivery readiness must not decide semantic state completeness.
        var surface = new RewardClaimSurface(
            SurfaceKind,
            entities.GetId(screen, "screen"),
            rewards,
            potionSlotsFull,
            discardablePotions,
            canProceed,
            proceedButton.IsSkip)
        {
            CanLeaveWithUnclaimedRewards = canProceed
                && NativeRewardDecisionProvider.ObserveOwner(screen)?.RewardsSet.DisallowSkipping == false
        };
        StateCompleteness completeness = DescribeCompleteness(catalogBoundExactly, hasCurrentCommand);
        string signature = StableIdentityHash.Object(new
        {
            game.Version,
            surface
        });
        return new LiveObservation(
            signature,
            readiness,
            new RewardFlowLiveContext("reward_flow", "room_rewards"),
            surface,
            completeness,
            game,
            Array.Empty<string>());
    }

    internal static StateCompleteness DescribeCompleteness(
        bool catalogBoundExactly,
        bool hasCurrentCommand)
    {
        return new StateCompleteness(
            catalogBoundExactly
                ? "contract_complete_for_reward_claim"
                : "partial",
            !catalogBoundExactly
                ? "native_reward_catalog_waiting_for_exact_presentation_binding"
                : hasCurrentCommand
                ? "native_reward_catalog_intersected_with_current_delivery_controls"
                : "temporarily_empty_while_ui_settles",
            new[]
            {
                "NRewardsScreen.ShowScreen exact RewardsSet owner",
                "RewardsSet.Rewards+Reward.SuccessfullySelected",
                "NRewardButton.Reward presentation binding",
                "NRewardButton.Reward.Description",
                "Player.PotionSlots+CanUseOrRemovePotions",
                "NPotionPopup.OnDiscardButtonPressed+DiscardPotionGameAction",
                "RewardsSet.DisallowSkipping+Hook.ShouldProceedToNextMapPoint",
                "NRewardsScreen.ProceedButton delivery binding"
            },
            catalogBoundExactly ? Array.Empty<string>() : new[] { "native_reward_presentation_bijection" });
    }

    private static VisibleReward BuildReward(
        NRewardButton button,
        NativeEntityRegistry entities,
        bool claimable)
    {
        Reward reward = button.Reward!;
        string label = ConnectorMod.SafeGetText(() => reward.Description) ?? reward.GetType().Name;
        string description = reward switch
        {
            RelicReward { Relic: { } relic } =>
                ConnectorMod.SafeGetText(() => relic.DynamicDescription) ?? label,
            PotionReward { Potion: { } potion } =>
                ConnectorMod.SafeGetText(() => potion.DynamicDescription) ?? label,
            _ => label
        };
        string rewardId = entities.GetId(reward, "reward");
        IReadOnlyList<VisibleCard>? cardOptions = reward is CardReward { IsPopulated: true } cardReward
            ? cardReward.Cards.Select(card => LiveContextReader.BuildCard(card, entities.GetId(card, "card"))).ToArray()
            : null;
        NativeCardRewardAlternativePreviewResult? preview = reward is CardReward currentCardReward
            ? NativeCardRewardAlternativePreview.Capture(currentCardReward) : null;
        return new VisibleReward(
            rewardId,
            RewardKind(reward),
            label,
            description,
            button.IsEnabled && claimable)
        {
            GroupId = rewardId,
            CardOptions = cardOptions,
            CardAlternatives = preview?.Alternatives?.Select(option =>
                new VisibleRewardCardAlternative(option.OptionId, option.Title, option.AfterSelected)).ToArray(),
            CardAlternativesComplete = preview?.Complete == true,
            CanSkipSelection = preview?.CanSkipSelection,
            CanReroll = preview?.CanReroll,
            SelectionCompletesReward = preview?.SelectionCompletesReward
        };
    }

    private static VisibleCombatPotion BuildDiscardablePotion(
        int slot,
        PotionModel potion,
        NativeEntityRegistry entities) =>
        new(
            entities.GetId(potion, "potion"),
            potion.Id.Entry,
            ConnectorMod.SafeGetText(() => potion.Title),
            ConnectorMod.SafeGetText(() => potion.DynamicDescription),
            slot,
            potion.TargetType.ToString(),
            CanUse: false,
            Automatic: potion.Usage == PotionUsage.Automatic);

    private static IReadOnlyList<(int Slot, PotionModel Potion)> OccupiedPotions(Player player)
    {
        var result = new List<(int, PotionModel)>();
        for (int slot = 0; slot < player.PotionSlots.Count; slot++)
        {
            PotionModel? potion = player.GetPotionAtSlotIndex(slot);
            if (potion != null)
                result.Add((slot, potion));
        }
        return result;
    }

    private static bool ArePotionSlotsFull(Player player, int? occupiedCount = null) =>
        (occupiedCount ?? OccupiedPotions(player).Count) >= player.PotionSlots.Count;

    internal static NativeInputResult StartClaim(
        NativeEntityRegistry entities,
        string expectedScreenId,
        string expectedRewardId)
    {
        if (!entities.TryResolve(expectedScreenId, out NRewardsScreen? screen)
            || screen == null
            || !entities.TryResolve(expectedRewardId, out Reward? reward)
            || reward == null
            || RunManager.Instance.DebugOnlyGetState() is not { } runState
            || LocalContext.GetMe(runState) is not { } player)
        {
            return NativeInputResult.Rejected(
                "reward_binding_changed",
                "The exact rewards screen, reward, or local player is no longer available.");
        }

        NRewardButton[] matches = ConnectorMod.FindAll<NRewardButton>(screen)
            .Where(button => ReferenceEquals(button.Reward, reward))
            .ToArray();
        return matches.Length != 1
            ? NativeInputResult.Rejected(
                "reward_binding_changed",
                "The native reward no longer has one exact presentation binding.")
            : StartClaim(entities, screen, player, matches[0], reward);
    }

    internal static NativeInputResult StartProceed(
        NativeEntityRegistry entities,
        string expectedScreenId)
    {
        if (!entities.TryResolve(expectedScreenId, out NRewardsScreen? screen)
            || screen == null
            || ConnectorMod.FindFirst<NProceedButton>(screen) is not { } proceed)
        {
            return NativeInputResult.Rejected(
                "reward_proceed_binding_changed",
                "The exact rewards screen or proceed control is no longer available.");
        }

        return StartProceed(entities, screen, proceed);
    }

    internal static NativeInputResult StartDiscardPotion(
        NativeEntityRegistry entities,
        string expectedScreenId,
        string expectedPotionId)
    {
        if (!entities.TryResolve(expectedScreenId, out NRewardsScreen? screen)
            || screen == null
            || !entities.TryResolve(expectedPotionId, out PotionModel? potion)
            || potion?.Owner is not Player player)
        {
            return NativeInputResult.Rejected(
                "potion_capacity_binding_changed",
                "The exact rewards screen, potion, or potion owner is no longer available.");
        }

        int slot = Enumerable.Range(0, player.PotionSlots.Count)
            .FirstOrDefault(
                index => ReferenceEquals(player.GetPotionAtSlotIndex(index), potion),
                -1);
        return slot < 0
            ? NativeInputResult.Rejected(
                "potion_slot_changed",
                "The exact potion is no longer in the player's belt.")
            : StartDiscardPotion(entities, screen, player, potion, slot);
    }

    private static NativeInputResult StartClaim(
        NativeEntityRegistry entities,
        NRewardsScreen expectedScreen,
        Player expectedPlayer,
        NRewardButton expectedButton,
        Reward expectedReward)
    {
        NativeRewardDecision decision =
            NativeRewardDecisionProvider.Capture(expectedScreen, entities);
        if (!IsCurrent(expectedScreen)
            || !NativeSemanticActionCatalog.ContainsExactlyOnce(
                decision.Actions,
                "claim",
                expectedReward)
            || !ConnectorMod.FindAll<NRewardButton>(expectedScreen).Any(button => ReferenceEquals(button, expectedButton))
            || !ReferenceEquals(expectedButton.Reward, expectedReward)
            || !ConnectorMod.IsNodeVisible(expectedButton)
            || !expectedButton.IsEnabled)
        {
            return NativeInputResult.Rejected(
                "reward_claim_changed",
                "The advertised reward is no longer claimable.");
        }

        expectedButton.ForceClick();
        return NativeInputResult.Delivered("native_reward_button_clicked");
    }

    private static NativeInputResult StartProceed(
        NativeEntityRegistry entities,
        NRewardsScreen expectedScreen,
        NProceedButton expectedButton)
    {
        NativeRewardDecision decision =
            NativeRewardDecisionProvider.Capture(expectedScreen, entities);
        if (!IsCurrent(expectedScreen)
            || !NativeSemanticActionCatalog.ContainsExactlyOnce(
                decision.Actions,
                "proceed")
            || ConnectorMod.FindFirst<NProceedButton>(expectedScreen) is not { } currentButton
            || !ReferenceEquals(currentButton, expectedButton)
            || !ConnectorMod.IsNodeVisible(expectedButton)
            || !expectedButton.IsEnabled)
        {
            return NativeInputResult.Rejected(
                "reward_proceed_changed",
                "The advertised rewards proceed control is no longer enabled.");
        }

        expectedButton.ForceClick();
        return NativeInputResult.Delivered("native_rewards_proceed_button_clicked");
    }

    private static NativeInputResult StartDiscardPotion(
        NativeEntityRegistry entities,
        NRewardsScreen expectedScreen,
        Player expectedPlayer,
        PotionModel expectedPotion,
        int expectedSlot)
    {
        NativeRewardDecision decision =
            NativeRewardDecisionProvider.Capture(expectedScreen, entities);
        if (!IsCurrent(expectedScreen)
            || !NativeSemanticActionCatalog.ContainsExactlyOnce(
                decision.Actions,
                "discard",
                expectedPotion)
            || !expectedPlayer.CanUseOrRemovePotions
            || !ArePotionSlotsFull(expectedPlayer)
            || !ReferenceEquals(expectedPlayer.GetPotionAtSlotIndex(expectedSlot), expectedPotion)
            || !ConnectorMod.FindAll<NRewardButton>(expectedScreen).Any(button =>
                ConnectorMod.IsNodeVisible(button) && button.Reward is PotionReward))
        {
            return NativeInputResult.Rejected(
                "potion_capacity_changed",
                "The advertised potion slot or full-potion reward state changed before execution.");
        }

        var action = new DiscardPotionGameAction(
            expectedPlayer,
            (uint)expectedSlot,
            CombatManager.Instance.IsInProgress);
        RunManager.Instance.ActionQueueSynchronizer.RequestEnqueue(action);
        return NativeInputResult.Delivered("native_discard_potion_action_enqueued");
    }

    private static bool IsCurrent(NRewardsScreen screen) =>
        ActiveInputResolver.IsVisibleActiveOverlay(screen)
        && ReferenceEquals(NOverlayStack.Instance?.Peek(), screen);

    private static string RewardKind(Reward reward) => reward switch
    {
        GoldReward => "gold",
        PotionReward => "potion",
        RelicReward => "relic",
        CardReward => "card",
        _ => "other_visible_reward"
    };

    private static LiveObservation BindingUnavailable(
        GameBuildIdentity game,
        string reason,
        IReadOnlyList<string> missing)
        => NativeUiFailClosedObservation.BindingUnavailable(
            game,
            new RewardFlowLiveContext("reward_flow", "room_rewards"),
            nameof(NRewardsScreen),
            reason,
            new[] { "NRewardsScreen exact-version binding" },
            missing,
            "reward_claim_binding_unavailable",
            "host.surface.reward_claim.binding_unavailable",
            "The current visible reward entries or controls are not exact.");
}
