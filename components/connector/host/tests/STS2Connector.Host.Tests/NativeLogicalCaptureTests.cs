using System.Text.Json.Nodes;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

using NativeLogicalCapture = global::STS2Connector.PlayerEnvironment.NativeLogicalCapture;

public sealed class NativeLogicalCaptureTests
{
    [Theory]
    [InlineData(24, 576)]
    [InlineData(100, 10_000)]
    public void NativeProjectionKeepsEveryAdmittedCombinationWithoutChangingLegacyLimit(int dimension, int expected)
    {
        string[] cards = Enumerable.Range(0, dimension).Select(i => $"card-{i}").ToArray();
        string[] targets = Enumerable.Range(0, dimension).Select(i => $"target-{i}").ToArray();
        var candidate = new NativeUiActionCandidate("native-candidate", "choose", "test_choose", "Choose",
            new Dictionary<string, string>(), new Dictionary<string, NativeUiOperandDomain>
            {
                ["card_id"] = new("entity_id", cards), ["target_id"] = new("entity_id", targets)
            }, cards.Select(id => new ActionEntityBinding("card", id))
                .Concat(targets.Select(id => new ActionEntityBinding("target", id))).ToArray(), "native_ui");
        var visible = cards.Select(id => Ref(id, "card")).Concat(targets.Select(id => Ref(id, "target")))
            .ToDictionary(item => item.ReferentId, StringComparer.Ordinal);
        var bindings = new[] { new NativeUiBoundAction(candidate) };
        var legacy = PlayerEnvironmentService.ProjectBoundActions(bindings, "owner", visible);
        var native = PlayerEnvironmentService.ProjectBoundActions(bindings, "owner", visible, NativeLogicalCapture.MaximumActions);
        Assert.Equal("truncated", legacy.Projection.Status);
        Assert.Equal(512, legacy.Projection.MaterializedCount);
        Assert.Equal("complete", native.Projection.Status);
        Assert.Equal(expected, native.Projection.TotalCount);
        Assert.Equal(expected, native.Projection.MaterializedCount);
        Assert.Equal(expected, native.Bindings.Count);
        Assert.Equal(expected, native.Projection.Actions.Select(action => action.BoundActionId).Distinct().Count());
        Assert.All(native.Bindings.Values, binding => Assert.Same(bindings[0], binding.NativeAction));
    }

    [Fact]
    public void CompleteNativeFrameKeepsInformationLeavesTogetherWithHeldAndSummaryLeaves()
    {
        var leaves = new[] { Leaf("held", "root", "confirm_target"), Leaf("information", "card_tips", "show_card_tips"),
            Leaf("summary", "root", "activate") };
        TextMenuFrame frame = new(Snapshot(), "native-owner", leaves);
        TextMenuFrame captured = NativeLogicalCapture.Validate(frame);
        Assert.Same(leaves, captured.Leaves);
        Assert.Empty(captured.CardPlays);
        Assert.Equal("complete", captured.Page.Completeness.Status);
    }

    [Theory]
    [InlineData("interactive", "visible_unsupported")]
    [InlineData("settling", "settling")]
    [InlineData("observed", "observed")]
    public void CapacityFailureKeepsNoPrefixOrDispatchableLeaf(string sourceReadiness, string expectedReadiness)
    {
        var leaves = Enumerable.Range(0, NativeLogicalCapture.MaximumActions + 1)
            .Select(index => Leaf(index.ToString(), "root", "select")).ToArray();
        TextMenuFrame frame = new(Snapshot() with { Status = sourceReadiness }, "native-owner", leaves);
        TextMenuFrame captured = NativeLogicalCapture.Validate(frame);
        Assert.Equal(expectedReadiness, captured.Page.Status);
        Assert.Empty(captured.Leaves);
        Assert.Equal("partial", captured.Page.Completeness.Status);
        Assert.Contains("native_logical_action_capacity_exceeded", captured.Page.Completeness.Missing);
        Assert.Equal(leaves.Length, frame.Leaves.Count);
    }

    [Fact]
    public void ResourcePresentationDifferenceDoesNotInventSettlingOrOverwriteLogicalValue()
    {
        JsonNode logical = JsonValue.Create(7)!;
        JsonObject facts = NativeLogicalPresentation.ResourceFacts("[center]6[/center]", logical);
        Assert.Equal("[center]6[/center]", facts["displayed_text"]!.GetValue<string>());
        Assert.Equal(7, facts["logical_stars"]!.GetValue<int>());
        Assert.Equal(7, logical.GetValue<int>());
        Assert.Null(facts["settling"]);
    }

    [Fact]
    public void CompoundInputRetainsKnownEarlierStageAndFreezesResultStages()
    {
        var input = new[] { new NativeInputStage(NativeInputStageKind.ControllerModeInput,
            NativeInputDelivery.Delivered, "native_controller_mode_input_delivered") };
        NativeInputResult partial = NativeInputResult.PartiallyDelivered("begin_changed", "Card begin was not delivered", input);
        input[0] = new(NativeInputStageKind.CardBeginInput, NativeInputDelivery.Unknown, "changed");
        Assert.False(partial.Accepted);
        Assert.Equal(NativeInputDelivery.PartiallyDelivered, partial.Delivery);
        Assert.Equal(NativeInputStageKind.ControllerModeInput, Assert.Single(partial.Stages!).Stage);
        NativeInputResult unknown = NativeInputResult.Unknown("confirmation_unknown", "Input threw", partial.Stages!.ToArray());
        Assert.Equal(NativeInputDelivery.Unknown, unknown.Delivery);
        Assert.Equal(NativeInputDelivery.Delivered, Assert.Single(unknown.Stages!).Delivery);
        NativeInputResult noAcceptance = NativeInputResult.DeliveredWithoutAcceptance("focus_not_witnessed",
            "Input delivered, no execution witness", partial.Stages!.ToArray());
        Assert.False(noAcceptance.Accepted);
        Assert.Equal(NativeInputDelivery.Delivered, noAcceptance.Delivery);
        Assert.Null(NativeInputResult.Delivered("legacy").Delivery);
        Assert.Null(NativeInputResult.Rejected("legacy", "rejected").Stages);
    }

    [Theory]
    [InlineData(true, true, 0)]
    [InlineData(true, false, 1)]
    [InlineData(false, true, 1)]
    [InlineData(false, false, 2)]
    public void EnteredLogicalListContentAndActionBindingsHaveIndependentCompleteness(bool content, bool bindings, int count)
    {
        var missing = NativeLogicalGridCompleteness.Missing(content, bindings);
        Assert.Equal(count, missing.Count);
        Assert.Equal(!content, missing.Contains("native_logical_public_list_content_incomplete"));
        Assert.Equal(!bindings, missing.Contains("native_logical_public_list_action_bindings_incomplete"));
    }

    [Fact]
    public void NativePotionOwnerRequiresExactMethodHolderCurrentModelAndSlot()
    {
        var holder = new PotionOwnerFixture();
        var other = new PotionOwnerFixture();
        object potion = new();
        Func<bool> predicate = holder.ShouldCancelTargeting;
        var method = typeof(PotionOwnerFixture).GetMethod(nameof(PotionOwnerFixture.ShouldCancelTargeting));
        Assert.True(NativePotionTargetOwner.Matches(predicate, holder, method, potion, potion, potion, true));
        Assert.False(NativePotionTargetOwner.Matches(other.ShouldCancelTargeting, holder, method, potion, potion, potion, true));
        Assert.False(NativePotionTargetOwner.Matches(holder.OtherPredicate, holder, method, potion, potion, potion, true));
        Assert.False(NativePotionTargetOwner.Matches(predicate, holder, method, potion, new object(), potion, true));
        Assert.False(NativePotionTargetOwner.Matches(predicate, holder, method, potion, potion, new object(), true));
        Assert.False(NativePotionTargetOwner.Matches(predicate, holder, method, potion, potion, potion, false));
        Assert.False(NativePotionTargetOwner.Matches(predicate, holder, null, potion, potion, potion, true));
        Assert.False(NativePotionTargetOwner.Matches(null, holder, method, potion, potion, potion, true));
    }

    [Theory]
    [InlineData("Grid", "visible_cards", "visible_unsupported")]
    [InlineData("ChoicePeek", "current_ui_controls", "visible_unsupported")]
    [InlineData("BundlePeek", "bundle_membership", "visible_unsupported")]
    [InlineData("Grid", "visible_cards", "settling")]
    public void QualifiedSelectorCannotEraseInheritedHudOrConsistencyFailure(
        string scope, string displacedGap, string actualSourceStatus)
    {
        // Golden negative: a current selector binder succeeds while the common
        // persistent capture fails. Final publication must keep that failure,
        // null HUD and all consistency omissions, and expose no dispatch leaf.
        PlayerEnvironmentSnapshot source = Snapshot() with
        {
            Status = actualSourceStatus,
            Persistent = null,
            Completeness = new("partial", "inherited", "inherited",
                new[] { displacedGap, "persistent_visible_state", "public_information_binding_power_owner",
                    "page_entities_catalog_consistency", "finite_bound_action_projection_incomplete" },
                new[] { "hidden_future_outcome" })
        };
        PlayerEnvironmentCompleteness projected = NativeLogicalCapturePolicy.Replace(source.Completeness,
            Enum.Parse<NativeLogicalProjectionReplacement>(scope), true, Array.Empty<string>(), "native_selector", "exact_current_native_binder");
        TextMenuFrame frame = new(source with
        {
            Completeness = projected,
            Status = NativeLogicalCapturePolicy.ProjectionStatus("selecting", source.Status, projected)
        }, "same-native-owner", new[] { Leaf("native-select", "root", "select"), Leaf("peek", "root", "close_peek") });
        TextMenuFrame published = NativeLogicalCapture.Validate(frame);
        Assert.Equal(actualSourceStatus, published.Page.Status);
        Assert.Equal("partial", published.Page.Completeness.Status);
        Assert.Null(published.Page.Persistent);
        Assert.DoesNotContain(displacedGap, published.Page.Completeness.Missing);
        Assert.Equal(new[] { "finite_bound_action_projection_incomplete", "page_entities_catalog_consistency",
            "persistent_visible_state", "public_information_binding_power_owner" }, published.Page.Completeness.Missing);
        Assert.Equal(source.Completeness.HiddenByPolicy, published.Page.Completeness.HiddenByPolicy);
        Assert.Empty(published.Leaves);
    }

    [Fact]
    public void LegacyProjectionReplacementRequiresExactSourceTypeOrMatchingNativeOwner()
    {
        const string type = "NChooseACardSelectionScreen";
        const string kind = "native_generated_card_choice";
        var surface = new NativeGeneratedCardChoiceSurface(kind, "peek", "exact-owner", null,
            Array.Empty<string>(), false, true, Array.Empty<VisibleCard>());
        Assert.True(NativeLogicalCapturePolicy.DisplacesLegacyProjection(surface, type, kind, "exact-owner"));
        Assert.False(NativeLogicalCapturePolicy.DisplacesLegacyProjection(surface, type, kind, "other-owner"));
        Assert.False(NativeLogicalCapturePolicy.DisplacesLegacyProjection(surface, type, "native_simple_card_selection", "exact-owner"));
        Assert.True(NativeLogicalCapturePolicy.DisplacesLegacyProjection(
            new UnsupportedSurface("unsupported", type, "old holder binding unavailable"), type, kind, "exact-owner"));
        Assert.False(NativeLogicalCapturePolicy.DisplacesLegacyProjection(
            new UnsupportedSurface("unsupported", "persistent_visible_state", "HUD failed"), type, kind, "exact-owner"));
        Assert.False(NativeLogicalCapturePolicy.DisplacesLegacyProjection(
            new NoActionSurface("no_action", "settling", "HUD pending"), type, kind, "exact-owner"));
    }

    [Theory]
    [InlineData(true, "complete", "interactive")]
    [InlineData(false, "partial", "visible_unsupported")]
    public void OnlyProvedLocalWindowReplacementCanRestoreCompleteInteractiveSelector(
        bool proof, string expectedCompleteness, string expectedReadiness)
    {
        var inherited = new PlayerEnvironmentCompleteness("partial", "legacy", "legacy",
            new[] { "visible_cards", "current_controls", "finite_bound_action_projection_incomplete" }, Array.Empty<string>());
        PlayerEnvironmentCompleteness captured = NativeLogicalCapturePolicy.Replace(inherited,
            NativeLogicalProjectionReplacement.Grid, proof, Array.Empty<string>(), "full_roster", "exact_binder");
        Assert.Equal(expectedCompleteness, captured.Status);
        Assert.Equal(expectedReadiness, NativeLogicalCapturePolicy.ProjectionStatus("selecting", "visible_unsupported", captured));
        if (proof) Assert.Empty(captured.Missing);
        else Assert.Equal(inherited.Missing.Order(StringComparer.Ordinal), captured.Missing);
    }

    [Theory]
    [InlineData("native_logical_grid_execution_identity_unavailable", "interactive", "visible_unsupported")]
    [InlineData("native_logical_grid_current_binding_missing", "interactive", "visible_unsupported")]
    [InlineData("native_logical_deck_binding_missing", "interactive", "visible_unsupported")]
    [InlineData("native_logical_action_capacity_exceeded", "interactive", "visible_unsupported")]
    [InlineData("native_logical_grid_current_binding_missing", "settling", "settling")]
    [InlineData("native_logical_grid_current_binding_missing", "observed", "observed")]
    public void MissingBindingIdentityOrCapacityDoesNotInventNativeSettling(
        string reason, string actualReadiness, string expectedReadiness)
    {
        PlayerEnvironmentSnapshot source = Snapshot() with { Status = actualReadiness };
        TextMenuFrame failure = NativeLogicalCapturePolicy.Partial(source, "same-native-owner", reason);
        Assert.Equal(expectedReadiness, failure.Page.Status);
        Assert.Equal(source.Interaction.Stage, failure.Page.Interaction.Stage);
        Assert.Equal("partial", failure.Page.Completeness.Status);
        Assert.Equal(new[] { reason }, failure.Page.Completeness.Missing);
        Assert.Empty(NativeLogicalCapture.Validate(failure).Leaves);
    }

    [Fact]
    public void UnexplainedInheritedFailureAndMissingNativePresentationRemainPartial()
    {
        var unexplained = new PlayerEnvironmentCompleteness("visible_unmapped", "legacy", "legacy",
            Array.Empty<string>(), Array.Empty<string>());
        PlayerEnvironmentCompleteness partial = NativeLogicalCapturePolicy.Replace(unexplained,
            NativeLogicalProjectionReplacement.Grid, true, Array.Empty<string>(), "native", "native");
        Assert.Equal("partial", partial.Status);
        Assert.Equal("visible_unsupported", NativeLogicalCapturePolicy.ProjectionStatus("selecting", "interactive", partial));
        var local = unexplained with { Status = "partial", Missing = new[] { "visible_cards" } };
        PlayerEnvironmentCompleteness preview = NativeLogicalCapturePolicy.Replace(local,
            NativeLogicalProjectionReplacement.Grid, true,
            new[] { "native_logical_offscreen_upgrade_presentation_unavailable" }, "native", "native");
        Assert.Equal("partial", preview.Status);
        Assert.Equal(new[] { "native_logical_offscreen_upgrade_presentation_unavailable" }, preview.Missing);
        Assert.Equal("settling", NativeLogicalCapturePolicy.ProjectionStatus("settling", "interactive", preview));
        Assert.Equal("observed", NativeLogicalCapturePolicy.ProjectionStatus("completed", "interactive", preview));
    }

    [Theory]
    [InlineData("interactive", true, "visible_unsupported")]
    [InlineData("settling", true, "settling")]
    [InlineData("interactive", false, "settling")]
    public void MissingHeldCardLabelsRetainNativeReadinessWithoutChangingTextCompatibility(
        string actualSourceStatus, bool nativeLogical, string expectedStatus)
    {
        PlayerEnvironmentSnapshot source = Snapshot() with { Status = actualSourceStatus };
        PlayerEnvironmentSnapshot page = NativeTextMenuFrameBuilder.ProjectHeldCardPage(source,
            source.Referents, new JsonObject { ["kind"] = "combat_card_operation" }, "native_targeting",
            displayComplete: false, nativeLogical);
        Assert.Equal(expectedStatus, page.Status);
        Assert.Equal("partial", page.Completeness.Status);
        Assert.Equal(new[] { "current_native_card_display" }, page.Completeness.Missing);
        Assert.Empty(NativeLogicalCapture.Validate(new(page, "held", new[] { Leaf("cancel", "root", "cancel_card_play") })).Leaves);
    }

    [Fact]
    public void NativeInformationFailureReportsItsOwnGapAndKeepsBothHiddenPolicies()
    {
        PlayerEnvironmentSnapshot inherited = Snapshot() with
        {
            Completeness = new("partial", "inherited", "inherited", new[] { "persistent_visible_state" }, new[] { "hidden_future_outcome" })
        };
        PlayerEnvironmentSnapshot unresolved = inherited with
        {
            Interaction = inherited.Interaction with { Kind = "native_information_unresolved", Stage = "unresolved" },
            Completeness = inherited.Completeness with { HiddenByPolicy = new[] { "hidden_draw_order" } }
        };
        PlayerEnvironmentSnapshot native = NativeLogicalCapturePolicy.PreserveNativeScope(inherited,
            unresolved, NativeLogicalProjectionReplacement.InformationPage, nativeLogical: true);
        Assert.Equal("visible_unsupported", native.Status);
        Assert.Equal("partial", native.Completeness.Status);
        Assert.Equal(new[] { "current_native_information_projection_incomplete", "persistent_visible_state" }, native.Completeness.Missing);
        Assert.Equal(new[] { "hidden_draw_order", "hidden_future_outcome" }, native.Completeness.HiddenByPolicy);
        Assert.Equal("unresolved", native.Interaction.Stage);
    }

    [Fact]
    public void ValidHeldDisplayReplacesOnlyItsKnownMissingSlice()
    {
        PlayerEnvironmentSnapshot source = Snapshot() with
        {
            Status = "visible_unsupported",
            Completeness = new("partial", "legacy", "legacy", new[] { "current_native_card_display" }, Array.Empty<string>())
        };
        PlayerEnvironmentSnapshot native = NativeTextMenuFrameBuilder.ProjectHeldCardPage(source,
            source.Referents, new JsonObject { ["kind"] = "combat_card_operation" }, "native_targeting",
            displayComplete: true, nativeLogical: true);
        Assert.Equal("interactive", native.Status);
        Assert.Equal("complete", native.Completeness.Status);
        Assert.Empty(native.Completeness.Missing);
    }

    [Fact]
    public void VisibleWithdrawnInspectorKeepsCompleteNoActionObservedThroughInformationScope()
    {
        PlayerEnvironmentSnapshot inherited = Snapshot();
        var closing = NativeTextMenuInformation.ProjectCardInspectPage(inherited,
            "Defend", "1", "Gain 5 Block.", false) with { Status = "observed" };
        var captured = new NativeTextMenuInformationCapture(closing, "inspect",
            Array.Empty<NativeTextMenuInformationLeaf>());
        var result = NativeTextMenuInformation.PreserveInformationScope(inherited, captured, nativeLogical: true);
        Assert.Equal("observed", result.Page.Status);
        Assert.Equal("complete", result.Page.Completeness.Status);
        Assert.Empty(result.Leaves);
        Assert.Equal("inspect_card", result.Page.Interaction.Kind);
        Assert.Equal("Gain 5 Block.", result.Page.Interaction.Content.Surface["description"]!.GetValue<string>());
    }

    [Fact]
    public void NoActionInspectorDoesNotEraseUnrelatedInheritedMissingFacts()
    {
        PlayerEnvironmentSnapshot inherited = Snapshot() with { Completeness =
            new("partial", "inherited", "inherited", new[] { "persistent_visible_state" }, Array.Empty<string>()) };
        var closing = NativeTextMenuInformation.ProjectCardInspectPage(inherited,
            "Defend", "1", "Gain 5 Block.", false) with { Status = "observed" };
        var captured = new NativeTextMenuInformationCapture(closing, "inspect", Array.Empty<NativeTextMenuInformationLeaf>());
        var result = NativeTextMenuInformation.PreserveInformationScope(inherited, captured, nativeLogical: true);
        Assert.Equal("partial", result.Page.Completeness.Status);
        Assert.Equal("visible_unsupported", result.Page.Status);
        Assert.Equal(new[] { "persistent_visible_state" }, result.Page.Completeness.Missing);
        Assert.Empty(result.Leaves);
    }

    [Fact]
    public void CompletedInformationReturnDoesNotReopenInputOnUnderlyingObservedRoom()
    {
        PlayerEnvironmentSnapshot underlying = Snapshot() with { Status = "observed" };
        var captured = new NativeTextMenuInformationCapture(underlying, "underlying_room",
            Array.Empty<NativeTextMenuInformationLeaf>());
        NativeTextMenuInformationCapture result = NativeTextMenuInformation.PreserveInformationScope(
            Snapshot(), captured, nativeLogical: true);
        Assert.Same(captured, result);
        Assert.Equal("observed", result.Page.Status);
    }

    private sealed class PotionOwnerFixture
    {
        public bool ShouldCancelTargeting() => throw new InvalidOperationException("Owner probe must not invoke native delegates.");
        public bool OtherPredicate() => throw new InvalidOperationException("Owner probe must not invoke native delegates.");
    }

    private static PlayerEnvironmentReferent Ref(string id, string role) => new(id, role, "entity", id,
        new(true, true, false, false, "native_visible_fact"), null, null);
    private static TextMenuLeaf Leaf(string key, string group, string verb) => new(key, group, verb, key, null,
        Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => throw new InvalidOperationException("Capture cannot dispatch."));
    private static PlayerEnvironmentSnapshot Snapshot() => new("1.0.0", PlayerEnvironmentContract.SnapshotSchema, "source", 1,
        DateTimeOffset.UnixEpoch, "interactive", null,
        new("interaction", "game_over", "summary", null, "sts2.player-environment/surface/game_over-1",
            new(new JsonObject { ["kind"] = "game_over" }, new JsonObject { ["kind"] = "game_over" }),
            Array.Empty<PlayerEnvironmentInteractionCapability>()), Array.Empty<PlayerEnvironmentReferent>(),
        new("sts2.player-environment/bound-actions-1", "complete", 0, 0, NativeLogicalCapture.MaximumActions,
            "native", Array.Empty<PlayerEnvironmentBoundAction>()), Array.Empty<PlayerEnvironmentReadOpportunity>(),
        new("complete", "public", "complete", Array.Empty<string>(), Array.Empty<string>()),
        new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));
}
