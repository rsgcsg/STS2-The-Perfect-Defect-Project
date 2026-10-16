using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Models;

namespace STS2Platform.NativeFoundation;

internal enum NativeDetachedPreviewKind { DeckUpgrade, SingleEnchantment }

/// <summary>Process-local presentation only. Original is the game-owned
/// subject; Display is never added to a scope/pile or used as an input operand.
/// Public formatting has its own mandatory read preflight.</summary>
internal sealed record NativeDetachedCardPreview(CardModel Original, CardModel Display,
    NativeDetachedPreviewKind Kind, PileType DisplayPile, CardPreviewMode PreviewMode);

internal sealed class NativePreviewReadStateViolationException(string message) : Exception(message);

internal static class NativeDetachedCardPreviewFactory
{
    internal static bool TryDeckUpgrade(CardModel original, out NativeDetachedCardPreview? result, out string? gap)
    {
        result = null;
        if (!NativeCardPreviewReadQualification.TryPreflightUpgrade(original, out gap)) return false;
        SourceStamp before = SourceStamp.Card(original);
        try
        {
            // Use the qualified structural native copy bridge, then the same
            // native grid UpgradeInternal. Scope.CloneCard would register the
            // temporary model into live _allCards and is never a read path.
            CardModel display = NativeDetachedCardCopy.CreateQualifiedCopy(original);
            display.UpgradeInternal();
            display.UpgradePreviewType = CardUpgradePreviewType.Deck;
            before.AssertUnchanged();
            if (!NativeCardPreviewReadQualification.TryPreflightUpgrade(original, out gap)) return false;
            result = new(original, display, NativeDetachedPreviewKind.DeckUpgrade, PileType.None, CardPreviewMode.Upgrade);
            gap = null;
            return true;
        }
        catch (NativePreviewReadStateViolationException) { throw; }
        catch
        {
            before.AssertUnchanged();
            gap = "native_detached_upgrade_preview_failed";
            return false;
        }
    }

    internal static bool TrySingleEnchant(CardModel original, EnchantmentModel canonicalEnchantment, int amount,
        out NativeDetachedCardPreview? result, out string? gap)
    {
        result = null;
        if (!NativeCardPreviewReadQualification.TryPreflightSingleEnchant(original, canonicalEnchantment, out gap)) return false;
        SourceStamp before = SourceStamp.Card(original);
        SourceStamp enchantmentBefore = SourceStamp.Model(canonicalEnchantment, typeof(EnchantmentModel));
        try
        {
            CardModel display = NativeDetachedCardCopy.CreateQualifiedCopy(original);
            EnchantmentModel enchantment = canonicalEnchantment.ToMutable();
            // Native NEnchantPreview.Init model sequence, on the detached copy.
            // Do not replace it with guessed arithmetic or finalized upgrades.
            display.EnchantInternal(enchantment, amount);
            display.IsEnchantmentPreview = true;
            enchantment.ModifyCard();
            before.AssertUnchanged();
            enchantmentBefore.AssertUnchanged();
            if (!NativeCardPreviewReadQualification.TryPreflightSingleEnchant(original, canonicalEnchantment, out gap)) return false;
            result = new(original, display, NativeDetachedPreviewKind.SingleEnchantment, PileType.None, CardPreviewMode.Normal);
            gap = null;
            return true;
        }
        catch (NativePreviewReadStateViolationException) { throw; }
        catch
        {
            before.AssertUnchanged();
            enchantmentBefore.AssertUnchanged();
            gap = "native_detached_enchantment_preview_failed";
            return false;
        }
    }

    // An assertion of the audited invocation's original-object footprint. It
    // does not permit unknown code to run and does not restore/roll back state.
    // Only fixed native owner fields and plain CLR containers are inspected.
    private sealed class SourceStamp
    {
        private const BindingFlags Fields = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
        private readonly List<(object Owner, FieldInfo Field, object? Value)> fields = new();
        private readonly List<(System.Collections.IList List, object?[] Items)> lists = new();
        private readonly List<(HashSet<CardKeyword> Set, CardKeyword[] Items)> keywordSets = new();

        internal static SourceStamp Model(object model, Type owner)
        {
            var stamp = new SourceStamp();
            stamp.Add(model, owner);
            if (owner.GetField("_dynamicVars", Fields)?.GetValue(model) is DynamicVarSet vars) stamp.AddVariables(vars);
            return stamp;
        }

        internal static SourceStamp Card(CardModel card)
        {
            SourceStamp stamp = Model(card, typeof(CardModel));
            if (typeof(CardModel).GetField("<Enchantment>k__BackingField", Fields)?.GetValue(card) is EnchantmentModel enchantment)
            {
                stamp.Add(enchantment, typeof(EnchantmentModel));
                if (typeof(EnchantmentModel).GetField("_dynamicVars", Fields)?.GetValue(enchantment) is DynamicVarSet vars) stamp.AddVariables(vars);
            }
            if (typeof(CardModel).GetField("<Affliction>k__BackingField", Fields)?.GetValue(card) is AfflictionModel affliction)
                stamp.Add(affliction, typeof(AfflictionModel));
            if (typeof(CardModel).GetField("_keywords", Fields)?.GetValue(card) is HashSet<CardKeyword> keywords)
                stamp.keywordSets.Add((keywords, keywords.ToArray()));
            if (typeof(CardModel).GetField("_temporaryStarCosts", Fields)?.GetValue(card) is System.Collections.IList temporary)
                stamp.lists.Add((temporary, temporary.Cast<object?>().ToArray()));
            if (typeof(CardModel).GetField("_energyCost", Fields)?.GetValue(card) is CardEnergyCost cost)
            {
                stamp.Add(cost, typeof(CardEnergyCost));
                if (typeof(CardEnergyCost).GetField("_localModifiers", Fields)?.GetValue(cost) is System.Collections.IList modifiers)
                {
                    stamp.lists.Add((modifiers, modifiers.Cast<object?>().ToArray()));
                    foreach (object modifier in modifiers) stamp.Add(modifier, typeof(LocalCostModifier));
                }
            }
            return stamp;
        }

        private void AddVariables(DynamicVarSet vars)
        {
            Add(vars, typeof(DynamicVarSet));
            if (typeof(DynamicVarSet).GetField("_vars", Fields)?.GetValue(vars) is Dictionary<string, DynamicVar> values)
                foreach (DynamicVar value in values.Values) Add(value, typeof(DynamicVar));
        }

        private void Add(object owner, Type declaring) => fields.AddRange(declaring.GetFields(Fields)
            .Where(field => !field.IsStatic).Select(field => (owner, field, field.GetValue(owner))));

        private static bool Same(object? left, object? right) => ReferenceEquals(left, right)
            || left != null && right != null && left.GetType() == right.GetType()
                && (left is bool or int or uint or decimal or float or double or long or ulong || left.GetType().IsEnum)
                && left.Equals(right);

        internal void AssertUnchanged()
        {
            if (fields.Any(value => !Same(value.Value, value.Field.GetValue(value.Owner)))
                || lists.Any(value => value.List.Count != value.Items.Length
                    || value.List.Cast<object?>().Zip(value.Items).Any(pair => !Same(pair.First, pair.Second)))
                || keywordSets.Any(value => !value.Set.SetEquals(value.Items)))
                throw new NativePreviewReadStateViolationException("Native detached preview changed its original model footprint");
        }
    }
}
