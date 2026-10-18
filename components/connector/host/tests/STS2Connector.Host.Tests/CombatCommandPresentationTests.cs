using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Models.Potions;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Platform.NativeFoundation;

namespace STS2Connector.Host.Tests;

public sealed class CombatCommandPresentationTests
{
    [Fact]
    public void UsablePotionCommandsFollowCapturedInventorySlotsAcrossOpaqueKeyChanges()
    {
        PotionModel first = new EnergyPotion(), second = new SkillPotion(), unavailable = new EnergyPotion();
        PotionModel hidden = new SkillPotion();
        object player = new();
        NativeSemanticAction Action(string key, PotionModel potion, string id) =>
            new(key, "use", id, potion, new[] { new NativeSemanticOperand("target", "player", player) },
                "actual_native_catalog_fixture");
        foreach (bool reverseKeys in new[] { false, true })
        {
            NativeSemanticAction[] actions =
            [
                Action(reverseKeys ? "a" : "z", first, "energy-slot0"),
                Action(reverseKeys ? "z" : "a", second, "skill-slot1"),
                Action("0-hidden", hidden, "hidden")
            ];
            var decision = new NativeCombatDecision("captured", "combat_play_phase", true,
                actions, Array.Empty<string>(), null);
            var options = new List<VisibleCombatCommandOption>();
            CombatTurnSurfaceReader.AddPotionOptions(options, decision, new[] { first, unavailable, second });
            Assert.Equal(new[] { "energy-slot0", "skill-slot1" }, options.Select(option => option.EntityId));
            Assert.All(options, option => Assert.Equal(new[] { "player" }, option.TargetEntityIds));
            Assert.Same(first, decision.Actions[0].NativeSubject);
            Assert.Same(second, decision.Actions[1].NativeSubject);
            Assert.Same(player, decision.Actions[0].Operands[0].NativeValue);
            Assert.Equal(actions, decision.Actions);
        }
    }

    [Fact]
    public void PlayableCommandOrderFollowsCapturedHandAcrossOpaqueKeyChangesWithoutLosingTargetsOrRepeatedCards()
    {
        CardModel first = new StrikeDefect(), second = new StrikeDefect(), unavailable = new DefendDefect();
        CardModel hidden = new StrikeDefect();
        object enemyA = new(), enemyB = new();
        NativeSemanticAction Action(string key, string verb, CardModel card, string id, string target, object enemy) =>
            new(key, verb, id, card, new[] { new NativeSemanticOperand("target", target, enemy) }, "actual_native_catalog_fixture");
        NativeSemanticAction[] Actions(string firstKey, string secondKey) => new[]
        {
            Action(firstKey + "b", "play", first, "first-instance", "enemy-b", enemyB),
            Action(secondKey, "play", second, "second-instance", "enemy-a", enemyA),
            Action(firstKey + "a", "play", first, "first-instance", "enemy-a", enemyA),
            Action("0-hidden", "play", hidden, "hidden-instance", "enemy-a", enemyA),
            Action("0-other-verb", "use", unavailable, "unavailable", "enemy-a", enemyA)
        };
        foreach (var nativeActions in new[] { Actions("z", "a"), Actions("a", "z").Reverse().ToArray() })
        {
            var original = nativeActions.ToArray();
            var decision = new NativeCombatDecision("captured", "combat_play_phase", true,
                nativeActions, Array.Empty<string>(), null);
            var options = new List<VisibleCombatCommandOption>();
            CombatTurnSurfaceReader.AddCardOptions(options, decision, new[] { first, unavailable, second });
            Assert.Equal(new[] { "first-instance", "second-instance" }, options.Select(option => option.EntityId));
            Assert.Equal(new[] { "enemy-a", "enemy-b" }, options[0].TargetEntityIds);
            Assert.Equal(new[] { "enemy-a" }, options[1].TargetEntityIds);
            Assert.Equal(original, decision.Actions);
            Assert.Same(first, decision.Actions.Single(action => action.SubjectReferentId == "first-instance"
                && action.Operands[0].ReferentId == "enemy-b").NativeSubject);
            Assert.Same(enemyB, decision.Actions.Single(action => action.SubjectReferentId == "first-instance"
                && action.Operands[0].ReferentId == "enemy-b").Operands[0].NativeValue);
        }
    }
}
