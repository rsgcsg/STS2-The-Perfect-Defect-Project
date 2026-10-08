using System;
using Godot;
using HarmonyLib;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.GameOverScreen;
using STS2Connector;
using STS2Connector.PlayerEnvironment;

namespace STS2Platform.GameMod;

// Exact 9cb4f1ad STS2 composition seams. These hooks observe completed typed
// callbacks only; they never dispatch, wait, persist or infer causal completion.
internal static class ConnectorNativeLogicalPatches
{
    private static bool initialized;
    internal static void Initialize()
    {
        if (initialized) return;
        var harmony = new Harmony("rsgcsg.sts2-platform.connector.native-logical");
        Patch(harmony, typeof(NTargetManager), nameof(NTargetManager.OnNodeHovered), new[] { typeof(Node) }, nameof(TargetReturned));
        Patch(harmony, typeof(NTargetManager), nameof(NTargetManager.OnNodeUnhovered), new[] { typeof(Node) }, nameof(TargetReturned));
        Patch(harmony, typeof(NPlayerHand), "OnHolderPressed", new[] { typeof(NCardHolder) }, nameof(InputReturned));
        Patch(harmony, typeof(NCard), nameof(NCard.SetPreviewTarget), new[] { typeof(Creature) }, nameof(CardPreviewReturned));
        Patch(harmony, typeof(NInspectCardScreen), "UpdateCardDisplay", Type.EmptyTypes, nameof(InspectPreviewReturned));
        Patch(harmony, typeof(NGameOverScreen), "OpenSummaryScreen", new[] { typeof(NButton) }, nameof(TerminalEntryReturned));
        initialized = true;
    }
    private static void Patch(Harmony harmony, Type owner, string method, Type[] arguments, string observer)
    {
        var original = AccessTools.Method(owner, method, arguments) ?? throw new MissingMethodException(owner.FullName, method);
        var postfix = AccessTools.Method(typeof(ConnectorNativeLogicalPatches), observer) ?? throw new MissingMethodException(observer);
        harmony.Patch(original, postfix: new HarmonyMethod(postfix));
    }
    private static void TargetReturned(NTargetManager __instance)
    {
        if (ReferenceEquals(NTargetManager.Instance, __instance) && ConnectorMod.IsLiveNode(__instance))
            PlayerEnvironmentService.NativeLogical.Publish("native_target_focus", "target_focus_callback_returned");
    }
    private static void InputReturned(NPlayerHand __instance)
    {
        if (ReferenceEquals(NPlayerHand.Instance, __instance) && ConnectorMod.IsLiveNode(__instance))
            PlayerEnvironmentService.NativeLogical.Publish("native_input_callback", "hand_holder_pressed_returned");
    }
    private static void CardPreviewReturned(NCard __instance)
    {
        if (ConnectorMod.IsLiveNode(__instance) && ConnectorMod.IsNodeVisible(__instance))
            PlayerEnvironmentService.NativeLogical.Publish("native_card_preview", "preview_target_callback_returned");
    }
    private static void InspectPreviewReturned(NInspectCardScreen __instance)
    {
        if (ConnectorMod.IsLiveNode(__instance) && ConnectorMod.IsNodeVisible(__instance))
            PlayerEnvironmentService.NativeLogical.Publish("native_inspect_preview", "card_display_callback_returned");
    }
    private static void TerminalEntryReturned(NGameOverScreen __instance)
    {
        if (ConnectorMod.IsLiveNode(__instance) && ConnectorMod.IsNodeVisible(__instance))
            PlayerEnvironmentService.NativeLogical.Publish("native_terminal_entry", "summary_animation_started", "terminal");
    }
}
