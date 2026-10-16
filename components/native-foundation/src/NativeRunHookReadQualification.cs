using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using HarmonyLib;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Modding;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Runs;

namespace STS2Platform.NativeFoundation;

/// <summary>
/// Shared bounded read qualification for the exact native run-listener iterator.
/// Operation-specific native override qualification remains with its provider.
/// Mod subscribers are rejected BEFORE enumeration can invoke their delegates.
/// </summary>
public static class NativeRunHookReadQualification
{
    private static readonly Guid AuditedGameMvid = new("57785517-0b16-42b9-8b36-bad6fb28384b");
    private static readonly FieldInfo? Subscribers = typeof(ModHelper).GetField("_runHookSubscribers",
        BindingFlags.Static | BindingFlags.NonPublic);
    public static bool IsUnpatched(MethodBase? method)
    {
        try { return method != null && (Harmony.GetPatchInfo(method) is not { } patches || patches.Owners.Count == 0); }
        catch { return false; }
    }

    private static bool IteratorIsUnpatched(MethodInfo? method)
    {
        Type? stateMachine = method?.GetCustomAttribute<IteratorStateMachineAttribute>()?.StateMachineType;
        return IsUnpatched(method) && stateMachine != null
            && IsUnpatched(stateMachine.GetMethod("MoveNext", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic));
    }

    private static bool GetterIsUnpatched(Type owner, string property) =>
        IsUnpatched(owner.GetProperty(property, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)?.GetMethod);

    private static bool NativeLambdaIsUnpatched(Type owner, string sourceMethod)
    {
        MethodInfo[] methods = owner.GetNestedType("<>c", BindingFlags.NonPublic)?
            .GetMethods(BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic)
            .Where(method => method.Name.StartsWith("<" + sourceMethod + ">b__", StringComparison.Ordinal)).ToArray()
            ?? Array.Empty<MethodInfo>();
        return methods.Length == 1 && IsUnpatched(methods[0]);
    }

    // Exact direct game-call closure of the audited iterator and Contains.
    // All checks happen before its first MoveNext, including deferred LINQ
    // predicates and mutable-owner assertions reached by native membership.
    private static bool IteratorReadClosureIsUnpatched() =>
        new[] { "Players", "Modifiers", "BadgeModels", "MultiplayerScalingModel" }
            .All(name => GetterIsUnpatched(typeof(RunState), name))
        && new[] { "IsActiveForHooks", "Deck", "Relics", "Potions" }
            .All(name => GetterIsUnpatched(typeof(Player), name))
        && GetterIsUnpatched(typeof(CardPile), "Cards")
        && new[] { "Enchantment", "HasBeenRemovedFromState", "Owner" }
            .All(name => GetterIsUnpatched(typeof(CardModel), name))
        && new[] { "IsMelted", "HasBeenRemovedFromState", "Owner" }
            .All(name => GetterIsUnpatched(typeof(RelicModel), name))
        && new[] { "HasBeenRemovedFromState", "Owner" }
            .All(name => GetterIsUnpatched(typeof(PotionModel), name))
        && new[] { "HasCard", "Card" }.All(name => GetterIsUnpatched(typeof(EnchantmentModel), name))
        && IsUnpatched(typeof(AbstractModel).GetMethod(nameof(AbstractModel.AssertMutable)))
        && GetterIsUnpatched(typeof(AbstractModel), "IsMutable")
        && NativeLambdaIsUnpatched(typeof(RunState), "IterateHookListeners")
        && NativeLambdaIsUnpatched(typeof(Player), "get_Potions");

    public static bool Preflight(IRunState run)
    {
        try
        {
            return run.GetType() == typeof(RunState)
                && typeof(RunState).Assembly.ManifestModule.ModuleVersionId == AuditedGameMvid
                && Subscribers?.GetValue(null) is ICollection { Count: 0 }
                && IteratorReadClosureIsUnpatched()
                && IteratorIsUnpatched(typeof(RunState).GetMethod(nameof(RunState.IterateHookListeners), new[] { typeof(ICombatState) }))
                && IteratorIsUnpatched(typeof(ModHelper).GetMethod(nameof(ModHelper.IterateAllRunStateSubscribers)))
                && IsUnpatched(typeof(RunState).GetMethod("Contains", BindingFlags.Static | BindingFlags.NonPublic));
        }
        catch { return false; }
    }

    public static bool TryCaptureListeners(IRunState run, out IReadOnlyList<AbstractModel> listeners, out string? gap)
    {
        listeners = Array.Empty<AbstractModel>();
        gap = "native_run_hook_iterator_not_pure_qualified";
        try
        {
            if (!Preflight(run)) return false;
            AbstractModel[] current = run.IterateHookListeners(null).ToArray();
            if (!Preflight(run) || current.Any(model => model == null || model.GetType().Assembly != typeof(RunState).Assembly))
                return false;
            listeners = current;
            gap = null;
            return true;
        }
        catch { return false; }
    }

    public static bool StillCurrent(IRunState run, IReadOnlyList<AbstractModel> listeners) =>
        TryCaptureListeners(run, out var current, out _) && listeners.SequenceEqual(current, ReferenceEqualityComparer.Instance);
}
