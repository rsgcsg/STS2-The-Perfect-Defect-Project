using System;
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

    private static bool ControlAvailable(Control control, Control container)
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

    internal static bool CanOpenDeck(bool exactCurrentControl, bool testMode) =>
        exactCurrentControl && !testMode;

    internal static bool CanShowTips(bool exactCurrentControl, bool hoverBlocked, bool debugHidingTips) =>
        exactCurrentControl && !hoverBlocked && !debugHidingTips;
}
