using System.Collections;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using HarmonyLib;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Events;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.GameActions.Multiplayer;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Rewards;
using STS2Connector.PlayerEnvironment.Witness;
using STS2HumanAnnotator.Core;
using STS2Platform.NativeFoundation;

namespace STS2HumanAnnotator.Mod;

internal static class NativeNestedCallbackSafety
{
    internal static void Run(string seam, Action callback)
    {
        try
        {
            callback();
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report(seam, exception);
        }
    }

    internal static T Run<T>(string seam, Func<T> callback, T fallback)
    {
        try
        {
            return callback();
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report(seam, exception);
            return fallback;
        }
    }

    internal static Exception? Finalize(
        string seam,
        Exception? original,
        Action callback)
    {
        try
        {
            callback();
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report(seam, exception);
        }
        return original;
    }
}

/// <summary>
/// Exact process-local lineage for generic card-selection screens. Parent
/// scopes flow only through the exact native async invocation that owns the
/// selector factory. Screens are then keyed by their own native object. No
/// queue position, current overlay, timing, or most-recent root participates.
/// </summary>
internal static class NativeNestedSelectorBindings
{
    private const string FactoryDerivedFamily = "factory_derived_generic_selector";
    internal sealed record Parent(
        string? ActionWitnessId,
        object? NativeOwner,
        PlayerChoiceContext? ChoiceContext,
        string Family,
        string NativeMechanism);

    internal sealed record Binding(
        string ActionWitnessId,
        object ParentOwner,
        string Family,
        string FactoryMechanism)
    {
        internal string? RecordingSessionId { get; } = RecorderRuntime.SessionId;
        internal string? FailureReason { get; init; }
        internal NativeDecisionOriginEvidence? NativeOrigin { get; init; }
        internal DecisionOccurrenceIdentity? ParentDecision { get; set; }
        internal string? DecisionHeadActionId { get; set; }
    }

    private static readonly ExactAsyncOwnerBindingScope<object, Parent, Binding> Screens = new();

    internal static IDisposable EnterParent(
        string actionWitnessId,
        object nativeOwner,
        string family,
        string nativeMechanism)
    {
        return Screens.Enter(new Parent(
            actionWitnessId,
            nativeOwner,
            null,
            family,
            nativeMechanism));
    }

    internal static IDisposable EnterGenericSelectorParent(
        string actionWitnessId,
        object nativeOwner,
        string nativeMechanism) =>
        EnterParent(
            actionWitnessId,
            nativeOwner,
            FactoryDerivedFamily,
            nativeMechanism);

    internal static IDisposable? EnterPlayerChoiceParent(
        PlayerChoiceContext context,
        string family,
        string nativeMechanism)
    {
        ArgumentNullException.ThrowIfNull(context);
        // A blocking choice is a real native origin without a GameAction.
        // Preserve an enclosing exact Event/Reward owner when one exists.
        if (context is BlockingPlayerChoiceContext)
            return Screens.EnterIfAbsent(new Parent(null, null, context, family, nativeMechanism));
        // Throwing contexts cannot establish a native decision origin.
        if (context is not GameActionPlayerChoiceContext
            && context is not HookPlayerChoiceContext
            && context is not BranchingPlayerChoiceContext)
            return null;
        return Screens.Enter(new Parent(
            null,
            null,
            context,
            family,
            nativeMechanism));
    }

    // Reward screens are also native input owners inside an EventOption Task.
    // Ordinary combat rewards have no such scope and remain independent roots.
    internal static void RegisterOptionalInputOwner(object owner, MethodBase factory)
    {
        try
        {
            // No scope is normal for independent combat reward screens.
            Screens.TryBindCurrent(owner, parent => Resolve(parent, owner, factory));
        }
        catch
        {
            // Preserve an unavailable binding through the same fail-closed
            // factory path; never turn a failed exact parent into a new root.
            Register(owner, factory);
        }
    }

    internal static void Register(object? screen, MethodBase factory)
    {
        if (screen == null)
            return;
        string failureReason = "selector_factory_without_exact_parent_scope";
        try
        {
            if (Screens.TryBindCurrent(
                    screen,
                    parent => Resolve(parent, screen, factory)))
                return;
        }
        catch (Exception exception)
        {
            // A context-bearing native invocation whose exact action/root
            // cannot be resolved is evidence of an unavailable binding, not
            // permission to fall back to ambient/current action state.
            failureReason = exception.Message;
            NativeUiObservationSafety.Report(
                $"{factory.DeclaringType?.FullName}.{factory.Name}.exact_parent",
                exception);
        }
        var unavailable = new Binding(
            "unavailable",
            screen,
            FamilyFor(screen),
            $"{factory.DeclaringType?.FullName}.{factory.Name}") { FailureReason = failureReason };
        if (!Screens.TrySet(screen, unavailable))
            throw new InvalidOperationException(
                "The exact selector screen already carries a different parent/root binding.");
        NativeUiObservationSafety.Report(
            unavailable.FactoryMechanism,
            "An exact parent/root scope was unavailable; ambient current GameAction lineage is not accepted.");
    }

    private static Binding Resolve(Parent parent, object screen, MethodBase factory)
    {
        string factoryMechanism = $"{factory.DeclaringType?.FullName}.{factory.Name}";
        if (parent.ActionWitnessId is { Length: > 0 } fixedRoot
            && parent.NativeOwner != null)
        {
            return new Binding(
                fixedRoot,
                parent.NativeOwner,
                string.Equals(
                    parent.Family,
                    FactoryDerivedFamily,
                    StringComparison.Ordinal)
                    ? FamilyFor(screen)
                    : parent.Family,
                factoryMechanism);
        }

        string actualFamily = FamilyFor(screen);
        if (parent.ChoiceContext is BlockingPlayerChoiceContext blocking
            && string.Equals(parent.Family, actualFamily, StringComparison.Ordinal))
        {
            string nativeRoot = NativeWitnessIdentity.Get(blocking, "choice_context");
            // The historical NativeAction* wire fields identify the actual
            // native context here; no synthetic GameAction or Human parent.
            return new Binding(nativeRoot, blocking, parent.Family, factoryMechanism) {
                NativeOrigin = new NativeDecisionOriginEvidence(nativeRoot, blocking.GetType().FullName!,
                    blocking.GetType().FullName!, factoryMechanism)
            };
        }
        if (parent.ChoiceContext == null
            || !string.Equals(parent.Family, actualFamily, StringComparison.Ordinal)
            || !TryResolveExactAction(parent.ChoiceContext, out GameAction? action)
            || action == null)
        {
            throw new InvalidOperationException(
                $"The exact {parent.Family} CardSelectCmd invocation did not carry a matching bound GameAction owner.");
        }
        if (!NativeUiCompletionRootBindings.TryGet(action, out string? actionWitnessId)
            || string.IsNullOrWhiteSpace(actionWitnessId))
        {
            // HookPlayerChoiceContext creates this real native GameAction and
            // awaits its execution before the exact CardSelectCmd factory.
            // Its subsequent Human input need not have a Human parent decision.
            if (action is GenericHookGameAction hook && hook.ExecutionStartedTask.IsCompletedSuccessfully
                || action is PlayCardAction && action.State == MegaCrit.Sts2.Core.Entities.Actions.GameActionState.GatheringPlayerChoice)
            {
                string nativeRoot = NativeWitnessIdentity.Get(action, "game_action");
                return new Binding(nativeRoot, action, parent.Family, factoryMechanism) {
                    NativeOrigin = new NativeDecisionOriginEvidence(nativeRoot, action.GetType().FullName!,
                        parent.ChoiceContext.GetType().FullName!, factoryMechanism)
                };
            }
            return new Binding("unavailable", action, parent.Family, factoryMechanism) {
                FailureReason = $"exact_native_owner_without_human_decision:{action.GetType().Name}"
            };
        }
        return new Binding(
            actionWitnessId,
            action,
            parent.Family,
            factoryMechanism);
    }

    private static bool TryResolveExactAction(
        PlayerChoiceContext context,
        out GameAction? action) =>
        TryResolveExactAction(
            context,
            new HashSet<PlayerChoiceContext>(ReferenceEqualityComparer.Instance),
            out action);

    private static bool TryResolveExactAction(
        PlayerChoiceContext context,
        ISet<PlayerChoiceContext> visited,
        out GameAction? action)
    {
        if (!visited.Add(context))
        {
            action = null;
            return false;
        }
        action = context switch
        {
            GameActionPlayerChoiceContext gameActionContext => gameActionContext.Action,
            HookPlayerChoiceContext hookContext => hookContext.GameAction,
            BranchingPlayerChoiceContext branchingContext =>
                ResolveBranchingContext(branchingContext, visited),
            _ => null
        };
        return action != null;
    }

    private static GameAction? ResolveBranchingContext(
        BranchingPlayerChoiceContext context,
        ISet<PlayerChoiceContext> visited)
    {
        // Exact v0.111.0 fields. The branch either creates a Hook context or
        // delegates to the original context; recurse only through that exact
        // object graph and never consult a global/current action.
        FieldInfo? createdField = AccessTools.Field(
            typeof(BranchingPlayerChoiceContext),
            "_createdContext");
        FieldInfo? originalField = AccessTools.Field(
            typeof(BranchingPlayerChoiceContext),
            "_originalContext");
        if (createdField == null || originalField == null)
            return null;
        PlayerChoiceContext? selected =
            createdField.GetValue(context) as PlayerChoiceContext
            ?? originalField.GetValue(context) as PlayerChoiceContext;
        return selected != null && !ReferenceEquals(selected, context)
            && TryResolveExactAction(selected, visited, out GameAction? action)
                ? action
                : null;
    }

    internal static bool TryGet(object screen, out Binding? binding)
    {
        return Screens.TryGet(screen, out binding);
    }

    internal static bool TryReserve(object screen, out Binding? binding) =>
        Screens.TryReserve(screen, out binding);

    internal static bool TryConsume(object screen, Binding expected) =>
        Screens.TryConsume(screen, expected);

    internal static bool TryRelease(object screen, Binding expected) =>
        Screens.TryRelease(screen, expected);

    internal static void Forget(object screen) => Screens.Forget(screen);

    internal static string FamilyFor(object screen) => screen switch
    {
        NSimpleCardSelectScreen => "generic_simple_card_selector",
        MegaCrit.Sts2.Core.Nodes.Combat.NPlayerHand => "combat_hand_selector",
        NCardRewardSelectionScreen => "reward_nested.replacement_selection",
        NChooseACardSelectionScreen => "native_generated_card_choice",
        NDeckCardSelectScreen or NDeckUpgradeSelectScreen
            or NDeckTransformSelectScreen or NDeckEnchantSelectScreen =>
            "generic_deck_card_selector",
        NCombatPileCardSelectScreen => "generic_combat_pile_selector",
        NChooseABundleSelectionScreen => "generic_card_bundle_selector",
        _ => "generic_card_selector"
    };
}

/// <summary>
/// Exact v0.111.0 CardSelectCmd entry points that carry their owning
/// PlayerChoiceContext as a native argument. The context object, not a global
/// current-action lookup, flows to the exact selector factory through the
/// logical async invocation.
/// </summary>
[HarmonyPatch]
internal static class NativeGameActionCardSelectorParentPatch
{
    internal static IEnumerable<MethodBase> TargetMethods()
    {
        yield return Required(
            nameof(CardSelectCmd.FromChooseACardScreen),
            typeof(PlayerChoiceContext),
            typeof(IReadOnlyList<CardModel>),
            typeof(Player),
            typeof(bool));
        yield return Required(
            nameof(CardSelectCmd.FromSimpleGridForRewards),
            typeof(PlayerChoiceContext),
            typeof(List<CardCreationResult>),
            typeof(Player),
            typeof(CardSelectorPrefs));
        yield return Required(
            nameof(CardSelectCmd.FromSimpleGrid),
            typeof(PlayerChoiceContext),
            typeof(IReadOnlyList<CardModel>),
            typeof(Player),
            typeof(CardSelectorPrefs));
        yield return Required(
            nameof(CardSelectCmd.FromCombatPile),
            typeof(PlayerChoiceContext),
            typeof(CardPile),
            typeof(Player),
            typeof(CardSelectorPrefs),
            typeof(Func<CardModel, bool>));
    }

    private static MethodBase Required(string name, params Type[] arguments) =>
        AccessTools.Method(typeof(CardSelectCmd), name, arguments)
        ?? throw new MissingMethodException(typeof(CardSelectCmd).FullName, name);

    private static void Prefix(
        [HarmonyArgument(0)] PlayerChoiceContext context,
        MethodBase __originalMethod,
        out IDisposable? __state)
    {
        __state = NativeNestedCallbackSafety.Run(
            $"CardSelectCmd.{__originalMethod.Name}.exact_parent",
            () => NativeNestedSelectorBindings.EnterPlayerChoiceParent(
                context,
                __originalMethod.Name switch {
                    nameof(CardSelectCmd.FromCombatPile) => "generic_combat_pile_selector",
                    nameof(CardSelectCmd.FromChooseACardScreen) => "native_generated_card_choice",
                    _ => "generic_simple_card_selector"
                },
                $"CardSelectCmd.{__originalMethod.Name}"),
            fallback: null);
    }

    private static Exception? Finalizer(
        IDisposable? __state,
        Exception? __exception) =>
        NativeNestedCallbackSafety.Finalize(
            "CardSelectCmd.exact_parent.finalizer",
            __exception,
            () => __state?.Dispose());
}

/// <summary>
/// EventOption.Chosen is the exact option-owned outer Task. Its logical async
/// execution context owns any generic selector opened by that option.
/// </summary>
[HarmonyPatch]
internal static class NativeEventNestedSelectorParentPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(EventOption), nameof(EventOption.Chosen), Type.EmptyTypes)
        ?? throw new MissingMethodException(typeof(EventOption).FullName, nameof(EventOption.Chosen));

    private static void Prefix(EventOption __instance, out IDisposable? __state)
    {
        __state = NativeNestedCallbackSafety.Run(
            "EventOption.Chosen.nested_parent",
            () =>
            {
                NativeUiCompletionRootBindings.TryGet(__instance, out string? actionWitnessId);
                return actionWitnessId == null
                    ? null
                    : NativeNestedSelectorBindings.EnterParent(
                        actionWitnessId,
                        __instance,
                        "event_option.nested_selector",
                        "EventOption.Chosen");
            },
            fallback: null);
    }

    private static Exception? Finalizer(IDisposable? __state, Exception? __exception)
    {
        return NativeNestedCallbackSafety.Finalize(
            "EventOption.Chosen.nested_parent.finalizer",
            __exception,
            () => __state?.Dispose());
    }
}

[HarmonyPatch]
internal static class NativeNestedSelectorFactoryPatch
{
    internal static IEnumerable<MethodBase> TargetMethods()
    {
        yield return Required(typeof(NSimpleCardSelectScreen), "Create",
            typeof(IReadOnlyList<CardModel>), typeof(MegaCrit.Sts2.Core.CardSelection.CardSelectorPrefs));
        yield return Required(typeof(NSimpleCardSelectScreen), "Create",
            typeof(IReadOnlyList<CardCreationResult>),
            typeof(MegaCrit.Sts2.Core.CardSelection.CardSelectorPrefs));
        yield return Required(typeof(NCombatPileCardSelectScreen), "Create",
            typeof(CardPile),
            typeof(MegaCrit.Sts2.Core.CardSelection.CardSelectorPrefs),
            typeof(Func<CardModel, bool>));
        yield return Required(typeof(NDeckCardSelectScreen), "Create",
            typeof(IReadOnlyList<CardModel>), typeof(MegaCrit.Sts2.Core.CardSelection.CardSelectorPrefs));
        yield return Required(typeof(NDeckUpgradeSelectScreen), "ShowScreen",
            typeof(IReadOnlyList<CardModel>),
            typeof(MegaCrit.Sts2.Core.CardSelection.CardSelectorPrefs),
            typeof(MegaCrit.Sts2.Core.Runs.IRunState));
        yield return Required(typeof(NDeckTransformSelectScreen), "ShowScreen",
            typeof(IReadOnlyList<CardModel>),
            typeof(Func<CardModel, CardTransformation>),
            typeof(MegaCrit.Sts2.Core.CardSelection.CardSelectorPrefs));
        yield return Required(typeof(NDeckEnchantSelectScreen), "ShowScreen",
            typeof(IReadOnlyList<CardModel>),
            typeof(MegaCrit.Sts2.Core.Models.EnchantmentModel),
            typeof(int),
            typeof(MegaCrit.Sts2.Core.CardSelection.CardSelectorPrefs));
        yield return Required(typeof(NChooseABundleSelectionScreen), "ShowScreen",
            typeof(IReadOnlyList<IReadOnlyList<CardModel>>));
    }

    private static MethodBase Required(Type type, string name, params Type[] arguments) =>
        AccessTools.Method(type, name, arguments)
        ?? throw new MissingMethodException(type.FullName, name);

    private static void Postfix(object? __result, MethodBase __originalMethod)
    {
        try
        {
            NativeNestedSelectorBindings.Register(__result, __originalMethod);
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report(
                $"{__originalMethod.DeclaringType?.FullName}.{__originalMethod.Name}",
                exception);
        }
    }
}

/// <summary>
/// Patches only native terminal selector callbacks. Preview/cancel-preview
/// methods are intentionally absent. CompletionSource state independently
/// confirms that the callback actually completed the exact child screen.
/// </summary>
[HarmonyPatch]
internal static class NativeNestedSelectorAcceptedPatch
{
    internal static IEnumerable<MethodBase> TargetMethods()
    {
        yield return Required(typeof(NSimpleCardSelectScreen), "CompleteSelection", Type.EmptyTypes);
        yield return Required(typeof(NCombatPileCardSelectScreen), "CompleteSelection", Type.EmptyTypes);
        yield return Required(typeof(NDeckCardSelectScreen), "CloseSelection", typeof(NButton));
        yield return Required(typeof(NDeckCardSelectScreen), "ConfirmSelection", typeof(NButton));
        yield return Required(typeof(NDeckUpgradeSelectScreen), "CloseSelection", typeof(NButton));
        yield return Required(typeof(NDeckUpgradeSelectScreen), "ConfirmSelection", typeof(NButton));
        yield return Required(typeof(NDeckTransformSelectScreen), "CloseSelection", typeof(NButton));
        // Transform ConfirmSelection only opens the manual-confirmation
        // preview; the preview CompleteSelection callback is terminal.
        yield return Required(typeof(NDeckTransformSelectScreen), "CompleteSelection", typeof(NButton));
        yield return Required(typeof(NDeckEnchantSelectScreen), "CloseSelection", typeof(NButton));
        yield return Required(typeof(NDeckEnchantSelectScreen), "ConfirmSelection", typeof(NButton));
        yield return Required(typeof(NChooseABundleSelectionScreen), "ConfirmSelection", typeof(NButton));
    }

    private static MethodBase Required(Type type, string name, params Type[] arguments) =>
        AccessTools.Method(type, name, arguments)
        ?? throw new MissingMethodException(type.FullName, name);

    private static void Prefix(
        object __instance,
        MethodBase __originalMethod,
        out NativeNestedSelectorBindings.Binding? __state)
    {
        __state = NativeNestedCallbackSafety.Run(
            $"{__originalMethod.DeclaringType?.FullName}.{__originalMethod.Name}.reserve",
            () => NativeNestedSelectorBindings.TryReserve(__instance, out var binding)
                ? binding
                : null,
            fallback: null);
    }

    private static void Postfix(
        object __instance,
        MethodBase __originalMethod,
        NativeNestedSelectorBindings.Binding? __state)
    {
        NativeNestedSelectorBindings.Binding? binding = __state;
        bool reserved = binding != null;
        try
        {
            if (binding == null)
                return;
            if (!TryReadCompletedSelection(
                    __instance,
                    out bool taskCancelled,
                    out object[] selected,
                    out string? unavailable))
                unavailable = "completion_result_unavailable";

            if (!RecorderRuntime.SelectorInputOwns(__instance))
            {
                // Native pile revalidation can complete this Task without a
                // Human input. Preserve native lifecycle, never label it Human.
                if (unavailable == null)
                    reserved = !NativeNestedSelectorBindings.TryConsume(__instance, binding);
                return;
            }
            if (binding.NativeOrigin != null || RecorderRuntime.IsP1NativeProfile)
            {
                // EndSelectorInput owns the first-class Human decision. There
                // is no Human parent transition to receive a continuation.
                if (unavailable == null)
                    reserved = !NativeNestedSelectorBindings.TryConsume(__instance, binding);
                return;
            }
            bool explicitClose = string.Equals(
                __originalMethod.Name,
                "CloseSelection",
                StringComparison.Ordinal);
            if (string.Equals(binding.ActionWitnessId, "unavailable", StringComparison.Ordinal)
                || unavailable != null)
            {
                bool persisted = RecorderRuntime.ObserveNestedHumanContinuationUnavailable(
                    binding.ActionWitnessId,
                    $"{__originalMethod.DeclaringType?.FullName}.{__originalMethod.Name}",
                    string.Equals(binding.ActionWitnessId, "unavailable", StringComparison.Ordinal)
                        ? "exact_parent_root_unavailable"
                        : unavailable!);
                if (persisted)
                {
                    reserved = !NativeNestedSelectorBindings.TryConsume(
                        __instance,
                        binding);
                }
                return;
            }
            // A terminal native result may legitimately contain no cards (for
            // example a min-0 SimpleGrid).  Only an unreadable result is
            // unavailable; do not reconstruct native selector legality here.
            var operands = new Dictionary<string, object>(StringComparer.Ordinal);
            for (int index = 0; index < selected.Length; index++)
                operands[$"selected_{index}"] = selected[index];
            bool durable = RecorderRuntime.ObserveAcceptedNestedHumanContinuation(
                binding.ActionWitnessId,
                binding.Family,
                explicitClose || taskCancelled ? "cancel" : "select",
                __instance,
                $"{__originalMethod.DeclaringType?.FullName}.{__originalMethod.Name}",
                selected.FirstOrDefault(),
                operands,
                explicitClose || taskCancelled ? "cancelled" : "accepted");
            if (durable)
            {
                reserved = !NativeNestedSelectorBindings.TryConsume(
                    __instance,
                    binding);
            }
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report(
                $"{__originalMethod.DeclaringType?.FullName}.{__originalMethod.Name}",
                exception);
        }
        finally
        {
            if (reserved && binding != null)
                NativeNestedSelectorBindings.TryRelease(__instance, binding);
        }
    }

    private static Exception? Finalizer(
        object __instance,
        MethodBase __originalMethod,
        NativeNestedSelectorBindings.Binding? __state,
        Exception? __exception) =>
        NativeNestedCallbackSafety.Finalize(
            $"{__originalMethod.DeclaringType?.FullName}.{__originalMethod.Name}.finalizer",
            __exception,
            () =>
            {
                if (__state != null)
                    NativeNestedSelectorBindings.TryRelease(__instance, __state);
            });

    private static bool TryReadCompletedSelection(
        object screen,
        out bool cancelled,
        out object[] selected,
        out string? unavailable)
    {
        cancelled = false;
        selected = Array.Empty<object>();
        unavailable = null;
        try
        {
            FieldInfo? field = FindField(screen.GetType(), "_completionSource");
            if (field == null)
            {
                unavailable = "completion_source_field_unavailable";
                return true;
            }
            object? source = field.GetValue(screen);
            object? taskObject = source?.GetType().GetProperty("Task")?.GetValue(source);
            if (taskObject is not Task task)
            {
                unavailable = source == null
                    ? "completion_source_null"
                    : $"completion_task_unavailable:{source.GetType().FullName}";
                return true;
            }
            NativeTerminalTaskDisposition terminal =
                NativeTerminalTaskDisposition.Classify(task);
            unavailable = terminal.UnavailableReason;
            cancelled = terminal.IsCancelled;
            if (!terminal.IsTerminal || unavailable != null || cancelled)
                return true;
            object? result = task.GetType().GetProperty("Result")?.GetValue(task);
            if (result is not IEnumerable || result is string)
            {
                unavailable = result == null
                    ? "completion_result_null"
                    : $"completion_result_not_enumerable:{result.GetType().FullName}";
                return true;
            }
            var flattened = new List<object>();
            if (!TryFlatten(result, flattened, out unavailable))
                return true;
            selected = flattened.ToArray();
            return true;
        }
        catch (Exception exception)
        {
            unavailable = $"completion_result_read_failed:{exception.GetType().Name}";
            return true;
        }
    }

    private static bool TryFlatten(
        object? value,
        ICollection<object> selected,
        out string? unavailable)
    {
        unavailable = null;
        if (value is not IEnumerable values || value is string)
        {
            unavailable = value == null
                ? "nested_selection_item_null"
                : $"nested_selection_item_not_enumerable:{value.GetType().FullName}";
            return false;
        }
        foreach (object? item in values)
        {
            if (item is CardModel)
                selected.Add(item);
            else if (item is IEnumerable nested && item is not string)
            {
                if (!TryFlatten(nested, selected, out unavailable))
                    return false;
            }
            else
            {
                unavailable = item == null
                    ? "nested_selection_item_null"
                    : $"nested_selection_item_unsupported:{item.GetType().FullName}";
                return false;
            }
        }
        return true;
    }

    private static FieldInfo? FindField(Type type, string name)
    {
        for (Type? current = type; current != null; current = current.BaseType)
        {
            FieldInfo? field = AccessTools.Field(current, name);
            if (field != null)
                return field;
        }
        return null;
    }
}

[HarmonyPatch]
internal static class NativeNestedSelectorExitPatch
{
    internal static IEnumerable<MethodBase> TargetMethods()
    {
        yield return Required(typeof(NCardGridSelectionScreen), "_ExitTree");
        yield return Required(typeof(NChooseABundleSelectionScreen), "_ExitTree");
    }

    private static MethodBase Required(Type type, string name) =>
        AccessTools.Method(type, name, Type.EmptyTypes)
        ?? throw new MissingMethodException(type.FullName, name);

    private static void Finalizer(object __instance)
    {
        try
        {
            if (!NativeNestedSelectorBindings.TryReserve(
                    __instance,
                    out NativeNestedSelectorBindings.Binding? binding)
                || binding == null)
                return;
            if (binding.NativeOrigin != null || RecorderRuntime.IsP1NativeProfile)
            {
                NativeNestedSelectorBindings.TryConsume(__instance, binding);
                return;
            }
            bool persisted = RecorderRuntime.ObserveNestedHumanContinuationUnavailable(
                binding.ActionWitnessId,
                $"{__instance.GetType().FullName}._ExitTree",
                "selector_exited_without_durable_terminal");
            if (!persisted
                || !NativeNestedSelectorBindings.TryConsume(__instance, binding))
                NativeNestedSelectorBindings.TryRelease(__instance, binding);
        }
        catch (Exception exception)
        {
            NativeUiObservationSafety.Report(
                $"{__instance.GetType().FullName}._ExitTree",
                exception);
        }
    }
}

/// <summary>
/// Exact owner chain for the choices rendered on a card-reward screen.  The
/// screen is registered while CardReward.OnSelect is executing under the
/// reward-claim root; later callbacks use only that exact screen key and the
/// exact alternative index.  A reroll is special: the outer reward Task stays
/// open and therefore cannot be used as its Commit witness.
/// </summary>
internal static class NativeCardRewardAlternativeBindings
{
    private sealed record Parent(
        CardReward Reward,
        string RewardClaimWitnessId,
        Parent? Previous);

    internal sealed record ScreenBinding(
        CardReward Reward,
        string RewardClaimWitnessId,
        IReadOnlyList<CardRewardAlternative> Alternatives);

    internal sealed class RerollBinding
    {
        internal RerollBinding(
            string actionWitnessId,
            CardRewardAlternative alternative)
        {
            ActionWitnessId = actionWitnessId;
            Alternative = alternative;
        }

        internal string ActionWitnessId { get; }
        internal CardRewardAlternative Alternative { get; }
        internal TwoSignalCommitGate CommitGate { get; } = new();
    }

    private sealed class ParentScope : IDisposable
    {
        private readonly Parent? _previous;
        private bool _disposed;

        internal ParentScope(Parent? previous) => _previous = previous;

        public void Dispose()
        {
            if (_disposed)
                return;
            _disposed = true;
            Current.Value = _previous;
        }
    }

    private sealed class TaskBinding
    {
        internal TaskBinding(Task<bool> task) => Task = task;

        internal Task<bool> Task { get; }
    }

    private static readonly AsyncLocal<Parent?> Current = new();
    private static readonly ConditionalWeakTable<NCardRewardSelectionScreen, ScreenBinding> Screens = new();
    private static readonly ConditionalWeakTable<CardReward, TaskBinding> Tasks = new();
    private static readonly ConditionalWeakTable<CardReward, RerollBinding> Rerolls = new();
    private static readonly object Gate = new();

    internal static IDisposable? Enter(CardReward reward, string? rewardClaimWitnessId)
    {
        if (string.IsNullOrWhiteSpace(rewardClaimWitnessId))
            return null;
        Parent? previous = Current.Value;
        Current.Value = new Parent(reward, rewardClaimWitnessId, previous);
        return new ParentScope(previous);
    }

    internal static void RegisterScreen(
        NCardRewardSelectionScreen? screen,
        IReadOnlyList<CardRewardAlternative> alternatives)
    {
        Parent? parent = Current.Value;
        if (screen == null || parent == null)
            return;
        ArgumentNullException.ThrowIfNull(alternatives);
        CardRewardAlternative[] snapshot = alternatives.ToArray();
        lock (Gate)
        {
            if (Screens.TryGetValue(screen, out ScreenBinding? existing))
            {
                if (ReferenceEquals(existing.Reward, parent.Reward)
                    && string.Equals(
                        existing.RewardClaimWitnessId,
                        parent.RewardClaimWitnessId,
                        StringComparison.Ordinal))
                    return;
                throw new InvalidOperationException(
                    "The exact card-reward screen already belongs to another reward/root.");
            }
            Screens.Add(
                screen,
                new ScreenBinding(
                    parent.Reward,
                    parent.RewardClaimWitnessId,
                    snapshot));
        }
    }

    internal static void RefreshScreen(
        NCardRewardSelectionScreen screen,
        IReadOnlyList<CardRewardAlternative> alternatives)
    {
        ArgumentNullException.ThrowIfNull(alternatives);
        CardRewardAlternative[] snapshot = alternatives.ToArray();
        lock (Gate)
        {
            if (!Screens.TryGetValue(screen, out ScreenBinding? binding))
                return;
            Screens.Remove(screen);
            Screens.Add(screen, binding with { Alternatives = snapshot });
        }
    }

    internal static bool TryGetAlternative(
        NCardRewardSelectionScreen screen,
        int index,
        out ScreenBinding? binding,
        out CardRewardAlternative? alternative)
    {
        alternative = null;
        lock (Gate)
        {
            if (!Screens.TryGetValue(screen, out binding)
                || index < 0
                || index >= binding.Alternatives.Count)
                return false;
            alternative = binding.Alternatives[index];
            return true;
        }
    }

    internal static void RememberTask(CardReward reward, Task<bool> task)
    {
        lock (Gate)
        {
            if (Tasks.TryGetValue(reward, out TaskBinding? existing))
            {
                if (ReferenceEquals(existing.Task, task))
                    return;
                throw new InvalidOperationException(
                    "The exact CardReward already carries a different SelectUnsynchronized Task.");
            }
            Tasks.Add(reward, new TaskBinding(task));
        }
    }

    internal static bool TryTakeTask(CardReward reward, out Task<bool>? task)
    {
        task = null;
        lock (Gate)
        {
            if (!Tasks.TryGetValue(reward, out TaskBinding? binding))
                return false;
            Tasks.Remove(reward);
            task = binding.Task;
            return true;
        }
    }

    internal static RerollBinding BeginReroll(
        CardReward reward,
        string actionWitnessId,
        CardRewardAlternative alternative)
    {
        lock (Gate)
        {
            if (Rerolls.TryGetValue(reward, out RerollBinding? existing))
            {
                if (string.Equals(existing.ActionWitnessId, actionWitnessId, StringComparison.Ordinal)
                    && ReferenceEquals(existing.Alternative, alternative))
                    return existing;
                throw new InvalidOperationException(
                    "The exact CardReward already carries another reroll root.");
            }
            var binding = new RerollBinding(actionWitnessId, alternative);
            Rerolls.Add(reward, binding);
            return binding;
        }
    }

    internal static bool TryGetReroll(CardReward reward, out RerollBinding? binding) =>
        TryGetRerollCore(reward, out binding);

    internal static bool MarkAccepted(
        CardReward reward,
        RerollBinding expected)
    {
        lock (Gate)
        {
            if (!Rerolls.TryGetValue(reward, out RerollBinding? current)
                || !ReferenceEquals(current, expected))
                return false;
            return current.CommitGate.ObserveFirst();
        }
    }

    internal static bool MarkRerolled(
        CardReward reward,
        RerollBinding expected)
    {
        lock (Gate)
        {
            if (!Rerolls.TryGetValue(reward, out RerollBinding? current)
                || !ReferenceEquals(current, expected))
                return false;
            return current.CommitGate.ObserveSecond();
        }
    }

    internal static bool TryBeginCommit(
        CardReward reward,
        RerollBinding expected)
    {
        lock (Gate)
        {
            if (!Rerolls.TryGetValue(reward, out RerollBinding? current)
                || !ReferenceEquals(current, expected)
                || !current.CommitGate.TryReserveCommit())
                return false;
            return true;
        }
    }

    internal static void CommitFailed(
        CardReward reward,
        RerollBinding expected)
    {
        lock (Gate)
        {
            if (Rerolls.TryGetValue(reward, out RerollBinding? current)
                && ReferenceEquals(current, expected))
                current.CommitGate.TryReleaseCommit();
        }
    }

    private static bool TryGetRerollCore(CardReward reward, out RerollBinding? binding)
    {
        lock (Gate)
            return Rerolls.TryGetValue(reward, out binding);
    }

    internal static void EndReroll(CardReward reward, RerollBinding binding)
    {
        lock (Gate)
        {
            if (Rerolls.TryGetValue(reward, out RerollBinding? current)
                && ReferenceEquals(current, binding))
                Rerolls.Remove(reward);
        }
    }

    internal static void ForgetScreen(NCardRewardSelectionScreen screen)
    {
        lock (Gate)
        {
            if (!Screens.TryGetValue(screen, out ScreenBinding? binding))
                return;
            Screens.Remove(screen);
            Tasks.Remove(binding.Reward);
            Rerolls.Remove(binding.Reward);
        }
    }
}

[HarmonyPatch]
internal static class NativeCardRewardParentPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(CardReward), "OnSelect", Type.EmptyTypes)
        ?? throw new MissingMethodException(typeof(CardReward).FullName, "OnSelect");

    private static void Prefix(CardReward __instance, out IDisposable? __state)
    {
        __state = NativeNestedCallbackSafety.Run(
            "CardReward.OnSelect.parent",
            () =>
            {
                NativeUiCompletionRootBindings.TryGet(__instance, out string? root);
                return NativeCardRewardAlternativeBindings.Enter(__instance, root);
            },
            fallback: null);
    }

    private static Exception? Finalizer(IDisposable? __state, Exception? __exception)
    {
        return NativeNestedCallbackSafety.Finalize(
            "CardReward.OnSelect.parent.finalizer",
            __exception,
            () => __state?.Dispose());
    }
}

[HarmonyPatch]
internal static class NativeCardRewardScreenBindingPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(NCardRewardSelectionScreen),
            "ShowScreen",
            new[]
            {
                typeof(IReadOnlyList<CardCreationResult>),
                typeof(IReadOnlyList<CardRewardAlternative>)
            })
        ?? throw new MissingMethodException(
            typeof(NCardRewardSelectionScreen).FullName,
            "ShowScreen");

    private static void Postfix(
        [HarmonyArgument(1)] IReadOnlyList<CardRewardAlternative> alternatives,
        NCardRewardSelectionScreen? __result) =>
        NativeNestedCallbackSafety.Run(
            "NCardRewardSelectionScreen.ShowScreen.binding",
            () => NativeCardRewardAlternativeBindings.RegisterScreen(__result, alternatives));
}

[HarmonyPatch]
internal static class NativeCardRewardScreenRefreshBindingPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(NCardRewardSelectionScreen),
            nameof(NCardRewardSelectionScreen.RefreshOptions),
            new[]
            {
                typeof(IReadOnlyList<CardCreationResult>),
                typeof(IReadOnlyList<CardRewardAlternative>)
            })
        ?? throw new MissingMethodException(
            typeof(NCardRewardSelectionScreen).FullName,
            nameof(NCardRewardSelectionScreen.RefreshOptions));

    private static void Postfix(
        NCardRewardSelectionScreen __instance,
        [HarmonyArgument(1)] IReadOnlyList<CardRewardAlternative> alternatives) =>
        NativeNestedCallbackSafety.Run(
            "NCardRewardSelectionScreen.RefreshOptions.binding",
            () => NativeCardRewardAlternativeBindings.RefreshScreen(__instance, alternatives));
}

[HarmonyPatch]
internal static class NativeRewardSelectTaskBindingPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(Reward), nameof(Reward.SelectUnsynchronized), Type.EmptyTypes)
        ?? throw new MissingMethodException(
            typeof(Reward).FullName,
            nameof(Reward.SelectUnsynchronized));

    private static void Prefix(Reward __instance, out IDisposable? __state)
    {
        __state = NativeNestedCallbackSafety.Run(
            "Reward.SelectUnsynchronized.nested_parent",
            () =>
            {
                NativeUiCompletionRootBindings.TryGet(__instance, out string? root);
                return root == null
                    ? null
                    : NativeNestedSelectorBindings.EnterGenericSelectorParent(
                        root,
                        __instance,
                        "Reward.SelectUnsynchronized");
            },
            fallback: null);
    }

    private static void Postfix(Reward __instance, Task<bool> __result)
    {
        NativeNestedCallbackSafety.Run(
            "Reward.SelectUnsynchronized.task_binding",
            () =>
            {
                if (__instance is CardReward reward && __result != null)
                    NativeCardRewardAlternativeBindings.RememberTask(reward, __result);
            });
    }

    private static Exception? Finalizer(
        IDisposable? __state,
        Exception? __exception) =>
        NativeNestedCallbackSafety.Finalize(
            "Reward.SelectUnsynchronized.nested_parent.finalizer",
            __exception,
            () => __state?.Dispose());
}

[HarmonyPatch]
internal static class NativeCardRewardAlternativePatch
{
    private const string NativeActionType =
        "NCardRewardSelectionScreen.OnAlternateRewardSelected";

    private readonly record struct PatchState(
        NativeUiScopeEntry Scope,
        NativeCardRewardAlternativeBindings.ScreenBinding? Binding,
        CardRewardAlternative? Alternative,
        NativeCardRewardAlternativeBindings.RerollBinding? Reroll);

    internal static MethodBase TargetMethod() =>
        AccessTools.Method(
            typeof(NCardRewardSelectionScreen),
            "OnAlternateRewardSelected",
            new[] { typeof(int) })
        ?? throw new MissingMethodException(
            typeof(NCardRewardSelectionScreen).FullName,
            "OnAlternateRewardSelected");

    private static void Prefix(
        NCardRewardSelectionScreen __instance,
        [HarmonyArgument(0)] int index,
        out PatchState __state)
    {
        __state = NativeNestedCallbackSafety.Run(
            "NCardRewardSelectionScreen.OnAlternateRewardSelected.prefix",
            () => CreateState(__instance, index),
            fallback: default);
    }

    private static PatchState CreateState(
        NCardRewardSelectionScreen screen,
        int index)
    {
        if (!NativeCardRewardAlternativeBindings.TryGetAlternative(
                screen,
                index,
                out NativeCardRewardAlternativeBindings.ScreenBinding? binding,
                out CardRewardAlternative? alternative)
            || binding == null
            || alternative == null)
        {
            return default;
        }
        bool reroll = string.Equals(alternative.OptionId, "REROLL", StringComparison.Ordinal);
        NativeUiScopeEntry scope = RecorderRuntime.TryEnterSemanticScope(
            "native_card_reward_alternative_ui",
            NativeActionType,
            new ProcessLocalObservedAction(
                "activate",
                alternative,
                new Dictionary<string, object>(StringComparer.Ordinal)),
            new NativePostCommitCompletionExpectation(
                "card_reward_alternative",
                reroll ? "CardReward.Reroll" : "Reward.SelectUnsynchronized",
                NativeOwnerWitnessId: reroll
                    ? NativeWitnessIdentity.Get(binding.Reward, "native_owner")
                    : null,
                NativeOperandWitnessId: NativeWitnessIdentity.Get(
                    alternative,
                    "native_operand")),
            new ProcessLocalObservedAction(
                "activate",
                alternative,
                new Dictionary<string, object>(StringComparer.Ordinal)));
        try
        {
            NativeCardRewardAlternativeBindings.RerollBinding? rerollBinding =
                reroll && scope.ActionWitnessId is { } root
                    ? NativeCardRewardAlternativeBindings.BeginReroll(
                        binding.Reward,
                        root,
                        alternative)
                    : null;
            return new PatchState(scope, binding, alternative, rerollBinding);
        }
        catch
        {
            // Prefix safety converts the collision to a diagnostic, but this
            // scope must be unwound here because no PatchState is returned.
            RecorderRuntime.ExitNativeUiScope(scope);
            throw;
        }
    }

    private static void Postfix(
        NCardRewardSelectionScreen __instance,
        [HarmonyArgument(0)] int index,
        PatchState __state)
    {
        NativeNestedCallbackSafety.Run(
            "NCardRewardSelectionScreen.OnAlternateRewardSelected.postfix",
            () => ObserveAccepted(__instance, index, __state));
    }

    private static void ObserveAccepted(
        NCardRewardSelectionScreen screen,
        int index,
        PatchState state)
    {
        if ((!state.Scope.Entered && !state.Scope.DeferredFailure)
            || state.Binding is not { } binding
            || state.Alternative is not { } alternative)
            return;
        bool accepted = RecorderRuntime.ObserveAcceptedSemanticUiAction(
            NativeActionType,
            new ProcessLocalObservedAction(
                "activate",
                alternative,
                new Dictionary<string, object>(StringComparer.Ordinal)),
            new NativeWitnessEvidence(
                "native_card_reward_alternative_ui",
                NativeActionType,
                NativeWitnessIdentity.Get(alternative, "card_reward_alternative"),
                new Dictionary<string, string>(StringComparer.Ordinal)
                {
                    ["alternative_index"] = index.ToString(System.Globalization.CultureInfo.InvariantCulture),
                    ["reward_claim_root"] = binding.RewardClaimWitnessId
                },
                DateTimeOffset.UtcNow),
            captureImmediatePostCommitBoundary: false,
            actionWitnessId: state.Scope.ActionWitnessId);
        if (!accepted)
        {
            if (state.Reroll is { } failedReroll)
                NativeCardRewardAlternativeBindings.EndReroll(binding.Reward, failedReroll);
            return;
        }

        if (state.Reroll is { } reroll)
        {
            if (NativeCardRewardAlternativeBindings.MarkAccepted(binding.Reward, reroll))
                TryCommitReroll(binding.Reward, alternative, reroll);
        }
        else if (state.Scope.ActionWitnessId is { } root
                 && NativeCardRewardAlternativeBindings.TryTakeTask(
                     binding.Reward,
                     out Task<bool>? task)
                 && task != null)
        {
            RecorderRuntime.QueueNativePostCommitBoundary(
                (Task)task,
                "Reward.SelectUnsynchronized",
                nativeOperand: alternative,
                expectedActionWitnessId: root);
        }
        else if (state.Scope.ActionWitnessId is { } missingTaskRoot)
        {
            RecorderRuntime.ObserveSemanticUiNativeCommitBindingFailure(
                missingTaskRoot,
                "card_reward_alternative",
                "Reward.SelectUnsynchronized",
                "The exact CardReward carried no SelectUnsynchronized Task.");
        }
    }

    internal static void TryCommitReroll(
        CardReward reward,
        CardRewardAlternative alternative,
        NativeCardRewardAlternativeBindings.RerollBinding binding)
    {
        if (!NativeCardRewardAlternativeBindings.TryBeginCommit(reward, binding))
            return;
        bool durable = RecorderRuntime.ObserveSemanticUiNativeCommit(
            binding.ActionWitnessId,
            "card_reward_alternative",
            "CardReward.Reroll",
            nativeOwner: reward,
            nativeOperand: alternative);
        if (durable)
            NativeCardRewardAlternativeBindings.EndReroll(reward, binding);
        else
        {
            NativeCardRewardAlternativeBindings.CommitFailed(reward, binding);
            RecorderRuntime.ObserveSemanticUiNativeCommitBindingFailure(
                binding.ActionWitnessId,
                "card_reward_alternative",
                "CardReward.Reroll",
                "The exact reroll Commit could not be persisted; its exact carrier remains bound.");
        }
    }

    private static Exception? Finalizer(PatchState __state, Exception? __exception)
    {
        return NativeNestedCallbackSafety.Finalize(
            "NCardRewardSelectionScreen.OnAlternateRewardSelected.finalizer",
            __exception,
            () =>
            {
                if (__state.Binding is { } binding
                    && __state.Reroll is { } reroll
                    && __exception != null)
                    NativeCardRewardAlternativeBindings.EndReroll(binding.Reward, reroll);
                RecorderRuntime.ExitNativeUiScope(__state.Scope);
            });
    }
}

[HarmonyPatch]
internal static class NativeCardRewardRerollCompletionPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(CardReward), nameof(CardReward.Reroll), Type.EmptyTypes)
        ?? throw new MissingMethodException(typeof(CardReward).FullName, nameof(CardReward.Reroll));

    private static void Postfix(CardReward __instance)
    {
        NativeNestedCallbackSafety.Run(
            "CardReward.Reroll.completion",
            () => ObserveReroll(__instance));
    }

    private static void ObserveReroll(CardReward reward)
    {
        if (!NativeCardRewardAlternativeBindings.TryGetReroll(
                reward,
                out NativeCardRewardAlternativeBindings.RerollBinding? binding)
            || binding == null)
            return;
        bool accepted = NativeCardRewardAlternativeBindings.MarkRerolled(reward, binding);
        // If TaskCompletionSource continuations ran inline, acceptance is
        // published by the enclosing callback immediately afterward.  If
        // they ran asynchronously, this call publishes the exact Commit now.
        // The shared exact-object carrier makes either ordering equivalent.
        if (accepted)
        {
            // The exact alternative is still recoverable from the active
            // screen binding; use the stable REROLL option identity only.
            NativeCardRewardAlternativePatch.TryCommitReroll(
                reward,
                binding.Alternative,
                binding);
        }
    }
}

[HarmonyPatch]
internal static class NativeCardRemovalRewardNestedSelectorPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(CardRemovalReward), "OnSelect", Type.EmptyTypes)
        ?? throw new MissingMethodException(typeof(CardRemovalReward).FullName, "OnSelect");

    private static void Prefix(CardRemovalReward __instance, out IDisposable? __state)
    {
        __state = NativeNestedCallbackSafety.Run(
            "CardRemovalReward.OnSelect.nested_parent",
            () =>
            {
                NativeUiCompletionRootBindings.TryGet(__instance, out string? root);
                return root == null
                    ? null
                    : NativeNestedSelectorBindings.EnterParent(
                        root,
                        __instance,
                        "reward_card_removal.nested_selector",
                        "CardRemovalReward.OnSelect");
            },
            fallback: null);
    }

    private static Exception? Finalizer(IDisposable? __state, Exception? __exception)
    {
        return NativeNestedCallbackSafety.Finalize(
            "CardRemovalReward.OnSelect.nested_parent.finalizer",
            __exception,
            () => __state?.Dispose());
    }
}

[HarmonyPatch]
internal static class NativeCardRewardScreenExitPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NCardRewardSelectionScreen), "_ExitTree", Type.EmptyTypes)
        ?? throw new MissingMethodException(
            typeof(NCardRewardSelectionScreen).FullName,
            "_ExitTree");

    private static void Finalizer(NCardRewardSelectionScreen __instance) =>
        NativeNestedCallbackSafety.Run(
            "NCardRewardSelectionScreen._ExitTree.cleanup",
            () => NativeCardRewardAlternativeBindings.ForgetScreen(__instance));
}
