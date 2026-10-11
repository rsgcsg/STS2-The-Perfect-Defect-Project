using System;

namespace STS2Platform.GameMod;

internal enum InspectionDepartureDisposition { ExistingContextPublication, Publish, Missing }

// One exact native callback's synchronous scope, never a remembered owner or
// later-event correlation. The direct native Update is the callback's final
// operation; its return overwrites any earlier nested Update's acknowledgment.
internal sealed class ConnectorNativeLogicalInspectionDeparture : IDisposable
{
    internal const string NativeCallbackName = "<Close>b__23_0";
    internal const string GameAssemblySha256 = "9cb4f1ad8c9f284aa8fec3122ffd6d780bbf543d875c817abdd12ff63fbf12b4";
    internal const string GameModuleVersionId = "57785517-0b16-42b9-8b36-bad6fb28384b";
    [ThreadStatic] private static ConnectorNativeLogicalInspectionDeparture? active;
    private readonly ConnectorNativeLogicalInspectionDeparture? previous;
    private readonly object inspector, context;
    private bool disposed, contextReturned, publicationAccounted;
    private ConnectorNativeLogicalInspectionDeparture(object inspector, object context)
    {
        this.inspector = inspector; this.context = context;
        previous = active; active = this;
    }
    internal static ConnectorNativeLogicalInspectionDeparture? Begin(object inspector,
        object? exactInspector, object? actualCurrent, object context, bool live, bool mounted, bool visible) =>
        live && mounted && visible && ReferenceEquals(inspector, exactInspector) && ReferenceEquals(inspector, actualCurrent)
            ? new(inspector, context) : null;
    internal static void ContextReturned(object context, bool accounted)
    {
        if (active is not { disposed: false } scope || !ReferenceEquals(scope.context, context)) return;
        scope.contextReturned = true; scope.publicationAccounted = accounted;
    }
    internal InspectionDepartureDisposition Returned(object inspector, object? exactInspector,
        bool live, bool visible, bool nativeFailed)
    {
        if (disposed || nativeFailed || !contextReturned) return InspectionDepartureDisposition.Missing;
        // An actual Update source position, including explicit missing, is kept;
        // recapturing it here would duplicate or backfill the original callback.
        if (publicationAccounted) return InspectionDepartureDisposition.ExistingContextPublication;
        return live && !visible && ReferenceEquals(this.inspector, inspector) && ReferenceEquals(inspector, exactInspector)
            ? InspectionDepartureDisposition.Publish : InspectionDepartureDisposition.Missing;
    }
    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        if (ReferenceEquals(active, this)) active = previous;
    }
}
