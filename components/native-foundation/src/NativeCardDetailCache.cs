using System;
using System.Collections;
using System.Collections.Generic;
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
/// two costly game text calls of one card render, the title and the pile (or upgrade
/// preview) description, keyed by a closed set of live public
/// inputs read AFTER the native preflight and preview update of that render: runtime type,
/// preview mode, display pile, combat/pile context, every scalar (and scalar collection)
/// field of the card, its live keywords, modified costs, and every dynamic variable's
/// scalar state including its preview value. Values are the game's own rendering results
/// from an earlier live render with an identical key; this class assembles no text.
/// Everything else (costs, target type, hover tips and hover cards, text projection, gaps)
/// is rendered live every time: hover tips may read state outside the card, e.g. an
/// enemy power's countdown. Combat modifiers (strength, dexterity, relics, counters) reach
/// the description only through variable preview values (in the key) or
/// AddExtraArgsToDescription (excluded); no card type overrides another description path.
///
/// Only card types frozen in <see cref="ShadowedTypes"/> are cached, and only on the game build
/// that list was measured on (<see cref="ShadowedGameMvid"/>): each had at least one shadow
/// compare and the whole shadow corpus had none differing. Any other type, or any other game
/// build, renders live until the shadow job is run again and the list is refrozen.
///
/// Not cached (always rendered live): types that override AddExtraArgsToDescription,
/// GetDescriptionForPile, GetDescriptionForUpgradePreview, HoverTips or Title; enchanted or afflicted cards; a card with a current
/// target; a variable with a non-scalar field or a capturing delegate; any incomplete render.
///
/// Reconcile: every 64th headless frame (every frame in shadow mode,
/// STS2_NATIVE_CARD_DETAIL_CACHE_SHADOW=1) renders everything live and compares it with the
/// stored entry byte for byte; a difference is compact_state_divergence, which is fatal.
/// </summary>
public static class NativeCardDetailCache
{
    private const int ReconcilePeriod = 64;
    private const int MaxEntries = 8192;

    public sealed record Entry(string? Title, string? Description);

    private static readonly bool Shadow =
        Environment.GetEnvironmentVariable("STS2_NATIVE_CARD_DETAIL_CACHE_SHADOW") == "1";
    private static readonly Dictionary<string, Entry> Entries = new(StringComparer.Ordinal);
    private static readonly Dictionary<Type, bool> CacheableTypes = new();
    private static readonly Dictionary<Type, (NativeField Field, FieldKind Kind)[]> ScalarFields = new();
    private static readonly Dictionary<string, long> TypeCompares = new(StringComparer.Ordinal);
    private static int _ownerThread;
    private static long _frame;

    static NativeCardDetailCache()
    {
        // Shadow evidence for refreezing ShadowedTypes: per-type live-vs-cache compares.
        if (Shadow)
            AppDomain.CurrentDomain.ProcessExit += (_, _) => Console.Error.WriteLine("[native-card-detail-cache] type-compares "
                + string.Join(',', TypeCompares.OrderBy(pair => pair.Key, StringComparer.Ordinal).Select(pair => pair.Key + "=" + pair.Value))
                + $" divergence={(Divergence == null ? 0 : 1)}");
    }

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

    /// <summary>The stored title and description for this key, outside reconcile frames.</summary>
    internal static Entry? TryServe(string? key)
    {
        if (key == null || Reconciling || !Entries.TryGetValue(key, out Entry? entry)) return null;
        Hits++;
        return entry;
    }

    /// <summary>After a complete live render: store its title and description, or compare them.</summary>
    internal static void Store(string? key, string? title, string? description)
    {
        if (key == null || !Active) return;
        if (Entries.TryGetValue(key, out Entry? entry))
        {
            Compares++;
            if (Shadow)
            {
                string type = key[..key.IndexOf('|')];
                TypeCompares[type] = TypeCompares.GetValueOrDefault(type) + 1;
            }
            if (!string.Equals(entry.Title, title, StringComparison.Ordinal)
                || !string.Equals(entry.Description, description, StringComparison.Ordinal))
            {
                Divergence = "compact_state_divergence: native card detail cache differs from live render for " + key;
                throw new NativeCompactStateDivergenceException(Divergence);
            }
            return;
        }
        if (Entries.Count >= MaxEntries) Entries.Clear();
        Entries[key] = new Entry(title, description);
        Stores++;
    }

    private static bool Cacheable(Type type)
    {
        if (CacheableTypes.TryGetValue(type, out bool value)) return value;
        // Shadow mode measures every structurally eligible type; otherwise only the frozen list.
        value = Shadow || typeof(CardModel).Module.ModuleVersionId == ShadowedGameMvid && type.Namespace == "MegaCrit.Sts2.Core.Models.Cards" && ShadowedTypes.Contains(type.Name);
        for (Type? t = type; t != null && t != typeof(CardModel); t = t.BaseType)
            foreach (string name in new[] { "AddExtraArgsToDescription", "GetDescriptionForPile",
                         "GetDescriptionForUpgradePreview", "get_HoverTips", "get_Title" })
                if (t.GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly)
                    .Any(method => method.Name == name)) value = false;
        CacheableTypes[type] = value;
        return value;
    }

    private static bool Scalar(Type type) => type.IsPrimitive || type.IsEnum || type == typeof(string) || type == typeof(decimal)
        || Nullable.GetUnderlyingType(type) is { } inner && Scalar(inner);

    private enum FieldKind { Scalar, Delegate, ScalarCollection, Other }

    /// <summary>A field type that AppendScalars keys completely (a scalar or a scalar collection).</summary>
    internal static bool ScalarOrScalarCollection(Type type) => Kind(type) is FieldKind.Scalar or FieldKind.ScalarCollection;

    // The field-type tests of AppendScalars, taken once per field instead of per read.
    private static FieldKind Kind(Type type) =>
        Scalar(type) ? FieldKind.Scalar
        : typeof(Delegate).IsAssignableFrom(type) ? FieldKind.Delegate
        : type.IsGenericType && type.GetGenericArguments() is [var item] && Scalar(item) ? FieldKind.ScalarCollection
        : FieldKind.Other;

    private static (NativeField Field, FieldKind Kind)[] Fields(Type type)
    {
        if (ScalarFields.TryGetValue(type, out (NativeField Field, FieldKind Kind)[]? fields)) return fields;
        var list = new List<FieldInfo>();
        for (Type? t = type; t != null && t != typeof(object); t = t.BaseType)
            list.AddRange(t.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly));
        fields = list.Select(field => (NativeField.Of(field)!, Kind(field.FieldType))).ToArray();
        ScalarFields[type] = fields;
        return fields;
    }

    // Scalars and scalar collections are key parts. Delegates must be non-capturing.
    // References to other models (owner, pool, clone links, vars set) are context, not text inputs.
    internal static bool AppendScalars(StringBuilder key, object value)
    {
        foreach ((NativeField field, FieldKind kind) in Fields(value.GetType()))
        {
            Type type = field.FieldType;
            if (kind == FieldKind.Scalar)
            {
                key.Append(';').Append(field.Name).Append('=').Append(field.GetValue(value));
                continue;
            }
            if (kind == FieldKind.Delegate)
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
            if (kind == FieldKind.ScalarCollection && field.GetValue(value) is IEnumerable items)
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

    // Frozen from Friday shadow job 1454482 (92 corpus games, 0 divergence, 255 types with >= 1 compare)
    // on this game build only. Refreeze after any game update. Names are in MegaCrit.Sts2.Core.Models.Cards.
    internal static readonly Guid ShadowedGameMvid = new("57785517-0b16-42b9-8b36-bad6fb28384b");
    private static readonly HashSet<string> ShadowedTypes = new(StringComparer.Ordinal)
    {
        "Abrasive", "Accuracy", "AdaptiveStrike", "Adrenaline", "Afterimage", "Alchemize", "AllForOne", "Anointed",
        "Anticipate", "Automation", "Backflip", "Backstab", "BallLightning", "Barrage", "BeamCell", "BeatDown",
        "Beckon", "BiasedCognition", "BlackHole", "BodySlam", "Bolas", "Bombardment", "BoostAway", "BootSequence",
        "BorrowedTime", "Breakthrough", "Buffer", "BulkUp", "Bulwark", "Burn", "Bury", "Calamity", "CallOfTheVoid",
        "Capacitor", "Catastrophe", "CelestialMight", "Chaos", "ChargeBattery", "ChildOfTheStars", "Chill", "Claw",
        "CloakAndDagger", "Clumsy", "ColdSnap", "CollisionCourse", "Comet", "Compact", "CompileDriver",
        "ConsumingShadow", "Coolant", "Coolheaded", "CreativeAi", "CrescentSpear", "CrushUnder", "CurseOfTheBell",
        "DaggerThrow", "DarkShackles", "Darkness", "Dash", "Dazed", "DeadlyPoison", "Debris", "Decay",
        "DecisionsDecisions", "DefendDefect", "Deflect", "Defragment", "Demesne", "Devastate", "Discovery",
        "DoubleEnergy", "Doubt", "DramaticEntrance", "DualWield", "Dualcast", "DyingStar", "EchoForm", "Enthralled",
        "Entropy", "Envenom", "Equilibrium", "EscapePlan", "EternalArmor", "Expose", "Exterminate", "FanOfKnives",
        "Fasten", "Feral", "FightThrough", "Finesse", "Fisticuffs", "FlakCannon", "FlashOfSteel", "Flatten",
        "Flechettes", "FocusedStrike", "FranticEscape", "Ftl", "Fuel", "Fusion", "Genesis", "GeneticAlgorithm",
        "Glacier", "Glasswork", "Glitterstream", "Glow", "GoForTheEyes", "GoldAxe", "Greed", "Guilty", "GunkUp",
        "Hailstorm", "HandOfGreed", "HandTrick", "Hang", "Haze", "HelixDrill", "HiddenGem", "HighFive", "Hologram",
        "Hotfix", "Hyperbeam", "IAmInvincible", "IceLance", "Impatience", "Infection", "Inferno", "Injury",
        "Iteration", "JackOfAllTrades", "Jackpot", "LeadingStrike", "Leap", "LightningRod", "Loop", "Luminesce",
        "MachineLearning", "MakeItSo", "MasterOfStrategy", "Mayhem", "Metamorphosis", "MeteorStrike", "MindBlast",
        "Modded", "MomentumStrike", "Monologue", "MultiCast", "Murder", "NegativePulse", "NeowsFury", "Nightmare",
        "Nostalgia", "NoxiousFumes", "Null", "Omnislice", "Outbreak", "Outmaneuver", "Overclock", "Pagestorm",
        "Panache", "PanicButton", "PhantomBlades", "PhotonCut", "PiercingWail", "Pinpoint", "Poke", "Pounce",
        "PrepTime", "Prepared", "Production", "Prolong", "Prophesize", "Prowess", "Purity", "Quadcast", "Radiate",
        "Rainbow", "ReaperForm", "Reboot", "Rebound", "Reflex", "Refract", "Rend", "Restlessness", "RocketPunch",
        "RollingBoulder", "Royalties", "Salvo", "Scavenge", "Scourge", "Scrape", "Scrawl", "SecretTechnique",
        "SecretWeapon", "SeekerStrike", "SeekingEdge", "SentryMode", "SerpentForm", "ShadowShield", "ShadowStep",
        "SharedFate", "Shatter", "Shiv", "Shockwave", "SicEm", "Sidestep", "SignalBoost", "Skim", "SleightOfFlesh",
        "Slimed", "Smokestack", "Snakebite", "Snap", "Soul", "SovereignBlade", "Spinner", "Splash", "SpoilsMap",
        "Storm", "Stratagem", "StrikeDefect", "Subroutine", "SuckerPunch", "SummonForth", "Sunder", "Supercritical",
        "SweepingBeam", "SweepingGaze", "Synchronize", "Synthesis", "Tempest", "Terraforming", "TeslaCoil", "TheBomb",
        "TheGambit", "TheHunt", "ThinkingAhead", "ThrummingHatchet", "Thunder", "ToricToughness", "Toxic",
        "TrashToTreasure", "Turbo", "UltimateDefend", "UltimateStrike", "UpMySleeve", "Uproar", "Void", "Volley",
        "Voltaic", "Whistle", "WhiteNoise", "Wound", "WroughtInWar", "Zap",
    };
}
