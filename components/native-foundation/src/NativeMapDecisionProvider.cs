using System;
using System.Collections.Generic;
using System.Linq;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Runs;

namespace STS2Platform.NativeFoundation;

/// <summary>
/// Projects map destinations from the current RunState and the shipped
/// MapTravel rule. Presentation state may suppress delivery, but cannot add a
/// destination to this catalog.
/// </summary>
public static class NativeMapDecisionProvider
{
    public static NativeMapDecision Capture(INativeReferentIdentity identities)
    {
        try
        {
            RunState? run = RunManager.Instance.DebugOnlyGetState();
            return run == null
                ? Unavailable("run_state_unavailable", "The active run state is unavailable.")
                : Capture(run, identities);
        }
        catch (Exception exception)
        {
            return Unavailable(
                "capture_failed",
                $"{exception.GetType().Name}: {exception.Message}");
        }
    }

    public static NativeMapDecision Capture(
        RunState run,
        INativeReferentIdentity identities)
    {
        try
        {
            MapPoint[] destinations = GetDestinations(run)
                .Distinct()
                .OrderBy(point => point.coord.row)
                .ThenBy(point => point.coord.col)
                .ToArray();
            NativeSemanticAction[] actions = destinations
                .Select(point =>
                {
                    string id = identities.GetId(point, "map_point");
                    return new NativeSemanticAction(
                        NativeSemanticActionCatalog.BuildKey("travel", id),
                        "travel",
                        id,
                        point,
                        Array.Empty<NativeSemanticOperand>(),
                        "RunState.Map+MapTravel.GetTravelablePointsFrom");
                })
                .ToArray();
            return new NativeMapDecision(
                "captured",
                "map_navigation",
                actions.Length > 0,
                actions,
                new[]
                {
                    "RunState.CurrentMapPoint+VisitedMapCoords",
                    "ActMap.StartingMapPoint+BossMapPoint+SecondBossMapPoint",
                    "MapTravel.GetTravelablePointsFrom"
                },
                actions.Length == 0 ? "No native map destination is available." : null);
        }
        catch (Exception exception)
        {
            return Unavailable(
                "capture_failed",
                $"{exception.GetType().Name}: {exception.Message}");
        }
    }

    internal static IReadOnlyList<MapPoint> GetDestinations(RunState run) =>
        NativeMapReadProjection.GetDestinations(run);

    internal static IReadOnlyList<MapPoint> GetDestinations(ActMap map, IReadOnlyList<MapCoord> visited,
        MapPoint? current, Func<MapPoint, IEnumerable<MapPoint>> travelableFrom) =>
        NativeMapReadProjection.GetDestinations(map, visited, current, travelableFrom);

    private static NativeMapDecision Unavailable(string status, string detail) =>
        new(
            status,
            "unavailable",
            false,
            Array.Empty<NativeSemanticAction>(),
            Array.Empty<string>(),
            detail);
}
