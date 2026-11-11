using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Models;

namespace STS2Platform.NativeFoundation;

/// <summary>
/// fast_chain only, two switches, both default off: <see cref="Lane.Deck"/> (one master-deck card
/// of the player summary, STS2_HEADLESS_DECK_MEMO=1) and <see cref="Lane.Hud"/> (one combat HUD
/// pile card outside Hand and Play, STS2_HEADLESS_HUD_MEMO=1). Memoizes the whole public projection
/// of that card (everything but native_ref), keyed by the card's live state. Off, or for an
/// unkeyed card, the caller's original live code runs; this class assembles no text.
///
/// Invalidation is the key, not events: every render reads the key again from live state, so any
/// change of the card face (upgrade, cost change, temporary cost, keyword, variable, pile, combat
/// start or end) changes the key. Key = lane, runtime type, display and actual pile, combat in
/// progress, upgrade preview type, owner's Osty alive, energy icon prefix, patch generation, every
/// scalar and scalar-collection field of the card, its energy cost (with local modifiers and
/// temporary star costs), live keywords, modified energy and star cost, and every variable's scalar
/// state (base, preview, enchanted, upgraded flag).
///
/// Why that set is complete (game build of <see cref="NativeCardDetailCache"/>): a keyed card is never
/// in Hand or Play, and its upgrade preview type is never Combat, so the native preview update runs
/// no global hooks; its upgrade preview is a detached copy outside every pile, so it has no combat
/// state either. Then every variable kept here derives its preview only from its own fields. Outside
/// state reaches the text only through the inputs keyed above, except where excluded:
/// - enchanted, afflicted or targeted cards, star-cost X, a card without an owner;
/// - calculated variables (their delegates read the owner, piles or combat);
/// - card types overriding the title, the description paths or extra description arguments;
/// - card types declaring a non-scalar instance field;
/// - card types whose hover tips read the owner or combat (<see cref="OutsideHoverTips"/>);
/// - card types not in the frozen shadow list, or any other game build.
///
/// Reconcile: each lane counts its own render frames (headless commands in which it computed at least
/// one key), not commands, so a command that renders no card cannot absorb the check. On every 64th
/// render frame of a lane (every frame in shadow mode, STS2_NATIVE_CARD_MEMO_SHADOW=1) every keyed card
/// of that lane renders live and its JSON is compared with the stored entry byte for byte; a difference
/// is compact_state_divergence, which is fatal. Reconcile is part of the measured cost. Shadow mode
/// also asserts that computing a key left the card's footprint unchanged.
///
/// JSON: this class does no JSON I/O itself (Native Foundation owns no serializer). The Managed entry
/// point passes the full writer's default serializer to <see cref="Configure"/>; the stored text and
/// every reconcile comparison use it.
///
/// Episodes: <see cref="BeginEpisode"/> (run start or save load, where the output language is set)
/// drops every entry, so no text of an earlier language or episode is ever served.
/// </summary>
public static class NativeCardProjectionMemo
{
    public enum Lane { Deck = 0, Hud = 1 }

    private const int ReconcilePeriod = 64;
    private const int MaxEntries = 16384;
    private const BindingFlags Declared = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;

    private sealed record Stored(Dictionary<string, object?> Value, string Json);

    private static readonly bool Shadow =
        Environment.GetEnvironmentVariable("STS2_NATIVE_CARD_MEMO_SHADOW") == "1";
    private static readonly bool Stats = Shadow
        || Environment.GetEnvironmentVariable("STS2_NATIVE_CARD_MEMO_STATS") == "1";
    private static readonly Dictionary<string, Stored> Entries = new(StringComparer.Ordinal);
    private static readonly Dictionary<Type, bool> MemoTypes = new();
    private static readonly Dictionary<string, long> TypeCompares = new(StringComparer.Ordinal);
    private static readonly long[] HitCount = new long[2], StoreCount = new long[2], CompareCount = new long[2], UnkeyedCount = new long[2];
    private static readonly string[] RenderOverrides =
        { "AddExtraArgsToDescription", "GetDescriptionForPile", "GetDescriptionForUpgradePreview", "get_HoverTips", "get_Title" };
    // Static audit of every card type's ExtraHoverTips on this game build: these read the owner or combat.
    private static readonly HashSet<string> OutsideHoverTips = new(StringComparer.Ordinal) { "Fasten", "FranticEscape" };
    private static int _configured;
    private static int _ownerThread;
    private static long _frame;
    private static long _reconcileTicks;
    private static long _episodes;
    // Per lane: the command of its last render frame, its render frame count, whether that frame reconciles.
    private static readonly long[] LaneCommand = { -1, -1 }, LaneFrames = new long[2], ReconcileFrameCount = new long[2];
    private static readonly bool[] LaneReconciling = new bool[2];
    private static int _buildChecked;
    private static Func<Dictionary<string, object?>, string>? _serialize;

    static NativeCardProjectionMemo()
    {
        if (Stats)
            AppDomain.CurrentDomain.ProcessExit += (_, _) => Console.Error.WriteLine("[native-card-memo] " + Summary());
    }

    public static bool DeckEnabled { get; private set; }
    public static bool HudEnabled { get; private set; }
    public static string? Divergence { get; private set; }
    public static long Hits(Lane lane) => HitCount[(int)lane];
    public static long Stores(Lane lane) => StoreCount[(int)lane];
    public static long Compares(Lane lane) => CompareCount[(int)lane];
    public static long Unkeyed(Lane lane) => UnkeyedCount[(int)lane];
    public static long RenderFrames(Lane lane) => LaneFrames[(int)lane];
    public static long ReconcileFrames(Lane lane) => ReconcileFrameCount[(int)lane];
    /// <summary>Wall time of the live renders of reconcile frames that had a key (the price of the check).</summary>
    public static double ReconcileMilliseconds => _reconcileTicks * 1000.0 / Stopwatch.Frequency;

    /// <summary>
    /// Process start only, once, by the Managed entry point. A switch requested without fast_chain,
    /// or without the serializer (<paramref name="serialize"/>: a projection's JSON text, as the
    /// full writer's default serializer writes it), fails loudly instead of quietly staying off.
    /// </summary>
    public static void Configure(bool deck, bool hud, Func<Dictionary<string, object?>, string>? serialize = null)
    {
        if (Interlocked.Exchange(ref _configured, 1) != 0)
            throw new InvalidOperationException("native_card_memo_configured_twice");
        if ((deck || hud) && !NativePatchStateCache.Enabled)
            throw new InvalidOperationException("native_card_memo_requires_fast_chain");
        if ((deck || hud) && serialize == null)
            throw new InvalidOperationException("native_card_memo_requires_serializer");
        _serialize = serialize;
        DeckEnabled = deck;
        HudEnabled = hud;
    }

    /// <summary>One headless command. Off: no-op.</summary>
    public static void BeginFrame()
    {
        if (DeckEnabled || HudEnabled) _frame++;
    }

    private static bool Enabled(Lane lane) =>
        NativePatchStateCache.Enabled && (lane == Lane.Deck ? DeckEnabled : HudEnabled);

    /// <summary>
    /// Run start or save load (the only places the native and output language are set). Drops every entry.
    /// Off: no-op.
    /// </summary>
    public static void BeginEpisode()
    {
        if (!DeckEnabled && !HudEnabled) return;
        Entries.Clear();
        _episodes++;
    }

    // The first keyed card of a lane in a command opens that lane's render frame.
    private static bool Reconciling(Lane lane)
    {
        int i = (int)lane;
        if (LaneCommand[i] != _frame)
        {
            LaneCommand[i] = _frame;
            LaneFrames[i]++;
            LaneReconciling[i] = Shadow || LaneFrames[i] % ReconcilePeriod == 0;
            if (LaneReconciling[i]) ReconcileFrameCount[i]++;
        }
        return LaneReconciling[i];
    }

    private static bool OwnerThread()
    {
        int id = Environment.CurrentManagedThreadId;
        int owner = Interlocked.CompareExchange(ref _ownerThread, id, 0);
        return owner == 0 || owner == id;
    }

    /// <summary>
    /// The card's projection without native_ref: a copy of the stored one, or <paramref name="render"/>
    /// (the original live code). <paramref name="json"/> is that projection's default JSON when this
    /// class produced or checked it, otherwise null; it is the same bytes the caller would serialize.
    /// </summary>
    internal static Dictionary<string, object?> Project(Lane lane, CardModel card, PileType displayPile,
        Func<Dictionary<string, object?>> render, out string? json)
    {
        json = null;
        if (!Enabled(lane)) return render();
        string? key = Key(lane, card, displayPile);
        if (key == null)
        {
            UnkeyedCount[(int)lane]++;
            return render();
        }
        bool reconciling = Reconciling(lane);
        if (!reconciling && Entries.TryGetValue(key, out Stored? hit))
        {
            HitCount[(int)lane]++;
            json = hit.Json;
            return Copy(hit.Value);
        }
        long started = reconciling ? Stopwatch.GetTimestamp() : 0;
        Dictionary<string, object?> live = render();
        string text = _serialize!(live);
        if (Entries.TryGetValue(key, out Stored? stored))
        {
            CompareCount[(int)lane]++;
            if (Shadow)
            {
                string name = lane + ":" + card.GetType().Name;
                TypeCompares[name] = TypeCompares.GetValueOrDefault(name) + 1;
            }
            if (!string.Equals(stored.Json, text, StringComparison.Ordinal))
            {
                Divergence = $"compact_state_divergence: native card projection memo ({lane}) differs from live render for {card.GetType().FullName}";
                throw new NativeCompactStateDivergenceException(Divergence);
            }
        }
        else if (Copyable(live))
        {
            if (Entries.Count >= MaxEntries) Entries.Clear();
            Entries[key] = new Stored(Copy(live), text);
            StoreCount[(int)lane]++;
        }
        if (reconciling) _reconcileTicks += Stopwatch.GetTimestamp() - started;
        json = text;
        return live;
    }

    // Game-typed reflection lives here, not in the outer class: Configure runs at process start,
    // before the game assembly resolver is installed, and must not load the game assembly.
    private static class Game
    {
        internal static readonly HashSet<Type> LocalVariables = new()
        {
            typeof(DynamicVar), typeof(BlockVar), typeof(BoolVar), typeof(CardsVar), typeof(DamageVar), typeof(EnergyVar),
            typeof(ExtraDamageVar), typeof(ForgeVar), typeof(GoldVar), typeof(HealVar), typeof(HpLossVar), typeof(IfUpgradedVar),
            typeof(IntVar), typeof(MaxHpVar), typeof(OstyDamageVar), typeof(RepeatVar), typeof(StarsVar), typeof(StringVar),
            typeof(SummonVar)
        };
        internal static readonly NativeField? TemporaryStarCosts =
            NativeField.Of(typeof(CardModel).GetField("_temporaryStarCosts", Declared));
        // CardModel getters that fill a field on first read. A key never reads one before it is filled: a card
        // with any still empty renders live (the live render fills it), so computing a key changes nothing.
        internal static readonly NativeField?[] LazyFields = new[] { "_dynamicVars", "_energyCost", "_pool", "_keywords" }
            .Select(name => NativeField.Of(typeof(CardModel).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic))).ToArray();
        internal static readonly NativeField? StarCostSet = NativeField.Of(typeof(CardModel).GetField("_starCostSet", BindingFlags.Instance | BindingFlags.NonPublic));
        internal static readonly NativeField? LocalModifiers =
            NativeField.Of(typeof(CardEnergyCost).GetField("_localModifiers", Declared));
    }

    /// <summary>Key of a card, or null when it must be rendered live. Reads only; never renders.</summary>
    internal static string? Key(Lane lane, CardModel card, PileType displayPile)
    {
        if (!Enabled(lane) || !OwnerThread()) return null;
        if (Interlocked.Exchange(ref _buildChecked, 1) == 0 && !Shadow
            && typeof(CardModel).Module.ModuleVersionId != NativeCardDetailCache.ShadowedGameMvid)
            Console.Error.WriteLine("[native-card-memo] game module " + typeof(CardModel).Module.ModuleVersionId
                + " is not the build the type list was frozen on; every card renders live (refreeze with shadow mode)");
        if (Game.LazyFields.Any(field => field == null || field.GetValue(card) == null) || Game.StarCostSet?.GetValue(card) is not true) return null;
        // Shadow: every path out of the key, keyed or not, must leave the card footprint unchanged.
        NativeDetachedCardPreviewFactory.SourceStamp? before = Shadow ? NativeDetachedCardPreviewFactory.SourceStamp.Card(card) : null;
        string? result = KeyOf(lane, card, displayPile);
        before?.AssertUnchanged();
        return result;
    }

    private static string? KeyOf(Lane lane, CardModel card, PileType displayPile)
    {
        if (card.Enchantment != null || card.Affliction != null || card.CurrentTarget != null || card.Owner == null
            || card.UpgradePreviewType == CardUpgradePreviewType.Combat || card.HasStarCostX)
            return null;
        Type type = card.GetType();
        if (!MemoType(type)) return null;
        PileType? pile = card.Pile?.Type;
        if (lane == Lane.Deck
                ? pile != PileType.Deck || displayPile != PileType.Deck || card.CombatState != null
                : pile is null or PileType.Hand or PileType.Play || pile != displayPile)
            return null;
        foreach (DynamicVar variable in card.DynamicVars.Values)
            if (!Game.LocalVariables.Contains(variable.GetType())
                && !(variable.GetType().IsGenericType && variable.GetType().GetGenericTypeDefinition() == typeof(PowerVar<>)))
                return null;
        var key = new StringBuilder(512);
        key.Append((int)lane).Append('|').Append(type.FullName).Append('|').Append((int)displayPile).Append('|').Append((int)pile!.Value)
            .Append('|').Append(CombatManager.Instance.IsInProgress ? 'c' : 'n').Append('|').Append((int)card.UpgradePreviewType)
            .Append('|').Append(card.Owner.IsOstyAlive ? 'o' : '-').Append('|').Append(EnergyIconHelper.GetPrefix(card))
            .Append('|').Append(NativePatchStateCache.Generation);
        if (!NativeCardDetailCache.AppendScalars(key, card)) return null;
        CardEnergyCost cost = card.EnergyCost;
        key.Append("|ec");
        if (!NativeCardDetailCache.AppendScalars(key, cost)) return null;
        if (Game.LocalModifiers?.GetValue(cost) is not System.Collections.IList modifiers) return null;
        foreach (object? modifier in modifiers)
        {
            key.Append("|lm");
            if (modifier == null || !NativeCardDetailCache.AppendScalars(key, modifier)) return null;
        }
        if (Game.TemporaryStarCosts?.GetValue(card) is not System.Collections.IList starCosts) return null;
        foreach (object? starCost in starCosts)
        {
            key.Append("|ts");
            if (starCost == null || !NativeCardDetailCache.AppendScalars(key, starCost)) return null;
        }
        key.Append("|kw");
        foreach (int keyword in card.Keywords.Select(k => (int)k).OrderBy(k => k))
            key.Append(',').Append(keyword);
        key.Append("|e").Append(cost.CostsX ? "X" : cost.GetWithModifiers(CostModifiers.All).ToString())
            .Append("|s").Append(card.CurrentStarCost >= 0 ? card.GetStarCostWithModifiers().ToString() : "-");
        foreach (DynamicVar variable in card.DynamicVars.Values)
        {
            key.Append("|v:").Append(variable.GetType().FullName);
            if (!NativeCardDetailCache.AppendScalars(key, variable)) return null;
        }
        return key.ToString();
    }

    private static bool MemoType(Type type)
    {
        if (MemoTypes.TryGetValue(type, out bool value)) return value;
        // Shadow mode measures every structurally eligible type; otherwise only the frozen list.
        value = type.Namespace == "MegaCrit.Sts2.Core.Models.Cards" && !OutsideHoverTips.Contains(type.Name)
            && (Shadow || typeof(CardModel).Module.ModuleVersionId == NativeCardDetailCache.ShadowedGameMvid && FrozenTypes.Contains(type.Name));
        for (Type? t = type; value && t != null && t != typeof(CardModel); t = t.BaseType)
        {
            if (t.GetMethods(Declared).Any(method => RenderOverrides.Contains(method.Name))) value = false;
            if (t.GetFields(Declared).Any(field => !NativeCardDetailCache.ScalarOrScalarCollection(field.FieldType))) value = false;
        }
        MemoTypes[type] = value;
        return value;
    }

    // The projection is a tree of string-keyed dictionaries, lists and arrays of plain values.
    private static bool Copyable(object? value) => value switch
    {
        null or string or bool or int or long or decimal or double => true,
        Dictionary<string, object?> map => map.Values.All(Copyable),
        List<Dictionary<string, object?>> list => list.All(Copyable),
        Dictionary<string, object?>[] array => array.All(Copyable),
        List<string> or string[] => true,
        List<object?> list => list.All(Copyable),
        object?[] array => array.All(Copyable),
        _ => false
    };

    private static Dictionary<string, object?> Copy(Dictionary<string, object?> map)
    {
        var copy = new Dictionary<string, object?>(map.Count);
        foreach (var (name, value) in map) copy[name] = CopyValue(value);
        return copy;
    }

    private static object? CopyValue(object? value) => value switch
    {
        null or string or bool or int or long or decimal or double => value,
        Dictionary<string, object?> map => Copy(map),
        List<Dictionary<string, object?>> list => list.Select(Copy).ToList(),
        Dictionary<string, object?>[] array => array.Select(Copy).ToArray(),
        List<string> list => new List<string>(list),
        string[] array => (string[])array.Clone(),
        List<object?> list => list.Select(CopyValue).ToList(),
        object?[] array => array.Select(CopyValue).ToArray(),
        _ => throw new InvalidOperationException("native_card_memo_uncopyable_value")
    };

    private static string Summary() =>
        string.Join(' ', Enum.GetValues<Lane>().Select(lane => $"{lane.ToString().ToLowerInvariant()}:hits={Hits(lane)},stores={Stores(lane)},compares={Compares(lane)},unkeyed={Unkeyed(lane)},render_frames={RenderFrames(lane)},reconcile_frames={ReconcileFrames(lane)}"))
        + $" frames={_frame} episodes={_episodes} entries={Entries.Count} reconcile_ms={ReconcileMilliseconds:F1} divergence={(Divergence == null ? 0 : 1)}"
        + (Shadow ? " type-compares " + string.Join(',', TypeCompares.OrderBy(pair => pair.Key, StringComparer.Ordinal).Select(pair => pair.Key + "=" + pair.Value)) : "");

    // Frozen from Friday shadow job 1458801 (92 corpus games, both lanes, every frame live vs memo plus the
    // read-only key check: 1,461,862 compares, 0 divergence; 142 types with >= 1 compare) on this game build
    // only (NativeCardDetailCache.ShadowedGameMvid). Refreeze after any game update.
    // Names are in MegaCrit.Sts2.Core.Models.Cards.
    private static readonly HashSet<string> FrozenTypes = new(StringComparer.Ordinal)
    {
        "AdaptiveStrike", "AllForOne", "Anointed", "Anticipate", "Backstab", "BallLightning", "BeamCell", "BeatDown",
        "Beckon", "BiasedCognition", "Bombardment", "BoostAway", "BootSequence", "Buffer", "BulkUp", "Bulwark",
        "Burn", "Capacitor", "Chaos", "ChargeBattery", "Chill", "Clash", "Claw", "CloakAndDagger", "Clumsy",
        "ColdSnap", "Compact", "ConsumingShadow", "Coolant", "Coolheaded", "CurseOfTheBell", "DarkShackles",
        "Darkness", "Dash", "Dazed", "DeadlyPoison", "DefendDefect", "Defragment", "Devastate", "DoubleEnergy",
        "Doubt", "DramaticEntrance", "DualWield", "Dualcast", "EchoForm", "Enthralled", "Equilibrium", "Expose",
        "Exterminate", "Feral", "Finesse", "FocusedStrike", "Ftl", "Fusion", "GeneticAlgorithm", "Glacier",
        "Glasswork", "GoForTheEyes", "Greed", "Guilty", "GunkUp", "Hailstorm", "Hologram", "Hotfix", "Hyperbeam",
        "IceLance", "Impatience", "Infection", "Injury", "Iteration", "Jackpot", "LeadingStrike", "Leap",
        "LightningRod", "Loop", "Luminesce", "MachineLearning", "Metamorphosis", "MeteorStrike", "Modded",
        "MomentumStrike", "MultiCast", "NegativePulse", "NeowsFury", "Null", "Outbreak", "Outmaneuver", "Overclock",
        "PanicButton", "PiercingWail", "Poke", "Prolong", "Prowess", "Purity", "Quadcast", "Rainbow", "Reboot",
        "Rebound", "Refract", "Restlessness", "RocketPunch", "Salvo", "Scavenge", "Scrape", "Scrawl",
        "SecretTechnique", "SecretWeapon", "ShadowShield", "Shatter", "Shiv", "Shockwave", "Sidestep", "SignalBoost",
        "Skim", "Slimed", "Smokestack", "Spinner", "Storm", "StrikeDefect", "Subroutine", "SuckerPunch", "Sunder",
        "Supercritical", "SweepingBeam", "Synthesis", "Tempest", "TeslaCoil", "TheGambit", "ThrummingHatchet",
        "Thunder", "ToricToughness", "Toxic", "Turbo", "UltimateDefend", "UltimateStrike", "Uproar", "Void", "Volley",
        "Whistle", "WhiteNoise", "Wound", "Zap"
    };
}
