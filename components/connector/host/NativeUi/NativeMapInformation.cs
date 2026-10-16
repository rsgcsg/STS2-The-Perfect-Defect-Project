using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MegaCrit.Sts2.Core.ControllerInput;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.TopBar;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.Capstones;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Runs;
using STS2Platform.NativeFoundation;
using Godot;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;

namespace STS2Connector.NativeUi;

/// <summary>Eligibility of the already-mapped global information controls while
/// the exact native Map owns input. Back/travel/floor and OS window focus are not
/// substitutes for each control's native state.</summary>
internal static class NativeMapInformation
{
    internal static bool Current(NMapScreen map) =>
        ReferenceEquals(NMapScreen.Instance, map) && map.IsOpen
        && ConnectorMod.IsNodeVisible(map) && map.IsInsideTree() && map.IsNodeReady()
        && map.CanProcess() && ActiveScreenContext.Instance.IsCurrent(map);

    internal static bool Available(NMapScreen map, Control control, Control container)
    {
        if (!Current(map) || NRun.Instance is not { } run
            || !(ReferenceEquals(run.GlobalUi.TopBar, container)
                || ReferenceEquals(run.GlobalUi.RelicInventory, container)))
            return false;
        return ControlAvailable(control, container);
    }

    internal static bool ControlAvailable(Control control, Control container)
    {
        if (!ConnectorMod.IsNodeVisible(container) || !container.CanProcess()
            || container.FocusBehaviorRecursive == Control.FocusBehaviorRecursiveEnum.Disabled
            || !ConnectorMod.IsNodeVisible(control) || !control.IsInsideTree() || !control.IsNodeReady()
            || !control.CanProcess() || control is NClickableControl { IsEnabled: false })
            return false;
        for (Node? current = control; current != null; current = current.GetParent())
        {
            if (!ConnectorMod.IsLiveNode(current)) return false;
            if (current is Control item && item.FocusBehaviorRecursive == Control.FocusBehaviorRecursiveEnum.Disabled)
                return false;
            if (ReferenceEquals(current, container)) return true;
        }
        return false;
    }

    internal sealed record ReadEntry(string Status, string? Gap, Func<bool> StillCurrent);
    private const BindingFlags PrivateInstance = BindingFlags.Instance | BindingFlags.NonPublic;
    private static readonly FieldInfo? MapOwner = typeof(NMapScreen).GetField("_map", PrivateInstance);
    private static readonly FieldInfo? Pressed = typeof(NHotkeyManager).GetField("_hotkeyPressedBindings", PrivateInstance);
    private static readonly FieldInfo? Released = typeof(NHotkeyManager).GetField("_hotkeyReleasedBindings", PrivateInstance);
    private static bool Unpatched(Type type, string name) => NativeRunHookReadQualification.IsUnpatched(
        type.GetMethod(name, BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic));
    private static bool PureCapstone(ICapstoneScreen? capstone)
    {
        if (capstone == null) return true;
        Type type = capstone.GetType();
        var close = type.GetMethod(nameof(ICapstoneScreen.AfterCapstoneClosed));
        return (type == typeof(NDeckViewScreen) || type == typeof(NCardPileScreen) || type == typeof(NCardsViewScreen))
            && close?.Module.ModuleVersionId == new Guid("57785517-0b16-42b9-8b36-bad6fb28384b")
            && NativeRunHookReadQualification.IsUnpatched(close)
            && Unpatched(typeof(NCapstoneContainer), "Close") && Unpatched(typeof(NCapstoneContainer), "CloseInternal");
    }
    internal static bool IsExactHotkey(Action? action, object target, string method) =>
        action != null && action.GetInvocationList().Length == 1 && ReferenceEquals(action.Target, target)
        && action.Method.DeclaringType == typeof(NClickableControl) && action.Method.Name == method
        && NativeRunHookReadQualification.IsUnpatched(action.Method);
    private static bool CurrentHotkey(NHotkeyManager manager, NTopBarMapButton button)
    {
        if (Pressed?.GetValue(manager) is not Dictionary<StringName, List<Action>> pressed
            || Released?.GetValue(manager) is not Dictionary<StringName, List<Action>> released) return false;
        return pressed.TryGetValue(MegaInput.viewMap, out var before) && released.TryGetValue(MegaInput.viewMap, out var after)
            && IsExactHotkey(before.LastOrDefault(), button, "OnPressHandler")
            && IsExactHotkey(after.LastOrDefault(), button, "OnReleaseHandler");
    }
    internal static ReadEntry CaptureReadEntry(RunState state)
    {
        if (NRun.Instance is not { } run || NMapScreen.Instance is not { } map
            || run.GlobalUi.TopBar.Map is not { } button || NHotkeyManager.Instance is not { } hotkeys)
            return new("unsupported", "native_map_information_entry_owner_missing", () => false);
        var capstone = NCapstoneContainer.Instance?.CurrentCapstoneScreen;
        bool Entry()
        {
            if (!ReferenceEquals(RunManager.Instance.DebugOnlyGetState(), state)
                || !ReferenceEquals(NRun.Instance, run) || !ReferenceEquals(NMapScreen.Instance, map)
                || !ReferenceEquals(MapOwner?.GetValue(map), state.Map)
                || !ReferenceEquals(run.GlobalUi.TopBar.Map, button) || !ReferenceEquals(NHotkeyManager.Instance, hotkeys)
                || !ReferenceEquals(NCapstoneContainer.Instance?.CurrentCapstoneScreen, capstone)
                || !PureCapstone(capstone)
                || !Unpatched(typeof(NTopBarMapButton), "OnRelease") || !Unpatched(typeof(NTopBarMapButton), "IsOpen")
                || !Unpatched(typeof(NHotkeyManager), "_UnhandledInput")
                || !Unpatched(typeof(NMapScreen), "get_IsOpen") || !Unpatched(typeof(NClickableControl), "get_IsEnabled")) return false;
            // Already displayed information does not depend on travel/Back/FTUE.
            if (map.IsOpen && ConnectorMod.IsNodeVisible(map)) return true;
            // Exact last registered native press+release handlers prove the
            // hotkey route independently of the mouse's visibility/enabled path.
            // OS focus is deliberately excluded from intrinsic entitlement.
            if (hotkeys.CanProcess() && CurrentHotkey(hotkeys, button)) return true;
            // The normal topbar remains independently clickable for these
            // exact overlays; arbitrary interception/unknown overlays do not
            // become a false native-unavailable claim.
            IOverlayScreen? overlay = NOverlayStack.Instance?.Peek();
            bool knownOverlay = overlay == null || overlay is MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NCardRewardSelectionScreen
                or NRewardsScreen or MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NCardGridSelectionScreen;
            return knownOverlay && ControlAvailable(button, run.GlobalUi.TopBar);
        }
        return Entry() ? new("captured", null, Entry)
            : new("unsupported", "native_map_information_entry_not_qualified", Entry);
    }

    internal static bool CanOpenDeck(bool exactCurrentControl, bool testMode) =>
        exactCurrentControl && !testMode;

    internal static bool CanShowTips(bool exactCurrentControl, bool hoverBlocked, bool debugHidingTips) =>
        exactCurrentControl && !hoverBlocked && !debugHidingTips;
}
