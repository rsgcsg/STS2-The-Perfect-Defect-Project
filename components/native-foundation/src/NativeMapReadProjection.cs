using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Hooks;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Modifiers;
using MegaCrit.Sts2.Core.Models.Relics;
using MegaCrit.Sts2.Core.Runs;

namespace STS2Platform.NativeFoundation;

public sealed record NativeMapReadNode(MapPoint Point, MapCoord Coordinate, string PointType,
    string State, IReadOnlyList<MapCoord> Children);
public sealed record NativeMapReadResult(string Status, int ActIndex, MapCoord? Current,
    IReadOnlyList<MapCoord> Visited, IReadOnlyList<NativeMapReadNode> Nodes, string? Gap);

/// <summary>
/// Current generated map only. The same native destination owner is shared by
/// action catalogs and detached information. No UI, generation or input call.
/// </summary>
public static class NativeMapReadProjection
{
    private const BindingFlags InstanceFlags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    private static readonly Guid GameMvid = new("57785517-0b16-42b9-8b36-bad6fb28384b");
    private static bool Unpatched(MethodBase? method) => NativeRunHookReadQualification.IsUnpatched(method);
    private static MethodInfo? Getter(Type type, string name) => type.GetProperty(name, InstanceFlags)?.GetMethod;
    private static bool SimpleNativeMapGetter(Type type, string name)
    {
        MethodInfo? method = Getter(type, name);
        if (method?.Module.ModuleVersionId != GameMvid || !Unpatched(method)) return false;
        byte[]? body = method.GetMethodBody()?.GetILAsByteArray();
        // Exact shipped field getters (ldarg.0; ldfld token; ret), or the
        // native base optional-second-boss getter (ldnull; ret). No callbacks.
        return body is { Length: 7 } && body[0] == 0x02 && body[1] == 0x7b && body[6] == 0x2a
            || body is { Length: 2 } && body[0] == 0x14 && body[1] == 0x2a;
    }
    private static bool IteratorUnpatched(MethodInfo? method)
    {
        Type? machine = method?.GetCustomAttribute<IteratorStateMachineAttribute>()?.StateMachineType;
        return Unpatched(method) && machine != null
            && Unpatched(machine.GetMethod("MoveNext", InstanceFlags));
    }
    private static bool MapClosure(RunState run, ActMap map) =>
        run.GetType() == typeof(RunState) && map.GetType().Assembly == typeof(ActMap).Assembly
        && typeof(RunState).Module.ModuleVersionId == GameMvid
        && new[] { "Map", "VisitedMapCoords", "CurrentMapCoord", "CurrentMapPoint", "CurrentActIndex" }
            .All(name => Unpatched(Getter(typeof(RunState), name)))
        && new[] { "Grid", "StartingMapPoint", "BossMapPoint", "SecondBossMapPoint" }
            .All(name => SimpleNativeMapGetter(map.GetType(), name))
        && Unpatched(Getter(typeof(MapPoint), "PointType")) && Unpatched(Getter(typeof(MapPoint), "Children"))
        && Unpatched(typeof(ActMap).GetMethod(nameof(ActMap.GetRowCount)))
        && Unpatched(typeof(ActMap).GetMethod(nameof(ActMap.GetColumnCount)))
        && Unpatched(typeof(ActMap).GetMethod(nameof(ActMap.GetPoint), new[] { typeof(int), typeof(int) }))
        && map.GetType().GetMethod(nameof(ActMap.GetPoint), new[] { typeof(MapCoord) }) is { DeclaringType: var owner } pointMethod
        && owner == typeof(ActMap) && Unpatched(pointMethod)
        && IteratorUnpatched(typeof(ActMap).GetMethod(nameof(ActMap.GetAllMapPoints)))
        && IteratorUnpatched(typeof(ActMap).GetMethod(nameof(ActMap.GetPointsInRow)))
        && Unpatched(typeof(MapCoord).GetMethod(nameof(MapCoord.Equals), new[] { typeof(MapCoord) }));

    public static IReadOnlyList<MapPoint> GetDestinations(RunState run) => GetDestinations(
        run.Map, run.VisitedMapCoords, run.CurrentMapPoint,
        current => MapTravel.GetTravelablePointsFrom(run, current));

    public static IReadOnlyList<MapPoint> GetDestinations(ActMap map, IReadOnlyList<MapCoord> visited,
        MapPoint? current, Func<MapPoint, IEnumerable<MapPoint>> travelableFrom)
    {
        if (visited.Count == 0 || current == null) return new[] { map.StartingMapPoint };
        if (map.SecondBossMapPoint != null && current.coord.Equals(map.BossMapPoint.coord))
            return new[] { map.SecondBossMapPoint };
        if (current.coord.row == map.GetRowCount() - 1) return new[] { map.BossMapPoint };
        return travelableFrom(current).ToArray();
    }
    private static bool NeedsTravelHook(ActMap map, IReadOnlyList<MapCoord> visited, MapPoint? current) =>
        visited.Count > 0 && current != null
        && !(map.SecondBossMapPoint != null && current.coord.Equals(map.BossMapPoint.coord))
        && current.coord.row != map.GetRowCount() - 1;
    private static MethodInfo? FreeTravelMethod(Type type) => type.GetMethod(nameof(AbstractModel.ShouldAllowFreeTravel), Type.EmptyTypes);
    private static bool TravelClosure(IReadOnlyList<AbstractModel> listeners) =>
        Unpatched(typeof(MapTravel).GetMethod(nameof(MapTravel.GetTravelablePointsFrom)))
        && Unpatched(typeof(Hook).GetMethod(nameof(Hook.ShouldAllowFreeTravel)))
        && new[] { typeof(AbstractModel), typeof(Flight), typeof(WingedBoots) }.All(type => Unpatched(FreeTravelMethod(type)))
        && Unpatched(Getter(typeof(WingedBoots), "IsUsedUp")) && Unpatched(Getter(typeof(WingedBoots), "TimesUsed"))
        && listeners.All(model => FreeTravelMethod(model.GetType())?.DeclaringType is { } owner
            && (owner == typeof(AbstractModel) || owner == typeof(Flight) || owner == typeof(WingedBoots)));
    private static (int Col, int Row) Key(MapCoord coord) => (coord.col, coord.row);
    private static bool CoordsEqual(IReadOnlyList<MapCoord> left, IReadOnlyList<MapCoord> right) =>
        left.Count == right.Count && left.Select(Key).SequenceEqual(right.Select(Key));
    private sealed record PointFreeze(MapPoint Point, MapCoord Coord, string Type, MapPoint[] Children);
    private static NativeMapReadResult Fail(string status, string gap) =>
        new(status, 0, null, Array.Empty<MapCoord>(), Array.Empty<NativeMapReadNode>(), gap);

    public static NativeMapReadResult Capture(RunState run)
    {
        try
        {
            ActMap map = run.Map;
            if (map == null || map is NullActMap) return Fail("not_applicable", "native_map_not_generated");
            if (!MapClosure(run, map)) return Fail("unsupported", "native_map_read_call_closure_unqualified");
            int act = run.CurrentActIndex;
            var grid = (MapPoint?[,])Getter(map.GetType(), "Grid")!.Invoke(map, null)!;
            MapPoint?[] gridCells = grid.Cast<MapPoint?>().ToArray();
            MapPoint start = map.StartingMapPoint, boss = map.BossMapPoint;
            MapPoint? secondBoss = map.SecondBossMapPoint;
            var inventory = gridCells.Where(point => point != null).Cast<MapPoint>()
                .Concat(new[] { start, boss }).Concat(secondBoss == null ? Array.Empty<MapPoint>() : new[] { secondBoss })
                .Distinct(ReferenceEqualityComparer.Instance).Cast<MapPoint>().ToArray();
            if (inventory.Length == 0 || inventory.Any(point => point == null || point.GetType().Assembly != typeof(MapPoint).Assembly)
                || inventory.GroupBy(point => Key(point.coord)).Any(group => group.Count() != 1))
                return Fail("unknown", "native_map_inventory_ambiguous");
            var members = new HashSet<MapPoint>(inventory, ReferenceEqualityComparer.Instance);
            PointFreeze[] points = inventory.Select(point => new PointFreeze(point, point.coord,
                point.PointType.ToString().ToLowerInvariant(), point.Children.ToArray())).ToArray();
            if (points.Any(point => point.Children.Any(child => !members.Contains(child))))
                return Fail("unknown", "native_map_child_outside_current_inventory");
            MapCoord[] visited = run.VisitedMapCoords.ToArray();
            MapCoord? currentCoord = run.CurrentMapCoord;
            MapPoint? current = run.CurrentMapPoint;
            var byCoord = points.ToDictionary(point => Key(point.Coord));
            MapPoint? resolvedCurrent = currentCoord is { } coordinate && byCoord.TryGetValue(Key(coordinate), out var found) ? found.Point : null;
            if (!ReferenceEquals(current, resolvedCurrent)
                || (visited.Length == 0 ? currentCoord != null : currentCoord == null || Key(visited[^1]) != Key(currentCoord.Value)))
                return Fail("unknown", "native_map_current_coordinate_binding_changed");
            bool hooks = NeedsTravelHook(map, visited, current);
            IReadOnlyList<AbstractModel> listeners = Array.Empty<AbstractModel>();
            if (hooks && (!NativeRunHookReadQualification.TryCaptureListeners(run, out listeners, out _)
                    || !TravelClosure(listeners)))
                return Fail("unsupported", "native_map_travel_hook_unqualified");
            int[] boots = listeners.OfType<WingedBoots>().Select(value => value.TimesUsed).ToArray();
            MapPoint[] destinations = GetDestinations(run).ToArray(); // exactly one native destination capture
            if (destinations.Any(point => !members.Contains(point)))
                return Fail("unknown", "native_map_destination_outside_current_inventory");
            var destinationSet = new HashSet<MapPoint>(destinations, ReferenceEqualityComparer.Instance);
            var visitedSet = new HashSet<(int, int)>(visited.Select(Key));
            NativeMapReadNode[] nodes = points.OrderBy(point => point.Coord.row).ThenBy(point => point.Coord.col)
                .Select(point => new NativeMapReadNode(point.Point, point.Coord, point.Type,
                    destinationSet.Contains(point.Point) ? "travelable" : visited.Length > 0 && visitedSet.Contains(Key(point.Coord))
                        ? "traveled" : "untravelable",
                    point.Children.OrderBy(child => child.coord.row).ThenBy(child => child.coord.col).Select(child => child.coord).ToArray()))
                .ToArray();
            bool stable = ReferenceEquals(run.Map, map) && run.CurrentActIndex == act && MapClosure(run, map)
                && ReferenceEquals(Getter(map.GetType(), "Grid")!.Invoke(map, null), grid)
                && gridCells.SequenceEqual(grid.Cast<MapPoint?>(), ReferenceEqualityComparer.Instance)
                && ReferenceEquals(map.StartingMapPoint, start) && ReferenceEquals(map.BossMapPoint, boss)
                && ReferenceEquals(map.SecondBossMapPoint, secondBoss) && ReferenceEquals(run.CurrentMapPoint, current)
                && CoordsEqual(visited, run.VisitedMapCoords)
                && points.All(point => Key(point.Point.coord) == Key(point.Coord)
                    && point.Point.PointType.ToString().ToLowerInvariant() == point.Type
                    && point.Children.SequenceEqual(point.Point.Children, ReferenceEqualityComparer.Instance))
                && (!hooks || TravelClosure(listeners) && NativeRunHookReadQualification.StillCurrent(run, listeners)
                    && boots.SequenceEqual(listeners.OfType<WingedBoots>().Select(value => value.TimesUsed)));
            return stable ? new("captured", act, currentCoord, visited, nodes, null)
                : Fail("unknown", "native_map_changed_during_read");
        }
        catch { return Fail("unknown", "native_map_read_failed"); }
    }
}
