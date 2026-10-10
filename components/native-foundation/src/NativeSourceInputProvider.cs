using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Threading;
using Godot;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;

namespace STS2Platform.NativeFoundation;

/// <summary>Transient process-private prefix witnesses; never serialized operands or action authority.</summary>
public sealed record NativeSourceInputPrefix(string Verb, object? Owner, object? Subject,
    IReadOnlyDictionary<string, object> Arguments, string NativeMechanism, string ExpectedNativeActionType,
    Type? AcceptedCarrierType = null);

/// <summary>Opaque original invocation. It retains no prefix owner/subject/argument graph.</summary>
public sealed class NativeSourceInputInvocation
{
    internal NativeSourceInputInvocation(NativeSourceInputProvider.Registration registration, object token, string type, Type? acceptedCarrierType)
    { Registration = registration; Token = token; ExpectedType = type; AcceptedCarrierType = acceptedCarrierType; }
    internal readonly NativeSourceInputProvider.Registration Registration;
    internal readonly object Token;
    internal readonly string ExpectedType;
    internal readonly Type? AcceptedCarrierType;
    internal bool HasCarrier;
    internal NCardPlay? CreatedCardPlay;
    internal bool CreatedCardPlayConflict;
    internal int Terminal;
}

/// <summary>Neutral forwarding from existing typed native hooks. One original Source sink owns persistence.</summary>
public static class NativeSourceInputProvider
{
    // Pure acquisition guard notification. No token, native read, allocation,
    // disk work or declaration is created before recorder activation.
    public static event Action? BeforePrefix;
    public static void BeforeInputPrefix()
    { try { BeforePrefix?.Invoke(); } catch { /* Passive guard cannot affect native input. */ } }
    internal sealed class Registration(Func<NativeSourceInputPrefix, object?> prefix,
        Action<object, string, string?> terminal) : IDisposable
    {
        internal Func<NativeSourceInputPrefix, object?>? Prefix = prefix;
        internal Action<object, string, string?>? Terminal = terminal;
        public void Dispose()
        {
            lock (Gate)
            {
                if (ReferenceEquals(current, this)) current = null;
                Prefix = null; Terminal = null;
            }
        }
    }
    private sealed record Carrier(NativeSourceInputInvocation Invocation);
    private static readonly object Gate = new();
    private static Registration? current;
    private static readonly ConditionalWeakTable<GameAction, Carrier> Carriers = new();
    [ThreadStatic] private static Stack<NativeSourceInputInvocation>? scopes;
    [ThreadStatic] private static Stack<object>? protocolDispatches;

    // Composition-only registration; public native hooks cannot create a sink.
    internal static IDisposable Register(Func<NativeSourceInputPrefix, object?> prefix, Action<object, string, string?> terminal)
    {
        lock (Gate)
        {
            if (current != null) throw new InvalidOperationException("native_source_input_owner_conflict");
            return current = new(prefix, terminal);
        }
    }
    internal static IDisposable ProtocolDispatch(object originalRequest)
    {
        protocolDispatches ??= new(); protocolDispatches.Push(originalRequest);
        return new LexicalScope(() =>
        {
            if (protocolDispatches is { Count: > 0 } && ReferenceEquals(protocolDispatches.Peek(), originalRequest))
                protocolDispatches.Pop();
        });
    }
    private sealed class LexicalScope(Action exit) : IDisposable
    { private Action? callback = exit; public void Dispose() => Interlocked.Exchange(ref callback, null)?.Invoke(); }

    public static NativeSourceInputInvocation? Begin(NativeSourceInputPrefix prefix)
    {
        if (protocolDispatches is { Count: > 0 }) return null;
        Registration? original; Func<NativeSourceInputPrefix, object?>? callback;
        lock (Gate) { original = current; callback = original?.Prefix; }
        if (original == null || callback == null) return null;
        object? token;
        try { token = callback(prefix); } catch { return null; }
        if (token == null) return null;
        var invocation = new NativeSourceInputInvocation(original, token, prefix.ExpectedNativeActionType, prefix.AcceptedCarrierType);
        scopes ??= new(); scopes.Push(invocation); return invocation;
    }
    public static void BindSubmitted(GameAction exactAction)
    {
        if (scopes is not { Count: > 0 }) return;
        var original = scopes.Peek();
        if (original.AcceptedCarrierType == null || original.AcceptedCarrierType != exactAction.GetType()
            || Volatile.Read(ref original.Terminal) != 0) return;
        bool conflict = false;
        lock (Gate)
        {
            if (Carriers.TryGetValue(exactAction, out var prior)) conflict = !ReferenceEquals(prior.Invocation, original);
            else { Carriers.Add(exactAction, new(original)); original.HasCarrier = true; }
        }
        if (conflict) Complete(original, "unknown", "native_source_carrier_conflict");
    }
    public static bool ObserveAccepted(GameAction exactAction)
    {
        NativeSourceInputInvocation? original;
        lock (Gate)
        {
            if (!Carriers.TryGetValue(exactAction, out var binding)) return false;
            original = binding.Invocation; Carriers.Remove(exactAction);
        }
        Complete(original, "delivered", null);
        return true;
    }
    public static void Accepted(NativeSourceInputInvocation? original) => Complete(original, "delivered", null);
    public static void Rejected(NativeSourceInputInvocation? original, string reason) => Complete(original, "rejected_before_input", reason);
    private static void Complete(NativeSourceInputInvocation? original, string delivery, string? reason)
    {
        if (original == null || Interlocked.Exchange(ref original.Terminal, 1) != 0) return;
        Action<object, string, string?>? callback;
        lock (Gate) callback = original.Registration.Terminal;
        try { callback?.Invoke(original.Token, delivery, reason); } catch { /* Passive evidence cannot change native delivery. */ }
    }
    public static void BindCreatedCardPlay(NCardPlay exactCarrier)
    {
        if (scopes is not { Count: > 0 }) return;
        var original = scopes.Peek();
        if (original.ExpectedType != "NPlayerHand.StartCardPlay") return;
        if (original.CreatedCardPlay != null && !ReferenceEquals(original.CreatedCardPlay, exactCarrier))
            original.CreatedCardPlayConflict = true;
        else original.CreatedCardPlay = exactCarrier;
    }
    public static void ObserveCreatedCardPlay(NativeSourceInputInvocation? original, NPlayerHand exactHand, NHandCardHolder originalHolder)
    {
        if (original == null) return;
        var carrier = original.CreatedCardPlay; original.CreatedCardPlay = null;
        bool accepted = false;
        try
        {
            accepted = !original.CreatedCardPlayConflict && carrier != null
                && ReferenceEquals(carrier.Holder, originalHolder)
                && ReferenceEquals(carrier.Holder.CardModel, originalHolder.CardModel)
                && ReferenceEquals(carrier.GetParent(), exactHand) && exactHand.InCardPlay
                && exactHand.GetChildren().OfType<NCardPlay>().Where(child => GodotObject.IsInstanceValid(child)
                    && !child.IsQueuedForDeletion()).SingleOrDefault() is { } currentCarrier
                && ReferenceEquals(currentCarrier, carrier);
        }
        catch { /* No native getter error supplies an acceptance witness. */ }
        if (accepted) Accepted(original);
    }
    public static void Finish(NativeSourceInputInvocation? original, Exception? nativeException = null)
    {
        if (original == null) return;
        original.CreatedCardPlay = null;
        if (scopes is { Count: > 0 } && ReferenceEquals(scopes.Peek(), original)) scopes.Pop();
        // An exact requested carrier may be accepted later. Its original object
        // binding remains until OnEnqueued or the existing Source close owner.
        if (nativeException != null || !original.HasCarrier)
            Complete(original, "unknown", nativeException == null
                ? "native_source_scope_ended_without_acceptance" : "native_source_input_boundary_threw");
    }
}
