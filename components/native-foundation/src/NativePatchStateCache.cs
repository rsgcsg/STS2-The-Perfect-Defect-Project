using System;
using System.Collections.Generic;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Threading;
using HarmonyLib;

namespace STS2Platform.NativeFoundation;

/// <summary>
/// fast_chain only, default off. Memoizes native read qualification answers that are
/// functions of loaded CLR metadata and the patch-owner state only, by patch generation.
/// Off: every caller takes its original live path and this class is never consulted.
/// Only the Managed headless entry point enables it (STS2_HEADLESS_FAST_CHAIN=1, read
/// once by that entry point); no other host calls <see cref="Configure"/>, so every
/// other host, including the in-game Mod, always stays off.
///
/// Generation: a new generation starts, and every cached answer is dropped, when the
/// set of methods that currently have at least one patch owner differs from the set
/// sampled last. Sampling points: native initialization end (after every install
/// block) and every headless command before it is handled. A patch change made INSIDE
/// a command is observed at the next command; in that process the only patch writers
/// are the initialization install blocks, which end with <see cref="Refresh"/>. The
/// call-scope selector patches (DeckUpgradeCallScope, NativeSelectorCallScope) are
/// installed once there; their per-call frames are [ThreadStatic] values and never
/// change the patch-owner state.
///
/// Live object facts (field values, delegates, listeners, formatter roster, combat
/// or TestMode state) are never cached; only the metadata/patch part is.
/// </summary>
public static class NativePatchStateCache
{
    public static bool Enabled { get; private set; }

    /// <summary>Process start only, before any read; the Managed entry point is the only caller.</summary>
    public static void Configure(bool enabled) => Enabled = enabled;

    // Sampling (rare) is serialized by Gate; the hot path takes no lock. Each thread keeps
    // its own answers tagged with the generation they were computed in, and drops them as
    // soon as it sees a newer global generation.
    private static readonly object Gate = new();
    private static HashSet<MethodBase>? _snapshot;
    private static long _generation;
    [ThreadStatic] private static Local? _local;

    public static long Generation => Volatile.Read(ref _generation);
    public static long Hits { get; private set; }
    public static long Misses { get; private set; }
    public static long Invalidations { get; private set; }

    /// <summary>The original live check: patch owners of exactly this method.</summary>
    public static bool IsUnpatchedLive(MethodBase? method)
    {
        try { return method != null && (Harmony.GetPatchInfo(method) is not { } patches || patches.Owners.Count == 0); }
        catch { return false; }
    }

    public static bool IsUnpatched(MethodBase? method)
    {
        if (!Enabled) return IsUnpatchedLive(method);
        if (method == null) return false;
        Local local = Begin(out long generation);
        if (local.Unpatched.TryGetValue(method, out bool hit)) { Hits++; return hit; }
        bool value = IsUnpatchedLive(method);
        Misses++;
        if (generation == Volatile.Read(ref _generation)) local.Unpatched[method] = value;
        return value;
    }

    /// <summary>
    /// Memoizes a predicate whose answer depends only on (a, b, c) metadata and patch state.
    /// Keys compare strings by value and every other argument by reference. An exception
    /// is never cached; it propagates to the original caller's handler.
    /// </summary>
    public static bool Memo(string id, object? a, object? b, Func<bool> compute, object? c = null)
    {
        if (!Enabled) return compute();
        Local local = Begin(out long generation);
        var key = new Key(id, a, b, c);
        if (local.Pure.TryGetValue(key, out bool hit)) { Hits++; return hit; }
        bool value = compute();
        Misses++;
        if (generation == Volatile.Read(ref _generation)) local.Pure[key] = value;
        return value;
    }

    /// <summary>Same as <see cref="Memo(string, object?, object?, Func{bool}, object?)"/> for a one-argument
    /// predicate, without allocating a closure on a hit.</summary>
    public static bool Memo<T>(string id, T argument, Func<T, bool> compute) where T : class
    {
        if (!Enabled) return compute(argument);
        Local local = Begin(out long generation);
        var key = new Key(id, argument, null, null);
        if (local.Pure.TryGetValue(key, out bool hit)) { Hits++; return hit; }
        bool value = compute(argument);
        Misses++;
        if (generation == Volatile.Read(ref _generation)) local.Pure[key] = value;
        return value;
    }

    private static readonly System.Collections.Concurrent.ConcurrentDictionary<(Type, BindingFlags), FieldInfo[]> DelegateFieldCache = new();

    /// <summary>
    /// The fields of <paramref name="type"/> (for <paramref name="flags"/>, in reflection order) whose
    /// declared type is a delegate type. Pure metadata: loaded field declarations never change in a
    /// process, so this needs no generation. Callers still read every value live.
    /// </summary>
    public static FieldInfo[] DelegateFields(Type type, BindingFlags flags) =>
        DelegateFieldCache.GetOrAdd((type, flags), static key => Array.FindAll(
            key.Item1.GetFields(key.Item2), field => typeof(Delegate).IsAssignableFrom(field.FieldType)));

    /// <summary>Sampling point: start a new generation if the patched-method set changed.</summary>
    public static void Revalidate()
    {
        if (!Enabled) return;
        HashSet<MethodBase> current = PatchedWithOwners();
        lock (Gate)
        {
            if (_snapshot != null && _snapshot.SetEquals(current)) return;
            Drop(current);
        }
    }

    /// <summary>Unconditional new generation (end of native initialization).</summary>
    public static void Refresh()
    {
        if (!Enabled) return;
        HashSet<MethodBase> current = PatchedWithOwners();
        lock (Gate) Drop(current);
    }

    private static Local Begin(out long generation)
    {
        generation = Volatile.Read(ref _generation);
        if (generation == 0)
        {
            Revalidate(); // first use before any sampling point
            generation = Volatile.Read(ref _generation);
        }
        Local local = _local ??= new Local();
        if (local.Generation != generation)
        {
            local.Unpatched.Clear();
            local.Pure.Clear();
            local.Generation = generation;
        }
        return local;
    }

    private static void Drop(HashSet<MethodBase> current)
    {
        _snapshot = current;
        Volatile.Write(ref _generation, _generation + 1);
        Invalidations++;
    }

    private static HashSet<MethodBase> PatchedWithOwners()
    {
        var current = new HashSet<MethodBase>(ReferenceEqualityComparer.Instance);
        foreach (MethodBase method in Harmony.GetAllPatchedMethods())
        {
            // A method whose patch info cannot be read counts as patched, as the
            // live check would answer for it; the set still changes with it.
            if (!IsUnpatchedLive(method)) current.Add(method);
        }
        return current;
    }

    private sealed class Local
    {
        internal long Generation = -1;
        internal readonly Dictionary<MethodBase, bool> Unpatched = new(ReferenceEqualityComparer.Instance);
        internal readonly Dictionary<Key, bool> Pure = new(KeyComparer.Instance);
    }

    private readonly record struct Key(string Id, object? A, object? B, object? C);

    private sealed class KeyComparer : IEqualityComparer<Key>
    {
        internal static readonly KeyComparer Instance = new();
        private static bool Same(object? x, object? y) =>
            x is string left && y is string right ? string.Equals(left, right, StringComparison.Ordinal) : ReferenceEquals(x, y);
        private static int Hash(object? value) =>
            value is string text ? StringComparer.Ordinal.GetHashCode(text) : value == null ? 0 : RuntimeHelpers.GetHashCode(value);
        public bool Equals(Key x, Key y) => string.Equals(x.Id, y.Id, StringComparison.Ordinal) && Same(x.A, y.A) && Same(x.B, y.B) && Same(x.C, y.C);
        public int GetHashCode(Key key) => HashCode.Combine(StringComparer.Ordinal.GetHashCode(key.Id), Hash(key.A), Hash(key.B), Hash(key.C));
    }
}
