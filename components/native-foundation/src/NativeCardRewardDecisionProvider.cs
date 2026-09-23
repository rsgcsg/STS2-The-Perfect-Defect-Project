using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Rewards;

namespace STS2Platform.NativeFoundation;

/// <summary>
/// Tracks the exact native option lists supplied to the shipped card reward
/// selector. UI holders and buttons remain delivery bindings only.
/// </summary>
public static class NativeCardRewardDecisionProvider
{
    private sealed record Owner(
        IReadOnlyList<CardCreationResult> NativeOptions,
        IReadOnlyList<CardCreationResult> Options,
        IReadOnlyList<CardRewardAlternative> Alternatives,
        CardReward? Parent,
        string ParentStatus,
        IReadOnlyList<NativeCardRewardAlternativeFact> AlternativeFacts);

    /// <summary>
    /// CardReward.OnSelect calls ShowScreen before its first await in the pinned
    /// native build. This thread-local scope exists only during that synchronous
    /// invocation; it cannot flow into an asynchronous continuation.
    /// </summary>
    private sealed class SynchronousSelectionScope : IDisposable
    {
        internal SynchronousSelectionScope(CardReward reward, SynchronousSelectionScope? previous)
        {
            Reward = reward;
            Previous = previous;
            ThreadId = Environment.CurrentManagedThreadId;
        }

        internal CardReward Reward { get; }
        internal SynchronousSelectionScope? Previous { get; }
        internal int ThreadId { get; }
        internal NCardRewardSelectionScreen? BoundScreen { get; set; }
        internal int ShowCalls { get; set; }
        internal bool IsDisposed => _disposed;
        private volatile bool _disposed;

        public void Dispose()
        {
            if (_disposed)
            {
                if (Environment.CurrentManagedThreadId == ThreadId
                    && ReferenceEquals(CurrentSelection, this))
                    CurrentSelection = Previous is { IsDisposed: false } ? Previous : null;
                return;
            }
            _disposed = true;
            if (Environment.CurrentManagedThreadId != ThreadId)
            {
                if (BoundScreen != null)
                    InvalidateParent(BoundScreen, "selection_scope_cross_thread_disposal");
                return;
            }
            if (!ReferenceEquals(CurrentSelection, this))
            {
                SynchronousSelectionScope? nested = CurrentSelection;
                CurrentSelection = null;
                while (nested != null)
                {
                    nested._disposed = true;
                    if (nested.BoundScreen != null)
                        InvalidateParent(nested.BoundScreen, "selection_scope_out_of_order");
                    nested = nested.Previous;
                }
                if (BoundScreen != null)
                    InvalidateParent(BoundScreen, "selection_scope_out_of_order");
                return;
            }
            CurrentSelection = Previous is { IsDisposed: false } ? Previous : null;
        }
    }

    [ThreadStatic]
    private static SynchronousSelectionScope? CurrentSelection;

    private static readonly FieldInfo? NativeCardsField = typeof(CardReward).GetField(
        "_cards", BindingFlags.Instance | BindingFlags.NonPublic);
    private static readonly ConditionalWeakTable<NCardRewardSelectionScreen, Owner> Owners = new();

    public static IDisposable BeginSynchronousOnSelect(CardReward reward)
    {
        ArgumentNullException.ThrowIfNull(reward);
        var scope = new SynchronousSelectionScope(reward, CurrentSelection);
        CurrentSelection = scope;
        return scope;
    }

    public static void Register(
        NCardRewardSelectionScreen screen,
        IReadOnlyList<CardCreationResult> options,
        IReadOnlyList<CardRewardAlternative> alternatives)
    {
        RegisterExact(screen, options, alternatives, null, "parent_not_bound");
    }

    public static void RegisterFromShowScreen(
        NCardRewardSelectionScreen screen,
        IReadOnlyList<CardCreationResult> options,
        IReadOnlyList<CardRewardAlternative> alternatives)
    {
        SynchronousSelectionScope? scope = CurrentSelection;
        if (scope == null || scope.IsDisposed || scope.ThreadId != Environment.CurrentManagedThreadId)
        {
            if (scope is { IsDisposed: true })
                CurrentSelection = null;
            RegisterExact(screen, options, alternatives, null, "parent_not_bound");
            return;
        }

        scope.ShowCalls++;
        if (scope.ShowCalls != 1 || !MatchesNativeOptions(scope.Reward, options))
        {
            if (scope.BoundScreen != null)
                InvalidateParent(scope.BoundScreen, "ambiguous_show_screen");
            RegisterExact(screen, options, alternatives, null, "ambiguous_show_screen");
            return;
        }

        scope.BoundScreen = screen;
        RegisterExact(screen, options, alternatives, scope.Reward, "exact_synchronous_parent");
    }

    public static void Refresh(
        NCardRewardSelectionScreen screen,
        IReadOnlyList<CardCreationResult> options,
        IReadOnlyList<CardRewardAlternative> alternatives)
    {
        CardReward? parent = null;
        string status = "parent_not_bound";
        if (Owners.TryGetValue(screen, out Owner? prior))
        {
            parent = prior.Parent;
            status = prior.ParentStatus;
            if (parent != null && !MatchesNativeOptions(parent, options))
            {
                parent = null;
                status = "parent_options_mismatch";
            }
        }
        try
        {
            RegisterExact(screen, options, alternatives, parent, status);
        }
        catch
        {
            InvalidateParent(screen, "refresh_registration_failed");
            throw;
        }
    }

    public static NativeCardRewardParentFacts CaptureParentFacts(NCardRewardSelectionScreen screen)
    {
        if (!Owners.TryGetValue(screen, out Owner? owner))
            return new NativeCardRewardParentFacts(
                "owner_not_registered", null, Array.Empty<NativeCardRewardAlternativeFact>(),
                "No exact native ShowScreen owner was registered.");
        try
        {
            if (owner.Parent == null)
                return new NativeCardRewardParentFacts(
                    owner.ParentStatus, null, Array.Empty<NativeCardRewardAlternativeFact>(),
                    "The shown card reward has no proved synchronous parent.");
            if (!MatchesNativeOptions(owner.Parent, owner.NativeOptions)
                || owner.Options.Count != owner.NativeOptions.Count
                || owner.Options.Where((option, index) =>
                    !ReferenceEquals(option, owner.NativeOptions[index])).Any())
                return new NativeCardRewardParentFacts(
                    "parent_options_mismatch", null, Array.Empty<NativeCardRewardAlternativeFact>(),
                    "The exact parent card-option list or its contents changed without a native refresh.");
            if (owner.AlternativeFacts.Any(fact =>
                    !string.Equals(fact.Alternative.OptionId, fact.OptionId, StringComparison.Ordinal)
                    || fact.Alternative.AfterSelected != fact.AfterSelected))
                return new NativeCardRewardParentFacts(
                    "alternative_outcome_changed", null, Array.Empty<NativeCardRewardAlternativeFact>(),
                    "An alternative changed after its native screen registration.");
            return new NativeCardRewardParentFacts(
                "captured", owner.Parent, owner.AlternativeFacts, null);
        }
        catch (Exception exception)
        {
            return new NativeCardRewardParentFacts(
                "capture_failed", null, Array.Empty<NativeCardRewardAlternativeFact>(),
                $"{exception.GetType().Name}: {exception.Message}");
        }
    }

    private static void RegisterExact(
        NCardRewardSelectionScreen screen,
        IReadOnlyList<CardCreationResult> options,
        IReadOnlyList<CardRewardAlternative> alternatives,
        CardReward? parent,
        string parentStatus)
    {
        ArgumentNullException.ThrowIfNull(screen);
        ArgumentNullException.ThrowIfNull(options);
        ArgumentNullException.ThrowIfNull(alternatives);
        CardRewardAlternative[] snapshot = alternatives.ToArray();
        NativeCardRewardAlternativeFact[] facts = snapshot.Select(alternative =>
            new NativeCardRewardAlternativeFact(
                alternative, alternative.OptionId, alternative.AfterSelected)).ToArray();
        Owners.Remove(screen);
        Owners.Add(screen, new Owner(options, options.ToArray(), snapshot, parent, parentStatus, facts));
    }

    private static void InvalidateParent(NCardRewardSelectionScreen screen, string status)
    {
        if (!Owners.TryGetValue(screen, out Owner? owner))
            return;
        Owners.Remove(screen);
        Owners.Add(screen, owner with { Parent = null, ParentStatus = status });
    }

    private static bool MatchesNativeOptions(CardReward parent, IReadOnlyList<CardCreationResult> options)
    {
        try
        {
            return NativeCardsField?.GetValue(parent) is IReadOnlyList<CardCreationResult> native
                   && ReferenceEquals(native, options);
        }
        catch
        {
            return false;
        }
    }

    public static NativeCardRewardDecision Capture(
        NCardRewardSelectionScreen screen,
        INativeReferentIdentity identities)
    {
        if (!Owners.TryGetValue(screen, out Owner? owner))
        {
            return Unavailable(
                "owner_not_registered",
                "The exact card reward options were not observed at ShowScreen or RefreshOptions.");
        }

        try
        {
            var actions = new List<NativeSemanticAction>();
            foreach (CardCreationResult option in owner.Options)
            {
                string id = identities.GetId(option.Card, "card");
                actions.Add(new NativeSemanticAction(
                    NativeSemanticActionCatalog.BuildKey("select", id),
                    "select",
                    id,
                    option.Card,
                    Array.Empty<NativeSemanticOperand>(),
                    "NCardRewardSelectionScreen.ShowScreen/RefreshOptions native card options"));
            }
            foreach (CardRewardAlternative alternative in owner.Alternatives)
            {
                string id = identities.GetId(alternative, "card_reward_alternative");
                actions.Add(new NativeSemanticAction(
                    NativeSemanticActionCatalog.BuildKey("activate", id),
                    "activate",
                    id,
                    alternative,
                    Array.Empty<NativeSemanticOperand>(),
                    "NCardRewardSelectionScreen.ShowScreen/RefreshOptions native alternatives"));
            }

            return new NativeCardRewardDecision(
                "captured",
                "card_reward",
                actions.Count > 0,
                actions.ToArray(),
                new[]
                {
                    "NCardRewardSelectionScreen.ShowScreen native options",
                    "NCardRewardSelectionScreen.RefreshOptions native options",
                    "CardCreationResult.Card",
                    "CardRewardAlternative"
                },
                actions.Count == 0 ? "The active card reward has no native option." : null);
        }
        catch (Exception exception)
        {
            return Unavailable(
                "capture_failed",
                $"{exception.GetType().Name}: {exception.Message}");
        }
    }

    private static NativeCardRewardDecision Unavailable(string status, string detail) =>
        new(
            status,
            "unavailable",
            false,
            Array.Empty<NativeSemanticAction>(),
            Array.Empty<string>(),
            detail);
}
