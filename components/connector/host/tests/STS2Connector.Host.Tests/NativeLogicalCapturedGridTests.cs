using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.Json.Nodes;
using HarmonyLib;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Localization;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector;

[Collection("Native detached preview isolation")]
public sealed class NativeLogicalCapturedGridTests
{
    private static T Bare<T>() where T : class => (T)RuntimeHelpers.GetUninitializedObject(typeof(T));
    private static (SnapshotBuildResult Snapshot, NativeLogicalCapturedGrid Grid) Captured()
    {
        CardModel[] originals = Enumerable.Range(0, 64).Select(_ => (CardModel)new DefendDefect()).ToArray();
        var state = new NativeLogicalGridState(Bare<NDeckCardSelectScreen>(), Bare<NCardGrid>(),
            new TaskCompletionSource<IEnumerable<CardModel>>().Task, originals, new[] { originals[63] },
            new(new LocString("card_selection", "TO_DISCARD"), 0, 64), "selecting", PileType.Deck,
            Bare<NPeekButton>(), false, originals, new Dictionary<string, NButton>());
        var cards = originals.Select((_, index) => new VisibleCard("card-" + index, "DEFEND_DEFECT", "Captured " + index,
            "Skill", "1", null, "Frozen description", "Starter", false, index == 63, null)).ToArray();
        var rows = new JsonArray(cards.Select(card => (JsonNode)new JsonObject {
            ["entity_id"] = card.EntityId, ["description"] = card.Description }).ToArray());
        var referents = cards.Select(card => new PlayerEnvironmentReferent(card.EntityId, "card", "entity", card.Name,
            new(true, true, card.IsSelected, false, "native_logical_public_list"), "fixture", new JsonObject {
                ["entity_id"] = card.EntityId, ["description"] = card.Description })).ToArray();
        var input = new NativeLogicalGridInputSpec("request-original:card-63:deselect", "logical_grid_card:card-63:deselect",
            "request-original", "deselect", "deselect", "selecting", "Deselect captured card", "card-63",
            NativeLogicalGridInputKind.Toggle, state, originals[63], true);
        var neutral = new NativePublicBasisGridSurface("native_deck_card_selection", "selecting", "owner", "request-original", cards)
        { Inputs = new[] { input }, Referents = referents, PublicSurface = new JsonObject { ["cards"] = rows.DeepClone() } };
        var grid = new NativeLogicalCapturedGrid(state, "owner", "request-original", new JsonObject {
            ["kind"] = neutral.Kind, ["stage"] = neutral.Stage, ["cards"] = rows }, referents,
            new[] { input }, Array.Empty<string>(), "Frozen prompt", neutral);
        var game = new GameBuildIdentity("exact", "commit", null, 1, new("exact", true, true, true, "Fixture"));
        var host = new LiveObservation("source", "ready", new MenuLiveContext("menu", "fixture"),
            new UnsupportedSurface(neutral.Kind, nameof(NDeckCardSelectScreen), "holder window unavailable"),
            new("complete", "native", Array.Empty<string>(), Array.Empty<string>()), game, Array.Empty<string>());
        var page = new PlayerEnvironmentSnapshot("1.0.0", PlayerEnvironmentContract.SnapshotSchema, "snapshot", 1,
            DateTimeOffset.UnixEpoch, "interactive", null,
            new("owner", neutral.Kind, "selecting", null, "fixture", new(new JsonObject(), new JsonObject()), Array.Empty<PlayerEnvironmentInteractionCapability>()),
            Array.Empty<PlayerEnvironmentReferent>(), new("fixture", "complete", 0, 0, 512, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
            Array.Empty<PlayerEnvironmentReadOpportunity>(), new("complete", "complete", "native", Array.Empty<string>(), Array.Empty<string>()),
            new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));
        return (new(page, host, new Dictionary<string, PlayerEnvironmentNativeBinding>(), new Dictionary<string, PlayerReadBuildResult>()), grid);
    }

    private static int unexpectedCalls;
    private static bool UnexpectedCapture(ref NativeLogicalGridState? __result) { unexpectedCalls++; __result = null; return false; }
    private static bool UnexpectedTitle(ref string __result) { unexpectedCalls++; __result = "Late getter"; return false; }

    [Fact]
    public void NativeCompositionUsesAllFrozenOriginalRowsAndNeverRecapturesOrRenders()
    {
        var (snapshot, grid) = Captured();
        var harmony = new Harmony("frozen-grid-compose-" + Guid.NewGuid().ToString("N"));
        const BindingFlags flags = BindingFlags.Static | BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
        MethodInfo capture = typeof(NativeLogicalGridState).GetMethod(nameof(NativeLogicalGridState.Capture), flags)!;
        MethodInfo title = typeof(CardModel).GetProperty(nameof(CardModel.Title))!.GetMethod!;
        harmony.Patch(capture, prefix: new HarmonyMethod(typeof(NativeLogicalCapturedGridTests).GetMethod(nameof(UnexpectedCapture), flags)!));
        harmony.Patch(title, prefix: new HarmonyMethod(typeof(NativeLogicalCapturedGridTests).GetMethod(nameof(UnexpectedTitle), flags)!));
        unexpectedCalls = 0;
        try
        {
            var frame = NativeLogicalGridCapture.ComposeNativeFrame(snapshot, snapshot.Snapshot, Array.Empty<TextMenuLeaf>(),
                new(true, grid, Array.Empty<string>()))!;
            Assert.Equal(0, unexpectedCalls); Assert.Same(grid.State, frame.LogicalGridProof);
            Assert.Equal(64, frame.Page.Interaction.Content.Surface["cards"]!.AsArray().Count);
            Assert.Equal(64, frame.Page.Referents.Count); Assert.Single(frame.Leaves);
            Assert.Equal("card-63", frame.Leaves[0].SubjectReferentId); Assert.Equal("deselect", frame.Leaves[0].Verb);
            Assert.Equal("logical_grid:owner:request-original", frame.OwnerKey);
            Assert.Equal("Frozen prompt", frame.Page.Interaction.Prompt);
        }
        finally { harmony.Unpatch(capture, HarmonyPatchType.Prefix, harmony.Id); harmony.Unpatch(title, HarmonyPatchType.Prefix, harmony.Id); }
    }

    [Fact]
    public void AttemptedNullIsFinalForThisCaptureAndMissingRemainsPartial()
    {
        var (snapshot, _) = Captured();
        Assert.Null(NativeLogicalGridCapture.ComposeNativeFrame(snapshot, snapshot.Snapshot, Array.Empty<TextMenuLeaf>(), new(true, null, Array.Empty<string>())));
        var failed = NativeLogicalGridCapture.ComposeNativeFrame(snapshot, snapshot.Snapshot, Array.Empty<TextMenuLeaf>(),
            new(true, null, new[] { "original_grid_binding_unavailable" }))!;
        Assert.Empty(failed.Leaves); Assert.Equal("partial", failed.Page.Completeness.Status);
        Assert.Contains("original_grid_binding_unavailable", failed.Page.Completeness.Missing);
        Assert.Throws<InvalidOperationException>(() => NativeLogicalGridCapture.ComposeNativeFrame(snapshot, snapshot.Snapshot,
            Array.Empty<TextMenuLeaf>(), NativeLogicalGridCaptureResult.NotAttempted));
    }

    [Fact]
    public void NativeProjectionCannotMutateFrozenFactsOrEraseUnrelatedOmissions()
    {
        var (snapshot, grid) = Captured();
        snapshot = snapshot with { Snapshot = snapshot.Snapshot with { Completeness = snapshot.Snapshot.Completeness with {
            Status = "partial", Missing = new[] { "persistent_public_context_missing" } } } };
        var capture = new NativeLogicalGridCaptureResult(true, grid, Array.Empty<string>());
        var first = NativeLogicalGridCapture.ComposeNativeFrame(snapshot, snapshot.Snapshot, Array.Empty<TextMenuLeaf>(), capture)!;
        first.Page.Interaction.Content.Surface["cards"]![0]!["description"] = "Changed output only";
        first.Page.Referents[0].Properties!["description"] = "Changed output referent only";
        var second = NativeLogicalGridCapture.ComposeNativeFrame(snapshot, snapshot.Snapshot, Array.Empty<TextMenuLeaf>(), capture)!;
        Assert.Equal("Frozen description", second.Page.Interaction.Content.Surface["cards"]![0]!["description"]!.GetValue<string>());
        Assert.Equal("Frozen description", second.Page.Referents[0].Properties!["description"]!.GetValue<string>());
        Assert.Contains("persistent_public_context_missing", second.Page.Completeness.Missing);
        Assert.Equal("partial", second.Page.Completeness.Status);
    }
}
