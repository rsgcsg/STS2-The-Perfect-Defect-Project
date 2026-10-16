using System;
using System.Collections.Generic;
using System.Reflection;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Rewards;

namespace STS2Platform.NativeFoundation;

/// <summary>
/// Exact synchronous CardReward.OnSelect observation, shared by native UI and
/// TestMode presentation adapters. It does not flow into async continuations,
/// discover an ambient reward, execute selection, or manufacture an operand.
/// </summary>
public static class NativeCardRewardSelectionObservation
{
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
        internal Action<string>? InvalidatePresentation { get; set; }
        internal object? BoundPresentation { get; set; }
        internal int ShowCalls { get; set; }
        internal bool Ambiguous { get; set; }
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
                InvalidatePresentation?.Invoke("selection_scope_cross_thread_disposal");
                return;
            }
            if (!ReferenceEquals(CurrentSelection, this))
            {
                SynchronousSelectionScope? nested = CurrentSelection;
                CurrentSelection = null;
                while (nested != null)
                {
                    nested._disposed = true;
                    nested.InvalidatePresentation?.Invoke("selection_scope_out_of_order");
                    nested = nested.Previous;
                }
                InvalidatePresentation?.Invoke("selection_scope_out_of_order");
                return;
            }
            CurrentSelection = Previous is { IsDisposed: false } ? Previous : null;
        }
    }

    [ThreadStatic]
    private static SynchronousSelectionScope? CurrentSelection;

    private static readonly FieldInfo? NativeCardsField = typeof(CardReward).GetField(
        "_cards", BindingFlags.Instance | BindingFlags.NonPublic);

    public static IDisposable BeginSynchronousOnSelect(CardReward reward)
    {
        ArgumentNullException.ThrowIfNull(reward);
        var scope = new SynchronousSelectionScope(reward, CurrentSelection);
        CurrentSelection = scope;
        return scope;
    }

    public static CardReward? BindCurrent(
        IReadOnlyList<CardCreationResult> options,
        object presentation,
        Action<string> invalidatePresentation,
        out string status,
        bool allowSequentialCallback = false)
    {
        ArgumentNullException.ThrowIfNull(options);
        ArgumentNullException.ThrowIfNull(presentation);
        ArgumentNullException.ThrowIfNull(invalidatePresentation);
        SynchronousSelectionScope? scope = CurrentSelection;
        if (scope == null || scope.IsDisposed || scope.ThreadId != Environment.CurrentManagedThreadId)
        {
            if (scope is { IsDisposed: true }) CurrentSelection = null;
            status = "parent_not_bound";
            return null;
        }
        scope.ShowCalls++;
        if (scope.Ambiguous || (scope.ShowCalls != 1 && !(allowSequentialCallback
                && ReferenceEquals(scope.BoundPresentation, presentation)))
            || !MatchesNativeOptions(scope.Reward, options))
        {
            scope.Ambiguous = true;
            scope.InvalidatePresentation?.Invoke("ambiguous_show_screen");
            status = "ambiguous_show_screen";
            return null;
        }
        scope.BoundPresentation = presentation;
        scope.InvalidatePresentation = invalidatePresentation;
        status = "exact_synchronous_parent";
        return scope.Reward;
    }

    public static bool MatchesNativeOptions(CardReward parent, IReadOnlyList<CardCreationResult> options)
    {
        try { return NativeCardsField?.GetValue(parent) is IReadOnlyList<CardCreationResult> native
            && ReferenceEquals(native, options); }
        catch { return false; }
    }
}
