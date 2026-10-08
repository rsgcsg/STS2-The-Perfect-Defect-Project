using STS2Connector.NativeUi;

namespace STS2Connector.Tests;

public sealed class NativeInputDispositionTests
{
    [Fact]
    public void LegacyProjectionKeepsOnlyRepresentableDeliveryFacts()
    {
        var stage = new NativeInputStage(NativeInputStageKind.TargetFocus,
            NativeInputDelivery.Delivered, "native_focus_input");
        Assert.Equal(LegacyNativeInputDisposition.NotDelivered,
            NativeInputResult.Rejected("guard", "before any input").LegacyDisposition);
        Assert.Equal(LegacyNativeInputDisposition.Delivered,
            NativeInputResult.Delivered("native_input").LegacyDisposition);
        Assert.Equal(LegacyNativeInputDisposition.Delivered,
            NativeInputResult.DeliveredStages("native_input", stage).LegacyDisposition);
        Assert.Equal(LegacyNativeInputDisposition.Unknown,
            NativeInputResult.PartiallyDelivered("guard", "later stage rejected", stage).LegacyDisposition);
        Assert.Equal(LegacyNativeInputDisposition.Unknown,
            NativeInputResult.DeliveredWithoutAcceptance("unconfirmed", "native input sent", stage).LegacyDisposition);
        Assert.Equal(LegacyNativeInputDisposition.Unknown,
            NativeInputResult.Unknown("unknown", "outcome unknown", stage).LegacyDisposition);
    }

    [Fact]
    public void InconsistentInternalStageFactsFailClosedInsteadOfBecomingRejection()
    {
        var stage = new NativeInputStage(NativeInputStageKind.TargetFocus,
            NativeInputDelivery.Delivered, "native_focus_input");
        Assert.Equal(LegacyNativeInputDisposition.Unknown,
            new NativeInputResult(false, "guard", "input already sent", null,
                NativeInputDelivery.RejectedBeforeInput, new[] { stage }).LegacyDisposition);
        Assert.Equal(LegacyNativeInputDisposition.Unknown,
            new NativeInputResult(false, "guard", "delivery omitted", null,
                Stages: new[] { stage }).LegacyDisposition);
        Assert.Equal(LegacyNativeInputDisposition.Unknown,
            new NativeInputResult(true, "guard", "contradictory acceptance", null,
                NativeInputDelivery.RejectedBeforeInput).LegacyDisposition);
    }
}
