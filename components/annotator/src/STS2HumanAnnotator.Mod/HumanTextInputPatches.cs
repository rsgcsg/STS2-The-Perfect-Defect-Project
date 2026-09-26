using System.Reflection;
using HarmonyLib;
using Godot;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;

namespace STS2HumanAnnotator.Mod;

/// <summary>Independent read-only Human text input side stream. The existing
/// card-play scope, semantic tracker and native method keep their behavior.</summary>
[HarmonyPatch]
internal static class HumanTextCardStartPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NPlayerHand), "StartCardPlay")
        ?? throw new MissingMethodException(typeof(NPlayerHand).FullName, "StartCardPlay");

    private static void Prefix(
        NPlayerHand __instance,
        [HarmonyArgument(0)] NHandCardHolder holder,
        out RecorderRuntime.HumanTextCardScope? __state) =>
        __state = RecorderRuntime.BeginHumanTextCardInput(__instance, holder);

    private static Exception? Finalizer(
        RecorderRuntime.HumanTextCardScope? __state, Exception? __exception)
    {
        NativeNestedCallbackSafety.Run("human_text_input.finalizer", () =>
            RecorderRuntime.FinishHumanTextCardInput(__state, __exception));
        return __exception;
    }
}

[HarmonyPatch]
internal static class HumanTextCardFactoryPatch
{
    internal static IEnumerable<MethodBase> TargetMethods()
    {
        yield return AccessTools.Method(typeof(NMouseCardPlay), nameof(NMouseCardPlay.Create))
            ?? throw new MissingMethodException(typeof(NMouseCardPlay).FullName,
                nameof(NMouseCardPlay.Create));
        yield return AccessTools.Method(typeof(NControllerCardPlay), nameof(NControllerCardPlay.Create))
            ?? throw new MissingMethodException(typeof(NControllerCardPlay).FullName,
                nameof(NControllerCardPlay.Create));
    }

    private static void Postfix(NCardPlay __result)
    {
        if (__result != null)
            NativeNestedCallbackSafety.Run("human_text_input.factory", () =>
                RecorderRuntime.BindHumanTextCardCarrier(__result));
    }
}

[HarmonyPatch]
internal static class HumanTextControllerStartPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NControllerCardPlay), nameof(NControllerCardPlay.Start))
        ?? throw new MissingMethodException(typeof(NControllerCardPlay).FullName, "Start");

    private static void Prefix(NControllerCardPlay __instance,
        out NControllerCardPlay? __state) =>
        __state = RecorderRuntime.BeginHumanTextTargetSetup(__instance);

    private static Exception? Finalizer(NControllerCardPlay? __state, Exception? __exception)
    {
        RecorderRuntime.EndHumanTextTargetSetup(__state);
        return __exception;
    }
}

[HarmonyPatch]
internal static class HumanTextTargetStartPatch
{
    internal static MethodBase TargetMethod() =>
        typeof(NTargetManager).GetMethods()
            .Single(method => method.Name == nameof(NTargetManager.StartTargeting)
                && method.GetParameters().Length == 5
                && method.GetParameters()[1].ParameterType == typeof(Control));

    private static void Postfix(NTargetManager __instance,
        [HarmonyArgument(1)] Control control,
        [HarmonyArgument(2)] TargetMode startingMode) =>
        RecorderRuntime.BindHumanTextTargetManager(__instance, control, startingMode);
}

[HarmonyPatch]
internal static class HumanTextControllerInputPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NControllerCardPlay), nameof(NControllerCardPlay._Input))
        ?? throw new MissingMethodException(typeof(NControllerCardPlay).FullName, "_Input");

    private static void Prefix(NControllerCardPlay __instance, InputEvent inputEvent,
        out RecorderRuntime.HumanTextContinuationScope? __state) =>
        __state = RecorderRuntime.BeginHumanTextControllerInput(__instance, inputEvent);

    private static Exception? Finalizer(
        RecorderRuntime.HumanTextContinuationScope? __state, Exception? __exception)
    {
        NativeNestedCallbackSafety.Run("human_text_input.controller_finalizer", () =>
            RecorderRuntime.FinishHumanTextContinuation(__state, __exception));
        return __exception;
    }
}

[HarmonyPatch]
internal static class HumanTextTargetInputPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NTargetManager), nameof(NTargetManager._Input))
        ?? throw new MissingMethodException(typeof(NTargetManager).FullName, "_Input");

    private static void Prefix(NTargetManager __instance, InputEvent inputEvent,
        out RecorderRuntime.HumanTextContinuationScope? __state) =>
        __state = RecorderRuntime.BeginHumanTextTargetInput(__instance, inputEvent);

    private static Exception? Finalizer(
        RecorderRuntime.HumanTextContinuationScope? __state, Exception? __exception)
    {
        NativeNestedCallbackSafety.Run("human_text_input.target_finalizer", () =>
            RecorderRuntime.FinishHumanTextContinuation(__state, __exception));
        return __exception;
    }
}

[HarmonyPatch]
internal static class HumanTextTargetFinishPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NTargetManager), "FinishTargeting")
        ?? throw new MissingMethodException(typeof(NTargetManager).FullName, "FinishTargeting");

    private static void Prefix(NTargetManager __instance, bool cancel) =>
        RecorderRuntime.ObserveHumanTextTargetFinish(__instance, cancel);
}

[HarmonyPatch]
internal static class HumanTextTryPlayPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NCardPlay), "TryPlayCard")
        ?? throw new MissingMethodException(typeof(NCardPlay).FullName, "TryPlayCard");

    private static void Prefix(NCardPlay __instance, Creature? target) =>
        RecorderRuntime.ObserveHumanTextTryPlay(__instance, target);
}

[HarmonyPatch]
internal static class HumanTextCancelPlayPatch
{
    internal static MethodBase TargetMethod() =>
        AccessTools.Method(typeof(NCardPlay), nameof(NCardPlay.CancelPlayCard))
        ?? throw new MissingMethodException(typeof(NCardPlay).FullName, "CancelPlayCard");

    private static void Prefix(NCardPlay __instance) =>
        RecorderRuntime.ObserveHumanTextCancel(__instance);
}
