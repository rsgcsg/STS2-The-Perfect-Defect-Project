using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using HarmonyLib;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Enchantments;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Models.Powers;
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

    private static readonly ConditionalWeakTable<Type, ILookup<string, MethodInfo>> MethodTables = new();

    internal static bool MethodsUnpatched(Type owner, params string[] names)
    {
        // CLR method metadata is immutable for each loaded Type. Reuse only
        // that table; every requested overload's Harmony state remains live.
        var methods = MethodTables.GetValue(owner, static type =>
            type.GetMethods(NativeMembers).ToLookup(method => method.Name, StringComparer.Ordinal));
        return names.All(name => methods[name].Any() && methods[name].All(IsUnpatched));
    }

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
    // SetOwner implementations are rejected BEFORE they can run. The exact
    // CalculatedVar SetOwner/UpdateValues branch only reads the base variable;
    // multiplier invocation is separately guarded by the rendering operation. DynamicVars must already be initialized on the original: this is
    // the native mutable-card invariant, not permission to warm a live getter.
    private static bool CopyVariablesQualified(AbstractModel owner, DynamicVarSet set) =>
        set.GetType() == typeof(DynamicVarSet)
        && Vars?.GetValue(set) is Dictionary<string, DynamicVar> values && values.GetType() == typeof(Dictionary<string, DynamicVar>)
        && ReferenceEquals(values.Comparer, EqualityComparer<string>.Default)
        && values.All(pair => pair.Value != null
            && pair.Value.GetType().Assembly == typeof(CardModel).Assembly
            && VarName?.GetValue(pair.Value) is string name && pair.Key == name
            && ReferenceEquals(VarOwner?.GetValue(pair.Value), owner)
            && (pair.Value is CalculatedVar calculated
                ? owner is CardModel card && CalculatedVariableMetadataQualified(card, calculated)
                    && values.ContainsKey("CalculationBase")
                    && values.ContainsKey(calculated.GetType() == typeof(CalculatedDamageVar) ? "ExtraDamage" : "CalculationExtra")
                : BaseImplementation(pair.Value.GetType(), typeof(DynamicVar), nameof(DynamicVar.SetOwner))))
        && values.Values.Distinct(ReferenceEqualityComparer.Instance).Count() == values.Count;




    internal static bool CalculatedVariableMetadataQualified(CardModel owner, CalculatedVar value)
    {
        Type type = value.GetType();
        if (type != typeof(CalculatedVar) && type != typeof(CalculatedDamageVar) && type != typeof(CalculatedBlockVar)) return false;
        if (!BaseImplementation(type, typeof(CalculatedVar), "SetOwner")
            || !BaseImplementation(type, typeof(CalculatedVar), "GetBaseVar")
            || !BaseImplementation(type, type == typeof(CalculatedDamageVar) ? typeof(CalculatedDamageVar) : typeof(CalculatedVar), "GetExtraVar")
            || !MethodsUnpatched(typeof(CalculatedVar), "SetOwner", "UpdateValues", "GetBaseVar", "GetExtraVar", "RecalculateForUpgradeOrEnchant", "Calculate")
            || !MethodsUnpatched(typeof(DynamicVarSet), "get_CalculationBase", "get_CalculationExtra", "get_ExtraDamage")) return false;
        object? stored = typeof(CalculatedVar).GetField("_multiplierCalc", NativeMembers)?.GetValue(value);
        if (stored is not Delegate multiplier || multiplier.GetInvocationList().Length != 1
            || multiplier.Method.Module.ModuleVersionId != AuditedGameMvid
            || multiplier.Method.DeclaringType?.DeclaringType != owner.GetType()
            || !NativeCalculatedVariableInventory.Multipliers.Contains(multiplier.Method.DeclaringType!.FullName + "::" + multiplier.Method.Name)
            || !IsUnpatched(multiplier.Method)) return false;
        return multiplier.Method.IsStatic ? multiplier.Target == null : multiplier.Target?.GetType() == multiplier.Method.DeclaringType;
    }

    private static bool CopyCostQualified(CardModel card)
    {
        object? value = typeof(CardModel).GetField("_energyCost", NativeMembers)?.GetValue(card);
        if (value == null) return true; // native getter initializes the NEW copy
        return value is CardEnergyCost cost
            && ReferenceEquals(typeof(CardEnergyCost).GetField("_card", NativeMembers)?.GetValue(cost), card)
            && typeof(CardEnergyCost).GetField("_localModifiers", NativeMembers)?.GetValue(cost) is List<LocalCostModifier> modifiers
            && modifiers.GetType() == typeof(List<LocalCostModifier>) && modifiers.All(modifier => modifier != null && modifier.GetType() == typeof(LocalCostModifier));
    }

    // Only the exact native UI observers audited for this structural bridge.
    // They do not define clone model values, and are suppressed on the NEW
    // card before native DeepCloneFields. Unknown observers are never run.
    private static bool UiObserver(Delegate callback, string method, CardModel original) =>
        callback is Action && callback.Method == typeof(NCard).GetMethod(method, NativeMembers)
        && IsUnpatched(callback.Method) && callback.Target is NCard node && node.GetType() == typeof(NCard)
        && ReferenceEquals(NativeDetachedCardCopy.UiModel(node), original);

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
            "get_EnchantedValue", "set_EnchantedValue", "get_PreviewValue", "set_PreviewValue", "get_WasJustUpgraded", "set_WasJustUpgraded")
        && MethodsUnpatched(typeof(CardEnergyCost), "Clone", "get_Canonical", "get_CostsX", "get_WasJustUpgraded", "set_WasJustUpgraded")
        && FixedConstructorsUnpatched(typeof(CardEnergyCost)) && CloneLambdaUnpatched(typeof(CardEnergyCost))
        && MethodsUnpatched(typeof(LocalCostModifier), "Clone", "get_Amount", "set_Amount", "get_Type", "get_Expiration", "get_IsReduceOnly")
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
                // Native X-cost cards override this display fact. Reuse the
                // exact-game effective-getter qualification used by rendering.
                || !NativeRenderMethod(card.GetType().GetMethod("get_HasEnergyCostX", NativeMembers))) return false;
            if (!AttachmentCopyQualified(card))
            { gap = "native_preview_existing_attachment_copy_callbacks_unqualified"; return false; }
            if (Canonical?.GetValue(card) is not CardModel canonical || canonical.GetType() != card.GetType()
                || Mutable.GetValue(canonical) is not false || Keywords?.GetValue(card) is not HashSet<CardKeyword> keywords
                || keywords.GetType() != typeof(HashSet<CardKeyword>) || !ReferenceEquals(keywords.Comparer, EqualityComparer<CardKeyword>.Default)
                || typeof(CardModel).GetField("_temporaryStarCosts", NativeMembers)?.GetValue(card) is not List<TemporaryCardCost> temporary
                || temporary.GetType() != typeof(List<TemporaryCardCost>)
                || CardVars?.GetValue(card) is not DynamicVarSet vars || !CopyVariablesQualified(card, vars) || !CopyCostQualified(card))
            { gap = "native_preview_initialized_card_copy_facts_unqualified"; return false; }
            gap = null;
            return true;
        }
        catch { return false; }
    }

    internal static bool TryPreflightCanonicalHover(CardModel canonical, out string? gap)
    {
        gap = "native_canonical_hover_copy_closure_unqualified";
        try
        {
            if (typeof(CardModel).Module.ModuleVersionId != AuditedGameMvid || canonical.GetType() != typeof(Shiv)
                || Mutable?.GetValue(canonical) is not false || !CopyClosureUnpatched()
                || !BaseImplementation(canonical.GetType(), typeof(CardModel), "DeepCloneFields")
                || !BaseImplementation(canonical.GetType(), typeof(CardModel), "AfterCloned")
                || !MethodsUnpatched(typeof(CardModel), "get_CanonicalKeywords")
                || !MethodsUnpatched(typeof(Shiv), "get_CanonicalVars", "get_CanonicalKeywords")
                || !FixedConstructorsUnpatched(typeof(DynamicVar)) || !FixedConstructorsUnpatched(typeof(DamageVar))
                || !MethodsUnpatched(typeof(DamageVar), "set_Props")
                || !SingletonVariableListUnpatched() || !SingletonListUnpatched(typeof(CardKeyword))
                || Enchantment?.GetValue(canonical) != null || Affliction?.GetValue(canonical) != null
                || !CopyCostQualified(canonical)) return false;
            if (CardVars?.GetValue(canonical) is DynamicVarSet initialized && !CopyVariablesQualified(canonical, initialized)) return false;
            if (Keywords?.GetValue(canonical) is { } keywords
                && (keywords is not HashSet<CardKeyword> set || set.GetType() != typeof(HashSet<CardKeyword>)
                    || !ReferenceEquals(set.Comparer, EqualityComparer<CardKeyword>.Default))) return false;
            if (typeof(CardModel).GetField("_temporaryStarCosts", NativeMembers)?.GetValue(canonical) is not List<TemporaryCardCost> temporary
                || temporary.GetType() != typeof(List<TemporaryCardCost>)) return false;
            if (Canonical?.GetValue(canonical) is { } parent && !ReferenceEquals(parent, canonical)) return false;
            if (!CanonicalRegistryBindingQualified(canonical))
            { gap = "native_canonical_hover_registry_binding_unqualified"; return false; }
            gap = null;
            return true;
        }
        catch { return false; }
    }

    private static bool CanonicalRegistryBindingQualified(CardModel canonical)
    {
        if (!MethodsUnpatched(typeof(AbstractModel), "get_Id")
            || !MethodsUnpatched(typeof(ModelDb), "GetById", "GetByIdOrNull")
            || !MethodsUnpatched(typeof(ModelId), "GetHashCode", "Equals", "get_EqualityContract", "get_Category", "get_Entry")
            || !IsUnpatched(typeof(ModelDb).GetMethod("GetById", NativeMembers)?.MakeGenericMethod(typeof(CardModel)))
            || !IsUnpatched(typeof(ModelDb).GetMethod("GetByIdOrNull", NativeMembers)?.MakeGenericMethod(typeof(CardModel)))) return false;
        // No arbitrary dictionary comparer or derived ModelId equality can run
        // during this lookup. Enumeration of these definition keys invokes no
        // model methods and exposes no catalog/world data to the consumer.
        if (typeof(ModelDb).GetField("_contentById", NativeMembers)?.GetValue(null) is not Dictionary<ModelId, AbstractModel> registry
            || registry.GetType() != typeof(Dictionary<ModelId, AbstractModel>)
            || !ReferenceEquals(registry.Comparer, EqualityComparer<ModelId>.Default)
            || registry.Keys.Any(key => key is null || key.GetType() != typeof(ModelId))) return false;
        ModelId id = canonical.Id;
        return id.GetType() == typeof(ModelId) && registry.TryGetValue(id, out AbstractModel? current)
            && ReferenceEquals(current, canonical);
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
                || !(upgrade.DeclaringType == typeof(CardModel)
                    || upgrade.DeclaringType == card.GetType() && NativeUpgradeMethodInventory.Contains(card.GetType().Name))
                // Harmony keys inherited methods by their actual declaration.
                || !IsUnpatched(upgrade.DeclaringType?.GetMethod("OnUpgrade", NativeMembers | BindingFlags.DeclaredOnly))
                // Reuse the render qualification for the effective native getter;
                // native curses/status cards override the limit with zero.
                || !NativeRenderMethod(card.GetType().GetMethod("get_MaxUpgradeLevel", NativeMembers))
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
            Type type = canonical.GetType();
            if (type.Assembly != typeof(CardModel).Assembly || type.Namespace != "MegaCrit.Sts2.Core.Models.Enchantments"
                || !NativeEnchantmentMethodInventory.Types.Contains(type.Name) || Mutable?.GetValue(canonical) is not false
                || !MethodsUnpatched(typeof(AbstractModel), "AssertCanonical")
                || !MethodsUnpatched(typeof(EnchantmentModel), "ToMutable", "DeepCloneFields", "get_DynamicVars", "get_CanonicalVars",
                    "set_CanonicalInstance", "ApplyInternal", "get_Card", "set_Card", "get_Amount", "set_Amount",
                    "ModifyCard", "OnEnchant", "RecalculateValues")
                || !MethodsUnpatched(typeof(CardModel), "EnchantInternal", "set_Enchantment", "set_IsEnchantmentPreview", "AddKeyword", "RemoveKeyword")
                || !BaseImplementation(type, typeof(EnchantmentModel), "DeepCloneFields")
                || !BaseImplementation(type, typeof(AbstractModel), "AfterCloned")
                || typeof(AbstractModel).GetField("ExecutionFinished", NativeMembers)?.GetValue(canonical) != null
                || typeof(EnchantmentModel).GetField("StatusChanged", NativeMembers)?.GetValue(canonical) != null) return false;
            if (typeof(EnchantmentModel).GetField("_dynamicVars", NativeMembers)?.GetValue(canonical) is DynamicVarSet existing
                && !CopyVariablesQualified(canonical, existing)) return false;
            MethodInfo? onEnchant = type.GetMethod("OnEnchant", NativeMembers);
            if (!IsUnpatched(onEnchant)) return false;
            if (onEnchant!.DeclaringType != typeof(EnchantmentModel)
                && !NativeEnchantmentMethodInventory.KeywordUpdates.Contains(type.Name)
                && type != typeof(TezcatarasEmber))
            {
                // SoulsPower invokes CardCmd.RemoveKeyword -> FindOnTable,
                // rather than the model-only primitive. Its native UI lookup
                // closure is an explicit remaining row, never executed here.
                gap = "native_preview_enchantment_command_ui_lookup_unqualified";
                return false;
            }
            MethodInfo? recalculate = type.GetMethod("RecalculateValues", NativeMembers);
            if (!IsUnpatched(recalculate) || recalculate!.DeclaringType != typeof(EnchantmentModel) && type != typeof(Adroit)) return false;
            MethodInfo? factory = type.GetMethod("get_CanonicalVars", NativeMembers);
            if (!IsUnpatched(factory) || factory!.DeclaringType != typeof(EnchantmentModel)
                && !NativeEnchantmentMethodInventory.VariableFactories.Contains(type.Name)) return false;
            if (factory.DeclaringType != typeof(EnchantmentModel)
                && (!FixedConstructorsUnpatched(typeof(DynamicVar)) || !FixedConstructorsUnpatched(typeof(BlockVar))
                    || !FixedConstructorsUnpatched(typeof(DamageVar)) || !MethodsUnpatched(typeof(DamageVar), "set_Props") || !FixedConstructorsUnpatched(typeof(IntVar))
                    || !FixedConstructorsUnpatched(typeof(PowerVar<WeakPower>)) || !SingletonVariableListUnpatched())) return false;
            if (type == typeof(Adroit) && !MethodsUnpatched(typeof(DynamicVarSet), "get_Block")) return false;
            if (type == typeof(TezcatarasEmber)
                && (!MethodsUnpatched(typeof(AbstractModel), "get_IsCanonical")
                    || !MethodsUnpatched(typeof(CardEnergyCost), "GetWithModifiers", "UpgradeBy", "SetCustomBaseCost")
                    || !MethodsUnpatched(typeof(CardModel), "InvokeEnergyCostChanged")
                    || !MethodsUnpatched(typeof(LocalCostModifier), "set_Amount"))) return false;
            gap = null;
            return true;
        }
        catch { return false; }
    }

    private static bool SingletonVariableListUnpatched() => SingletonListUnpatched(typeof(DynamicVar));

    private static bool SingletonListUnpatched(Type element)
    {
        Type? fixedType = typeof(CardModel).Assembly.GetType("<>z__ReadOnlySingleElementList`1")?.MakeGenericType(element);
        return fixedType != null && fixedType.GetMethods(NativeMembers).All(IsUnpatched)
            && FixedConstructorsUnpatched(fixedType)
            && fixedType.GetNestedTypes(NativeMembers).All(type => type.GetMethods(NativeMembers).All(IsUnpatched)
                && FixedConstructorsUnpatched(type));
    }
}
