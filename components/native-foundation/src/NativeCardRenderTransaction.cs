using System;
using System.Collections.Generic;
using System.Linq;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Models;

namespace STS2Platform.NativeFoundation;

// Only the pinned native Normal card renderer is invoked. Keep the actual card's
// owner/pile context; a detached clone would change native preview calculations.
// This transaction restores arbitrary prior preview values, not just base values.
internal sealed class NativeRenderStateViolationException(string message, Exception? inner) : Exception(message, inner);

internal static class NativeCardRenderTransaction
{
    private sealed record Saved(DynamicVar Variable, decimal Base, bool Upgraded,
        decimal Enchanted, decimal Preview, string? EnergyPrefix)
    {
        internal static Saved Capture(DynamicVar value) => new(value, value.BaseValue,
            value.WasJustUpgraded, value.EnchantedValue, value.PreviewValue,
            value is EnergyVar energy ? energy.ColorPrefix : null);
        internal void Restore()
        {
            Variable.EnchantedValue = Enchanted;
            Variable.PreviewValue = Preview;
            if (Variable is EnergyVar energy) energy.ColorPrefix = EnergyPrefix!;
        }
        internal bool Exact => Variable.BaseValue == Base && Variable.WasJustUpgraded == Upgraded
            && Variable.EnchantedValue == Enchanted && Variable.PreviewValue == Preview
            && (Variable is not EnergyVar energy || energy.ColorPrefix == EnergyPrefix);
    }

    private static readonly HashSet<Type> KnownVariables = new()
    {
        typeof(DynamicVar), typeof(BlockVar), typeof(BoolVar), typeof(CalculatedBlockVar),
        typeof(CalculatedDamageVar), typeof(CalculatedVar), typeof(CalculationBaseVar),
        typeof(CalculationExtraVar), typeof(CardsVar), typeof(DamageVar), typeof(EnergyVar),
        typeof(ExtraDamageVar), typeof(ForgeVar), typeof(GoldVar), typeof(HealVar),
        typeof(HpLossVar), typeof(IfUpgradedVar), typeof(IntVar), typeof(MaxHpVar),
        typeof(OstyDamageVar), typeof(RepeatVar), typeof(StarsVar), typeof(StringVar), typeof(SummonVar)
    };

    private static bool Known(DynamicVar value) => KnownVariables.Contains(value.GetType())
        || value.GetType().IsGenericType
            && value.GetType().GetGenericTypeDefinition() == typeof(PowerVar<>);

    internal static T Run<T>(CardModel card, PileType displayPile, Func<T> capture,
        CardPreviewMode mode = CardPreviewMode.Normal, Action? beforePreview = null)
    {
        NativeCardPreviewReadQualification.InvokePreflight(card, mode, displayPile);
        beforePreview?.Invoke();
        DynamicVar[] cardVars = card.DynamicVars.Values.ToArray();
        DynamicVar[] enchantVars = card.Enchantment?.DynamicVars.Values.ToArray() ?? [];
        DynamicVar[] all = cardVars.Concat(enchantVars).ToArray();
        if (all.Any(value => !Known(value)) || all.Distinct(ReferenceEqualityComparer.Instance).Count() != all.Length)
            throw new InvalidOperationException("Unqualified native preview variable type or alias");
        Saved[] saved = all.Select(Saved.Capture).ToArray();
        var enchantment = card.Enchantment;
        Exception? primary = null;
        try
        {
            card.DynamicVars.ClearPreview();
            card.UpdateDynamicVarPreview(mode, card.CurrentTarget, card.DynamicVars);
            if (enchantment != null)
            {
                enchantment.DynamicVars.ClearPreview();
                card.UpdateDynamicVarPreview(mode, card.CurrentTarget, enchantment.DynamicVars);
            }
            return capture();
        }
        catch (Exception error) { primary = error; throw; }
        finally
        {
            foreach (Saved value in saved) value.Restore();
            if (!ReferenceEquals(card.Enchantment, enchantment)
                || !card.DynamicVars.Values.SequenceEqual(cardVars, ReferenceEqualityComparer.Instance)
                || !(enchantment?.DynamicVars.Values ?? Array.Empty<DynamicVar>())
                    .SequenceEqual(enchantVars, ReferenceEqualityComparer.Instance)
                || saved.Any(value => !value.Exact))
                throw new NativeRenderStateViolationException("Native card rendering changed its owned preview state", primary);
        }
    }
}
