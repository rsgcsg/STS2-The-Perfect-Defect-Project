using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Platform.NativeFoundation;

namespace STS2Connector.Host.Tests;

public sealed class CombatCommandPresentationTests
{
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
