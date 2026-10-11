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
using MegaCrit.Sts2.Core.Models.Potions;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Potions;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;

namespace STS2Connector.PlayerEnvironment;

/// <summary>Native potion-holder and popup controls for the text profile.</summary>
internal sealed record NativePotionTargetBinding(
    NPotionHolder Holder, PotionModel Potion, NTargetManager Manager, Func<bool> ExitPredicate, int Slot);

internal static class NativeTextMenuPotions
{
    private static readonly FieldInfo? HolderUsable = typeof(NPotionHolder)
        .GetField("_isUsable", BindingFlags.Instance | BindingFlags.NonPublic);
    private static readonly FieldInfo? HolderDisabled = typeof(NPotionHolder)
        .GetField("_disabledUntilPotionRemoved", BindingFlags.Instance | BindingFlags.NonPublic);
    private static readonly FieldInfo? TargetExitCondition = typeof(NTargetManager)
        .GetField("_exitEarlyCondition", BindingFlags.Instance | BindingFlags.NonPublic);
    private static readonly MethodInfo? PotionExitMethod = typeof(NPotionHolder)
        .GetMethod("ShouldCancelTargeting", BindingFlags.Instance | BindingFlags.NonPublic);

    internal static NativePotionTargetBinding? CaptureNativeTargeting()
    {
        if (NRun.Instance?.GlobalUi.TopBar.PotionContainer is not { } container
            || NTargetManager.Instance is not { IsInSelection: true } manager
            || TargetExitCondition?.GetValue(manager) is not Func<bool> exit
            || exit.Target is not NPotionHolder holder
            || holder.Potion?.Model is not { } potion
            || RunManager.Instance.DebugOnlyGetState() is not { } run
            || LocalContext.GetMe(run) is not { } player
            || !ReferenceEquals(potion.Owner, player)) return null;
        NPotionHolder[] holders = ConnectorMod.FindAll<NPotionHolder>(container).ToArray();
        if (holders.Length != player.PotionSlots.Count) return null;
        int slot = Array.FindIndex(holders, current => ReferenceEquals(current, holder));
        if (slot < 0 || holders.Count(current => ReferenceEquals(current, holder)) != 1
            || !NativePotionTargetOwner.Matches(exit, holder, PotionExitMethod, potion,
                holder.Potion?.Model, player.GetPotionAtSlotIndex(slot),
                ConnectorMod.IsLiveNode(holder) && ConnectorMod.IsNodeVisible(holder))) return null;
        return new(holder, potion, manager, exit, slot);
    }

    private static bool IsCurrent(NativePotionTargetBinding binding) =>
        CaptureNativeTargeting() is { } current
        && ReferenceEquals(current.Holder, binding.Holder) && ReferenceEquals(current.Potion, binding.Potion)
        && ReferenceEquals(current.Manager, binding.Manager) && ReferenceEquals(current.ExitPredicate, binding.ExitPredicate)
        && current.Slot == binding.Slot;

    private static PotionModel? CurrentPotion(NativePotionTargetBinding? binding) =>
        binding == null ? PendingPotion : IsCurrent(binding) ? binding.Potion : null;

    private static NPotionHolder? _targetingHolder;
    private static PotionModel? _targetingPotion;
    private static NTargetManager? _targetingManager;

    private static void ClearPendingTargeting()
    {
        if (_targetingManager != null)
            _targetingManager.TargetingEnded -= ClearPendingTargeting;
        _targetingManager = null;
        _targetingHolder = null;
        _targetingPotion = null;
    }

    internal static bool HasPendingTargeting
    {
        get
        {
            if (_targetingHolder == null || _targetingPotion == null)
                return false;
            if (!ReferenceEquals(_targetingHolder.Potion?.Model, _targetingPotion)
                || !ReferenceEquals(_targetingManager, NTargetManager.Instance)
                || _targetingManager?.IsInSelection != true)
            {
                ClearPendingTargeting();
                return false;
            }
            return true;
        }
    }

    internal static PotionModel? PendingPotion => HasPendingTargeting ? _targetingPotion : null;

    internal static IReadOnlyList<NCreature> Targets(NativePotionTargetBinding? binding = null)
    {
        PotionModel? potion = CurrentPotion(binding);
        NCombatRoom? room = NCombatRoom.Instance;
        NTargetManager? manager = NTargetManager.Instance;
        if (potion == null || room == null || manager?.IsInSelection != true
            || !CombatManager.Instance.IsInProgress
            || potion.TargetType is not (TargetType.AnyEnemy or TargetType.AnyPlayer
                or TargetType.AnyAlly))
            return Array.Empty<NCreature>();
        return room.CreatureNodes.Where(node => ConnectorMod.IsLiveNode(node)
            && ConnectorMod.IsNodeVisible(node)
            && node.Entity is { IsHittable: true }
            && manager.AllowedToTargetNode(node)).ToArray();
    }

    internal static NMerchantButton? MerchantTarget(NativePotionTargetBinding? binding = null)
    {
        if (CurrentPotion(binding) is not FoulPotion potion
            || potion.TargetType != TargetType.TargetedNoCreature
            || potion.Owner.RunState.CurrentRoom is not { } currentRoom
            || NTargetManager.Instance is not { IsInSelection: true } manager)
            return null;
        (NMerchantButton? button, _) = FoulPotion.GetFoulPotionMerchantTarget(
            currentRoom);
        return button != null && ConnectorMod.IsLiveNode(button)
            && ConnectorMod.IsNodeVisible(button) && button.IsEnabled
            && manager.AllowedToTargetNode(button)
            ? button : null;
    }

    internal static NativeInputResult FocusMerchant(NMerchantButton button, NativePotionTargetBinding? binding = null)
    {
        if (!ReferenceEquals(MerchantTarget(binding), button))
            return NativeInputResult.Rejected("potion_merchant_target_changed", "The exact merchant target changed.");
        NTargetManager manager = NTargetManager.Instance;
        bool accepted = false;
        void OnHovered(Node current) { if (ReferenceEquals(current, button)) accepted = true; }
        manager.NodeHovered += OnHovered;
        try { manager.OnNodeHovered(button); }
        catch (Exception)
        {
            return NativeInputResult.Unknown("native_merchant_focus_unknown", "The merchant focus boundary threw.",
                new NativeInputStage(NativeInputStageKind.MerchantFocus, NativeInputDelivery.Unknown, "native_merchant_focus_threw"));
        }
        finally { manager.NodeHovered -= OnHovered; }
        return accepted ? NativeInputResult.Delivered("native_merchant_target_focused")
            : NativeInputResult.DeliveredWithoutAcceptance("merchant_focus_not_witnessed",
                "Native focus input returned without an acceptance witness.",
                new NativeInputStage(NativeInputStageKind.MerchantFocus, NativeInputDelivery.Delivered, "native_merchant_focus_input_delivered"));
    }

    internal static NativeInputResult SelectMerchant(NMerchantButton button, NativePotionTargetBinding? binding = null)
    {
        if (!ReferenceEquals(MerchantTarget(binding), button))
            return NativeInputResult.Rejected("potion_merchant_target_changed",
                "The exact visible native merchant target is no longer current.");
        NTargetManager manager = NTargetManager.Instance;
        bool hovered = false;
        void OnHovered(Node current)
        {
            if (ReferenceEquals(current, button)) hovered = true;
        }
        manager.NodeHovered += OnHovered;
        try { manager.OnNodeHovered(button); }
        catch (Exception)
        {
            return NativeInputResult.Unknown("native_potion_focus_unknown", "The native potion focus boundary threw.",
                new NativeInputStage(NativeInputStageKind.MerchantFocus, NativeInputDelivery.Unknown, "native_potion_focus_threw"));
        }
        finally { manager.NodeHovered -= OnHovered; }
        if (!hovered)
            return NativeInputResult.DeliveredWithoutAcceptance("potion_target_focus_not_witnessed",
                "Native focus input returned, but potion target acceptance was not witnessed.",
                new NativeInputStage(NativeInputStageKind.MerchantFocus, NativeInputDelivery.Delivered, "native_potion_focus_input_delivered"));
        var focus = new NativeInputStage(NativeInputStageKind.MerchantFocus, NativeInputDelivery.Delivered,
            "native_potion_target_focused");
        if (!manager.IsInSelection)
            return NativeInputResult.PartiallyDelivered("potion_target_selection_changed",
                "Potion focus was delivered; confirmation was not delivered.", focus);
        try { manager._Input(new InputEventAction { Action = MegaInput.select, Pressed = true }); }
        catch (Exception)
        {
            return NativeInputResult.Unknown("native_potion_confirm_unknown",
                "Potion focus was delivered; confirmation input threw.", focus,
                new NativeInputStage(NativeInputStageKind.MerchantConfirmInput, NativeInputDelivery.Unknown, "native_potion_confirm_threw"));
        }
        ClearPendingTargeting();
        return NativeInputResult.DeliveredStages("native_foul_potion_merchant_selected", focus,
            new NativeInputStage(NativeInputStageKind.MerchantConfirmInput, NativeInputDelivery.Delivered, "native_foul_potion_merchant_selected"));
    }

    internal static Node? FocusedTarget(NativePotionTargetBinding? binding = null)
    {
        if (CurrentPotion(binding) == null || NTargetManager.Instance is not { IsInSelection: true } manager)
            return null;
        Node? focused = NativeTextMenuCombat.CurrentFocusedNode(manager);
        return focused is CanvasItem visibleFocus && ConnectorMod.IsLiveNode(focused) && ConnectorMod.IsNodeVisible(visibleFocus)
            && (focused is NCreature creature && NCombatRoom.Instance?.CreatureNodes.Contains(creature) == true
                || focused is NMerchantButton && CurrentPotion(binding) is FoulPotion) ? focused : null;
    }

    internal static NativeInputResult FocusTarget(NCreature node, NativePotionTargetBinding? binding = null)
    {
        if (!Targets(binding).Contains(node))
            return NativeInputResult.Rejected("potion_target_changed", "The exact potion target is no longer focusable.");
        NTargetManager manager = NTargetManager.Instance;
        bool accepted = false;
        void OnHovered(NCreature current) { if (ReferenceEquals(current, node)) accepted = true; }
        manager.CreatureHovered += OnHovered;
        try { manager.OnNodeHovered(node); }
        finally { manager.CreatureHovered -= OnHovered; }
        return accepted ? NativeInputResult.Delivered("native_potion_target_focused")
            : NativeInputResult.DeliveredWithoutAcceptance("potion_target_focus_not_witnessed",
                "Native focus input returned, but potion target acceptance was not witnessed.",
                new NativeInputStage(NativeInputStageKind.TargetFocus, NativeInputDelivery.Delivered, "native_potion_focus_input_delivered"));
    }

    internal static bool CanUnfocusTarget(Node expected, NativePotionTargetBinding? binding = null) =>
        ReferenceEquals(FocusedTarget(binding), expected) && NTargetManager.Instance.AllowedToTargetNode(expected);

    internal static NativeInputResult UnfocusTarget(Node expected, NativePotionTargetBinding? binding = null)
    {
        if (!CanUnfocusTarget(expected, binding))
            return NativeInputResult.Rejected("potion_target_focus_changed", "The exact potion target focus changed.");
        NTargetManager.Instance.OnNodeUnhovered(expected);
        return NativeInputResult.Delivered("native_potion_target_unfocused");
    }

    internal static NativeInputResult SelectTarget(NCreature node, NativePotionTargetBinding? binding = null)
    {
        if (!Targets(binding).Contains(node))
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
        catch (Exception)
        {
            return NativeInputResult.Unknown("native_potion_focus_unknown", "The native potion focus boundary threw.",
                new NativeInputStage(NativeInputStageKind.TargetFocus, NativeInputDelivery.Unknown, "native_potion_focus_threw"));
        }
        finally { manager.CreatureHovered -= OnHovered; }
        if (!hovered)
            return NativeInputResult.DeliveredWithoutAcceptance("potion_target_focus_not_witnessed",
                "Native focus input returned, but potion target acceptance was not witnessed.",
                new NativeInputStage(NativeInputStageKind.TargetFocus, NativeInputDelivery.Delivered, "native_potion_focus_input_delivered"));
        var focus = new NativeInputStage(NativeInputStageKind.TargetFocus, NativeInputDelivery.Delivered,
            "native_potion_target_focused");
        if (!manager.IsInSelection)
            return NativeInputResult.PartiallyDelivered("potion_target_selection_changed",
                "Potion focus was delivered; confirmation was not delivered.", focus);
        try { manager._Input(new InputEventAction { Action = MegaInput.select, Pressed = true }); }
        catch (Exception)
        {
            return NativeInputResult.Unknown("native_potion_confirm_unknown",
                "Potion focus was delivered; confirmation input threw.", focus,
                new NativeInputStage(NativeInputStageKind.PotionTargetConfirmInput, NativeInputDelivery.Unknown, "native_potion_confirm_threw"));
        }
        ClearPendingTargeting();
        return NativeInputResult.DeliveredStages("native_potion_target_selected", focus,
            new NativeInputStage(NativeInputStageKind.PotionTargetConfirmInput, NativeInputDelivery.Delivered, "native_potion_target_selected"));
    }

    internal static NativeInputResult CancelTargeting(NativePotionTargetBinding? binding = null)
    {
        if (CurrentPotion(binding) == null)
            return NativeInputResult.Rejected("potion_target_changed",
                "The exact native potion targeting operation is gone.");
        NTargetManager.Instance._Input(new InputEventAction
        { Action = MegaInput.cancel, Pressed = true });
        ClearPendingTargeting();
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
        if (expected.CanUse && Button(popup, "%UseButton") is { } use
            && (potion is not FoulPotion
                || NControllerManager.Instance?.IsUsingDirectionalNavigation == true
                || NGame.IsGameFocusedWindow()))
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
        var stages = new List<NativeInputStage>();
        NControllerManager? controller = NControllerManager.Instance;
        if (path == "%UseButton" && potion is FoulPotion
            && controller?.IsUsingDirectionalNavigation != true)
        {
            if (controller == null || !NGame.IsGameFocusedWindow())
                return NativeInputResult.Rejected("controller_input_unavailable",
                    "Foul Potion's merchant target requires current native directional input.");
            try { controller._Input(new InputEventAction
                { Action = Controller.faceButtonSouth, Pressed = true }); }
            catch (Exception)
            {
                return NativeInputResult.Unknown("native_potion_controller_unknown", "The controller input boundary threw.",
                    new NativeInputStage(NativeInputStageKind.ControllerModeInput, NativeInputDelivery.Unknown, "native_controller_mode_input_threw"));
            }
            stages.Add(new NativeInputStage(NativeInputStageKind.ControllerModeInput, NativeInputDelivery.Delivered,
                "native_controller_mode_input_delivered"));
            if (!controller.IsUsingDirectionalNavigation
                || !ReferenceEquals(PotionPopupSurfaceReader.Current(), popup)
                || !ReferenceEquals(Button(popup, path), button))
                return NativeInputResult.PartiallyDelivered("native_potion_use_pending",
                    "Controller input was delivered; potion use input was not delivered.", stages.ToArray());
        }
        try { button.ForceClick(); }
        catch (Exception)
        {
            stages.Add(new NativeInputStage(NativeInputStageKind.PotionPopupInput, NativeInputDelivery.Unknown,
                "native_potion_popup_button_threw"));
            return NativeInputResult.Unknown("native_potion_popup_unknown", "The potion popup input boundary threw.", stages.ToArray());
        }
        stages.Add(new NativeInputStage(NativeInputStageKind.PotionPopupInput, NativeInputDelivery.Delivered,
            "native_potion_popup_button_clicked"));
        if (path == "%UseButton" && NTargetManager.Instance?.IsInSelection == true
            && (potion.TargetType is
                TargetType.AnyEnemy or TargetType.TargetedNoCreature
                || potion.CanThrowAtAlly()))
        {
            ClearPendingTargeting();
            _targetingManager = NTargetManager.Instance;
            _targetingManager.TargetingEnded += ClearPendingTargeting;
            _targetingHolder = holder;
            _targetingPotion = potion;
        }
        return NativeInputResult.DeliveredStages("native_potion_popup_button_clicked", stages.ToArray());
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
