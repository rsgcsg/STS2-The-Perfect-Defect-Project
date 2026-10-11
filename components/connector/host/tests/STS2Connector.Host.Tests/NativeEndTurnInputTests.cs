using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using System.Text.Json.Nodes;
using Xunit;

namespace STS2Connector;

public sealed class NativeEndTurnInputTests
{
    private static NativeEndTurnInput.Scope Scope() => new(new(), new(), new(), new(), new(), new(), 1);

    [Fact]
    public void ExactCompletedGestureRequestsOnceAndDoesNotClaimQueuedExecution()
    {
        var expected = Scope(); int calls = 0;
        var result = NativeEndTurnInput.Dispatch(expected, expected with { }, null, () => calls++);
        Assert.True(result.Accepted); Assert.Equal(1, calls);
        Assert.Equal("native_end_turn_input_request_accepted", result.DeliveryEvidence);
    }

    [Theory]
    [InlineData("model_room")]
    [InlineData("room")]
    [InlineData("player")]
    [InlineData("hand")]
    [InlineData("button")]
    [InlineData("combat")]
    [InlineData("turn")]
    [InlineData("missing")]
    public void ExactOwnerDriftNeverCallsTheNativeGesture(string field)
    {
        var expected = Scope();
        var current = field switch
        {
            "model_room" => expected with { ModelRoom = new() }, "room" => expected with { Room = new() },
            "player" => expected with { Player = new() }, "hand" => expected with { Hand = new() },
            "button" => expected with { Button = new() }, "combat" => expected with { Combat = new() },
            "turn" => expected with { TurnNumber = 2 }, _ => null
        };
        int calls = 0;
        var result = NativeEndTurnInput.Dispatch(expected, current, null, () => calls++);
        Assert.False(result.Accepted); Assert.Equal(0, calls);
        Assert.Equal("native_end_turn_binding_changed", result.ErrorCode);
    }

    [Theory]
    [InlineData(false, false, true, false, true, NativeEndTurnInput.BindingUnavailable)]
    [InlineData(true, true, true, false, true, NativeEndTurnInput.BindingUnavailable)]
    [InlineData(true, false, false, false, true, NativeEndTurnInput.BindingUnavailable)]
    [InlineData(true, false, true, true, true, NativeEndTurnInput.BindingUnavailable)]
    [InlineData(true, false, true, false, false, NativeEndTurnInput.FtueUnseen)]
    [InlineData(true, false, true, false, null, NativeEndTurnInput.FtueUnknown)]
    public void ReadinessAndFtueFailuresNeverCallTheNativeGesture(bool play, bool selecting,
        bool enabled, bool ready, bool? seen, string missing)
    {
        var expected = Scope(); int calls = 0;
        string? reason = NativeEndTurnInput.ReadinessMissing(play, selecting, enabled, ready, seen);
        Assert.Equal(missing, reason);
        var result = NativeEndTurnInput.Dispatch(expected, expected, reason, () => calls++);
        Assert.False(result.Accepted); Assert.Equal(missing, result.ErrorCode); Assert.Equal(0, calls);
    }

    [Fact]
    public void PossibleNativeRequestThenExceptionRemainsUnknown()
    {
        var expected = Scope(); int calls = 0;
        var result = NativeEndTurnInput.Dispatch(expected, expected, null,
            () => { calls++; throw new InvalidOperationException("after possible native request"); });
        Assert.Equal(1, calls); Assert.Equal(NativeInputDelivery.Unknown, result.Delivery);
        Assert.Equal("native_end_turn_input_unknown", result.ErrorCode);
    }

    [Theory]
    [InlineData(NativeEndTurnInput.FtueUnseen)]
    [InlineData(NativeEndTurnInput.FtueUnknown)]
    [InlineData(NativeEndTurnInput.BindingUnavailable)]
    public void UnsupportedEndTurnClosesWholeCaptureBeforeSnapshotAndTextAuthority(string missing)
    {
        var surface = new CombatTurnSurface("combat_turn", "room", true)
        { PlayableCards = new[] { new VisibleCombatCommandOption("card", "Card", Array.Empty<string>()) } };
        var game = new GameBuildIdentity("exact", "commit", null, 1, new("exact", true, true, true, "Fixture"));
        var before = new LiveObservation("source", "ready", new MenuLiveContext("menu", "fixture"), surface,
            new("contract_complete_for_immediate_combat_turn", "native", Array.Empty<string>(), Array.Empty<string>()),
            game, Array.Empty<string>());
        Assert.Equal(2, NativeUiActionRuntime.BuildBindings(before).Count);
        Assert.Single(PlayerEnvironmentService.DescribeTextCombatCommands(surface));
        var closed = CombatTurnSurfaceReader.ApplyEndTurnCapturePolicy(before, missing);
        Assert.IsType<UnsupportedSurface>(closed.Surface);
        Assert.Equal("unsupported", closed.Readiness); Assert.Equal("partial", closed.Completeness.PlayerVisibleSemantics);
        Assert.Contains(missing, closed.Completeness.Missing);
        Assert.Equal("empty_fail_closed", closed.Completeness.InteractionDiscovery);
        Assert.Equal("none_fail_closed", closed.InputOwnership.Status);
        Assert.Empty(NativeUiActionRuntime.BuildBindings(closed));
        Assert.False(PlayerEnvironmentService.CanPublishTextCombatEntry(true, closed.Surface, closed.Readiness,
            closed.Completeness, new("fixture", "complete", 0, 0, 10, "native", Array.Empty<global::STS2Connector.PlayerEnvironment.Protocol.PlayerEnvironmentBoundAction>())));
        Assert.Same(before, CombatTurnSurfaceReader.ApplyEndTurnCapturePolicy(before, null));
    }

    [Theory]
    [InlineData(null, NativeEndTurnInput.FtueUnseen)]
    [InlineData(TextMenuContract.Profile, NativeEndTurnInput.FtueUnknown)]
    [InlineData(TextMenuV2Contract.Profile, NativeEndTurnInput.FtueUnseen)]
    [InlineData(NativeLogicalContract.Profile, NativeEndTurnInput.FtueUnknown)]
    public void UnsupportedFtueCannotBecomeAFilteredCompleteCatalogInAnyCaptureProfile(string? profile, string missing)
    {
        var game = new GameBuildIdentity("exact", "commit", null, 1, new("exact", true, true, true, "Fixture"));
        var closed = CombatTurnSurfaceReader.ApplyEndTurnCapturePolicy(new("source", "ready",
            new MenuLiveContext("menu", "fixture"), new CombatTurnSurface("combat_turn", "room", true),
            new("contract_complete_for_immediate_combat_turn", "native", Array.Empty<string>(), Array.Empty<string>()),
            game, Array.Empty<string>()), missing);
        var page = new PlayerEnvironmentSnapshot("1.0.0", PlayerEnvironmentContract.SnapshotSchema, "snapshot", 1,
            DateTimeOffset.UnixEpoch, "visible_unsupported", null,
            new("room", "combat_turn", "unsupported", null, "fixture", new(new JsonObject(), new JsonObject()), Array.Empty<PlayerEnvironmentInteractionCapability>()),
            Array.Empty<PlayerEnvironmentReferent>(), new("fixture", "complete", 0, 0, 512, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
            Array.Empty<PlayerEnvironmentReadOpportunity>(), new("partial", "partial", "empty_fail_closed", new[] { missing }, Array.Empty<string>()),
            new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit")) { InputProfile = profile };
        var snapshot = new SnapshotBuildResult(page, closed,
            new Dictionary<string, PlayerEnvironmentNativeBinding>(), new Dictionary<string, PlayerReadBuildResult>());
        int calls = 0;
        var frame = NativeTextMenuFrameBuilder.Capture(snapshot, new NativeEntityRegistry(), _ =>
            { calls++; return NativeInputResult.Delivered("must-not-run"); }, nativeLogical: profile == NativeLogicalContract.Profile);
        Assert.Empty(frame.Leaves); Assert.Equal(0, calls);
        Assert.Equal("visible_unsupported", frame.Page.Status); Assert.Equal("partial", frame.Page.Completeness.Status);
        Assert.Contains(missing, frame.Page.Completeness.Missing); Assert.Empty(frame.Page.BoundActions.Actions);
    }
}
