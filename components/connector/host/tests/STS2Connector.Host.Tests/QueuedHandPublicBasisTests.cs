using System.Text.Json;
using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.Host.Tests;

public sealed class QueuedHandPublicBasisTests
{
    private static VisibleCard Facts(string id) => new(id, "STRIKE_DEFECT", "Strike", "Attack", "2", null,
        "Current combat description", "Basic", false, false, new("ENCHANTMENT", "Current", "Current enchantment", 2, "card_hover_semantics"),
        "AnyEnemy", true, null) { CurrentPublicDetails = new JsonObject {
            ["keywords"] = new JsonArray(new JsonObject { ["name"] = "Current keyword", ["description"] = "Current text" }),
            ["card_previews"] = new JsonArray(new JsonObject { ["description"] = "Current preview" }) } };

    private static SnapshotBuildResult Build(CardModel[] hand, CardModel[] deliverable,
        IReadOnlyDictionary<CardModel, VisibleCard> facts, RewardPageSnapshotIdentity identity, bool executing)
    {
        var surface = new CombatTurnSurface("combat_turn", "room", !executing) {
            PlayableCards = executing ? Array.Empty<VisibleCombatCommandOption>() : deliverable.Select(card =>
                new VisibleCombatCommandOption(facts[card].EntityId, "Strike", new[] { "enemy" })).ToArray() };
        var context = new CombatLiveContext("combat", "normal", 1, "player", true,
            new("player", 0, 3, 3, null, hand.Select(card => facts[card]).ToArray(), 0, 0, 0, Array.Empty<VisibleStatus>(), Array.Empty<VisibleCombatCompanion>(),
                Array.Empty<VisibleCombatPotionState>(), Array.Empty<VisibleOrb>(), 0), Array.Empty<VisibleEnemy>());
        var game = new GameBuildIdentity("exact", "commit", null, 1, new("exact", true, true, true, "Fixture"));
        var observation = new LiveObservation(StableIdentityHash.Object(new { context, surface }), executing ? "settling" : "ready",
            context, surface, new("contract_complete_for_immediate_combat_turn", "native", Array.Empty<string>(), Array.Empty<string>()),
            game, Array.Empty<string>());
        return PlayerEnvironmentService.BuildSnapshot(inputProfile: PublicBasisContract.Profile,
            capturedInput: new(game, observation, new(false, null, null), identity) {
                Read = kind => kind == "combat_piles" ? PlayerReadBuildResult.Success(new(kind, "normal_player_read", "per_zone",
                    new CombatPilesReadContent(kind, Array.Empty<CombatPileReadZone>()), new("complete", Array.Empty<string>(), Array.Empty<string>())))
                    : PlayerReadBuildResult.Failure("unexpected_read", kind) });
    }

    [Fact]
    public void IdleNoQueueKeepsOriginalHandCatalogAndEveryFrozenReadByteIdentical()
    {
        CardModel first = new StrikeDefect(), second = new StrikeDefect(), invisible = new DefendDefect();
        CardModel[] logical = [first, second, invisible], oldVisible = [first, second];
        var facts = new Dictionary<CardModel, VisibleCard>(ReferenceEqualityComparer.Instance) { [first] = Facts("first"), [second] = Facts("second") };
        var identity = new RewardPageSnapshotIdentity();
        var before = Build(oldVisible, oldVisible, facts, identity, executing: false);
        var selected = CombatTurnSurfaceReader.IncludeVisibleQueuedHandCards(logical, oldVisible, Array.Empty<CardModel>());
        Assert.Equal(oldVisible, selected);
        var after = Build(selected, oldVisible, facts, identity, executing: false);
        // Both alternatives represent one controlled observation boundary. Only
        // the acquisition wall clock is fixed, not any public gameplay value.
        after = after with { Snapshot = after.Snapshot with { ObservedAt = before.Snapshot.ObservedAt } };
        Assert.Equal(PlayerEnvironmentService.FreezePublicBasis(before, null, DateTimeOffset.UnixEpoch).Bytes,
            PlayerEnvironmentService.FreezePublicBasis(after, null, DateTimeOffset.UnixEpoch).Bytes);
        Assert.Equal(JsonSerializer.Serialize(before.Snapshot.BoundActions, ConnectorMod._jsonOptions),
            JsonSerializer.Serialize(after.Snapshot.BoundActions, ConnectorMod._jsonOptions));
        Assert.NotEmpty(before.Snapshot.Reads);
    }

    [Fact]
    public void VisibleQueuedExactCombatInstanceHasCurrentHandDetailWithoutBecomingDeliverable()
    {
        CardModel first = new StrikeDefect(), queued = new StrikeDefect(), sameDefinitionClone = new StrikeDefect(), nonHand = new StrikeDefect();
        CardModel[] logical = [first, queued], holders = [first], queue = [queued, sameDefinitionClone, nonHand];
        var selected = CombatTurnSurfaceReader.IncludeVisibleQueuedHandCards(logical, holders, queue);
        Assert.Equal(logical, selected);
        var facts = new Dictionary<CardModel, VisibleCard>(ReferenceEqualityComparer.Instance) { [first] = Facts("first"), [queued] = Facts("queued") };
        var built = Build(selected, holders, facts, new RewardPageSnapshotIdentity(), executing: true);
        Assert.Empty(built.Snapshot.BoundActions.Actions);
        var opportunity = Assert.Single(built.Snapshot.Reads, read => read.Kind == "surface_card" && read.TargetReferentId == "queued");
        var resolution = PlayerEnvironmentService.ResolveReadMaterialization(built.Snapshot, opportunity.ReadId, built.Snapshot.SnapshotId, built.ReadBuilds);
        var read = PlayerEnvironmentService.BuildResolvedRead(built, resolution, built.Snapshot.SnapshotId,
            DateTimeOffset.UnixEpoch, includePublicCardDetails: true).Read!;
        Assert.Equal("complete", read.Completeness.Status);
        Assert.Equal("hand", read.Content["display_context"]!.GetValue<string>());
        Assert.Equal("Current combat description", read.Content["description"]!.GetValue<string>());
        Assert.Equal("2", read.Content["cost"]!.GetValue<string>());
        Assert.NotNull(read.Content["existing_enchantment"]);
        Assert.Equal("Current keyword", read.Content["keywords"]![0]!["name"]!.GetValue<string>());
        Assert.Equal("Current preview", read.Content["card_previews"]![0]!["description"]!.GetValue<string>());
        using var original = JsonDocument.Parse(PlayerEnvironmentService.FreezePublicBasis(built, null, DateTimeOffset.UnixEpoch).Bytes);
        Assert.Equal("complete", original.RootElement.GetProperty("capture_status").GetString());
        Assert.DoesNotContain(selected, card => ReferenceEquals(card, sameDefinitionClone));
        Assert.DoesNotContain(selected, card => ReferenceEquals(card, nonHand));
    }
}
