using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.Host.Tests;

public sealed class PublicCardDetailCompatibilityTests
{
    private static VisibleCard Card() => new("card", "NATIVE_CARD", "Native card", "Skill", "1", null,
        "Native description", "Common", false, false, null);
    private static SnapshotBuildResult Build(VisibleCard card, bool outer = false, bool enriched = false)
    {
        ILiveSurface surface = outer
            ? new RewardClaimSurface("reward_claim", "screen", new[] {
                new VisibleReward("reward", "card", "Cards", null, true) { CardOptions = new[] { card } }
            }, false, Array.Empty<VisibleCombatPotion>(), true, false)
            : new CardRewardSelectionSurface("card_reward_selection", "screen", new[] { card }, Array.Empty<VisibleCardRewardAlternative>());
        var entry = PlayerEnvironmentService.BuildLinkedDetailCatalog(surface, includePublicCardDetails: enriched).Single();
        var read = new PlayerEnvironmentReadOpportunity("read:surface_card:" + (entry.DetailKey ?? entry.EntityId), "surface_card", "card",
            "sts2.player-environment/read/surface_card-1", "normal_player_visible_surface_card", true,
            "single_entity", Array.Empty<string>());
        var game = new GameBuildIdentity("exact", "commit", null, 1, new("exact", true, true, true, "Fixture"));
        var host = new LiveObservation("source", "ready", new MenuLiveContext("menu", "fixture"), surface,
            new("complete", "native", Array.Empty<string>(), Array.Empty<string>()), game, Array.Empty<string>());
        var page = new PlayerEnvironmentSnapshot("1.0.0", PlayerEnvironmentContract.SnapshotSchema, "snapshot", 1,
            DateTimeOffset.UnixEpoch, "interactive", null,
            new("room", "reward_claim", "ready", null, "fixture", new(new JsonObject(), new JsonObject()), Array.Empty<PlayerEnvironmentInteractionCapability>()),
            Array.Empty<PlayerEnvironmentReferent>(), new("fixture", "complete", 0, 0, 512, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
            new[] { read }, new("complete", "complete", "native", Array.Empty<string>(), Array.Empty<string>()),
            new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));
        return new(page, host, new Dictionary<string, PlayerEnvironmentNativeBinding>(), new Dictionary<string, PlayerReadBuildResult>());
    }
    private static PlayerEnvironmentReadResponse Read(SnapshotBuildResult snapshot, bool enriched, string? readId = null)
    {
        var opportunity = snapshot.Snapshot.Reads.Single(value => readId == null || value.ReadId == readId);
        var resolution = PlayerEnvironmentService.ResolveReadMaterialization(snapshot.Snapshot, opportunity.ReadId,
            snapshot.Snapshot.SnapshotId, snapshot.ReadBuilds);
        return PlayerEnvironmentService.BuildResolvedRead(snapshot, resolution, snapshot.Snapshot.SnapshotId,
            DateTimeOffset.UnixEpoch, includePublicCardDetails: enriched).Read!;
    }
    [Fact]
    public void CapturedMetadataDoesNotChangeLegacyCardOrReadBytesAndOuterCardsHaveTheSameDetailOwner()
    {
        var plain = Card();
        var detailed = plain with { CurrentPublicDetails = new JsonObject { ["keywords"] = new JsonArray(), ["card_previews"] = new JsonArray() } };
        Assert.Equal(JsonSerializer.Serialize(plain, ConnectorMod._jsonOptions), JsonSerializer.Serialize(detailed, ConnectorMod._jsonOptions));
        var oldRead = Read(Build(plain), false); var withMetadata = Read(Build(detailed), false);
        Assert.Equal(JsonSerializer.Serialize(oldRead, ConnectorMod._jsonOptions), JsonSerializer.Serialize(withMetadata, ConnectorMod._jsonOptions));
        Assert.Equal(new[] { "card" }, PlayerEnvironmentService.BuildLinkedDetailCatalog(Build(detailed, outer: true, enriched: true).HostObservation.Surface,
            includePublicCardDetails: true).Select(entry => entry.EntityId));
        var enriched = Read(Build(detailed, outer: true, enriched: true), true);
        Assert.Equal("complete", enriched.Completeness.Status);
        Assert.Equal("1", enriched.Content["public_details_revision"]!.GetValue<string>());
        Assert.IsType<JsonArray>(enriched.Content["keywords"]); Assert.IsType<JsonArray>(enriched.Content["card_previews"]);
        Assert.Equal(withMetadata.ContentSchema, enriched.ContentSchema);
        Assert.Equal(DateTimeOffset.UnixEpoch, enriched.ObservedAt);
    }
    [Fact]
    public void AnEnrichedDetailCannotClaimCompleteWithoutTheActualCapturedNativeFields()
    {
        var missing = Read(Build(Card(), enriched: true), true);
        Assert.Equal("partial", missing.Completeness.Status);
        Assert.Contains("current_native_public_card_details_not_captured", missing.Completeness.Missing);
        var partial = Read(Build(Card() with { CurrentPublicDetails = new JsonObject { ["keywords"] = new JsonArray() },
            CurrentPublicDetailsMissing = new[] { "native_hover_card_unqualified" } }, enriched: true), true);
        Assert.Equal("partial", partial.Completeness.Status);
        Assert.Contains("native_hover_card_unqualified", partial.Completeness.Missing);
    }
    [Fact]
    public void CapturedHandDeckAndPileKeepSeparateDisplayInstancesForTheSameEntity()
    {
        VisibleCard In(string display) => Card() with { Description = display, CurrentPublicDetails = new JsonObject {
            ["keywords"] = new JsonArray(new JsonObject { ["name"] = display, ["description"] = display }), ["card_previews"] = new JsonArray() } };
        var baseline = Build(Card());
        var surface = new CombatTurnSurface("combat_turn", "room", true);
        var context = new CombatLiveContext("combat", "normal", 1, "player", true,
            new("player", 0, 3, 3, null, new[] { In("hand") }, 1, 0, 0, Array.Empty<VisibleStatus>(),
                Array.Empty<VisibleCombatCompanion>(), Array.Empty<VisibleCombatPotionState>(), Array.Empty<VisibleOrb>(), 0), Array.Empty<VisibleEnemy>());
        var builds = new Dictionary<string, PlayerReadBuildResult> {
            ["run_deck"] = PlayerReadBuildResult.Success(new("run_deck", "normal", "unordered_multiset",
                new RunDeckReadContent("run_deck", 1, new[] { In("deck") }), new("complete", Array.Empty<string>(), Array.Empty<string>()))),
            ["combat_piles"] = PlayerReadBuildResult.Success(new("combat_piles", "normal", "native_visible_order",
                new CombatPilesReadContent("combat_piles", new[] { new CombatPileReadZone("draw", 1, "unordered_multiset", new[] { In("draw") }) }),
                new("complete", Array.Empty<string>(), Array.Empty<string>()))) };
        var catalog = PlayerEnvironmentService.BuildLinkedDetailCatalog(surface, context, builds, includePublicCardDetails: true);
        Assert.Equal(3, catalog.Count); Assert.Equal(3, catalog.Select(value => value.DetailKey).Distinct().Count());
        Assert.Empty(PlayerEnvironmentService.BuildLinkedDetailCatalog(surface));
        var snapshot = baseline with { HostObservation = baseline.HostObservation with { Surface = surface, Context = context }, ReadBuilds = builds,
            Snapshot = baseline.Snapshot with { Reads = catalog.Select(entry => new PlayerEnvironmentReadOpportunity(
                "read:surface_card:" + entry.DetailKey, entry.Kind, entry.EntityId, "sts2.player-environment/read/surface_card-1",
                entry.VisibilityBasis, true, "single_entity", Array.Empty<string>())).ToArray() } };
        foreach (var entry in catalog)
        {
            var detail = Read(snapshot, true, "read:surface_card:" + entry.DetailKey);
            Assert.Equal(entry.DisplayContext, detail.Content["display_context"]!.GetValue<string>());
            Assert.Equal(entry.DisplayContext, detail.Content["description"]!.GetValue<string>());
            Assert.Equal(entry.DisplayContext, detail.Content["keywords"]![0]!["name"]!.GetValue<string>());
        }
    }

}
