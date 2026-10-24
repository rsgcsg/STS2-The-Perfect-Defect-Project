using System;
using System.Diagnostics.CodeAnalysis;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Multiplayer.Game;
using MegaCrit.Sts2.Core.Nodes.Events;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;

namespace STS2Connector.NativeUi;

// EventRoom.LocalMutableEvent reads the global synchronizer. During native room
// preload it can still return the previous event, with its old UI still mounted.
// Capture and delivery must agree on the actual canonical/model/node owner.
internal static class NativeEventRoomBinding
{
    internal sealed record Current(EventRoom Room, NEventRoom UiRoom, EventModel Event, NEventLayout Layout);

    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "_canonicalEvent")]
    internal static extern ref EventModel? CanonicalEvent(EventSynchronizer synchronizer);

    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "_event")]
    internal static extern ref EventModel MountedEvent(NEventRoom room);

    internal static bool TryCapture(
        EventRoom expectedRoom,
        NEventRoom? expectedUiRoom,
        [NotNullWhen(true)] out Current? current)
    {
        current = null;
        var manager = RunManager.Instance;
        var synchronizer = manager.EventSynchronizer;
        if (!ReferenceEquals(manager.DebugOnlyGetState()?.CurrentRoom, expectedRoom)
            || expectedUiRoom == null
            || !ReferenceEquals(NEventRoom.Instance, expectedUiRoom)
            || !ConnectorMod.IsLiveNode(expectedUiRoom)
            || IsBlockingMapTravel(NMapScreen.Instance?.IsOpen == true, NMapScreen.Instance?.IsTraveling == true)
            || synchronizer == null
            || !ReferenceEquals(expectedRoom.CanonicalEvent, CanonicalEvent(synchronizer))
            || synchronizer.Events.Count != 1)
            return false;

        EventModel model = synchronizer.GetLocalEvent();
        if (model.Node is not NEventLayout layout || !ConnectorMod.IsLiveNode(layout)
            || !Matches(expectedRoom.CanonicalEvent, CanonicalEvent(synchronizer),
                model, MountedEvent(expectedUiRoom), model.Node, expectedUiRoom.Layout,
                IsBlockingMapTravel(NMapScreen.Instance?.IsOpen == true, NMapScreen.Instance?.IsTraveling == true)))
            return false;

        current = new(expectedRoom, expectedUiRoom, model, layout);
        return true;
    }

    // IsTraveling survives TravelToMapCoord until a later map lifecycle reset.
    // Once closed, the map is not the current input owner. Exact event model,
    // mounted node and layout identity still fence the previous room.
    internal static bool IsBlockingMapTravel(bool mapOpen, bool mapTraveling) => mapOpen && mapTraveling;

    internal static bool Matches(
        object? roomCanonicalEvent,
        object? synchronizerCanonicalEvent,
        object? localEvent,
        object? mountedEvent,
        object? modelNode,
        object? currentLayout,
        bool mapTraveling) =>
        !mapTraveling
        && roomCanonicalEvent != null && ReferenceEquals(roomCanonicalEvent, synchronizerCanonicalEvent)
        && localEvent != null && ReferenceEquals(localEvent, mountedEvent)
        && modelNode != null && ReferenceEquals(modelNode, currentLayout);
}
