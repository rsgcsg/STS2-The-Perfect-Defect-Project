using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.LiveHost;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Platform.NativeFoundation;

namespace STS2Connector.PlayerEnvironment;

internal static partial class PlayerEnvironmentService
{
    internal static SealedObservationCapabilities GetPublicBasisCapabilities() => new(
        PublicBasisContract.CapabilitiesSchema, PublicBasisContract.ReadProfile, PublicBasisContract.Profile,
        PublicBasisContract.CaptureSchema, PublicBasisContract.ChunkSchema, PublicBasisContract.ReleaseSchema,
        PublicBasisContract.Route + "/current", PublicBasisContract.Route + "/read", PublicBasisContract.Route + "/release",
        SealedObservationContract.MaxCapsuleBytes, SealedObservationContract.MaxRetainedBytes,
        SealedObservationContract.MaxCapsules, SealedObservationContract.TtlMs, SealedObservationContract.DefaultChunkBytes,
        SealedObservationContract.MinChunkBytes, SealedObservationContract.MaxChunkBytes, false, PublicBasisContract.Scope);

    internal static SealedObservationCapture ReadCurrentPublicBasis(string? expectedSnapshotId) =>
        SealedObservations.Capture(CapturePublicBasis, expectedSnapshotId);

    private static FrozenPublicObservation CapturePublicBasis()
    {
        using var guard = new P1CaptureGuard();
        NativeSourceInputProvider.BeforePrefix += P1CaptureGuard.InvalidateActive;
        try
        {
            var before = P1NativeCaptureBoundary.Capture();
            SnapshotBuildResult built = BuildSnapshot(inputProfile: PublicBasisContract.Profile);
            var frozen = FreezePublicBasis(built, before.Run == null ? null : Entities.GetId(before.Run, "run"));
            guard.RequireStable(before.Matches(P1NativeCaptureBoundary.Capture()));
            return frozen;
        }
        finally { NativeSourceInputProvider.BeforePrefix -= P1CaptureGuard.InvalidateActive; }
    }

    internal static NativeLogicalService.NativeLogicalSourceCapture CaptureNativeLogicalSource()
    {
        using var guard = new P1CaptureGuard();
        NativeSourceInputProvider.BeforePrefix += P1CaptureGuard.InvalidateActive;
        try
        {
            var before = P1NativeCaptureBoundary.Capture();
            var pair = BuildSourceSnapshot();
            string? continuity = before.Run == null ? null : Entities.GetId(before.Run, "run");
            var captured = AssembleSourceCapture(pair, continuity, () => NativeLogicalCapture.Prepare(
                NativeTextMenuFrameBuilder.Capture(pair.NativePreparation, Entities,
                    binding => StartPlayerEnvironmentInput(pair.NativePreparation, binding.NativeAction, binding.ExactOperands), nativeLogical: true), Entities));
            guard.RequireStable(before.Matches(P1NativeCaptureBoundary.Capture()));
            return captured;
        }
        finally { NativeSourceInputProvider.BeforePrefix -= P1CaptureGuard.InvalidateActive; }
    }

    internal static NativeLogicalService.NativeLogicalSourceCapture AssembleSourceCapture(
        SourceSnapshotBuild pair, string? continuity, Func<TextMenuFrame> captureNative)
    {
        FrozenPublicObservation? basis = null; string? missing = null;
        try { basis = FreezePublicBasis(pair.PublicBasis, continuity, pair.PublicBasis.Snapshot.ObservedAt); }
        catch (SealedObservationException error) { missing = error.Code; }
        TextMenuFrame frame;
        try { frame = captureNative(); }
        catch
        {
            // This is an explicit missing native part. No native UI leaf or positive owner fact is fabricated.
            var page = pair.NativePreparation.Snapshot;
            frame = new(page with { Status = "visible_unsupported", Completeness = page.Completeness with {
                Status = "partial", Missing = page.Completeness.Missing.Append("source_native_frame_capture_failed").Distinct(StringComparer.Ordinal).ToArray() } },
                "source-native-frame-unavailable", Array.Empty<TextMenuLeaf>());
        }
        frame = frame with { GameContinuityId = continuity };
        return new(frame, basis == null ? null : new(basis.SnapshotId, basis.Bytes, basis.CapturedAt), missing);
    }

    internal static IReadOnlyCollection<string> PublicBasisReadKinds(LiveObservation draft) =>
        PlayerVisibilityCatalog.Build(draft, true, ShopSurfaceFacts.TryGetCurrent(out _, out _, out _), aggregateCurrentPublicReads: true)
            .ReadCatalog.Select(read => read.Kind).Distinct(StringComparer.Ordinal).ToArray();

    // Share this assembly with original-source capture. Passing a built Snapshot is essential:
    // this method cannot call BuildSnapshot/Read or obtain another live version.
    internal static FrozenPublicObservation FreezePublicBasis(SnapshotBuildResult built, string? continuity, DateTimeOffset? captureTime = null)
    {
        var reads = new List<PublicBasisRead>();
        foreach (var opportunity in built.Snapshot.Reads)
        {
            var resolution = ResolveReadMaterialization(built.Snapshot, opportunity.ReadId,
                built.Snapshot.SnapshotId, built.ReadBuilds);
            var result = resolution.ErrorCode != null
                ? new PlayerEnvironmentReadResult(null, resolution.ErrorCode, resolution.Detail)
                : BuildResolvedRead(built, resolution, built.Snapshot.SnapshotId, captureTime, includePublicCardDetails: true);
            reads.Add(new(opportunity.ReadId, opportunity.Kind, result.Read == null ? null
                : (JsonObject)JsonSerializer.SerializeToNode(result.Read, ConnectorMod._jsonOptions)!,
                result.Read == null ? result.ErrorCode ?? "read_materialization_missing" : null));
        }
        var snapshot = (JsonObject)JsonSerializer.SerializeToNode(built.Snapshot, ConnectorMod._jsonOptions)!;
        return PublicBasisAssembly.Freeze(snapshot, reads, continuity, ConnectorMod._jsonOptions, captureTime);
    }

    internal static SealedObservationChunk ReadPublicBasis(string captureId, string cursor, int maxBytes) =>
        SealedObservations.Read(captureId, cursor, maxBytes, SealedObservationProfile.PublicBasis);
    internal static SealedObservationRelease ReleasePublicBasis(string captureId) =>
        SealedObservations.Release(captureId, SealedObservationProfile.PublicBasis);
}
