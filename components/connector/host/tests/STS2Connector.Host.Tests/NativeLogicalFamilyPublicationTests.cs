using STS2Connector.Authority;
using System.Text.Json.Nodes;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;
using STS2Platform.GameMod;

namespace STS2Connector;

// Runs the production Service→projector→store→Hub path. Native phases are
// faithful detached fixture inputs here; actual Godot callback guards remain
// exact metadata/source tests plus the separate real-game canary obligation.
public sealed class NativeLogicalFamilyPublicationTests
{
    private sealed class Fixture : IDisposable
    {
        internal TextMenuFrame Frame = MakeFrame("deck", true);
        internal int Captures;
        internal readonly MainThreadWorkQueue Queue = new();
        internal readonly NativeLogicalService Owner;
        internal readonly NativeLogicalSubscription Subscription;
        private readonly RequestNamespace requests = RequestTestDriver.Namespace();
        private readonly MutationControllerCoordinator authority = new("runtime", enableDeadlineTimer: false);
        internal readonly string Reader;
        internal Fixture()
        {
            Reader = authority.Register(new("family-reader", "test", "Family reader", "1")).Client.ClientSessionId;
            Owner = new(() => { Captures++; return Frame; }, () => "run", requests,
                (work, cancellation) => Queue.Enqueue(work, cancellation), () => true, clientActive: authority.IsActiveClient);
            Owner.Initialize();
            Owner.InstallPublicationProfile(NativeLogicalPublicationProfile.ProfileId, NativeLogicalPublicationProfile.DefinitionSha256,
                NativeLogicalPublicationProfile.RequiredCoverage);
            Subscription = Owner.Attach(new(Reader, NativeLogicalProjector.ScopeFields,
                NativeLogicalPublicationProfile.RequiredCoverage, "full_reference")).Subscription!;
        }
        internal NativeLogicalEventAvailability Next(string after, int number)
        {
            var reply = Owner.Hub.AwaitAsync(Reader, Subscription.SubscriptionId, Subscription.ScopeId, after,
                number.ToString("x32"), "any_event", 2000).GetAwaiter().GetResult();
            Assert.Equal("event", reply.Status);
            return Assert.IsType<NativeLogicalEventAvailability>(reply.Event);
        }
        internal NativeLogicalObservation Observe(NativeLogicalEventAvailability value) =>
            NativeLogicalDecoder.Decode<NativeLogicalObservation>(Owner.Store.ExportFrozen(value.Event.CaptureRef!).CopyCaptureBytes());
        public void Dispose() { Owner.Dispose(); requests.Dispose(); }
    }
    private static TextMenuFrame MakeFrame(string owner, bool offered)
    {
        var page = new PlayerEnvironmentSnapshot("1.0.0", PlayerEnvironmentContract.SnapshotSchema, "source", 1,
            DateTimeOffset.UnixEpoch, offered ? "interactive" : "observed", null,
            new("interaction", "held", offered ? "ready" : "observed", null, "surface", new(new JsonObject { ["kind"] = owner }, new JsonObject()),
                Array.Empty<PlayerEnvironmentInteractionCapability>()), Array.Empty<PlayerEnvironmentReferent>(),
            new("sts2.player-environment/bound-actions-1", "complete", 0, 0, 65536, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
            Array.Empty<PlayerEnvironmentReadOpportunity>(), new("complete", "public", "complete", Array.Empty<string>(), Array.Empty<string>()),
            new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));
        return new(page, owner, offered ? new[] { new TextMenuLeaf("binding", "root", "confirm", "Confirm", null,
            Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => NativeInputResult.Delivered("native_callback")) } : Array.Empty<TextMenuLeaf>())
        { GameContinuityId = "run" };
    }
    [Fact]
    public void SourcePhasesFreezeOriginalNoActionClosingAndDistinctReturnedOwnerOccurrences()
    {
        using var fixture = new Fixture();
        var first = fixture.Next(fixture.Subscription.StartingCursor, 1);
        var deck = fixture.Observe(first);
        fixture.Frame = MakeFrame("inspect", true);
        fixture.Owner.Publish("native_information_owner", "Open_returned");
        var opened = fixture.Next(first.Event.Cursor, 2);
        fixture.Frame = MakeFrame("inspect", false);
        fixture.Owner.Publish("native_information_owner", "Close_returned");
        var closing = fixture.Next(opened.Event.Cursor, 3);
        var noAction = fixture.Observe(closing);
        Assert.Equal("observed", noAction.Status);
        Assert.Equal("complete", noAction.Completeness.Status);
        Assert.Empty(fixture.Owner.Store.Catalog(closing.Event.CaptureRef!).Actions);
        fixture.Frame = MakeFrame("deck", true);
        fixture.Owner.Publish("native_information_owner", "information_context_update_returned");
        var returned = fixture.Next(closing.Event.Cursor, 4);
        var deckAgain = fixture.Observe(returned);
        Assert.Equal(deck.Interaction!.Content.Surface["kind"]!.GetValue<string>(), deckAgain.Interaction!.Content.Surface["kind"]!.GetValue<string>());
        Assert.NotEqual(deck.OwnerOccurrence.OccurrenceId, deckAgain.OwnerOccurrence.OccurrenceId);
        Assert.NotEqual(deck.SnapshotId, deckAgain.SnapshotId);
        // Later state never rewrites the original closing observation/catalog.
        Assert.Equal("observed", fixture.Observe(closing).Status);
        Assert.Empty(fixture.Owner.Store.Catalog(closing.Event.CaptureRef!).Actions);
        Assert.Equal(new[] { "1", "2", "3", "4" }, new[] { first, opened, closing, returned }.Select(e => e.Event.PublicationIndex));
    }
    [Fact]
    public void VisibleCloseFreezesSurvivingBackstopCatalogBeforeActualDelayedDeckReturn()
    {
        using var fixture = new Fixture();
        var initial = fixture.Next(fixture.Subscription.StartingCursor, 1);
        fixture.Frame = MakeFrame("inspect", true) with { Leaves = new[]
        {
            new TextMenuLeaf("return_card_inspect", "root", "return_card_inspect", "Close card inspection", null,
                Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => NativeInputResult.Delivered("native_close"))
        } };
        fixture.Owner.Publish("native_information_owner", "Close_returned");
        var withdrawal = fixture.Next(initial.Event.Cursor, 2);
        Assert.Equal("available", withdrawal.Availability);
        Assert.Equal("complete", fixture.Observe(withdrawal).Completeness.Status);
        Assert.Equal("return_card_inspect", Assert.Single(fixture.Owner.Store.Catalog(withdrawal.Event.CaptureRef!).Actions).Verb);
        object inspect = new(), context = new();
        using var witness = ConnectorNativeLogicalInspectionDeparture.Begin(inspect, inspect, inspect, context, true, true, true);
        fixture.Frame = MakeFrame("deck", true);
        bool accounted = fixture.Owner.PublishTracked("native_information_owner", "information_context_update_returned");
        ConnectorNativeLogicalInspectionDeparture.ContextReturned(context, accounted);
        Assert.Equal(InspectionDepartureDisposition.ExistingContextPublication, witness!.Returned(inspect, inspect, true, false, false));
        var returned = fixture.Next(withdrawal.Event.Cursor, 3);
        Assert.Equal("deck", fixture.Observe(returned).Interaction!.Content.Surface["kind"]!.GetValue<string>());
        Assert.Equal("return_card_inspect", Assert.Single(fixture.Owner.Store.Catalog(withdrawal.Event.CaptureRef!).Actions).Verb);
        Assert.Equal(new[] { "1", "2", "3" }, new[] { initial, withdrawal, returned }.Select(value => value.Event.PublicationIndex));
    }

    [Fact]
    public void IncompleteRewardSourceAndFailedRefreshKeepOriginalMissingPositionsWithoutRecapture()
    {
        using var fixture = new Fixture();
        var initial = fixture.Next(fixture.Subscription.StartingCursor, 1);
        fixture.Frame = MakeFrame("reward_claim", true);
        fixture.Frame = fixture.Frame with { Page = fixture.Frame.Page with { Completeness = fixture.Frame.Page.Completeness with
            { Status = "partial", Missing = new[] { "native_reward_owner" } } } };
        fixture.Owner.Publish("native_reward_owner", "overlay_Push_returned");
        var early = fixture.Next(initial.Event.Cursor, 2);
        Assert.Null(early.Event.CaptureRef); Assert.Equal("source_capture_incomplete", early.Event.MissingReason);
        fixture.Frame = MakeFrame("card_reward", true);
        int captures = fixture.Captures;
        fixture.Owner.PublishMissing("native_reward_catalog", "card_reward_refresh_failed", "native_callback_failed");
        var failed = fixture.Next(early.Event.Cursor, 3);
        Assert.Equal(captures, fixture.Captures);
        Assert.Null(failed.Event.CaptureRef); Assert.Equal("native_callback_failed", failed.Event.MissingReason);
        fixture.Owner.Publish("native_reward_owner", "card_reward_show_registered_returned");
        var registered = fixture.Next(failed.Event.Cursor, 4);
        Assert.NotNull(registered.Event.CaptureRef);
        Assert.Null(early.Event.CaptureRef); Assert.Null(failed.Event.CaptureRef);
    }
    [Fact]
    public void FullAdmissionBurstRetainsEveryOriginalSourcePositionAsExplicitMissing()
    {
        using var fixture = new Fixture();
        var initial = fixture.Next(fixture.Subscription.StartingCursor, 1);
        using var cancellation = new CancellationTokenSource();
        var queued = Enumerable.Range(0, 4).Select(_ => fixture.Owner.CurrentAsync(
            new(fixture.Reader, NativeLogicalProjector.ScopeFields, null), cancellation.Token)).ToArray();
        int captures = fixture.Captures;
        for (int i = 0; i < 9; i++) fixture.Owner.Publish("native_reward_input_availability", "reward_holder_clickability_returned");
        cancellation.Cancel();
        foreach (var task in queued) Assert.ThrowsAny<OperationCanceledException>(() => task.GetAwaiter().GetResult());
        Assert.Equal(captures, fixture.Captures);
        var events = fixture.Owner.Hub.Events(fixture.Reader, fixture.Subscription.SubscriptionId, fixture.Subscription.ScopeId, initial.Event.Cursor);
        Assert.Null(events.Gap); Assert.Equal(9, events.Events.Count);
        Assert.Equal(Enumerable.Range(2, 9).Select(i => i.ToString()), events.Events.Select(e => e.Event.PublicationIndex));
        Assert.All(events.Events, e => { Assert.Null(e.Event.CaptureRef); Assert.Equal("encoding_capacity_exceeded", e.Event.MissingReason); });
    }
    [Theory]
    [InlineData("card_reward")]
    [InlineData("combat_turn")]
    [InlineData("map_navigation")]
    public void ActualVisibilityBeforeUpdateOrderingPublishesNonInformationReturnOnce(string underlying)
    {
        using var fixture = new Fixture();
        var initial = fixture.Next(fixture.Subscription.StartingCursor, 1);
        object inspect = new(), context = new();
        // Native callback begins with the exact current visible inspector.
        using var witness = ConnectorNativeLogicalInspectionDeparture.Begin(inspect, inspect, inspect, context, true, true, true);
        Assert.NotNull(witness);
        // Exact native body changes Visible=false BEFORE Update. Both old
        // information-family guards are already false for these real owner types.
        fixture.Frame = MakeFrame(underlying, true);
        ConnectorNativeLogicalInspectionDeparture.ContextReturned(context, false);
        Assert.Equal(InspectionDepartureDisposition.Publish, witness.Returned(inspect, inspect, true, false, false));
        Assert.True(fixture.Owner.PublishTracked("native_information_owner", "information_context_update_returned"));
        var returned = fixture.Next(initial.Event.Cursor, 2);
        Assert.Equal(underlying, fixture.Observe(returned).Interaction!.Content.Surface["kind"]!.GetValue<string>());
        Assert.Single(fixture.Owner.Hub.Events(fixture.Reader, fixture.Subscription.SubscriptionId,
            fixture.Subscription.ScopeId, initial.Event.Cursor).Events);
        // No later current read, clock, timer, or remembered-family marker repairs history.
        witness.Dispose();
        ConnectorNativeLogicalInspectionDeparture.ContextReturned(context, true);
        Assert.Equal(InspectionDepartureDisposition.Missing, witness.Returned(inspect, inspect, true, false, false));
    }
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void ActualUpdateAccountingPreventsDeckAndSubscriberChangedOwnerDuplicate(bool subscriberChangesOwner)
    {
        using var fixture = new Fixture();
        var initial = fixture.Next(fixture.Subscription.StartingCursor, 1);
        object inspect = new(), context = new();
        using var witness = ConnectorNativeLogicalInspectionDeparture.Begin(inspect, inspect, inspect, context, true, true, true);
        fixture.Frame = MakeFrame(subscriberChangesOwner ? "combat_turn" : "deck", true);
        // Update prefix saw deck. Updated subscribers may switch to combat;
        // existing postfix still publishes because its issued __state is true.
        bool accounted = fixture.Owner.PublishTracked("native_information_owner", "information_context_update_returned");
        ConnectorNativeLogicalInspectionDeparture.ContextReturned(context, accounted);
        Assert.Equal(InspectionDepartureDisposition.ExistingContextPublication, witness!.Returned(inspect, inspect, true, false, false));
        var returned = fixture.Next(initial.Event.Cursor, 2);
        Assert.Equal(subscriberChangesOwner ? "combat_turn" : "deck",
            fixture.Observe(returned).Interaction!.Content.Surface["kind"]!.GetValue<string>());
        Assert.Single(fixture.Owner.Hub.Events(fixture.Reader, fixture.Subscription.SubscriptionId,
            fixture.Subscription.ScopeId, initial.Event.Cursor).Events);
    }
    [Fact]
    public void ExplicitMissingOriginalUpdateIsNeverRecapturedByDepartureFinalizer()
    {
        using var fixture = new Fixture();
        var initial = fixture.Next(fixture.Subscription.StartingCursor, 1);
        object inspect = new(), context = new();
        using var witness = ConnectorNativeLogicalInspectionDeparture.Begin(inspect, inspect, inspect, context, true, true, true);
        fixture.Frame = fixture.Frame with { Page = fixture.Frame.Page with { Completeness = fixture.Frame.Page.Completeness with
            { Status = "partial", Missing = new[] { "native_reward_owner" } } } };
        bool accounted = fixture.Owner.PublishTracked("native_information_owner", "information_context_update_returned");
        ConnectorNativeLogicalInspectionDeparture.ContextReturned(context, accounted);
        fixture.Frame = MakeFrame("combat_turn", true);
        Assert.Equal(InspectionDepartureDisposition.ExistingContextPublication, witness!.Returned(inspect, inspect, true, false, false));
        var missing = fixture.Next(initial.Event.Cursor, 2);
        Assert.Null(missing.Event.CaptureRef); Assert.Equal("source_capture_incomplete", missing.Event.MissingReason);
        Assert.Single(fixture.Owner.Hub.Events(fixture.Reader, fixture.Subscription.SubscriptionId,
            fixture.Subscription.ScopeId, initial.Event.Cursor).Events);
    }

}
