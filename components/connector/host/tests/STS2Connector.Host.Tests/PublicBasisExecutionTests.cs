using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.Authority;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector;

// Only native acquisition/delivery is detached; the actual SnapshotBuilder,
// aggregate detail projection, identity, original request owner and executor run.
public sealed class PublicBasisExecutionTests
{
    private sealed class Source
    {
        internal readonly RewardPageSnapshotIdentity Identity = new();
        internal string Native = "native-a";
        internal string Screen = "screen-a";
        internal int Deliveries;
        internal bool Unknown;
        internal SnapshotBuildResult Capture(string? profile = PublicBasisContract.Profile, bool sharedRun = false)
        {
            var game = new GameBuildIdentity("exact", "commit", null, 1,
                new("exact", true, true, true, "Fixture"));
            var card = new VisibleCard("card-a", "CARD", "Card", "Skill", "1", null,
                "Visible text", "Common", false, false, null) {
                CurrentPublicDetails = new JsonObject { ["keywords"] = new JsonArray(), ["card_previews"] = new JsonArray() } };
            var surface = new CardRewardSelectionSurface("card_reward_selection", Screen, new[] { card },
                Array.Empty<VisibleCardRewardAlternative>()) { SelectableCardEntityIds = new[] { card.EntityId } };
            var native = new LiveObservation(Native, "ready", new RewardFlowLiveContext("reward_flow", "card_reward"),
                surface, new("contract_complete_for_card_reward_selection", "exact", Array.Empty<string>(), Array.Empty<string>()),
                game, Array.Empty<string>());
            var input = new SnapshotBuildInput(game, native, new(sharedRun,
                sharedRun ? new PersistentVisibleState("active_run", null!, null!, null!) : null, null), Identity) {
                Read = kind => PlayerReadBuildResult.Failure("fixture_read_unavailable", kind) };
            return PlayerEnvironmentService.BuildSnapshot(inputProfile: profile, capturedInput: input);
        }
        internal PlayerEnvironmentActionReceipt Submit(RequestNamespace requests, PlayerEnvironmentActionRequest request) =>
            RequestTestDriver.Submit<PlayerEnvironmentActionReceipt>(requests, request, r =>
                PlayerEnvironmentService.RunAdmittedLegacyCore(r, requests, () => Capture(r.InputProfile),
                    (_, _, _) => { Deliveries++; return Unknown ? NativeInputResult.Unknown("fixture_unknown", "Do not retry")
                        : NativeInputResult.Delivered("fixture_native_input"); }, p => Capture(p).Snapshot));
    }
    private static PlayerEnvironmentActionRequest Request(SnapshotBuildResult offered, string id = "request",
        string? profile = PublicBasisContract.Profile) => new(id, offered.Snapshot.SnapshotId,
            Assert.Single(offered.Snapshot.BoundActions.Actions).BoundActionId, "client", "lease", 1, profile);

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void AggregateCaptureSubmitsWithoutSelfStaleEvenAcrossLegacyObserve(bool interleave)
    {
        var source = new Source(); using var requests = RequestTestDriver.Namespace();
        var legacy = source.Capture(null);
        var offered = source.Capture();
        Assert.NotEqual(legacy.Snapshot.Reads[0].ReadId, offered.Snapshot.Reads[0].ReadId);
        var frozen = PlayerEnvironmentService.FreezePublicBasis(offered, null);
        using var original = JsonDocument.Parse(frozen.Bytes);
        Assert.Equal("2", original.RootElement.GetProperty("revision").GetString());
        Assert.Equal(offered.Snapshot.SnapshotId, frozen.SnapshotId);
        Assert.Equal(PublicBasisContract.Profile, original.RootElement.GetProperty("snapshot").GetProperty("input_profile").GetString());
        if (interleave)
        {
            var again = source.Capture(null);
            Assert.Equal(legacy.Snapshot.SnapshotId, again.Snapshot.SnapshotId);
            Assert.Equal(legacy.Snapshot.Sequence, again.Snapshot.Sequence);
        }
        var receipt = source.Submit(requests, Request(offered));
        Assert.Equal("delivered", receipt.Delivery); Assert.Null(receipt.ReasonCode); Assert.Equal(1, source.Deliveries);
        Assert.Equal(PublicBasisContract.Profile, receipt.InputProfile);
        Assert.Equal(offered.Snapshot.SnapshotId, receipt.Successor!.SnapshotId);
        Assert.Equal(PublicBasisContract.Profile, receipt.Successor.InputProfile);
        var result = requests.Find("request", PublicBasisContract.Profile);
        RequestTestDriver.AssertWireEqual(receipt, RequestTestDriver.Decode<PlayerEnvironmentActionReceipt>(result.Reply!));
        Assert.Null(requests.Find("request", null).Reply);
    }

    [Fact]
    public void AggregateProfileSelectsMapAndMaterializesTheAdvertisedInventory()
    {
        var source = new Source(); var publicBasis = source.Capture(sharedRun: true);
        Assert.Contains(publicBasis.Snapshot.Reads, r => r.Kind == PlayerVisibleReadBuilder.MapKind);
        Assert.Contains(PlayerVisibleReadBuilder.MapKind, publicBasis.ReadBuilds.Keys);
        var legacy = source.Capture(null, sharedRun: true);
        Assert.DoesNotContain(legacy.Snapshot.Reads, r => r.Kind == PlayerVisibleReadBuilder.MapKind);
        Assert.Empty(legacy.ReadBuilds);
    }

    [Theory]
    [InlineData("native")]
    [InlineData("binding")]
    [InlineData("away-and-back")]
    public void RealChangesStillStaleTheOldPublicToken(string change)
    {
        var source = new Source(); using var requests = RequestTestDriver.Namespace(); var offered = source.Capture();
        if (change == "binding") source.Screen = "screen-replaced";
        else source.Native = "native-b";
        if (change == "away-and-back") { source.Capture(null); source.Native = "native-a"; }
        var rejected = source.Submit(requests, Request(offered));
        Assert.Equal("stale_snapshot", rejected.ReasonCode); Assert.Equal("not_delivered", rejected.Delivery);
        Assert.Equal(0, source.Deliveries);
    }

    [Fact]
    public void WrongProfilesCannotReusePublicTokensOrReceipts()
    {
        var source = new Source(); using var requests = RequestTestDriver.Namespace(); var offered = source.Capture();
        var wrong = source.Submit(requests, Request(offered, profile: null));
        Assert.Equal("stale_snapshot", wrong.ReasonCode); Assert.Equal(0, source.Deliveries);
        var unknown = requests.Admit(Request(offered, "unknown-profile", "public-basis-2"));
        Assert.Equal("rejected", unknown.Status); Assert.Equal("invalid_player_environment_action", unknown.Reason);
        Assert.Throws<ArgumentException>(() => source.Capture("public-basis-2"));
        Assert.NotEqual(PlayerEnvironmentService.ActionRequestFingerprint(Request(offered)),
            PlayerEnvironmentService.ActionRequestFingerprint(Request(offered, profile: null)));
    }

    [Fact]
    public void LostControlAndUnknownRetainExistingExecutionFences()
    {
        var source = new Source(); var offered = source.Capture();
        using (var lostControl = RequestTestDriver.Namespace(_ => MutationAdmission.Reject("controller_rejected", "expired")))
        {
            Assert.Equal("controller_rejected", source.Submit(lostControl, Request(offered)).ReasonCode);
            Assert.Equal(0, source.Deliveries);
        }
        using var requests = RequestTestDriver.Namespace(); source.Unknown = true;
        var request = Request(offered); var receipt = source.Submit(requests, request);
        Assert.Equal("unknown", receipt.Delivery); Assert.False(receipt.Retry.Allowed);
        RequestTestDriver.AssertWireEqual(receipt, source.Submit(requests, request));
        Assert.Equal(1, source.Deliveries);
    }
}
