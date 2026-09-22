using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;

namespace STS2Connector.NativeUi;

/// <summary>
/// Current-screen presentation references captured at the exact native
/// RefreshOptions/Create call site. This is not a legality or Human witness.
/// </summary>
internal static class CardRewardAlternativePresentationBindings
{
    // The composition hook supplies NCardRewardAlternativeButton instances.
    // Object references let source tests exercise identity without creating
    // Godot nodes; the reader checks the exact native type before authority.
    internal sealed record Pair(
        CardRewardAlternative Alternative,
        object Button);

    private sealed record Generation(
        IReadOnlyList<CardRewardAlternative> NativeAlternatives,
        IReadOnlyList<Pair> Pairs);

    internal sealed class RefreshScope
    {
        private readonly NCardRewardSelectionScreen _screen;
        private readonly IReadOnlyList<CardRewardAlternative> _nativeAlternatives;
        private readonly CardRewardAlternative[] _alternatives;
        private readonly List<Pair> _pairs = new();
        private readonly RefreshScope? _previous;
        private readonly int _threadId;
        private volatile bool _invalid;
        private volatile bool _finished;

        internal RefreshScope(
            NCardRewardSelectionScreen screen,
            IReadOnlyList<CardRewardAlternative> alternatives,
            RefreshScope? previous)
        {
            _screen = screen;
            _nativeAlternatives = alternatives;
            _alternatives = alternatives.ToArray();
            _previous = previous;
            _threadId = Environment.CurrentManagedThreadId;
        }

        internal RefreshScope? Previous => _previous;
        internal bool IsFinished => _finished;

        internal void Invalidate() => _invalid = true;

        internal void Record(object? button)
        {
            if (_finished || _invalid || _threadId != Environment.CurrentManagedThreadId
                || !ReferenceEquals(CurrentRefresh, this)
                || button == null || _pairs.Count >= _alternatives.Length
                || _pairs.Any(pair => ReferenceEquals(pair.Button, button)))
            {
                _invalid = true;
                return;
            }

            CardRewardAlternative alternative = _alternatives[_pairs.Count];
            if (alternative == null)
            {
                _invalid = true;
                return;
            }
            _pairs.Add(new Pair(alternative, button));
        }

        internal void Finish(bool nativeSucceeded)
        {
            if (_finished)
                return;
            _finished = true;
            if (_threadId != Environment.CurrentManagedThreadId)
            {
                _invalid = true;
                RemoveActiveIfCurrent(_screen, this);
                return;
            }
            if (!ReferenceEquals(CurrentRefresh, this))
            {
                InvalidateChain(CurrentRefresh);
                CurrentRefresh = null;
                _invalid = true;
                RemoveActiveIfCurrent(_screen, this);
                return;
            }
            CurrentRefresh = _previous is { _finished: false } ? _previous : null;
            bool complete = nativeSucceeded && !_invalid
                && _pairs.Count == _alternatives.Length
                && _alternatives.Distinct(ReferenceEqualityComparer.Instance).Count()
                    == _alternatives.Length;
            lock (Gate)
            {
                bool ownsActive = ActiveScopes.TryGetValue(_screen, out RefreshScope? active)
                    && ReferenceEquals(active, this);
                if (!ownsActive)
                    return;
                ActiveScopes.Remove(_screen);
                if (!complete)
                    return;
                Generations.Remove(_screen);
                Generations.Add(_screen, new Generation(
                    _nativeAlternatives,
                    _pairs.ToArray()));
            }
        }
    }

    [ThreadStatic]
    private static RefreshScope? CurrentRefresh;

    private static readonly object Gate = new();
    private static readonly ConditionalWeakTable<NCardRewardSelectionScreen, Generation>
        Generations = new();
    private static readonly ConditionalWeakTable<NCardRewardSelectionScreen, RefreshScope>
        ActiveScopes = new();

    internal static RefreshScope BeginRefresh(
        NCardRewardSelectionScreen screen,
        IReadOnlyList<CardRewardAlternative> alternatives)
    {
        ArgumentNullException.ThrowIfNull(screen);
        ArgumentNullException.ThrowIfNull(alternatives);
        if (CurrentRefresh is { IsFinished: true })
            CurrentRefresh = null;
        RefreshScope? previous = CurrentRefresh;
        var scope = new RefreshScope(screen, alternatives, previous);
        lock (Gate)
        {
            Generations.Remove(screen);
            if (ActiveScopes.TryGetValue(screen, out RefreshScope? active))
            {
                active.Invalidate();
                scope.Invalidate();
                ActiveScopes.Remove(screen);
            }
            ActiveScopes.Add(screen, scope);
        }
        if (previous != null)
        {
            InvalidateChain(previous);
            scope.Invalidate();
        }
        CurrentRefresh = scope;
        return scope;
    }

    internal static void ObserveCreated(object? button) =>
        CurrentRefresh?.Record(button);

    internal static void InvalidateCurrent() => InvalidateChain(CurrentRefresh);

    internal static void Invalidate(NCardRewardSelectionScreen screen)
    {
        lock (Gate)
        {
            Generations.Remove(screen);
            if (ActiveScopes.TryGetValue(screen, out RefreshScope? active))
                active.Invalidate();
            ActiveScopes.Remove(screen);
        }
        for (RefreshScope? scope = CurrentRefresh; scope != null; scope = scope.Previous)
            scope.Invalidate();
    }

    internal static bool TryCapture(
        NCardRewardSelectionScreen screen,
        IReadOnlyList<CardRewardAlternative> semanticAlternatives,
        IReadOnlyList<object> currentChildren,
        out IReadOnlyList<Pair> pairs)
    {
        pairs = Array.Empty<Pair>();
        Generation? generation;
        lock (Gate)
        {
            if (!Generations.TryGetValue(screen, out generation))
                return !ActiveScopes.TryGetValue(screen, out _)
                    && semanticAlternatives.Count == 0 && currentChildren.Count == 0;
        }
        try
        {
            if (generation.NativeAlternatives.Count != generation.Pairs.Count
                || generation.Pairs.Count != semanticAlternatives.Count
                || generation.Pairs.Count != currentChildren.Count)
                return false;

            var seenButtons = new HashSet<object>(
                ReferenceEqualityComparer.Instance);
            var seenAlternatives = new HashSet<CardRewardAlternative>(
                ReferenceEqualityComparer.Instance);
            var children = new HashSet<object>(
                currentChildren, ReferenceEqualityComparer.Instance);
            if (children.Count != currentChildren.Count)
                return false;

            for (int index = 0; index < generation.Pairs.Count; index++)
            {
                Pair pair = generation.Pairs[index];
                if (!ReferenceEquals(generation.NativeAlternatives[index], pair.Alternative)
                    || !ReferenceEquals(semanticAlternatives[index], pair.Alternative)
                    || !seenAlternatives.Add(pair.Alternative)
                    || !seenButtons.Add(pair.Button)
                    || !children.Contains(pair.Button))
                    return false;
            }
            pairs = generation.Pairs;
            return true;
        }
        catch
        {
            return false;
        }
    }

    internal static bool TryResolveButton(
        NCardRewardSelectionScreen screen,
        CardRewardAlternative alternative,
        IReadOnlyList<CardRewardAlternative> semanticAlternatives,
        IReadOnlyList<object> currentChildren,
        out object? button)
    {
        button = null;
        if (!TryCapture(screen, semanticAlternatives, currentChildren, out var pairs))
            return false;
        Pair[] matches = pairs
            .Where(pair => ReferenceEquals(pair.Alternative, alternative))
            .ToArray();
        if (matches.Length != 1)
            return false;
        button = matches[0].Button;
        return true;
    }

    private static void InvalidateChain(RefreshScope? scope)
    {
        for (; scope != null; scope = scope.Previous)
            scope.Invalidate();
    }

    private static void RemoveActiveIfCurrent(
        NCardRewardSelectionScreen screen,
        RefreshScope scope)
    {
        lock (Gate)
        {
            if (ActiveScopes.TryGetValue(screen, out RefreshScope? active)
                && ReferenceEquals(active, scope))
                ActiveScopes.Remove(screen);
        }
    }
}
