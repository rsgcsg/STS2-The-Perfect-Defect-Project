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
    MerchantConfirmInput
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
