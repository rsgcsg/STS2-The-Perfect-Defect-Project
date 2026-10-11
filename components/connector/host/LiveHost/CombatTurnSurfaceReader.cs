using STS2Connector.NativeUi;
using System;
using System.Collections.Generic;
using System.Linq;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost.Contracts;
using STS2Platform.NativeFoundation;

namespace STS2Connector.LiveHost;

internal sealed class CombatTurnSurfaceReader : ILiveSurfaceReader
{
    internal const string PlayCardDeliveryEvidence = "native_card_play_accepted";
    internal const string UsePotionDeliveryEvidence = "native_potion_use_enqueued";
    internal const string EndTurnDeliveryEvidence = NativeEndTurnInput.DeliveryEvidence;

    public string Kind => "combat_turn";

    public InputOwnerLayer Layer => InputOwnerLayer.Room;

    public LiveObservation? TryBuild(
        ActiveSurfaceSnapshot snapshot,
        NativeEntityRegistry entities,
        GameBuildIdentity game)
    {
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        NCombatRoom? room = NCombatRoom.Instance;
        NPlayerHand? hand = NPlayerHand.Instance;
        if (runState?.CurrentRoom is not CombatRoom combatRoom
            || !CombatManager.Instance.IsInProgress
            || room == null
            || hand == null
            || !ConnectorMod.IsLiveNode(room)
            || hand.IsInCardSelection)
        {
            return null;
        }

        if (LiveContextReader.BuildCombat(runState, combatRoom, entities) is not CombatLiveContext context)
            return null;
        Player player = LocalContext.GetMe(runState)
            ?? throw new InvalidOperationException("Local player is unavailable.");
        PlayerCombatState? playerCombat = player.PlayerCombatState;
        if (playerCombat == null)
            return null;
        NativeCombatDecision semanticDecision = NativeCombatDecisionProvider.Capture(entities);
        CardModel[] visibleHandCards = hand.ActiveHolders
            .Select(holder => holder.CardModel)
            .Where(card => card != null)
            .Cast<CardModel>()
            .ToArray();
        context = context with
        {
            Player = context.Player with
            {
                Hand = visibleHandCards
                    .Select(card => LiveContextReader.BuildCard(
                        card,
                        entities.GetId(card, "card"),
                        includeCombatLegality: true))
                    .ToArray()
            }
        };

        bool presentationReady = IsCombatInputReady(
            CombatManager.Instance.IsInProgress,
            CombatManager.Instance.PlayerActionsDisabled,
            playerCombat.Phase == PlayerTurnPhase.Play,
            CombatManager.Instance.IsPartOfPlayerTurn(player),
            !hand.InCardPlay && hand.CurrentMode == NPlayerHand.Mode.Play,
            hand.PeekButton?.IsPeeking == true);
        bool inputReady = IsProjectionReady(
            presentationReady,
            semanticDecision.Status,
            semanticDecision.IsDecisionOpen);
        var playableCards = new List<VisibleCombatCommandOption>();
        var usablePotions = new List<VisibleCombatCommandOption>();
        if (inputReady)
        {
            AddCardOptions(playableCards, semanticDecision, visibleHandCards);
            AddPotionOptions(usablePotions, semanticDecision, player);
        }

        bool canEndTurn = inputReady
            && room.Ui.EndTurnButton.IsEnabled
            && semanticDecision.Actions.Any(action => action.Verb == "end_turn");
        NativeEndTurnInput.Binding? endTurn = null;
        string? endTurnMissing = null;
        if (canEndTurn)
            endTurn = NativeEndTurnInput.Capture(room, hand, player, out endTurnMissing);
        string? endTurnIdentity = endTurn == null ? null : NativeEndTurnInput.Identity(entities, endTurn.ExactScope);
        var surface = new CombatTurnSurface(
            Kind,
            entities.GetId(room, "room"),
            canEndTurn)
        {
            PlayableCards = playableCards,
            UsablePotions = usablePotions
        };
        string readiness = inputReady ? "ready" : "settling";
        var completeness = new StateCompleteness(
            "contract_complete_for_immediate_combat_turn_including_visible_companions; pile contents available through a separate read-only Player Environment Read",
            inputReady
                ? "derived_from_same_validator_as_execution"
                : presentationReady
                    ? "empty_while_native_semantic_decision_is_unavailable"
                    : "empty_while_native_hand_rejects_input",
            new[]
            {
                "CombatManager.DebugOnlyGetState",
                "LocalContext.GetMe",
                "PlayerCombatState",
                "PlayerCombatState.Pets+MonsterModel.IsHealthBarVisible",
                "NativeCombatDecisionProvider logical action catalog",
                "CombatState.HittableEnemies",
                "NPlayerHand.ActiveHolders+AreCardActionsAllowed-equivalent guards",
                "NCardPlayQueue exclusion"
            },
            Array.Empty<string>());
        string signature = StableIdentityHash.Object(new
        {
            game.Version,
            context,
            surface
        });
        if (endTurnIdentity != null)
            signature = StableIdentityHash.Object(new { signature, endTurnIdentity });

        var observation = new LiveObservation(
            signature,
            readiness,
            context,
            surface,
            completeness,
            game,
            Array.Empty<string>())
        {
            EndTurnBinding = endTurn,
            EndTurnBindingIdentity = endTurnIdentity,
            Diagnostics = new[]
            {
                new HostDiagnostic(
                    "host.visibility.combat_pile_contents_externalized",
                    "info",
                    "visibility",
                    "none",
                    "unknown",
                    Path: "context.player.draw_discard_exhaust_piles",
                    VisibilityClass: "normal_player_read",
                    RequiredForAction: false,
                    SafeDetail: "Pile counts remain in immediate context; player-inspectable contents use the state-bound Read contract.")
            }
        };
        return ApplyEndTurnCapturePolicy(observation, endTurnMissing);
    }

    internal static LiveObservation ApplyEndTurnCapturePolicy(LiveObservation observation, string? missing)
    {
        if (missing == null) return observation;
        string[] absent = observation.Completeness.Missing.Append(missing).Distinct(StringComparer.Ordinal).ToArray();
        return observation with
        {
            Signature = StableIdentityHash.Object(new { observation.Signature, absent }),
            Readiness = "unsupported",
            Surface = new UnsupportedSurface(observation.Surface.Kind, missing,
                "The native End Turn gesture requires an exact current binding and an already seen playable-card FTUE."),
            Completeness = observation.Completeness with
            { PlayerVisibleSemantics = "partial", InteractionDiscovery = "empty_fail_closed", Missing = absent },
            InputOwnership = new("none_fail_closed", null, "The native End Turn input binding is unsupported."),
            EndTurnBinding = null,
            EndTurnBindingIdentity = null
        };
    }

    private static void AddCardOptions(
        ICollection<VisibleCombatCommandOption> commandOptions,
        NativeCombatDecision decision,
        IReadOnlyList<CardModel> visibleHandCards)
    {
        foreach (IGrouping<object?, NativeSemanticAction> group in NativeDecisionProjection
                     .VisibleSubjects(decision, "play", visibleHandCards)
                     .GroupBy(action => action.NativeSubject, ReferenceEqualityComparer.Instance))
        {
            NativeSemanticAction first = group.First();
            CardModel card = (CardModel)first.NativeSubject!;
            string cardName = ConnectorMod.SafeGetText(() => card.Title) ?? card.Id.Entry;
            string[] targetIds = group.SelectMany(action => action.Operands)
                .Where(operand => operand.Role == "target")
                .Select(operand => operand.ReferentId)
                .Distinct(StringComparer.Ordinal)
                .Order(StringComparer.Ordinal)
                .ToArray();
            commandOptions.Add(new VisibleCombatCommandOption(
                first.SubjectReferentId!,
                cardName,
                targetIds));
        }
    }

    private static void AddPotionOptions(
        ICollection<VisibleCombatCommandOption> commandOptions,
        NativeCombatDecision decision,
        Player player)
    {
        object[] deliverablePotions = Enumerable.Range(0, player.PotionSlots.Count)
            .Select(index => player.GetPotionAtSlotIndex(index))
            .Where(potion => CanUsePotion(player, potion))
            .Cast<object>()
            .ToArray();
        foreach (IGrouping<object?, NativeSemanticAction> group in NativeDecisionProjection
                     .VisibleSubjects(decision, "use", deliverablePotions)
                     .GroupBy(action => action.NativeSubject, ReferenceEqualityComparer.Instance))
        {
            NativeSemanticAction first = group.First();
            PotionModel potion = (PotionModel)first.NativeSubject!;
            string potionName = ConnectorMod.SafeGetText(() => potion.Title) ?? potion.Id.Entry;
            string[] targetIds = group.SelectMany(action => action.Operands)
                .Where(operand => operand.Role == "target")
                .Select(operand => operand.ReferentId)
                .Distinct(StringComparer.Ordinal)
                .Order(StringComparer.Ordinal)
                .ToArray();
            commandOptions.Add(new VisibleCombatCommandOption(
                first.SubjectReferentId!,
                potionName,
                targetIds));
        }
    }

    internal static bool IsAdvertisablePotionTarget(
        PotionModel potion,
        Creature? target) => potion.IsValidTarget(target);

    internal static bool IsCombatInputReady(
        bool combatInProgress,
        bool playerActionsDisabled,
        bool inPlayPhase,
        bool isPartOfPlayerTurn,
        bool handAcceptsInput,
        bool handIsPeeking) =>
        combatInProgress
        && !playerActionsDisabled
        && inPlayPhase
        && isPartOfPlayerTurn
        && handAcceptsInput
        && !handIsPeeking;

    internal static NativeInputResult StartDirectPlayCard(
        NativeEntityRegistry entities,
        string expectedRoomId,
        string expectedCardId,
        string? expectedTargetId)
    {
        if (!TryResolveDirectCombatOwner(entities, expectedRoomId, out Player? player)
            || !entities.TryResolve(expectedCardId, out CardModel? card)
            || card == null)
        {
            return NativeInputResult.Rejected(
                "combat_binding_changed",
                "The exact combat room, player, or hand card is no longer current.");
        }
        Creature? target = null;
        if (expectedTargetId != null
            && (!entities.TryResolve(expectedTargetId, out target) || target == null))
        {
            return NativeInputResult.Rejected(
                "target_no_longer_legal",
                "The exact advertised combat target no longer resolves.");
        }
        return StartPlayCard(player!, card, target);
    }

    internal static NativeInputResult StartDirectUsePotion(
        NativeEntityRegistry entities,
        string expectedRoomId,
        string expectedPotionId,
        string? expectedTargetId)
    {
        if (!TryResolveDirectCombatOwner(entities, expectedRoomId, out Player? player)
            || !entities.TryResolve(expectedPotionId, out PotionModel? potion)
            || potion == null)
        {
            return NativeInputResult.Rejected(
                "combat_binding_changed",
                "The exact combat room, player, or potion is no longer current.");
        }
        int slot = Enumerable.Range(0, player!.PotionSlots.Count)
            .FirstOrDefault(index => ReferenceEquals(player.GetPotionAtSlotIndex(index), potion), -1);
        if (slot < 0)
        {
            return NativeInputResult.Rejected(
                "potion_slot_changed",
                "The exact advertised potion is no longer in a current slot.");
        }
        Creature? target = null;
        if (expectedTargetId != null
            && (!entities.TryResolve(expectedTargetId, out target) || target == null))
        {
            return NativeInputResult.Rejected(
                "target_no_longer_legal",
                "The exact advertised potion target no longer resolves.");
        }
        return StartUsePotion(player, potion, slot, target);
    }

    internal static NativeInputResult StartDirectEndTurn(
        NativeEntityRegistry entities,
        string expectedRoomId, NativeEndTurnInput.Binding? expected) =>
        TryResolveDirectCombatOwner(entities, expectedRoomId, out Player? player)
            && expected != null && ReferenceEquals(expected.Player, player)
            ? NativeEndTurnInput.Start(expected)
            : NativeInputResult.Rejected(
                "combat_binding_changed",
                "The exact combat room or player is no longer current.");

    private static bool TryResolveDirectCombatOwner(
        NativeEntityRegistry entities,
        string expectedRoomId,
        out Player? player)
    {
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        NCombatRoom? room = NCombatRoom.Instance;
        player = runState == null ? null : LocalContext.GetMe(runState);
        return runState?.CurrentRoom is CombatRoom
               && room != null
               && ConnectorMod.IsLiveNode(room)
               && string.Equals(entities.GetId(room, "room"), expectedRoomId, StringComparison.Ordinal)
               && player != null;
    }

    internal static NativeInputResult StartPlayCard(
        Player expectedPlayer,
        CardModel expectedCard,
        Creature? expectedTarget)
    {
        if (!IsActionablePlayerTurn(expectedPlayer))
            return NativeInputResult.Rejected("combat_phase_changed", "Combat is no longer in the local player's play phase.");
        PlayerCombatState combat = expectedPlayer.PlayerCombatState!;
        if (!combat.Hand.Cards.Contains(expectedCard))
            return NativeInputResult.Rejected("card_left_hand", "The advertised card is no longer in hand.");
        if (NPlayerHand.Instance?.ActiveHolders.Any(holder =>
                ReferenceEquals(holder.CardModel, expectedCard)) != true)
        {
            return NativeInputResult.Rejected(
                "card_left_visible_hand",
                "The advertised card is no longer an actionable holder in the native hand UI.");
        }
        if (!expectedCard.CanPlay(out UnplayableReason reason, out _) || reason != UnplayableReason.None)
            return NativeInputResult.Rejected("card_no_longer_playable", $"The card is no longer playable: {reason}.");
        if (expectedCard.TargetType == TargetType.AnyEnemy)
        {
            if (expectedTarget == null
                || expectedPlayer.Creature.CombatState?.HittableEnemies.Contains(expectedTarget) != true)
            {
                return NativeInputResult.Rejected("target_no_longer_legal", "The advertised enemy target is no longer hittable.");
            }
        }

        if (!expectedCard.TryManualPlay(expectedTarget))
            return NativeInputResult.Rejected("card_target_no_longer_valid", "The card no longer accepts the advertised target.");
        return NativeInputResult.Delivered(PlayCardDeliveryEvidence);
    }

    internal static NativeInputResult StartUsePotion(
        Player expectedPlayer,
        PotionModel expectedPotion,
        int expectedSlot,
        Creature? expectedTarget)
    {
        if (!IsActionablePlayerTurn(expectedPlayer) || !CanUsePotion(expectedPlayer, expectedPotion))
            return NativeInputResult.Rejected("potion_no_longer_usable", "The potion is no longer usable in the current phase.");
        if (!ReferenceEquals(expectedPlayer.GetPotionAtSlotIndex(expectedSlot), expectedPotion))
            return NativeInputResult.Rejected("potion_slot_changed", "The advertised potion is no longer in the same slot.");
        if (!expectedPotion.IsValidTarget(expectedTarget)
            || (expectedPotion.TargetType == TargetType.AnyEnemy
                && expectedPlayer.Creature.CombatState?.HittableEnemies.Contains(expectedTarget!) != true))
        {
            return NativeInputResult.Rejected("target_no_longer_legal", "The advertised potion target is no longer hittable.");
        }

        expectedPotion.EnqueueManualUse(expectedTarget);
        return NativeInputResult.Delivered(UsePotionDeliveryEvidence);
    }

    internal static bool CanUsePotion(Player player, PotionModel? potion) =>
        potion != null
        && IsActionablePlayerTurn(player)
        && potion.Usage != PotionUsage.Automatic
        && !potion.IsQueued
        && !potion.Owner.Creature.IsDead
        && potion.PassesCustomUsabilityCheck;

    internal static bool IsActionablePlayerTurn(Player player)
    {
        NPlayerHand? hand = NPlayerHand.Instance;
        return IsCombatInputReady(
            CombatManager.Instance.IsInProgress,
            CombatManager.Instance.PlayerActionsDisabled,
            player.PlayerCombatState?.Phase == PlayerTurnPhase.Play,
            CombatManager.Instance.IsPartOfPlayerTurn(player),
            hand != null && !hand.InCardPlay && hand.CurrentMode == NPlayerHand.Mode.Play,
            hand?.PeekButton?.IsPeeking == true);
    }

    internal static bool IsProjectionReady(
        bool presentationReady,
        string semanticStatus,
        bool semanticDecisionOpen) =>
        presentationReady
        && string.Equals(semanticStatus, "captured", StringComparison.Ordinal)
        && semanticDecisionOpen;
}
