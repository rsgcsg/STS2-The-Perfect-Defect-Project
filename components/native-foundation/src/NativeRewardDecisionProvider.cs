using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Hooks;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Rewards;

namespace STS2Platform.NativeFoundation;

/// <summary>
/// Tracks the exact RewardsSet passed to the shipped rewards screen and
/// projects its current semantic choices. The registry is weak and read-only;
/// it cannot keep a screen alive or execute a reward.
/// </summary>
public static class NativeRewardDecisionProvider
{
    private sealed record Owner(RewardsSet Set, bool IsTerminal);

    private static readonly ConditionalWeakTable<NRewardsScreen, Owner> Owners = new();

    public static void Register(
        NRewardsScreen screen,
        RewardsSet set,
        bool isTerminal)
    {
        NativeRewardSetObservation.Register(set);
        Owners.Remove(screen);
        Owners.Add(screen, new Owner(set, isTerminal));
    }

    public static NativeRewardDecisionOwner? ObserveOwner(NRewardsScreen screen) =>
        Owners.TryGetValue(screen, out Owner? owner)
            ? new NativeRewardDecisionOwner(owner.Set, owner.IsTerminal)
            : null;

    public static NativeRewardDecision Capture(
        NRewardsScreen screen,
        INativeReferentIdentity identities)
    {
        if (!Owners.TryGetValue(screen, out Owner? owner))
        {
            return Unavailable(
                "owner_not_registered",
                "The exact RewardsSet owner was not observed at ShowScreen.");
        }

        try
        {
            RewardsSet set = owner.Set;
            Player player = set.Player;
            Reward[] rewards = set.Rewards
                .Where(value => !value.SuccessfullySelected)
                .ToArray();
            IReadOnlyList<PotionModel> occupiedPotions = OccupiedPotions(player);
            bool potionSlotsFull = occupiedPotions.Count >= player.PotionSlots.Count;
            var actions = new List<NativeSemanticAction>();
            foreach (Reward reward in rewards)
            {
                // Keep the existing Player Environment contract: when the
                // belt is full, expose exact potion-discard choices rather
                // than a claim delivery that STS2 will reject as TooFull.
                if (reward is PotionReward && potionSlotsFull)
                    continue;
                string id = identities.GetId(reward, "reward");
                actions.Add(new NativeSemanticAction(
                    NativeSemanticActionCatalog.BuildKey("claim", id),
                    "claim",
                    id,
                    reward,
                    Array.Empty<NativeSemanticOperand>(),
                    "RewardsSet.Rewards+Reward.SuccessfullySelected"));
            }

            bool hasPendingPotionReward = rewards.Any(reward => reward is PotionReward);
            if (hasPendingPotionReward
                && potionSlotsFull
                && player.CanUseOrRemovePotions)
            {
                actions.AddRange(NativePotionDiscardDecisionProvider.CaptureSlots(player.PotionSlots, identities));
            }

            bool canProceed = (!set.DisallowSkipping || set.AllRewardsSuccessfullySelected)
                              && Hook.ShouldProceedToNextMapPoint(player.RunState);
            if (canProceed)
            {
                actions.Add(new NativeSemanticAction(
                    NativeSemanticActionCatalog.BuildKey("proceed", null),
                    "proceed",
                    null,
                    set,
                    Array.Empty<NativeSemanticOperand>(),
                    "RewardsSet.DisallowSkipping+AllRewardsSuccessfullySelected+Hook.ShouldProceedToNextMapPoint"));
            }

            return new NativeRewardDecision(
                "captured",
                "room_rewards",
                actions.Count > 0,
                owner.IsTerminal,
                rewards,
                actions.OrderBy(action => action.Key, StringComparer.Ordinal).ToArray(),
                new[]
                {
                    "NRewardsScreen.ShowScreen exact RewardsSet owner",
                    "RewardsSet.Rewards+DisallowSkipping+AllRewardsSuccessfullySelected",
                    "Reward.SuccessfullySelected",
                    "Hook.ShouldProceedToNextMapPoint",
                    "Player.PotionSlots+CanUseOrRemovePotions"
                },
                actions.Count == 0 ? "The active RewardsSet has no semantic action." : null);
        }
        catch (Exception exception)
        {
            return Unavailable(
                "capture_failed",
                $"{exception.GetType().Name}: {exception.Message}");
        }
    }

    private static IReadOnlyList<PotionModel> OccupiedPotions(Player player)
    {
        var result = new List<PotionModel>();
        for (int slot = 0; slot < player.PotionSlots.Count; slot++)
        {
            if (player.GetPotionAtSlotIndex(slot) is { } potion)
                result.Add(potion);
        }
        return result;
    }

    private static NativeRewardDecision Unavailable(string status, string detail) =>
        new(
            status,
            "unavailable",
            false,
            false,
            Array.Empty<Reward>(),
            Array.Empty<NativeSemanticAction>(),
            Array.Empty<string>(),
            detail);
}
