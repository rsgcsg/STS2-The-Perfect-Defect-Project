using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector.Tests;

public sealed class P1CaptureProjectionTests
{
    [Fact]
    public void SameDraftReadSelectorRefusesSettlingChildAndUnknownOwnersBeforeMaterialization()
    {
        var player = new VisibleCombatPlayer("player", 0, 3, 3, null, Array.Empty<VisibleCard>(), 0, 0, 0,
            Array.Empty<VisibleStatus>(), Array.Empty<VisibleCombatCompanion>(), Array.Empty<VisibleCombatPotionState>(),
            Array.Empty<VisibleOrb>(), 0);
        var context = new CombatLiveContext("combat", "normal", 1, "player", true, player, Array.Empty<VisibleEnemy>());
        var ready = new LiveObservation("public", "ready", context, new CombatTurnSurface("combat_turn", "room", true),
            new("public", "native", Array.Empty<string>(), Array.Empty<string>()),
            new("v0.111.0", "41cef1ea", null, 0, new("exact", true, true, true, "fixture")), Array.Empty<string>());
        Assert.Equal(new[] { "run_deck", "combat_piles" }, PlayerEnvironmentService.P1CaptureReadKinds(ready));
        Assert.Empty(PlayerEnvironmentService.P1CaptureReadKinds(ready with { Readiness = "settling" }));
        Assert.Empty(PlayerEnvironmentService.P1CaptureReadKinds(ready with { Context = context with { IsPlayPhase = false } }));
        Assert.Empty(PlayerEnvironmentService.P1CaptureReadKinds(ready with { InputOwnership = new("none_fail_closed", null, "fixture") }));
        Assert.Empty(PlayerEnvironmentService.P1CaptureReadKinds(ready with
        { Surface = new UnsupportedSurface("card_selection", "child", "not an ordinary ready combat owner") }));
    }

    [Fact]
    public void ProductionCapabilitiesAdvertiseOnlyTheBoundedReadOnlyFoundation()
    {
        P1CaptureCapabilities capabilities = PlayerEnvironmentService.GetP1CaptureCapabilities();
        Assert.False(capabilities.AgentAdmissible);
        Assert.False(capabilities.CreatesMutationAuthority);
        Assert.Equal(P1CaptureContract.MaxReadInstances, capabilities.MaxReadInstances);
        Assert.Equal(P1CaptureContract.Scope, capabilities.SupportedCaptureScope);
        Assert.Contains("current_act_map", capabilities.RequiredFullP1Missing);
        Assert.Contains("p1_semantic_catalog", capabilities.RequiredFullP1Missing);
        Assert.Equal(P1CaptureContract.CaptureSchema, capabilities.Retention.CaptureSchema);
        Assert.Equal(P1CaptureContract.Route + "/current", capabilities.Retention.CurrentRoute);
    }

    [Fact]
    public void HiddenDrawPermutationDoesNotEnterPublicReadOrderAliasesOrFrozenPackage()
    {
        // Production assigns opaque entropy aliases, then sorts only public
        // card values. Exact same objects keep aliases across hidden reorder.
        var registry = new NativeEntityRegistry();
        object[] native = { new(), new(), new() };
        var cards = native.Select((card, index) => new VisibleCard(registry.GetId(card, "card"),
            index == 0 ? "DEFEND" : "STRIKE", "Visible", "Attack", "1", null,
            "Current public description", "Basic", false, false, null)).ToArray();
        var before = PlayerVisibleReadBuilder.OrderPublicReadCards(cards);
        var after = PlayerVisibleReadBuilder.OrderPublicReadCards(cards.Reverse());
        Assert.Equal(before, after);
        Assert.NotEqual(before[1].EntityId, before[2].EntityId); // Duplicate definitions are not merged.
        foreach (object original in native)
            Assert.Same(original, registry.CaptureExactReferences(new[] { registry.GetId(original, "card") }).Single().Value);

        var options = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
        var basis = new JsonObject
        {
            ["schema"] = PlayerEnvironmentContract.SnapshotSchema, ["snapshot_id"] = "same-capture-boundary",
            ["session"] = new JsonObject { ["runtime_instance_id"] = "runtime", ["environment_fingerprint"] = "environment" },
            ["information_policy"] = new JsonObject { ["includes_hidden_information"] = false }
        };
        FrozenPublicObservation Freeze(VisibleCard[] values)
        {
            var read = new P1CapturedRead("same-read-instance", "combat_piles", "read:combat_piles", null,
                "combat_piles", null, "available", "sts2.player-environment/read/combat_piles-1",
                JsonSerializer.SerializeToNode(values, options), Array.Empty<string>());
            return P1CaptureAssembly.Freeze(basis, new[] { read },
                new[] { new P1CaptureCoverage("draw", true, "available", new[] { read.InstanceId }, Array.Empty<string>()) },
                true, null, options, DateTimeOffset.Parse("2026-10-15T00:00:00+00:00"));
        }
        Assert.Equal(Freeze(before).Bytes, Freeze(after).Bytes);
    }

    [Fact]
    public void CombatPileReadPreservesNativeDiscardAndExhaustGridOrder()
    {
        VisibleCard Card(string id, string definition) => new(id, definition, definition, "Skill", "1", null,
            "Visible", "Common", false, false, null);
        VisibleCard[] cards = { Card("first", "ZAP"), Card("second", "DEFEND"), Card("third", "ZAP") };
        foreach (var pile in new[] { MegaCrit.Sts2.Core.Entities.Cards.PileType.Discard,
            MegaCrit.Sts2.Core.Entities.Cards.PileType.Exhaust })
        {
            Assert.Equal(cards, PlayerVisibleReadBuilder.OrderCombatPileCards(pile, cards));
            Assert.Equal(cards.Reverse(), PlayerVisibleReadBuilder.OrderCombatPileCards(pile, cards.Reverse()));
            Assert.NotEqual(PlayerVisibleReadBuilder.OrderCombatPileCards(pile, cards),
                PlayerVisibleReadBuilder.OrderCombatPileCards(pile, cards.Reverse()));
        }
        Assert.Equal(PlayerVisibleReadBuilder.OrderCombatPileCards(MegaCrit.Sts2.Core.Entities.Cards.PileType.Draw, cards),
            PlayerVisibleReadBuilder.OrderCombatPileCards(MegaCrit.Sts2.Core.Entities.Cards.PileType.Draw, cards.Reverse()));
    }
}
