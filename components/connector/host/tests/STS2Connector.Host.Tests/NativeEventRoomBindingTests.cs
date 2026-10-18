using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Events;
using MegaCrit.Sts2.Core.Multiplayer.Game;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using STS2Connector.LiveHost;
using STS2Connector.NativeUi;

namespace STS2Connector.Tests;

public sealed class NativeEventRoomBindingTests
{
    private static T Uninitialized<T>() where T : class =>
        (T)RuntimeHelpers.GetUninitializedObject(typeof(T));

    [Fact]
    public void NativeOwnerFieldsResolveThePinnedActualObjects()
    {
        var synchronizer = Uninitialized<EventSynchronizer>();
        var uiRoom = Uninitialized<NEventRoom>();
        var canonical = Uninitialized<SlipperyBridge>();
        var local = Uninitialized<SlipperyBridge>();

        NativeEventRoomBinding.CanonicalEvent(synchronizer) = canonical;
        NativeEventRoomBinding.MountedEvent(uiRoom) = local;

        Assert.Same(canonical, NativeEventRoomBinding.CanonicalEvent(synchronizer));
        Assert.Same(local, NativeEventRoomBinding.MountedEvent(uiRoom));
    }

    [Fact]
    public void EventToMapToNewEventDoesNotRepublishPreviousContinueDuringPreload()
    {
        EventModel bridge = Uninitialized<SlipperyBridge>();
        EventModel oldLocal = Uninitialized<SlipperyBridge>();
        EventModel stone = Uninitialized<StoneOfAllTime>();
        EventModel newLocal = Uninitialized<StoneOfAllTime>();
        object oldLayout = new(), newLayout = new();

        Assert.True(NativeEventRoomBinding.Matches(
            bridge, bridge, oldLocal, oldLocal, oldLayout, oldLayout, mapTraveling: false));

        // Native TravelToMapCoord retains the previous UI while the next room
        // is pushed and its event assets are awaited (Human02 source487).
        Assert.False(NativeEventRoomBinding.Matches(
            bridge, bridge, oldLocal, oldLocal, oldLayout, oldLayout, mapTraveling: true));
        bool ownerAligned = NativeEventRoomBinding.Matches(
            stone, bridge, oldLocal, oldLocal, oldLayout, oldLayout, mapTraveling: false);
        Assert.False(ownerAligned);
        Assert.True(LiveObservationReader.ClassifyEventRoomMountNoInputTransition(
            runInProgress: true, currentRoomIsEvent: true, hasBlockingSurface: false,
            sourceType: "run_without_visible_overlay", eventRoomNodePresent: ownerAligned));

        // BeginEvent can finish before NRun replaces the old event room node.
        Assert.False(NativeEventRoomBinding.Matches(
            stone, stone, newLocal, oldLocal, newLayout, oldLayout, mapTraveling: false));
        Assert.True(NativeEventRoomBinding.Matches(
            stone, stone, newLocal, newLocal, newLayout, newLayout, mapTraveling: false));
    }

    [Fact]
    public void SameEventDefinitionDoesNotSubstituteForExactCanonicalOrLocalOwner()
    {
        EventModel current = Uninitialized<SlipperyBridge>();
        EventModel other = Uninitialized<SlipperyBridge>();
        object layout = new();
        Assert.False(NativeEventRoomBinding.Matches(
            current, other, current, current, layout, layout, mapTraveling: false));
        Assert.False(NativeEventRoomBinding.Matches(
            current, current, current, other, layout, layout, mapTraveling: false));
        Assert.False(NativeEventRoomBinding.Matches(
            current, current, current, current, layout, new object(), mapTraveling: false));
    }

    [Theory]
    [InlineData(0)]
    [InlineData(1)]
    [InlineData(2)]
    public void MissingCanonicalModelOrLayoutNeverOwnsInput(int missing)
    {
        object canonical = new(), local = new(), layout = new();
        Assert.False(NativeEventRoomBinding.Matches(
            missing == 0 ? null : canonical, canonical,
            missing == 1 ? null : local, local,
            missing == 2 ? null : layout, layout, mapTraveling: false));
    }
}
