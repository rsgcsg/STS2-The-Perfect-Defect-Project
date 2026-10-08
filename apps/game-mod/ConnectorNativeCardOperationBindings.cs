using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading.Tasks;
using HarmonyLib;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens;
using STS2Connector;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;

namespace STS2Platform.GameMod;

// Exact source witnesses only. These patches never dispatch an input, mutate
// native state, wait for a Task, or turn completion into causal evidence.
internal static class ConnectorNativeCardOperationBindings
{
    internal static void Register(Harmony harmony)
    {
        Add(harmony, typeof(NMouseCardPlay), nameof(NMouseCardPlay.Start), Type.EmptyTypes, typeof(void),
            nameof(MouseStartEntering), nameof(MouseStartReturned), nameof(MouseStartFinalized));
        Add(harmony, typeof(NMouseCardPlay), "MultiCreatureTargeting", new[] { typeof(TargetMode) }, typeof(Task),
            nameof(MouseMultiEntering), nameof(MouseMultiReturned), nameof(MouseMultiFinalized));
        Add(harmony, typeof(NCardPlay), nameof(NCardPlay.CancelPlayCard), Type.EmptyTypes, typeof(void),
            nameof(MouseCancelEntering));
        Add(harmony, typeof(NMouseCardPlay), nameof(NMouseCardPlay._ExitTree), Type.EmptyTypes, typeof(void),
            nameof(MouseExitEntering));
        Add(harmony, typeof(NInspectCardScreen), "UpdateCardDisplay", Type.EmptyTypes, typeof(void),
            nameof(InspectDisplayEntering), nameof(InspectDisplayReturned), nameof(InspectDisplayFinalized));
        foreach ((string method, Type[] arguments) in new[]
        {
            (nameof(NInspectCardScreen.Open), new[] { typeof(List<CardModel>), typeof(int), typeof(bool) }),
            ("SetCard", new[] { typeof(int) }),
            ("ToggleShowUpgrade", new[] { typeof(NTickbox) })
        }) Add(harmony, typeof(NInspectCardScreen), method, arguments, typeof(void), nameof(InspectReplacing));
        // Close withdraws controls but does not replace the source/display
        // tuple. Capture continues to revalidate that exact visible tuple;
        // Open/SetCard/Toggle and failed displays still invalidate it.
        NativeMouseCardConfirmation.Registered();
        NativeCardInspectionBinding.Registered();
    }
    private static void Add(Harmony harmony, Type owner, string method, Type[] arguments, Type returns,
        string before, string? after = null, string? finalized = null)
    {
        var original = owner.GetMethod(method, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic
            | BindingFlags.DeclaredOnly, arguments) ?? throw new MissingMethodException(owner.FullName, method);
        if (original.DeclaringType != owner || original.IsStatic || original.ReturnType != returns)
            throw new MissingMethodException("The exact native source invocation signature changed.");
        HarmonyMethod Handler(string name) => new(typeof(ConnectorNativeCardOperationBindings).GetMethod(name,
            BindingFlags.Static | BindingFlags.NonPublic) ?? throw new MissingMethodException(name));
        harmony.Patch(original, prefix: Handler(before), postfix: after == null ? null : Handler(after),
            finalizer: finalized == null ? null : Handler(finalized));
    }
    private static void MouseStartEntering(NMouseCardPlay __instance, out NativeSourceInvocation<NativeMouseCardConfirmation.Origin>.Ticket? __state) =>
        __state = NativeMouseCardConfirmation.StartEntering(__instance);
    private static void MouseStartReturned(NMouseCardPlay __instance, NativeSourceInvocation<NativeMouseCardConfirmation.Origin>.Ticket? __state) =>
        NativeMouseCardConfirmation.StartReturned(__instance, __state);
    private static Exception? MouseStartFinalized(NMouseCardPlay __instance, NativeSourceInvocation<NativeMouseCardConfirmation.Origin>.Ticket? __state, Exception? __exception)
    {
        NativeMouseCardConfirmation.StartFinalized(__instance, __state, __exception);
        return __exception;
    }
    private static void MouseMultiEntering(NMouseCardPlay __instance, TargetMode targetMode,
        out NativeSourceInvocation<NativeMouseCardConfirmation.Phase>.Ticket? __state) =>
        __state = NativeMouseCardConfirmation.MultiEntering(__instance, targetMode);
    private static void MouseMultiReturned(NMouseCardPlay __instance, NativeSourceInvocation<NativeMouseCardConfirmation.Phase>.Ticket? __state, Task __result) =>
        NativeMouseCardConfirmation.MultiReturned(__instance, __state, __result);
    private static Exception? MouseMultiFinalized(NMouseCardPlay __instance, NativeSourceInvocation<NativeMouseCardConfirmation.Phase>.Ticket? __state, Exception? __exception)
    {
        NativeMouseCardConfirmation.MultiFinalized(__instance, __state, __exception);
        return __exception;
    }
    private static void MouseCancelEntering(NCardPlay __instance)
    {
        if (__instance is NMouseCardPlay mouse) NativeMouseCardConfirmation.Invalidate(mouse);
    }
    private static void MouseExitEntering(NMouseCardPlay __instance) => NativeMouseCardConfirmation.Invalidate(__instance);
    private static void InspectReplacing(NInspectCardScreen __instance) => NativeCardInspectionBinding.Invalidate(__instance);
    private static void InspectDisplayEntering(NInspectCardScreen __instance,
        out NativeSourceInvocation<NativeCardInspectionBinding.Fact>.Ticket? __state) =>
        __state = NativeCardInspectionBinding.DisplayEntering(__instance);
    private static void InspectDisplayReturned(NInspectCardScreen __instance, NativeSourceInvocation<NativeCardInspectionBinding.Fact>.Ticket? __state)
    {
        NativeCardInspectionBinding.DisplayReturned(__instance, __state);
        // Same exact completed callback, after its relation has been certified.
        // No second independently ordered postfix can capture a torn relation.
        if (ConnectorMod.IsLiveNode(__instance) && ConnectorMod.IsNodeVisible(__instance))
        {
            PlayerEnvironmentService.NativeLogical.Publish("native_inspect_preview", "card_display_callback_returned");
            ConnectorNativeLogicalFamily.InspectContentReturned(__instance);
        }
    }
    private static Exception? InspectDisplayFinalized(NInspectCardScreen __instance,
        NativeSourceInvocation<NativeCardInspectionBinding.Fact>.Ticket? __state, Exception? __exception)
    {
        NativeCardInspectionBinding.DisplayFinalized(__instance, __state, __exception);
        return __exception;
    }
}
