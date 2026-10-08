using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading.Tasks;
using Godot;
using HarmonyLib;
using MegaCrit.Sts2.Core.Events;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.RestSite;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.GameActions.Multiplayer;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Potions;
using MegaCrit.Sts2.Core.Multiplayer.Game;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Potions;
using MegaCrit.Sts2.Core.Nodes.Relics;
using MegaCrit.Sts2.Core.Nodes.Rewards;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.TreasureRoomRelic;
using MegaCrit.Sts2.Core.Nodes.Screens.Shops;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.RestSite;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.PlayerEnvironment.Witness;
using STS2HumanAnnotator.Core;
using STS2Platform.NativeFoundation;

namespace STS2HumanAnnotator.Mod;

[HarmonyPatch]
internal static class NativeCardStartPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NPlayerHand), "StartCardPlay")
        ?? throw new MissingMethodException(typeof(NPlayerHand).FullName, "StartCardPlay");

    internal static void Prefix([HarmonyArgument(0)] NHandCardHolder holder, out IDisposable? __state) =>
        __state = RecorderRuntime.StageCardPlay(holder);

    private static Exception? Finalizer(IDisposable? __state, Exception? __exception) =>
        NativeNestedCallbackSafety.Finalize("card_start.scope", __exception, () => __state?.Dispose());
}

[HarmonyPatch]
internal static class NativeCardPlayFactoryPatch
{
    internal static IEnumerable<MethodBase> TargetMethods()
    {
        yield return AccessTools.Method(typeof(NMouseCardPlay), nameof(NMouseCardPlay.Create))
            ?? throw new MissingMethodException(typeof(NMouseCardPlay).FullName, nameof(NMouseCardPlay.Create));
        yield return AccessTools.Method(typeof(NControllerCardPlay), nameof(NControllerCardPlay.Create))
            ?? throw new MissingMethodException(typeof(NControllerCardPlay).FullName, nameof(NControllerCardPlay.Create));
    }

    private static void Postfix(NCardPlay __result)
    {
        if (__result != null)
            NativeNestedCallbackSafety.Run("card_play.factory", () => RecorderRuntime.BindStagedCardPlay(__result));
    }
}

[HarmonyPatch]
internal static class NativeCardPlayCleanupPatch
{
    internal static MethodBase TargetMethod() => AccessTools.Method(typeof(NCardPlay), "Cleanup")
        ?? throw new MissingMethodException(typeof(NCardPlay).FullName, "Cleanup");
    private static void Prefix(NCardPlay __instance) => RecorderRuntime.ForgetStagedCardPlay(__instance);
}

[HarmonyPatch]
internal static class NativeCardPlayPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NCardPlay), "TryPlayCard")
        ?? throw new MissingMethodException(typeof(NCardPlay).FullName, "TryPlayCard");

    private static void Prefix(
        NCardPlay __instance,
        [HarmonyArgument(0)] Creature? target,
        out NativeUiScopeEntry __state)
    {
        __state = __instance.Holder.CardModel is { } card
            ? RecorderRuntime.TryEnterCardScope(__instance, card, target)
            : default;
    }

    internal static Exception? Finalizer(NativeUiScopeEntry __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state);
        return __exception;
    }
}

[HarmonyPatch(typeof(NPotionHolder), nameof(NPotionHolder.UsePotion))]
internal static class NativePotionUseStartPatch
{
    internal static void Prefix(
        NPotionHolder __instance,
        out RecorderRuntime.PotionUseArmHandle? __state)
    {
        __state = RecorderRuntime.ArmPotionUse(__instance.Potion?.Model);
    }

    internal static void Postfix(
        RecorderRuntime.PotionUseArmHandle? __state,
        Task __result)
    {
        if (!__state.HasValue)
            return;
        if (__result == null)
        {
            RecorderRuntime.ClearPotionUseArm(__state);
            return;
        }
        _ = __result.ContinueWith(
            _ => RecorderRuntime.ClearPotionUseArm(__state),
            CancellationToken.None,
            TaskContinuationOptions.ExecuteSynchronously,
            TaskScheduler.Default);
    }
}

[HarmonyPatch(typeof(PotionModel), nameof(PotionModel.EnqueueManualUse))]
internal static class NativePotionEnqueuePatch
{
    internal static void Prefix(
        PotionModel __instance,
        [HarmonyArgument(0)] Creature? target,
        out NativeUiScopeEntry __state)
    {
        __state = RecorderRuntime.TryEnterPotionUseScope(__instance, target);
    }

    internal static Exception? Finalizer(NativeUiScopeEntry __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state);
        return __exception;
    }
}

[HarmonyPatch(typeof(NEndTurnButton), nameof(NEndTurnButton.CallReleaseLogic))]
internal static class NativeEndTurnPatch
{
    internal static void Prefix(out NativeUiScopeEntry __state)
    {
        __state = RecorderRuntime.TryEnterScope(
            "native_end_turn_ui",
            nameof(EndPlayerTurnAction),
            semanticSelection: new ProcessLocalObservedAction(
                "end_turn",
                null,
                new Dictionary<string, object>(StringComparer.Ordinal)));
    }

    internal static Exception? Finalizer(NativeUiScopeEntry __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state);
        return __exception;
    }
}

[HarmonyPatch(typeof(NEndTurnButton), nameof(NEndTurnButton.SecretEndTurnLogicViaFtue))]
internal static class NativeFtueEndTurnPatch
{
    internal static void Prefix(out NativeUiScopeEntry __state)
    {
        __state = RecorderRuntime.TryEnterScope(
            "native_ftue_end_turn_ui",
            nameof(EndPlayerTurnAction),
            semanticSelection: new ProcessLocalObservedAction(
                "end_turn",
                null,
                new Dictionary<string, object>(StringComparer.Ordinal)));
    }

    internal static Exception? Finalizer(NativeUiScopeEntry __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state);
        return __exception;
    }
}

// RequestEnqueue can return before OnEnqueued during an enemy turn. Bind
// the exact object while its native Human input scope still exists.
[HarmonyPatch(typeof(ActionQueueSynchronizer), nameof(ActionQueueSynchronizer.RequestEnqueue), new[] { typeof(GameAction) })]
internal static class NativeSubmittedHumanInputPatch
{
    private static void Prefix([HarmonyArgument(0)] GameAction action) =>
        NativeNestedCallbackSafety.Run("native_human_input.submitted",
            () => RecorderRuntime.BindSubmittedHumanInput(action));
}

[HarmonyPatch(typeof(GameAction), nameof(GameAction.OnEnqueued))]
internal static class AcceptedGameActionPatch
{
    // OnEnqueued has assigned the exact queue ID and state, while the caller has
    // not yet notified ActionExecutor. This is the earliest accepted-action seam
    // where no started/cancelled/finished lifecycle event can already be lost.
    internal static void Postfix(GameAction __instance) =>
        RecorderRuntime.ObserveAcceptedAction(__instance);
}

[HarmonyPatch(
    typeof(NCardPlayQueue),
    nameof(NCardPlayQueue.RemoveCardFromQueueForCancellation),
    new[] { typeof(PlayCardAction) })]
internal static class PlayCardExecutionAbortPatch
{
    // In exact v0.111.0 PlayCardAction returns without Cancel() when its card
    // is no longer in hand. This read-only seam distinguishes that native no-op
    // from a finished play; it does not change queue or card behavior.
    internal static void Prefix(PlayCardAction action)
    {
        if (action.State == MegaCrit.Sts2.Core.Entities.Actions.GameActionState.Executing)
            RecorderRuntime.ObservePlayCardExecutionAborted(action);
    }
}

[HarmonyPatch]
internal static class NativeGeneratedChoiceCardPatch
{
    private readonly record struct PatchState(
        NativeUiScopeEntry Scope,
        bool WasSelected,
        CardModel? Card);

    private static readonly AccessTools.FieldRef<NChooseACardSelectionScreen, bool>
        CardSelected = AccessTools.FieldRefAccess<NChooseACardSelectionScreen, bool>("_cardSelected");

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NChooseACardSelectionScreen), "SelectHolder")
        ?? throw new MissingMethodException(
            typeof(NChooseACardSelectionScreen).FullName,
            "SelectHolder");

    private static void Prefix(
        NChooseACardSelectionScreen __instance,
        [HarmonyArgument(0)] NCardHolder holder,
        out PatchState __state)
    {
        CardModel? card = holder.CardModel;
        __state = new PatchState(
            card != null ? RecorderRuntime.TryEnterGeneratedChoiceCardScope(__instance, holder) : default,
            CardSelected(__instance),
            card);
    }

    private static void Postfix(
        NChooseACardSelectionScreen __instance,
        PatchState __state)
    {
        if ((__state.Scope.Entered || __state.Scope.DeferredFailure)
            && !__state.WasSelected && CardSelected(__instance)
            && __state.Card is { } card)
            RecorderRuntime.ObserveGeneratedChoiceCard(card);
    }

    private static Exception? Finalizer(PatchState __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state.Scope);
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeGeneratedChoiceSkipPatch
{
    private readonly record struct PatchState(NativeUiScopeEntry Scope, bool WasComplete);

    private static readonly AccessTools.FieldRef<NChooseACardSelectionScreen, bool>
        ScreenComplete = AccessTools.FieldRefAccess<NChooseACardSelectionScreen, bool>("_screenComplete");

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NChooseACardSelectionScreen), "OnSkipButtonReleased")
        ?? throw new MissingMethodException(
            typeof(NChooseACardSelectionScreen).FullName,
            "OnSkipButtonReleased");

    private static void Prefix(
        NChooseACardSelectionScreen __instance,
        out PatchState __state)
    {
        __state = new PatchState(
            RecorderRuntime.TryEnterGeneratedChoiceSkipScope(__instance),
            ScreenComplete(__instance));
    }

    private static void Postfix(NChooseACardSelectionScreen __instance, PatchState __state)
    {
        if ((__state.Scope.Entered || __state.Scope.DeferredFailure)
            && !__state.WasComplete && ScreenComplete(__instance))
            RecorderRuntime.ObserveGeneratedChoiceSkip();
    }

    private static Exception? Finalizer(PatchState __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state.Scope);
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeBossRelicSelectionPatch
{
    private static readonly FieldInfo? RelicsField =
        typeof(NChooseARelicSelection).GetField(
            "_relics",
            BindingFlags.Instance | BindingFlags.NonPublic);
    private const string SelectNativeActionType = "NChooseARelicSelection.SelectHolder";
    private const string SkipNativeActionType = "NChooseARelicSelection.OnSkipButtonReleased";
    private const string CompletionFamily = "boss_relic_choice";

    private readonly record struct PatchState(
        NativeUiScopeEntry Scope,
        NChooseARelicSelection? Screen,
        RelicModel? Relic,
        bool IsSkip);

    internal static IEnumerable<MethodBase> TargetMethods()
    {
        yield return AccessTools.Method(
                   typeof(NChooseARelicSelection),
                   "SelectHolder",
                   new[] { typeof(NRelicBasicHolder) })
               ?? throw new MissingMethodException(
                   typeof(NChooseARelicSelection).FullName,
                   "SelectHolder");
        yield return AccessTools.Method(
                   typeof(NChooseARelicSelection),
                   "OnSkipButtonReleased",
                   new[] { typeof(NButton) })
               ?? throw new MissingMethodException(
                   typeof(NChooseARelicSelection).FullName,
                   "OnSkipButtonReleased");
    }

    private static void Prefix(
        MethodBase __originalMethod,
        NChooseARelicSelection __instance,
        out PatchState __state,
        [HarmonyArgument(0)] object control)
    {
        __state = default;
        try
        {
            bool isSkip = string.Equals(
                __originalMethod.Name,
                "OnSkipButtonReleased",
                StringComparison.Ordinal);
            RelicModel? relic = control is NRelicBasicHolder holder
                ? holder.Relic?.Model
                : null;
            string nativeActionType = isSkip ? SkipNativeActionType : SelectNativeActionType;
            ProcessLocalObservedAction observed = isSkip
                ? new ProcessLocalObservedAction(
                    "skip",
                    null,
                    new Dictionary<string, object>(StringComparer.Ordinal))
                : new ProcessLocalObservedAction(
                    "select",
                    relic,
                    new Dictionary<string, object>(StringComparer.Ordinal));
            ProcessLocalObservedAction semanticSelection = isSkip
                ? new ProcessLocalObservedAction(
                    "skip",
                    __instance,
                    new Dictionary<string, object>(StringComparer.Ordinal))
                : new ProcessLocalObservedAction(
                    "select",
                    relic,
                    new Dictionary<string, object>(StringComparer.Ordinal));
            __state = isSkip || relic != null
                ? new PatchState(
                    RecorderRuntime.TryEnterSemanticScope(
                        isSkip ? "native_boss_relic_skip_ui" : "native_boss_relic_select_ui",
                        nativeActionType,
                        observed,
                        new NativePostCommitCompletionExpectation(
                            CompletionFamily,
                            NativeBossRelicDecisionProvider.CommitSeam),
                        semanticSelection),
                    __instance,
                    relic,
                    isSkip)
                : default;
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("boss_relic.enter", exception);
        }
    }

    private static void Postfix(
        NChooseARelicSelection __instance,
        PatchState __state)
    {
        try
        {
            if ((!__state.Scope.Entered && !__state.Scope.DeferredFailure)
                || __state.Screen == null)
                return;

            bool isSkip = __state.IsSkip;
            string nativeActionType = isSkip ? SkipNativeActionType : SelectNativeActionType;
            ProcessLocalObservedAction observed = isSkip
                ? new ProcessLocalObservedAction(
                    "skip",
                    null,
                    new Dictionary<string, object>(StringComparer.Ordinal))
                : new ProcessLocalObservedAction(
                    "select",
                    __state.Relic,
                    new Dictionary<string, object>(StringComparer.Ordinal));
            var arguments = new Dictionary<string, string>(StringComparer.Ordinal)
            {
                ["screen"] = NativeWitnessIdentity.Get(__instance, "screen")
            };
            if (RelicsField?.GetValue(__instance) is not IReadOnlyList<RelicModel> nativeRelics)
            {
                FailCarrierClosed(
                    nativeActionType,
                    observed,
                    new NativeWitnessEvidence(
                        isSkip ? "native_boss_relic_skip_ui" : "native_boss_relic_select_ui",
                        nativeActionType,
                        __state.Relic == null
                            ? NativeWitnessIdentity.Get(__instance, "screen")
                            : NativeWitnessIdentity.Get(__state.Relic, "relic"),
                        new Dictionary<string, string>(StringComparer.Ordinal)
                        {
                            ["screen"] = NativeWitnessIdentity.Get(__instance, "screen")
                        },
                    DateTimeOffset.UtcNow),
                    "boss_relic_accepted_carrier_unavailable",
                    "The exact native relic option list is unavailable after the UI callback.",
                    null,
                    __state.Scope.ActionWitnessId);
                return;
            }
            if (!NativeBossRelicDecisionProvider.TryGetRegisteredChoiceCarrier(
                    nativeRelics,
                    out NativeBossRelicChoiceCarrier? carrier,
                    out string carrierDetail)
                || carrier?.ParentLineage.ParentAction == null)
            {
                FailCarrierClosed(
                    nativeActionType,
                    observed,
                    new NativeWitnessEvidence(
                        isSkip ? "native_boss_relic_skip_ui" : "native_boss_relic_select_ui",
                        nativeActionType,
                        __state.Relic == null
                            ? NativeWitnessIdentity.Get(__instance, "screen")
                            : NativeWitnessIdentity.Get(__state.Relic, "relic"),
                        new Dictionary<string, string>(StringComparer.Ordinal)
                        {
                            ["screen"] = NativeWitnessIdentity.Get(__instance, "screen")
                        },
                    DateTimeOffset.UtcNow),
                    "boss_relic_accepted_carrier_unavailable",
                    carrierDetail,
                    carrier,
                    __state.Scope.ActionWitnessId);
                return;
            }

            arguments["parent_action"] = NativeWitnessIdentity.Get(
                carrier.ParentLineage.ParentAction,
                "parent_action");
            bool accepted = RecorderRuntime.ObserveAcceptedSemanticUiAction(
                nativeActionType,
                observed,
                new NativeWitnessEvidence(
                    isSkip ? "native_boss_relic_skip_ui" : "native_boss_relic_select_ui",
                    nativeActionType,
                    __state.Relic == null
                        ? NativeWitnessIdentity.Get(__instance, "screen")
                        : NativeWitnessIdentity.Get(__state.Relic, "relic"),
                    arguments,
                    DateTimeOffset.UtcNow),
                captureImmediatePostCommitBoundary: false,
                actionWitnessId: __state.Scope.ActionWitnessId);
            if (accepted && __state.Scope.ActionWitnessId is { } actionWitnessId)
            {
                // The parent GameAction is the exact carrier through the async
                // RelicsSelected continuation to SyncLocalChoice. This is a
                // weak, one-shot binding, never a queue or latest-root heuristic.
                if (!NativeUiCompletionRootBindings.Remember(
                        carrier.ParentLineage.ParentAction,
                        actionWitnessId))
                {
                    bool durableFailure = RecorderRuntime.ObserveSemanticUiCarrierBindingFailure(
                        actionWitnessId,
                        nativeActionType,
                        "The exact boss-relic parent is already bound to a different child action.");
                    if (durableFailure)
                    {
                        NativeBossRelicDecisionProvider.ConsumeRegisteredChoice(
                            carrier.ParentLineage.ParentAction);
                    }
                }
            }
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("boss_relic.accepted", exception);
        }
    }

    private static void FailCarrierClosed(
        string nativeActionType,
        ProcessLocalObservedAction observed,
        NativeWitnessEvidence witness,
        string reason,
        string detail,
        NativeBossRelicChoiceCarrier? carrier,
        string? actionWitnessId)
    {
        try
        {
            // Durable failed-closed accounting always happens before carrier
            // cleanup. No accepted choice may disappear merely because its
            // async native continuation cannot be proved.
            RecorderRuntime.ObserveAcceptedSemanticUiFailure(
                nativeActionType,
                observed,
                witness,
                reason,
                detail);
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("boss_relic.carrier_failure", exception);
        }
        finally
        {
            try
            {
                // A parent is cleaned only when the provider returned the
                // exact verified carrier. If no carrier exists, there is no
                // safe identity with which to remove another root's binding.
                GameAction? parent = carrier?.ParentLineage.ParentAction;
                if (parent != null)
                {
                    NativeUiCompletionRootBindings.TakeIfMatches(parent, actionWitnessId);
                    NativeBossRelicDecisionProvider.ConsumeRegisteredChoice(parent);
                }
            }
            catch (Exception exception)
            {
                NativeUiObservationSafety.Report("boss_relic.carrier_cleanup", exception);
            }
        }
    }

    private static Exception? Finalizer(
        PatchState __state,
        Exception? __exception)
    {
        try
        {
            RecorderRuntime.ExitNativeUiScope(__state.Scope);
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("boss_relic.exit", exception);
        }
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeBossRelicCommitPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(PlayerChoiceSynchronizer),
            nameof(PlayerChoiceSynchronizer.SyncLocalChoice),
            new[] { typeof(Player), typeof(uint), typeof(PlayerChoiceResult) })
        ?? throw new MissingMethodException(
            typeof(PlayerChoiceSynchronizer).FullName,
            nameof(PlayerChoiceSynchronizer.SyncLocalChoice));

    private static void Postfix(PlayerChoiceSynchronizer __instance)
    {
        try
        {
            if (!NativeBossRelicDecisionProvider.TryGetRegisteredCurrentChoiceCarrier(
                    out NativeBossRelicChoiceCarrier? carrier,
                    out GameAction? currentParent,
                    out string carrierDetail))
            {
                string? pendingActionWitnessId = currentParent == null
                    ? null
                    : NativeUiCompletionRootBindings.TryGet(
                        currentParent,
                        out string? witness)
                        ? witness
                        : null;
                RecorderRuntime.ObserveSemanticUiNativeCommitBindingFailure(
                    pendingActionWitnessId,
                    "boss_relic_choice",
                    NativeBossRelicDecisionProvider.CommitSeam,
                    carrierDetail);
                // No exact Commit was persisted. Retain the parent-bound
                // registration and root carrier for lifecycle/Close audit.
                return;
            }
            if (carrier?.ParentLineage.ParentAction == null)
                return;
            object parent = carrier.ParentLineage.ParentAction;
            NativeUiCompletionRootBindings.TryGet(parent, out string? actionWitnessId);
            if (actionWitnessId == null)
            {
                RecorderRuntime.ObserveSemanticUiNativeCommitBindingFailure(
                    null,
                    "boss_relic_choice",
                    NativeBossRelicDecisionProvider.CommitSeam,
                    "The exact Human root binding is unavailable at boss relic Commit.");
                return;
            }
            bool durableCommit = RecorderRuntime.ObserveSemanticUiNativeCommit(
                actionWitnessId,
                "boss_relic_choice",
                NativeBossRelicDecisionProvider.CommitSeam,
                nativeOwner: __instance,
                nativeLineage: parent);
            if (durableCommit)
            {
                NativeUiCompletionRootBindings.TakeIfMatches(parent, actionWitnessId);
                NativeBossRelicDecisionProvider.ConsumeRegisteredChoice(
                    (GameAction)parent);
            }
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("boss_relic.commit", exception);
        }
    }
}

[HarmonyPatch]
internal static class NativeCombatHandSelectPatch
{
    internal static IEnumerable<MethodBase> TargetMethods()
    {
        yield return AccessTools.Method(typeof(NPlayerHand), "SelectCardInSimpleMode")
            ?? throw new MissingMethodException(typeof(NPlayerHand).FullName, "SelectCardInSimpleMode");
        yield return AccessTools.Method(typeof(NPlayerHand), "SelectCardInUpgradeMode")
            ?? throw new MissingMethodException(typeof(NPlayerHand).FullName, "SelectCardInUpgradeMode");
    }

    private static void Prefix(
        MethodBase __originalMethod,
        [HarmonyArgument(0)] NHandCardHolder holder,
        out NativeUiScopeEntry __state)
    {
        string nativeActionType = $"NPlayerHand.{__originalMethod.Name}";
        __state = holder.CardModel is { } card
            ? RecorderRuntime.TryEnterSemanticScope(
                "native_combat_hand_selection_ui",
                nativeActionType,
                new ProcessLocalObservedAction(
                    "select",
                    card,
                    new Dictionary<string, object>(StringComparer.Ordinal)))
            : default;
    }

    private static void Postfix(
        MethodBase __originalMethod,
        [HarmonyArgument(0)] NHandCardHolder holder,
        NativeUiScopeEntry __state)
    {
        if ((!__state.Entered && !__state.DeferredFailure) || holder.CardModel is not { } card)
            return;
        string nativeActionType = $"NPlayerHand.{__originalMethod.Name}";
        RecorderRuntime.ObserveAcceptedSemanticUiAction(
            nativeActionType,
            new ProcessLocalObservedAction(
                "select",
                card,
                new Dictionary<string, object>(StringComparer.Ordinal)),
            new NativeWitnessEvidence(
                "native_combat_hand_selection_ui",
                nativeActionType,
                NativeWitnessIdentity.Get(card, "card"),
                new Dictionary<string, string>(StringComparer.Ordinal),
                DateTimeOffset.UtcNow));
    }

    private static Exception? Finalizer(NativeUiScopeEntry __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state);
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeCombatHandDeselectPatch
{
    internal sealed record State(RecorderRuntime.SelectorInput? Input, NPlayerHand Hand, object[] Selected);
    internal static IEnumerable<MethodBase> TargetMethods()
    {
        yield return AccessTools.Method(typeof(NSelectedHandCardContainer), "DeselectHolder");
        yield return AccessTools.Method(typeof(MegaCrit.Sts2.Core.Nodes.Cards.NUpgradePreview), "ReturnCard");
    }
    [HarmonyPriority(Priority.First)]
    private static void Prefix(object __instance, NCardHolder __0, MethodBase __originalMethod, out State? __state)
    {
        __state = null;
        if (NativeAutomaticHandDeselectScopePatch.Active || RecorderRuntime.SelectorInputActive)
            return;
        __state = NativeNestedCallbackSafety.Run("selector.deselect.before", () =>
        {
            NPlayerHand? hand = (__instance as NSelectedHandCardContainer)?.Hand;
            if (__instance is MegaCrit.Sts2.Core.Nodes.Cards.NUpgradePreview preview)
            {
                hand = MegaCrit.Sts2.Core.Nodes.Rooms.NCombatRoom.Instance?.Ui.Hand;
                if (hand == null || !ReferenceEquals(NativeSelectorInputFacts.Field(hand, "_upgradePreview"), preview))
                    return null;
            }
            if (hand == null) return null;
            var selected = NativeSelectorInputFacts.Selected(hand);
            return new State(RecorderRuntime.BeginSelectorInput(hand,
                $"{__originalMethod.DeclaringType!.FullName}.{__originalMethod.Name}",
                __0.CardModel, "deselect_combat_hand_card"), hand, selected);
        }, null);
    }
    [HarmonyPriority(Priority.Last)]
    private static void Postfix(State? __state)
    {
        if (__state != null)
            NativeNestedCallbackSafety.Run("selector.deselect.after", () =>
                RecorderRuntime.EndSelectorInput(__state.Input,
                    NativeSelectorInputFacts.Changed(__state.Hand, __state.Selected), false));
    }
    private static Exception? Finalizer(State? __state, Exception? __exception) =>
        NativeNestedCallbackSafety.Finalize("selector.deselect.finally", __exception, () =>
        { if (__state != null && __exception != null) RecorderRuntime.EndSelectorInput(__state.Input, false, false); });
}

[HarmonyPatch]
internal static class NativeCombatHandConfirmPatch
{
    private const string NativeActionType = "NPlayerHand.OnSelectModeConfirmButtonPressed";

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NPlayerHand), "OnSelectModeConfirmButtonPressed")
        ?? throw new MissingMethodException(
            typeof(NPlayerHand).FullName,
            "OnSelectModeConfirmButtonPressed");

    private static void Prefix(out NativeUiScopeEntry __state)
    {
        __state = RecorderRuntime.TryEnterSemanticScope(
            "native_combat_hand_confirm_ui",
            NativeActionType,
            new ProcessLocalObservedAction(
                "confirm",
                null,
                new Dictionary<string, object>(StringComparer.Ordinal)));
    }

    private static void Postfix(NativeUiScopeEntry __state)
    {
        if (!__state.Entered && !__state.DeferredFailure)
            return;
        RecorderRuntime.ObserveAcceptedSemanticUiAction(
            NativeActionType,
            new ProcessLocalObservedAction(
                "confirm",
                null,
                new Dictionary<string, object>(StringComparer.Ordinal)),
            new NativeWitnessEvidence(
                "native_combat_hand_confirm_ui",
                NativeActionType,
                null,
                new Dictionary<string, string>(StringComparer.Ordinal),
                DateTimeOffset.UtcNow));
    }

    private static Exception? Finalizer(NativeUiScopeEntry __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state);
        return __exception;
    }
}

[HarmonyPatch(typeof(NMapScreen), nameof(NMapScreen.OnMapPointSelectedLocally))]
internal static class NativeMapChoicePatch
{
    private static void Prefix(
        [HarmonyArgument(0)] NMapPoint point,
        out NativeUiScopeEntry __state)
    {
        __state = RecorderRuntime.TryEnterSemanticScope(
            "native_map_choice_ui",
            nameof(VoteForMapCoordAction),
            new ProcessLocalObservedAction(
                "activate",
                point.Point,
                new Dictionary<string, object>(StringComparer.Ordinal)),
            nativeSemanticSelection: new ProcessLocalObservedAction(
                "travel",
                point.Point,
                new Dictionary<string, object>(StringComparer.Ordinal)));
    }

    private static Exception? Finalizer(NativeUiScopeEntry __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state);
        return __exception;
    }
}

internal static class NativeTreasureUiContext
{
    // These observed actions must match the public Player Environment
    // projection. The native provider keeps TreasureRoom as its semantic
    // owner, but the public projection hides room-owner bindings from the
    // player action and maps choose/proceed operations to "activate".
    internal static TreasureRoom? CurrentRoom() =>
        RunManager.Instance.DebugOnlyGetState()?.CurrentRoom as TreasureRoom;

    internal static NTreasureRoom? CurrentUi() => NRun.Instance?.TreasureRoom;
}

internal static class NativeRewardUiContext
{
    private static readonly FieldInfo IsTerminalField =
        AccessTools.Field(typeof(NRewardsScreen), "_isTerminal")
        ?? throw new MissingFieldException(typeof(NRewardsScreen).FullName, "_isTerminal");

    internal static RewardsSet? CurrentRewardsSet() =>
        NOverlayStack.Instance?.Peek() is NRewardsScreen screen
            ? NativeRewardDecisionProvider.ObserveOwner(screen)?.RewardsSet
            : null;

    internal static bool IsActChangeReady(NRewardsScreen screen)
    {
        if (IsTerminalField.GetValue(screen) is not true
            || RunManager.Instance.debugAfterCombatRewardsOverride != null)
            return false;
        RunState? runState = RunManager.Instance.DebugOnlyGetState();
        if (runState?.CurrentRoom is not { } room
            || (room.RoomType != RoomType.Boss && !room.IsVictoryRoom))
            return false;

        // Exact v0.111.0 terminal branch: the second-boss map point uses the
        // ordinary terminal continuation; every other boss/victory branch
        // enqueues VoteToMoveToNextActAction.
        return runState.Map.SecondBossMapPoint == null
            || runState.CurrentMapCoord != runState.Map.BossMapPoint.coord;
    }
}

/// <summary>
/// Carries the exact Human root through a native UI callback to the Task
/// returned by that callback's game-owned operation. The key is the exact
/// native owner object, never a queue position, timestamp, or ambient current
/// root. Entries are weak and consumed once the operation is bound.
/// </summary>
internal static class NativeUiCompletionRootBindings
{
    private static readonly ExactOwnerWitnessBindingTable<object> Bindings = new();

    internal static bool Remember(object? owner, string? actionWitnessId)
    {
        return Bindings.TryBind(owner, actionWitnessId);
    }

    internal static bool RememberOrFailClosed(
        object? owner,
        string? actionWitnessId,
        string nativeActionType,
        string detail)
    {
        if (Remember(owner, actionWitnessId))
            return true;
        if (!string.IsNullOrWhiteSpace(actionWitnessId))
        {
            RecorderRuntime.ObserveSemanticUiCarrierBindingFailure(
                actionWitnessId,
                nativeActionType,
                detail);
        }
        else
        {
            NativeUiObservationSafety.Report(
                "native_completion_root.remember",
                detail + " Exact action witness unavailable.");
        }
        return false;
    }

    internal static string? Take(object? owner)
    {
        return Bindings.Take(owner);
    }

    internal static bool TakeIfMatches(object? owner, string? expectedActionWitnessId)
    {
        return Bindings.TakeIfMatches(owner, expectedActionWitnessId);
    }

    internal static bool Transfer(
        object? source,
        object? destination,
        string? expectedActionWitnessId) =>
        Bindings.TryTransfer(source, destination, expectedActionWitnessId);

    internal static bool TransferRewardScreenToAction(GameAction action, string actionWitnessId)
    {
        // The reward Prefix already owns this witness. Move that exact screen
        // carrier; rebinding the same witness to a second owner must fail.
        return Bindings.TryGetOwner(actionWitnessId, out object? owner)
            && owner is NRewardsScreen
            && Bindings.TryTransfer(owner, action, actionWitnessId);
    }

    internal static bool Contains(object? owner) =>
        Bindings.Contains(owner);

    internal static bool TryGet(object? owner, out string? actionWitnessId)
    {
        return Bindings.TryGetWitness(owner, out actionWitnessId);
    }

    internal static void ReleaseUnclaimed(string actionWitnessId)
    {
        if (Bindings.TryGetOwner(actionWitnessId, out object? owner))
            Bindings.TakeIfMatches(owner, actionWitnessId);
    }

    internal static bool TryGetAction(
        string actionWitnessId,
        out GameAction? action)
    {
        action = null;
        if (string.IsNullOrWhiteSpace(actionWitnessId))
            return false;
        if (!Bindings.TryGetOwner(actionWitnessId, out object? owner)
            || owner is not GameAction exactAction)
            return false;
        action = exactAction;
        return true;
    }

    internal static object? CurrentRewardOrTreasureOwner()
    {
        object? overlay = NOverlayStack.Instance?.Peek();
        if (Contains(overlay))
            return overlay;
        object? treasure = NativeTreasureUiContext.CurrentUi();
        return Contains(treasure) ? treasure : null;
    }
}

/// <summary>
/// Native callbacks must never let persistence/identity failures escape into
/// STS2. Logging is itself isolated because Godot teardown can make logging
/// unavailable while an overlay is closing.
/// </summary>
internal static class NativeUiObservationSafety
{
    internal static void Report(string seam, Exception exception)
    {
        try
        {
            GD.PrintErr(
                $"[STS2 Human Annotator] native observation failed at {seam}: "
                + $"{exception.GetType().Name}: {exception.Message}");
        }
        catch
        {
            // Never throw from a native Harmony callback's diagnostic path.
        }
    }

    internal static void Report(string seam, string detail)
    {
        try
        {
            GD.PrintErr($"[STS2 Human Annotator] native observation unavailable at {seam}: {detail}");
        }
        catch
        {
            // Never throw from a native Harmony callback's diagnostic path.
        }
    }
}

[HarmonyPatch]
internal static class NativeTreasureChestChoicePatch
{
    private const string NativeActionType = "NTreasureRoom.OnChestButtonReleased";

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(NTreasureRoom),
            "OnChestButtonReleased",
            new[] { typeof(NButton) })
        ?? throw new MissingMethodException(
            typeof(NTreasureRoom).FullName,
            "OnChestButtonReleased");

    private static void Prefix(NTreasureRoom __instance, out NativeUiScopeEntry __state)
    {
        TreasureRoom? room = NativeTreasureUiContext.CurrentRoom();
        __state = room == null
            ? default
            : RecorderRuntime.TryEnterSemanticScope(
                "native_treasure_chest_ui",
                NativeActionType,
                new ProcessLocalObservedAction(
                    "open",
                    null,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                new NativePostCommitCompletionExpectation(
                    "treasure_open",
                    "OneOffSynchronizer.DoLocalTreasureRoomRewards",
                    NativeOperandWitnessId: NativeWitnessIdentity.Get(room, "native_operand")),
                nativeSemanticSelection: new ProcessLocalObservedAction(
                    "open",
                    room,
                    new Dictionary<string, object>(StringComparer.Ordinal)));        if (__state.Entered)
            __state = __state with { CarrierBindingFailed = !NativeUiCompletionRootBindings.Remember(__instance, __state.ActionWitnessId) };
    }

    private static void Postfix(NTreasureRoom __instance, NativeUiScopeEntry __state)
    {
        try
        {
            TreasureRoom? room = NativeTreasureUiContext.CurrentRoom();
            if ((!__state.Entered && !__state.DeferredFailure) || room == null)
                return;
            bool accepted = RecorderRuntime.ObserveAcceptedSemanticUiAction(
                NativeActionType,
                new ProcessLocalObservedAction(
                    "open",
                    null,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                new NativeWitnessEvidence(
                    "native_treasure_chest_ui",
                    NativeActionType,
                    NativeWitnessIdentity.Get(room, "treasure_room"),
                    new Dictionary<string, string>(StringComparer.Ordinal),
                    DateTimeOffset.UtcNow),
                captureImmediatePostCommitBoundary: false,
                actionWitnessId: __state.ActionWitnessId);
            if (!accepted)
                NativeUiCompletionRootBindings.TakeIfMatches(__instance, __state.ActionWitnessId);
            else if (__state.CarrierBindingFailed)
                RecorderRuntime.ObserveSemanticUiCarrierBindingFailure(__state.ActionWitnessId!, NativeActionType,
                    "Exact treasure UI owner was already bound before native execution.");
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("treasure_chest.accepted", exception);
        }
    }

    private static Exception? Finalizer(NativeUiScopeEntry __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state);
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeTreasureRelicChoicePatch
{
    private const string NativeActionType = nameof(PickRelicAction);

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(NTreasureRoomRelicCollection),
            "PickRelic",
            new[] { typeof(NTreasureRoomRelicHolder) })
        ?? throw new MissingMethodException(
            typeof(NTreasureRoomRelicCollection).FullName,
            "PickRelic");

    private readonly record struct PatchState(
        NativeUiScopeEntry Scope,
        RelicModel? Relic);

    private static void Prefix(
        [HarmonyArgument(0)] NTreasureRoomRelicHolder holder,
        out PatchState __state)
    {
        RelicModel? relic = holder.Relic?.Model;
        __state = new PatchState(
            relic == null
                ? default
                : RecorderRuntime.TryEnterSemanticScope(
                    "native_treasure_relic_ui",
                    NativeActionType,
                    new ProcessLocalObservedAction(
                        "activate",
                        relic,
                        new Dictionary<string, object>(StringComparer.Ordinal)),
                    nativeSemanticSelection: new ProcessLocalObservedAction(
                        "select",
                        relic,
                        new Dictionary<string, object>(StringComparer.Ordinal))),
            relic);
    }

    private static Exception? Finalizer(PatchState __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state.Scope);
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeTreasureProceedPatch
{
    private const string NativeActionType = "NTreasureRoom.OnProceedButtonPressed";

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(NTreasureRoom),
            "OnProceedButtonPressed",
            new[] { typeof(NButton) })
        ?? throw new MissingMethodException(
            typeof(NTreasureRoom).FullName,
            "OnProceedButtonPressed");

    private readonly record struct PatchState(
        NativeUiScopeEntry Scope,
        TreasureRoom? Room,
        string? Verb,
        bool IsGameAction);

    private static void Prefix(
        NTreasureRoom __instance,
        [HarmonyArgument(0)] NButton button,
        out PatchState __state)
    {
        TreasureRoom? room = NativeTreasureUiContext.CurrentRoom();
        NTreasureRoom? uiRoom = NativeTreasureUiContext.CurrentUi();
        if (room == null || uiRoom == null || !ReferenceEquals(uiRoom, __instance)
            || button is not NProceedButton proceed)
        {
            __state = default;
            return;
        }

        bool isGameAction = proceed.IsSkip;
        string verb = isGameAction ? "skip" : "activate";
        string expectedNativeActionType = isGameAction
            ? nameof(PickRelicAction)
            : NativeActionType;
        __state = new PatchState(
            RecorderRuntime.TryEnterSemanticScope(
                "native_treasure_proceed_ui",
                expectedNativeActionType,
                new ProcessLocalObservedAction(
                    verb,
                    null,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                isGameAction
                    ? null
                    : new NativePostCommitCompletionExpectation(
                        "treasure_proceed",
                        "RunManager.ProceedFromTerminalRewardsScreen",
                        NativeOperandWitnessId: NativeWitnessIdentity.Get(room, "native_operand")),
                nativeSemanticSelection: new ProcessLocalObservedAction(
                    isGameAction ? "skip" : "proceed",
                    room,
                    new Dictionary<string, object>(StringComparer.Ordinal))),
            room,
            verb,
            isGameAction);
        if (__state.Scope.Entered && !isGameAction)
            __state = __state with { Scope = __state.Scope with { CarrierBindingFailed = !NativeUiCompletionRootBindings.Remember(__instance, __state.Scope.ActionWitnessId) } };
    }

    private static void Postfix(
        NTreasureRoom __instance,
        PatchState __state)
    {
        try
        {
            if ((!__state.Scope.Entered && !__state.Scope.DeferredFailure)
                || __state.Room is not { } room
                || __state.Verb is not { } verb
                || __state.IsGameAction)
                return;
            bool accepted = RecorderRuntime.ObserveAcceptedSemanticUiAction(
                NativeActionType,
                new ProcessLocalObservedAction(
                    verb,
                    null,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                new NativeWitnessEvidence(
                    "native_treasure_proceed_ui",
                    NativeActionType,
                    NativeWitnessIdentity.Get(__instance.ProceedButton, "proceed_button"),
                    new Dictionary<string, string>(StringComparer.Ordinal),
                    DateTimeOffset.UtcNow),
                captureImmediatePostCommitBoundary: false,
                actionWitnessId: __state.Scope.ActionWitnessId);
            if (accepted && __state.Scope.CarrierBindingFailed)
                RecorderRuntime.ObserveSemanticUiCarrierBindingFailure(__state.Scope.ActionWitnessId!, NativeActionType,
                    "Exact treasure owner was already bound before native execution.");
            // Owner was staged before native execution and may already have
            // transferred to its exact Task. Never reinsert a consumed binding.
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("treasure_proceed.accepted", exception);
        }
    }

    private static Exception? Finalizer(PatchState __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state.Scope);
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeTreasureNormalRewardsPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(OneOffSynchronizer),
            "DoLocalTreasureRoomRewards")
        ?? throw new MissingMethodException(
            typeof(OneOffSynchronizer).FullName,
            "DoLocalTreasureRoomRewards");

    private static void Postfix(OneOffSynchronizer __instance, Task<int> __result) =>
        RecorderRuntime.QueueNativePostCommitBoundary(
            __result,
            "OneOffSynchronizer.DoLocalTreasureRoomRewards",
            nativeOwner: __instance,
            nativeOperand: NativeTreasureUiContext.CurrentRoom(),
            completionRootOwner: NativeTreasureUiContext.CurrentUi());
}

[HarmonyPatch]
internal static class NativeTreasureProceedCompletionPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(RunManager),
            "ProceedFromTerminalRewardsScreen")
        ?? throw new MissingMethodException(
            typeof(RunManager).FullName,
            "ProceedFromTerminalRewardsScreen");

    private sealed record OwnerState(object? Owner, object? Operand, NMapScreen? Map, bool MapWasOpen);
    private static void Prefix(out OwnerState __state) =>
        __state = new(NativeUiCompletionRootBindings.CurrentRewardOrTreasureOwner(),
            NOverlayStack.Instance?.Peek() is NRewardsScreen
                ? NativeRewardUiContext.CurrentRewardsSet()
                : NativeTreasureUiContext.CurrentRoom(), NMapScreen.Instance, NMapScreen.Instance?.IsOpen == true);

    private static void Postfix(RunManager __instance, Task __result, OwnerState __state)
    {
        // Proceed's ordinary terminal-reward branch opens the map synchronously.
        // Observe Commit and the distinct input-owner boundary before returning
        // to Human input, not after a frame-loop completion transport delay.
        if (NativeUiCompletionRootBindings.TryGet(__state.Owner, out string? root)
            && root != null && HumanActionScope.Current is { } context
            && __state.Map is { } map
            && NativeSynchronousOwnerHandoff.Matches(__result, map, __state.MapWasOpen,
                NMapScreen.Instance, map.IsOpen, root, context.ActionWitnessId)
            && context.CompletionExpectation is { } completion
            && completion.AcceptsKind("RunManager.ProceedFromTerminalRewardsScreen"))
        {
            if (RecorderRuntime.ObserveSemanticUiNativeCommit(root, completion.Family,
                "RunManager.ProceedFromTerminalRewardsScreen", __instance, __state.Operand))
            {
                NativeUiCompletionRootBindings.TakeIfMatches(__state.Owner, root);
                NativeDecisionOwnerReadyProvider.ObserveMapProceedReady(map);
            }
            return;
        }
        RecorderRuntime.QueueNativePostCommitBoundary(
            __result,
            "RunManager.ProceedFromTerminalRewardsScreen",
            nativeOwner: __instance,
            nativeOperand: __state.Operand,
            completionRootOwner: __state.Owner);
    }
}

[HarmonyPatch]
internal static class NativeRewardClaimStartPatch
{
    private const string NativeActionType = "NRewardButton.OnRelease";

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NRewardButton), "OnRelease")
        ?? throw new MissingMethodException(typeof(NRewardButton).FullName, "OnRelease");

    private static void Prefix(NRewardButton __instance, out NativeUiScopeEntry __state)
    {
        __state = __instance.Reward == null
            ? default
            : RecorderRuntime.TryEnterSemanticScope(
                "native_reward_claim_ui",
                NativeActionType,
                new ProcessLocalObservedAction(
                    "activate",
                    __instance.Reward,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                RewardClaimCompletion(__instance.Reward),
                new ProcessLocalObservedAction(
                    "claim",
                    __instance.Reward,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                nestedInputOwner: NOverlayStack.Instance?.Peek() as NRewardsScreen);
        if (__state.Entered && __instance.Reward != null)
            __state = __state with { CarrierBindingFailed = !NativeUiCompletionRootBindings.Remember(__instance.Reward, __state.ActionWitnessId) };
    }

    private static NativePostCommitCompletionExpectation RewardClaimCompletion(Reward reward) =>
        new(
            "reward_claim",
            reward is CardReward
                ? "NCardRewardSelectionScreen.ShowScreen"
                : "RewardsSetSynchronizer.SelectLocalReward",
            NativeOperandWitnessId: NativeWitnessIdentity.Get(reward, "native_operand"));

    private static void Postfix(NRewardButton __instance, NativeUiScopeEntry __state)
    {
        try
        {
            if ((!__state.Entered && !__state.DeferredFailure) || __instance.Reward == null)
                return;
            var outcome = __state.Entered ? HumanActionScope.Current?.NativeAttemptOutcome
                : HumanActionScope.CurrentDeferredFailure?.NativeAttemptOutcome;
            if (outcome != null && ReferenceEquals(outcome.Operand, __instance.Reward)
                && outcome.Result.IsCompletedSuccessfully && !outcome.Result.Result)
            {
                RecorderRuntime.ObserveRejectedNativeUiInput(NativeActionType, __instance.Reward);
                NativeUiCompletionRootBindings.TakeIfMatches(__instance.Reward, __state.ActionWitnessId);
                return;
            }
            bool accepted = RecorderRuntime.ObserveAcceptedSemanticUiAction(
                NativeActionType,
                new ProcessLocalObservedAction(
                    "activate",
                    __instance.Reward,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                new NativeWitnessEvidence(
                    "native_reward_claim_ui",
                    NativeActionType,
                    NativeWitnessIdentity.Get(__instance, "reward_button"),
                    new Dictionary<string, string>(StringComparer.Ordinal),
                    DateTimeOffset.UtcNow),
                captureImmediatePostCommitBoundary: false,
                actionWitnessId: __state.ActionWitnessId);
            if (!accepted)
            {
                NativeUiCompletionRootBindings.TakeIfMatches(__instance.Reward, __state.ActionWitnessId);
                return;
            }
            if (__state.CarrierBindingFailed)
            {
                RecorderRuntime.ObserveSemanticUiCarrierBindingFailure(__state.ActionWitnessId!, NativeActionType,
                    "Exact reward owner was already bound before native execution.");
                return;
            }
            if (__instance.Reward is CardReward reward
                && __state.ActionWitnessId is { } actionWitnessId
                && NOverlayStack.Instance?.Peek() is NCardRewardSelectionScreen screen)
            {
                if (RecorderRuntime.ObserveSemanticUiNativeCommit(
                    actionWitnessId,
                    "reward_claim",
                    "NCardRewardSelectionScreen.ShowScreen",
                    nativeOwner: screen,
                    nativeOperand: reward))
                    NativeUiCompletionRootBindings.TakeIfMatches(reward, actionWitnessId);
            }
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("reward_claim.accepted", exception);
        }
    }

    private static Exception? Finalizer(NativeUiScopeEntry __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state);
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeRewardProceedPatch
{
    internal const string NativeActionType = "NRewardsScreen.OnProceedButtonPressed";
    internal const string ActChangeNativeActionType =
        "NRewardsScreen.OnProceedButtonPressed.act_change_ready";

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NRewardsScreen), "OnProceedButtonPressed")
        ?? throw new MissingMethodException(typeof(NRewardsScreen).FullName, "OnProceedButtonPressed");

    private readonly record struct PatchState(
        NativeUiScopeEntry Scope,
        string NativeActionType);

    private static void Prefix(NRewardsScreen __instance, out PatchState __state)
    {
        __state = default;
        try
        {
            NativeRewardDecisionOwner? owner =
                NativeRewardDecisionProvider.ObserveOwner(__instance);
            bool isActChangeReady = NativeRewardUiContext.IsActChangeReady(__instance);
            string nativeActionType = isActChangeReady
                ? ActChangeNativeActionType
                : NativeActionType;
            NativePostCommitCompletionExpectation completion = isActChangeReady
                ? new NativePostCommitCompletionExpectation(
                    "act_change.ready",
                    NativeActChangeDecisionProvider.CommitSeam)
                : new NativePostCommitCompletionExpectation(
                    "reward_proceed",
                    "RunManager.ProceedFromTerminalRewardsScreen",
                    NativeOperandWitnessId: owner == null
                        ? null
                        : NativeWitnessIdentity.Get(owner.RewardsSet, "native_operand"),
                    AlternativeKinds: new[]
                    {
                        "RewardsSetSynchronizer.SkipLocalRewardsSet"
                    });
            __state = new PatchState(RecorderRuntime.TryEnterSemanticScope(
                "native_reward_proceed_ui",
                nativeActionType,
                new ProcessLocalObservedAction(
                    "activate",
                    null,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                completion,
                nativeSemanticSelection: owner == null
                    ? null
                    : new ProcessLocalObservedAction(
                        "proceed",
                        owner.RewardsSet,
                        new Dictionary<string, object>(StringComparer.Ordinal)),
                nestedInputOwner: __instance),
                nativeActionType);
            if (__state.Scope.Entered)
                __state = __state with { Scope = __state.Scope with {
                    CarrierBindingFailed = !NativeUiCompletionRootBindings.Remember(__instance, __state.Scope.ActionWitnessId) } };
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("reward_proceed.enter", exception);
        }
    }

    private static void Postfix(NRewardsScreen __instance, PatchState __state)
    {
        try
        {
            if (!__state.Scope.Entered && !__state.Scope.DeferredFailure)
                return;
            bool accepted = RecorderRuntime.ObserveAcceptedSemanticUiAction(
                __state.NativeActionType,
                new ProcessLocalObservedAction(
                    "activate",
                    null,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                new NativeWitnessEvidence(
                    "native_reward_proceed_ui",
                    __state.NativeActionType,
                    null,
                    new Dictionary<string, string>(StringComparer.Ordinal),
                    DateTimeOffset.UtcNow),
                captureImmediatePostCommitBoundary: false,
                actionWitnessId: __state.Scope.ActionWitnessId);
            if (!accepted)
                NativeUiCompletionRootBindings.TakeIfMatches(__instance, __state.Scope.ActionWitnessId);
            else if (__state.Scope.CarrierBindingFailed)
                RecorderRuntime.ObserveSemanticUiCarrierBindingFailure(__state.Scope.ActionWitnessId!, __state.NativeActionType,
                    "Exact rewards screen was already bound before native execution.");
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("reward_proceed.accepted", exception);
        }
    }

    private static Exception? Finalizer(PatchState __state, Exception? __exception)
    {
        try
        {
            RecorderRuntime.ExitNativeUiScope(__state.Scope);
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("reward_proceed.exit", exception);
        }
        return __exception;
    }
}

/// <summary>
/// Every native potion popup has its own discard decision, in any room. Its discard button is the Human
/// input; the popup enqueues the exact DiscardPotionGameAction, whose
/// ExecuteAction is the native mutation owner. The action object is bound in
/// RequestEnqueue Prefix because GameAction.OnEnqueued runs inside that
/// method, before a Postfix can observe it.
/// </summary>
[HarmonyPatch]
internal static class NativeRewardPotionDiscardPatch
{
    internal const string NativeActionType = "NPotionPopup.OnDiscardButtonPressed";
    internal const string CompletionFamily = "potion_belt.discard";

    private static readonly FieldInfo? HolderField =
        typeof(NPotionPopup).GetField(
            "_holder",
            BindingFlags.Instance | BindingFlags.NonPublic);

    private readonly record struct PatchState(
        NativeUiScopeEntry Scope,
        PotionModel? Potion,
        NPotionPopup? Popup);

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(NPotionPopup),
            "OnDiscardButtonPressed",
            new[] { typeof(NButton) })
        ?? throw new MissingMethodException(
            typeof(NPotionPopup).FullName,
            "OnDiscardButtonPressed");

    private static void Prefix(
        NPotionPopup __instance,
        out PatchState __state)
    {
        __state = default;
        try
        {
            NPotionHolder? holder = HolderField?.GetValue(__instance) as NPotionHolder;
            PotionModel? potion = holder?.Potion?.Model;
            if (potion == null)
                return;

            __state = new PatchState(
                RecorderRuntime.TryEnterSemanticScope(
                    "native_reward_potion_discard_ui",
                    NativeActionType,
                    new ProcessLocalObservedAction(
                        "activate",
                        potion,
                        new Dictionary<string, object>(StringComparer.Ordinal)),
                    new NativePostCommitCompletionExpectation(
                        CompletionFamily,
                        "DiscardPotionGameAction.ExecuteAction"),
                    new ProcessLocalObservedAction(
                        "activate",
                        potion,
                        new Dictionary<string, object>(StringComparer.Ordinal))),
                potion,
                __instance);
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("reward_potion_discard.enter", exception);
        }
    }

    private static void Postfix(
        NPotionPopup __instance,
        PatchState __state)
    {
        try
        {
            if ((!__state.Scope.Entered && !__state.Scope.DeferredFailure)
                || __state.Potion == null
                || __state.Popup == null)
                return;

            RecorderRuntime.ObserveAcceptedSemanticUiAction(
                NativeActionType,
                new ProcessLocalObservedAction(
                    "activate",
                    __state.Potion,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                new NativeWitnessEvidence(
                    "native_reward_potion_discard_ui",
                    NativeActionType,
                    NativeWitnessIdentity.Get(__state.Potion, "potion"),
                    new Dictionary<string, string>(StringComparer.Ordinal)
                    {
                        ["screen"] = NativeWitnessIdentity.Get(
                            __state.Popup,
                            "screen"),
                        ["popup"] = NativeWitnessIdentity.Get(__instance, "popup")
                    },
                    DateTimeOffset.UtcNow),
                captureImmediatePostCommitBoundary: false,
                actionWitnessId: __state.Scope.ActionWitnessId);
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("reward_potion_discard.accepted", exception);
        }
    }

    private static Exception? Finalizer(
        PatchState __state,
        Exception? __exception)
    {
        try
        {
            RecorderRuntime.ExitNativeUiScope(__state.Scope);
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("reward_potion_discard.exit", exception);
        }
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeRewardPotionDiscardEnqueuePatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(ActionQueueSynchronizer),
            nameof(ActionQueueSynchronizer.RequestEnqueue),
            new[] { typeof(GameAction) })
        ?? throw new MissingMethodException(
            typeof(ActionQueueSynchronizer).FullName,
            nameof(ActionQueueSynchronizer.RequestEnqueue));

    private static void Prefix([HarmonyArgument(0)] GameAction action)
    {
        try
        {
            if (action is not DiscardPotionGameAction)
                return;
            string? actionWitnessId = RecorderRuntime.CurrentSemanticActionWitnessId(
                NativeRewardPotionDiscardPatch.NativeActionType);
            if (actionWitnessId != null)
            {
                if (!NativeUiCompletionRootBindings.Remember(action, actionWitnessId))
                {
                    NativeUiObservationSafety.Report(
                        "reward_potion_discard.enqueue",
                        "The exact Human witness is already bound to a different child action; child ingress remains failed-closed.");
                    RecorderRuntime.ObserveSemanticUiCarrierBindingFailure(
                        actionWitnessId,
                        NativeRewardPotionDiscardPatch.NativeActionType,
                        "The exact Human witness is already bound to a different potion-discard child action.");
                }
                else
                    RecorderRuntime.ObserveSubmittedUiCarrier(action);
            }
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("reward_potion_discard.enqueue", exception);
        }
    }
}

[HarmonyPatch]
internal static class NativeRewardPotionDiscardCommitPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(DiscardPotionGameAction),
            "ExecuteAction")
        ?? throw new MissingMethodException(
            typeof(DiscardPotionGameAction).FullName,
            "ExecuteAction");

    private static void Postfix(DiscardPotionGameAction __instance, Task __result)
    {
        try
        {
            if (!NativeUiCompletionRootBindings.TryGet(__instance, out string? actionWitnessId)
                || actionWitnessId == null) return;
            RecorderRuntime.QueueNativePostCommitBoundary(
                SuccessfulDiscard(__result, __instance), "DiscardPotionGameAction.ExecuteAction",
                nativeOwner: __instance, nativeLineage: __instance,
                expectedActionWitnessId: actionWitnessId, completionRootOwner: __instance);
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("potion_discard.commit", exception);
        }
    }
    private static async Task<bool> SuccessfulDiscard(Task task, DiscardPotionGameAction action)
    {
        await task;
        return action.State != MegaCrit.Sts2.Core.Entities.Actions.GameActionState.Canceled;
    }

}

/// <summary>
/// Carries the exact Human reward-screen root to the Vote action object
/// created by SetLocalPlayerReady. The action object is the only stable
/// carrier across the queued act-change seam.
/// </summary>
[HarmonyPatch]
internal static class NativeActChangeVoteEnqueuePatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(ActionQueueSynchronizer),
            nameof(ActionQueueSynchronizer.RequestEnqueue),
            new[] { typeof(GameAction) })
        ?? throw new MissingMethodException(
            typeof(ActionQueueSynchronizer).FullName,
            nameof(ActionQueueSynchronizer.RequestEnqueue));

    private static void Prefix([HarmonyArgument(0)] GameAction action)
    {
        try
        {
            if (action is not VoteToMoveToNextActAction)
                return;
            string? actionWitnessId = RecorderRuntime.CurrentSemanticActionWitnessId(
                NativeRewardProceedPatch.ActChangeNativeActionType)
                ?? RecorderRuntime.CurrentSemanticActionWitnessId(
                    NativeRewardProceedPatch.NativeActionType);
            if (actionWitnessId != null)
            {
                // Prefix is intentional: GameAction.OnEnqueued is raised by
                // RequestEnqueue's original body before a Postfix can run.
                if (!NativeUiCompletionRootBindings.TransferRewardScreenToAction(action, actionWitnessId))
                {
                    NativeUiObservationSafety.Report(
                        "act_change.vote_enqueue",
                        "The exact Human witness is already bound to a different child action; child ingress remains failed-closed.");
                    RecorderRuntime.ObserveSemanticUiCarrierBindingFailure(
                        actionWitnessId,
                        NativeRewardProceedPatch.ActChangeNativeActionType,
                        "The exact Human witness is already bound to a different act-change child action.");
                }
            }
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("act_change.vote_enqueue", exception);
        }
    }
}

[HarmonyPatch]
internal static class NativeActChangeVoteCommitPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(VoteToMoveToNextActAction),
            "ExecuteAction")
        ?? throw new MissingMethodException(
            typeof(VoteToMoveToNextActAction).FullName,
            "ExecuteAction");

    private static void Prefix(
        VoteToMoveToNextActAction __instance,
        out string? __state)
    {
        __state = null;
        try
        {
            if (!NativeUiCompletionRootBindings.TryGet(__instance, out __state)
                || __state == null)
                return;
            if (!NativeUiCompletionRootBindings.Transfer(
                    __instance,
                    RunManager.Instance,
                    __state))
            {
                RecorderRuntime.ObserveSemanticUiCarrierBindingFailure(
                    __state,
                    NativeRewardProceedPatch.ActChangeNativeActionType,
                    "The exact vote-to-RunManager carrier transfer was ambiguous.");
                __state = null;
            }
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("act_change.vote_execute", exception);
        }
    }

    private static void Postfix(
        VoteToMoveToNextActAction __instance,
        string? __state)
    {
        try
        {
            if (__state == null)
                return;

            // OnPlayerReady is called from this exact ExecuteAction body. The
            // RunManager binding was staged in Prefix so a synchronously-started
            // EnterNextAct cannot race the ActEntered callback.
            RecorderRuntime.ObserveNativeActChangeOwnerReady(__state, __instance);
            RecorderRuntime.ObserveSemanticUiNativeCommit(
                __state,
                "act_change.ready",
                NativeActChangeDecisionProvider.CommitSeam,
                nativeOwner: RunManager.Instance.ActChangeSynchronizer,
                nativeOperand: __instance,
                nativeLineage: __instance);
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("act_change.vote_commit", exception);
        }
    }
}

[HarmonyPatch(typeof(RewardsSetSynchronizer), nameof(RewardsSetSynchronizer.SkipLocalRewardsSet))]
internal static class NativeRewardSkipCommitPatch
{
    private sealed record OwnerState(object? Owner, RewardsSet? Rewards, string? ActionWitnessId);

    private static void Prefix(out OwnerState? __state)
    {
        __state = null;
        try
        {
            object? owner = NOverlayStack.Instance?.Peek();
            if (NativeUiCompletionRootBindings.TryGet(owner, out string? actionWitnessId)
                && actionWitnessId != null)
                __state = new(owner, NativeRewardUiContext.CurrentRewardsSet(), actionWitnessId);
        }
        catch (Exception exception) { NativeUiObservationSafety.Report("reward_skip.owner", exception); }
    }

    private static void Postfix(RewardsSetSynchronizer __instance, OwnerState? __state)
    {
        if (__state?.ActionWitnessId == null || __state.Rewards == null) return;
        try
        {
            // SkipLocalRewardsSet can synchronously complete the reward parent
            // and remove its screen. The pre-call owner is the exact carrier;
            // the newly exposed overlay must never supply this Commit identity.
            bool durable = RecorderRuntime.ObserveSemanticUiNativeCommit(
                __state.ActionWitnessId,
                "reward_proceed",
                "RewardsSetSynchronizer.SkipLocalRewardsSet",
                nativeOwner: __instance,
                nativeOperand: __state.Rewards);
            if (durable)
                NativeUiCompletionRootBindings.TakeIfMatches(__state.Owner, __state.ActionWitnessId);
        }
        catch (Exception exception) { NativeUiObservationSafety.Report("reward_skip.commit", exception); }
    }
}

[HarmonyPatch]
internal static class NativeCardRewardSelectionPatch
{
    private const string NativeActionType = "NCardRewardSelectionScreen.SelectCard";

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NCardRewardSelectionScreen), "SelectCard")
        ?? throw new MissingMethodException(
            typeof(NCardRewardSelectionScreen).FullName,
            "SelectCard");

    private static void Prefix(
        NCardRewardSelectionScreen __instance,
        [HarmonyArgument(0)] NCardHolder cardHolder,
        out NativeUiScopeEntry __state)
    {
        __state = cardHolder.CardModel is { } card
            ? RecorderRuntime.TryEnterSemanticScope(
                "native_card_reward_selection_ui",
                NativeActionType,
                new ProcessLocalObservedAction(
                    "select",
                    card,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                new NativePostCommitCompletionExpectation(
                    "card_reward_select",
                    "NCardRewardSelectionScreen.SelectCard",
                    NativeOwnerWitnessId: NativeWitnessIdentity.Get(
                        __instance,
                        "native_owner"),
                    NativeOperandWitnessId: NativeWitnessIdentity.Get(card, "native_operand")))
            : default;
    }

    private static void Postfix(
        NCardRewardSelectionScreen __instance,
        [HarmonyArgument(0)] NCardHolder cardHolder,
        NativeUiScopeEntry __state)
    {
        if ((!__state.Entered && !__state.DeferredFailure) || cardHolder.CardModel is not { } card)
            return;
        RecorderRuntime.ObserveAcceptedSemanticUiAction(
            NativeActionType,
            new ProcessLocalObservedAction(
                "select",
                card,
                new Dictionary<string, object>(StringComparer.Ordinal)),
            new NativeWitnessEvidence(
                "native_card_reward_selection_ui",
                NativeActionType,
                NativeWitnessIdentity.Get(card, "card"),
                new Dictionary<string, string>(StringComparer.Ordinal),
                DateTimeOffset.UtcNow),
            captureImmediatePostCommitBoundary: false,
            actionWitnessId: __state.ActionWitnessId);
        if (__state.ActionWitnessId is { } actionWitnessId)
        {
            RecorderRuntime.ObserveSemanticUiNativeCommit(
                actionWitnessId,
                "card_reward_select",
                "NCardRewardSelectionScreen.SelectCard",
                nativeOwner: __instance,
                nativeOperand: card);
        }
    }

    private static Exception? Finalizer(NativeUiScopeEntry __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state);
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeRewardClaimCompletionPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(RewardsSetSynchronizer),
            "SelectLocalReward",
            new[] { typeof(Reward) })
        ?? throw new MissingMethodException(
            typeof(RewardsSetSynchronizer).FullName,
            "SelectLocalReward");

    private static void Postfix(
        RewardsSetSynchronizer __instance,
        [HarmonyArgument(0)] Reward reward,
        Task<bool> __result)
    {
        var outcome = new NativeUiAttemptOutcome(reward, __result);
        if (HumanActionScope.Current is { } context)
            context.NativeAttemptOutcome = outcome;
        else if (HumanActionScope.CurrentDeferredFailure is { } failure)
            failure.NativeAttemptOutcome = outcome;
        if (__result.IsCompletedSuccessfully && !__result.Result)
            return;
        // CardReward opens a nested native decision before SelectLocalReward's
        // Task can complete. That exact ShowScreen owner is the claim Commit;
        // the Task remains the later business outcome and must not block the
        // child Human decision.
        if (reward is CardReward)
            return;
        RecorderRuntime.QueueNativePostCommitBoundary(
            __result,
            "RewardsSetSynchronizer.SelectLocalReward",
            nativeOwner: __instance,
            nativeOperand: reward,
            completionRootOwner: reward);
    }
}

[HarmonyPatch]
internal static class NativeEventOptionPatch
{
    private const string NativeActionType = "NEventRoom.OptionButtonClicked";

    private readonly record struct PatchState(
        NativeUiScopeEntry Scope,
        EventOption? Option,
        string? Verb,
        NEventRoom? Room,
        NMapScreen? Map,
        bool MapWasOpen);

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(NEventRoom),
            "OptionButtonClicked",
            new[] { typeof(EventOption), typeof(int) })
        ?? throw new MissingMethodException(
            typeof(NEventRoom).FullName,
            "OptionButtonClicked");

    private static void Prefix(
        NEventRoom __instance,
        [HarmonyArgument(0)] EventOption option,
        out PatchState __state)
    {
        if (option == null || option.IsLocked)
        {
            __state = default;
            return;
        }

        string verb = option.IsProceed ? "proceed_event" : "choose_event_option";
        __state = new PatchState(
            RecorderRuntime.TryEnterSemanticScope(
                "native_event_option_ui",
                NativeActionType,
                new ProcessLocalObservedAction(
                    "activate",
                    option,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                new NativePostCommitCompletionExpectation(
                    "event_option",
                    "EventOption.Chosen",
                    NativeOperandWitnessId: NativeWitnessIdentity.Get(option, "native_operand")),
                new ProcessLocalObservedAction(
                    verb,
                    option,
                    new Dictionary<string, object>(StringComparer.Ordinal))),
            option,
            verb,
            __instance,
            NMapScreen.Instance,
            NMapScreen.Instance?.IsOpen == true);
        // Chosen can synchronously open a selector before this callback returns.
        if (__state.Scope.Entered)
            __state = __state with { Scope = __state.Scope with {
                CarrierBindingFailed = !NativeUiCompletionRootBindings.Remember(option, __state.Scope.ActionWitnessId) } };
    }

    private static void Postfix(PatchState __state)
    {
        try
        {
            if ((!__state.Scope.Entered && !__state.Scope.DeferredFailure)
                || __state.Option is not { } option
                || __state.Verb is not { } verb
                || __state.Room is not { } room)
                return;
            bool accepted = RecorderRuntime.ObserveAcceptedSemanticUiAction(
                NativeActionType,
                new ProcessLocalObservedAction(
                    "activate",
                    option,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                new NativeWitnessEvidence(
                    "native_event_option_ui",
                    NativeActionType,
                    NativeWitnessIdentity.Get(option, "event_option"),
                    new Dictionary<string, string>(StringComparer.Ordinal),
                    DateTimeOffset.UtcNow),
                captureImmediatePostCommitBoundary: false,
                actionWitnessId: __state.Scope.ActionWitnessId);
            if (!accepted)
            {
                NativeUiCompletionRootBindings.TakeIfMatches(option, __state.Scope.ActionWitnessId);
                return;
            }
            if (__state.Scope.CarrierBindingFailed)
            {
                RecorderRuntime.ObserveSemanticUiCarrierBindingFailure(
                    __state.Scope.ActionWitnessId!, NativeActionType,
                    "The exact EventOption was already bound before native execution.");
                return;
            }
            if (NativeEventOptionCompletionPatch.TryTakeTask(option, out Task? task)
                && task != null)
            {
                if (NativeUiCompletionRootBindings.TryGet(
                        option,
                        out string? actionWitnessId)
                    && actionWitnessId != null)
                {
                    // The native Proceed branch opens the exact map before
                    // Chosen and this outer callback return. Preserve that
                    // owner handoff now; Task completion alone is only Commit.
                    if (verb == "proceed_event" && __state.Map is { } map
                        && actionWitnessId == __state.Scope.ActionWitnessId
                        && NativeSynchronousOwnerHandoff.Matches(task, map, __state.MapWasOpen,
                            NMapScreen.Instance, map.IsOpen, actionWitnessId, HumanActionScope.Current?.ActionWitnessId))
                    {
                        if (RecorderRuntime.ObserveSemanticUiNativeCommit(
                            actionWitnessId, "event_option", "EventOption.Chosen", nativeOperand: option))
                        {
                            NativeUiCompletionRootBindings.TakeIfMatches(option, actionWitnessId);
                            NativeDecisionOwnerReadyProvider.ObserveEventProceedReady(map);
                        }
                        return;
                    }
                    RecorderRuntime.QueueNativePostCommitBoundary(
                        task,
                        "EventOption.Chosen",
                        nativeOperand: option,
                        expectedActionWitnessId: actionWitnessId);
                    NativeUiCompletionRootBindings.TakeIfMatches(option, actionWitnessId);
                }
            }
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("event_option.accepted", exception);
        }
    }

    private static Exception? Finalizer(PatchState __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state.Scope);
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeEventOptionCompletionPatch
{
    private sealed class TaskCarrier
    {
        internal TaskCarrier(Task task) => Task = task;

        internal Task Task { get; }
    }

    private static readonly ConditionalWeakTable<EventOption, TaskCarrier> Tasks = new();

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(EventOption), nameof(EventOption.Chosen))
        ?? throw new MissingMethodException(typeof(EventOption).FullName, nameof(EventOption.Chosen));

    private static void Postfix(EventOption __instance, Task __result)
    {
        if (__result == null)
            return;
        try
        {
            Tasks.Remove(__instance);
            Tasks.Add(__instance, new TaskCarrier(__result));
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report("EventOption.Chosen.task_carrier", exception);
        }
    }

    internal static bool TryTakeTask(EventOption option, out Task? task)
    {
        task = null;
        if (!Tasks.TryGetValue(option, out TaskCarrier? carrier))
            return false;
        Tasks.Remove(option);
        task = carrier.Task;
        return true;
    }

}

[HarmonyPatch]
internal static class NativeRestSiteOptionPatch
{
    private const string NativeActionType = "RestSiteSynchronizer.ChooseLocalOption";

    private readonly record struct PatchState(
        NativeUiScopeEntry Scope,
        RestSiteOption? Option,
        NRestSiteRoom? Room,
        string? InheritedActionWitnessId,
        IDisposable? NestedSelectorScope);

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(RestSiteSynchronizer),
            nameof(RestSiteSynchronizer.ChooseLocalOption),
            new[] { typeof(int) })
        ?? throw new MissingMethodException(
            typeof(RestSiteSynchronizer).FullName,
            nameof(RestSiteSynchronizer.ChooseLocalOption));

    private static void Prefix(
        RestSiteSynchronizer __instance,
        [HarmonyArgument(0)] int index,
        out PatchState __state)
    {
        NRestSiteRoom? room = NRestSiteRoom.Instance;
        RestSiteOption[] options = __instance.GetLocalOptions().ToArray();
        if (room == null || index < 0 || index >= options.Length)
        {
            __state = default;
            return;
        }
        RestSiteOption option = options[index];
        NativeUiCompletionRootBindings.TryGet(option, out string? inheritedActionWitnessId);
        __state = new PatchState(
            default,
            option,
            room,
            inheritedActionWitnessId,
            null);
        string? actionWitnessId = __state.Scope.ActionWitnessId ?? inheritedActionWitnessId;
        if (actionWitnessId != null)
        {
            __state = __state with
            {
                NestedSelectorScope = NativeNestedSelectorBindings.EnterParent(
                    actionWitnessId,
                    option,
                    "rest_site.nested_selector",
                    NativeActionType)
            };
        }
    }

    private static void Postfix(
        RestSiteSynchronizer __instance,
        PatchState __state,
        Task<bool> __result)
    {
        if (__state.Option is not { } option
            || __state.Room is not { } room)
            return;
        string? actionWitnessId = __state.Scope.ActionWitnessId
            ?? __state.InheritedActionWitnessId;
        if (actionWitnessId == null
            || !__state.Scope.Entered && !__state.Scope.DeferredFailure
                && __state.InheritedActionWitnessId == null)
            return;
        bool accepted = RecorderRuntime.ObserveAcceptedSemanticUiAction(
            NativeActionType,
            new ProcessLocalObservedAction(
                "activate",
                option,
                new Dictionary<string, object>(StringComparer.Ordinal)),
            new NativeWitnessEvidence(
                "native_rest_site_option_ui",
                NativeActionType,
                NativeWitnessIdentity.Get(option, "rest_option"),
                new Dictionary<string, string>(StringComparer.Ordinal),
                DateTimeOffset.UtcNow),
            captureImmediatePostCommitBoundary: false,
            actionWitnessId: actionWitnessId);
        if (!accepted)
        {
            NativeUiCompletionRootBindings.TakeIfMatches(option, actionWitnessId);
            return;
        }
        if (__result != null)
        {
            RecorderRuntime.QueueNativePostCommitBoundary(
                __result,
                NativeActionType,
                nativeOwner: __instance,
                nativeOperand: option,
                expectedActionWitnessId: actionWitnessId);
            NativeUiCompletionRootBindings.TakeIfMatches(option, actionWitnessId);
        }
    }

    private static Exception? Finalizer(PatchState __state, Exception? __exception)
    {
        __state.NestedSelectorScope?.Dispose();
        RecorderRuntime.ExitNativeUiScope(__state.Scope);
        return __exception;
    }
}

/// <summary>
/// NRestSiteButton disables its visible options before it invokes the
/// asynchronous RestSiteSynchronizer operation. Capture the exact interactive
/// frame at the button boundary, then let the nested synchronizer callback
/// publish the accepted root and bind the returned Task to that same root.
/// </summary>
[HarmonyPatch]
internal static class NativeRestSiteButtonPatch
{
    private const string NativeActionType = "RestSiteSynchronizer.ChooseLocalOption";

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NRestSiteButton), "OnRelease")
        ?? throw new MissingMethodException(
            typeof(NRestSiteButton).FullName,
            "OnRelease");

    private static void Prefix(
        NRestSiteButton __instance,
        out NativeUiScopeEntry __state)
    {
        RestSiteOption? option = __instance.Option;
        RestSiteRoom? room = RunManager.Instance.DebugOnlyGetState()?.CurrentRoom as RestSiteRoom;
        if (option == null
            || room == null
            || NRestSiteRoom.Instance == null
            || !room.Options.Any(candidate => ReferenceEquals(candidate, option)))
        {
            __state = default;
            return;
        }
        __state = RecorderRuntime.TryEnterSemanticScope(
            "native_rest_site_option_ui",
            NativeActionType,
            new ProcessLocalObservedAction(
                "activate",
                option,
                new Dictionary<string, object>(StringComparer.Ordinal)),
            new NativePostCommitCompletionExpectation(
                "rest_site",
                NativeActionType,
                NativeOperandWitnessId: NativeWitnessIdentity.Get(option, "native_operand")),
            new ProcessLocalObservedAction(
                "choose_rest_option",
                option,
                new Dictionary<string, object>(StringComparer.Ordinal)));
        if (__state.ActionWitnessId != null
            && !NativeUiCompletionRootBindings.Remember(option, __state.ActionWitnessId))
        {
            NativeUiObservationSafety.Report(
                "native_rest_site_option_ui.root_collision",
                "The exact RestSiteOption already belongs to a different Human root.");
        }
    }

    private static Exception? Finalizer(
        NativeUiScopeEntry __state,
        Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state);
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeRestSiteProceedPatch
{
    private const string NativeActionType = "NRestSiteRoom.OnProceedButtonReleased";

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(NRestSiteRoom),
            "OnProceedButtonReleased",
            new[] { typeof(NButton) })
        ?? throw new MissingMethodException(
            typeof(NRestSiteRoom).FullName,
            "OnProceedButtonReleased");

    private static void Prefix(
        NRestSiteRoom __instance,
        out NativeUiScopeEntry __state)
    {
        // NRestSiteRoom delegates directly to Open(), whose IsOpen branch is
        // a native no-op. It is not another accepted Human effect.
        if (NMapScreen.Instance?.IsOpen == true)
        {
            __state = default;
            return;
        }
        RestSiteRoom? room = RunManager.Instance.DebugOnlyGetState()?.CurrentRoom as RestSiteRoom;
        __state = room == null
            ? default
            : RecorderRuntime.TryEnterSemanticScope(
                "native_rest_site_proceed_ui",
                NativeActionType,
                new ProcessLocalObservedAction(
                    "activate",
                    null,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                nativeSemanticSelection: new ProcessLocalObservedAction(
                    "proceed_rest_site",
                    room,
                    new Dictionary<string, object>(StringComparer.Ordinal)));
    }

    private static void Postfix(
        NRestSiteRoom __instance,
        NativeUiScopeEntry __state)
    {
        if (!__state.Entered && !__state.DeferredFailure)
            return;
        RecorderRuntime.ObserveAcceptedSemanticUiAction(
            NativeActionType,
            new ProcessLocalObservedAction(
                "activate",
                null,
                new Dictionary<string, object>(StringComparer.Ordinal)),
            new NativeWitnessEvidence(
                "native_rest_site_proceed_ui",
                NativeActionType,
                NativeWitnessIdentity.Get(__instance, "proceed_button"),
                new Dictionary<string, string>(StringComparer.Ordinal),
                DateTimeOffset.UtcNow));
    }

    private static Exception? Finalizer(NativeUiScopeEntry __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state);
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeShopPurchasePatch
{
    private const string NativeActionType = "MerchantEntry.OnTryPurchaseWrapper";
    private const string CardRemovalNativeActionType =
        "MerchantCardRemovalEntry.OnTryPurchaseWrapper";

    private readonly record struct PatchState(
        NativeUiScopeEntry Scope,
        MerchantEntry? Entry,
        NMerchantInventory? Inventory,
        string? Operation,
        string? NativeActionType,
        IDisposable? NestedSelectorScope);

    internal static IEnumerable<MethodBase> TargetMethods()
    {
        yield return AccessTools.Method(
            typeof(MerchantEntry),
            nameof(MerchantEntry.OnTryPurchaseWrapper),
            new[] { typeof(MerchantInventory), typeof(bool) })
            ?? throw new MissingMethodException(
                typeof(MerchantEntry).FullName,
                nameof(MerchantEntry.OnTryPurchaseWrapper));
        yield return AccessTools.Method(
            typeof(MerchantCardRemovalEntry),
            nameof(MerchantCardRemovalEntry.OnTryPurchaseWrapper),
            new[] { typeof(MerchantInventory), typeof(bool), typeof(bool) })
            ?? throw new MissingMethodException(
                typeof(MerchantCardRemovalEntry).FullName,
                nameof(MerchantCardRemovalEntry.OnTryPurchaseWrapper));
    }

    private static void Prefix(
        MerchantEntry __instance,
        [HarmonyArgument(0)] MerchantInventory? inventory,
        out PatchState __state)
    {
        NMerchantInventory? ui = NMerchantRoom.Instance?.Inventory;
        if (ui == null || inventory == null || !ReferenceEquals(ui.Inventory, inventory))
        {
            __state = default;
            return;
        }
        string operation = __instance switch
        {
            MerchantCardEntry => "purchase_shop_card",
            MerchantRelicEntry => "purchase_shop_relic",
            MerchantPotionEntry => "purchase_shop_potion",
            MerchantCardRemovalEntry => "open_shop_card_removal",
            _ => string.Empty
        };
        if (operation.Length == 0)
        {
            __state = default;
            return;
        }
        string nativeActionType = __instance is MerchantCardRemovalEntry
            ? CardRemovalNativeActionType
            : NativeActionType;
        NativeUiScopeEntry scope = RecorderRuntime.TryEnterSemanticScope(
            "native_shop_purchase_ui",
            nativeActionType,
            new ProcessLocalObservedAction(
                __instance is MerchantCardRemovalEntry ? "open" : "activate",
                __instance,
                new Dictionary<string, object>(StringComparer.Ordinal)),
            new NativePostCommitCompletionExpectation(
                "shop_inventory",
                nativeActionType,
                NativeOperandWitnessId: NativeWitnessIdentity.Get(__instance, "native_operand")),
            new ProcessLocalObservedAction(
                operation,
                __instance,
                new Dictionary<string, object>(StringComparer.Ordinal)));
        __state = new PatchState(
            scope,
            __instance,
            ui,
            operation,
            nativeActionType,
            __instance is MerchantCardRemovalEntry
                && scope.ActionWitnessId != null
                && NativeUiCompletionRootBindings.Remember(__instance, scope.ActionWitnessId)
                && NativeUiCompletionRootBindings.TryGet(__instance, out string? exactRoot)
                ? NativeNestedSelectorBindings.EnterParent(
                    exactRoot!,
                    __instance,
                    "shop_inventory.card_removal_nested_selector",
                    CardRemovalNativeActionType)
                : null);
    }

    private static void Postfix(
        MerchantEntry __instance,
        PatchState __state,
        Task<bool> __result)
    {
        if ((!__state.Scope.Entered && !__state.Scope.DeferredFailure)
            || __state.Entry is not { } entry
            || __state.Inventory is not { } inventory
            || __state.Operation is not { Length: > 0 } operation
            || __state.NativeActionType is not { Length: > 0 } nativeActionType)
            return;
        if (__result.IsCompletedSuccessfully && !__result.Result)
        {
            RecorderRuntime.ObserveRejectedNativeUiInput(nativeActionType, entry);
            NativeUiCompletionRootBindings.TakeIfMatches(entry, __state.Scope.ActionWitnessId);
            return;
        }
        bool accepted = RecorderRuntime.ObserveAcceptedSemanticUiAction(
            nativeActionType,
            new ProcessLocalObservedAction(
                __instance is MerchantCardRemovalEntry ? "open" : "activate",
                entry,
                new Dictionary<string, object>(StringComparer.Ordinal)),
            new NativeWitnessEvidence(
                "native_shop_purchase_ui",
                nativeActionType,
                NativeWitnessIdentity.Get(entry, "shop_offer"),
                new Dictionary<string, string>(StringComparer.Ordinal),
                DateTimeOffset.UtcNow),
            captureImmediatePostCommitBoundary: false,
            actionWitnessId: __state.Scope.ActionWitnessId);
        if (!accepted)
        {
            NativeUiCompletionRootBindings.TakeIfMatches(entry, __state.Scope.ActionWitnessId);
            return;
        }
        if (__result != null)
        {
            RecorderRuntime.QueueNativePostCommitBoundary(
                __result,
                nativeActionType,
                nativeOperand: entry,
                expectedActionWitnessId: __state.Scope.ActionWitnessId);
            NativeUiCompletionRootBindings.TakeIfMatches(entry, __state.Scope.ActionWitnessId);
        }
    }

    private static Exception? Finalizer(PatchState __state, Exception? __exception)
    {
        __state.NestedSelectorScope?.Dispose();
        RecorderRuntime.ExitNativeUiScope(__state.Scope);
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeShopRoomOpenPatch
{
    private const string NativeActionType = "NMerchantRoom.OpenInventory";

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(NMerchantRoom),
            nameof(NMerchantRoom.OpenInventory),
            Type.EmptyTypes)
        ?? throw new MissingMethodException(
            typeof(NMerchantRoom).FullName,
            nameof(NMerchantRoom.OpenInventory));

    private static void Prefix(
        NMerchantRoom __instance,
        out NativeUiScopeEntry __state)
    {
        MerchantRoom? room = RunManager.Instance.DebugOnlyGetState()?.CurrentRoom as MerchantRoom;
        __state = room == null || __instance.Inventory.IsOpen
            ? default
            : RecorderRuntime.TryEnterSemanticScope(
                "native_shop_room_ui",
                NativeActionType,
                new ProcessLocalObservedAction(
                    "open",
                    null,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                nativeSemanticSelection: new ProcessLocalObservedAction(
                    "open_shop_inventory",
                    __instance,
                    new Dictionary<string, object>(StringComparer.Ordinal)));
    }

    private static void Postfix(
        NMerchantRoom __instance,
        NativeUiScopeEntry __state)
    {
        if (!__state.Entered && !__state.DeferredFailure)
            return;
        RecorderRuntime.ObserveAcceptedSemanticUiAction(
            NativeActionType,
            new ProcessLocalObservedAction(
                "open",
                null,
                new Dictionary<string, object>(StringComparer.Ordinal)),
            new NativeWitnessEvidence(
                "native_shop_room_open_ui",
                NativeActionType,
                NativeWitnessIdentity.Get(__instance, "shop_room"),
                new Dictionary<string, string>(StringComparer.Ordinal),
                DateTimeOffset.UtcNow));
    }

    private static Exception? Finalizer(NativeUiScopeEntry __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state);
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeShopRoomProceedPatch
{
    private const string NativeActionType = "NMerchantRoom.HideScreen";

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(NMerchantRoom),
            "HideScreen",
            new[] { typeof(NButton) })
        ?? throw new MissingMethodException(
            typeof(NMerchantRoom).FullName,
            "HideScreen");

    private static void Prefix(
        NMerchantRoom __instance,
        out NativeUiScopeEntry __state)
    {
        MerchantRoom? room = RunManager.Instance.DebugOnlyGetState()?.CurrentRoom as MerchantRoom;
        __state = room == null || __instance.Inventory.IsOpen
            ? default
            : RecorderRuntime.TryEnterSemanticScope(
                "native_shop_room_proceed_ui",
                NativeActionType,
                new ProcessLocalObservedAction(
                    "activate",
                    null,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                nativeSemanticSelection: new ProcessLocalObservedAction(
                    "proceed_shop",
                    __instance,
                    new Dictionary<string, object>(StringComparer.Ordinal)));
    }

    private static void Postfix(
        NMerchantRoom __instance,
        NativeUiScopeEntry __state)
    {
        if (!__state.Entered && !__state.DeferredFailure)
            return;
        RecorderRuntime.ObserveAcceptedSemanticUiAction(
            NativeActionType,
            new ProcessLocalObservedAction(
                "activate",
                null,
                new Dictionary<string, object>(StringComparer.Ordinal)),
            new NativeWitnessEvidence(
                "native_shop_room_proceed_ui",
                NativeActionType,
                NativeWitnessIdentity.Get(__instance, "shop_room"),
                new Dictionary<string, string>(StringComparer.Ordinal),
                DateTimeOffset.UtcNow));
    }

    private static Exception? Finalizer(NativeUiScopeEntry __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state);
        return __exception;
    }
}

[HarmonyPatch]
internal static class NativeShopInventoryClosePatch
{
    private const string NativeActionType = "NMerchantInventory.Close";

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(NMerchantInventory),
            "Close",
            Type.EmptyTypes)
        ?? throw new MissingMethodException(
            typeof(NMerchantInventory).FullName,
            "Close");

    private static void Prefix(
        NMerchantInventory __instance,
        out NativeUiScopeEntry __state)
    {
        NMerchantRoom? room = NMerchantRoom.Instance;
        __state = room?.Inventory is not { IsOpen: true } current
                  || !ReferenceEquals(current, __instance)
            ? default
            : RecorderRuntime.TryEnterSemanticScope(
                "native_shop_inventory_close_ui",
                NativeActionType,
                new ProcessLocalObservedAction(
                    "cancel",
                    null,
                    new Dictionary<string, object>(StringComparer.Ordinal)),
                nativeSemanticSelection: new ProcessLocalObservedAction(
                    "close_shop_inventory",
                    __instance,
                    new Dictionary<string, object>(StringComparer.Ordinal)));
    }

    private static void Postfix(
        NMerchantInventory __instance,
        NativeUiScopeEntry __state)
    {
        if (!__state.Entered && !__state.DeferredFailure)
            return;
        RecorderRuntime.ObserveAcceptedSemanticUiAction(
            NativeActionType,
            new ProcessLocalObservedAction(
                "cancel",
                null,
                new Dictionary<string, object>(StringComparer.Ordinal)),
            new NativeWitnessEvidence(
                "native_shop_inventory_close_ui",
                NativeActionType,
                NativeWitnessIdentity.Get(__instance, "shop_inventory"),
                new Dictionary<string, string>(StringComparer.Ordinal),
                DateTimeOffset.UtcNow));
    }

    private static Exception? Finalizer(NativeUiScopeEntry __state, Exception? __exception)
    {
        RecorderRuntime.ExitNativeUiScope(__state);
        return __exception;
    }
}

/// <summary>
/// Captures Launch on the exact RunState. Setup provenance distinguishes new
/// singleplayer runs from saved resumes; Launch alone is not a new-run witness.
/// </summary>
[HarmonyPatch]
internal static class NativeRunStartedPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(RunManager), "Launch")
        ?? throw new MissingMethodException(
            typeof(RunManager).FullName,
            "Launch");

    internal static readonly NativeRunLaunchProvenance<RunState> Origins = new();

    private static void Postfix(RunState __result)
    {
        try { NativeRunLifecycleProvider.ObserveLaunch(__result); }
        catch (Exception exception) { NativeUiObservationSafety.Report("run.launch.source", exception); }
        RecorderRuntime.ObserveNativeRunStarted(Origins.JournalKind(__result));
    }
}

// Singleplayer is the recorder's supported admission domain. Setup observes
// the exact RunState even while recording is closed; no gameplay is recorded.
[HarmonyPatch(typeof(RunManager), nameof(RunManager.SetUpNewSingleplayer))]
internal static class NativeNewRunSetupPatch
{
    private static void Prefix([HarmonyArgument(0)] RunState state, out NativeRunSetupInvocation? __state)
    {
        __state = null;
        try { __state = NativeRunLifecycleProvider.StageSetup(state, RunManager.Instance.DebugOnlyGetState(), true); }
        catch (Exception exception) { NativeUiObservationSafety.Report("run_setup.new.prefix", exception); }
    }
    private static void Postfix([HarmonyArgument(0)] RunState state, NativeRunSetupInvocation? __state)
    {
        try
        {
            NativeRunLifecycleProvider.FinishSetup(__state, RunManager.Instance.DebugOnlyGetState());
            NativeRunStartedPatch.Origins.ObserveSetup(state, true);
        }
        catch (Exception exception) { NativeUiObservationSafety.Report("run_setup.new", exception); }
    }
    private static Exception? Finalizer(NativeRunSetupInvocation? __state, Exception? __exception)
    {
        try { NativeRunLifecycleProvider.AbandonSetup(__state); }
        catch (Exception exception) { NativeUiObservationSafety.Report("run_setup.new.finalizer", exception); }
        return __exception;
    }
}

[HarmonyPatch(typeof(RunManager), nameof(RunManager.SetUpSavedSingleplayer))]
internal static class NativeSavedRunSetupPatch
{
    private static void Prefix([HarmonyArgument(0)] RunState state, out NativeRunSetupInvocation? __state)
    {
        __state = null;
        try { __state = NativeRunLifecycleProvider.StageSetup(state, RunManager.Instance.DebugOnlyGetState(), false); }
        catch (Exception exception) { NativeUiObservationSafety.Report("run_setup.saved.prefix", exception); }
    }
    private static void Postfix([HarmonyArgument(0)] RunState state, NativeRunSetupInvocation? __state)
    {
        try
        {
            NativeRunLifecycleProvider.FinishSetup(__state, RunManager.Instance.DebugOnlyGetState());
            NativeRunStartedPatch.Origins.ObserveSetup(state, false);
        }
        catch (Exception exception) { NativeUiObservationSafety.Report("run_setup.saved", exception); }
    }
    private static Exception? Finalizer(NativeRunSetupInvocation? __state, Exception? __exception)
    {
        try { NativeRunLifecycleProvider.AbandonSetup(__state); }
        catch (Exception exception) { NativeUiObservationSafety.Report("run_setup.saved.finalizer", exception); }
        return __exception;
    }
}

/// <summary>
/// Captures the exact STS2 terminal seam.  RunManager.OnEnded is invoked by
/// both native victory and defeat paths; the recorder only projects this
/// observation and never manufactures a successor or settles an action.
/// </summary>
[HarmonyPatch]
internal static class NativeRunEndedPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(RunManager),
            "OnEnded",
            new[] { typeof(bool) })
        ?? throw new MissingMethodException(
            typeof(RunManager).FullName,
            "OnEnded");

    private static void Postfix([HarmonyArgument(0)] bool isVictory)
    {
        try { NativeRunLifecycleProvider.ObserveTerminal(RunManager.Instance.DebugOnlyGetState(), isVictory); }
        catch (Exception exception) { NativeUiObservationSafety.Report("run.terminal.source", exception); }
        RecorderRuntime.ObserveNativeRunEnded(isVictory);
    }
}

[HarmonyPatch(typeof(RunManager), nameof(RunManager.CleanUp))]
internal static class NativeRunCleanupPatch
{
    private sealed record CleanupState(bool WasInProgress, RunState? Previous);
    private static void Prefix(RunManager __instance, out CleanupState __state) =>
        __state = new(__instance.IsInProgress, __instance.DebugOnlyGetState());

    private static void Postfix(RunManager __instance, [HarmonyArgument(0)] bool graceful, CleanupState __state)
    {
        // Source lifecycle follows exact pre/post continuity even when legacy IsInProgress was already false.
        try { NativeRunLifecycleProvider.ObserveCleanup(__state.Previous, __instance.DebugOnlyGetState(), graceful); }
        catch (Exception exception) { NativeUiObservationSafety.Report("run.cleanup.source", exception); }
        if (!__state.WasInProgress) return;
        try { RecorderRuntime.ObserveNativeRunCleanup(graceful); }
        catch (Exception exception) { NativeUiObservationSafety.Report("run.cleanup", exception); }
    }
}
