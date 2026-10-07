using System;
using System.Text.Json;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment;

internal static partial class PlayerEnvironmentService
{
    private static readonly SealedObservationStore SealedObservations = new();

    internal static SealedObservationCapabilities GetSealedObservationCapabilities() => new(
        SealedObservationContract.CapabilitiesSchema, SealedObservationContract.ReadProfile,
        TextMenuV2Contract.Profile, SealedObservationContract.Schema,
        SealedObservationContract.ChunkSchema, SealedObservationContract.ReleaseSchema,
        SealedObservationContract.Route + "/current", SealedObservationContract.Route + "/read",
        SealedObservationContract.Route + "/release", SealedObservationContract.MaxCapsuleBytes,
        SealedObservationContract.MaxRetainedBytes, SealedObservationContract.MaxCapsules,
        SealedObservationContract.TtlMs, SealedObservationContract.DefaultChunkBytes,
        SealedObservationContract.MinChunkBytes, SealedObservationContract.MaxChunkBytes,
        false, "sampled_public_text_menu_v2_current_cursor");

    // Transport must run this complete synchronous operation on the game main thread.
    internal static SealedObservationCapture ReadCurrentSealedObservation(string? expectedSnapshotId) =>
        SealedObservations.Capture(() => FreezePublicTextMenuV2(ObserveTextMenuV2Context(), ConnectorMod._jsonOptions),
            expectedSnapshotId);

    internal static FrozenPublicObservation FreezePublicTextMenuV2(
        TextMenuV2ObservationContext context, JsonSerializerOptions options)
    {
        if (context.Snapshot.InputProfile != TextMenuV2Contract.Profile
            || context.Snapshot.Schema != TextMenuV2Contract.SnapshotSchema
            || context.Snapshot.InformationPolicy.IncludesHiddenInformation)
            throw new SealedObservationException("unsupported_input_profile", "Only public text-menu-v2 snapshots may be sealed.");
        DateTimeOffset capturedAt = DateTimeOffset.UtcNow;
        using var stream = new BoundedObservationStream(SealedObservationContract.MaxCapsuleBytes);
        // Snapshot only: no private native frame, no continuity/control/result envelope.
        JsonSerializer.Serialize(stream, context.Snapshot, options);
        return new(stream.ToArray(), context.Snapshot.SnapshotId, context.Snapshot.Session,
            context.GameContinuityId, capturedAt);
    }

    // Retained bytes only; these methods do not enter the game main-thread queue.
    internal static SealedObservationChunk ReadSealedObservation(string captureId, string cursor, int maxBytes) =>
        SealedObservations.Read(captureId, cursor, maxBytes);
    internal static SealedObservationRelease ReleaseSealedObservation(string captureId) =>
        SealedObservations.Release(captureId);
}
