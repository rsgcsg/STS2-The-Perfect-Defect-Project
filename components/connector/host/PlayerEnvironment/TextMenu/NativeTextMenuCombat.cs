using System;
using System.Collections.Generic;
using System.Linq;
using Godot;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.ControllerInput;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.LiveHost;
using STS2Connector.NativeUi;

namespace STS2Connector.PlayerEnvironment;

/// <summary>
/// Exact, device-scoped entry points into the game's card-play operation. This
/// class does not recreate card legality or enqueue a PlayCardAction. Mouse
/// single-creature targeting requires its exact manager binding. The native
/// logical profile additionally uses source-witnessed mouse confirmation.
/// </summary>
internal static class NativeTextMenuCombat
{
    private static readonly System.Reflection.FieldInfo? MouseTargetSignalsField =
        typeof(NMouseCardPlay).GetField("_signalsConnected",
            System.Reflection.BindingFlags.Instance
            | System.Reflection.BindingFlags.NonPublic);
    private static readonly System.Reflection.FieldInfo? TargetExitConditionField =
        typeof(NTargetManager).GetField("_exitEarlyCondition",
            System.Reflection.BindingFlags.Instance
            | System.Reflection.BindingFlags.NonPublic);

    internal static NCardPlay? CurrentCardPlay(NPlayerHand hand) =>
        hand.GetChildren().OfType<NCardPlay>()
            .Where(ConnectorMod.IsLiveNode).SingleOrDefault();

    internal static bool CanBegin(NPlayerHand hand, NHandCardHolder holder)
    {
        CardModel? card = holder.CardModel;
        return card != null
            && ReferenceEquals(NPlayerHand.Instance, hand)
            && CombatManager.Instance.IsInProgress
            && hand.CurrentMode == NPlayerHand.Mode.Play
            && !hand.InCardPlay
            && hand.PeekButton?.IsPeeking != true
            && CombatManager.Instance.PlayerActionsDisabled == false
            && hand.ActiveHolders.Contains(holder)
            && ConnectorMod.IsLiveNode(holder)
            && ConnectorMod.IsNodeVisible(holder)
            && holder.Hitbox.IsEnabled
            && CanUseDirectionalCardInput()
            && card.CanPlay(out _, out _);
    }

    private static bool CanUseDirectionalCardInput() =>
        NControllerManager.Instance is { } manager
        && (manager.IsUsingDirectionalNavigation || NGame.IsGameFocusedWindow());

    internal static NativeInputResult Begin(
        NPlayerHand hand, NHandCardHolder holder, CardModel card)
    {
        if (!ReferenceEquals(holder.CardModel, card) || !CanBegin(hand, holder))
            return NativeInputResult.Rejected("card_begin_changed",
                "The exact hand holder cannot begin native card play now.");

        NControllerManager? manager = NControllerManager.Instance;
        if (manager == null)
            return NativeInputResult.Rejected("controller_input_changed",
                "Native controller input manager is unavailable.");
        var stages = new List<NativeInputStage>();
        if (!manager.IsUsingDirectionalNavigation)
        {
            try { manager._Input(new InputEventAction
                { Action = Controller.faceButtonSouth, Pressed = true }); }
            catch (Exception)
            {
                stages.Add(new NativeInputStage(NativeInputStageKind.ControllerModeInput, NativeInputDelivery.Unknown,
                    "native_controller_mode_input_threw"));
                return NativeInputResult.Unknown("native_controller_input_unknown",
                    "The controller input boundary threw; delivery is unknown.", stages.ToArray());
            }
            stages.Add(new NativeInputStage(NativeInputStageKind.ControllerModeInput, NativeInputDelivery.Delivered,
                "native_controller_mode_input_delivered"));
            if (!manager.IsUsingDirectionalNavigation || !ReferenceEquals(holder.CardModel, card)
                || !CanBegin(hand, holder))
                return NativeInputResult.PartiallyDelivered("native_card_begin_pending",
                    "Controller input was delivered, but the exact card begin was not delivered.", stages.ToArray());
        }
        // The holder's real Pressed signal is wired to NPlayerHand.OnHolderPressed.
        try { holder.EmitSignal(NCardHolder.SignalName.Pressed, holder); }
        catch (Exception)
        {
            stages.Add(new NativeInputStage(NativeInputStageKind.CardBeginInput, NativeInputDelivery.Unknown,
                "native_hand_holder_pressed_threw"));
            return NativeInputResult.Unknown("native_card_begin_unknown",
                "The card input boundary threw; delivery is unknown.", stages.ToArray());
        }
        stages.Add(new NativeInputStage(NativeInputStageKind.CardBeginInput, NativeInputDelivery.Delivered,
            "native_hand_holder_pressed_controller_mode"));
        return NativeInputResult.DeliveredStages("native_hand_holder_pressed_controller_mode", stages.ToArray());
    }

    internal static NativeInputResult Cancel(
        NPlayerHand hand, NCardPlay play, CardModel card)
    {
        if (!Owns(hand, play, card))
            return NativeInputResult.Rejected("card_play_changed",
                "The exact pending card-play operation is no longer current.");
        play.CancelPlayCard();
        return NativeInputResult.Delivered("native_card_play_cancel_requested");
    }

    internal static bool Owns(NPlayerHand hand, NCardPlay play, CardModel card) =>
        ReferenceEquals(NPlayerHand.Instance, hand)
        && hand.InCardPlay
        && ReferenceEquals(CurrentCardPlay(hand), play)
        && ReferenceEquals(play.Holder.CardModel, card)
        && ConnectorMod.IsLiveNode(play)
        && ConnectorMod.IsLiveNode(play.Holder);

    // Single-target mouse input is owned by the same native target manager.
    // Its private connection flag is set only while this exact carrier awaits
    // the manager; an unrelated targeting operation cannot lend it a menu.
    internal static bool OwnsMouseTarget(
        NPlayerHand hand, NMouseCardPlay play, CardModel card) =>
        Owns(hand, play, card)
        && card.TargetType is TargetType.AnyEnemy or TargetType.AnyAlly
        && NTargetManager.Instance is { IsInSelection: true } manager
        // SingleCreatureTargeting passes an instance-bound exit predicate to
        // StartTargeting. This proves the manager still belongs to this play,
        // even if another native family reused the singleton meanwhile.
        && TargetExitConditionField?.GetValue(manager) is Func<bool> exit
        && ExactMouseManagerOwner(play, exit)
        && MouseTargetSignalsField?.GetValue(play) is true;

    internal static bool ExactMouseManagerOwner(object play, Delegate? exitCondition) =>
        exitCondition != null && ReferenceEquals(exitCondition.Target, play);

    internal static IReadOnlyList<NCreature> CurrentTargets(
        NPlayerHand hand, NCardPlay play, CardModel card)
    {
        NCombatRoom? room = NCombatRoom.Instance;
        NTargetManager? targetManager = NTargetManager.Instance;
        if (!Owns(hand, play, card) || room == null
            || targetManager?.IsInSelection != true
            || card.TargetType is not (TargetType.AnyEnemy or TargetType.AnyAlly))
            return Array.Empty<NCreature>();
        if (play is NMouseCardPlay mouse && !OwnsMouseTarget(hand, mouse, card))
            return Array.Empty<NCreature>();

        return room.CreatureNodes
            .Where(node => ConnectorMod.IsLiveNode(node)
                && ConnectorMod.IsNodeVisible(node)
                && node.Entity is { IsHittable: true } creature
                && targetManager.AllowedToTargetNode(node)
                && card.CanPlayTargeting(creature))
            .ToArray();
    }

    private static readonly System.Reflection.PropertyInfo? HoveredNodeProperty =
        typeof(NTargetManager).GetProperty("HoveredNode",
            System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic);

    internal static Node? CurrentFocusedNode(NTargetManager manager) =>
        HoveredNodeProperty?.GetValue(manager) as Node;

    internal static NCreature? FocusedTarget(NPlayerHand hand, NCardPlay play, CardModel card)
    {
        if (!Owns(hand, play, card) || card.TargetType is not (TargetType.AnyEnemy or TargetType.AnyAlly)
            || play is NMouseCardPlay mouse && !OwnsMouseTarget(hand, mouse, card)
            || NTargetManager.Instance is not { IsInSelection: true } manager)
            return null;
        NCreature? focused = CurrentFocusedNode(manager) as NCreature;
        return focused != null && ConnectorMod.IsLiveNode(focused) && ConnectorMod.IsNodeVisible(focused)
            && NCombatRoom.Instance?.CreatureNodes.Contains(focused) == true ? focused : null;
    }

    internal static bool CanUnfocusTarget(NPlayerHand hand, NCardPlay play, CardModel card, NCreature expected) =>
        ReferenceEquals(FocusedTarget(hand, play, card), expected)
        && NTargetManager.Instance.AllowedToTargetNode(expected);

    internal static NativeInputResult UnfocusTarget(
        NPlayerHand hand, NCardPlay play, CardModel card, NCreature expected)
    {
        if (!CanUnfocusTarget(hand, play, card, expected))
            return NativeInputResult.Rejected("card_target_focus_changed",
                "The exact held-card focus is no longer current.");
        NTargetManager manager = NTargetManager.Instance;
        manager.OnNodeUnhovered(expected);
        return NativeInputResult.Delivered("native_target_unfocused");
    }

    internal static NativeInputResult FocusTarget(
        NPlayerHand hand, NCardPlay play, CardModel card, NCreature target)
    {
        if (!CurrentTargets(hand, play, card).Contains(target))
            return NativeInputResult.Rejected("card_target_changed",
                "The exact native target is no longer focusable.");
        NTargetManager manager = NTargetManager.Instance;
        bool accepted = false;
        void OnHovered(NCreature current)
        {
            if (ReferenceEquals(current, target)) accepted = true;
        }
        manager.CreatureHovered += OnHovered;
        try { manager.OnNodeHovered(target); }
        finally { manager.CreatureHovered -= OnHovered; }
        return accepted
            ? NativeInputResult.Delivered("native_target_focused")
            : NativeInputResult.DeliveredWithoutAcceptance("card_target_focus_not_witnessed",
                "Native focus input returned, but target acceptance was not witnessed.",
                new NativeInputStage(NativeInputStageKind.TargetFocus, NativeInputDelivery.Delivered, "native_target_focus_input_delivered"));
    }

    internal static NativeInputResult ConfirmTarget(
        NPlayerHand hand, NCardPlay play, CardModel card, NCreature target)
    {
        if (!CurrentTargets(hand, play, card).Contains(target))
            return NativeInputResult.Rejected("card_target_changed",
                "The exact native target is no longer confirmable.");

        bool accepted = false;
        NTargetManager manager = NTargetManager.Instance;
        void OnHovered(NCreature current) { if (ReferenceEquals(current, target)) accepted = true; }
        manager.CreatureHovered += OnHovered;
        try { manager.OnNodeHovered(target); }
        catch (Exception)
        {
            return NativeInputResult.Unknown("native_target_focus_unknown",
                "The native focus boundary threw; delivery is unknown.",
                new NativeInputStage(NativeInputStageKind.TargetFocus, NativeInputDelivery.Unknown, "native_target_focus_threw"));
        }
        finally { manager.CreatureHovered -= OnHovered; }
        if (!accepted)
            return NativeInputResult.DeliveredWithoutAcceptance("card_target_focus_not_witnessed",
                "Native focus input returned, but target acceptance was not witnessed.",
                new NativeInputStage(NativeInputStageKind.TargetFocus, NativeInputDelivery.Delivered, "native_target_focus_input_delivered"));
        var focus = new NativeInputStage(NativeInputStageKind.TargetFocus, NativeInputDelivery.Delivered,
            "native_target_focused");
        if (!manager.IsInSelection)
            return NativeInputResult.PartiallyDelivered("card_target_selection_changed",
                "Native focus was delivered; target confirmation was not delivered.", focus);
        try { manager._Input(new InputEventAction { Action = MegaInput.select, Pressed = true }); }
        catch (Exception)
        {
            return NativeInputResult.Unknown("native_target_confirm_unknown",
                "Focus was delivered; the confirmation input boundary threw.", focus,
                new NativeInputStage(NativeInputStageKind.TargetConfirmInput, NativeInputDelivery.Unknown, "native_target_select_input_threw"));
        }
        return NativeInputResult.DeliveredStages("native_target_select_input_delivered", focus,
            new NativeInputStage(NativeInputStageKind.TargetConfirmInput, NativeInputDelivery.Delivered, "native_target_select_input_delivered"));
    }

    internal static NativeInputResult ConfirmUntargeted(
        NPlayerHand hand, NControllerCardPlay play, CardModel card)
    {
        if (!Owns(hand, play, card)
            || card.TargetType is TargetType.AnyEnemy or TargetType.AnyAlly)
            return NativeInputResult.Rejected("card_play_changed",
                "The exact untargeted card confirmation is not current.");
        play._Input(new InputEventAction { Action = MegaInput.select, Pressed = true });
        return NativeInputResult.Delivered("native_card_confirm_input_delivered");
    }
}
