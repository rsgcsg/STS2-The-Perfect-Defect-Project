using System;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Threading.Tasks;
using Godot;
using MegaCrit.Sts2.Core.ControllerInput;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using STS2Connector.LiveHost;
using STS2Connector.PlayerEnvironment;

namespace STS2Connector.NativeUi;

// Source-only lifetime facts. Neither registry can execute a card or invent a
// target mode: the actual native Start/Multi invocation supplies its operands.
internal static class NativeMouseCardConfirmation
{
    internal sealed record Origin(NPlayerHand Hand, NHandCardHolder Holder,
        CardModel Card, CancellationTokenSource? Cancellation);
    internal sealed record Phase(Origin Original, TargetMode Mode);
    internal sealed record Binding(NMouseCardPlay Play, NativeSourceInvocation<Phase>.Ticket Ticket,
        Phase Source, bool Pressed);
    private static readonly ConditionalWeakTable<NMouseCardPlay, NativeSourceInvocation<Origin>> Operations = new();
    private static readonly ConditionalWeakTable<NMouseCardPlay, NativeSourceInvocation<Phase>> Phases = new();
    private const BindingFlags Fields = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
    private static readonly FieldInfo? Cancellation = typeof(NMouseCardPlay).GetField("_cancellationTokenSource", Fields);
    private static readonly FieldInfo? LeftDown = typeof(NMouseCardPlay).GetField("_isLeftMouseDown", Fields);
    private static readonly FieldInfo? Shortcut = typeof(NMouseCardPlay).GetField("_cancelShortcut", Fields);
    private static bool registered;
    internal static void Registered() => registered = true;
    private static Origin? ReadOrigin(NMouseCardPlay play) => NPlayerHand.Instance is { } hand
        && play.Holder?.CardModel is { } card && NativeTextMenuCombat.Owns(hand, play, card)
        ? new(hand, play.Holder, card, Cancellation?.GetValue(play) as CancellationTokenSource) : null;

    internal static NativeSourceInvocation<Origin>.Ticket? StartEntering(NMouseCardPlay play)
    {
        Phases.GetValue(play, static _ => new()).Invalidate();
        if (ReadOrigin(play) is not { } origin) { Operations.GetValue(play, static _ => new()).Invalidate(); return null; }
        return Operations.GetValue(play, static _ => new()).Begin(origin);
    }
    internal static void StartReturned(NMouseCardPlay play, NativeSourceInvocation<Origin>.Ticket? ticket)
    {
        var owner = Operations.GetValue(play, static _ => new());
        if (ticket != null && ReadOrigin(play) is { Cancellation: not null } current
            && SameOriginal(ticket.Fact, current)) owner.Complete(ticket, current);
        else owner.Invalidate(ticket);
        // Multi may already have entered synchronously inside this same Start.
        // A Start return never replaces or clears that independent phase ticket.
    }
    internal static void StartFinalized(NMouseCardPlay play, NativeSourceInvocation<Origin>.Ticket? ticket, Exception? exception) =>
        Operations.GetValue(play, static _ => new()).Finalized(ticket, exception);

    internal static NativeSourceInvocation<Phase>.Ticket? MultiEntering(NMouseCardPlay play, TargetMode actualMode)
    {
        var owner = Phases.GetValue(play, static _ => new());
        if (ReadOrigin(play) is not { Cancellation: not null } origin
            || actualMode is not (TargetMode.ReleaseMouseToTarget or TargetMode.ClickMouseToTarget))
        { owner.Invalidate(); return null; }
        // Actual METHOD_ENTRY is a synchronous proof before the first body
        // preview. The same ticket must acquire its actual returned Task next.
        return owner.Begin(new(origin, actualMode));
    }
    internal static void MultiReturned(NMouseCardPlay play, NativeSourceInvocation<Phase>.Ticket? ticket, Task? actualTask) =>
        Phases.GetValue(play, static _ => new()).Returned(ticket, actualTask);
    internal static void MultiFinalized(NMouseCardPlay play, NativeSourceInvocation<Phase>.Ticket? ticket, Exception? exception) =>
        Phases.GetValue(play, static _ => new()).Finalized(ticket, exception);
    internal static void Invalidate(NMouseCardPlay play)
    {
        if (Operations.TryGetValue(play, out var operation)) operation.Invalidate();
        if (Phases.TryGetValue(play, out var phase)) phase.Invalidate();
    }
    internal static bool SameOriginal(Origin a, Origin b) => ReferenceEquals(a.Hand, b.Hand)
        && ReferenceEquals(a.Holder, b.Holder) && ReferenceEquals(a.Card, b.Card);
    internal static bool SameLiveOrigin(Origin expected, Origin actual) => SameOriginal(expected, actual)
        && actual.Cancellation is { IsCancellationRequested: false }
        && (expected.Cancellation == null || ReferenceEquals(expected.Cancellation, actual.Cancellation));
    private static bool CurrentOrigin(NMouseCardPlay play, Origin expected) =>
        ReadOrigin(play) is { } actual && SameLiveOrigin(expected, actual);
    internal static bool KnownOperation(NMouseCardPlay play, NPlayerHand hand, CardModel card)
    {
        if (!registered) return false;
        if (Phases.TryGetValue(play, out var phase) && phase.Active is { } active
            && ReferenceEquals(active.Fact.Original.Hand, hand) && ReferenceEquals(active.Fact.Original.Card, card)
            && CurrentOrigin(play, active.Fact.Original)) return true;
        if (!Operations.TryGetValue(play, out var operation)) return false;
        var witnessed = operation.Active ?? operation.Completed;
        return witnessed != null && ReferenceEquals(witnessed.Fact.Hand, hand)
            && ReferenceEquals(witnessed.Fact.Card, card) && CurrentOrigin(play, witnessed.Fact);
    }
    internal static Binding? Capture(NMouseCardPlay play, NPlayerHand hand, CardModel card)
    {
        if (!registered || !Phases.TryGetValue(play, out var owner) || owner.Active is not { } ticket
            || !ReferenceEquals(ticket.Fact.Original.Hand, hand) || !ReferenceEquals(ticket.Fact.Original.Card, card)
            || !CurrentOrigin(play, ticket.Fact.Original)
            || card.TargetType is TargetType.AnyEnemy or TargetType.AnyAlly
            || ActiveInputResolver.Capture().OpenModal != null
            || NControllerManager.Instance?.IsUsingDirectionalNavigation != false
            || LeftDown?.GetValue(play) is not bool down || !TransitionPending(ticket.Fact.Mode, down)
            || !ReadNativePlayZone(play) || CollidesWithNativeCancel(play, ticket.Fact.Mode)) return null;
        return new(play, ticket, ticket.Fact, ticket.Fact.Mode == TargetMode.ClickMouseToTarget);
    }
    internal static bool TransitionPending(TargetMode mode, bool leftDown) => mode switch
    {
        TargetMode.ReleaseMouseToTarget => leftDown,
        TargetMode.ClickMouseToTarget => !leftDown,
        _ => false
    };
    private static bool CollidesWithNativeCancel(NMouseCardPlay play, TargetMode mode)
    {
        if (Shortcut?.GetValue(play) is not StringName shortcut) return true;
        using var prospective = new InputEventMouseButton
        { ButtonIndex = MouseButton.Left, Pressed = mode == TargetMode.ClickMouseToTarget };
        // Unattached object, no listeners or dispatch. Use the exact same native
        // shortcut query as _Input; no input-map rules or coordinate data copied.
        return prospective.IsActionPressed(shortcut, false, false);
    }
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "IsCardInPlayZone")]
    internal static extern bool ReadNativePlayZone(NMouseCardPlay owner);

    internal static NativeInputResult Confirm(Binding expected)
    {
        Binding? current = Capture(expected.Play, expected.Source.Original.Hand, expected.Source.Original.Card);
        if (current == null || !ReferenceEquals(current.Ticket, expected.Ticket)
            || current.Source.Mode != expected.Source.Mode || current.Pressed != expected.Pressed)
            return NativeInputResult.Rejected("native_mouse_confirm_changed", "The exact native mouse confirmation scope changed before input.");
        try
        {
            using var input = new InputEventMouseButton { ButtonIndex = MouseButton.Left, Pressed = expected.Pressed };
            // Recheck native shortcut semantics on the actual prospective input.
            if (Shortcut?.GetValue(expected.Play) is not StringName shortcut || input.IsActionPressed(shortcut, false, false))
                return NativeInputResult.Rejected("native_mouse_confirm_cancel_collision", "The native left-state input also matches the current cancel shortcut.");
            expected.Play._Input(input);
        }
        catch (Exception)
        {
            return NativeInputResult.Unknown("native_mouse_confirm_input_unknown", "The native mouse input may have been delivered before the boundary threw.");
        }
        return NativeInputResult.Delivered("native_mouse_left_confirmation_input_delivered; native_continuation_not_verified");
    }
}
