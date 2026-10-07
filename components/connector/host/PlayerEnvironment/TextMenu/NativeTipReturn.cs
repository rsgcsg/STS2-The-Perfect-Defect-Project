using System;
using STS2Connector.NativeUi;

namespace STS2Connector.PlayerEnvironment;

internal enum NativeTipEntry { Focus, Mouse }

/// <summary>Close the exact native tip lifecycle, not merely its rendered node.
/// Only the owning adapter provides the fixed native exit/remove callbacks.</summary>
internal static class NativeTipReturn
{
    internal static NativeInputResult Close(bool signalEntered,
        Func<bool> exactOwner, Func<bool> exactSource,
        Action emitExit, Action remove, Action clearOwner)
    {
        if (!exactOwner())
            return NativeInputResult.Rejected("native_information_owner_changed",
                "Closing tips requires their exact current native owner.");
        if (signalEntered && !exactSource())
            return NativeInputResult.Rejected("native_tip_source_changed",
                "The exact native entry source is no longer current and visible.");

        // Native card focus is latched: removing the tip node alone leaves the
        // source focused, so entering that same source cannot create its tips again.
        // Native exit also owns hand hover tracking/layout and must run first.
        if (signalEntered) emitExit();
        remove(); // Native exit may already remove the same tip; removal is idempotent.
        clearOwner();
        return NativeInputResult.Delivered(signalEntered
            ? "native focus/hover exit; exact tip lifecycle closed"
            : "NHoverTipSet.Remove; exact direct relic tip closed");
    }
}
