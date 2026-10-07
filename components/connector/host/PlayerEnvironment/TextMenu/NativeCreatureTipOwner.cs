using System;
using System.Collections.Generic;
using System.Linq;

namespace STS2Connector.PlayerEnvironment;

internal enum NativeCreatureTipOwnerScope { Current, Retired, Unresolved }

/// <summary>Read-only projection of the native room's exact current/removing UI
/// rosters. A rendered death-animation node is not a current input owner.</summary>
internal static class NativeCreatureTipOwner
{
    internal static (NativeCreatureTipOwnerScope Scope, T? Owner) Resolve<T>(
        IReadOnlyList<T> exactTreeOwners, IReadOnlyList<T> currentRoomOwners,
        IReadOnlyList<T> removingRoomOwners) where T : class
    {
        if (exactTreeOwners.Count != 1) return (NativeCreatureTipOwnerScope.Unresolved, null);
        T owner = exactTreeOwners[0];
        int current = currentRoomOwners.Count(value => ReferenceEquals(value, owner));
        int removing = removingRoomOwners.Count(value => ReferenceEquals(value, owner));
        if (current == 1 && removing == 0) return (NativeCreatureTipOwnerScope.Current, owner);
        if (current == 0 && removing == 1) return (NativeCreatureTipOwnerScope.Retired, owner);
        // No positive retirement fact, conflicting rosters or duplicate ownership
        // remain unresolved; absence from a public projection is never enough.
        return (NativeCreatureTipOwnerScope.Unresolved, owner);
    }
}
