using System.Text;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector.Tests;

public sealed class NativeLogicalSourcePayloadTests
{
    private static NativeLogicalAttachRequest Request() => new("source", NativeLogicalProjector.ScopeFields,
        new[] { new NativeLogicalSeamCoverage("bootstrap", "1", "complete_at_seam") }, "full_reference");
    private static NativeLogicalSourcePublicBasis Basis() => new("ordinary-semantic-snapshot",
        Encoding.UTF8.GetBytes("{\"schema\":\"sts2.player-environment/public-basis-1\",\"snapshot\":{\"snapshot_id\":\"ordinary-semantic-snapshot\"}}"), DateTimeOffset.UnixEpoch);

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void SameOriginalSlotRetainsNeutralWhenNativeMissingAcrossAckAndEpochRollover(bool rollover)
    {
        var store = new NativeLogicalCaptureStore();
        using var hub = new NativeLogicalPublicationHub(store, new[] { new NativeLogicalSeamCoverage("bootstrap", "1", "complete_at_seam") });
        var source = hub.AttachSource(Request(), "bootstrap"); var basis = Basis();
        var payload = store.SealSourcePayload(null, "native_mapping_unsupported", basis, null,
            new("runtime", "environment"), source.Subscription.StreamGeneration, source.Subscription.ScopeId, DateTimeOffset.UnixEpoch);
        if (rollover) hub.ChangeGeneration();
        Assert.True(hub.Complete(source.InitialReservation, new[] { new NativeLogicalProjectionOutcome(source.Subscription.ScopeId, null,
            "native_mapping_unsupported") { SourcePayload = payload } }));
        store.ReleaseSourcePayload(payload.PayloadId);
        var rows = hub.SourceEvents(source.RegistrationId, source.Subscription.SubscriptionId, source.Subscription.StartingCursor);
        var row = Assert.Single(rows.Events).Event;
        Assert.Equal("native_mapping_unsupported", row.MissingReason); Assert.Null(row.CaptureRef);
        Assert.Equal(payload.PayloadId, row.SourcePayloadRef);
        var copy = store.ExportSourcePayload(row.SourcePayloadRef!);
        Assert.Equal(basis.Bytes, copy.CopyPublicBytes()); Assert.Null(copy.Native);
        Assert.Equal(source.Subscription.StreamGeneration, copy.Descriptor.StreamGeneration);
        Assert.DoesNotContain("source_payload_ref", Encoding.UTF8.GetString(NativeLogicalWire.Encode(row)));
        Assert.Throws<NativeLogicalException>(() => store.ExportFrozen(payload.PayloadId));
        Assert.True(store.ChargedBytes > 0);
        hub.AcknowledgeSource(source.RegistrationId, source.Subscription.SubscriptionId, rows.NextCursor);
        Assert.Equal(0, store.ChargedBytes);
        Assert.Throws<NativeLogicalException>(() => store.ExportSourcePayload(payload.PayloadId));
    }

    [Fact]
    public void ForeignSourcePartIsRejectedBeforeSlotCompletionOrPinning()
    {
        var store = new NativeLogicalCaptureStore();
        using var hub = new NativeLogicalPublicationHub(store, new[] { new NativeLogicalSeamCoverage("bootstrap", "1", "complete_at_seam") });
        var source = hub.AttachSource(Request(), "bootstrap");
        var foreign = store.SealSourcePayload(null, "missing", Basis(), null, new("runtime", "environment"),
            source.Subscription.StreamGeneration, "foreign-scope", DateTimeOffset.UnixEpoch);
        Assert.Equal("invalid_projection", Assert.Throws<NativeLogicalException>(() => hub.Complete(source.InitialReservation,
            new[] { new NativeLogicalProjectionOutcome(source.Subscription.ScopeId, null, "missing") { SourcePayload = foreign } })).Code);
        Assert.Empty(hub.SourceEvents(source.RegistrationId, source.Subscription.SubscriptionId, source.Subscription.StartingCursor).Events);
        store.ReleaseSourcePayload(foreign.PayloadId); Assert.Equal(0, store.ChargedBytes);
    }

    [Fact]
    public void ResourceAdmissionOccursBeforeNeutralOwnershipCopyAndOriginalByteMutationCannotChangeIt()
    {
        var store = new NativeLogicalCaptureStore(limits: new(MaxCaptureBytes: 256, MaxRetainedBytes: 256));
        var basis = Basis(); var original = (byte[])basis.Bytes.Clone();
        var retained = store.SealSourcePayload(null, "missing", basis, null, new("runtime", "environment"), "generation", "scope", DateTimeOffset.UnixEpoch);
        basis.Bytes[0] = 0;
        Assert.Equal(original, store.ExportSourcePayload(retained.PayloadId).CopyPublicBytes());
        long charged = store.ChargedBytes;
        Assert.Equal("capacity_exceeded", Assert.Throws<NativeLogicalException>(() => store.SealSourcePayload(null, "missing",
            basis with { Bytes = new byte[257] }, null, new("runtime", "environment"), "generation", "scope", DateTimeOffset.UnixEpoch)).Code);
        Assert.Equal(charged, store.ChargedBytes);
        store.ReleaseSourcePayload(retained.PayloadId); Assert.Equal(0, store.ChargedBytes);
    }
}
