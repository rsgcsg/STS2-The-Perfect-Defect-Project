using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

using NativeLogicalCapture = global::STS2Connector.PlayerEnvironment.NativeLogicalCapture;

public sealed class PublicCombatFactsTests
{
    [Theory]
    [InlineData(HpDisplay.InfiniteWithoutNumbers, 999999999, null, "infinite_without_numbers", false)]
    [InlineData(HpDisplay.Normal, 40, 40, "normal", true)]
    [InlineData(HpDisplay.InfiniteWithNumbers, 999999999, 999999999, "infinite_with_numbers", true)]
    public void ActualEnemyProjectionMasksOnlyNativeSuppressedNumbersInContextAndReferents(
        HpDisplay mode, int nativeHp, int? expectedHp, string expectedMode, bool available)
    {
        // Native scalar creature fixture uses no model/rules/scene initialization.
        // The first tuple is the real WaterfallGiant post-transformation source
        // fact; the old BuildEnemy exposed its 999999999 sentinel unconditionally.
        var creature = new Creature((Player)null!, nativeHp, nativeHp) { HpDisplay = mode };
        var missing = new List<string>();
        VisibleEnemy enemy = LiveContextReader.BuildEnemy(creature, new NativeEntityRegistry(), missing);
        var context = Context(enemy);
        PlayerEnvironmentInteractionContent content = PlayerEnvironmentService.ProjectVisibleFacts(
            new NoActionSurface("combat_turn", "fixture", "Fixture"), context);
        var referents = PlayerEnvironmentService.ProjectFactReferents(content.Context);
        PlayerEnvironmentSnapshot page = Snapshot(content, referents.Values.ToArray());

        // These are the real visibility and recursive referent-copy paths, rather
        // than a predicate-only test or a manually sanitized JSON counterexample.
        PlayerEnvironmentSnapshot projected = TextMenuV2Visibility.Sanitize(page);
        JsonObject body = Assert.IsType<JsonObject>(projected.Interaction.Content.Context["enemies"]![0]);
        JsonObject mirror = Assert.IsType<JsonObject>(Assert.Single(projected.Referents,
            value => value.ReferentId == enemy.EntityId).Properties);
        foreach (JsonObject facts in new[] { body, mirror })
        {
            Assert.True(facts.ContainsKey("hp"));
            Assert.True(facts.ContainsKey("max_hp"));
            Assert.Equal(expectedHp == null ? null : (decimal?)expectedHp.Value,
                facts["hp"]?.GetValue<decimal>());
            Assert.Equal(expectedHp == null ? null : (decimal?)expectedHp.Value,
                facts["max_hp"]?.GetValue<decimal>());
            Assert.Equal(expectedMode, facts["hp_display_mode"]!.GetValue<string>());
            Assert.Equal(available, facts["hp_numbers_available"]!.GetValue<bool>());
            Assert.True(facts["health_bar_visible"]!.GetValue<bool>());
        }
        Assert.Equal("complete", projected.Completeness.Status);
        Assert.Equal(page.Status, projected.Status);
        Assert.Empty(missing);
    }

    [Theory]
    [InlineData(HpDisplay.Normal)]
    [InlineData(HpDisplay.InfiniteWithNumbers)]
    [InlineData(HpDisplay.InfiniteWithoutNumbers)]
    public void SemanticHealthVisibilityCannotBeReplacedByNumberMode(HpDisplay mode)
    {
        Assert.False(LiveContextReader.HealthNumbersAvailable(mode, healthBarVisible: false));
        Assert.Equal(mode != HpDisplay.InfiniteWithoutNumbers,
            LiveContextReader.HealthNumbersAvailable(mode, healthBarVisible: true));
    }

    [Fact]
    public void UnknownNativeHealthModeFailsInsteadOfInventingHiddenOrNormalNumbers()
    {
        var creature = new Creature((Player)null!, 40, 50) { HpDisplay = (HpDisplay)123 };
        var failure = Assert.Throws<LiveContextReader.PublicCombatCaptureException>(() => LiveContextReader.BuildEnemy(
            creature, new NativeEntityRegistry(), new List<string>()));
        var context = new UnknownLiveContext("unknown", failure.Missing, "Required health capture failed")
        { CaptureMissing = new[] { failure.Missing } };
        var game = new GameBuildIdentity("exact", "commit", null, 1,
            new("exact", true, true, true, "Fixture"));
        var owner = new LiveObservation("source", "ready", context,
            new NoActionSurface("combat_hand_card_selection", "fixture", "Fixture"),
            new("contract_complete", "native", Array.Empty<string>(), Array.Empty<string>()), game, Array.Empty<string>());
        var failed = LiveObservationReader.ApplyRequiredContextCompleteness(owner);
        Assert.IsType<UnsupportedSurface>(failed.Surface);
        Assert.Equal(new[] { LiveContextReader.RequiredHealthCaptureMissing }, failed.Completeness.Missing);
        Assert.Equal("partial", failed.Completeness.PlayerVisibleSemantics);
        Assert.Equal("unsupported", failed.Readiness);
    }

    [Fact]
    public void OptionalPowerDetailFailureRetainsRequiredVisibleScalarsAndRichSuccess()
    {
        var sources = new[] { new PowerFixture("rich"), new PowerFixture("no-text") { DetailFails = true } };
        LiveContextReader.CombatStatusCapture captured = Capture(sources);
        Assert.Empty(captured.Missing);
        Assert.Equal(new[] { "rich", "no-text" }, captured.Statuses.Select(status => status.DefinitionId));
        Assert.All(captured.Statuses, status => { Assert.Equal(7m, status.Amount); Assert.Equal("Buff", status.Type); });
        Assert.Equal("Existing optional rich text", captured.Statuses[0].Description);
        Assert.Null(captured.Statuses[1].Description);
    }

    [Fact]
    public void HiddenPowerDoesNotReadScalarOrDetailAndDoesNotBecomeMissing()
    {
        var source = new PowerFixture("hidden") { Hidden = true, ScalarFails = true, DetailFails = true };
        var captured = Capture(new[] { source });
        Assert.Empty(captured.Statuses);
        Assert.Empty(captured.Missing);
        Assert.Equal(0, source.ScalarReads);
        Assert.Equal(0, source.DetailReads);
    }

    [Theory]
    [InlineData("visibility")]
    [InlineData("scalar")]
    [InlineData("roster")]
    public void RequiredPowerFailureHasExplicitMissingEvenWhenOtherPowersWereCaptured(string failure)
    {
        var bad = new PowerFixture("bad") { VisibilityFails = failure == "visibility", ScalarFails = failure == "scalar" };
        IEnumerable<PowerFixture> sources = failure == "roster"
            ? BrokenRoster() : new[] { new PowerFixture("good"), bad };
        var captured = Capture(sources);
        Assert.Equal(new[] { LiveContextReader.RequiredPowerCaptureMissing }, captured.Missing);
        Assert.Contains(captured.Statuses, power => power.DefinitionId == "good");
        if (failure != "roster") Assert.Equal(0, bad.DetailReads);
    }

    [Theory]
    [InlineData("combat_turn", "ready", "unsupported")]
    [InlineData("combat_hand_card_selection", "ready", "unsupported")]
    [InlineData("native_generated_card_choice", "ready", "unsupported")]
    [InlineData("combat_turn", "settling", "settling")]
    public void SharedCombatFailureSurvivesCompleteChildOwnerAndNativeCapturePolicy(
        string ownerKind, string readiness, string expected)
    {
        var captured = Capture(new[] { new PowerFixture("bad") { ScalarFails = true } });
        CombatLiveContext context = Context() with { CaptureMissing = captured.Missing };
        var game = new GameBuildIdentity("exact", "commit", null, 1,
            new("exact", true, true, true, "Fixture"));
        var owner = new LiveObservation("source", readiness, context,
            new NoActionSurface(ownerKind, "fixture", "Fixture"),
            new("contract_complete", "native", Array.Empty<string>(), Array.Empty<string>()), game, Array.Empty<string>());
        LiveObservation failed = LiveObservationReader.ApplyRequiredContextCompleteness(owner);
        Assert.IsType<UnsupportedSurface>(failed.Surface);
        Assert.Equal(expected, failed.Readiness);
        Assert.Equal(captured.Missing, failed.Completeness.Missing);
        Assert.Equal("none_fail_closed", failed.InputOwnership!.Status);
        Assert.Equal("contract_complete", owner.Completeness.PlayerVisibleSemantics);

        var inherited = new PlayerEnvironmentCompleteness("partial", "public", "native",
            failed.Completeness.Missing, Array.Empty<string>());
        var afterChild = NativeLogicalCapturePolicy.Replace(inherited,
            NativeLogicalProjectionReplacement.Grid, true, Array.Empty<string>(), "complete_child", "native_child");
        var content = PlayerEnvironmentService.ProjectVisibleFacts(failed.Surface, context);
        Assert.Null(content.Context["capture_missing"]);
        PlayerEnvironmentSnapshot page = Snapshot(content, Array.Empty<PlayerEnvironmentReferent>()) with
        { Status = expected == "settling" ? "settling" : "visible_unsupported", Completeness = afterChild };
        var leaf = new TextMenuLeaf("select", "root", "select", "Select", null,
            Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => throw new InvalidOperationException("Must not dispatch"));
        var closed = NativeLogicalCapture.Validate(new TextMenuFrame(page, "owner", new[] { leaf }));
        Assert.Equal("partial", closed.Page.Completeness.Status);
        Assert.Equal(captured.Missing, closed.Page.Completeness.Missing);
        Assert.Empty(closed.Leaves);
    }

    private sealed class PowerFixture(string id)
    {
        internal string Id = id;
        internal bool Hidden, VisibilityFails, ScalarFails, DetailFails;
        internal int ScalarReads, DetailReads;
    }

    private static LiveContextReader.CombatStatusCapture Capture(IEnumerable<PowerFixture> sources) =>
        LiveContextReader.CaptureStatuses(sources,
            source => source.VisibilityFails ? throw new InvalidOperationException("Visibility failed") : !source.Hidden,
            source =>
            {
                source.ScalarReads++;
                if (source.ScalarFails) throw new InvalidOperationException("Scalar failed");
                return new VisibleStatus(source.Id, "Public name", 7, "Buff", null);
            },
            source =>
            {
                source.DetailReads++;
                if (source.DetailFails) throw new InvalidOperationException("Optional text failed");
                return "Existing optional rich text";
            });

    private static IEnumerable<PowerFixture> BrokenRoster()
    {
        yield return new PowerFixture("good");
        throw new InvalidOperationException("Native roster enumeration failed");
    }

    private static CombatLiveContext Context(params VisibleEnemy[] enemies) => new("combat", "boss", 1, "player", true,
        new VisibleCombatPlayer("player", 0, 3, 3, null, Array.Empty<VisibleCard>(), 0, 0, 0,
            Array.Empty<VisibleStatus>(), Array.Empty<VisibleCombatCompanion>(), Array.Empty<VisibleCombatPotionState>(),
            Array.Empty<VisibleOrb>(), 0), enemies);

    private static PlayerEnvironmentSnapshot Snapshot(PlayerEnvironmentInteractionContent content,
        IReadOnlyList<PlayerEnvironmentReferent> referents) => new("1.0.0", PlayerEnvironmentContract.SnapshotSchema,
        "source", 1, DateTimeOffset.UnixEpoch, "interactive", null,
        new("interaction", "combat_turn", "ready", null, "sts2.player-environment/surface/combat_turn-1", content,
            Array.Empty<PlayerEnvironmentInteractionCapability>()), referents,
        new("sts2.player-environment/bound-actions-1", "complete", 0, 0, 512, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
        Array.Empty<PlayerEnvironmentReadOpportunity>(), new("complete", "public", "native", Array.Empty<string>(), Array.Empty<string>()),
        new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));
}
