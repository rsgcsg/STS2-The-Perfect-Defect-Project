using System;
using System.Reflection;
using Godot;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Rewards;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Capstones;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using STS2Connector;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;

namespace STS2Platform.GameMod;

// Native routing with callback-local witnesses only. Eligibility survives hide
// or removal; no instance is retained as a lifecycle or causal registry.
internal static class ConnectorNativeLogicalFamily
{
    internal static bool Information(object? value) => value is NCardsViewScreen or NInspectCardScreen;
    internal static bool Reward(object? value) => value is NRewardsScreen or NCardRewardSelectionScreen;
    internal static bool Live(object? value) => value is Node node && ConnectorMod.IsLiveNode(node);
    internal static bool Current(Node node) => Guard(() => Live(node) && node.IsNodeReady()
        && node is CanvasItem canvas && ConnectorMod.IsNodeVisible(canvas)
        && (node is not NInspectCardScreen inspector || NativeCardInspectionBinding.HasEnteredNativeSource(inspector))
        && ReferenceEquals(ActiveScreenContext.Instance.GetCurrentScreen(), node));
    internal static bool CurrentFamily(bool reward) => Guard(() =>
    {
        var current = ActiveScreenContext.Instance.GetCurrentScreen();
        return (reward ? Reward(current) : Information(current)) && current is Node node && Current(node);
    });
    internal static bool OwnedControl(Node node, bool reward) => Guard(() =>
    {
        if (!Live(node) || node is not CanvasItem canvas || !ConnectorMod.IsNodeVisible(canvas)) return false;
        // Only exact actual descendants of the current owner can route callbacks.
        for (Node? parent = node.GetParent(); parent is not null && Live(parent); parent = parent.GetParent())
            if ((reward ? Reward(parent) : Information(parent)) && Current(parent)) return true;
        return false;
    });
    internal static bool RewardControl(NClickableControl node) =>
        (node is NRewardButton or NProceedButton or NCardRewardAlternativeButton) && OwnedControl(node, true);
    internal static bool Guard(Func<bool> test) { try { return test(); } catch { return false; } }
    internal static void Publish(bool eligible, string seam, string phase)
    {
        if (!eligible) return;
        // The single service reserves before capture. A failed native frame stays
        // an original missing position; observers neither retry nor fill it later.
        PlayerEnvironmentService.NativeLogical.Publish(seam, phase);
    }

    internal static void InformationBefore(Node __instance, out bool __state) =>
        __state = Information(__instance) && Current(__instance);
    internal static void InformationReturned(Node __instance, bool __state, MethodBase __originalMethod) =>
        Publish(__state || (Information(__instance) && Current(__instance)), "native_information_owner", __originalMethod.Name + "_returned");
    internal static void InformationContentReturned(Node __instance, MethodBase __originalMethod) =>
        Publish((Information(__instance) && Current(__instance)) || (__instance is NCardGrid && OwnedControl(__instance, false)),
            "native_information_content", __originalMethod.Name + "_returned");
    internal static void InspectContentReturned(NInspectCardScreen screen) =>
        Publish(Current(screen), "native_information_content", "UpdateCardDisplay_returned");
    internal static void CapstoneOpenBefore(NCapstoneContainer __instance, ICapstoneScreen screen, out bool __state) =>
        __state = Guard(() => ReferenceEquals(NCapstoneContainer.Instance, __instance) && Live(__instance) && Information(screen) && Live(screen));
    internal static void CapstoneCloseBefore(NCapstoneContainer __instance, out bool __state) =>
        __state = Guard(() => ReferenceEquals(NCapstoneContainer.Instance, __instance) && Live(__instance)
            && Information(__instance.CurrentCapstoneScreen) && CurrentFamily(false));
    internal static void CapstoneReturned(bool __state, MethodBase __originalMethod) =>
        Publish(__state, "native_information_owner", "capstone_" + __originalMethod.Name + "_returned");
    internal static void ContextBefore(ActiveScreenContext __instance, out bool __state) =>
        __state = ReferenceEquals(ActiveScreenContext.Instance, __instance) && CurrentFamily(false);
    internal static void ContextReturned(ActiveScreenContext __instance, bool __state)
    {
        bool eligible = ReferenceEquals(ActiveScreenContext.Instance, __instance) && (__state || CurrentFamily(false));
        bool accounted = eligible && PlayerEnvironmentService.NativeLogical.PublishTracked(
            "native_information_owner", "information_context_update_returned");
        ConnectorNativeLogicalInspectionDeparture.ContextReturned(__instance, accounted);
    }

    internal static void InspectDepartureBefore(NInspectCardScreen __instance,
        out ConnectorNativeLogicalInspectionDeparture? __state)
    {
        __state = null;
        try
        {
            __state = ConnectorNativeLogicalInspectionDeparture.Begin(__instance,
                NGame.Instance?.InspectCardScreen, ActiveScreenContext.Instance.GetCurrentScreen(), ActiveScreenContext.Instance,
                Live(__instance) && NativeCardInspectionBinding.HasEnteredNativeSource(__instance),
                __instance.IsNodeReady(), ConnectorMod.IsNodeVisible(__instance));
        }
        catch { /* An unproved stale or non-owning callback cannot claim departure. */ }
    }
    internal static Exception? InspectDepartureReturned(NInspectCardScreen __instance,
        ConnectorNativeLogicalInspectionDeparture? __state, Exception? __exception)
    {
        if (__state is null) return __exception;
        try
        {
            // Exact 9cb4 callback: Visible=false, Update(), return. All visibility
            // and Update listeners have completed before this finalizer freezes.
            InspectionDepartureDisposition disposition;
            try
            {
                disposition = __state.Returned(__instance, NGame.Instance?.InspectCardScreen,
                    Live(__instance), __instance.Visible, __exception is not null);
            }
            catch { disposition = InspectionDepartureDisposition.Missing; }
            if (disposition == InspectionDepartureDisposition.Publish)
                Publish(true, "native_information_owner", "information_context_update_returned");
            else if (disposition == InspectionDepartureDisposition.Missing)
                PlayerEnvironmentService.NativeLogical.PublishMissing("native_information_owner", "inspect_delayed_close_failed",
                    __exception is null ? "native_inspect_departure_binding_changed" : "native_callback_failed");
        }
        finally { __state.Dispose(); }
        return __exception;
    }

    internal static void RewardBefore(Node __instance, out bool __state) => __state = Reward(__instance) && Current(__instance);
    internal static void RewardOwnerReturned(Node __instance, bool __state, MethodBase __originalMethod) =>
        Publish(__state || (Reward(__instance) && Current(__instance)), "native_reward_owner", __originalMethod.DeclaringType!.Name + "_" + __originalMethod.Name + "_returned");
    internal static void RewardCatalogReturned(Node __instance, bool __state, MethodBase __originalMethod) =>
        Publish(__state || (Reward(__instance) && Current(__instance)), "native_reward_catalog", __originalMethod.Name + "_returned");
    internal static void RewardShown(NRewardsScreen? __result) =>
        Publish(__result is not null && Current(__result), "native_reward_owner", "reward_show_registered_returned");
    internal static void CardRewardShown(NCardRewardSelectionScreen? __result) =>
        Publish(__result is not null && Current(__result), "native_reward_owner", "card_reward_show_registered_returned");
    internal static Exception? CardRewardRefreshReturned(NCardRewardSelectionScreen __instance, bool __state, Exception? __exception)
    {
        // Runs after Foundation postfix and Connector presentation finalizer.
        // Even native failure is a source callback outcome, never an invented ready frame.
        if (__state || Current(__instance))
        {
            if (__exception is null) Publish(true, "native_reward_catalog", "card_reward_refresh_finalized");
            else PlayerEnvironmentService.NativeLogical.PublishMissing("native_reward_catalog", "card_reward_refresh_failed", "native_callback_failed");
        }
        return __exception;
    }
    internal static void OverlayBefore(NOverlayStack __instance, IOverlayScreen screen, out bool __state) =>
        __state = Guard(() => ReferenceEquals(NOverlayStack.Instance, __instance) && Live(__instance) && Reward(screen) && Live(screen));
    internal static void OverlayRemoveBefore(NOverlayStack __instance, IOverlayScreen screen, out bool __state) =>
        __state = Guard(() => ReferenceEquals(NOverlayStack.Instance, __instance) && Live(__instance)
            && Reward(screen) && screen is Node node && Live(node) && ReferenceEquals(node.GetParent(), __instance));
    internal static void OverlayReturned(bool __state, MethodBase __originalMethod) => Publish(__state, "native_reward_owner", "overlay_" + __originalMethod.Name + "_returned");
    internal static void HolderReturned(NCardHolder __instance) =>
        Publish(__instance is NGridCardHolder && OwnedControl(__instance, true), "native_reward_input_availability", "reward_holder_clickability_returned");
    internal static void ControlBefore(NClickableControl __instance, out bool __state) => __state = RewardControl(__instance);
    internal static void ControlReturned(NClickableControl __instance, bool __state, MethodBase __originalMethod) =>
        Publish(__state || RewardControl(__instance), "native_reward_input_availability", "control_" + __originalMethod.Name + "_returned");
    internal static void ChoiceReturned(NCardRewardSelectionScreen __instance) =>
        Publish(Current(__instance), "native_reward_input_availability", "reward_waiting_choice_bound");
}
