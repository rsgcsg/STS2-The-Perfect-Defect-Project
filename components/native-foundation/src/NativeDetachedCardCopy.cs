using System;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Models;

namespace STS2Platform.NativeFoundation;

/// <summary>Fixed exact-build structural bridge for presentation only. The
/// caller must first qualify the effective native copy closure and every
/// copied delegate. Only the newly allocated copy is changed. All native field
/// copying and rules remain in DeepCloneFields/AfterCloned and the real native
/// upgrade/enchantment methods. Never register or dispatch this copy.</summary>
internal static class NativeDetachedCardCopy
{
    internal static CardModel CreateQualifiedCopy(CardModel original)
    {
        // Native AbstractModel.MutableClone performs these three structural
        // steps. Its DeepCloneFields reattaches enchantment/affliction before
        // AfterCloned clears copied card UI delegates. Isolate those two on
        // the NEW copy first; never clear/restore an event on the original.
        var copy = (CardModel)MemberwiseClone(original);
        SetMutable(copy, true);
        EnchantmentChanged(copy) = null;
        AfflictionChanged(copy) = null;
        DeepCloneFields(copy);
        AfterCloned(copy);
        return copy;
    }

    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "MemberwiseClone")]
    private static extern object MemberwiseClone(object original);
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "set_IsMutable")]
    private static extern void SetMutable(AbstractModel copy, bool value);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "EnchantmentChanged")]
    private static extern ref Action? EnchantmentChanged(CardModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "AfflictionChanged")]
    private static extern ref Action? AfflictionChanged(CardModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "DeepCloneFields")]
    private static extern void DeepCloneFields(CardModel copy);
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "AfterCloned")]
    private static extern void AfterCloned(CardModel copy);
}
