using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Enchantments;
using MegaCrit.Sts2.Core.Nodes.Cards;

namespace STS2Platform.NativeFoundation;

/// <summary>
/// One exact-game qualification owner for native model copying and presentation.
/// These checks precede native invocation. A transaction/finally is not a purity
/// proof. Rendering's operation-specific closure lives in the Rendering partial.
/// This class never executes a method selected by a consumer or interprets IL.
/// </summary>
public static partial class NativeCardPreviewReadQualification
{
    internal static readonly Guid AuditedGameMvid = new("57785517-0b16-42b9-8b36-bad6fb28384b");
    internal const BindingFlags NativeMembers = BindingFlags.Instance | BindingFlags.Static
        | BindingFlags.Public | BindingFlags.NonPublic;

    public static bool IsUnpatched(MethodBase? method)
    {
        try { return method != null && (Harmony.GetPatchInfo(method) is not { } patches || patches.Owners.Count == 0); }
        catch { return false; }
    }

    internal static bool MethodsUnpatched(Type owner, params string[] names) => names.All(name =>
    {
        MethodInfo[] exact = owner.GetMethods(NativeMembers).Where(method => method.Name == name).ToArray();
        return exact.Length > 0 && exact.All(IsUnpatched);
    });

    private static bool FixedConstructorsUnpatched(Type type) =>
        type.GetConstructors(NativeMembers).All(IsUnpatched);

    private static bool CloneLambdaUnpatched(Type owner)
    {
        MethodInfo[] exact = owner.GetNestedType("<>c", BindingFlags.NonPublic)?
            .GetMethods(NativeMembers).Where(method => method.Name.StartsWith("<Clone>b__", StringComparison.Ordinal)).ToArray()
            ?? Array.Empty<MethodInfo>();
        return exact.Length == 1 && IsUnpatched(exact[0]);
    }

    private static bool BaseImplementation(Type current, Type owner, string method) =>
        current.GetMethod(method, NativeMembers) is { } effective
        && effective.DeclaringType == owner && IsUnpatched(effective);

    private static readonly FieldInfo? Mutable = typeof(AbstractModel).GetField("<IsMutable>k__BackingField", NativeMembers);
    private static readonly FieldInfo? CardVars = typeof(CardModel).GetField("_dynamicVars", NativeMembers);
    private static readonly FieldInfo? Vars = typeof(DynamicVarSet).GetField("_vars", NativeMembers);
    private static readonly FieldInfo? VarName = typeof(DynamicVar).GetField("<Name>k__BackingField", NativeMembers);
    private static readonly FieldInfo? VarOwner = typeof(DynamicVar).GetField("_owner", NativeMembers);
    private static readonly FieldInfo? Keywords = typeof(CardModel).GetField("_keywords", NativeMembers);
    private static readonly FieldInfo? Enchantment = typeof(CardModel).GetField("<Enchantment>k__BackingField", NativeMembers);
    private static readonly FieldInfo? Affliction = typeof(CardModel).GetField("<Affliction>k__BackingField", NativeMembers);
    private static readonly FieldInfo? Canonical = typeof(CardModel).GetField("_canonicalInstance", NativeMembers);

    // The native clone only calls base Clone/ResetToBase then SetOwner. Unknown
    // SetOwner implementations (notably CalculatedVar) are rejected BEFORE they
    // can run. DynamicVars must already be initialized on the original: this is
    // the native mutable-card invariant, not permission to warm a live getter.
    private static bool CopyVariablesQualified(AbstractModel owner, DynamicVarSet set) =>
        set.GetType() == typeof(DynamicVarSet)
        && Vars?.GetValue(set) is Dictionary<string, DynamicVar> values
        && values.All(pair => pair.Value != null
            && pair.Value.GetType().Assembly == typeof(CardModel).Assembly
            && pair.Value is not CalculatedVar
            && VarName?.GetValue(pair.Value) is string name && pair.Key == name
            && ReferenceEquals(VarOwner?.GetValue(pair.Value), owner)
            && BaseImplementation(pair.Value.GetType(), typeof(DynamicVar), nameof(DynamicVar.SetOwner)))
        && values.Values.Distinct(ReferenceEqualityComparer.Instance).Count() == values.Count;



    private static bool CopyCostQualified(CardModel card)
    {
        object? value = typeof(CardModel).GetField("_energyCost", NativeMembers)?.GetValue(card);
        if (value == null) return true; // native getter initializes the NEW copy
        return value is CardEnergyCost cost
            && ReferenceEquals(typeof(CardEnergyCost).GetField("_card", NativeMembers)?.GetValue(cost), card)
            && typeof(CardEnergyCost).GetField("_localModifiers", NativeMembers)?.GetValue(cost) is List<LocalCostModifier> modifiers
            && modifiers.All(modifier => modifier != null && modifier.GetType() == typeof(LocalCostModifier));
    }

    // Only the exact native UI observers audited for this structural bridge.
    // They do not define clone model values, and are suppressed on the NEW
    // card before native DeepCloneFields. Unknown observers are never run.
    private static bool UiObserver(Delegate callback, string method, CardModel original) =>
        callback is Action && callback.Method == typeof(NCard).GetMethod(method, NativeMembers)
        && IsUnpatched(callback.Method) && callback.Target is NCard node && node.GetType() == typeof(NCard)
        && ReferenceEquals(typeof(NCard).GetField("_model", NativeMembers)?.GetValue(node), original);

    private static bool CopiedDelegatesQualified(CardModel card)
    {
        foreach (string name in new[] { "AfflictionChanged", "EnchantmentChanged", "EnergyCostChanged", "KeywordsChanged",
                     "ReplayCountChanged", "Played", "Drawn", "StarCostChanged", "Upgraded", "Forged" })
        {
            FieldInfo? field = typeof(CardModel).GetField(name, NativeMembers);
            if (field == null) return false;
            if (field.GetValue(card) is not Delegate callbacks) continue;
            string? observer = name switch
            { "AfflictionChanged" => "OnAfflictionChanged", "EnchantmentChanged" => "OnEnchantmentChanged", _ => null };
            if (observer == null || callbacks.GetInvocationList().Any(callback => !UiObserver(callback, observer, card))) return false;
        }
        return typeof(AbstractModel).GetField("ExecutionFinished", NativeMembers) is { } completed
            && completed.GetValue(card) == null;
    }

    private static bool AttachmentCopyQualified(CardModel card)
    {
        if (Enchantment == null || Affliction == null) return false;
        if (Enchantment.GetValue(card) is EnchantmentModel enchantment)
        {
            if (enchantment.GetType().Assembly != typeof(CardModel).Assembly || Mutable?.GetValue(enchantment) is not true
                || typeof(AbstractModel).GetField("ExecutionFinished", NativeMembers)?.GetValue(enchantment) != null
                || !BaseImplementation(enchantment.GetType(), typeof(EnchantmentModel), "DeepCloneFields")
                || !BaseImplementation(enchantment.GetType(), typeof(AbstractModel), "AfterCloned")
                || !ReferenceEquals(typeof(EnchantmentModel).GetField("_card", NativeMembers)?.GetValue(enchantment), card)
                || typeof(EnchantmentModel).GetField("_dynamicVars", NativeMembers)?.GetValue(enchantment) is not DynamicVarSet vars
                || !CopyVariablesQualified(enchantment, vars)
                || !MethodsUnpatched(typeof(AbstractModel), "ClonePreservingMutability")
                || !MethodsUnpatched(typeof(EnchantmentModel), "DeepCloneFields", "get_DynamicVars", "ApplyInternal",
                    "get_Card", "set_Card", "get_Amount", "set_Amount")
                || !MethodsUnpatched(typeof(CardModel), "EnchantInternal", "set_Enchantment")) return false;
            if (typeof(EnchantmentModel).GetField("StatusChanged", NativeMembers)?.GetValue(enchantment) is Delegate status
                && status.GetInvocationList().Any(callback => !UiObserver(callback, "OnEnchantmentStatusChanged", card))) return false;
        }
        if (Affliction.GetValue(card) is AfflictionModel affliction)
        {
            // Native AfterCloned clears _card and AmountChanged, retaining the
            // existing amount. AfflictInternal then writes the SAME amount;
            // the native recalculation/event branch is therefore not reached.
            if (affliction.GetType().Assembly != typeof(CardModel).Assembly || Mutable?.GetValue(affliction) is not true
                || typeof(AbstractModel).GetField("ExecutionFinished", NativeMembers)?.GetValue(affliction) != null
                || !BaseImplementation(affliction.GetType(), typeof(AbstractModel), "DeepCloneFields")
                || !BaseImplementation(affliction.GetType(), typeof(AfflictionModel), "AfterCloned")
                || !ReferenceEquals(typeof(AfflictionModel).GetField("_card", NativeMembers)?.GetValue(affliction), card)
                || typeof(AfflictionModel).GetField("AmountChanged", NativeMembers)?.GetValue(affliction) != null
                || !MethodsUnpatched(typeof(AbstractModel), "ClonePreservingMutability", "DeepCloneFields")
                || !MethodsUnpatched(typeof(AfflictionModel), "AfterCloned", "get_Card", "set_Card", "get_Amount", "set_Amount")
                || !MethodsUnpatched(typeof(CardModel), "AfflictInternal", "set_Affliction")) return false;
        }
        return true;
    }

    // All reachable native calls for the initialized/plain-copy branch. In
    // particular KeywordSources.Local short-circuits before combat hooks;
    // attachment branches have their own guard below; canonical lookup is excluded by
    // the existing canonical-instance binding. Framework collection/decimal
    // primitives are the trusted CLR boundary, not a native plugin callback.
    private static bool CopyClosureUnpatched() =>
        MethodsUnpatched(typeof(AbstractModel), "MutableClone", "get_IsMutable", "set_IsMutable", "AssertMutable", "AfterCloned")
        && MethodsUnpatched(typeof(CardModel), "DeepCloneFields", "AfterCloned", "GetKeywordsWithSources", "get_LocalKeywords",
            "get_DynamicVars", "get_Enchantment", "get_Affliction", "get_EnergyCost", "get_CanonicalEnergyCost", "get_HasEnergyCostX",
            "set_CurrentTarget", "set_CurrentPlayIndex", "set_DeckVersion", "set_HasBeenRemovedFromState")
        && MethodsUnpatched(typeof(DynamicVarSet), "Clone", "get_Values", "InitializeWithOwner", "RecalculateForUpgradeOrEnchant")
        && FixedConstructorsUnpatched(typeof(DynamicVarSet)) && CloneLambdaUnpatched(typeof(DynamicVarSet))
        && MethodsUnpatched(typeof(DynamicVar), "Clone", "ResetToBase", "SetOwner", "get_Name", "get_BaseValue", "set_BaseValue",
            "get_EnchantedValue", "set_EnchantedValue", "get_PreviewValue", "set_PreviewValue")
        && MethodsUnpatched(typeof(CardEnergyCost), "Clone", "get_Canonical", "get_CostsX", "get_WasJustUpgraded", "set_WasJustUpgraded")
        && FixedConstructorsUnpatched(typeof(CardEnergyCost)) && CloneLambdaUnpatched(typeof(CardEnergyCost))
        && MethodsUnpatched(typeof(LocalCostModifier), "Clone", "get_Amount", "get_Type", "get_Expiration", "get_IsReduceOnly")
        && FixedConstructorsUnpatched(typeof(LocalCostModifier));

    internal static bool TryPreflightCopy(CardModel card, out string? gap)
    {
        gap = "native_preview_copy_closure_unqualified";
        try
        {
            if (typeof(CardModel).Assembly.ManifestModule.ModuleVersionId != AuditedGameMvid
                || card.GetType().Assembly != typeof(CardModel).Assembly
                || card.GetType().Namespace != "MegaCrit.Sts2.Core.Models.Cards"
                || Mutable?.GetValue(card) is not true || !CopyClosureUnpatched()
                || !BaseImplementation(card.GetType(), typeof(CardModel), "DeepCloneFields")
                || !BaseImplementation(card.GetType(), typeof(CardModel), "AfterCloned")
                || !BaseImplementation(card.GetType(), typeof(CardModel), "get_CanonicalEnergyCost")
                || !BaseImplementation(card.GetType(), typeof(CardModel), "get_HasEnergyCostX")) return false;
            if (!CopiedDelegatesQualified(card) || !AttachmentCopyQualified(card))
            { gap = "native_preview_existing_attachment_copy_callbacks_unqualified"; return false; }
            if (Canonical?.GetValue(card) is not CardModel canonical || canonical.GetType() != card.GetType()
                || Mutable.GetValue(canonical) is not false || Keywords?.GetValue(card) is not HashSet<CardKeyword>
                || CardVars?.GetValue(card) is not DynamicVarSet vars || !CopyVariablesQualified(card, vars) || !CopyCostQualified(card))
            { gap = "native_preview_initialized_card_copy_facts_unqualified"; return false; }
            gap = null;
            return true;
        }
        catch { return false; }
    }

    internal static bool TryPreflightUpgrade(CardModel card, out string? gap)
    {
        if (!TryPreflightCopy(card, out gap)) return false;
        gap = "native_preview_upgrade_closure_unqualified";
        try
        {
            // Explicit exact-build method inventory; it contains no costs,
            // damage values or other reimplementation of native upgrade rules.
            if (card.GetType().GetMethod("OnUpgrade", NativeMembers) is not { } upgrade
                || upgrade.DeclaringType != card.GetType() || !NativeUpgradeMethodInventory.Contains(card.GetType().Name)
                || !IsUnpatched(upgrade)
                || !BaseImplementation(card.GetType(), typeof(CardModel), "get_MaxUpgradeLevel")
                || !MethodsUnpatched(typeof(CardModel), "UpgradeInternal", "get_IsUpgradable", "get_CurrentUpgradeLevel",
                    "set_CurrentUpgradeLevel", "get_MaxUpgradeLevel", "AddKeyword", "RemoveKeyword", "InvokeEnergyCostChanged",
                    "get_UpgradePreviewType", "set_UpgradePreviewType")
                || !MethodsUnpatched(typeof(DynamicVar), "UpgradeValueBy", "get_WasJustUpgraded", "set_WasJustUpgraded")
                || !NativeUpgradeMethodInventory.VariableGetters.All(name => MethodsUnpatched(typeof(DynamicVarSet), name))
                || !MethodsUnpatched(typeof(CardEnergyCost), "UpgradeBy", "SetCustomBaseCost")
                || !MethodsUnpatched(typeof(LocalCostModifier), "set_Amount")) return false;
            if (!card.IsUpgradable) { gap = "native_preview_card_not_upgradable"; return false; }
            gap = null;
            return true;
        }
        catch { return false; }
    }

    internal static bool TryPreflightSingleEnchant(CardModel card, EnchantmentModel canonical, out string? gap)
    {
        if (!TryPreflightCopy(card, out gap)) return false;
        gap = "native_preview_enchantment_closure_unqualified";
        try
        {
            if (canonical.GetType() != typeof(Adroit) && canonical.GetType() != typeof(Sharp)
                || Mutable?.GetValue(canonical) is not false
                || !MethodsUnpatched(typeof(AbstractModel), "AssertCanonical")
                || !MethodsUnpatched(typeof(EnchantmentModel), "ToMutable", "DeepCloneFields", "get_DynamicVars", "get_CanonicalVars",
                    "set_CanonicalInstance", "ApplyInternal", "get_Card", "set_Card", "get_Amount", "set_Amount",
                    "ModifyCard", "OnEnchant", "RecalculateValues")
                || !MethodsUnpatched(typeof(CardModel), "EnchantInternal", "set_Enchantment", "set_IsEnchantmentPreview")
                || !BaseImplementation(canonical.GetType(), typeof(EnchantmentModel), "DeepCloneFields")
                || !BaseImplementation(canonical.GetType(), typeof(AbstractModel), "AfterCloned")
                || !BaseImplementation(canonical.GetType(), typeof(EnchantmentModel), "OnEnchant")
                || !IsUnpatched(canonical.GetType().GetMethod("RecalculateValues", NativeMembers))
                || typeof(AbstractModel).GetField("ExecutionFinished", NativeMembers)?.GetValue(canonical) != null
                || typeof(EnchantmentModel).GetField("StatusChanged", NativeMembers)?.GetValue(canonical) != null) return false;
            if (typeof(EnchantmentModel).GetField("_dynamicVars", NativeMembers)?.GetValue(canonical) is DynamicVarSet existing
                && !CopyVariablesQualified(canonical, existing)) return false;
            if (canonical.GetType() == typeof(Adroit)
                && (!MethodsUnpatched(typeof(Adroit), "get_CanonicalVars", "RecalculateValues")
                    || !MethodsUnpatched(typeof(DynamicVarSet), "get_Block")
                    || !FixedConstructorsUnpatched(typeof(DynamicVar)) || !FixedConstructorsUnpatched(typeof(BlockVar))
                    || !SingletonVariableListUnpatched())) return false;
            gap = null;
            return true;
        }
        catch { return false; }
    }

    private static bool SingletonVariableListUnpatched()
    {
        Type? fixedType = typeof(CardModel).Assembly.GetType("<>z__ReadOnlySingleElementList`1")?.MakeGenericType(typeof(DynamicVar));
        return fixedType != null && fixedType.GetMethods(NativeMembers).All(IsUnpatched)
            && FixedConstructorsUnpatched(fixedType)
            && fixedType.GetNestedTypes(NativeMembers).All(type => type.GetMethods(NativeMembers).All(IsUnpatched)
                && FixedConstructorsUnpatched(type));
    }
}
