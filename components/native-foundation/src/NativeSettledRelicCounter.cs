using System;
using System.Reflection;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Relics;

namespace STS2Platform.NativeFoundation;

public sealed record NativeSettledRelicCounterValue(
    string DefinitionId, string RuleId, int DisplayCounter, int LogicalCounter);

/// <summary>Versioned, read-only public remainder for the two approved mechanisms.
/// Private cumulative totals and the animation flag never leave this seam.</summary>
public static class NativeSettledRelicCounter
{
    public const string GameMvid = "57785517-0b16-42b9-8b36-bad6fb28384b";
    public static string CurrentGameMvid => typeof(RelicModel).Assembly.ManifestModule.ModuleVersionId.ToString("D");
    private static readonly FieldInfo? KusarigamaAttacks = typeof(Kusarigama).GetField(
        "_attacksPlayedThisTurn", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly);

    public static bool Supports(string definitionId) => definitionId is "KUSARIGAMA" or "LASTING_CANDY";

    public static NativeSettledRelicCounterValue? Capture(RelicModel relic)
    {
        if (!Supports(relic.Id.Entry)) return null;
        if (CurrentGameMvid != GameMvid)
            throw new InvalidOperationException("settled_relic_counter_exact_game_changed");
        if (!relic.IsMutable || !relic.ShowCounter) return null;
        int logical, display = relic.DisplayAmount;
        string rule;
        if (relic is Kusarigama)
        {
            // This one named field is the registered v0.111.0 read seam. It is
            // not a reflective property browser or a way to expose total plays.
            if (KusarigamaAttacks?.FieldType != typeof(int)
                || KusarigamaAttacks.DeclaringType != typeof(Kusarigama)
                || relic.DynamicVars.Cards.IntValue != 3
                || KusarigamaAttacks.GetValue(relic) is not int attacks || attacks < 0)
                throw new InvalidOperationException("kusarigama_logical_counter_unavailable");
            logical = attacks % 3;
            rule = "kusarigama-attacks-mod3-1";
            if (display < 0 || display > 3) throw new InvalidOperationException("kusarigama_display_out_of_scope");
        }
        else if (relic is LastingCandy candy && candy.CombatRewardsSeen >= 0)
        {
            logical = candy.CombatRewardsSeen % 2;
            rule = "lasting-candy-rewards-mod2-1";
            if (display < 0 || display > 2) throw new InvalidOperationException("lasting_candy_display_out_of_scope");
        }
        else throw new InvalidOperationException("settled_relic_counter_native_type_changed");
        return new(relic.Id.Entry, rule, display, logical);
    }
}
