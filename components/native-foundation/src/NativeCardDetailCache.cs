using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Models;

namespace STS2Platform.NativeFoundation;

/// <summary>Loud stop: a served card detail differs from the live native rendering.</summary>
public sealed class NativeCompactStateDivergenceException(string message) : Exception(message);

/// <summary>
/// fast_chain only (<see cref="NativePatchStateCache.Enabled"/>), default off. Memoizes the
/// formatted public card detail of one card render, keyed by a closed set of live public
/// inputs read AFTER the native preflight and preview update of that render: runtime type,
/// preview mode, display pile, combat/pile context, every scalar (and scalar collection)
/// field of the card, its live keywords, modified costs, and every dynamic variable's
/// scalar state including its preview value. Values are the game's own rendering results
/// from an earlier live render with an identical key; this class assembles no text.
///
/// Not cached (always rendered live): types that override AddExtraArgsToDescription,
/// GetDescriptionForPile or HoverTips; enchanted or afflicted cards; a card with a current
/// target; a variable with a non-scalar field or a capturing delegate; any incomplete
/// render; a render whose hover cards are not canonical.
///
/// Reconcile: every 64th headless frame (every frame in shadow mode,
/// STS2_NATIVE_CARD_DETAIL_CACHE_SHADOW=1) renders everything live and compares it with the
/// stored entry byte for byte; a difference is compact_state_divergence, which is fatal.
/// </summary>
public static class NativeCardDetailCache
{
    private const int ReconcilePeriod = 64;
    private const int MaxEntries = 8192;

    public sealed record Entry(Dictionary<string, object?> Visible, string Form);  // Form: exact canonical form

    private static readonly bool Shadow =
        Environment.GetEnvironmentVariable("STS2_NATIVE_CARD_DETAIL_CACHE_SHADOW") == "1";
    private static readonly Dictionary<string, Entry> Entries = new(StringComparer.Ordinal);
    private static readonly Dictionary<Type, bool> CacheableTypes = new();
    private static readonly Dictionary<Type, FieldInfo[]> ScalarFields = new();
    private static int _ownerThread;
    private static long _frame;

    public static long Hits { get; private set; }
    public static long Stores { get; private set; }
    public static long Compares { get; private set; }
    public static string? Divergence { get; private set; }

    private static bool Active => NativePatchStateCache.Enabled && OwnerThread();
    private static bool Reconciling => Shadow || _frame % ReconcilePeriod == 0;

    private static bool OwnerThread()
    {
        int id = Environment.CurrentManagedThreadId;
        int owner = Interlocked.CompareExchange(ref _ownerThread, id, 0);
        return owner == 0 || owner == id;
    }

    /// <summary>One headless command. Off: no-op.</summary>
    public static void BeginFrame()
    {
        if (!NativePatchStateCache.Enabled) return;
        _frame++;
        if (Shadow && _frame % 256 == 0)
            Console.Error.WriteLine($"[native-card-detail-cache] frames={_frame} compares={Compares} stores={Stores} entries={Entries.Count} divergence={(Divergence == null ? 0 : 1)}");
    }

    /// <summary>Key of a render, or null when it must be rendered live. Call after preview update.</summary>
    internal static string? Key(CardModel card, PileType displayPile, CardPreviewMode mode)
    {
        if (!Active) return null;
        Type type = card.GetType();
        if (!Cacheable(type) || card.Enchantment != null || card.Affliction != null || card.CurrentTarget != null)
            return null;
        var key = new StringBuilder(256);
        key.Append(type.FullName).Append('|').Append((int)mode).Append('|').Append((int)displayPile)
            .Append('|').Append(card.CombatState != null ? 'c' : 'n').Append('|').Append(card.Pile?.Type.ToString() ?? "-");
        if (!AppendScalars(key, card)) return null;
        key.Append("|kw");
        foreach (var keyword in (card.Keywords ?? Enumerable.Empty<CardKeyword>()).Select(k => (int)k).OrderBy(k => k))
            key.Append(',').Append(keyword);
        key.Append("|e").Append(card.EnergyCost.CostsX ? "X" : card.EnergyCost.GetWithModifiers(CostModifiers.All).ToString())
            .Append("|s").Append(card.HasStarCostX ? "X" : card.CurrentStarCost >= 0 ? card.GetStarCostWithModifiers().ToString() : "-");
        foreach (DynamicVar variable in card.DynamicVars.Values)
        {
            key.Append("|v:").Append(variable.GetType().FullName);
            if (!AppendScalars(key, variable)) return null;
        }
        return key.ToString();
    }

    /// <summary>A stored detail for this key, outside reconcile frames.</summary>
    internal static Dictionary<string, object?>? TryServe(string? key)
    {
        if (key == null || Reconciling || !Entries.TryGetValue(key, out Entry? entry)) return null;
        Hits++;
        return Clone(entry.Visible);
    }

    /// <summary>After a complete live render with canonical hover cards: store it, or compare it.</summary>
    internal static void Store(string? key, Dictionary<string, object?> visible)
    {
        if (key == null || !Active || !Plain(visible)) return;
        string json = Canonical(new StringBuilder(512), visible).ToString();
        if (Entries.TryGetValue(key, out Entry? entry))
        {
            Compares++;
            if (!string.Equals(entry.Form, json, StringComparison.Ordinal))
            {
                Divergence = "compact_state_divergence: native card detail cache differs from live render for " + key;
                throw new NativeCompactStateDivergenceException(Divergence);
            }
            return;
        }
        if (Entries.Count >= MaxEntries) Entries.Clear();
        Entries[key] = new Entry(Clone(visible), json);
        Stores++;
    }

    private static bool Cacheable(Type type)
    {
        if (CacheableTypes.TryGetValue(type, out bool value)) return value;
        value = true;
        for (Type? t = type; t != null && t != typeof(CardModel); t = t.BaseType)
            foreach (string name in new[] { "AddExtraArgsToDescription", "GetDescriptionForPile", "get_HoverTips" })
                if (t.GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly)
                    .Any(method => method.Name == name)) value = false;
        CacheableTypes[type] = value;
        return value;
    }

    private static bool Scalar(Type type) => type.IsPrimitive || type.IsEnum || type == typeof(string) || type == typeof(decimal)
        || Nullable.GetUnderlyingType(type) is { } inner && Scalar(inner);

    private static FieldInfo[] Fields(Type type)
    {
        if (ScalarFields.TryGetValue(type, out FieldInfo[]? fields)) return fields;
        var list = new List<FieldInfo>();
        for (Type? t = type; t != null && t != typeof(object); t = t.BaseType)
            list.AddRange(t.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly));
        fields = list.ToArray();
        ScalarFields[type] = fields;
        return fields;
    }

    // Scalars and scalar collections are key parts. Delegates must be non-capturing.
    // References to other models (owner, pool, clone links, vars set) are context, not text inputs.
    private static bool AppendScalars(StringBuilder key, object value)
    {
        foreach (FieldInfo field in Fields(value.GetType()))
        {
            Type type = field.FieldType;
            if (Scalar(type))
            {
                key.Append(';').Append(field.Name).Append('=').Append(field.GetValue(value));
                continue;
            }
            if (typeof(Delegate).IsAssignableFrom(type))
            {
                if (field.GetValue(value) is not Delegate call) continue;
                if (field.DeclaringType != null && typeof(DynamicVar).IsAssignableFrom(field.DeclaringType))
                {
                    if (call.Target != null && call.Target.GetType().Name != "<>c") return false;
                    key.Append(';').Append(field.Name).Append('=').Append(call.Method.DeclaringType?.FullName)
                        .Append(':').Append(call.Method.MetadataToken);
                }
                continue;
            }
            if (type.IsGenericType && field.GetValue(value) is IEnumerable items && type.GetGenericArguments() is [var item] && Scalar(item))
            {
                var parts = items.Cast<object?>().Select(element => Convert.ToString(element) ?? "").ToList();
                if (!typeof(IList).IsAssignableFrom(type)) parts.Sort(StringComparer.Ordinal);
                key.Append(';').Append(field.Name).Append("=[").Append(string.Join(',', parts)).Append(']');
                continue;
            }
            if (typeof(DynamicVar).IsAssignableFrom(value.GetType()) && field.Name != "_owner") return false;
        }
        return true;
    }

    private static bool Plain(object? value) => value switch
    {
        null or string or bool or int or long or decimal => true,
        Dictionary<string, object?> map => map.Values.All(Plain),
        List<Dictionary<string, object?>> list => list.All(Plain),
        _ => false
    };

    // Exact, type-tagged, length-prefixed form of a plain render: equal iff the values are equal.
    private static StringBuilder Canonical(StringBuilder text, object? value)
    {
        switch (value)
        {
            case null: return text.Append('n');
            case string s: return text.Append('s').Append(s.Length).Append(':').Append(s);
            case bool b: return text.Append(b ? 't' : 'f');
            case int i: return text.Append('i').Append(i.ToString(CultureInfo.InvariantCulture)).Append(';');
            case long l: return text.Append('l').Append(l.ToString(CultureInfo.InvariantCulture)).Append(';');
            case decimal d: return text.Append('d').Append(d.ToString(CultureInfo.InvariantCulture)).Append(';');
            case Dictionary<string, object?> map:
                text.Append('{').Append(map.Count).Append(':');
                foreach (var (name, item) in map) Canonical(Canonical(text, name), item);
                return text.Append('}');
            case List<Dictionary<string, object?>> list:
                text.Append('[').Append(list.Count).Append(':');
                foreach (var item in list) Canonical(text, item);
                return text.Append(']');
            default: throw new InvalidOperationException("native_card_detail_cache_unplain_value");
        }
    }

    private static Dictionary<string, object?> Clone(Dictionary<string, object?> map)
    {
        var copy = new Dictionary<string, object?>(map.Count);
        foreach (var (name, value) in map)
            copy[name] = value switch
            {
                Dictionary<string, object?> child => Clone(child),
                List<Dictionary<string, object?>> list => list.Select(Clone).ToList(),
                _ => value
            };
        return copy;
    }
}
