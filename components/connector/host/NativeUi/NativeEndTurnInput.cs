using System;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;
using STS2Connector.LiveHost;

namespace STS2Connector.NativeUi;

// Host-private exact operands for one completed logical End Turn gesture.
// The native button alone constructs and requests its captured-turn action.
internal static class NativeEndTurnInput
{
    internal const string Ftue = "can_play_cards_ftue";
    internal const string FtueUnseen = "native_end_turn_ftue_unseen";
    internal const string FtueUnknown = "native_end_turn_ftue_unknown";
    internal const string BindingUnavailable = "native_end_turn_binding_unavailable";
    internal const string DeliveryEvidence = "native_end_turn_input_request_accepted";
    internal sealed record Binding(CombatRoom ModelRoom, NCombatRoom Room, Player Player,
        NPlayerHand Hand, NEndTurnButton Button, CombatState Combat, int TurnNumber)
    {
        internal Scope ExactScope => new(ModelRoom, Room, Player, Hand, Button, Combat, TurnNumber);
    }
    internal sealed record Scope(object ModelRoom, object Room, object Player, object Hand,
        object Button, object Combat, int TurnNumber);

    // Existing opaque native aliases bind the offered capture, not only the
    // freshly rebuilt delivery. Only this digest enters opaque snapshot/action
    // identity; the private aliases and captured turn never become operands.
    internal static string Identity(NativeEntityRegistry entities, Scope scope) => StableIdentityHash.Object(new
    {
        modelRoom = entities.GetId(scope.ModelRoom, "end_turn_model_room"),
        room = entities.GetId(scope.Room, "room"),
        player = entities.GetId(scope.Player, "player"),
        hand = entities.GetId(scope.Hand, "end_turn_hand"),
        button = entities.GetId(scope.Button, "end_turn_button"),
        combat = entities.GetId(scope.Combat, "end_turn_combat"),
        scope.TurnNumber
    });

    // This exact field has no public typed accessor in the pinned game. Copy its
    // value only: no reflective invocation or write to native state is allowed.
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "_combatState")]
    private static extern ref CombatState? ButtonCombatState(NEndTurnButton button);
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "get_CanTurnBeEnded")]
    internal static extern bool ReadCanTurnBeEnded(NEndTurnButton button);

    internal static Binding? CaptureOwner(NCombatRoom room, NPlayerHand hand, Player player)
    {
        var run = RunManager.Instance.DebugOnlyGetState();
        var combat = CombatManager.Instance.DebugOnlyGetState();
        var button = room.Ui.EndTurnButton;
        if (run?.CurrentRoom is not CombatRoom modelRoom || combat == null
            || !ReferenceEquals(NCombatRoom.Instance, room) || !ReferenceEquals(NPlayerHand.Instance, hand)
            || !ReferenceEquals(room.Ui.Hand, hand) || !ReferenceEquals(LocalContext.GetMe(run), player)
            || !ReferenceEquals(LocalContext.GetMe(combat), player) || !ReferenceEquals(ButtonCombatState(button), combat)
            || !ReferenceEquals(player.Creature.CombatState, combat) || player.PlayerCombatState == null
            || !ConnectorMod.IsLiveNode(room) || !ConnectorMod.IsLiveNode(hand) || !ConnectorMod.IsLiveNode(button)) return null;
        return new(modelRoom, room, player, hand, button, combat, player.PlayerCombatState.TurnNumber);
    }

    internal static bool? ReadFtueSeen()
    { try { return SaveManager.Instance.SeenFtue(Ftue); } catch { return null; } }

    internal static Binding? Capture(NCombatRoom room, NPlayerHand hand, Player player, out string? missing)
    {
        missing = BindingUnavailable;
        try
        {
            var owner = CaptureOwner(room, hand, player);
            if (owner == null) return null;
            if (ReadinessMissing(CombatTurnSurfaceReader.IsActionablePlayerTurn(player),
                hand.IsInCardSelection, owner.Button.IsEnabled,
                CombatManager.Instance.IsPlayerReadyToEndTurn(player), true) != null) return null;
            bool? seen = ReadFtueSeen();
            missing = ReadinessMissing(true, false, true, false, seen);
            return seen == true
                ? owner
                : null;
        }
        catch { missing ??= BindingUnavailable; return null; }
    }

    internal static string? ReadinessMissing(bool actionablePlay, bool inSelection,
        bool buttonEnabled, bool alreadyReady, bool? ftueSeen) =>
        !actionablePlay || inSelection || !buttonEnabled || alreadyReady ? BindingUnavailable
        : ftueSeen switch { true => null, false => FtueUnseen, _ => FtueUnknown };

    internal static bool SameScope(Scope expected, Scope? current) => current != null
        && ReferenceEquals(expected.ModelRoom, current.ModelRoom)
        && ReferenceEquals(expected.Room, current.Room) && ReferenceEquals(expected.Player, current.Player)
        && ReferenceEquals(expected.Hand, current.Hand) && ReferenceEquals(expected.Button, current.Button)
        && ReferenceEquals(expected.Combat, current.Combat) && expected.TurnNumber == current.TurnNumber;

    internal static NativeInputResult Start(Binding? expected)
    {
        if (expected == null)
            return NativeInputResult.Rejected(BindingUnavailable, "The exact End Turn input binding is unavailable.");
        var current = Capture(expected.Room, expected.Hand, expected.Player, out var missing);
        return Dispatch(expected.ExactScope, current?.ExactScope, missing,
            () => expected.Button.CallReleaseLogic());
    }

    internal static NativeInputResult Dispatch(Scope expected, Scope? current,
        string? missing, Action completedGesture) => missing != null || !SameScope(expected, current)
        ? NativeInputResult.Rejected(missing ?? "native_end_turn_binding_changed",
            "The exact End Turn owner, readiness or FTUE gate changed before input.")
        : Deliver(completedGesture);

    // One callback boundary, shared by production and its failure regression.
    // A callback exception cannot establish that no request was submitted.
    internal static NativeInputResult Deliver(Action completedGesture)
    {
        try { completedGesture(); }
        catch { return NativeInputResult.Unknown("native_end_turn_input_unknown",
            "The native End Turn input request may have been accepted before the callback threw; never retry."); }
        return NativeInputResult.Delivered(DeliveryEvidence);
    }
}
