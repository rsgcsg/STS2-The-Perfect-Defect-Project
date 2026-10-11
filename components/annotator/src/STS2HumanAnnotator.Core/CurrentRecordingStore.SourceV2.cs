namespace STS2HumanAnnotator.Core;

public sealed partial class RecordingSessionStore
{
    public bool IsSourceSessionV2 => _sourceSessionV2 != null;
    public bool IsOrderedSourceSession => _sourceSessionV2?.Profile.ProfileId == SourceSessionContractV3.ProfileId;
    public SourceCaptureProfileV2? SourceProfileV2 => _sourceSessionV2?.Profile;
    public SourceSessionStatusV2? GetSourceStatusV2() => _sourceSessionV2?.Status;
    public static RecordingSessionStore CreateSourceV2(string root, CurrentRecordingManifest manifest,
        SourceCaptureProfileV2 profile, SourceDeclaration initialSource, SourceEpochPacketV2 initialEpoch)
        => CreateSourceVersion(root, manifest, profile, initialSource, initialEpoch, 2);
    public static RecordingSessionStore CreateSourceV3(string root, CurrentRecordingManifest manifest,
        SourceCaptureProfileV2 profile, SourceDeclaration initialSource, SourceEpochPacketV2 initialEpoch)
        => CreateSourceVersion(root, manifest, profile, initialSource, initialEpoch, 3);
    private static RecordingSessionStore CreateSourceVersion(string root, CurrentRecordingManifest manifest,
        SourceCaptureProfileV2 profile, SourceDeclaration initialSource, SourceEpochPacketV2 initialEpoch, int version)
    {
        var format = SourceSessionWireFormat.ForVersion(version);
        SourceSessionStreamsV2.ValidateProfile(profile); SourceSessionContract.Validate(initialSource);
        if (manifest.Schema != format.Schema("source-session-manifest") || manifest.SchemaVersion != version
            || manifest.SourceSchemaVersion != version || profile.ProfileId != format.ProfileId || manifest.SourceEnvironment == null
            || manifest.CaptureProfileId != profile.ProfileId || manifest.CaptureProfileSha256 != SourceSessionContractV2.ProfileDigest(profile)
            || manifest.DecisionSchemaVersion != null || manifest.TextInputSchemaVersion != null || manifest.CloseSchemaVersion != null
            || manifest.DispositionSchemaVersion != null || manifest.ContinuousSchemaVersion != null || manifest.SupportedFamilies.Count != 0
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
    public void SealSourceInputOrderV3(SourceInputTokenV2 token, SourceBasisOrderV3 order) => SourceV2.Ledger.SetInputOrder(token, order);
    public bool IsSourceObservationPausedV2(SourceNativePositionV2 position) => SourceV2.Ledger.IsPaused(position);
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
    // Failed source accounting cannot produce Close; the worker still releases all native/disk resources explicitly.
    public void AbortSourceV2(string code)
    {
        lock (_gate)
        {
            if (_disposed) return;
            _disposed = true;
            var source = SourceV2; source.Ledger.MarkFailure(code);
            try { source.WriteFailure(); source.WriteCoverage(); }
            finally
            {
                try
                {
                    RecordingResourceCleanup.DisposeAll(EvidenceStreams().Concat(
                        new IDisposable?[] { source, _ownerLease }));
                }
                finally
                {
                    _closed = false; _appendHealth = "failed"; _decisions = _decisions with { AccountingComplete = false };
                }
            }
        }
    }
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
