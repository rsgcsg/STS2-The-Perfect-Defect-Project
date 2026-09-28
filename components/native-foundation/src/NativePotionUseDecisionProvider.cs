using System;
using System.Collections.Generic;
using System.Linq;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.GameActions.Multiplayer;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Runs;

namespace STS2Platform.NativeFoundation;

/// <summary>Current native potion operands, independent of the room UI.
/// Queued is an input guard, not execution-time semantic invalidity.</summary>
public static class NativePotionUseDecisionProvider
{
    public static NativeCombatDecision Capture(INativeReferentIdentity identities)
    {
        if (CombatManager.Instance.IsInProgress)
            return NativeCombatDecisionProvider.Capture(identities);
        var run = RunManager.Instance.DebugOnlyGetState();
        var player = run == null ? null : LocalContext.GetMe(run);
        if (player == null)
            return new("player_unavailable", "unavailable", false,
                Array.Empty<NativeSemanticAction>(), Array.Empty<string>(), "No exact local player.");
        return CaptureNonCombat(player, identities);
    }

    internal static NativeCombatDecision CaptureNonCombat(Player player, INativeReferentIdentity identities)
    {
        var actions = new List<NativeSemanticAction>();
        foreach (PotionModel? potion in player.PotionSlots)
        {
            if (potion == null || !AllowsNonCombatUse(potion.Usage,
                    player.CanUseOrRemovePotions, player.Creature.IsDead,
                    potion.PassesCustomUsabilityCheck))
                continue;
            // NPotionHolder.TargetNode passes null for NMerchantButton;
            // UsePotionAction also preserves null for TargetedNoCreature.
            // Native custom usability owns the current room/target availability.
            // This execution catalog does not authorize bypassing UI targeting.
            if (potion.TargetType == TargetType.TargetedNoCreature)
            {
                if (potion.IsValidTarget(null))
                {
                    string subjectId = identities.GetId(potion, "potion");
                    actions.Add(new(NativeSemanticActionCatalog.BuildKey("use", subjectId,
                            new Dictionary<string, string>()), "use", subjectId, potion,
                        Array.Empty<NativeSemanticOperand>(),
                        "current_potion_slot+AnyTime+CanUseOrRemovePotions+PassesCustomUsabilityCheck+IsValidTarget(null)"));
                }
                continue;
            }
            // NPotionHolder.UsePotion cannot throw at another player outside
            // combat (CanThrowAtAlly); EnqueueManualUse binds null to self.
            if (potion.TargetType is not (TargetType.AnyPlayer or TargetType.Self)
                || !potion.IsValidTarget(player.Creature))
                continue;
            string potionId = identities.GetId(potion, "potion");
            string targetId = identities.GetId(player.Creature, "player");
            actions.Add(new(NativeSemanticActionCatalog.BuildKey("use", potionId,
                    new Dictionary<string, string> { ["target"] = targetId }),
                "use", potionId, potion,
                new[] { new NativeSemanticOperand("target", targetId, player.Creature) },
                "current_potion_slot+AnyTime+CanUseOrRemovePotions+PassesCustomUsabilityCheck+IsValidTarget"));
        }
        return new("captured", "potion_belt_non_combat_use", actions.Count > 0,
            actions.OrderBy(action => action.Key, StringComparer.Ordinal).ToArray(),
            new[] { "NPotionPopup.AnyTime", "NPotionHolder.UsePotion+CanThrowAtAlly",
                "PotionModel.EnqueueManualUse+IsValidTarget", "Player.PotionSlots" }, null);
    }

    internal static bool AllowsNonCombatUse(PotionUsage usage, bool canUse,
        bool ownerDead, bool customUsable) =>
        usage == PotionUsage.AnyTime && canUse && !ownerDead && customUsable;

    public static Creature? ResolveTarget(UsePotionAction action, PotionModel potion)
    {
        // Read the native serialized operand, never infer another player's
        // target from the current UI or substitute the local player for it.
        var native = (NetUsePotionAction)action.ToNetAction();
        if (!CombatManager.Instance.IsInProgress)
            return native.targetPlayerId is { } id
                ? action.Player.RunState.GetPlayer(id)?.Creature
                : potion.TargetType == TargetType.TargetedNoCreature ? null : action.Player.Creature;
        return action.TargetId.HasValue
            ? action.Player.Creature.CombatState?.GetCreature(action.TargetId)
            : potion.TargetType is TargetType.Self or TargetType.AnyPlayer or TargetType.AnyAlly
                ? action.Player.Creature : null;
    }
}
