using System;

namespace STS2Connector.NativeUi;

/// <summary>Projection/delivery of one exact native selector control. Requested
/// counts describe the selection task; they do not override its current button.</summary>
internal static class NativeSelectorControl
{
    internal static bool Available<T>(T? control, Func<T, bool> visibleEnabled) where T : class =>
        control != null && visibleEnabled(control);

    internal static NativeInputResult Click<T>(T? control, Func<bool> exactOwner,
        Func<T, bool> visibleEnabled, Action<T> nativeClick, string evidence) where T : class
    {
        if (!exactOwner() || !Available(control, visibleEnabled))
            return NativeInputResult.Rejected("player_environment_target_not_actionable",
                "The exact current selector control is no longer visible and enabled.");
        nativeClick(control!);
        return NativeInputResult.Delivered(evidence);
    }
}
