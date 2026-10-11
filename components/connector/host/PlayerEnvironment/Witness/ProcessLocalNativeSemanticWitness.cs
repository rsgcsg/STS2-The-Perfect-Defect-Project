using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Multiplayer;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.Extensions;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Platform.NativeFoundation;

namespace STS2Connector.PlayerEnvironment.Witness;

public sealed record ProcessLocalSemanticAction(
    string Key,
    string Verb,
    string? SubjectReferentId,
    IReadOnlyDictionary<string, string> Arguments,
    string NativeLegalityBasis);

public sealed record ProcessLocalObservedSemanticAction(
    string NativeActionType,
    string? Key,
    string Status,
    int SemanticMatchCount,
    string Membership,
    string? Detail);

public sealed record ProcessLocalUiCatalogObservation(
    string SnapshotId,
    string SnapshotStatus,
    string InteractionKind,
    string BoundActionsStatus,
    int ActionCount,
    string CatalogDigest,
    string? ObservedMembership,
    int? ObservedMatchCount);

public sealed record ProcessLocalNativeSemanticCapture(
    string Schema,
    string Status,
    string Scope,
    string Phase,
    string? SemanticStateDigest,
    JsonNode? SemanticState,
    string SemanticCatalogDigest,
    IReadOnlyList<ProcessLocalSemanticAction> SemanticActions,
    ProcessLocalObservedSemanticAction? ObservedAction,
    ProcessLocalUiCatalogObservation UiCatalog,
    IReadOnlyList<string> Evidence,
    IReadOnlyList<string> NonClaims,
    string? Detail);

/// <summary>
/// Typed read-only process-local observation of gameplay-semantic decisions
/// projected from STS2-owned state and Native Foundation validators. Connector
/// public action delivery may consume the same providers, while evidence and
/// diagnostics may preserve this observation without gaining mutation authority.
/// </summary>
public static class PlayerEnvironmentNativeSemanticWitness
{
    public const string Schema =
        "sts2.player-environment/process-local-native-semantic-capture-1";

    public static ProcessLocalNativeSemanticCapture Capture(
        string phase,
        GameAction? observedAction = null,
        ProcessLocalNativeWitnessFrame? uiFrame = null,
        Action<ProcessLocalNativeWitnessFrame>? capturedUiFrame = null,
        string? semanticNativeActionType = null,
        ProcessLocalObservedAction? semanticSelection = null)
    {
        if (uiFrame == null)
        {
            uiFrame = PlayerEnvironmentNativeWitness.Capture();
            capturedUiFrame?.Invoke(uiFrame);
        }
        ProcessLocalUiCatalogObservation ui = BuildUiCatalog(uiFrame, observedAction);
        try
        {
            RunState? run = RunManager.Instance.DebugOnlyGetState();
            if (semanticNativeActionType == "NPotionPopup.OnDiscardButtonPressed"
                || run?.CurrentRoom is not CombatRoom combatRoom
                || !CombatManager.Instance.IsInProgress)
            {
                NativeDomainOwnerObservation domain = NativeDomainOwnerProbe.Capture();
                return DomainOwnerCapture(
                    phase,
                    observedAction,
                    semanticNativeActionType,
                    semanticSelection,
                    ui,
                    domain,
                    NativeUiRuntime.Entities);
            }

            Player? player = LocalContext.GetMe(run);
            PlayerCombatState? playerCombat = player?.PlayerCombatState;
            if (player == null || playerCombat == null)
            {
                return Unavailable(
                    phase,
                    "combat_state_unavailable",
                    observedAction,
                    ui,
                    "The local player combat state is unavailable.");
            }

            NativeEntityRegistry entities = NativeUiRuntime.Entities;
            CombatLiveContext context = LiveContextReader.BuildCombat(run, combatRoom, entities);
            if (context.CaptureMissing.Count != 0)
                return Unavailable(phase, LiveContextReader.RequiredPowerCaptureMissing,
                    observedAction, ui, "Required public combat power facts could not be captured.");
            NativeCombatDecision decision = NativeCombatDecisionProvider.Capture(entities);
            bool localPlayPhase = decision.IsDecisionOpen;
            IReadOnlyList<VisibleCombatPotionState> semanticPotions =
                BuildSemanticPotionStates(player, entities, decision);
            context = context with
            {
                Player = context.Player with { PotionStates = semanticPotions }
            };

            var state = new
            {
                encounter_type = context.EncounterType,
                round = context.Round,
                turn_owner = context.TurnOwner,
                player = new
                {
                    entity_id = context.Player.PlayerEntityId,
                    hp = player.Creature.CurrentHp,
                    max_hp = player.Creature.MaxHp,
                    context.Player.Block,
                    context.Player.Energy,
                    context.Player.MaxEnergy,
                    context.Player.Stars,
                    hand = context.Player.Hand.Select(card => new
                    {
                        card.EntityId,
                        card.DefinitionId,
                        card.Type,
                        card.Cost,
                        card.StarCost,
                        card.IsUpgraded,
                        card.TargetType,
                        card.CanPlay,
                        card.UnplayableReason
                    }).ToArray(),
                    context.Player.DrawPileCount,
                    context.Player.DiscardPileCount,
                    context.Player.ExhaustPileCount,
                    statuses = context.Player.Statuses.Select(status => new
                    {
                        status.DefinitionId,
                        status.Amount,
                        status.Type
                    }).ToArray(),
                    companions = context.Player.Companions.Select(companion => new
                    {
                        companion.EntityId,
                        companion.DefinitionId,
                        companion.IsAlive,
                        companion.HealthBarVisible,
                        companion.Hp,
                        companion.MaxHp,
                        companion.HpDisplayMode,
                        companion.HpNumbersAvailable,
                        companion.Block,
                        statuses = companion.Statuses.Select(status => new
                        {
                            status.DefinitionId,
                            status.Amount,
                            status.Type
                        }).ToArray()
                    }).ToArray(),
                    potions = semanticPotions,
                    orbs = context.Player.Orbs.Select(orb => new
                    {
                        orb.EntityId,
                        orb.DefinitionId,
                        orb.PassiveValue,
                        orb.EvokeValue,
                        orb.QueueIndex,
                        orb.IsNextToEvoke
                    }).ToArray(),
                    context.Player.OrbSlots
                },
                enemies = context.Enemies.Select(enemy => new
                {
                    enemy.EntityId,
                    enemy.CombatId,
                    enemy.DefinitionId,
                    enemy.Hp,
                    enemy.MaxHp,
                    enemy.HealthBarVisible,
                    enemy.HpDisplayMode,
                    enemy.HpNumbersAvailable,
                    enemy.Block,
                    statuses = enemy.Statuses.Select(status => new
                    {
                        status.DefinitionId,
                        status.Amount,
                        status.Type
                    }).ToArray(),
                    intents = enemy.Intents.Select(intent => intent.Type).ToArray()
                }).ToArray(),
                player_turn_number = playerCombat.TurnNumber,
                player_phase = playerCombat.Phase.ToString(),
                player_actions_disabled = CombatManager.Instance.PlayerActionsDisabled,
                player_ready_to_end_turn = CombatManager.Instance.IsPlayerReadyToEndTurn(player),
                action_synchronizer_state =
                    RunManager.Instance.ActionQueueSynchronizer.CombatState.ToString(),
                semantic_input_open = localPlayPhase
            };
            IReadOnlyList<ProcessLocalSemanticAction> actions = decision.Actions
                .Select(action => new ProcessLocalSemanticAction(
                    action.Key,
                    action.Verb,
                    action.SubjectReferentId,
                    action.Operands.ToDictionary(
                        operand => operand.Role,
                        operand => operand.ReferentId,
                        StringComparer.Ordinal),
                    action.NativeLegalityBasis))
                .ToArray();
            ProcessLocalObservedAction? semanticObserved = semanticSelection
                ?? TryDescribeForUi(observedAction);
            ProcessLocalObservedSemanticAction? observed = semanticObserved == null
                ? null
                : ToLegacyObserved(
                    semanticObserved.Subject == null
                        ? NativeSemanticActionCatalog.DescribeWithoutSubject(
                            decision.Actions,
                            semanticNativeActionType
                                ?? observedAction?.GetType().Name
                                ?? "native_ui_action",
                            semanticObserved.Verb,
                            semanticObserved.Arguments)
                        : NativeSemanticActionCatalog.Describe(
                            decision.Actions,
                            semanticNativeActionType
                                ?? observedAction?.GetType().Name
                                ?? "native_ui_action",
                            semanticObserved.Verb,
                            semanticObserved.Subject,
                            semanticObserved.Arguments));
            string scope = observedAction?.State.ToString() == "ReadyToResumeExecuting"
                ? "player_choice_continuation"
                : localPlayPhase ? "combat_play_phase" : "combat_non_decision";

            return new ProcessLocalNativeSemanticCapture(
                Schema,
                decision.Status,
                scope,
                phase,
                StableIdentityHash.Object(state),
                JsonSerializer.SerializeToNode(state, ConnectorMod._jsonOptions),
                CatalogDigest(actions),
                actions,
                observed,
                ui,
                decision.Evidence,
                new[]
                {
                    "read_only_semantic_observation_not_mutation_authority",
                    "not_public_bound_action_delivery_authority",
                    "compact_state_omits_persistent_run_hud_and_on_demand_reads",
                    "end_turn_finished_does_not_alone_prove_turn_commit",
                    "player_choice_options_remain_owned_by_the_current_selector"
                },
                decision.Detail);
        }
        catch (Exception exception)
        {
            return Unavailable(
                phase,
                "capture_failed",
                observedAction,
                ui,
                $"{exception.GetType().Name}: {exception.Message}");
        }
    }

    private static ProcessLocalNativeSemanticCapture Unavailable(
        string phase,
        string status,
        GameAction? observedAction,
        ProcessLocalUiCatalogObservation ui,
        string detail) =>
        new(
            Schema,
            status,
            "unavailable",
            phase,
            null,
            null,
            CatalogDigest(Array.Empty<ProcessLocalSemanticAction>()),
            Array.Empty<ProcessLocalSemanticAction>(),
            observedAction == null
                ? null
                : new ProcessLocalObservedSemanticAction(
                    observedAction.GetType().Name,
                    null,
                    "not_described",
                    0,
                    "unknown",
                    detail),
            ui,
            Array.Empty<string>(),
            new[] { "semantic_state_not_captured" },
            detail);

    private static ProcessLocalNativeSemanticCapture DomainOwnerCapture(
        string phase,
        GameAction? observedAction,
        string? semanticNativeActionType,
        ProcessLocalObservedAction? semanticSelection,
        ProcessLocalUiCatalogObservation ui,
        NativeDomainOwnerObservation domain,
        NativeEntityRegistry entities)
    {
        (string status, string scope, IReadOnlyList<NativeSemanticAction> nativeActions,
            IReadOnlyList<string> nativeEvidence, string? nativeDetail) =
            CaptureDomainDecision(domain, entities, semanticNativeActionType ?? observedAction?.GetType().Name);
        semanticSelection ??= observedAction is UsePotionAction ? TryDescribeForUi(observedAction) : null;
        IReadOnlyList<ProcessLocalSemanticAction> actions = nativeActions
            .Select(action => new ProcessLocalSemanticAction(
                action.Key,
                action.Verb,
                action.SubjectReferentId,
                action.Operands.ToDictionary(
                    operand => operand.Role,
                    operand => operand.ReferentId,
                    StringComparer.Ordinal),
                action.NativeLegalityBasis))
            .ToArray();
        var semanticState = new
        {
            domain,
            native_decision_status = status,
            action_keys = actions.Select(action => action.Key).ToArray()
        };
        JsonNode state = JsonSerializer.SerializeToNode(
                semanticState,
                ConnectorMod._jsonOptions)
            ?? new JsonObject();
        NativeObservedSemanticAction? selected = semanticSelection == null
            ? null
            : DescribeDomainSelection(
                nativeActions,
                semanticNativeActionType ?? observedAction?.GetType().Name ?? "native_ui_action",
                semanticSelection);
        return new ProcessLocalNativeSemanticCapture(
            Schema,
            status,
            scope,
            phase,
            StableIdentityHash.Object(semanticState),
            state,
            CatalogDigest(actions),
            actions,
            selected != null
                ? ToLegacyObserved(selected)
                : observedAction == null
                    ? null
                    : new ProcessLocalObservedSemanticAction(
                        observedAction.GetType().Name,
                        null,
                        "outside_direct_native_catalog",
                        0,
                        "not_applicable",
                        "No typed native selection was supplied for this non-combat lifecycle."),
            ui,
            domain.Evidence.Concat(nativeEvidence).Distinct(StringComparer.Ordinal).ToArray(),
            domain.NonClaims,
            nativeDetail
            ?? $"semantic_owner={domain.SemanticDomain};input_owner={domain.InputDomain}");
    }

    private static NativeObservedSemanticAction DescribeDomainSelection(
        IReadOnlyList<NativeSemanticAction> actions,
        string nativeActionType,
        ProcessLocalObservedAction selection)
    {
        NativeObservedSemanticAction exact = NativeSemanticActionCatalog.Describe(
            actions,
            nativeActionType,
            selection.Verb,
            selection.Subject,
            selection.Arguments);
        if (exact.Membership == "exact_once" || selection.Subject == null)
            return exact;

        // Public delivery may intentionally call a native travel/claim/select
        // operation "activate". Exact process-local subject/operand identity,
        // not the presentation verb, is the binding authority in that case.
        return NativeSemanticActionCatalog.DescribeByIdentity(
            actions,
            nativeActionType,
            selection.Subject,
            selection.Arguments);
    }

    private static (
        string Status,
        string Scope,
        IReadOnlyList<NativeSemanticAction> Actions,
        IReadOnlyList<string> Evidence,
        string? Detail) CaptureDomainDecision(
        NativeDomainOwnerObservation domain,
        NativeEntityRegistry entities,
        string? semanticNativeActionType)
    {
        // The exact native root type outranks a stale overlay during room
        // transitions. This selects a typed provider; it does not infer an
        // action or its legality.
        if (semanticNativeActionType == nameof(UsePotionAction))
        {
            NativeCombatDecision decision = NativePotionUseDecisionProvider.Capture(entities);
            return (decision.Status, decision.Scope, decision.Actions, decision.Evidence, decision.Detail);
        }
        if (semanticNativeActionType == "NPotionPopup.OnDiscardButtonPressed")
        {
            NativePotionDiscardDecision decision = NativePotionDiscardDecisionProvider.Capture(entities);
            return (decision.Status, decision.Scope, decision.Actions, decision.Evidence, decision.Detail);
        }
        if (semanticNativeActionType == nameof(VoteForMapCoordAction))
        {
            NativeMapDecision decision = NativeMapDecisionProvider.Capture(entities);
            return (decision.Status, decision.Scope, decision.Actions, decision.Evidence, decision.Detail);
        }
        if (semanticNativeActionType == nameof(PickRelicAction)
            && NRun.Instance?.TreasureRoom is { } exactTreasure)
        {
            NativeTreasureDecision decision =
                NativeTreasureDecisionProvider.Capture(exactTreasure, entities);
            return (decision.Status, decision.Scope, decision.Actions, decision.Evidence, decision.Detail);
        }
        object? overlay = NOverlayStack.Instance?.Peek();
        if ((semanticNativeActionType is
                "NChooseARelicSelection.SelectHolder" or
                "NChooseARelicSelection.OnSkipButtonReleased")
            && overlay is NChooseARelicSelection bossRelicScreen)
        {
            NativeBossRelicDecision decision =
                CaptureBossRelicDecision(bossRelicScreen, entities);
            return (decision.Status, decision.Scope, decision.Actions, new[]
            {
                $"NativePlayerChoiceLineage:{decision.ParentLineage.Status}",
                NativeBossRelicDecisionProvider.ParentCommand,
                NativeBossRelicDecisionProvider.CommitSeam
            }.Concat(decision.Detail == null
                ? Array.Empty<string>()
                : new[] { decision.Detail }).ToArray(), decision.Detail);
        }
        if (overlay is NRewardsScreen rewards)
        {
            NativeRewardDecision decision =
                NativeRewardDecisionProvider.Capture(rewards, entities);
            return (decision.Status, decision.Scope, decision.Actions, decision.Evidence, decision.Detail);
        }
        if (overlay is NCardRewardSelectionScreen cardReward)
        {
            NativeCardRewardDecision decision =
                NativeCardRewardDecisionProvider.Capture(cardReward, entities);
            return (decision.Status, decision.Scope, decision.Actions, decision.Evidence, decision.Detail);
        }
        if (NMapScreen.Instance?.IsOpen == true)
        {
            NativeMapDecision decision = NativeMapDecisionProvider.Capture(entities);
            return (decision.Status, decision.Scope, decision.Actions, decision.Evidence, decision.Detail);
        }
        if (RunManager.Instance.DebugOnlyGetState()?.CurrentRoom is TreasureRoom
            && NRun.Instance?.TreasureRoom is { } treasure)
        {
            NativeTreasureDecision decision =
                NativeTreasureDecisionProvider.Capture(treasure, entities);
            return (decision.Status, decision.Scope, decision.Actions, decision.Evidence, decision.Detail);
        }
        if (RunManager.Instance.DebugOnlyGetState()?.CurrentRoom is
            MegaCrit.Sts2.Core.Rooms.EventRoom or
            MegaCrit.Sts2.Core.Rooms.MerchantRoom or
            MegaCrit.Sts2.Core.Rooms.RestSiteRoom)
        {
            NativeRoomDecision decision = NativeRoomDecisionProvider.Capture(entities);
            return (decision.Status, decision.Scope, decision.Actions, decision.Evidence, decision.Detail);
        }
        return (
            domain.Status,
            domain.SemanticDomain,
            Array.Empty<NativeSemanticAction>(),
            Array.Empty<string>(),
            "No migrated native decision adapter owns the current domain.");
    }

    private static NativeBossRelicDecision CaptureBossRelicDecision(
        NChooseARelicSelection screen,
        NativeEntityRegistry entities)
    {
        var relicsField = typeof(NChooseARelicSelection).GetField(
            "_relics",
            System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic);
        return relicsField?.GetValue(screen) is IReadOnlyList<RelicModel> relics
            ? NativeBossRelicDecisionProvider.Capture(screen, relics, entities)
            : new NativeBossRelicDecision(
                "capture_failed",
                "boss_relic_choice",
                false,
                Array.Empty<RelicModel>(),
                Array.Empty<NativeSemanticAction>(),
                new NativePlayerChoiceLineage(
                    "unavailable",
                    null,
                    null,
                    "The exact NChooseARelicSelection._relics binding is unavailable."),
                false,
                NativeBossRelicDecisionProvider.ScreenOwner,
                NativeBossRelicDecisionProvider.ParentCommand,
                NativeBossRelicDecisionProvider.CompletionSeam,
                NativeBossRelicDecisionProvider.CommitSeam,
                NativeBossRelicDecisionProvider.NextBoundary,
                "The exact NChooseARelicSelection._relics binding is unavailable.");
    }

    private static ProcessLocalUiCatalogObservation BuildUiCatalog(
        ProcessLocalNativeWitnessFrame frame,
        GameAction? observedAction)
    {
        ProcessLocalObservedAction? observed = TryDescribeForUi(observedAction);
        ProcessLocalNativeMatch? match = observed == null ? null : frame.Resolve(observed);
        var projection = frame.Snapshot.BoundActions;
        return new ProcessLocalUiCatalogObservation(
            frame.Snapshot.SnapshotId,
            frame.Snapshot.Status,
            frame.Snapshot.Interaction.Kind,
            projection.Status,
            projection.Actions.Count,
            StableIdentityHash.Object(projection.Actions.Select(action => new
            {
                action.Verb,
                action.SubjectReferentId,
                arguments = action.Arguments
                    .OrderBy(argument => argument.Role, StringComparer.Ordinal)
                    .Select(argument => new { argument.Role, argument.ReferentId })
                    .ToArray()
            }).ToArray()),
            match?.Status,
            match?.MatchCount);
    }

    private static IReadOnlyList<VisibleCombatPotionState> BuildSemanticPotionStates(
        Player player,
        NativeEntityRegistry entities,
        NativeCombatDecision decision)
    {
        var result = new List<VisibleCombatPotionState>();
        for (int slot = 0; slot < player.PotionSlots.Count; slot++)
        {
            PotionModel? potion = player.GetPotionAtSlotIndex(slot);
            if (potion == null)
                continue;
            bool automatic = potion.Usage == PotionUsage.Automatic;
            result.Add(new VisibleCombatPotionState(
                entities.GetId(potion, "potion"),
                potion.TargetType.ToString(),
                decision.Actions.Any(action =>
                    action.Verb == "use" && ReferenceEquals(action.NativeSubject, potion)),
                automatic));
        }
        return result;
    }

    private static ProcessLocalObservedSemanticAction ToLegacyObserved(
        NativeObservedSemanticAction observed) =>
        new(
            observed.NativeActionType,
            observed.Key,
            observed.Status,
            observed.MatchCount,
            observed.Membership,
            observed.Detail);

    private static ProcessLocalObservedAction? TryDescribeForUi(GameAction? action)
    {
        if (action is PlayCardAction play)
        {
            CardModel? card = play.NetCombatCard.ToCardModelOrNull();
            if (card == null)
                return null;
            IReadOnlyDictionary<string, object> arguments = play.Target == null
                ? new Dictionary<string, object>(StringComparer.Ordinal)
                : new Dictionary<string, object>(StringComparer.Ordinal) { ["target"] = play.Target };
            return new ProcessLocalObservedAction("play", card, arguments);
        }
        if (action is UsePotionAction use)
        {
            PotionModel? potion = use.Player.GetPotionAtSlotIndex((int)use.PotionIndex);
            if (potion == null)
                return null;
            Creature? target = NativePotionUseDecisionProvider.ResolveTarget(use, potion);
            IReadOnlyDictionary<string, object> arguments = target == null
                ? new Dictionary<string, object>(StringComparer.Ordinal)
                : new Dictionary<string, object>(StringComparer.Ordinal) { ["target"] = target };
            return new ProcessLocalObservedAction("use", potion, arguments);
        }
        return action is EndPlayerTurnAction
            ? new ProcessLocalObservedAction(
                "end_turn",
                null,
                new Dictionary<string, object>(StringComparer.Ordinal))
            : null;
    }

    private static string CatalogDigest(IReadOnlyList<ProcessLocalSemanticAction> actions) =>
        StableIdentityHash.Object(actions.Select(action => action.Key).Order(StringComparer.Ordinal).ToArray());

}
