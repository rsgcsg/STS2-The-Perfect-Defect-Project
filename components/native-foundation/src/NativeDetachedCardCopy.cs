using System;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards;

namespace STS2Platform.NativeFoundation;

/// <summary>Fixed exact-build structural bridge for presentation only. The
/// caller must first qualify the effective native copy closure. All copied card
/// callbacks are detached on the new copy before native work. All native field
/// copying and rules remain in DeepCloneFields/AfterCloned and the real native
/// upgrade/enchantment methods. Never register or dispatch this copy.</summary>
internal static class NativeDetachedCardCopy
{
    internal static CardModel CreateQualifiedCopy(CardModel original)
    {
        // Native AbstractModel.MutableClone performs these three structural
        // steps. Its DeepCloneFields reattaches enchantment/affliction before
        // AfterCloned clears copied card delegates. Detach all card callbacks on
        // the NEW copy first; never clear/restore an event on the original.
        var copy = (CardModel)MemberwiseClone(original);
        SetMutable(copy, true);
        EnchantmentChanged(copy) = null;
        AfflictionChanged(copy) = null;
        EnergyCostChanged(copy) = null;
        KeywordsChanged(copy) = null;
        ReplayCountChanged(copy) = null;
        Played(copy) = null;
        Drawn(copy) = null;
        StarCostChanged(copy) = null;
        Upgraded(copy) = null;
        Forged(copy) = null;
        ExecutionFinished(copy) = null;
        DeepCloneFields(copy);
        if (copy is SovereignBlade blade) AfterCloned(blade);
        else AfterCloned(copy);
        return copy;
    }

    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "_model")]
    internal static extern ref CardModel? UiModel(NCard node);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "_subscribedEnchantment")]
    internal static extern ref EnchantmentModel? UiSubscribedEnchantment(NCard node);

    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "MemberwiseClone")]
    private static extern object MemberwiseClone(object original);
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "set_IsMutable")]
    private static extern void SetMutable(AbstractModel copy, bool value);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "EnchantmentChanged")]
    private static extern ref Action? EnchantmentChanged(CardModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "AfflictionChanged")]
    private static extern ref Action? AfflictionChanged(CardModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "EnergyCostChanged")]
    private static extern ref Action? EnergyCostChanged(CardModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "KeywordsChanged")]
    private static extern ref Action? KeywordsChanged(CardModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "ReplayCountChanged")]
    private static extern ref Action? ReplayCountChanged(CardModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "Played")]
    private static extern ref Action? Played(CardModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "Drawn")]
    private static extern ref Action? Drawn(CardModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "StarCostChanged")]
    private static extern ref Action? StarCostChanged(CardModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "Upgraded")]
    private static extern ref Action? Upgraded(CardModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "Forged")]
    private static extern ref Action? Forged(CardModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "ExecutionFinished")]
    private static extern ref Action<AbstractModel>? ExecutionFinished(AbstractModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "DeepCloneFields")]
    private static extern void DeepCloneFields(CardModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "AfterCloned")]
    private static extern void AfterCloned(CardModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "AfterCloned")]
    private static extern void AfterCloned(SovereignBlade copy);
}
