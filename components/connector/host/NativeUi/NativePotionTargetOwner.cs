using System;
using System.Reflection;

namespace STS2Connector.NativeUi;

/// <summary>Reference-only exact binding check for NPotionHolder's fixed native
/// ShouldCancelTargeting delegate. This never invokes a delegate or method.</summary>
internal static class NativePotionTargetOwner
{
    internal static bool Matches(Delegate? predicate, object holder, MethodInfo? method,
        object potion, object? currentHolderPotion, object? currentSlotPotion, bool liveVisible) =>
        liveVisible && predicate != null && method != null
        && ReferenceEquals(predicate.Target, holder) && predicate.Method == method
        && ReferenceEquals(potion, currentHolderPotion) && ReferenceEquals(potion, currentSlotPotion);
}
