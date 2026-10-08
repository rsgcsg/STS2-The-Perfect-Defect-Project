namespace STS2HumanAnnotator.Core;

public sealed partial class RecordingSessionStore
{
    public bool IsSourceSessionV2 => _sourceSessionV2 != null;
    public SourceCaptureProfileV2? SourceProfileV2 => _sourceSessionV2?.Profile;
    public SourceSessionStatusV2? GetSourceStatusV2() => _sourceSessionV2?.Status;
    public static RecordingSessionStore CreateSourceV2(string root, CurrentRecordingManifest manifest,
        SourceCaptureProfileV2 profile, SourceDeclaration initialSource, SourceEpochPacketV2 initialEpoch)
    {
        SourceSessionStreamsV2.ValidateProfile(profile); SourceSessionContract.Validate(initialSource);
        if (manifest.Schema != SourceSessionContractV2.ManifestSchema || manifest.SchemaVersion != 2
            || manifest.SourceSchemaVersion != 2 || manifest.SourceEnvironment == null
            || manifest.CaptureProfileId != profile.ProfileId || manifest.CaptureProfileSha256 != SourceSessionContractV2.ProfileDigest(profile)
            || manifest.DecisionSchemaVersion != null || manifest.TextInputSchemaVersion != null || manifest.CloseSchemaVersion != null
            || !manifest.NonClaims.SequenceEqual(SourceSessionContractV2.NonClaims))
            throw new InvalidDataException("source_manifest_invalid");
        SourceSessionContract.ValidateEnvironment(manifest.SourceEnvironment);
        SourceSessionContract.Identifier(manifest.SessionId); SourceSessionContract.Identifier(manifest.TimelineId);
        return new(Path.Combine(Path.GetFullPath(root), manifest.SessionId), SourceSessionJson.Copy(manifest), null,
            initialSource: initialSource, sourceProfileV2: profile, initialEpoch: initialEpoch);
    }
    private SourceSessionStreamsV2 SourceV2 => _sourceSessionV2 ?? throw new InvalidOperationException("source_v2_profile_required");

    // Native-thread admission only. These methods never acquire the disk gate or touch the filesystem.
    public SourceEpochAdmissionV2 AdmitSourceEpochV2(SourceEpochPacketV2 packet) => SourceV2.Ledger.StageEpoch(packet);
    public SourceSegmentV2 AdmitSourceDeclarationV2(SourceDeclaration declaration, string expectedSegmentId,
        SourceNativePositionV2 position) => SourceV2.Ledger.StageSource(declaration, position, expectedSegmentId);
    public SourceBoundaryV2 AdmitSourceBoundaryV2(string kind, SourceNativePositionV2 position,
        IReadOnlyList<SourceNativeSealV2>? seals = null, SourceNativeTransitionV2? transition = null) =>
        SourceV2.Ledger.StageBoundary(kind, position, seals, transition);
    public SourceInputTokenV2 ReserveSourceInputV2(string inputId, SourceNativePositionV2 prePosition) =>
        SourceV2.Ledger.ReserveInput(inputId, prePosition);
    public void MarkSourceV2AccountingFailed(string code) => SourceV2.Ledger.MarkFailure(code);

    // Only the serial recorder worker uses these writes. It cannot hold the metadata gate while doing disk I/O.
    public void AppendSourceEpochV2(SourceEpochAdmissionV2 admission) => SourceWriteV2(source => source.AppendEpoch(admission));
    public void AppendSourceDeclarationV2(SourceSegmentV2 declaration) => SourceWriteV2(source => source.AppendSegment(declaration));
    public void AppendSourceBoundaryV2(SourceBoundaryV2 boundary) => SourceWriteV2(source => source.AppendBoundary(boundary));
    public void AppendPublicObservationV2(SourceObservationPacketV2 packet) => SourceWriteV2(source => source.Observe(packet));
    public void BindSourceInputBasisV2(SourceInputTokenV2 token, FrozenPublicCaptureV2? capture,
        FrozenPublicCatalogV2? catalog) => SourceWriteV2(source => source.BindInput(token, capture, catalog));
    public void CompleteSourceInputV2(SourceInputTokenV2 token, SourceInputOutcomeV2 outcome) =>
        SourceWriteV2(source => source.CompleteInput(token, outcome));
    public void PrepareSourceCloseV2(IReadOnlyList<SourceNativeSealV2> completedOriginalSeals) =>
        SourceWriteV2(source => source.PrepareClose(completedOriginalSeals));
    private void SourceWriteV2(Action<SourceSessionStreamsV2> write)
    {
        lock (_gate)
        {
            EnsureOpen(); var source = SourceV2;
            try { write(source); }
            catch (Exception exception)
            {
                source.Ledger.MarkFailure(exception is InvalidDataException ? exception.Message : "source_disk_or_append_failed");
                MarkWriteFailureUnsafe(exception);
                try { source.WriteFailure(); } catch (Exception e) when (e is IOException or UnauthorizedAccessException) { }
                throw;
            }
        }
    }
}
