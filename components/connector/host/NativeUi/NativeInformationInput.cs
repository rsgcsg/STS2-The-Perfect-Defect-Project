using System;
using Godot;

namespace STS2Connector.NativeUi;

/// <summary>A fixed native information input, after exact source revalidation.
/// A callback exception cannot establish that no native side effect occurred.</summary>
internal static class NativeInformationInput
{
    internal static bool Mounted(Node node) => ConnectorMod.IsLiveNode(node)
        && node.IsInsideTree() && node.IsNodeReady();

    internal static NativeInputResult Dispatch(Func<bool> current, Action input, string evidence)
    {
        if (!current())
            return NativeInputResult.Rejected("native_information_source_changed",
                "The exact mounted native information source is no longer available.");
        try { input(); }
        catch (Exception)
        {
            return NativeInputResult.Unknown("native_information_input_unknown",
                "The native information callback may have received input before throwing.");
        }
        return NativeInputResult.Delivered(evidence);
    }
}
