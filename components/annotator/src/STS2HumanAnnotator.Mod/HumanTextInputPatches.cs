using System.Reflection;
using HarmonyLib;
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
