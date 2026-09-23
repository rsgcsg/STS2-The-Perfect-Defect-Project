using System;
using System.Runtime.CompilerServices;
using System.Text.Json;
using Godot;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;

namespace STS2Connector.NativeUi;

/// <summary>
/// Opt-in, process-private observations for a bounded native reward canary.
/// No result from this class participates in a Snapshot or input decision.
/// </summary>
internal sealed class CardRewardCanaryDiagnostics
{
    internal const string EnvironmentVariable =
        "STS2_CONNECTOR_CARD_REWARD_CANARY_DIAGNOSTICS";
    private const int ProcessGenerationLimit = 8;

    private sealed class Cycle
    {
        internal Cycle(int generation, int expected, bool hookObserved)
        {
            Generation = generation;
            Expected = expected;
            HookObserved = hookObserved;
            ThreadId = System.Environment.CurrentManagedThreadId;
        }

        internal int Generation { get; }
        internal int Expected { get; }
        internal bool HookObserved { get; }
        internal int ThreadId { get; }
        internal int Created { get; set; }
        internal int NullCreated { get; set; }
        internal bool Finished { get; set; }
        internal bool NativeSucceeded { get; set; }
        internal bool BindingFinishReturned { get; set; }
        internal bool CrossThread { get; set; }
        internal bool FailureReported { get; set; }
        internal bool SuccessReported { get; set; }
    }

    internal static CardRewardCanaryDiagnostics Process { get; } = CreateProcess();

    private readonly object _gate = new();
    private readonly Action<string> _sink;
    private readonly int _generationLimit;
    private readonly ConditionalWeakTable<NCardRewardSelectionScreen, Cycle> _latest = new();
    private readonly ConditionalWeakTable<
        CardRewardAlternativePresentationBindings.RefreshScope, Cycle> _scopes = new();
    private int _generations;
    private volatile bool _budgetReported;
    private volatile bool _sinkFailed;
    private readonly bool _configured;

    internal CardRewardCanaryDiagnostics(bool enabled, Action<string> sink, int generationLimit = ProcessGenerationLimit)
    {
        _configured = enabled;
        _sink = sink;
        _generationLimit = Math.Max(1, generationLimit);
    }

    internal bool Enabled => _configured && !_sinkFailed && !_budgetReported;

    internal void Begin(
        NCardRewardSelectionScreen screen,
        CardRewardAlternativePresentationBindings.RefreshScope scope,
        int expectedAlternatives)
    {
        if (!Enabled) return;
        try
        {
            Cycle? cycle = NewCycle(screen, expectedAlternatives, hookObserved: true);
            if (cycle != null)
            {
                lock (_gate) _scopes.Add(scope, cycle);
                Emit(new
                {
                    event_kind = "refresh_begin",
                    generation = cycle.Generation,
                    expected_alternatives = cycle.Expected
                });
            }
        }
        catch { DisableOnInternalFailure(); }
    }

    internal void Created(
        CardRewardAlternativePresentationBindings.RefreshScope? scope,
        bool returnedButton)
    {
        if (!Enabled || scope == null) return;
        try
        {
            lock (_gate)
            {
                if (!_scopes.TryGetValue(scope, out Cycle? cycle) || cycle.Finished)
                    return;
                if (cycle.ThreadId != System.Environment.CurrentManagedThreadId)
                {
                    cycle.CrossThread = true;
                    return;
                }
                cycle.Created++;
                if (!returnedButton) cycle.NullCreated++;
            }
        }
        catch { DisableOnInternalFailure(); }
    }

    internal void Finish(
        CardRewardAlternativePresentationBindings.RefreshScope? scope,
        bool nativeSucceeded,
        bool bindingFinishReturned)
    {
        if (!Enabled || scope == null) return;
        try
        {
            int generation;
            int expected;
            int created;
            int nullCreated;
            bool crossThread;
            lock (_gate)
            {
                if (!_scopes.TryGetValue(scope, out Cycle? cycle) || cycle.Finished)
                    return;
                cycle.Finished = true;
                cycle.CrossThread |= cycle.ThreadId != System.Environment.CurrentManagedThreadId;
                cycle.NativeSucceeded = nativeSucceeded;
                cycle.BindingFinishReturned = bindingFinishReturned;
                generation = cycle.Generation;
                expected = cycle.Expected;
                created = cycle.Created;
                nullCreated = cycle.NullCreated;
                crossThread = cycle.CrossThread;
            }
            // Do not call the Godot log sink from a thread other than the
            // synchronous native RefreshOptions caller.
            if (crossThread) return;
            Emit(new
            {
                event_kind = "refresh_finish",
                generation,
                expected_alternatives = expected,
                create_postfix_count = created,
                null_create_count = nullCreated,
                native_succeeded = nativeSucceeded,
                binding_finish_returned = bindingFinishReturned,
                same_thread = true
            });
        }
        catch { DisableOnInternalFailure(); }
    }

    internal void Page(
        NCardRewardSelectionScreen screen,
        string parentStatus,
        string? existingParentId,
        bool typedAlternativesMatch,
        int semanticAlternatives,
        int currentButtons,
        bool exactButtonReferences,
        bool buttonsReady)
    {
        if (!Enabled) return;
        try
        {
            Cycle? cycle;
            lock (_gate)
            {
                if (!_latest.TryGetValue(screen, out cycle)) cycle = null;
            }
            cycle ??= NewCycle(screen, semanticAlternatives, hookObserved: false);
            if (cycle == null) return;

            object detail;
            lock (_gate)
            {
                if (!_latest.TryGetValue(screen, out Cycle? current)
                    || !ReferenceEquals(current, cycle)) return;
                if (cycle.ThreadId != System.Environment.CurrentManagedThreadId)
                {
                    cycle.CrossThread = true;
                    return;
                }
                bool complete = cycle.HookObserved && cycle.Finished
                    && cycle.NativeSucceeded && cycle.BindingFinishReturned
                    && !cycle.CrossThread
                    && cycle.Created == cycle.Expected && cycle.NullCreated == 0
                    && semanticAlternatives > 0
                    && string.Equals(parentStatus, "captured", StringComparison.Ordinal)
                    && typedAlternativesMatch && exactButtonReferences && buttonsReady;
                if (complete ? cycle.SuccessReported : cycle.FailureReported)
                    return;
                if (complete) cycle.SuccessReported = true;
                else cycle.FailureReported = true;
                detail = new
                {
                    event_kind = "page_observation",
                    generation = cycle.Generation,
                    status = complete ? "binding_facts_captured" : "incomplete",
                    failure_category = complete ? null : FailureCategory(
                        cycle, parentStatus, typedAlternativesMatch,
                        semanticAlternatives, exactButtonReferences, buttonsReady),
                    refresh_hook_observed = cycle.HookObserved,
                    refresh_finished = cycle.Finished,
                    native_succeeded = cycle.NativeSucceeded,
                    binding_finish_returned = cycle.BindingFinishReturned,
                    same_thread = !cycle.CrossThread,
                    create_postfix_count = cycle.Created,
                    parent_status = parentStatus,
                    parent_id = existingParentId,
                    parent_id_status = existingParentId == null
                        ? "registry_id_unavailable" : "registry_id_existing",
                    typed_alternatives_match = typedAlternativesMatch,
                    semantic_alternatives = semanticAlternatives,
                    current_buttons = currentButtons,
                    exact_button_references = exactButtonReferences,
                    buttons_ready = buttonsReady
                };
            }
            Emit(detail);
        }
        catch { DisableOnInternalFailure(); }
    }

    private static string FailureCategory(
        Cycle cycle,
        string parentStatus,
        bool typedAlternativesMatch,
        int semanticAlternatives,
        bool exactButtonReferences,
        bool buttonsReady)
    {
        if (!cycle.HookObserved) return "refresh_hook_unobserved";
        if (!cycle.Finished) return "refresh_unfinished";
        if (cycle.CrossThread) return "cross_thread";
        if (!cycle.NativeSucceeded) return "native_exception";
        if (!cycle.BindingFinishReturned) return "binding_finish_failed";
        if (cycle.Created != cycle.Expected || cycle.NullCreated != 0)
            return "create_postfix_mismatch";
        if (semanticAlternatives == 0) return "no_alternative_to_probe";
        if (!string.Equals(parentStatus, "captured", StringComparison.Ordinal))
            return "parent_fact_unavailable";
        if (!typedAlternativesMatch) return "typed_alternative_mismatch";
        if (!exactButtonReferences) return "button_reference_mismatch";
        if (!buttonsReady) return "button_not_current_visible";
        return "unknown_binding_mismatch";
    }

    private Cycle? NewCycle(
        NCardRewardSelectionScreen screen,
        int expectedAlternatives,
        bool hookObserved)
    {
        bool exhausted = false;
        Cycle? cycle = null;
        lock (_gate)
        {
            if (!hookObserved && _latest.TryGetValue(screen, out Cycle? current))
                return current;
            if (_generations >= _generationLimit)
            {
                _latest.Remove(screen);
                if (!_budgetReported)
                {
                    _budgetReported = true;
                    exhausted = true;
                }
            }
            else
            {
                cycle = new Cycle(++_generations, expectedAlternatives, hookObserved);
                _latest.Remove(screen);
                _latest.Add(screen, cycle);
            }
        }
        if (exhausted)
            Emit(new { event_kind = "budget_exhausted", generation_limit = _generationLimit });
        return cycle;
    }

    private void Emit(object detail)
    {
        lock (_gate)
        {
            if (_sinkFailed) return;
            try
            {
                _sink(JsonSerializer.Serialize(new
                {
                    schema = "sts2.platform/card-reward-canary-diagnostic-1",
                    detail
                }));
            }
            catch { _sinkFailed = true; }
        }
    }

    private void DisableOnInternalFailure()
    {
        lock (_gate) _sinkFailed = true;
    }

    private static CardRewardCanaryDiagnostics CreateProcess()
    {
        try
        {
            bool enabled = string.Equals(
                System.Environment.GetEnvironmentVariable(EnvironmentVariable),
                "1", StringComparison.Ordinal);
            return new CardRewardCanaryDiagnostics(enabled,
                line => GD.Print($"[STS2 Platform] card-reward-canary {line}"));
        }
        catch
        {
            return new CardRewardCanaryDiagnostics(false, _ => { });
        }
    }
}
