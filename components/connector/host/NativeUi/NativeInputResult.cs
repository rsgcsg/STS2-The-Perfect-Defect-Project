using System;
using System.Collections.Generic;

namespace STS2Connector.NativeUi;

internal enum NativeInputDelivery
{
    RejectedBeforeInput,
    Delivered,
    PartiallyDelivered,
    Unknown
}

internal enum NativeInputStageKind
{
    ControllerModeInput,
    CardBeginInput,
    TargetFocus,
    TargetConfirmInput,
    PotionPopupInput,
    PotionTargetConfirmInput,
    MerchantFocus,
    MerchantConfirmInput,
    InspectionReturnControl,
    CardInspectionOpen
}

internal enum LegacyNativeInputDisposition
{
    NotDelivered,
    Delivered,
    Unknown
}

/// <summary>Known native input boundary only, never proof of execution, Commit
/// or effects. Contains no native operands or private owner keys.</summary>
internal sealed record NativeInputStage(
    NativeInputStageKind Stage,
    NativeInputDelivery Delivery,
    string Evidence);

internal sealed record NativeInputResult(
    bool Accepted,
    string? ErrorCode,
    string? Detail,
    string? DeliveryEvidence,
    NativeInputDelivery? Delivery = null,
    IReadOnlyList<NativeInputStage>? Stages = null)
{
    // Legacy wires cannot express partial delivery or delivery without confirmed
    // acceptance. Never turn those richer facts into a retryable rejection.
    internal LegacyNativeInputDisposition LegacyDisposition
    {
        get
        {
            bool noStartedStage = Stages == null || System.Linq.Enumerable.All(
                Stages, stage => stage.Delivery == NativeInputDelivery.RejectedBeforeInput);
            if (Delivery == null && (Stages == null || Stages.Count == 0))
                return Accepted ? LegacyNativeInputDisposition.Delivered : LegacyNativeInputDisposition.NotDelivered;
            if (Delivery == NativeInputDelivery.RejectedBeforeInput && !Accepted && noStartedStage)
                return LegacyNativeInputDisposition.NotDelivered;
            if (Delivery == NativeInputDelivery.Delivered && Accepted
                && (Stages == null || System.Linq.Enumerable.All(
                    Stages, stage => stage.Delivery == NativeInputDelivery.Delivered)))
                return LegacyNativeInputDisposition.Delivered;
            return LegacyNativeInputDisposition.Unknown;
        }
    }

    public static NativeInputResult Delivered(string? evidence) =>
        new(true, null, null, evidence);

    public static NativeInputResult Rejected(string code, string detail) =>
        new(false, code, detail, null);

    internal static NativeInputResult DeliveredStages(string evidence, params NativeInputStage[] stages) =>
        new(true, null, null, evidence, NativeInputDelivery.Delivered, Freeze(stages));

    internal static NativeInputResult DeliveredWithoutAcceptance(string code, string detail, params NativeInputStage[] stages) =>
        new(false, code, detail, null, NativeInputDelivery.Delivered, Freeze(stages));

    internal static NativeInputResult PartiallyDelivered(string code, string detail, params NativeInputStage[] stages) =>
        new(false, code, detail, null, NativeInputDelivery.PartiallyDelivered, Freeze(stages));

    internal static NativeInputResult Unknown(string code, string detail, params NativeInputStage[] stages) =>
        new(false, code, detail, null, NativeInputDelivery.Unknown, Freeze(stages));
    private static IReadOnlyList<NativeInputStage> Freeze(NativeInputStage[] stages) =>
        Array.AsReadOnly((NativeInputStage[])stages.Clone());

}
