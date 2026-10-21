using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.Json;
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
    [Fact]
    public void PublicBasisUsesFrozenFullRosterExactInputAndDistinctCardDisplays()
    {
        var (source, grid) = Captured();
        var before = grid.FullCards[63];
        var after = before with { Description = "Native after description", IsUpgraded = true };
        grid = grid with { NeutralSurface = grid.NeutralSurface with {
            CardDisplays = new[] { new NativeLogicalGridCardDisplay(before, "selector_before"),
                new NativeLogicalGridCardDisplay(after, "selector_after") } } };
        var input = new SnapshotBuildInput(source.HostObservation.Game, source.HostObservation,
            new(false, null, null), new RewardPageSnapshotIdentity()) {
            GridCapture = new(true, grid, Array.Empty<string>()),
            Read = kind => PlayerReadBuildResult.Failure("fixture", kind) };
        var first = PlayerEnvironmentService.BuildSnapshot(inputProfile: PublicBasisContract.Profile, capturedInput: input);
        var again = PlayerEnvironmentService.BuildSnapshot(inputProfile: PublicBasisContract.Profile, capturedInput: input);
        Assert.Equal(first.Snapshot.SnapshotId, again.Snapshot.SnapshotId);
        Assert.Equal(64, first.Snapshot.Interaction.Content.Surface["cards"]!.AsArray().Count);
        var action = Assert.Single(first.Snapshot.BoundActions.Actions);
        Assert.Equal("deselect", action.Verb); Assert.Equal("card-63", action.SubjectReferentId);
        Assert.Same(grid.Inputs[0], first.Bindings[action.BoundActionId].NativeAction.GridInput);
        Assert.Equal(2, first.Snapshot.Reads.Count(read => read.Kind == "surface_card" && read.TargetReferentId == "card-63"));
        Assert.Null(first.Snapshot.Interaction.Content.Surface["native_preview_text"]);
        var legacy = PlayerEnvironmentService.BuildSnapshot(capturedInput: input);
        Assert.IsNotType<NativePublicBasisGridSurface>(legacy.HostObservation.Surface);
    }

    [Fact]
    public void SourcePublicProjectionUsesTheSameFrozenGridWhileNativePreparationKeepsItsOriginalSurface()
    {
        var (source, grid) = Captured();
        var publicSurface = (JsonObject)grid.NeutralSurface.PublicSurface.DeepClone();
        publicSurface["selector_request"] = new JsonObject {
            ["entity_id"] = grid.RequestEntityId, ["owner_entity_id"] = grid.OwnerEntityId,
            ["family"] = "deck_card", ["stage"] = "selecting" };
        publicSurface["preview_options"] = new JsonArray(grid.FullCards.Select(card => (JsonNode)new JsonObject {
            ["subject_entity_id"] = card.EntityId, ["before"] = JsonSerializer.SerializeToNode(card, ConnectorMod._jsonOptions),
            ["after"] = null, ["after_availability"] = "not_applicable", ["display_context"] = "native_original_only",
            ["missing"] = new JsonArray() }).ToArray());
        grid = grid with { NeutralSurface = grid.NeutralSurface with { PublicSurface = publicSurface,
            CardDisplays = new[] { new NativeLogicalGridCardDisplay(grid.FullCards[63], "selector_original:deck_card") } } };
        var frozen = new NativeLogicalGridCaptureResult(true, grid, Array.Empty<string>());
        var input = new SnapshotBuildInput(source.HostObservation.Game, source.HostObservation,
            new(false, null, null), new RewardPageSnapshotIdentity()) {
            GridCapture = frozen, Read = kind => PlayerReadBuildResult.Failure("fixture", kind) };
        SnapshotBuildResult? neutral = null;
        var native = PlayerEnvironmentService.BuildSnapshot(textMenuCapture: true, aggregateCurrentPublicReads: true,
            sourcePublicProjection: value => neutral = value, capturedInput: input);
        Assert.NotNull(neutral);
        Assert.IsType<NativePublicBasisGridSurface>(neutral.HostObservation.Surface);
        Assert.Equal(64, neutral.Snapshot.Interaction.Content.Surface["cards"]!.AsArray().Count);
        Assert.Equal(grid.RequestEntityId, neutral.Snapshot.Interaction.Content.Surface["selector_request"]!["entity_id"]!.GetValue<string>());
        Assert.Equal(64, neutral.Snapshot.Interaction.Content.Surface["preview_options"]!.AsArray().Count);
        Assert.Contains(neutral.Snapshot.Reads, read => read.Kind == "surface_card" && read.TargetReferentId == "card-63");
        Assert.Same(source.HostObservation.Surface, native.HostObservation.Surface);
        Assert.Same(frozen, native.CapturedLogicalGrid);
        Assert.Null(neutral.Snapshot.Interaction.Content.Surface["native_preview_text"]);
    }

    [Fact]
    public void SourceControlWitnessKeepsTheExactOriginalControlWithoutChangingNativeVerbOrDispatch()
    {
        var (source, grid) = Captured(); object originalControl = Bare<NButton>();
        var control = new NativeLogicalGridInputSpec("request-original:confirm", "logical_grid_control:confirm",
            grid.RequestEntityId, "confirm_selection", "confirm", "preview", "Confirm", "control-original",
            NativeLogicalGridInputKind.Control, grid.State, Control: (NButton)originalControl);
        grid = grid with { Inputs = new[] { control } };
        var frame = NativeLogicalGridCapture.ComposeNativeFrame(source, source.Snapshot, Array.Empty<TextMenuLeaf>(),
            new(true, grid, Array.Empty<string>()))!;
        var leaf = Assert.Single(frame.Leaves);
        Assert.Equal("confirm_selection", leaf.Verb); Assert.Equal("control-original", leaf.SubjectReferentId);
        Assert.Null(leaf.NativeWitness);
        Assert.NotNull(leaf.NativeSourceWitness);
        Assert.Same(grid.State.Owner, leaf.NativeSourceWitness.Owner);
        Assert.Same(originalControl, leaf.NativeSourceWitness.Subject);
        Assert.Equal("confirm", leaf.NativeSourcePrefixVerb);
        Assert.Empty(leaf.NativeSourceWitness.Arguments);
    }

    [Fact]
    public void ActualPreviewComparisonChecksEveryPublicFactAndMultiplicityWithoutInventedIds()
    {
        JsonObject Card(string id, string description) => new() { ["entity_id"] = id, ["is_selected"] = true,
            ["definition_id"] = "NATIVE", ["description"] = description,
            ["card_previews"] = new JsonArray(new JsonObject { ["description"] = "Nested native text" }) };
        var expected = new[] { Card("original-1", "After"), Card("original-2", "After") };
        var actual = new[] { Card("display-only", "After"), Card("display-only", "After") };
        Assert.True(NativeLogicalGridCapture.PreviewRowsMatch(expected, actual));
        actual[0]["card_previews"]![0]!["description"] = "New information";
        Assert.False(NativeLogicalGridCapture.PreviewRowsMatch(expected, actual));
        Assert.False(NativeLogicalGridCapture.PreviewRowsMatch(expected, actual.Take(1).ToArray()));
        Assert.False(NativeLogicalGridCapture.PreviewRowsMatch(Array.Empty<JsonObject>(), Array.Empty<JsonObject>()));
    }

    [Fact]
    public void NativeSinglePreviewWidgetsExposeTheExactAfterControl()
    {
        foreach (Type type in new[] { typeof(NUpgradePreview), typeof(NEnchantPreview) })
        {
            FieldInfo? after = type.GetField("_after", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.NotNull(after);
            Assert.True(typeof(Godot.Control).IsAssignableFrom(after.FieldType));
        }
    }

    [Fact]
    public void PublicBasisInspectorUsesProtocolOpenWhileRetainingExactNativeInput()
    {
        var (source, grid) = Captured();
        var inspect = grid.Inputs[0] with { Key = "request-original:inspect:card-63", NativeLeafKey = "logical_grid_inspect:card-63",
            NativeVerb = "inspect_card", PublicVerb = "inspect", Kind = NativeLogicalGridInputKind.Inspect };
        grid = grid with { NeutralSurface = grid.NeutralSurface with { Inputs = new[] { inspect } } };
        var input = new SnapshotBuildInput(source.HostObservation.Game, source.HostObservation,
            new(false, null, null), new RewardPageSnapshotIdentity()) {
            GridCapture = new(true, grid, Array.Empty<string>()),
            Read = kind => PlayerReadBuildResult.Failure("fixture", kind) };
        var built = PlayerEnvironmentService.BuildSnapshot(inputProfile: PublicBasisContract.Profile, capturedInput: input);
        var action = Assert.Single(built.Snapshot.BoundActions.Actions);
        Assert.Equal("open", action.Verb);
        Assert.Same(inspect, built.Bindings[action.BoundActionId].NativeAction.GridInput);
        Assert.Equal("inspect_card", inspect.NativeVerb);
    }

    [Fact]
    public void EmptyDeckCardPreviewRequiresActualEmptyContainerAndTheSameQualifiedOpenRequest()
    {
        var (_, grid) = Captured();
        var request = new NativeSelectorOccurrenceFacts(grid.State.Owner, grid.State.Completion, "deck_card", "preview",
            grid.State.Cards, Array.Empty<CardModel>(), 0, 64, true, "native_selected_original_set", "native_preview_at_max",
            null, true, true, true, Array.Empty<string>());
        bool Qualified(NativeSelectorOccurrenceFacts value, bool container = true, int holders = 0) =>
            NativeLogicalGridCapture.IsQualifiedEmptyDeckCardPreview(value, container, holders);
        Assert.True(Qualified(request));
        Assert.False(Qualified(request, container: false));
        Assert.False(Qualified(request, holders: 1));
        Assert.False(Qualified(request with { Owner = Bare<NDeckTransformSelectScreen>(), Family = "deck_transform" }));
        Assert.False(Qualified(request with { MinSelect = 1 }));
        Assert.False(Qualified(request with { Selected = new[] { grid.State.Cards[0] } }));
        Assert.False(Qualified(request with { Stage = "selecting" }));
        Assert.False(Qualified(request with { MechanicsQualified = false }));
        Assert.False(Qualified(request with { Completion = Task.CompletedTask }));
    }

    [Fact]
    public void PublicBasisAndNativeCaptureRetainAllSixFamiliesWithSeparateTransformQualification()
    {
        Assert.True(NativeLogicalGridCapture.CapturesOwner(typeof(NDeckTransformSelectScreen), publicBasisOnly: true));
        Assert.True(NativeLogicalGridCapture.CapturesOwner(typeof(NDeckTransformSelectScreen), publicBasisOnly: false));
        foreach (Type type in new[] { typeof(NDeckCardSelectScreen), typeof(NDeckUpgradeSelectScreen),
            typeof(NDeckEnchantSelectScreen), typeof(NSimpleCardSelectScreen), typeof(NCombatPileCardSelectScreen) })
        {
            Assert.True(NativeLogicalGridCapture.CapturesOwner(type, publicBasisOnly: true));
            Assert.True(NativeLogicalGridCapture.CapturesOwner(type, publicBasisOnly: false));
        }
    }

    [Fact]
    public void UnqualifiedArtifactCanFreezePublicGridWithoutAcquiringExecutionPermission()
    {
        var (source, grid) = Captured();
        var game = source.HostObservation.Game with { Compatibility = new(
            "artifact_unqualified", false, true, true, "Passive observation only.") };
        Assert.True(NativeLogicalGridCapture.CaptureAvailable(game, publicBasisOnly: true));
        Assert.False(NativeLogicalGridCapture.CaptureAvailable(game, publicBasisOnly: false));
        var observation = source.HostObservation with { Game = game };
        var input = new SnapshotBuildInput(game, observation, new(false, null, null), new RewardPageSnapshotIdentity()) {
            GridCapture = new(true, grid, Array.Empty<string>()),
            Read = kind => PlayerReadBuildResult.Failure("fixture", kind) };
        var captured = PlayerEnvironmentService.BuildSnapshot(inputProfile: PublicBasisContract.Profile, capturedInput: input);
        Assert.Equal(64, captured.Snapshot.Interaction.Content.Surface["cards"]!.AsArray().Count);
        Assert.Single(captured.Snapshot.BoundActions.Actions);
        Assert.False(captured.HostObservation.Game.Compatibility.ActionExecutionAllowed);
        Assert.Equal("artifact_unqualified", captured.HostObservation.Game.Compatibility.Status);
        Assert.False(NativeLogicalGridCapture.CaptureAvailable(game with { Compatibility = game.Compatibility with {
            StateObservationAllowed = false } }, publicBasisOnly: true));
        Assert.False(NativeLogicalGridCapture.CaptureAvailable(game with { Compatibility = game.Compatibility with {
            ReadAllowed = false } }, publicBasisOnly: true));
    }

}
