using System;
using System.Collections.Generic;
using System.Runtime.CompilerServices;
using System.Threading;
using MegaCrit.Sts2.Core.Runs;

namespace STS2Platform.NativeFoundation;

/// <summary>Exact typed native callbacks and bounded setup invocation contexts; no causal or input authority.</summary>
public sealed record NativeRunLifecycleObservation(string WitnessId, string Kind, string Mechanism,
    RunState? PreviousState, RunState? State, string? StartProvenance, bool? Graceful, bool? Victory);

public sealed class NativeRunSetupInvocation
{
    internal NativeRunSetupInvocation(RunState state, RunState? previous, string provenance, string mechanism)
    { State = state; Previous = previous; Provenance = provenance; Mechanism = mechanism; }
    internal readonly RunState State;
    internal readonly RunState? Previous;
    internal readonly string Provenance, Mechanism;
    internal bool Consumed, Removed;
}

public static class NativeRunLifecycleProvider
{
    private sealed record Origin(string Provenance);
    private static readonly object Gate = new();
    private static readonly List<NativeRunSetupInvocation> Pending = new();
    private static readonly ConditionalWeakTable<RunState, Origin> Origins = new();
    private static long observerFailures;
    public static bool HasPendingSetup { get { lock (Gate) return Pending.Exists(x => !x.Removed && !x.Consumed); } }
    public static long ObserverFailures => Interlocked.Read(ref observerFailures);
    public static event Action<NativeRunLifecycleObservation>? Observed;
    public static event Action<string>? AccountingFailure;

    public static NativeRunSetupInvocation? StageSetup(RunState state, RunState? previous, bool newRun)
    {
        lock (Gate)
        {
            if (Pending.Count < 8)
            {
                var invocation = new NativeRunSetupInvocation(state, previous, newRun ? "new" : "saved",
                    newRun ? "RunManager.SetUpNewSingleplayer.prefix->actual_State" : "RunManager.SetUpSavedSingleplayer.prefix->actual_State");
                Pending.Add(invocation); return invocation;
            }
        }
        Fail("native_setup_context_capacity"); return null;
    }
    public static bool ConsumeActualSetup(RunState? actual)
    {
        if (actual is null) return false;
        NativeRunLifecycleObservation? observation;
        lock (Gate)
        {
            var matching = Pending.FindAll(x => !x.Removed && !x.Consumed && ReferenceEquals(x.State, actual)
                && !ReferenceEquals(x.Previous, actual));
            if (matching.Count == 0) return false;
            var original = matching[0];
            bool conflict = matching.Exists(x => !ReferenceEquals(x.Previous, original.Previous)
                || x.Provenance != original.Provenance);
            foreach (var invocation in matching) invocation.Consumed = true;
            string provenance = conflict ? "unknown" : original.Provenance;
            Origins.Remove(actual); Origins.Add(actual, new(provenance));
            observation = new(Id(), "setup_handoff", original.Mechanism, original.Previous, actual, provenance, null, null);
        }
        // Observers can inspect their own short gates; none runs under this native staging lock.
        Emit(observation); return true;
    }
    public static void FinishSetup(NativeRunSetupInvocation? invocation, RunState? actual)
    {
        if (invocation is null) return;
        if (ReferenceEquals(actual, invocation.State)) ConsumeActualSetup(actual);
        Remove(invocation);
    }
    public static void AbandonSetup(NativeRunSetupInvocation? invocation)
    { if (invocation is not null) Remove(invocation); }
    private static void Remove(NativeRunSetupInvocation invocation)
    { lock (Gate) { invocation.Removed = true; Pending.Remove(invocation); } }
    public static void ObserveLaunch(RunState state)
    {
        ConsumeActualSetup(state);
        string provenance;
        lock (Gate) provenance = Origins.TryGetValue(state, out var origin) ? origin.Provenance : "unknown";
        Emit(new(Id(), "launch", "RunManager.Launch.postfix", state, state, provenance, null, null));
    }
    public static void ObserveTerminal(RunState? state, bool victory) =>
        Emit(new(Id(), "terminal", "RunManager.OnEnded.postfix", state, state, null, null, victory));
    public static void ObserveCleanup(RunState? previous, RunState? actual, bool graceful) =>
        Emit(new(Id(), "cleanup", "RunManager.CleanUp.postfix", previous, actual, null, graceful, null));
    private static string Id() => "native-lifecycle-" + Guid.NewGuid().ToString("N");
    private static void Emit(NativeRunLifecycleObservation observation)
    {
        if (Observed is not { } observed) return;
        foreach (Action<NativeRunLifecycleObservation> observer in observed.GetInvocationList())
            try { observer(observation); } catch { Interlocked.Increment(ref observerFailures); }
    }
    private static void Fail(string code)
    {
        if (AccountingFailure is not { } failed) return;
        foreach (Action<string> observer in failed.GetInvocationList())
            try { observer(code); } catch { Interlocked.Increment(ref observerFailures); }
    }
}
