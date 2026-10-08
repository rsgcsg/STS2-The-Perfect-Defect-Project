using System.Collections;
using System.Reflection;
using Godot;
using HarmonyLib;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.GameActions.Multiplayer;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using STS2Connector.PlayerEnvironment.Witness;

namespace STS2HumanAnnotator.Mod;

/// <summary>Read-only, exact native selection state. No inferred legality.</summary>
internal static class NativeSelectorInputFacts
{
    internal static object? Field(object owner, string name) =>
        AccessTools.Field(owner.GetType(), name)?.GetValue(owner);

    internal static object[] Selected(object owner) =>
        Field(owner, "_selectedCards") is IEnumerable selected
            ? selected.Cast<object>().ToArray()
            : Field(owner, "_selectedBundle") is { } bundle ? new[] { bundle } : Array.Empty<object>();

    internal static bool Terminal(object owner) =>
        Field(owner, "_selectionCompletionSource") is { } hand ? TaskCompleted(hand)
        : Field(owner, "_completionSource") is { } screen ? TaskCompleted(screen)
        : Field(owner, "_screenComplete") is true;

    internal static Task? CompletionTask(object owner)
    {
        var source = Field(owner, "_selectionCompletionSource") ?? Field(owner, "_completionSource");
        return source?.GetType().GetProperty("Task")?.GetValue(source) as Task;
    }
    private static bool TaskCompleted(object source) =>
        source.GetType().GetProperty("Task")?.GetValue(source) is Task { IsCompletedSuccessfully: true };

    internal static bool Changed(object owner, object[] before) =>
        !before.ToHashSet(ReferenceEqualityComparer.Instance).SetEquals(Selected(owner));

    internal static string[] CardOperations(object owner, bool deselect) => owner switch
    {
        NPlayerHand => new[] { deselect ? "deselect_combat_hand_card" : "select_combat_hand_card" },
        NCombatPileCardSelectScreen => new[] { deselect ? "deselect_native_combat_pile_card" : "select_native_combat_pile_card" },
        NSimpleCardSelectScreen => new[] { deselect ? "deselect_simple_card" : "select_simple_card" },
        NDeckCardSelectScreen => new[] { deselect ? "deselect_native_deck_card" : "select_native_deck_card" },
        NDeckUpgradeSelectScreen => new[] { deselect ? "deselect_deck_upgrade_card" : "select_deck_upgrade_card" },
        NDeckTransformSelectScreen => new[] { "toggle_deck_transform_card" },
        NDeckEnchantSelectScreen => new[] { "toggle_card" },
        NCardRewardSelectionScreen => new[] { "select_card_reward", "choose_card_reward_alternative" },
        NChooseACardSelectionScreen => new[] { "select_visible_card" },
        NChooseABundleSelectionScreen => new[] { "preview_card_bundle" },
        _ => Array.Empty<string>()
    };

    internal static string[] ControlOperations(string field) => field switch
    {
        "_confirmButton" or "_selectModeConfirmButton" => new[] {
            "confirm_combat_hand_selection", "confirm_native_combat_pile_selection",
            "confirm_simple_card_selection", "open_player_environment_deck_preview",
            "preview_selection", "preview_deck_transform", "confirm_card_bundle" },
        "_previewConfirmButton" or "_singlePreviewConfirmButton" or "_multiPreviewConfirmButton" => new[] { "confirm_player_environment_deck_selection",
            "confirm_deck_upgrade", "confirm_selection", "confirm_deck_transform", "confirm_card_bundle" },
        "_previewCancelButton" or "_singlePreviewCancelButton" or "_multiPreviewCancelButton" => new[] { "cancel_player_environment_deck_preview",
            "cancel_deck_upgrade_preview", "cancel_preview", "cancel_deck_transform_preview", "cancel_card_bundle_preview" },
        "_closeButton" => new[] { "cancel_native_combat_pile_selection", "cancel_simple_card_selection",
            "cancel_player_environment_deck_selection", "cancel_deck_upgrade_selection", "close_selection",
            "cancel_deck_transform_selection", "close_combat_hand_peek" },
        "_skipButton" => new[] { "skip_visible_choice" },
        _ => Array.Empty<string>()
    };

}

[HarmonyPatch]
internal static class NativeHandDecisionParentPatch
{
    internal static IEnumerable<MethodBase> TargetMethods() => typeof(CardSelectCmd)
        .GetMethods(BindingFlags.Static | BindingFlags.Public)
        .Where(method => method.Name is "FromHand" or "FromHandForDiscard" or "FromHandForUpgrade" or "FromChooseACardScreen")
        .Where(method => method.GetParameters().FirstOrDefault()?.ParameterType == typeof(PlayerChoiceContext));

    private static void Prefix([HarmonyArgument(0)] PlayerChoiceContext context,
        MethodBase __originalMethod, out IDisposable? __state) =>
        __state = NativeNestedCallbackSafety.Run("selector.context", () =>
            NativeNestedSelectorBindings.EnterPlayerChoiceParent(context,
                __originalMethod.Name == "FromChooseACardScreen" ? "native_generated_card_choice" : "combat_hand_selector",
                $"CardSelectCmd.{__originalMethod.Name}"), null);

    private static Exception? Finalizer(IDisposable? __state, Exception? __exception) =>
        NativeNestedCallbackSafety.Finalize("selector.context.exit", __exception, () => __state?.Dispose());
}

[HarmonyPatch]
internal static class NativeHandDecisionFactoryPatch
{
    internal static MethodBase TargetMethod() => AccessTools.Method(typeof(NPlayerHand), "SelectCards");
    private static void Postfix(NPlayerHand __instance, MethodBase __originalMethod) =>
        NativeNestedCallbackSafety.Run("hand.selector.bind", () =>
        {
            // The same hand node is reused. A new SelectCards invocation owns a
            // new exact selection episode; stale bindings cannot cross it.
            NativeNestedSelectorBindings.Forget(__instance);
            NativeNestedSelectorBindings.Register(__instance, __originalMethod);
        });
}

[HarmonyPatch]
internal static class NativeGeneratedDecisionFactoryPatch
{
    internal static MethodBase TargetMethod() => AccessTools.Method(typeof(NChooseACardSelectionScreen), "ShowScreen");
    private static void Postfix(NChooseACardSelectionScreen __result, MethodBase __originalMethod) =>
        NativeNestedCallbackSafety.Run("generated.selector.bind", () => NativeNestedSelectorBindings.Register(__result, __originalMethod));
}

[HarmonyPatch]
internal static class NativeSelectorCardDecisionPatch
{
    internal sealed record State(RecorderRuntime.SelectorInputHandle? Input, object[] Selected, bool Terminal, Task? Completion);
    internal static IEnumerable<MethodBase> TargetMethods()
    {
        foreach (Type type in new[] { typeof(NSimpleCardSelectScreen), typeof(NCombatPileCardSelectScreen),
            typeof(NDeckCardSelectScreen), typeof(NDeckUpgradeSelectScreen),
            typeof(NDeckTransformSelectScreen), typeof(NDeckEnchantSelectScreen) })
            yield return AccessTools.DeclaredMethod(type, "OnCardClicked")
                ?? throw new MissingMethodException(type.FullName, "OnCardClicked");
        yield return AccessTools.Method(typeof(NPlayerHand), "SelectCardInSimpleMode");
        yield return AccessTools.Method(typeof(NPlayerHand), "SelectCardInUpgradeMode");
        yield return AccessTools.Method(typeof(NChooseACardSelectionScreen), "SelectHolder");
        yield return AccessTools.Method(typeof(NCardRewardSelectionScreen), "SelectCard");
        yield return AccessTools.Method(typeof(NCardRewardSelectionScreen), "OnAlternateRewardSelected");
        yield return AccessTools.Method(typeof(NChooseABundleSelectionScreen), "OnBundleClicked");
    }

    [HarmonyPriority(Priority.First)]
    private static void Prefix(object __instance, object __0, MethodBase __originalMethod, out State? __state)
    {
        if (RecorderRuntime.SelectorInputActive && !RecorderRuntime.IsOrderedSourceProfile) { __state = null; return; }
        __state = NativeNestedCallbackSafety.Run("selector.card.before", () =>
        {
            object? subject = __0 switch
            {
                CardModel card => card,
                NCardHolder holder => holder.CardModel,
                MegaCrit.Sts2.Core.Nodes.Cards.NCard card => card.Model,
                _ => __0
            };
            if (__instance is NCardRewardSelectionScreen rewardScreen && __0 is int alternativeIndex)
            {
                if (!NativeCardRewardAlternativeBindings.TryGetAlternative(rewardScreen, alternativeIndex, out _, out var alternative))
                    return null;
                subject = alternative;
            }
            var before = NativeSelectorInputFacts.Selected(__instance);
            bool wasTerminal = NativeSelectorInputFacts.Terminal(__instance);
            var completion = NativeSelectorInputFacts.CompletionTask(__instance);
            string verb = __originalMethod.Name == "DeselectCard"
                || before.Any(value => ReferenceEquals(value, subject)) ? "deselect" : "select";
            return new State(RecorderRuntime.BeginSelectorInputWithSourceVerb(__instance,
                $"{__originalMethod.DeclaringType!.FullName}.{__originalMethod.Name}",
                subject, verb, NativeSelectorInputFacts.CardOperations(__instance, verb == "deselect")), before, wasTerminal, completion);
        }, null);
    }

    [HarmonyPriority(Priority.Last)]
    private static void Postfix(object __instance, State? __state)
    {
        if (__state == null) return;
        NativeNestedCallbackSafety.Run("selector.card.after", () =>
            RecorderRuntime.EndSelectorInput(__state.Input,
                NativeSelectorInputFacts.Changed(__instance, __state.Selected)
                    || !__state.Terminal && (__state.Completion?.IsCompletedSuccessfully
                        ?? NativeSelectorInputFacts.Terminal(__instance)),
                NativeSelectorInputFacts.Terminal(__instance),
                sourceAcceptanceProven: NativeSelectorInputFacts.Changed(__instance, __state.Selected)));
    }

    private static Exception? Finalizer(State? __state, Exception? __exception) =>
        NativeNestedCallbackSafety.Finalize("selector.card.finally", __exception, () =>
        {
            if (__exception != null) RecorderRuntime.EndSelectorInput(__state?.Input, false, false);
        });
}

/// <summary>
/// Captures exact button delivery before OnRelease withdraws its affordance.
/// Only a control stored by its exact bound selector owner is admitted. Native
/// ForceClick does not enter this Human handler; controller ownership is also
/// rejected by RecorderRuntime's ordinary admission checks.
/// </summary>
[HarmonyPatch]
internal static class NativeSelectorControlDecisionPatch
{
    internal sealed record State(RecorderRuntime.SelectorInputHandle? Input, object Owner, object[] Selected,
        bool Terminal, string Verb);
    internal static MethodBase TargetMethod() => AccessTools.Method(typeof(NClickableControl), "OnReleaseHandler");
    [HarmonyPriority(Priority.First)]
    private static void Prefix(NClickableControl __instance, out State? __state)
    {
        if (RecorderRuntime.SelectorInputActive && !RecorderRuntime.IsOrderedSourceProfile) { __state = null; return; }
        __state = NativeNestedCallbackSafety.Run("selector.control.before", () =>
        {
            if (NativeSelectorInputFacts.Field(__instance, "_isPressed") is not true)
                return null;
            for (Node? owner = __instance.GetParent(); owner != null; owner = owner.GetParent())
            {
                if (RecorderRuntime.IsOrderedSourceProfile)
                {
                    if (owner is not (NPlayerHand or NSimpleCardSelectScreen or NCombatPileCardSelectScreen
                        or NDeckCardSelectScreen or NDeckUpgradeSelectScreen or NDeckTransformSelectScreen
                        or NDeckEnchantSelectScreen or NCardRewardSelectionScreen or NChooseACardSelectionScreen
                        or NChooseABundleSelectionScreen)) continue;
                }
                else if (!NativeNestedSelectorBindings.TryGet(owner, out var binding) || binding == null) continue;
                string? verb = null;
                string? controlField = null;
                foreach (var (field, action) in new[] {
                    ("_confirmButton", "confirm"), ("_selectModeConfirmButton", "confirm"),
                    ("_previewConfirmButton", "confirm"), ("_previewCancelButton", "cancel"),
                    ("_singlePreviewConfirmButton", "confirm"), ("_multiPreviewConfirmButton", "confirm"),
                    ("_singlePreviewCancelButton", "cancel"), ("_multiPreviewCancelButton", "cancel"),
                    ("_closeButton", "cancel"), ("_skipButton", "skip") })
                {
                    if (ReferenceEquals(NativeSelectorInputFacts.Field(owner, field), __instance))
                    { verb = action; controlField = field; }
                }
                if (verb == null) return null;
                var input = RecorderRuntime.BeginSelectorInputWithSourceVerb(owner,
                    $"{owner.GetType().FullName}.{__instance.Name}.Released",
                    null, verb, NativeSelectorInputFacts.ControlOperations(controlField!));
                return new State(input, owner, NativeSelectorInputFacts.Selected(owner),
                    NativeSelectorInputFacts.Terminal(owner), verb);
            }
            return null;
        }, null);
    }
    [HarmonyPriority(Priority.Last)]
    private static void Postfix(State? __state)
    {
        if (__state == null) return;
        NativeNestedCallbackSafety.Run("selector.control.after", () =>
        {
            bool terminal = NativeSelectorInputFacts.Terminal(__state.Owner);
            // Exact pressed/enabled control bound to a native advertised action;
            // preview/confirm can change only UI state without selected-set change.
            RecorderRuntime.EndSelectorInput(__state.Input, true, terminal);
        });
    }
    private static Exception? Finalizer(State? __state, Exception? __exception) =>
        NativeNestedCallbackSafety.Finalize("selector.control.finally", __exception, () =>
        { if (__exception != null) RecorderRuntime.EndSelectorInput(__state?.Input, false, false); });
}

/// <summary>Native revalidation and replacement are not Human deselect inputs.</summary>
[HarmonyPatch]
internal static class NativeAutomaticHandDeselectScopePatch
{
    [ThreadStatic] private static int _depth;
    internal static bool Active => _depth != 0;
    internal static MethodBase TargetMethod() => AccessTools.Method(typeof(NSelectedHandCardContainer), "DeselectCard");
    private static void Prefix() => _depth++;
    private static Exception? Finalizer(Exception? __exception) { _depth--; return __exception; }
}


[HarmonyPatch]
internal static class NativeCardRewardDecisionFactoryPatch
{
    internal static MethodBase TargetMethod() => AccessTools.Method(typeof(NCardRewardSelectionScreen), "ShowScreen");
    private static void Postfix(NCardRewardSelectionScreen? __result, MethodBase __originalMethod) =>
        NativeNestedCallbackSafety.Run("card_reward.selector.bind", () => NativeNestedSelectorBindings.Register(__result, __originalMethod));
}
