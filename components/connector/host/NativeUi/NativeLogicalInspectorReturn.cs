using System;

namespace STS2Connector.NativeUi;

/// <summary>VisibilityChanged is emitted before native Close advances the
/// ActiveScreenContext. Restore only the exact still-owned source control.</summary>
internal static class NativeLogicalInspectorReturn
{
    internal static void Restore(bool inspectorVisible, Func<bool> exactSource, Action enableNativeBack)
    {
        if (!inspectorVisible && exactSource()) enableNativeBack();
    }
}
