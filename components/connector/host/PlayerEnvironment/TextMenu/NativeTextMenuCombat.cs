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
/// class does not recreate card legality or enqueue a PlayCardAction. Mouse drag
/// requires a separate native input seam and is intentionally unavailable here.
/// </summary>
internal static class NativeTextMenuCombat
{
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
        if (!manager.IsUsingDirectionalNavigation)
        {
            // NControllerManager._Input recognizes a real controller action,
            // switches native presentation mode, and focuses the current
            // screen's default control. This is the game's own device-mode
            // transition, not a field write or a user preference change.
            manager._Input(new InputEventAction
            { Action = Controller.faceButtonSouth, Pressed = true });
            if (!manager.IsUsingDirectionalNavigation)
                return NativeInputResult.Delivered(
                    "native_controller_mode_input_delivered_card_begin_pending");
            if (!ReferenceEquals(holder.CardModel, card)
                || !CanBegin(hand, holder))
                return NativeInputResult.Delivered(
                    "native_controller_mode_changed_card_begin_pending");
        }

        // This is the holder's real Pressed signal wired to
        // NPlayerHand.OnHolderPressed. The private StartCardPlay helper must not
        // be called by reflection or reproduced by the Connector.
        holder.EmitSignal(NCardHolder.SignalName.Pressed, holder);
        return NativeInputResult.Delivered("native_hand_holder_pressed_controller_mode");
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

    internal static IReadOnlyList<NCreature> CurrentTargets(
        NPlayerHand hand, NControllerCardPlay play, CardModel card)
    {
        NCombatRoom? room = NCombatRoom.Instance;
        NTargetManager? targetManager = NTargetManager.Instance;
        if (!Owns(hand, play, card) || room == null
            || targetManager?.IsInSelection != true
            || card.TargetType is not (TargetType.AnyEnemy or TargetType.AnyAlly))
            return Array.Empty<NCreature>();

        return room.CreatureNodes
            .Where(node => ConnectorMod.IsLiveNode(node)
                && ConnectorMod.IsNodeVisible(node)
                && node.Entity is { IsHittable: true } creature
                && targetManager.AllowedToTargetNode(node)
                && card.CanPlayTargeting(creature))
            .ToArray();
    }

    internal static NativeInputResult FocusTarget(
        NPlayerHand hand, NControllerCardPlay play, CardModel card, NCreature target)
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
            : NativeInputResult.Rejected("card_target_focus_blocked",
                "The native targeting hook did not accept this creature.");
    }

    internal static NativeInputResult ConfirmTarget(
        NPlayerHand hand, NControllerCardPlay play, CardModel card, NCreature target)
    {
        if (!CurrentTargets(hand, play, card).Contains(target))
            return NativeInputResult.Rejected("card_target_changed",
                "The exact native target is no longer confirmable.");

        // OnNodeHovered is the game's public exact-object focus path; the
        // signal proves the native hook accepted the intended node immediately
        // before the game's normal select input is delivered. Do not write the
        // private HoveredNode or invoke private FinishTargeting.
        bool accepted = false;
        NTargetManager manager = NTargetManager.Instance;
        void OnHovered(NCreature current)
        {
            if (ReferenceEquals(current, target)) accepted = true;
        }
        manager.CreatureHovered += OnHovered;
        try { manager.OnNodeHovered(target); }
        finally { manager.CreatureHovered -= OnHovered; }
        if (!accepted || !manager.IsInSelection)
            return NativeInputResult.Rejected("card_target_focus_blocked",
                "Native targeting did not accept the exact creature.");

        manager._Input(new InputEventAction { Action = MegaInput.select, Pressed = true });
        return NativeInputResult.Delivered("native_target_select_input_delivered");
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
