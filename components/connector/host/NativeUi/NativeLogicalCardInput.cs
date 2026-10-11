using System;

namespace STS2Connector.NativeUi;

/// <summary>One already-bound logical card input. The native callback owns all
/// selection/preview/completion behavior; this boundary only fences a stale
/// binding and preserves uncertainty if the callback throws.</summary>
internal static class NativeLogicalCardInput
{
    internal static NativeInputResult Dispatch<T>(T exactModel, Func<bool> exactBinding,
        Action<T> nativeCallback, string evidence) where T : class
    {
        if (!exactBinding())
            return NativeInputResult.Rejected("native_logical_grid_changed", "The exact selector/model/request stage changed.");
        try { nativeCallback(exactModel); }
        catch (Exception)
        { return NativeInputResult.Unknown("native_logical_card_input_unknown", "The native callback may have received input before throwing."); }
        return NativeInputResult.Delivered(evidence);
    }
}
