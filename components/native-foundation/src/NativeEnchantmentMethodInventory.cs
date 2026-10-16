using System;
using System.Collections.Generic;

namespace STS2Platform.NativeFoundation;

/// <summary>Fixed exact-MVID model-only enchantment copy/update families.
/// These names select already audited native methods, never replacement rules.
/// Display hooks/hover text and command/UI lookups have separate qualifications.</summary>
internal static class NativeEnchantmentMethodInventory
{
    internal static readonly HashSet<string> Types = new(StringComparer.Ordinal)
    {
        "Adroit", "Clone", "Corrupted", "DeprecatedEnchantment", "Glam", "Goopy",
        "Imbued", "Inky", "Instinct", "Momentum", "Nimble", "PerfectFit",
        "RoyallyApproved", "Sharp", "Slither", "SlumberingEssence", "SoulsPower",
        "Sown", "Spiral", "Steady", "Swift", "TezcatarasEmber", "Vigorous", "MockFreeEnchantment"
    };
    internal static readonly HashSet<string> KeywordUpdates = new(StringComparer.Ordinal)
    { "Goopy", "RoyallyApproved", "Steady" };
    internal static readonly HashSet<string> VariableFactories = new(StringComparer.Ordinal)
    { "Adroit", "Glam", "Inky", "Spiral", "TezcatarasEmber" };
}
