using System;
using System.Linq;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

internal static class NativeTerminalBounds
{
    internal const int ReasonBytes = 65_536;
    internal const int StageCount = 16;
    internal const int StageFieldBytes = 128;
    private static readonly string MaximumReason = new('\0', ReasonBytes);
    private static readonly string MaximumStageField = new('\0', StageFieldBytes);
    internal static void Preflight(RequestNamespace.Preparation preparation,
        PlayerEnvironmentActionRequest request, NativeLogicalAction action)
    {
        var stages = Enumerable.Range(0, StageCount).Select(_ => new NativeLogicalInputStage(
            MaximumStageField, "partially_delivered", MaximumStageField)).ToArray();
        preparation.Encode(NativeLogicalExecutor.MakeResult(request, action, "partially_delivered",
            MaximumReason, stages, preparation.AdmissionAttribution));
        preparation.Reservation.ResetEncoding();
    }
}
