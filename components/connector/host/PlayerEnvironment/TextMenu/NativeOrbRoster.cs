using System;
using System.Collections.Generic;
using System.Linq;

namespace STS2Connector.PlayerEnvironment;

internal enum NativeOrbRosterError { NavigationUnresolved }
internal sealed record NativeOrbRosterResult<T>(IReadOnlyList<T> Nodes, NativeOrbRosterError? Error) where T : class;

/// <summary>Reconstruct only the native current navigation roster. Logical OrbQueue
/// updates can precede UI updates while input is ready; they cannot choose, erase
/// or reorder current presentation controls.</summary>
internal static class NativeOrbRoster
{
    internal static NativeOrbRosterResult<T> Capture<T>(T? anchor, bool exactEmptyAnchor,
        Func<T, bool> exactNativeMember, Func<T, T?> left, Func<T, T?> right,
        int maxNodes = 2048) where T : class
    {
        NativeOrbRosterResult<T> Fail() => new(Array.Empty<T>(), NativeOrbRosterError.NavigationUnresolved);
        if (anchor == null) return exactEmptyAnchor ? new(Array.Empty<T>(), null) : Fail();
        if (maxNodes < 1) return Fail();
        var nodes = new List<T>();
        T? current = anchor;
        while (current != null)
        {
            if (nodes.Count >= maxNodes || !exactNativeMember(current)
                || nodes.Any(value => ReferenceEquals(value, current))) return Fail();
            nodes.Add(current);
            T? next = left(current);
            if (next == null || !exactNativeMember(next) || !ReferenceEquals(right(next), current)) return Fail();
            if (ReferenceEquals(next, anchor)) break;
            current = next;
        }
        return new(nodes, null);
    }
}
