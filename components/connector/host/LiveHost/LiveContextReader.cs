using STS2Connector.NativeUi;
using System;
using System.Collections.Generic;
using System.Linq;
using Godot;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.HoverTips;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Events;
using MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Events;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.LiveHost;

internal static class LiveContextReader
{
    public static ILiveContext Build(NativeEntityRegistry entities)
    {
        try
        {
            RunState? runState = RunManager.Instance.DebugOnlyGetState();
            if (runState?.CurrentRoom is EventRoom eventRoom)
                return BuildEvent(eventRoom);
            if (runState?.CurrentRoom is RestSiteRoom)
                return new RestLiveContext("rest");
            if (runState?.CurrentRoom is MerchantRoom merchantRoom)
                return BuildShop(merchantRoom, entities);
            if (runState?.CurrentRoom is TreasureRoom)
                return new TreasureLiveContext("treasure");
            if (runState?.CurrentRoom is CombatRoom combatRoom && CombatManager.Instance.IsInProgress)
                return BuildCombat(runState, combatRoom, entities);

            return new UnknownLiveContext(
                "unknown",
                runState?.CurrentRoom?.GetType().Name ?? "no_active_run_context",
                "This context has not yet received a complete player-visible projection.");
        }
        catch (PublicCombatCaptureException ex)
        {
            return new UnknownLiveContext("unknown", ex.Missing,
                "Required public combat facts could not be captured.")
            { CaptureMissing = new[] { ex.Missing } };
        }
        catch (Exception ex)
        {
            return new UnknownLiveContext(
                "unknown",
                "context_read_failed",
                $"Context projection failed closed: {ex.GetType().Name}");
        }
    }

    public static EventLiveContext BuildEvent(EventRoom room)
    {
        EventModel model = room.LocalMutableEvent ?? room.CanonicalEvent;
        NEventRoom? uiRoom = NEventRoom.Instance;
        bool ancient = model is AncientEventModel;
        bool inDialogue = false;
        if (ancient && uiRoom != null)
        {
            NAncientEventLayout? layout = ConnectorMod.FindFirst<NAncientEventLayout>(uiRoom);
            NClickableControl? hitbox = layout?.GetNodeOrNull<NClickableControl>("%DialogueHitbox");
            inDialogue = hitbox is { IsEnabled: true } && ConnectorMod.IsNodeVisible(hitbox);
        }

        return new EventLiveContext(
            "event",
            model.Id.Entry,
            ReadNodeText(uiRoom, "%Title") ?? ConnectorMod.SafeGetText(() => model.Title),
            ancient,
            inDialogue,
            ReadNodeText(uiRoom, "%EventDescription") ?? ConnectorMod.SafeGetText(() => model.Description));
    }

    public static ShopLiveContext BuildShop(MerchantRoom room, NativeEntityRegistry entities)
    {
        // Persistent player facts are owned by top-level shared_state. Shop
        // context identifies only the semantic room; the Surface owns offers.
        return new ShopLiveContext("shop");
    }

    public static CombatLiveContext BuildCombat(
        RunState runState,
        CombatRoom room,
        NativeEntityRegistry entities)
    {
        CombatState combat = CombatManager.Instance.DebugOnlyGetState()
            ?? throw new InvalidOperationException("Combat state is unavailable.");
        Player player = LocalContext.GetMe(runState)
            ?? throw new InvalidOperationException("Local player is unavailable.");
        PlayerCombatState playerCombat = player.PlayerCombatState
            ?? throw new InvalidOperationException("Player combat state is unavailable.");

        var missing = new List<string>();
        VisibleCombatPlayer visiblePlayer = BuildPlayer(player, playerCombat, entities, missing);
        VisibleEnemy[] enemies = combat.Enemies
            .Where(enemy => enemy.IsAlive)
            .Select(enemy => BuildEnemy(enemy, entities, missing))
            .ToArray();
        bool playPhase = playerCombat.Phase == PlayerTurnPhase.Play
                         && CombatManager.Instance.IsPartOfPlayerTurn(player)
                         && !CombatManager.Instance.PlayerActionsDisabled;

        return new CombatLiveContext(
            "combat",
            room.RoomType switch
            {
                RoomType.Monster => "normal",
                RoomType.Elite => "elite",
                RoomType.Boss => "boss",
                _ => "unknown"
            },
            combat.RoundNumber,
            combat.CurrentSide.ToString().ToLowerInvariant(),
            playPhase,
            visiblePlayer,
            enemies)
        {
            CaptureMissing = Array.AsReadOnly(missing.Distinct(StringComparer.Ordinal).ToArray())
        };
    }

    public static VisibleCard BuildCard(
        CardModel card,
        string entityId,
        bool selected = false,
        bool includeCombatLegality = false,
        PileType displayPile = PileType.Hand)
    {
        string cost = card.EnergyCost.CostsX ? "X" : card.EnergyCost.GetAmountToSpend().ToString();
        string? starCost = card.HasStarCostX
            ? "X"
            : card.CurrentStarCost >= 0 ? card.GetStarCostWithModifiers().ToString() : null;
        string? description;
        try
        {
            description = ConnectorMod.StripRichTextTags(card.GetDescriptionForPile(displayPile)).Replace("\n", " ");
        }
        catch
        {
            description = ConnectorMod.SafeGetText(() => card.Description)?.Replace("\n", " ");
        }

        VisibleEnchantment? existing = card.Enchantment == null
            ? null
            : new VisibleEnchantment(
                card.Enchantment.Id.Entry,
                ConnectorMod.SafeGetText(() => card.Enchantment.Title),
                ConnectorMod.SafeGetText(() => card.Enchantment.DynamicDescription),
                card.Enchantment.Amount,
                "card_hover_semantics");

        bool? canPlay = null;
        string? unplayableReason = null;
        if (includeCombatLegality)
        {
            card.CanPlay(out UnplayableReason reason, out _);
            canPlay = reason == UnplayableReason.None;
            unplayableReason = reason == UnplayableReason.None ? null : reason.ToString();
        }

        return new VisibleCard(
            entityId,
            card.Id.Entry,
            ConnectorMod.SafeGetText(() => card.Title),
            card.Type.ToString(),
            cost,
            starCost,
            description,
            card.Rarity.ToString(),
            card.IsUpgraded,
            selected,
            existing,
            includeCombatLegality ? card.TargetType.ToString() : null,
            canPlay,
            unplayableReason);
    }

    internal const string RequiredPowerCaptureMissing = "public_combat_power_facts";
    internal const string RequiredHealthCaptureMissing = "public_combat_health_display";

    internal sealed class PublicCombatCaptureException(string missing) : InvalidOperationException(missing)
    {
        internal string Missing { get; } = missing;
    }

    private static IReadOnlyList<VisibleStatus> BuildStatuses(Creature creature, ICollection<string> missing)
    {
        CombatStatusCapture captured = CaptureStatuses(creature.Powers,
            power => power.IsVisible,
            power => new VisibleStatus(power.Id.Entry,
                ConnectorMod.SafeGetText(() => power.Title), power.DisplayAmount,
                power.Type.ToString(), null),
            power =>
            {
                HoverTip tip = power.HoverTips.OfType<HoverTip>()
                    .FirstOrDefault(value => value.Id == power.Id.ToString());
                return string.IsNullOrWhiteSpace(tip.Description)
                    ? ConnectorMod.StripRichTextTags(power.DumbHoverTip.Description)
                    : ConnectorMod.StripRichTextTags(tip.Description);
            });
        foreach (string reason in captured.Missing) missing.Add(reason);
        return captured.Statuses;
    }

    internal sealed record CombatStatusCapture(IReadOnlyList<VisibleStatus> Statuses,
        IReadOnlyList<string> Missing);

    // Required visible scalar facts are independent of optional rich tooltip
    // text. The same capture is used by legacy Read-rich and native profiles.
    internal static CombatStatusCapture CaptureStatuses<T>(IEnumerable<T> powers,
        Func<T, bool> isVisible, Func<T, VisibleStatus> scalarFacts,
        Func<T, string?> description)
    {
        var result = new List<VisibleStatus>();
        bool requiredFailed = false;
        try
        {
            foreach (T power in powers)
            {
                VisibleStatus scalar;
                try
                {
                    if (!isVisible(power)) continue;
                    scalar = scalarFacts(power);
                }
                catch
                {
                    requiredFailed = true;
                    continue;
                }
                string? optional = null;
                try { optional = description(power); }
                catch { /* Optional text cannot erase required public scalars. */ }
                result.Add(scalar with { Description = optional });
            }
        }
        catch { requiredFailed = true; } // An incomplete native roster is also missing.
        return new(Array.AsReadOnly(result.ToArray()), requiredFailed
            ? new[] { RequiredPowerCaptureMissing } : Array.Empty<string>());
    }

    public static IReadOnlyList<VisibleCombatPotionState> BuildPotionStates(
        Player player,
        NativeEntityRegistry entities,
        bool playPhase)
    {
        var result = new List<VisibleCombatPotionState>();
        for (int slot = 0; slot < player.PotionSlots.Count; slot++)
        {
            PotionModel? potion = player.GetPotionAtSlotIndex(slot);
            if (potion == null)
                continue;
            bool automatic = potion.Usage == PotionUsage.Automatic;
            bool canUse = playPhase
                          && !automatic
                          && !potion.IsQueued
                          && !potion.Owner.Creature.IsDead
                          && potion.PassesCustomUsabilityCheck
                          && HasVisiblePotionTarget(potion, player);
            result.Add(new VisibleCombatPotionState(
                entities.GetId(potion, "potion"),
                potion.TargetType.ToString(),
                canUse,
                automatic));
        }
        return result;
    }

    private static bool HasVisiblePotionTarget(PotionModel potion, Player player)
    {
        if (potion.TargetType == TargetType.AnyEnemy)
            return player.Creature.CombatState?.HittableEnemies.Any(potion.IsValidTarget) == true;
        if (potion.TargetType == TargetType.AnyAlly)
            return player.Creature.CombatState?.PlayerCreatures.Any(potion.IsValidTarget) == true;
        Creature? target = potion.TargetType is TargetType.Self or TargetType.AnyPlayer
            ? player.Creature
            : null;
        return potion.IsValidTarget(target);
    }

    private static VisibleCombatPlayer BuildPlayer(
        Player player,
        PlayerCombatState combat,
        NativeEntityRegistry entities,
        ICollection<string> missing)
    {
        bool playPhase = combat.Phase == PlayerTurnPhase.Play
                         && CombatManager.Instance.IsPartOfPlayerTurn(player)
                         && !CombatManager.Instance.PlayerActionsDisabled;
        VisibleOrb[] orbs = combat.OrbQueue?.Orbs.Select((orb, index) => new VisibleOrb(
            entities.GetId(orb, "orb"),
            orb.Id.Entry,
            ConnectorMod.SafeGetText(() => orb.Title),
            BuildOrbDescription(orb),
            orb.PassiveVal,
            orb.EvokeVal,
            index,
            index == 0)).ToArray() ?? Array.Empty<VisibleOrb>();

        return new VisibleCombatPlayer(
            entities.GetId(player.Creature, "player"),
            player.Creature.Block,
            combat.Energy,
            combat.MaxEnergy,
            player.Character.ShouldAlwaysShowStarCounter || combat.Stars > 0 ? combat.Stars : null,
            combat.Hand.Cards.Select(card => BuildCard(card, entities.GetId(card, "card"), includeCombatLegality: true)).ToArray(),
            combat.DrawPile.Cards.Count,
            combat.DiscardPile.Cards.Count,
            combat.ExhaustPile.Cards.Count,
            BuildStatuses(player.Creature, missing),
            BuildCompanions(combat, entities, missing),
            BuildPotionStates(player, entities, playPhase),
            orbs,
            combat.OrbQueue?.Capacity);
    }

    private static string? BuildOrbDescription(OrbModel orb)
    {
        return ConnectorMod.SafeGetText(() =>
        {
            // This mirrors OrbModel.HoverTips. Reading Description or
            // SmartDescription without these variables emits a localization
            // error and can make a coherent combat observation noisy or fail.
            var description = orb.SmartDescription;
            description.Add("energyPrefix", orb.Owner.Character.CardPool.Title);
            description.Add("Passive", orb.PassiveVal);
            description.Add("Evoke", orb.EvokeVal);
            return description;
        })?.Replace("\n", " ");
    }

    private static IReadOnlyList<VisibleCombatCompanion> BuildCompanions(
        PlayerCombatState combat,
        NativeEntityRegistry entities,
        ICollection<string> missing)
    {
        return combat.Pets.Select(companion =>
        {
            MonsterModel model = companion.Monster
                ?? throw new InvalidOperationException("A player combat pet has no monster model.");
            bool healthBarVisible = model.IsHealthBarVisible;
            bool numbersAvailable = HealthNumbersAvailable(companion.HpDisplay, healthBarVisible);
            return new VisibleCombatCompanion(
                entities.GetId(companion, "companion"),
                model.Id.Entry,
                ConnectorMod.SafeGetText(() => model.Title),
                companion.IsAlive,
                healthBarVisible,
                numbersAvailable ? companion.CurrentHp : null,
                numbersAvailable ? companion.MaxHp : null,
                companion.IsAlive ? companion.Block : 0m,
                companion.IsAlive ? BuildStatuses(companion, missing) : Array.Empty<VisibleStatus>(),
                HealthDisplayMode(companion.HpDisplay),
                numbersAvailable);
        }).ToArray();
    }

    internal static bool HealthNumbersAvailable(HpDisplay mode, bool healthBarVisible) =>
        healthBarVisible && mode.ShowsNumbers();

    internal static string HealthDisplayMode(HpDisplay mode) => mode switch
    {
        HpDisplay.Normal => "normal",
        HpDisplay.InfiniteWithNumbers => "infinite_with_numbers",
        HpDisplay.InfiniteWithoutNumbers => "infinite_without_numbers",
        _ => throw new PublicCombatCaptureException(RequiredHealthCaptureMissing)
    };

    internal static VisibleEnemy BuildEnemy(Creature creature, NativeEntityRegistry entities,
        ICollection<string> missing)
    {
        var intents = new List<VisibleIntent>();
        if (creature.Monster?.NextMove is MoveState move)
        {
            foreach (var intent in move.Intents)
            {
                string? label = null;
                string? title = null;
                string? description = null;
                try
                {
                    var targets = creature.CombatState?.PlayerCreatures;
                    if (targets != null)
                    {
                        label = ConnectorMod.StripRichTextTags(intent.GetIntentLabel(targets, creature).GetFormattedText());
                        var tip = intent.GetHoverTip(targets, creature);
                        title = tip.Title == null ? null : ConnectorMod.StripRichTextTags(tip.Title);
                        description = tip.Description == null ? null : ConnectorMod.StripRichTextTags(tip.Description);
                    }
                }
                catch
                {
                    // Intent type remains visible even if transient localization fails.
                }
                intents.Add(new VisibleIntent(intent.IntentType.ToString(), label, title, description));
            }
        }

        bool healthBarVisible = creature.Monster?.IsHealthBarVisible ?? true;
        bool numbersAvailable = HealthNumbersAvailable(creature.HpDisplay, healthBarVisible);
        return new VisibleEnemy(
            entities.GetId(creature, "enemy"),
            creature.CombatId,
            creature.Monster?.Id.Entry ?? "unknown",
            ConnectorMod.SafeGetText(() => creature.Monster?.Title),
            numbersAvailable ? creature.CurrentHp : null,
            numbersAvailable ? creature.MaxHp : null,
            creature.Block,
            BuildStatuses(creature, missing),
            intents,
            healthBarVisible,
            HealthDisplayMode(creature.HpDisplay),
            numbersAvailable);
    }

    private static string? ReadNodeText(Node? owner, string path)
    {
        try
        {
            Node? node = owner?.GetNodeOrNull(path);
            if (node == null && owner != null)
            {
                string nodeName = path.StartsWith("%", StringComparison.Ordinal)
                    ? path[1..]
                    : path;
                // The concrete event layout can own this unique-name label
                // below NEventRoom. Prefer already-rendered player-visible
                // text over unbound localization model variables.
                node = owner.FindChild(nodeName, recursive: true, owned: false);
            }
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
}
