using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Threading.Tasks;
using Godot;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.Events;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.Rewards;
using MegaCrit.Sts2.Core.Rewards;

namespace STS2Platform.NativeFoundation;

/// <summary>Transient process-private prefix witnesses; never serialized operands or action authority.</summary>
public sealed record NativeSourceInputPrefix(string Verb, object? Owner, object? Subject,
    IReadOnlyDictionary<string, object> Arguments, string NativeMechanism, string ExpectedNativeActionType,
    Type? AcceptedCarrierType = null);

/// <summary>Original invocation: weak owner identity plus same-lexical transient carrier receipts only.</summary>
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
    internal int CarrierObservationFailed;
    internal WeakReference<object>? OriginalOwner;
    internal string OriginalMechanism = "";
    internal NativeSourceCardPlayCancelScope? PendingCancel;
    internal WeakReference<object>? OriginalSubject;
    internal Task<bool>? OriginalRewardTask;
    internal bool HasOriginalRewardTask, OriginalScopeFinished, OriginalScopeFailed;
    internal int RewardTaskOutcome; // 0 pending, 1 normal bool result, 2 fault, 3 cancel, 4 conflicting task.
    public string? CarrierObservationError => Volatile.Read(ref CarrierObservationFailed) == 0
        ? null : NativeSourceInputProvider.CarrierObserverFailedReason;
}

public sealed record NativeSourceStageWitness(string Kind, object Owner, object Subject,
    IReadOnlyDictionary<string, object> Arguments, string Evidence, bool? NoSubmission);

/// <summary>Transient exact native callback receipt, discarded before its lexical finalizer returns.</summary>
public sealed class NativeSourceCardPlayCancelScope
{
    internal NativeSourceCardPlayCancelScope(NativeSourceInputProvider.Registration registration, object anchor,
        NCardPlay cardPlay, NPlayerHand hand, NHandCardHolder holder, object card, NativeSourceInputInvocation? lexical)
    { Registration = registration; Anchor = anchor; CardPlay = cardPlay; Hand = hand; Holder = holder; Card = card; Lexical = lexical; }
    internal readonly NativeSourceInputProvider.Registration Registration;
    internal readonly object Anchor, Card;
    internal readonly NCardPlay CardPlay;
    internal readonly NPlayerHand Hand;
    internal readonly NHandCardHolder Holder;
    internal readonly NativeSourceInputInvocation? Lexical;
    internal int FalseFinished, TrueFinished, Finalized;
    internal void ObserveFinished(bool played) { if (played) TrueFinished++; else FalseFinished++; }
}

/// <summary>Neutral forwarding from existing typed native hooks. One original Source sink owns persistence.</summary>
public static class NativeSourceInputProvider
{
    // Pure acquisition guard notification. No token, native read, allocation,
    // disk work or declaration is created before recorder activation.
    public static event Action? BeforePrefix;
    public static void BeforeInputPrefix()
    { try { BeforePrefix?.Invoke(); } catch { /* Passive guard cannot affect native input. */ } }
    public const string CardPlayCreatedKind = "card_play_created";
    public const string GameActionSubmittedKind = "game_action_submitted";
    public const string GameActionAcceptedKind = "game_action_accepted";
    public const string CardPlayCanceledKind = "card_play_canceled";
    public const string CarrierObserverFailedReason = "native_source_carrier_observer_failed";
    internal sealed class Registration(Func<NativeSourceInputPrefix, object?> prefix,
        Action<object, string, string?> terminal, Action<object, object, string>? sourceCarrier,
        Func<object?>? stageAnchor, Action<object, NativeSourceStageWitness>? stage, bool observeOriginalRewardTasks, bool observeOriginalEventChoices) : IDisposable
    {
        internal Func<NativeSourceInputPrefix, object?>? Prefix = prefix;
        internal Action<object, string, string?>? Terminal = terminal;
        internal Action<object, object, string>? SourceCarrier = sourceCarrier;
        internal Func<object?>? StageAnchor = stageAnchor;
        internal Action<object, NativeSourceStageWitness>? Stage = stage;
        internal readonly bool ObserveOriginalRewardTasks = observeOriginalRewardTasks;
        internal readonly bool ObserveOriginalEventChoices = observeOriginalEventChoices;
        public void Dispose()
        {
            lock (Gate)
            {
                if (ReferenceEquals(current, this)) current = null;
                Prefix = null; Terminal = null; SourceCarrier = null; StageAnchor = null; Stage = null;
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
    internal static IDisposable Register(Func<NativeSourceInputPrefix, object?> prefix, Action<object, string, string?> terminal,
        Action<object, object, string>? sourceCarrier = null, Func<object?>? stageAnchor = null,
        Action<object, NativeSourceStageWitness>? stage = null, bool observeOriginalRewardTasks = false, bool observeOriginalEventChoices = false)
    {
        lock (Gate)
        {
            if (current != null) throw new InvalidOperationException("native_source_input_owner_conflict");
            return current = new(prefix, terminal, sourceCarrier, stageAnchor, stage, observeOriginalRewardTasks, observeOriginalEventChoices);
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
        var invocation = new NativeSourceInputInvocation(original, token, prefix.ExpectedNativeActionType, prefix.AcceptedCarrierType)
        {
            OriginalOwner = original.StageAnchor == null && !original.ObserveOriginalRewardTasks || prefix.Owner == null ? null : new(prefix.Owner),
            OriginalSubject = (original.ObserveOriginalRewardTasks || original.ObserveOriginalEventChoices) && prefix.Subject != null ? new(prefix.Subject) : null,
            OriginalMechanism = prefix.NativeMechanism
        };
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
            else
            {
                Carriers.Add(exactAction, new(original)); original.HasCarrier = true;
                ObserveCarrierLocked(original, exactAction, GameActionSubmittedKind);
            }
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
            ObserveCarrierLocked(original, exactAction, GameActionAcceptedKind);
        }
        Complete(original, "delivered", null);
        return true;
    }
    public static void Accepted(NativeSourceInputInvocation? original) => Complete(original, "delivered", null);
    public static void Rejected(NativeSourceInputInvocation? original, string reason) => Complete(original, "rejected_before_input", reason);
    private static void Complete(NativeSourceInputInvocation? original, string delivery, string? reason)
    {
        if (original == null || Interlocked.Exchange(ref original.Terminal, 1) != 0) return;
        if (original.CarrierObservationError is { } gap)
            reason = reason == null ? gap : reason + "|" + gap;
        Action<object, string, string?>? callback;
        lock (Gate) callback = original.Registration.Terminal;
        try { callback?.Invoke(original.Token, delivery, reason); } catch { /* Passive evidence cannot change native delivery. */ }
    }
    // Called only under the original registration lock, after its typed native
    // witness succeeded. The observer must detach aliases synchronously. It
    // receives no new action authority and cannot redirect to a later sink.
    private static void ObserveCarrierLocked(NativeSourceInputInvocation original, object carrier, string kind)
    {
        if (Volatile.Read(ref original.Terminal) != 0) return;
        try { original.Registration.SourceCarrier?.Invoke(original.Token, carrier, kind); }
        catch { Interlocked.Exchange(ref original.CarrierObservationFailed, 1); }
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
        if (accepted)
        {
            lock (Gate) ObserveCarrierLocked(original, carrier!, CardPlayCreatedKind);
            Accepted(original);
        }
    }
    /// <summary>One actual SelectLocalReward return inside the exact original button callback, Source4 only.</summary>
    // Chosen sets WasChosen before its first await. This witnesses original
    // delivery/acceptance, never Task completion, effects or a causal successor.
    public static bool ObserveOriginalEventChosen(EventOption exactOption, Task originalTask) =>
        ObserveOriginalEventChosenCore(exactOption, exactOption.WasChosen, originalTask);
    internal static bool ObserveOriginalEventChosenCore(object exactOption, bool wasChosen, Task originalTask)
    {
        if (!wasChosen || originalTask == null || scopes is not { Count: > 0 }) return false;
        var original = scopes.Peek();
        if (!original.Registration.ObserveOriginalEventChoices || original.OriginalMechanism != "NEventRoom.OptionButtonClicked"
            || original.OriginalSubject == null || !original.OriginalSubject.TryGetTarget(out var subject)
            || !ReferenceEquals(subject, exactOption)) return false;
        Accepted(original); return true;
    }

    public static bool BindOriginalRewardTask(Reward exactReward, Task<bool> exactTask)
    {
        if (scopes is not { Count: > 0 }) return false;
        var original = scopes.Peek();
        if (original.OriginalOwner is not { } reference || !reference.TryGetTarget(out var owner)
            || owner is not NRewardButton button || !ReferenceEquals(button.Reward, exactReward)) return false;
        return BindOriginalRewardTaskCore(button, exactReward, exactTask);
    }
    // Pure lifecycle core. The production entry above alone supplies its typed native button proof.
    internal static bool BindOriginalRewardTaskCore(object exactOwner, Reward exactReward, Task<bool> exactTask)
    {
        if (protocolDispatches is { Count: > 0 } || scopes is not { Count: > 0 }) return false;
        var original = scopes.Peek();
        if (!original.Registration.ObserveOriginalRewardTasks || original.OriginalMechanism != "NRewardButton.OnRelease"
            || original.ExpectedType != "NRewardButton.OnRelease" || Volatile.Read(ref original.Terminal) != 0
            || original.OriginalOwner is not { } ownerReference || !ownerReference.TryGetTarget(out var owner)
            || !ReferenceEquals(owner, exactOwner)
            || original.OriginalSubject is not { } subjectReference || !subjectReference.TryGetTarget(out var subject)
            || !ReferenceEquals(subject, exactReward)) return false;
        lock (Gate)
        {
            if (original.HasOriginalRewardTask)
            {
                if (!ReferenceEquals(original.OriginalRewardTask, exactTask)) original.RewardTaskOutcome = 4;
                CompleteOriginalRewardTaskLocked(original); return true;
            }
            original.HasOriginalRewardTask = true; original.OriginalRewardTask = exactTask;
        }
        // Observe the exact Task; never wrap, replace, await on the native thread, or change its continuation path.
        try
        {
            if (exactTask.IsCompleted) ObserveOriginalRewardTask(original, exactTask);
            else _ = exactTask.ContinueWith(static (completed, state) =>
                ObserveOriginalRewardTask((NativeSourceInputInvocation)state!, completed), original,
                CancellationToken.None, TaskContinuationOptions.ExecuteSynchronously, TaskScheduler.Default);
        }
        catch
        {
            lock (Gate) { original.RewardTaskOutcome = 5; CompleteOriginalRewardTaskLocked(original); }
        }
        return true;
    }
    private static void ObserveOriginalRewardTask(NativeSourceInputInvocation original, Task<bool> exactTask)
    {
        lock (Gate)
        {
            if (!ReferenceEquals(original.OriginalRewardTask, exactTask)) return;
            if (original.RewardTaskOutcome != 4)
                original.RewardTaskOutcome = exactTask.IsCompletedSuccessfully ? 1 : exactTask.IsCanceled ? 3 : 2;
            CompleteOriginalRewardTaskLocked(original);
        }
    }
    private static void CompleteOriginalRewardTaskLocked(NativeSourceInputInvocation original)
    {
        if (!original.OriginalScopeFinished || original.OriginalScopeFailed || original.RewardTaskOutcome == 0) return;
        if (original.RewardTaskOutcome == 1) Complete(original, "delivered", null);
        else Complete(original, "unknown", original.RewardTaskOutcome == 3 ? "native_source_original_reward_task_canceled"
            : original.RewardTaskOutcome == 4 ? "native_source_original_reward_task_conflict"
            : original.RewardTaskOutcome == 5 ? "native_source_original_reward_task_observer_failed" : "native_source_original_reward_task_failed");
        original.OriginalRewardTask = null; // No game Task/continuation graph survives its original receipt.
    }

    /// <summary>Passive original CancelPlayCard callback only; no new input root or asynchronous carrier tracker.</summary>
    public static NativeSourceCardPlayCancelScope? BeginCardPlayCancel(NCardPlay exactCardPlay)
    {
        Registration? registration; object? anchor;
        lock (Gate) registration = current;
        if (registration?.StageAnchor == null || registration.Stage == null) return null;
        try { anchor = registration.StageAnchor(); } catch { return null; }
        if (anchor == null) return null;
        try
        {
            if (!GodotObject.IsInstanceValid(exactCardPlay) || exactCardPlay.IsQueuedForDeletion()
                || exactCardPlay.GetParent() is not NPlayerHand hand || !hand.InCardPlay
                || exactCardPlay.Holder is not { } holder || holder.CardModel is not { } card
                || hand.GetChildren().OfType<NCardPlay>().Where(child => GodotObject.IsInstanceValid(child)
                    && !child.IsQueuedForDeletion()).SingleOrDefault() is not { } only
                || !ReferenceEquals(only, exactCardPlay)) return null;
            NativeSourceInputInvocation? lexical = null;
            if (scopes is { Count: > 0 } && ReferenceEquals(scopes.Peek().Registration, registration))
            {
                var top = scopes.Peek();
                if (top.OriginalOwner is { } ownerReference && ownerReference.TryGetTarget(out var owner)
                    && (top.OriginalMechanism == "NCardPlay.TryPlayCard" && ReferenceEquals(owner, exactCardPlay)
                        || top.ExpectedType == "NPlayerHand.StartCardPlay" && ReferenceEquals(owner, hand)
                            && ReferenceEquals(top.CreatedCardPlay, exactCardPlay))) lexical = top;
            }
            var state = new NativeSourceCardPlayCancelScope(registration, anchor, exactCardPlay, hand, holder, card, lexical);
            exactCardPlay.Finished += state.ObserveFinished;
            return state;
        }
        catch { return null; } // No receipt is invented when exact pre-state cannot be observed.
    }
    public static void FinishCardPlayCancel(NativeSourceCardPlayCancelScope? original, Exception? nativeException = null)
    {
        if (original == null || Interlocked.Exchange(ref original.Finalized, 1) != 0) return;
        try { original.CardPlay.Finished -= original.ObserveFinished; } catch { return; }
        bool proven = false;
        try
        {
            proven = nativeException == null && original.FalseFinished == 1 && original.TrueFinished == 0
                && GodotObject.IsInstanceValid(original.CardPlay) && original.CardPlay.IsQueuedForDeletion()
                && ReferenceEquals(original.CardPlay.GetParent(), original.Hand)
                && ReferenceEquals(original.CardPlay.Holder, original.Holder)
                && ReferenceEquals(original.Holder.CardModel, original.Card);
        }
        catch { /* Getter failure is not a native cancellation receipt. */ }
        if (!proven) return;
        var lexical = original.Lexical;
        if (lexical != null && scopes is { Count: > 0 } && ReferenceEquals(scopes.Peek(), lexical)
            && Volatile.Read(ref lexical.Terminal) == 0 && lexical.PendingCancel == null)
            lexical.PendingCancel = original; // Released by this same synchronous invocation's finalizer.
        else PublishCancel(original, null); // Independent/async callback never backfills a sealed input.
    }
    private static void PublishCancel(NativeSourceCardPlayCancelScope original, bool? noSubmission)
    {
        Action<object, NativeSourceStageWitness>? callback;
        lock (Gate) callback = original.Registration.Stage;
        try { callback?.Invoke(original.Anchor, new(CardPlayCanceledKind, original.CardPlay, original.Card,
            new Dictionary<string, object>(StringComparer.Ordinal) { ["hand"] = original.Hand }, "native_finished_false", noSubmission)); }
        catch { /* Passive evidence failure cannot change CancelPlayCard. */ }
    }

    public static void Finish(NativeSourceInputInvocation? original, Exception? nativeException = null)
    {
        if (original == null) return;
        if (original.PendingCancel is { } canceled)
        {
            original.PendingCancel = null;
            bool open = Volatile.Read(ref original.Terminal) == 0;
            if (open) lock (Gate) ObserveCarrierLocked(original, canceled.CardPlay, CardPlayCanceledKind);
            PublishCancel(canceled, open && !original.HasCarrier && nativeException == null ? true : null);
        }
        original.CreatedCardPlay = null;
        if (scopes is { Count: > 0 } && ReferenceEquals(scopes.Peek(), original)) scopes.Pop();
        if (original.HasOriginalRewardTask) lock (Gate)
        {
            original.OriginalScopeFailed |= nativeException != null; original.OriginalScopeFinished = true;
            if (original.OriginalScopeFailed)
            { Complete(original, "unknown", "native_source_input_boundary_threw"); original.OriginalRewardTask = null; }
            else CompleteOriginalRewardTaskLocked(original);
        }
        // An exact requested carrier may be accepted later. Its original object
        // binding remains until OnEnqueued or the existing Source close owner.
        if (nativeException != null || !original.HasCarrier && !original.HasOriginalRewardTask)
            Complete(original, "unknown", nativeException == null
                ? "native_source_scope_ended_without_acceptance" : "native_source_input_boundary_threw");
    }
}
