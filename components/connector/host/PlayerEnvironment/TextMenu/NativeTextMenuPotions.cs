using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using Godot;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.ControllerInput;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.Potions;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Native potion-holder and popup controls for the text profile.</summary>
internal static class NativeTextMenuPotions
{
    private static readonly FieldInfo? HolderUsable = typeof(NPotionHolder)
        .GetField("_isUsable", BindingFlags.Instance | BindingFlags.NonPublic);
    private static readonly FieldInfo? HolderDisabled = typeof(NPotionHolder)
        .GetField("_disabledUntilPotionRemoved", BindingFlags.Instance | BindingFlags.NonPublic);
    private static NPotionHolder? _targetingHolder;
    private static PotionModel? _targetingPotion;

    internal static bool HasPendingTargeting
    {
        get
        {
            if (_targetingHolder == null || _targetingPotion == null)
                return false;
            if (!ReferenceEquals(_targetingHolder.Potion?.Model, _targetingPotion)
                || NTargetManager.Instance?.IsInSelection != true)
            {
                _targetingHolder = null;
                _targetingPotion = null;
                return false;
            }
            return true;
        }
    }

    internal static PotionModel? PendingPotion => HasPendingTargeting ? _targetingPotion : null;

    internal static IReadOnlyList<NCreature> Targets()
    {
        PotionModel? potion = PendingPotion;
        NCombatRoom? room = NCombatRoom.Instance;
        NTargetManager? manager = NTargetManager.Instance;
        if (potion == null || room == null || manager?.IsInSelection != true
            || !CombatManager.Instance.IsInProgress)
            return Array.Empty<NCreature>();
        return room.CreatureNodes.Where(node => ConnectorMod.IsLiveNode(node)
            && ConnectorMod.IsNodeVisible(node)
            && node.Entity is { IsHittable: true }
            && manager.AllowedToTargetNode(node)).ToArray();
    }

    internal static NativeInputResult SelectTarget(NCreature node)
    {
        if (!Targets().Contains(node))
            return NativeInputResult.Rejected("potion_target_changed",
                "The exact native potion target is no longer available.");
        NTargetManager manager = NTargetManager.Instance;
        bool hovered = false;
        void OnHovered(NCreature current)
        {
            if (ReferenceEquals(current, node)) hovered = true;
        }
        manager.CreatureHovered += OnHovered;
        try { manager.OnNodeHovered(node); }
        finally { manager.CreatureHovered -= OnHovered; }
        if (!hovered || !manager.IsInSelection)
            return NativeInputResult.Rejected("potion_target_focus_changed",
                "Native targeting did not accept the exact potion target.");
        manager._Input(new InputEventAction { Action = MegaInput.select, Pressed = true });
        _targetingHolder = null;
        _targetingPotion = null;
        return NativeInputResult.Delivered("native_potion_target_selected");
    }

    internal static NativeInputResult CancelTargeting()
    {
        if (!HasPendingTargeting)
            return NativeInputResult.Rejected("potion_target_changed",
                "The exact native potion targeting operation is gone.");
        NTargetManager.Instance._Input(new InputEventAction
        { Action = MegaInput.cancel, Pressed = true });
        _targetingHolder = null;
        _targetingPotion = null;
        return NativeInputResult.Delivered("native_potion_target_cancelled");
    }

    internal static IReadOnlyList<TextMenuLeaf> Openers(NativeEntityRegistry entities)
    {
        RunState? run = RunManager.Instance.DebugOnlyGetState();
        Player? player = run == null ? null : LocalContext.GetMe(run);
        var topBar = NRun.Instance?.GlobalUi.TopBar;
        var container = topBar?.PotionContainer;
        if (player == null || container == null || topBar == null
            || !ConnectorMod.IsNodeVisible(topBar)
            || PotionPopupSurfaceReader.Current() != null)
            return Array.Empty<TextMenuLeaf>();
        NPotionHolder[] holders = ConnectorMod.FindAll<NPotionHolder>(container).ToArray();
        if (holders.Length != player.PotionSlots.Count)
            return Array.Empty<TextMenuLeaf>();
        var leaves = new List<TextMenuLeaf>();
        for (int slot = 0; slot < holders.Length; slot++)
        {
            NPotionHolder holder = holders[slot];
            PotionModel? potion = player.GetPotionAtSlotIndex(slot);
            if (potion == null || !ReferenceEquals(holder.Potion?.Model, potion)
                || !ConnectorMod.IsLiveNode(holder) || !ConnectorMod.IsNodeVisible(holder)
                || !CanOpen(holder, potion))
                continue;
            int exactSlot = slot;
            string id = entities.GetId(potion, "potion");
            leaves.Add(new TextMenuLeaf($"open_potion:{id}", "root", "open_potion_popup",
                "Open " + potion.Title.GetFormattedText(), id,
                Array.Empty<PlayerEnvironment.Protocol.PlayerEnvironmentBoundActionArgument>(),
                () => Open(holder, potion, player, exactSlot)));
        }
        return leaves;
    }

    private static NativeInputResult Open(NPotionHolder holder, PotionModel potion,
        Player player, int slot)
    {
        if (slot < 0 || slot >= player.PotionSlots.Count
            || !ReferenceEquals(player.GetPotionAtSlotIndex(slot), potion)
            || !ReferenceEquals(holder.Potion?.Model, potion)
            || !ConnectorMod.IsLiveNode(holder) || !ConnectorMod.IsNodeVisible(holder)
            || !CanOpen(holder, potion)
            || PotionPopupSurfaceReader.Current() != null)
            return NativeInputResult.Rejected("potion_holder_changed",
                "The exact native potion holder is no longer current.");
        holder.ForceClick();
        return NativeInputResult.Delivered("native_potion_holder_clicked");
    }

    private static bool CanOpen(NPotionHolder holder, PotionModel potion)
    {
        if (HolderUsable == null || HolderDisabled == null
            || !holder.HasPotion || !holder.IsEnabled
            || potion.Owner.RunState.IsGameOver)
            return false;
        try
        {
            return HolderUsable.GetValue(holder) is true
                && HolderDisabled.GetValue(holder) is false;
        }
        catch (Exception)
        {
            return false;
        }
    }

    internal static IReadOnlyList<TextMenuLeaf> Popup(
        PotionPopupSurface expected, NativeEntityRegistry entities)
    {
        NPotionPopup? popup = PotionPopupSurfaceReader.Current();
        NPotionHolder? holder = popup == null ? null : PotionPopupSurfaceReader.HolderOf(popup);
        PotionModel? potion = holder?.Potion?.Model;
        if (popup == null || holder == null || potion == null
            || entities.GetId(popup, "screen") != expected.ScreenEntityId
            || entities.GetId(potion, "potion") != expected.PotionEntityId
            || !AtSlot(potion, expected.Slot))
            return Array.Empty<TextMenuLeaf>();
        string id = expected.PotionEntityId;
        var leaves = new List<TextMenuLeaf>();
        if (expected.CanUse && Button(popup, "%UseButton") is { } use)
            leaves.Add(new TextMenuLeaf("choose_potion_use:" + id, "root", "choose_potion_use",
                "Use " + expected.Name, id,
                Array.Empty<PlayerEnvironment.Protocol.PlayerEnvironmentBoundActionArgument>(),
                () => Click(popup, holder, potion, expected, use, "%UseButton")));
        if (expected.CanDiscard && Button(popup, "%DiscardButton") is { } discard)
            leaves.Add(new TextMenuLeaf("discard_potion:" + id, "root", "discard_potion",
                "Discard " + expected.Name, id,
                Array.Empty<PlayerEnvironment.Protocol.PlayerEnvironmentBoundActionArgument>(),
                () => Click(popup, holder, potion, expected, discard, "%DiscardButton")));
        leaves.Add(new TextMenuLeaf("close_potion_popup:" + id, "root", "close_potion_popup",
            "Close potion popup", id,
            Array.Empty<PlayerEnvironment.Protocol.PlayerEnvironmentBoundActionArgument>(),
            () => Close(popup, potion, expected, entities)));
        return leaves;
    }

    private static NPotionPopupButton? Button(NPotionPopup popup, string path)
    {
        NPotionPopupButton? button = popup.GetNodeOrNull<NPotionPopupButton>(path);
        return button != null && button.IsEnabled && ConnectorMod.IsNodeVisible(button)
            ? button : null;
    }

    private static bool Owns(NPotionPopup popup, PotionModel potion,
        PotionPopupSurface expected, NativeEntityRegistry entities) =>
        ReferenceEquals(PotionPopupSurfaceReader.Current(), popup)
        && ReferenceEquals(PotionPopupSurfaceReader.Potion(popup), potion)
        && entities.GetId(popup, "screen") == expected.ScreenEntityId
        && entities.GetId(potion, "potion") == expected.PotionEntityId
        && AtSlot(potion, expected.Slot);

    private static bool AtSlot(PotionModel potion, int slot) =>
        slot >= 0 && slot < potion.Owner.PotionSlots.Count
        && ReferenceEquals(potion.Owner.GetPotionAtSlotIndex(slot), potion);

    private static NativeInputResult Click(NPotionPopup popup, NPotionHolder holder,
        PotionModel potion,
        PotionPopupSurface expected, NPotionPopupButton button, string path)
    {
        // Exact popup identity is rechecked by the capture route; this control
        // must still be the popup's own enabled button immediately before click.
        if (!ReferenceEquals(PotionPopupSurfaceReader.Current(), popup)
            || !ReferenceEquals(PotionPopupSurfaceReader.Potion(popup), potion)
            || !ReferenceEquals(Button(popup, path), button))
            return NativeInputResult.Rejected("potion_popup_changed",
                "The native popup control is no longer current.");
        button.ForceClick();
        if (path == "%UseButton" && NTargetManager.Instance?.IsInSelection == true
            && (potion.TargetType is
                TargetType.AnyEnemy or TargetType.TargetedNoCreature
                || potion.CanThrowAtAlly()))
        {
            _targetingHolder = holder;
            _targetingPotion = potion;
        }
        return NativeInputResult.Delivered("native_potion_popup_button_clicked");
    }

    private static NativeInputResult Close(NPotionPopup popup, PotionModel potion,
        PotionPopupSurface expected, NativeEntityRegistry entities)
    {
        if (!Owns(popup, potion, expected, entities))
            return NativeInputResult.Rejected("potion_popup_changed",
                "The native popup is no longer current.");
        popup.Remove();
        return NativeInputResult.Delivered("native_potion_popup_closed");
    }
}
