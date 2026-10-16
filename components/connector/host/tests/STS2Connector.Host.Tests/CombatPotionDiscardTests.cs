using System.Reflection;
using System.Text.Json;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.Models;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;

namespace STS2Connector.Host.Tests;

public sealed class CombatPotionDiscardTests
{
    private sealed class AutomaticPotion : PotionModel
    {
        public override PotionRarity Rarity => PotionRarity.Common;
        public override PotionUsage Usage => PotionUsage.Automatic;
        public override TargetType TargetType => TargetType.Self;
        public override bool PassesCustomUsabilityCheck => false;
    }

    [Fact]
    public void NativeDiscardGuardsDoNotBorrowPotionUseOrRewardCapacity()
    {
        var player = Bare<Player>();
        var creature = Bare<Creature>();
        var potion = Bare<AutomaticPotion>();
        Set(player, "<Creature>k__BackingField", creature);
        Set(creature, "_currentHp", 30);
        var slots = new List<PotionModel?> { potion, null };
        Set(player, "_potionSlots", slots);
        player.CanUseOrRemovePotions = true;
        Assert.False(potion.PassesCustomUsabilityCheck);
        Assert.True(CombatTurnSurfaceReader.CanDiscardPotion(player, potion, 0));

        Set(potion, "<IsQueued>k__BackingField", true, typeof(PotionModel));
        Assert.False(CombatTurnSurfaceReader.CanDiscardPotion(player, potion, 0));
        Set(potion, "<IsQueued>k__BackingField", false, typeof(PotionModel));
        player.CanUseOrRemovePotions = false;
        Assert.False(CombatTurnSurfaceReader.CanDiscardPotion(player, potion, 0));
        player.CanUseOrRemovePotions = true;
        Set(creature, "_currentHp", 0);
        Assert.False(CombatTurnSurfaceReader.CanDiscardPotion(player, potion, 0));
        Set(creature, "_currentHp", 30);
        slots[0] = Bare<AutomaticPotion>();
        Assert.False(CombatTurnSurfaceReader.CanDiscardPotion(player, potion, 0));
        slots[1] = potion;
        Assert.False(CombatTurnSurfaceReader.CanDiscardPotion(player, potion, 0));
        Assert.True(CombatTurnSurfaceReader.CanDiscardPotion(player, potion, 1));
        Assert.False(CombatTurnSurfaceReader.CanDiscardPotion(player, potion, -1));
        Assert.False(CombatTurnSurfaceReader.CanDiscardPotion(player, potion, 2));
    }

    [Fact]
    public void CombatDiscardBindsExactSlotPrivatelyAndProjectsAnIndependentFiniteAction()
    {
        CombatTurnSurface Surface(int slot) => new("combat_turn", "room", false)
        {
            DiscardablePotions = new[] { new VisibleCombatPotion("potion", "REVIVE", "Revive", "Revive on death",
                slot, "Self", CanUse: false, Automatic: true) }
        };
        NativeUiBoundAction Binding(CombatTurnSurface surface) => Assert.Single(NativeUiActionRuntime.BuildBindings(
            new("s", "ready", null!, surface, null!, null!, Array.Empty<string>())));
        var first = Binding(Surface(0));
        Assert.Equal("discard_combat_potion", first.Candidate.Operation);
        Assert.Equal("activate_control", first.Candidate.Command);
        Assert.Equal("potion", first.Candidate.Operands["potion_id"]);
        Assert.Equal("0", first.Candidate.Operands["potion_slot"]);
        Assert.NotEqual(first.Candidate.CandidateId, Binding(Surface(1)).Candidate.CandidateId);

        var facts = PlayerEnvironmentService.ProjectFactReferents(JsonSerializer.SerializeToNode(Surface(0),
            new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower })!);
        var projection = PlayerEnvironmentService.ProjectBoundActions(new[] { first }, "interaction", facts);
        var action = Assert.Single(projection.Projection.Actions);
        Assert.Equal("activate", action.Verb);
        Assert.Equal("potion", action.SubjectReferentId);
        Assert.Empty(action.Arguments);
        Assert.Equal("discardable_potion", facts["potion"].Role);
    }

    private static T Bare<T>() => (T)System.Runtime.CompilerServices.RuntimeHelpers.GetUninitializedObject(typeof(T));
    private static void Set(object target, string field, object value, Type? declaring = null) =>
        (declaring ?? target.GetType()).GetField(field, BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(target, value);
}
