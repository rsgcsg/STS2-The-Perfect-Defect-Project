using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;
namespace STS2Connector;
using NativeLogicalCapture = global::STS2Connector.PlayerEnvironment.NativeLogicalCapture;

public sealed class RequiredPublicCombatCompatibilityTests
{
    [Theory]
    [InlineData("held", "public_combat_power_facts", false)]
    [InlineData("held", "public_combat_health_display", false)]
    [InlineData("potion", "public_combat_power_facts", false)]
    [InlineData("potion", "public_combat_health_display", false)]
    [InlineData("held", "public_combat_power_facts", true)]
    [InlineData("held", "public_combat_health_display", true)]
    [InlineData("potion", "public_combat_power_facts", true)]
    [InlineData("potion", "public_combat_health_display", true)]
    public void RequiredPublicCombatMissingMustSurviveActualDerivedOwners(string owner, string missing, bool nativeLogical)
    {
        PlayerEnvironmentSnapshot source = Source(missing);
        PlayerEnvironmentSnapshot projected = Project(source, owner, nativeLogical);
        Assert.Same(source.Interaction.Content.Context, projected.Interaction.Content.Context);
        var leaf = new TextMenuLeaf("cancel", "root", "cancel_card_play", "Cancel", null,
            Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => throw new InvalidOperationException("No dispatch permitted"));
        var frame = new TextMenuFrame(projected, "review", new[] { leaf });
        if (nativeLogical) frame = NativeLogicalCapture.Validate(frame);
        var v1 = new TextMenuSession().Observe(frame).Snapshot;
        var v2 = new TextMenuV2Session().Observe(frame).Snapshot;
        Assert.True(v1.MenuActions.Actions.Count == 0 && v2.MenuActions.Actions.Count == 0,
            $"Required {missing} erased by {owner}: projected {projected.Status}/{projected.Completeness.Status}, " +
            $"legacy actions={v1.MenuActions.Actions.Count}, text-v2 actions={v2.MenuActions.Actions.Count}");
        Assert.Equal("partial", frame.Page.Completeness.Status);
        Assert.Contains(missing, frame.Page.Completeness.Missing);
    }
    [Theory]
    [InlineData("held")]
    [InlineData("potion")]
    public void LegacyQualifiedDerivedOwnerStillSupersedesOnlyOldOwnerTransitionGap(string owner)
    {
        var projected = Project(Source("legal_actions"), owner, nativeLogical: false);
        Assert.Equal("interactive", projected.Status);
        Assert.Equal("complete", projected.Completeness.Status);
        Assert.Empty(projected.Completeness.Missing);
        var leaf = new TextMenuLeaf("cancel", "root", "cancel_card_play", "Cancel", null,
            Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => throw new InvalidOperationException("Observation only"));
        var frame = new TextMenuFrame(projected, "qualified-child", new[] { leaf });
        Assert.Single(new TextMenuSession().Observe(frame).Snapshot.MenuActions.Actions);
        Assert.Single(new TextMenuV2Session().Observe(frame).Snapshot.MenuActions.Actions);
    }
    private static PlayerEnvironmentSnapshot Project(PlayerEnvironmentSnapshot source, string owner, bool nativeLogical)
    {
        var surface = new JsonObject { ["kind"] = owner == "held" ? "combat_card_operation" : "potion_targeting" };
        if (owner == "held") return NativeTextMenuFrameBuilder.ProjectHeldCardPage(source,
            source.Referents, surface, "native_targeting", displayComplete: true, nativeLogical);
        return NativeLogicalCapturePolicy.PreserveNativeScope(source,
            NativeTextMenuFrameBuilder.ProjectPotionTargetPage(source, source.Referents, surface, nativeLogical),
            NativeLogicalProjectionReplacement.PotionTarget, nativeLogical);
    }
    private static PlayerEnvironmentSnapshot Source(string missing) => new("1.0.0", PlayerEnvironmentContract.SnapshotSchema,
        "review", 1, DateTimeOffset.UnixEpoch, "visible_unsupported", null,
        new("interaction", "combat_turn", "ready", null, "sts2.player-environment/surface/combat_turn-1",
            new(new JsonObject { ["kind"] = "combat_turn" }, new JsonObject { ["kind"] = missing == "public_combat_health_display" ? "unknown" : "combat" }),
            Array.Empty<PlayerEnvironmentInteractionCapability>()), Array.Empty<PlayerEnvironmentReferent>(),
        new("sts2.player-environment/bound-actions-1", "complete", 0, 0, 512, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
        Array.Empty<PlayerEnvironmentReadOpportunity>(), new("partial", "public", "native", new[] { missing }, Array.Empty<string>()),
        new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));
}
