using System.Reflection;
using System.Text.Json.Nodes;
using Godot;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.TopBar;
using MegaCrit.Sts2.Core.TestSupport;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;
using NativeCapture = STS2Connector.PlayerEnvironment.NativeLogicalCapture;

namespace STS2Connector;

public sealed class NativeMapInformationTests
{
    [Theory]
    [InlineData(true, false, true)]
    [InlineData(true, true, false)]
    [InlineData(false, false, false)]
    [InlineData(false, true, false)]
    public void DeckEntryRequiresItsNativeControlAndHonorsActualTestMode(bool current, bool testMode, bool allowed)
    {
        Assert.Equal(allowed, NativeMapInformation.CanOpenDeck(current, testMode));
    }

    [Theory]
    [InlineData(true, false, false, true)]
    [InlineData(true, true, false, false)]
    [InlineData(true, false, true, false)]
    [InlineData(false, false, false, false)]
    public void ActualHoverCreationAndVisibilityGatesAreIndependentOfDeck(bool current, bool blocked, bool hidden, bool allowed)
    {
        Assert.Equal(allowed, NativeMapInformation.CanShowTips(current, blocked, hidden));
        Assert.Equal(current, NativeMapInformation.CanOpenDeck(current, testMode: false));
    }

    [Fact]
    public void DisabledMapBackAndNoRoutesDoNotSuppressProvedGlobalInformation()
    {
        var empty = new MapNavigationSurface("map_navigation", "map", false, false, "none", Array.Empty<VisibleMapChoice>());
        var complete = new StateCompleteness("contract_complete_for_visible_singleplayer_map_navigation",
            "temporarily_empty_while_map_input_is_not_route_ready", Array.Empty<string>(), Array.Empty<string>());
        Assert.False(NativeTextMenuInformation.CanPublishMapPage("settling", "complete", 0, 0, "settling", complete,
            empty, nativeBackAvailable: false)); // unchanged legacy rule
        Assert.True(NativeTextMenuInformation.CanPublishMapPage("settling", "complete", 0, 0, "settling", complete,
            empty, nativeBackAvailable: false, nativeGlobalInformationAvailable: true));
        Assert.False(NativeTextMenuInformation.CanPublishMapPage("settling", "truncated", 0, 1, "settling", complete,
            empty, false, true));
        Assert.False(NativeTextMenuInformation.CanPublishMapPage("settling", "complete", 0, 0, "settling",
            complete with { Missing = new[] { "native_binding_missing" } }, empty, false, true));
        Assert.False(NativeTextMenuInformation.CanPublishMapPage("observed", "complete", 0, 0, "unknown", complete,
            empty, false, true));
        Assert.False(NativeTextMenuInformation.CanPublishMapPage("settling", "complete", 0, 0, "settling", complete,
            empty with { CanExitAnnotation = true }, false, true));
    }

    private static PlayerEnvironmentReferent Ref(string id, string role) =>
        new(id, role, "entity", id, new(true, true, false, false, "native_visible_fact"), null, null);

    private static PlayerEnvironmentSnapshot Page() => new("1.0.0", PlayerEnvironmentContract.SnapshotSchema, "map-snapshot", 1,
        DateTimeOffset.UnixEpoch, "interactive", null,
        new("map", "native_map", "native_information_page", null, "map-surface",
            new(new JsonObject { ["kind"] = "map_navigation" }, new JsonObject { ["kind"] = "native_map" }),
            Array.Empty<PlayerEnvironmentInteractionCapability>()), Array.Empty<PlayerEnvironmentReferent>(),
        new("sts2.player-environment/bound-actions-1", "complete", 0, 0, 100, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
        Array.Empty<PlayerEnvironmentReadOpportunity>(), new("complete", "visible", "exact", Array.Empty<string>(), Array.Empty<string>()),
        new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));

    private static SnapshotBuildResult NativeMap(bool annotation)
    {
        var map = new MapNavigationSurface("map_navigation", "map", true, false, annotation ? "drawing" : "none",
            new[] { new VisibleMapChoice("node", 0, 1, "Monster") })
        { CanExitAnnotation = annotation, AnnotationInputEntityId = annotation ? "annotation" : null };
        var context = new MapLiveContext("map", 0, null, Array.Empty<VisibleMapCoordinate>(), Array.Empty<VisibleMapNode>());
        var observation = new LiveObservation("map-source", "ready", context, map,
            new("contract_complete_for_visible_singleplayer_map_navigation", "exact", Array.Empty<string>(), Array.Empty<string>()),
            new("v", "commit", null, null, new("test", true, true, false, "fixture")), Array.Empty<string>());
        var refs = new[] { Ref("node", "map_node"), Ref("annotation", "map_annotation_input") };
        var native = NativeUiActionRuntime.DescribeMapCommands(map)
            .Select(value => NativeUiActionRuntime.BindActionToCurrentObservation(observation, value)!).ToArray();
        var projection = PlayerEnvironmentService.ProjectBoundActions(native, "map",
            refs.ToDictionary(value => value.ReferentId), 100);
        Assert.Equal("complete", projection.Projection.Status);
        return new(Page() with { Referents = refs, BoundActions = projection.Projection },
            observation, projection.Bindings, new Dictionary<string, PlayerReadBuildResult>());
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void MapCompositionRetainsTravelAnnotationBackAllGlobalLeavesAndTheirExactPublicSubjects(bool annotation)
    {
        var source = NativeMap(annotation);
        string[] globalKeys = { "open_run_deck", "show_topbar_tips", "inspect_relic", "show_relic_tips", "clear_native_tip", "return_native_map" };
        var globals = globalKeys.Select(key => new TextMenuLeaf(key, "information", key, key, key + "-subject",
            Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => NativeInputResult.Delivered("existing information"))).ToList();
        globals.Add(new("back", "root", "return_native_map", "Back", null, Array.Empty<PlayerEnvironmentBoundActionArgument>(),
            () => NativeInputResult.Delivered("native Back")));
        var frame = new TextMenuFrame(Page() with { Referents = globalKeys.Select(key => Ref(key + "-subject", "information")).ToArray() },
            "exact-map", globals);
        int delivered = 0, departures = 0;
        var complete = NativeTextMenuFrameBuilder.AppendMapActions(frame, source,
            _ => { delivered++; return NativeInputResult.Delivered("original native Map path"); },
            transition => { departures++; return transition(); });
        Assert.Equal(globals.Count + 1 + (annotation ? 1 : 0), complete.Leaves.Count);
        foreach (var leaf in globals) Assert.Contains(complete.Leaves, value => ReferenceEquals(value, leaf));
        Assert.Contains(complete.Page.Referents, value => value.ReferentId == "node");
        if (annotation)
        {
            // The exact annotation operand remains Host-private; its public exit leaf survives.
            var cancel = complete.Leaves.Single(value => value.Label == "Exit map annotation mode");
            Assert.Null(cancel.SubjectReferentId);
            Assert.True(cancel.Dispatch().Accepted);
        }
        Assert.Equal(complete.Page.Referents.Count, complete.Page.Referents.Select(value => value.ReferentId).Distinct().Count());
        Assert.All(complete.Leaves, leaf => Assert.All(leaf.Arguments.Select(value => value.ReferentId)
                .Concat(leaf.SubjectReferentId is { } id ? new[] { id } : Array.Empty<string>()),
            id => Assert.Contains(complete.Page.Referents, value => value.ReferentId == id)));
        Assert.True(complete.Leaves.Single(value => value.SubjectReferentId == "node").Dispatch().Accepted);
        Assert.Equal(annotation ? 2 : 1, delivered);
        Assert.Equal(1, departures); // Only travel departs; annotation retains its original input path.
        Assert.DoesNotContain(complete.Leaves, value => value.Verb is "play_card" or "open_draw_pile" or "open_discard_pile");

        foreach (string gap in new[] { "persistent_hud_missing", "public_information_binding_relic_subject", "native_consistency" })
        {
            var failed = NativeCapture.Validate(complete with { Page = complete.Page with
                { Completeness = complete.Page.Completeness with { Status = "partial", Missing = new[] { gap } } } });
            Assert.Empty(failed.Leaves);
            Assert.Contains(gap, failed.Page.Completeness.Missing);
        }
    }

    [Fact]
    public void NonmodalRenderedTipsKeepTheMapCatalogAndScopeFailures()
    {
        var frame = new TextMenuFrame(Page(), "map", new[]
        {
            new TextMenuLeaf("deck", "information", "open_run_deck", "Deck", null,
                Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => NativeInputResult.Delivered("deck"))
        });
        var shown = NativeTextMenuInformation.AttachPassiveHoverFacts(frame.Page,
            (new JsonNode[] { new JsonObject { ["title"] = "Deck", ["description"] = "Actually rendered native text" } }, 0));
        Assert.Equal("native_map", shown.Interaction.Kind);
        Assert.Equal("map", shown.Interaction.InteractionId);
        Assert.Equal(frame.Page.BoundActions, shown.BoundActions);
        var inherited = frame.Page with { Completeness = frame.Page.Completeness with
            { Status = "partial", Missing = new[] { "persistent_hud_missing" } } };
        var captured = NativeTextMenuInformation.PreserveInformationScope(inherited,
            new NativeTextMenuInformationCapture(shown, "map", Array.Empty<NativeTextMenuInformationLeaf>()), true);
        Assert.Equal("partial", captured.Page.Completeness.Status);
        Assert.Contains("persistent_hud_missing", captured.Page.Completeness.Missing);
    }

    [Fact]
    public void SharedRepeatedFocusProofRejectsDifferentSourceEntryOrRenderedSet()
    {
        object source = new(), set = new();
        Assert.True(NativeTipReturn.CanReuse(source, source, NativeTipEntry.Focus, NativeTipEntry.Focus, set, set, true, true));
        Assert.False(NativeTipReturn.CanReuse(source, source, NativeTipEntry.Focus, NativeTipEntry.Mouse, set, set, true, true));
        Assert.False(NativeTipReturn.CanReuse(new object(), source, NativeTipEntry.Focus, NativeTipEntry.Focus, set, set, true, true));
        Assert.False(NativeTipReturn.CanReuse(source, source, NativeTipEntry.Focus, NativeTipEntry.Focus, new object(), set, true, true));
        Assert.False(NativeTipReturn.CanReuse(source, source, NativeTipEntry.Focus, NativeTipEntry.Focus, set, set, true, false));
    }

    [Fact]
    public void ExactNativeAbiSuppliesThePublicSemanticControlGates()
    {
        Assert.Equal(typeof(bool), typeof(Node).GetMethod("CanProcess")!.ReturnType);
        Assert.NotNull(typeof(Control).GetProperty("FocusBehaviorRecursive"));
        Assert.Equal(typeof(bool), typeof(NClickableControl).GetProperty("IsEnabled")!.PropertyType);
        Assert.NotNull(typeof(NTopBarDeckButton).GetMethod("OnRelease", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly));
        Assert.NotNull(typeof(TestMode).GetProperty("IsOn") ?? (object?)typeof(TestMode).GetField("IsOn"));
    }
}
