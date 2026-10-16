using System;
using System.Collections.Generic;

namespace STS2Platform.NativeFoundation;

/// <summary>Exact native multiplier identities used by initialized card vars.
/// This inventory admits metadata/copy only. It does NOT qualify invoking a
/// multiplier in combat; the non-combat native Calculate branch skips it.
/// Unknown delegates are rejected even on that inactive branch.</summary>
internal static class NativeCalculatedVariableInventory
{
    internal static readonly HashSet<string> Multipliers = new(StringComparer.Ordinal)
    {
        "MegaCrit.Sts2.Core.Models.Cards.AshenStrike+<>c::<get_CanonicalVars>b__4_0",
        "MegaCrit.Sts2.Core.Models.Cards.Barrage+<>c::<get_CanonicalVars>b__5_0",
        "MegaCrit.Sts2.Core.Models.Cards.BeatIntoShape+<>c::<get_CanonicalVars>b__3_0",
        "MegaCrit.Sts2.Core.Models.Cards.BodySlam+<>c::<get_CanonicalVars>b__2_0",
        "MegaCrit.Sts2.Core.Models.Cards.Bully+<>c::<get_CanonicalVars>b__2_0",
        "MegaCrit.Sts2.Core.Models.Cards.CompileDriver+<>c::<get_CanonicalVars>b__3_0",
        "MegaCrit.Sts2.Core.Models.Cards.CrescentSpear+<>c::<get_CanonicalVars>b__4_0",
        "MegaCrit.Sts2.Core.Models.Cards.DeathMarch+<>c::<get_CanonicalVars>b__2_0",
        "MegaCrit.Sts2.Core.Models.Cards.DemonicShield+<>c::<get_CanonicalVars>b__8_0",
        "MegaCrit.Sts2.Core.Models.Cards.ExpectAFight+<>c::<get_CanonicalVars>b__4_0",
        "MegaCrit.Sts2.Core.Models.Cards.Finisher+<>c::<get_CanonicalVars>b__3_0",
        "MegaCrit.Sts2.Core.Models.Cards.FlakCannon+<>c::<get_CanonicalVars>b__3_0",
        "MegaCrit.Sts2.Core.Models.Cards.Flechettes+<>c::<get_CanonicalVars>b__3_0",
        "MegaCrit.Sts2.Core.Models.Cards.GangUp+<>c::<get_CanonicalVars>b__4_0",
        "MegaCrit.Sts2.Core.Models.Cards.GoldAxe+<>c::<get_CanonicalVars>b__2_0",
        "MegaCrit.Sts2.Core.Models.Cards.HelixDrill+<>c::<get_CanonicalVars>b__3_0",
        "MegaCrit.Sts2.Core.Models.Cards.KnifeTrap+<>c::<get_CanonicalVars>b__5_0",
        "MegaCrit.Sts2.Core.Models.Cards.LunarBlast+<>c::<get_CanonicalVars>b__3_0",
        "MegaCrit.Sts2.Core.Models.Cards.MementoMori+<>c::<get_CanonicalVars>b__2_0",
        "MegaCrit.Sts2.Core.Models.Cards.Mimic+<>c::<get_CanonicalVars>b__6_0",
        "MegaCrit.Sts2.Core.Models.Cards.MindBlast+<>c::<get_CanonicalVars>b__2_0",
        "MegaCrit.Sts2.Core.Models.Cards.Mirage+<>c::<get_CanonicalVars>b__6_0",
        "MegaCrit.Sts2.Core.Models.Cards.Murder+<>c::<get_CanonicalVars>b__2_0",
        "MegaCrit.Sts2.Core.Models.Cards.NoEscape+<>c::<get_CanonicalVars>b__4_0",
        "MegaCrit.Sts2.Core.Models.Cards.Normality+<>c::<get_CanonicalVars>b__12_0",
        "MegaCrit.Sts2.Core.Models.Cards.PerfectedStrike+<>c::<get_CanonicalVars>b__4_0",
        "MegaCrit.Sts2.Core.Models.Cards.PreciseCut+<>c::<get_CanonicalVars>b__2_0",
        "MegaCrit.Sts2.Core.Models.Cards.Protector+<>c::<get_CanonicalVars>b__6_0",
        "MegaCrit.Sts2.Core.Models.Cards.PullFromBelow+<>c::<get_CanonicalVars>b__5_0",
        "MegaCrit.Sts2.Core.Models.Cards.Radiate+<>c::<get_CanonicalVars>b__3_0",
        "MegaCrit.Sts2.Core.Models.Cards.Rattle+<>c::<get_CanonicalVars>b__7_0",
        "MegaCrit.Sts2.Core.Models.Cards.Rend+<>c::<get_CanonicalVars>b__2_0",
        "MegaCrit.Sts2.Core.Models.Cards.Sacrifice+<>c::<get_CanonicalVars>b__8_0",
        "MegaCrit.Sts2.Core.Models.Cards.SoulStorm+<>c::<get_CanonicalVars>b__4_0",
        "MegaCrit.Sts2.Core.Models.Cards.SovereignBlade+<>c::<get_CanonicalVars>b__24_0",
        "MegaCrit.Sts2.Core.Models.Cards.Squeeze+<>c::<get_CanonicalVars>b__6_0",
        "MegaCrit.Sts2.Core.Models.Cards.Stack+<>c::<get_CanonicalVars>b__6_0",
        "MegaCrit.Sts2.Core.Models.Cards.Supermassive+<>c::<get_CanonicalVars>b__2_0",
        "MegaCrit.Sts2.Core.Models.Cards.Synchronize+<>c::<get_CanonicalVars>b__5_0",
        "MegaCrit.Sts2.Core.Models.Cards.TearAsunder+<>c::<get_CanonicalVars>b__3_0",
        "MegaCrit.Sts2.Core.Models.Cards.TimesUp+<>c::<get_CanonicalVars>b__4_0",
        "MegaCrit.Sts2.Core.Models.Cards.Unleash+<>c::<get_CanonicalVars>b__6_0",
        "MegaCrit.Sts2.Core.Models.Cards.Voltaic+<>c::<get_CanonicalVars>b__5_0",
    };
}
