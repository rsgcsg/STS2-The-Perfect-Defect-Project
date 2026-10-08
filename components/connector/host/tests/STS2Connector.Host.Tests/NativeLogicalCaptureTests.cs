using System.Text.Json.Nodes;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

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

    [Fact]
    public void CapacityFailureKeepsNoPrefixOrDispatchableLeaf()
    {
        var leaves = Enumerable.Range(0, NativeLogicalCapture.MaximumActions + 1)
            .Select(index => Leaf(index.ToString(), "root", "select")).ToArray();
        TextMenuFrame frame = new(Snapshot(), "native-owner", leaves);
        TextMenuFrame captured = NativeLogicalCapture.Validate(frame);
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
