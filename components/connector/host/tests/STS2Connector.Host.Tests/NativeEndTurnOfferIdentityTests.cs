using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

// The native object source and callback are mocked, while binding identity,
// action projection, snapshot identity and original-request executors are real.
// Public content remains identical across offer and the private-only replacement.
public sealed class NativeEndTurnOfferIdentityTests
{
    private sealed class CaptureSource
    {
        internal NativeEndTurnInput.Scope Scope = new(new(), new(), new(), new(), new(), new(), 1);
        internal readonly NativeEntityRegistry Entities = new();
        private readonly RewardPageSnapshotIdentity identity = new();
        internal int Calls;
        private static readonly GameBuildIdentity Game = new("exact", "commit", null, 1,
            new("exact", true, true, true, "Fixture"));
        internal SnapshotBuildResult Snapshot()
        {
            var observation = new LiveObservation("same-public-source", "ready", new MenuLiveContext("menu", "fixture"),
                new CombatTurnSurface("combat_turn", "room", true),
                new("contract_complete_for_immediate_combat_turn", "native", Array.Empty<string>(), Array.Empty<string>()),
                Game, Array.Empty<string>()) { EndTurnBindingIdentity = NativeEndTurnInput.Identity(Entities, Scope) };
            var bindings = NativeUiActionRuntime.BuildBindings(observation);
            var catalog = PlayerEnvironmentService.ProjectBoundActions(bindings, "room",
                new Dictionary<string, PlayerEnvironmentReferent>());
            string signature = PlayerEnvironmentService.CanonicalAuthoritySignature(bindings);
            long native = identity.ObserveNative(PlayerEnvironmentService.CommonNativeSignature(Game, observation, null));
            var state = identity.ObserveLegacy(native, signature);
            var page = new PlayerEnvironmentSnapshot("1.0.0", PlayerEnvironmentContract.SnapshotSchema, state.StateId,
                state.Sequence, DateTimeOffset.UnixEpoch, "interactive", null,
                new("room", "combat_turn", "ready", null, "fixture", new(new JsonObject { ["kind"] = "combat_turn" }, new JsonObject()),
                    Array.Empty<PlayerEnvironmentInteractionCapability>()),
                Array.Empty<PlayerEnvironmentReferent>(), catalog.Projection, Array.Empty<PlayerEnvironmentReadOpportunity>(),
                new("complete", "complete", "complete", Array.Empty<string>(), Array.Empty<string>()),
                new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));
            return new(page, observation, catalog.Bindings, new Dictionary<string, PlayerReadBuildResult>());
        }
        internal TextMenuFrame Frame()
        {
            var capture = Snapshot();
            var action = Assert.Single(capture.Snapshot.BoundActions.Actions);
            var binding = capture.Bindings[action.BoundActionId];
            // This is the same shared BoundAction->leaf path used by actual
            // text1/text2/native-logical capture; no independent key is invented.
            var leaf = NativeTextMenuFrameBuilder.FromLegacy(action, binding, _ => Deliver());
            return new(capture.Snapshot, "same-room-owner", new[] { leaf })
            { GameContinuityId = "run", CardPlayCatalogComplete = true };
        }
        internal NativeInputResult Deliver() { Calls++; return NativeInputResult.Delivered("mock-native-input-request"); }
        internal void Replace(string member) => Scope = member switch
        {
            "button" => Scope with { Button = new() }, "combat" => Scope with { Combat = new() },
            "turn" => Scope with { TurnNumber = Scope.TurnNumber + 1 }, _ => throw new ArgumentException(member)
        };
    }
    private static PlayerEnvironmentActionRequest Request(string? profile, string snapshot, string action, string request) =>
        new(request, snapshot, action, "client", "lease", 1, profile);

    [Theory]
    [InlineData("button")]
    [InlineData("combat")]
    [InlineData("turn")]
    public void GenericSnapshotOriginalOfferCannotRebindToPrivateReplacement(string member)
    {
        var source = new CaptureSource(); using var requests = RequestTestDriver.Namespace();
        bool Run(PlayerEnvironmentActionRequest request) => PlayerEnvironmentService.RunAdmittedLegacyCore(request, requests,
            source.Snapshot, (_, _, _) => source.Deliver(), _ => source.Snapshot().Snapshot);
        var offered = source.Snapshot(); var old = Assert.Single(offered.Snapshot.BoundActions.Actions);
        Assert.Equal(offered.Snapshot.SnapshotId, source.Snapshot().Snapshot.SnapshotId);
        source.Replace(member);
        var request = Request(null, offered.Snapshot.SnapshotId, old.BoundActionId, "old-offer");
        var rejected = RequestTestDriver.Submit<PlayerEnvironmentActionReceipt>(requests, request, Run);
        Assert.Equal("not_delivered", rejected.Delivery); Assert.Equal("stale_snapshot", rejected.ReasonCode);
        RequestTestDriver.AssertWireEqual(rejected, RequestTestDriver.Submit<PlayerEnvironmentActionReceipt>(requests, request, Run));
        Assert.Equal(0, source.Calls);
        // No client refresh precedes the original submission: delivery-time
        // capture itself must reject the offered binding's replacement.
        var fresh = source.Snapshot(); var next = Assert.Single(fresh.Snapshot.BoundActions.Actions);
        Assert.Equal(JsonSerializer.Serialize(offered.HostObservation.Surface), JsonSerializer.Serialize(fresh.HostObservation.Surface));
        Assert.Equal(JsonSerializer.Serialize(offered.HostObservation.Context), JsonSerializer.Serialize(fresh.HostObservation.Context));
        Assert.NotEqual(offered.Snapshot.SnapshotId, fresh.Snapshot.SnapshotId); Assert.NotEqual(old.BoundActionId, next.BoundActionId);
        var oldHandle = RequestTestDriver.Submit<PlayerEnvironmentActionReceipt>(requests,
            Request(null, fresh.Snapshot.SnapshotId, old.BoundActionId, "old-handle"), Run);
        Assert.Equal("bound_action_not_current", oldHandle.ReasonCode); Assert.Equal(0, source.Calls);
        var accepted = RequestTestDriver.Submit<PlayerEnvironmentActionReceipt>(requests,
            Request(null, fresh.Snapshot.SnapshotId, next.BoundActionId, "fresh-offer"), Run);
        Assert.Equal("delivered", accepted.Delivery); Assert.Equal(1, source.Calls);
    }

    [Theory]
    [InlineData(TextMenuContract.Profile, "button")]
    [InlineData(TextMenuContract.Profile, "combat")]
    [InlineData(TextMenuContract.Profile, "turn")]
    [InlineData(TextMenuV2Contract.Profile, "button")]
    [InlineData(TextMenuV2Contract.Profile, "combat")]
    [InlineData(TextMenuV2Contract.Profile, "turn")]
    public void OriginalTextOfferCannotRebindToPrivateReplacement(string profile, string member)
    {
        var source = new CaptureSource(); using var requests = RequestTestDriver.Namespace();
        var v1 = new TextMenuExecutor(new object(), requests, source.Frame);
        var v2 = new TextMenuV2Executor(new object(), requests, source.Frame);
        (string Snapshot, string Action) Observe()
        {
            if (profile == TextMenuContract.Profile)
            { var s = v1.Observe(); return (s.SnapshotId, Assert.Single(s.MenuActions.Actions).ActionId); }
            var v = v2.Observe(); return (v.SnapshotId, Assert.Single(v.MenuActions.Actions).ActionId);
        }
        bool Run(PlayerEnvironmentActionRequest request) => profile == TextMenuContract.Profile ? v1.RunAdmitted(request) : v2.RunAdmitted(request);
        var offered = Observe(); Assert.Equal(offered, Observe()); source.Replace(member);
        var request = Request(profile, offered.Snapshot, offered.Action, "old-offer");
        if (profile == TextMenuContract.Profile)
        {
            var rejected = RequestTestDriver.Submit<TextMenuActionResult>(requests, request, Run);
            Assert.Equal("stale_snapshot", rejected.ReasonCode);
            RequestTestDriver.AssertWireEqual(rejected, RequestTestDriver.Submit<TextMenuActionResult>(requests, request, Run));
            Assert.Equal(0, source.Calls); var fresh = Observe();
            Assert.NotEqual(offered.Snapshot, fresh.Snapshot); Assert.NotEqual(offered.Action, fresh.Action);
            Assert.Equal("menu_action_not_current", RequestTestDriver.Submit<TextMenuActionResult>(requests,
                Request(profile, fresh.Snapshot, offered.Action, "old-handle"), Run).ReasonCode);
            Assert.Equal("applied", RequestTestDriver.Submit<TextMenuActionResult>(requests,
                Request(profile, fresh.Snapshot, fresh.Action, "fresh-offer"), Run).Status);
        }
        else
        {
            var rejected = RequestTestDriver.Submit<TextMenuV2ActionResult>(requests, request, Run);
            Assert.Equal("stale_snapshot", rejected.ReasonCode);
            RequestTestDriver.AssertWireEqual(rejected, RequestTestDriver.Submit<TextMenuV2ActionResult>(requests, request, Run));
            Assert.Equal(0, source.Calls); var fresh = Observe();
            Assert.NotEqual(offered.Snapshot, fresh.Snapshot); Assert.NotEqual(offered.Action, fresh.Action);
            Assert.Equal("menu_action_not_current", RequestTestDriver.Submit<TextMenuV2ActionResult>(requests,
                Request(profile, fresh.Snapshot, offered.Action, "old-handle"), Run).ReasonCode);
            Assert.Equal("applied", RequestTestDriver.Submit<TextMenuV2ActionResult>(requests,
                Request(profile, fresh.Snapshot, fresh.Action, "fresh-offer"), Run).Status);
        }
        Assert.Equal(1, source.Calls);
    }

    [Theory]
    [InlineData("button")]
    [InlineData("combat")]
    [InlineData("turn")]
    public void OriginalNativeLogicalOfferCannotRebindToPrivateReplacement(string member)
    {
        var source = new CaptureSource(); using var requests = RequestTestDriver.Namespace();
        var queue = new MainThreadWorkQueue();
        using var owner = new NativeLogicalService(source.Frame, () => "run", requests,
            (work, token) => queue.Enqueue(work, token), () => true, clientActive: _ => true);
        owner.Initialize();
        NativeLogicalCurrentReply Observe()
        { var task = owner.CurrentAsync(new("client", NativeLogicalProjector.ScopeFields, null)); queue.Drain(1); return task.GetAwaiter().GetResult(); }
        var offered = Observe(); var old = Assert.Single(owner.Store.Catalog(offered.Capture!.CaptureId).Actions);
        Assert.Equal(offered.Capture.SnapshotId, Observe().Capture!.SnapshotId);
        source.Replace(member);
        var request = Request(NativeLogicalContract.Profile, offered.Capture.SnapshotId, old.ActionId, "old-offer");
        var rejected = RequestTestDriver.Submit<NativeLogicalResult>(requests, request, owner.RunAdmitted);
        Assert.Equal("not_started", rejected.Delivery); Assert.Equal("stale_snapshot_or_binding", rejected.Reason);
        RequestTestDriver.AssertWireEqual(rejected, RequestTestDriver.Submit<NativeLogicalResult>(requests, request, owner.RunAdmitted));
        Assert.Equal(0, source.Calls);
        var fresh = Observe(); var next = Assert.Single(owner.Store.Catalog(fresh.Capture!.CaptureId).Actions);
        Assert.NotEqual(offered.Capture.SnapshotId, fresh.Capture.SnapshotId); Assert.NotEqual(old.ActionId, next.ActionId);
        var oldHandle = RequestTestDriver.Submit<NativeLogicalResult>(requests,
            Request(NativeLogicalContract.Profile, fresh.Capture.SnapshotId, old.ActionId, "old-handle"), owner.RunAdmitted);
        Assert.Equal("not_started", oldHandle.Delivery); Assert.Equal(0, source.Calls);
        var accepted = RequestTestDriver.Submit<NativeLogicalResult>(requests,
            Request(NativeLogicalContract.Profile, fresh.Capture.SnapshotId, next.ActionId, "fresh-offer"), owner.RunAdmitted);
        Assert.Equal("delivered", accepted.Delivery); Assert.Equal(1, source.Calls);
    }

    [Fact]
    public void PrivateNativeAliasesAndCapturedTurnNeverBecomeWireFactsOrOperands()
    {
        var source = new CaptureSource(); var captured = source.Snapshot();
        var binding = Assert.Single(captured.Bindings.Values).NativeAction.Candidate;
        Assert.Empty(binding.Operands); Assert.Empty(binding.EntityBindings);
        string wire = JsonSerializer.Serialize(captured.Snapshot, ConnectorMod._jsonOptions);
        Assert.DoesNotContain("end_turn_button", wire); Assert.DoesNotContain("end_turn_combat", wire);
        Assert.DoesNotContain(source.Entities.GetId(source.Scope.Button, "end_turn_button"), wire);
        Assert.DoesNotContain(source.Entities.GetId(source.Scope.Combat, "end_turn_combat"), wire);
        Assert.DoesNotContain(captured.HostObservation.EndTurnBindingIdentity!, wire);
    }

    [Fact]
    public void PrivateEndTurnReplacementDoesNotChangeOtherCandidateIdentities()
    {
        var source = new CaptureSource();
        LiveObservation Capture()
        {
            var observation = source.Snapshot().HostObservation;
            return observation with { Surface = ((CombatTurnSurface)observation.Surface) with
            {
                PlayableCards = new[] { new VisibleCombatCommandOption("card", "Card", Array.Empty<string>()) },
                UsablePotions = new[] { new VisibleCombatCommandOption("potion", "Potion", Array.Empty<string>()) }
            } };
        }
        var before = NativeUiActionRuntime.BuildBindings(Capture()).ToDictionary(b => b.Candidate.Operation);
        source.Replace("button");
        var after = NativeUiActionRuntime.BuildBindings(Capture()).ToDictionary(b => b.Candidate.Operation);
        Assert.Equal(before["play_card"].Candidate.CandidateId, after["play_card"].Candidate.CandidateId);
        Assert.Equal(before["use_potion"].Candidate.CandidateId, after["use_potion"].Candidate.CandidateId);
        Assert.NotEqual(before["end_turn"].Candidate.CandidateId, after["end_turn"].Candidate.CandidateId);
    }
}
