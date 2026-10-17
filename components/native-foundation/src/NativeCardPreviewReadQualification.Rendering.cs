using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MethodInfo = System.Reflection.MethodInfo;
using PropertyInfo = System.Reflection.PropertyInfo;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Orbs;
using MegaCrit.Sts2.Core.Models.Powers;
using MegaCrit.Sts2.Core.Models.Relics;
using MegaCrit.Sts2.Core.ValueProps;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Hooks;
using MegaCrit.Sts2.Core.HoverTips;
using MegaCrit.Sts2.Core.Localization;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Modding;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Runs;

namespace STS2Platform.NativeFoundation;

public static partial class NativeCardPreviewReadQualification
{
    private static bool RenderMethodsUnpatched(Type type) => type.GetMethods(NativeMembers | BindingFlags.DeclaredOnly)
        .All(IsUnpatched) && type.GetConstructors(NativeMembers).All(IsUnpatched);
    private static bool RenderIteratorUnpatched(MethodInfo? method)
    {
        if (!IsUnpatched(method)) return false;
        Type? iterator = method!.GetCustomAttribute<IteratorStateMachineAttribute>()?.StateMachineType;
        return iterator == null || RenderMethodsUnpatched(iterator);
    }
    private static bool RenderGettersUnpatched(Type type, params string[] names) => names.All(name =>
        IsUnpatched(type.GetProperty(name, NativeMembers)?.GetMethod));
    private static bool NativeRenderMethod(MethodInfo? method) => method != null
        && method.Module == typeof(CardModel).Module && RenderIteratorUnpatched(method);

    private static bool RenderCardDispatchQualified(CardModel card)
    {
        Type type = card.GetType();
        if (type.Module != typeof(CardModel).Module || type.Namespace != "MegaCrit.Sts2.Core.Models.Cards") return false;
        // The original game's overrides are its display semantics. Check the
        // effective methods, not a small card list or a constant-IL approximation.
        return new[] { "get_Title", "get_CanonicalEnergyCost", "get_HasEnergyCostX", "get_CurrentStarCost",
            "get_TargetType", "get_Pool", "AddExtraArgsToDescription", "get_CanonicalStarCost", "get_HasStarCostX",
            "get_MaxUpgradeLevel", "get_GainsBlock", "get_OrbEvokeType", "get_ExtraHoverTips" }
            .All(name => NativeRenderMethod(type.GetMethod(name, NativeMembers)));
    }

    private static bool RenderMultiplierQualified(CardModel owner, Delegate multiplier) =>
        multiplier.GetInvocationList().Length == 1 && multiplier.Method.Module == typeof(CardModel).Module
        && multiplier.Method.DeclaringType?.DeclaringType == owner.GetType() && IsUnpatched(multiplier.Method)
        && (multiplier.Method.IsStatic ? multiplier.Target == null : multiplier.Target?.GetType() == multiplier.Method.DeclaringType);

    private static bool RenderVariablesQualified(AbstractModel owner, DynamicVarSet? set)
    {
        if (set == null || set.GetType() != typeof(DynamicVarSet)
            || Vars?.GetValue(set) is not Dictionary<string, DynamicVar> values
            || values.GetType() != typeof(Dictionary<string, DynamicVar>)
            || !ReferenceEquals(values.Comparer, EqualityComparer<string>.Default)
            || values.Values.Distinct(ReferenceEqualityComparer.Instance).Count() != values.Count) return false;
        foreach (var pair in values)
        {
            DynamicVar value = pair.Value;
            Type type = value.GetType();
            if (type.Module != typeof(CardModel).Module || type.Namespace != typeof(DynamicVar).Namespace
                || type.IsGenericType && type.GetGenericArguments().Any(argument => argument.Module != typeof(CardModel).Module)
                || !ReferenceEquals(VarOwner?.GetValue(value), owner)
                || !Equals(VarName?.GetValue(value), pair.Key)) return false;
            // A native variable can hold a foreign delegate. Admit the game's
            // own multiplier, but never execute an injected callback to test it.
            for (Type? current = type; current != null && typeof(DynamicVar).IsAssignableFrom(current); current = current.BaseType)
            {
                if (!RenderMethodsUnpatched(current)) return false;
                foreach (FieldInfo field in current.GetFields(NativeMembers | BindingFlags.DeclaredOnly))
                    if (typeof(Delegate).IsAssignableFrom(field.FieldType) && field.GetValue(value) is Delegate callback
                        && !(current == typeof(CalculatedVar) && field.Name == "_multiplierCalc"
                            && owner is CardModel card && RenderMultiplierQualified(card, callback))) return false;
            }
        }
        return true;
    }
    private static bool RenderAttachmentQualified(CardModel card)
    {
        if (Affliction?.GetValue(card) is AfflictionModel affliction
            && (affliction.GetType().Module != typeof(CardModel).Module || !RenderMethodsUnpatched(affliction.GetType()))) return false;
        if (Enchantment?.GetValue(card) is not EnchantmentModel enchantment) return true;
        return enchantment.GetType().Module == typeof(CardModel).Module
            && RenderMethodsUnpatched(enchantment.GetType())
            && ReferenceEquals(typeof(EnchantmentModel).GetField("_card", NativeMembers)?.GetValue(enchantment), card)
            && RenderVariablesQualified(enchantment,
                typeof(EnchantmentModel).GetField("_dynamicVars", NativeMembers)?.GetValue(enchantment) as DynamicVarSet);
    }
    private static bool RenderPoolValueQualified(CardPoolModel pool) =>
        pool.GetType().Module == typeof(CardModel).Module
        && NativeRenderMethod(pool.GetType().GetProperty("EnergyColorName", NativeMembers)?.GetMethod);

    private static bool RenderCurrentPoolsQualified(CardModel card)
    {
        // A null cache is the original native lazy Pool lookup, not a foreign
        // callback. Do not warm it in preflight or demand a prewarmed asset.
        if (typeof(CardModel).GetField("_pool", NativeMembers)?.GetValue(card) is CardPoolModel pool
            && !RenderPoolValueQualified(pool)) return false;
        if (typeof(CardModel).GetField("_owner", NativeMembers)?.GetValue(card) is not Player player) return true;
        if (player.GetType() != typeof(Player)) return false;
        CharacterModel character = player.Character;
        return character.GetType().Module == typeof(CardModel).Module
            && NativeRenderMethod(character.GetType().GetProperty("CardPool", NativeMembers)?.GetMethod);
    }
    private static bool RenderFixedClosureUnpatched() =>
        MethodsUnpatched(typeof(CardModel), "get_TitleLocString", "get_Title", "get_Description", "get_Type", "get_Rarity",
            "get_IsUpgraded", "get_CurrentUpgradeLevel", "get_MaxUpgradeLevel", "get_Pile", "get_UpgradePreviewType", "get_IsEnchantmentPreview",
            "get_DynamicVars", "get_EnergyCost", "get_CurrentStarCost", "get_BaseStarCost", "get_TemporaryStarCost", "get_CanonicalStarCost",
            "get_HasStarCostX", "GetStarCostWithModifiers", "get_Owner", "get_RunState", "get_CombatState", "get_CurrentTarget",
            "get_Enchantment", "get_Affliction", "get_TargetType", "get_GainsBlock", "get_OrbEvokeType", "get_Keywords", "get_LocalKeywords",
            "get_IsSlyThisTurn", "get_ShouldRetainThisTurn", "get_BaseReplayCount", "GetEnchantedReplayCount", "GetKeywordsWithSources",
            "get_Pool", "GetDescriptionForPile", "GetDescriptionForUpgradePreview", "AddExtraArgsToDescription", "UpdateDynamicVarPreview", "get_HoverTips", "get_ExtraHoverTips")
        && MethodsUnpatched(typeof(AbstractModel), "get_Id", "get_IsMutable", "get_IsCanonical", "AssertMutable")
        && RenderMethodsUnpatched(typeof(CardEnergyCost)) && RenderMethodsUnpatched(typeof(LocalCostModifier))
        && RenderMethodsUnpatched(typeof(TemporaryCardCost)) && RenderMethodsUnpatched(typeof(DynamicVarSet))
        && RenderMethodsUnpatched(typeof(EnergyIconHelper)) && RenderMethodsUnpatched(typeof(LocString))
        && MethodsUnpatched(typeof(LocManager), "get_Instance", "GetTable", "SmartFormat", "get_CultureInfo")
        && RenderMethodsUnpatched(typeof(LocTable))
        && RenderMethodsUnpatched(typeof(IHoverTip)) && RenderMethodsUnpatched(typeof(HoverTip))
        && RenderMethodsUnpatched(typeof(CardHoverTip)) && RenderMethodsUnpatched(typeof(HoverTipFactory))
        && RenderMethodsUnpatched(typeof(CardKeywordOrder)) && RenderMethodsUnpatched(typeof(CardModel).Assembly.GetType("MegaCrit.Sts2.Core.Entities.Cards.CardKeywordExtensions")!)
        && RenderMethodsUnpatched(typeof(SummonVar))
        && SingletonListUnpatched(typeof(IHoverTip))
        && typeof(IHoverTip).GetNestedTypes(NativeMembers).All(RenderMethodsUnpatched)
        && RenderGettersUnpatched(typeof(CardPoolModel), "EnergyColorName")
        && RenderGettersUnpatched(typeof(Player), "RunState", "Creature", "Character", "IsOstyAlive", "PlayerCombatState")
        && RenderGettersUnpatched(typeof(Creature), "CombatState", "Player", "Monster", "Powers")
        && RenderGettersUnpatched(typeof(CardPile), "Type", "IsCombatPile", "Cards")
        && RenderGettersUnpatched(typeof(CombatManager), "Instance", "IsInProgress", "IsOverOrEnding", "IsStarting");

    private static bool NoFormattingCallbacks(object value)
    {
        for (Type? current = value.GetType(); current != null && current != typeof(object); current = current.BaseType)
            foreach (FieldInfo field in current.GetFields(NativeMembers | BindingFlags.DeclaredOnly))
                if (!field.IsStatic && typeof(Delegate).IsAssignableFrom(field.FieldType) && field.GetValue(value) != null) return false;
        return true;
    }
    private static bool RenderFormattingQualified()
    {
        object? formatter = typeof(LocManager).GetField("_smartFormatter", NativeMembers)?.GetValue(null);
        if (formatter == null || formatter.GetType().FullName != "SmartFormat.SmartFormatter"
            || formatter.GetType().Module.ModuleVersionId != new Guid("5daab956-3007-40b4-9902-24b91fb5bae9")
            || !RenderMethodsUnpatched(formatter.GetType()) || !NoFormattingCallbacks(formatter)) return false;
        foreach (string property in new[] { "Parser", "Settings" })
        {
            PropertyInfo? member = formatter.GetType().GetProperty(property, NativeMembers);
            if (member?.GetMethod is not { } getter || !IsUnpatched(getter) || member.GetValue(formatter) is not { } child
                || child.GetType().Module != formatter.GetType().Module || !RenderMethodsUnpatched(child.GetType())
                || !NoFormattingCallbacks(child)) return false;
        }
        var accepted = new HashSet<string>(StringComparer.Ordinal)
        {
            "SmartFormat.Extensions.ListFormatter", "SmartFormat.Extensions.DictionarySource", "SmartFormat.Extensions.ValueTupleSource",
            "SmartFormat.Extensions.ReflectionSource", "SmartFormat.Extensions.KeyValuePairSource", "SmartFormat.Extensions.DefaultSource", "SmartFormat.Extensions.PluralLocalizationFormatter",
            "SmartFormat.Extensions.ConditionalFormatter", "SmartFormat.Extensions.ChooseFormatter", "SmartFormat.Extensions.SubStringFormatter",
            "SmartFormat.Extensions.IsMatchFormatter", "MegaCrit.Sts2.Core.Localization.Formatters.LocaleNumberFormatter", "SmartFormat.Extensions.DefaultFormatter",
            "MegaCrit.Sts2.Core.Localization.Formatters.AbsoluteValueFormatter", "MegaCrit.Sts2.Core.Localization.Formatters.EnergyIconsFormatter",
            "MegaCrit.Sts2.Core.Localization.Formatters.StarIconsFormatter", "MegaCrit.Sts2.Core.Localization.Formatters.HighlightDifferencesFormatter",
            "MegaCrit.Sts2.Core.Localization.Formatters.HighlightDifferencesInverseFormatter", "MegaCrit.Sts2.Core.Localization.Formatters.PercentMoreFormatter",
            "MegaCrit.Sts2.Core.Localization.Formatters.PercentLessFormatter", "MegaCrit.Sts2.Core.Localization.Formatters.ShowIfUpgradedFormatter"
        };
        foreach (string property in new[] { "SourceExtensions", "FormatterExtensions" })
        {
            PropertyInfo? collection = formatter.GetType().GetProperty(property, NativeMembers);
            if (collection?.GetMethod is not { } getter || !IsUnpatched(getter)
                || collection.GetValue(formatter) is not IEnumerable extensions) return false;
            foreach (object extension in extensions)
                if (!accepted.Contains(extension.GetType().FullName ?? "")
                    || extension.GetType().Module != ((extension.GetType().FullName ?? "").StartsWith("SmartFormat.", StringComparison.Ordinal)
                        ? formatter.GetType().Module : typeof(CardModel).Module)
                    || !RenderMethodsUnpatched(extension.GetType()) || !NoFormattingCallbacks(extension)) return false;
        }
        return true;
    }
    private static readonly string[] RenderHookMethods = {
        "ModifyDamageAdditive", "ModifyDamageMultiplicative", "ModifyDamageCap", "ModifyBlockAdditive", "ModifyBlockMultiplicative",
        "TryModifyEnergyCostInCombat", "TryModifyEnergyCostInCombatLate", "TryModifyStarCost", "TryModifyKeywordsInCombat"
    };
    private static bool RenderCombatListenerQualified(AbstractModel model)
    {
        Type current = model.GetType();
        if (current.Assembly != typeof(CardModel).Assembly) return false;
        // Vanilla listeners only, with unpatched render hooks. The native hand/pile UI
        // evaluates these same hooks whenever it draws a card, so a vanilla override is
        // the game's own display semantics rather than an unqualified side channel; a
        // per-class allowlist here rejected ordinary combat (any relic/power override).
        foreach (string name in RenderHookMethods)
        {
            MethodInfo? method = current.GetMethod(name, NativeMembers);
            if (method == null || !IsUnpatched(method)) return false;
            if (method.DeclaringType?.Assembly != typeof(CardModel).Assembly) return false;
        }
        if (model is WeakPower or VulnerablePower)
            return RenderMethodsUnpatched(current) && RenderVariablesQualified(model,
                typeof(PowerModel).GetField("_dynamicVars", NativeMembers)?.GetValue(model) as DynamicVarSet);
        return true;
    }

    // This is the child-combat extension of the same Run-listener qualification.
    // Subscribers and the entire iterator/member-test closure are checked before
    // the first MoveNext; only then are current effective renderer hooks checked.
    private static bool RenderCombatQualified(CardModel card, IRunState run, ICombatState combat, out string? gap)
    {
        gap = "native_card_render_combat_hook_closure_unqualified";
        if (combat.GetType() != typeof(CombatState) || !NativeRunHookReadQualification.Preflight(run)
            || typeof(ModHelper).GetField("_combatHookSubscribers", NativeMembers)?.GetValue(null) is not ICollection { Count: 0 }
            || !RenderIteratorUnpatched(typeof(CombatState).GetMethod("IterateHookListeners", NativeMembers))
            || !RenderIteratorUnpatched(typeof(ModHelper).GetMethod("IterateAllCombatStateSubscribers", NativeMembers))
            || !RenderIteratorUnpatched(typeof(Hook).GetMethod("IterateCombatHookListeners", NativeMembers))
            || !MethodsUnpatched(typeof(CombatState), "Contains", "get_RunState", "get_Players", "get_PlayerCreatures", "get_Creatures",
                "get_Modifiers", "get_BadgeModels", "get_MultiplayerScalingModel")
            || !RenderMethodsUnpatched(typeof(CombatState).GetNestedType("<>c", BindingFlags.NonPublic)!)
            || !RenderGettersUnpatched(typeof(Creature), "IsPlayer", "Powers", "Player", "Monster", "CombatState", "PetOwner")
            || !RenderGettersUnpatched(typeof(Player), "IsActiveForHooks", "Relics", "PotionSlots", "PlayerCombatState", "Creature")
            || !RenderGettersUnpatched(typeof(PlayerCombatState), "AllPiles", "OrbQueue")
            || !RenderGettersUnpatched(typeof(OrbQueue), "Orbs")
            || !RenderGettersUnpatched(typeof(PowerModel), "Owner", "Amount")
            || !RenderGettersUnpatched(typeof(RelicModel), "IsMelted", "HasBeenRemovedFromState", "Owner")
            || !RenderGettersUnpatched(typeof(PotionModel), "HasBeenRemovedFromState", "Owner")
            || !RenderGettersUnpatched(typeof(OrbModel), "HasBeenRemovedFromState", "Owner")
            || !RenderGettersUnpatched(typeof(MonsterModel), "Creature")
            || !RenderGettersUnpatched(typeof(AfflictionModel), "HasCard", "Card")
            || !RenderGettersUnpatched(typeof(EnchantmentModel), "HasCard", "Card")
            || !RenderHookMethods.All(name => MethodsUnpatched(typeof(AbstractModel), name))
            || !MethodsUnpatched(typeof(Hook), "ModifyBlock", "ModifyDamage", "ModifyDamageInternal", "ModifyEnergyCostInCombat", "ModifyStarCost", "ModifyKeywordsInCombat")
            || !MethodsUnpatched(typeof(Player), "GetRelic") || !MethodsUnpatched(typeof(Creature), "GetPower")
            || !RenderMethodsUnpatched(typeof(ValuePropExtensions))
            || !RenderMethodsUnpatched(typeof(StrengthPower)) || !RenderMethodsUnpatched(typeof(DexterityPower))
            || !RenderMethodsUnpatched(typeof(FrailPower)) || !RenderMethodsUnpatched(typeof(PaperKrane))
            || !RenderMethodsUnpatched(typeof(PaperPhrog)) || !RenderMethodsUnpatched(typeof(DebilitatePower))
            || !RenderMethodsUnpatched(typeof(CrueltyPower))) return false;
        if (!ReferenceEquals(((CombatState)combat).RunState, run)) return false;
        AbstractModel[] listeners = run.IterateHookListeners(combat).ToArray();
        if (!listeners.All(RenderCombatListenerQualified)) { gap = "native_card_render_effective_combat_hook_unqualified"; return false; }
        gap = null;
        return true;
    }

    public static bool TryPreflightRender(CardModel card, CardPreviewMode mode, PileType displayPile,
        out string? gap, bool refreshPreview = true)
    {
        gap = "native_card_render_call_closure_unqualified";
        try
        {
            if (typeof(CardModel).Module.ModuleVersionId != AuditedGameMvid || mode is not (CardPreviewMode.Normal or CardPreviewMode.Upgrade)
                || displayPile is not (PileType.None or PileType.Deck or PileType.Hand or PileType.Play or PileType.Draw or PileType.Discard or PileType.Exhaust)
) return false;
            if (!RenderFixedClosureUnpatched()) { gap = "native_card_render_fixed_closure_unqualified"; return false; }
            if (!RenderCardDispatchQualified(card)) { gap = "native_card_render_effective_dispatch_unqualified"; return false; }
            if (!RenderFormattingQualified()) { gap = "native_card_render_formatter_roster_unqualified"; return false; }
            if (!RenderCurrentPoolsQualified(card)
                || !RenderVariablesQualified(card, CardVars?.GetValue(card) as DynamicVarSet)
                || !RenderAttachmentQualified(card)) { gap = "native_card_render_current_values_unqualified"; return false; }
            if (card.RunState is { } run)
            {
                if (!NativeRunHookReadQualification.Preflight(run)) { gap = "native_card_render_run_subscribers_unqualified"; return false; }
                if (card.CombatState is { } combat && !RenderCombatQualified(card, run, combat, out gap)) return false;
            }
            else if (card.CombatState != null) { gap = "native_card_render_combat_run_owner_unavailable"; return false; }
            gap = null;
            return true;
        }
        catch { return false; }
    }

    public static void InvokePreflight(CardModel card, CardPreviewMode mode, PileType displayPile, bool refreshPreview = true)
    {
        if (!TryPreflightRender(card, mode, displayPile, out string? gap, refreshPreview))
            throw new InvalidOperationException(gap);
    }
}
