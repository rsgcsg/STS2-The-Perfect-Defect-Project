using System;
using System.Collections.Generic;
using System.Linq;

namespace STS2Connector.PlayerEnvironment;

internal enum NativeOrbRosterError { NavigationUnresolved, CaptureInconsistent }
internal sealed record NativeOrbRosterResult<T>(IReadOnlyList<T> Nodes, NativeOrbRosterError? Error) where T : class;

/// <summary>Reconstruct the native current navigation roster, not every fading
/// scene child. Logical facts validate the result; they never select its members.</summary>
internal static class NativeOrbRoster
{
    internal static NativeOrbRosterResult<T> Capture<T>(T? anchor, bool exactEmptyAnchor,
        int capacity, IReadOnlyList<string> frozenOrbIds, Func<T, bool> exactNativeMember,
        Func<T, T?> left, Func<T, T?> right, Func<T, string?> modelId, int maxNodes = 2048) where T : class
    {
        NativeOrbRosterResult<T> Fail(NativeOrbRosterError error) => new(Array.Empty<T>(), error);
        if (anchor == null)
            return exactEmptyAnchor && capacity == 0 && frozenOrbIds.Count == 0
                ? new(Array.Empty<T>(), null) : Fail(NativeOrbRosterError.CaptureInconsistent);
        if (capacity < 1 || frozenOrbIds.Count > capacity || maxNodes < 1)
            return Fail(NativeOrbRosterError.CaptureInconsistent);
        var nodes = new List<T>();
        T? current = anchor;
        while (current != null)
        {
            if (nodes.Count >= maxNodes || !exactNativeMember(current)
                || nodes.Any(value => ReferenceEquals(value, current)))
                return Fail(NativeOrbRosterError.NavigationUnresolved);
            nodes.Add(current);
            T? next = left(current);
            if (next == null || !exactNativeMember(next) || !ReferenceEquals(right(next), current))
                return Fail(NativeOrbRosterError.NavigationUnresolved);
            if (ReferenceEquals(next, anchor)) break;
            current = next;
        }
        if (nodes.Count != capacity || frozenOrbIds.Distinct(StringComparer.Ordinal).Count() != frozenOrbIds.Count)
            return Fail(NativeOrbRosterError.CaptureInconsistent);
        for (int index = 0; index < nodes.Count; index++)
        {
            string? nativeModel = modelId(nodes[index]);
            if (index < frozenOrbIds.Count ? nativeModel != frozenOrbIds[index] : nativeModel != null)
                return Fail(NativeOrbRosterError.CaptureInconsistent);
        }
        return new(nodes, null);
    }
}
