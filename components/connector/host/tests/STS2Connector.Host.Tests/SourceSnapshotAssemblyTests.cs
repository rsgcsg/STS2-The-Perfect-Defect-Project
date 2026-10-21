using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector.Host.Tests;

public sealed class SourceSnapshotAssemblyTests
{
    private static PlayerEnvironmentSnapshot Page(string id) => new("1.0.0", PlayerEnvironmentContract.SnapshotSchema,
        id, 1, DateTimeOffset.UnixEpoch, "observed", null,
        new("owner", "synthetic", "ready", null, "sts2.player-environment/surface/synthetic-1",
            new(new JsonObject { ["kind"] = "synthetic" }, new JsonObject()), Array.Empty<PlayerEnvironmentInteractionCapability>()),
        Array.Empty<PlayerEnvironmentReferent>(),
        new("sts2.player-environment/bound-actions-1", "complete", 0, 0, 512, "finite", Array.Empty<PlayerEnvironmentBoundAction>()),
        Array.Empty<PlayerEnvironmentReadOpportunity>(), new("complete", "public", "complete", Array.Empty<string>(), Array.Empty<string>()),
        new("source-runtime", "source-environment"), new("player_visible_v1", "current_page", false, "omit"));
    private static SnapshotBuildResult Built(PlayerEnvironmentSnapshot page) => new(page, null!,
        new Dictionary<string, PlayerEnvironmentNativeBinding>(), new Dictionary<string, STS2Connector.LiveHost.PlayerReadBuildResult>());

    [Fact]
    public void PartialOriginalNavigationRemainsStoredAndMakesTheLogicalBasisPartial()
    {
        var page = Page("partial-popup");
        var raw = Built(page with { Completeness = page.Completeness with { Status = "partial", Missing = new[] { "original_missing" } } });
        var frozen = PlayerEnvironmentService.FreezePublicBasis(Built(Page("logical-base")) with { RawNativeNavigation = raw }, "same-run");
        using var body = JsonDocument.Parse(frozen.Bytes);
        Assert.Equal("partial", body.RootElement.GetProperty("capture_status").GetString());
        var snapshot = body.RootElement.GetProperty("snapshot");
        Assert.Equal("partial", snapshot.GetProperty("raw_native_navigation_basis").GetProperty("capture_status").GetString());
        Assert.Contains("raw_native_navigation_basis_incomplete", snapshot.GetProperty("completeness").GetProperty("missing").EnumerateArray().Select(item => item.GetString()));
    }

    [Fact]
    public void FoldedNavigationRetainsTheExactOriginalPopupPublicBasisSeparately()
    {
        var raw = Built(Page("original-popup"));
        var current = Built(Page("logical-base")) with { RawNativeNavigation = raw };
        var frozen = PlayerEnvironmentService.FreezePublicBasis(current, "same-run");
        using var body = JsonDocument.Parse(frozen.Bytes);
        var snapshot = body.RootElement.GetProperty("snapshot");
        Assert.Equal("logical-base", snapshot.GetProperty("snapshot_id").GetString());
        var original = snapshot.GetProperty("raw_native_navigation_basis");
        Assert.Equal("complete", original.GetProperty("capture_status").GetString());
        Assert.Equal("original-popup", original.GetProperty("snapshot").GetProperty("snapshot_id").GetString());
        using var expected = JsonDocument.Parse(PlayerEnvironmentService.FreezePublicBasis(raw, "same-run", raw.Snapshot.ObservedAt).Bytes);
        Assert.True(JsonNode.DeepEquals(JsonNode.Parse(expected.RootElement.GetProperty("snapshot").GetRawText()),
            JsonNode.Parse(original.GetProperty("snapshot").GetRawText())));
    }

    [Fact]
    public void CardRewardSourceWitnessUsesOnlyTheExactCapturedSubjectWithoutChangingPublicActionOrDispatch()
    {
        object owner = new(), card = new(), alternative = new(), humanOwner = new();
        var human = new TextMenuNativeWitnessBinding(humanOwner, card, new Dictionary<string, object>());
        TextMenuLeaf Leaf(string key, string verb, string id) => new(key, "root", verb, key, id,
            Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => throw new InvalidOperationException("must not dispatch"), human);
        var frame = new TextMenuFrame(Page("witness-original"), "owner", new[] {
            Leaf("card", "select", "card-original"), Leaf("alternative", "select", "alternative-original"),
            Leaf("foreign", "select", "not-in-captured-roster"), Leaf("inspect", "inspect", "card-original") });
        var joined = NativeLogicalCardRewardCapture.AttachSourceSelectionWitnesses(frame, owner,
            new Dictionary<string, object> { ["card-original"] = card, ["alternative-original"] = alternative });
        Assert.Same(owner, joined.Leaves[0].NativeSourceWitness!.Owner);
        Assert.Same(card, joined.Leaves[0].NativeSourceWitness!.Subject);
        Assert.Same(alternative, joined.Leaves[1].NativeSourceWitness!.Subject);
        Assert.Null(joined.Leaves[2].NativeSourceWitness); Assert.Null(joined.Leaves[3].NativeSourceWitness);
        for (int index = 0; index < frame.Leaves.Count; index++)
        {
            Assert.Same(frame.Leaves[index].Dispatch, joined.Leaves[index].Dispatch);
            Assert.Same(human, joined.Leaves[index].NativeWitness);
            Assert.Equal(JsonSerializer.Serialize(frame.Leaves[index], ConnectorMod._jsonOptions),
                JsonSerializer.Serialize(joined.Leaves[index], ConnectorMod._jsonOptions));
        }
    }

    [Fact]
    public void SourceAssemblyMaterializesTheExactCapturedDisplayDetailInsteadOfLegacyEntityLookup()
    {
        var card = new VisibleCard("card-original", "NATIVE_CARD", "Native card", "Skill", "1", null,
            "Native description", "Common", false, false, null) {
            CurrentPublicDetails = new JsonObject { ["keywords"] = new JsonArray(new JsonObject {
                ["name"] = "Captured keyword", ["description"] = "Captured body" }), ["card_previews"] = new JsonArray() } };
        var surface = new CardRewardSelectionSurface("card_reward_selection", "screen-original", new[] { card },
            Array.Empty<VisibleCardRewardAlternative>());
        var entry = Assert.Single(PlayerEnvironmentService.BuildLinkedDetailCatalog(surface, includePublicCardDetails: true));
        string originalReadId = "read:surface_card:" + entry.DetailKey;
        var ordinary = Page("ordinary-detail-original") with { Reads = new[] {
            new PlayerEnvironmentReadOpportunity(originalReadId, "surface_card", card.EntityId,
                "sts2.player-environment/read/surface_card-1", entry.VisibilityBasis, true, "single_entity", Array.Empty<string>()) } };
        var game = new GameBuildIdentity("exact", "commit", null, 1, new("exact", true, true, true, "Fixture"));
        var host = new LiveObservation("source", "ready", new MenuLiveContext("menu", "fixture"), surface,
            new("complete", "native", Array.Empty<string>(), Array.Empty<string>()), game, Array.Empty<string>());
        var built = Built(ordinary) with { HostObservation = host };
        var actual = new TextMenuFrame(Page("native-detail-original"), "native-owner", Array.Empty<TextMenuLeaf>());
        var captured = PlayerEnvironmentService.AssembleSourceCapture(new(built, Built(actual.Page)), null, () => actual);
        using var body = JsonDocument.Parse(captured.PublicBasis!.Bytes);
        Assert.Equal("complete", body.RootElement.GetProperty("capture_status").GetString());
        var read = Assert.Single(body.RootElement.GetProperty("reads").EnumerateArray());
        Assert.Equal(originalReadId, read.GetProperty("read_id").GetString());
        var content = read.GetProperty("response").GetProperty("content");
        Assert.Equal("surface:card_reward_selection", content.GetProperty("display_context").GetString());
        Assert.Equal("Captured keyword", content.GetProperty("keywords")[0].GetProperty("name").GetString());
    }

    [Fact]
    public void NativeExtractionFailureKeepsTheSameFrozenOrdinaryOriginalAndOnlyPublishesANegativeNativePart()
    {
        var ordinary = Page("ordinary-source"); var native = Page("private-native-preparation"); int nativeCalls = 0;
        var captured = PlayerEnvironmentService.AssembleSourceCapture(new(Built(ordinary), Built(native)), "original-continuity",
            () => { nativeCalls++; throw new InvalidOperationException("synthetic_native_read_failure"); });
        Assert.Equal(1, nativeCalls); Assert.NotNull(captured.PublicBasis); Assert.Null(captured.PublicMissingReason);
        Assert.Equal(DateTimeOffset.UnixEpoch, captured.PublicBasis.CapturedAt);
        using var bytes = JsonDocument.Parse(captured.PublicBasis.Bytes);
        Assert.Equal("ordinary-source", bytes.RootElement.GetProperty("snapshot").GetProperty("snapshot_id").GetString());
        Assert.Equal("partial", captured.Frame.Page.Completeness.Status); Assert.Empty(captured.Frame.Leaves);
        Assert.Contains("source_native_frame_capture_failed", captured.Frame.Page.Completeness.Missing);
        Assert.Equal("original-continuity", captured.Frame.GameContinuityId);
    }

    [Fact]
    public void RefusedNeutralFormatDoesNotDropTheActualNativeFrameOrBorrowAnEarlierNeutralOriginal()
    {
        var ordinary = Page("ordinary-invalid") with { Schema = "foreign-snapshot-format" };
        var actual = new TextMenuFrame(Page("native-original"), "actual-private-owner", Array.Empty<TextMenuLeaf>());
        int nativeCalls = 0;
        var captured = PlayerEnvironmentService.AssembleSourceCapture(new(Built(ordinary), Built(actual.Page)), null,
            () => { nativeCalls++; return actual; });
        Assert.Equal(1, nativeCalls); Assert.Null(captured.PublicBasis); Assert.Equal("invalid_public_basis", captured.PublicMissingReason);
        Assert.Equal(actual.Page, captured.Frame.Page); Assert.Equal(actual.OwnerKey, captured.Frame.OwnerKey);
    }
}
