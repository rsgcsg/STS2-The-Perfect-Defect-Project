using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.Models;
using STS2Connector.NativeUi;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Platform.NativeFoundation;

namespace STS2Connector.Host.Tests;

public sealed class NonCombatPotionExecutionTests
{
    [Fact]
    public void NativeFoulPotionNonCombatTargetIsNullNotThePlayer()
    {
        // Native type/validator check without creating a Godot merchant scene.
        // Its scene-specific custom guard is not bypassed or claimed live here.
        var potion = Bare<MegaCrit.Sts2.Core.Models.Potions.FoulPotion>();
        Assert.Equal(PotionUsage.AnyTime, potion.Usage);
        Assert.Equal(TargetType.TargetedNoCreature, potion.TargetType);
        Assert.True(potion.IsValidTarget(null));
        var creature = Bare<Creature>();
        Set(creature, "_currentHp", 30);
        Assert.False(potion.IsValidTarget(creature));
    }

    // The real PotionModel.IsValidTarget is exercised. Only the scene-dependent
    // custom availability is supplied by the fixture; this is not a live shop.
    private sealed class NodeTargetPotion : PotionModel
    {
        public bool CustomAvailable { get; set; }
        public override PotionRarity Rarity => PotionRarity.Event;
        public override PotionUsage Usage => PotionUsage.AnyTime;
        public override TargetType TargetType => TargetType.TargetedNoCreature;
        public override bool PassesCustomUsabilityCheck => CustomAvailable;
    }

    [Fact]
    public void NonCreatureUseHasExactExecutionMembershipWithoutInventedCreatureOperand()
    {
        var (player, potion, slots) = Fixture();
        var ids = new NativeEntityRegistry();
        Assert.True(potion.IsValidTarget(null));
        Assert.False(potion.IsValidTarget(player.Creature));
        var before = NativePotionUseDecisionProvider.CaptureNonCombat(player, ids);
        var action = Assert.Single(before.Actions);
        Assert.Equal("use", action.Verb);
        Assert.Same(potion, action.NativeSubject);
        Assert.Empty(action.Operands);
        Assert.Equal("exact_once", NativeSemanticActionCatalog.Describe(before.Actions,
            "UsePotionAction", "use", potion, new Dictionary<string, object>()).Membership);
        Assert.NotEqual("exact_once", NativeSemanticActionCatalog.Describe(before.Actions,
            "UsePotionAction", "use", potion,
            new Dictionary<string, object> { ["target"] = player.Creature }).Membership);
        Assert.NotEqual("exact_once", NativeSemanticActionCatalog.Describe(before.Actions,
            "UsePotionAction", "use", Bare<NodeTargetPotion>(), new Dictionary<string, object>()).Membership);

        Set(potion, "<IsQueued>k__BackingField", true, typeof(PotionModel));
        var execution = NativePotionUseDecisionProvider.CaptureNonCombat(player, ids);
        Assert.Equal(action.Key, Assert.Single(execution.Actions).Key);
        Assert.Equal("exact_once", NativeSemanticActionCatalog.Describe(execution.Actions,
            "UsePotionAction", "use", potion, new Dictionary<string, object>()).Membership);

        // Recompute current native custom validity; never borrow the accepted menu.
        potion.CustomAvailable = false;
        Assert.Empty(NativePotionUseDecisionProvider.CaptureNonCombat(player, ids).Actions);
        potion.CustomAvailable = true;
        slots[0] = null;
        Assert.Empty(NativePotionUseDecisionProvider.CaptureNonCombat(player, ids).Actions);
    }

    [Theory]
    [InlineData("custom")]
    [InlineData("permission")]
    [InlineData("dead")]
    [InlineData("removed")]
    public void NonCreatureUseKeepsIndependentNativeGuards(string blocker)
    {
        var (player, potion, slots) = Fixture();
        if (blocker == "custom") potion.CustomAvailable = false;
        if (blocker == "permission") player.CanUseOrRemovePotions = false;
        if (blocker == "dead") Set(player.Creature, "_currentHp", 0);
        if (blocker == "removed") slots[0] = null;
        Assert.Empty(NativePotionUseDecisionProvider.CaptureNonCombat(player, new NativeEntityRegistry()).Actions);
    }

    [Fact]
    public void ExecutionMembershipDoesNotSkipNativeTargetNodeSelection()
    {
        var (player, potion, _) = Fixture();
        var catalog = NativePotionUseDecisionProvider.CaptureNonCombat(player, new NativeEntityRegistry());
        Assert.Single(catalog.Actions);
        var direct = PotionPopupSurfaceReader.DirectUseActions(potion, catalog.Actions);
        Assert.Empty(direct);
        var surface = new PotionPopupSurface("potion_popup", "screen", "potion", "fixture", "Fixture", 0, true, true)
        {
            DirectCombatUse = direct.Length > 0,
        };
        var menu = NativeUiActionRuntime.DescribePotionPopupCommands(surface);
        Assert.Contains(menu, action => action.Kind == "choose_potion_use");
        Assert.DoesNotContain(menu, action => action.Kind == "use_potion");
    }

    private static (Player, NodeTargetPotion, List<PotionModel?>) Fixture()
    {
        var player = Bare<Player>();
        var creature = Bare<Creature>();
        var potion = Bare<NodeTargetPotion>();
        potion.CustomAvailable = true;
        Set(player, "<Creature>k__BackingField", creature);
        Set(creature, "<Player>k__BackingField", player);
        Set(creature, "_currentHp", 30);
        var slots = new List<PotionModel?> { potion, null };
        Set(player, "_potionSlots", slots);
        player.CanUseOrRemovePotions = true;
        return (player, potion, slots);
    }

    private static T Bare<T>() => (T)System.Runtime.CompilerServices.RuntimeHelpers.GetUninitializedObject(typeof(T));
    private static void Set(object target, string field, object value, Type? declaring = null) =>
        (declaring ?? target.GetType()).GetField(field,
            System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)!.SetValue(target, value);
}
