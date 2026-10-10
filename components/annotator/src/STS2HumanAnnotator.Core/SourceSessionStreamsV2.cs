using System.Text.Json;

namespace STS2HumanAnnotator.Core;

/// <summary>V2 streams under the existing store lifecycle. Metadata admission and disk gates are deliberately disjoint.</summary>
internal sealed class SourceSessionStreamsV2 : IDisposable
{
    private readonly string directory;
    private readonly CurrentRecordingManifest manifest;
    private readonly SourceCaptureProfileV2 profile;
    private readonly SourceSessionWireFormat format;
    internal readonly SourceSessionEpochLedgerV2 Ledger;
    private readonly Dictionary<string, FileStream> streams = new(StringComparer.Ordinal);
    private readonly Dictionary<string, long> counts = new(StringComparer.Ordinal);
    private readonly Dictionary<string, PublicCaptureReferenceV2> captures = new(StringComparer.Ordinal);
    private readonly Dictionary<string, PublicCatalogReferenceV2> catalogs = new(StringComparer.Ordinal);
    private readonly Dictionary<string, string> observations = new(StringComparer.Ordinal);
    private readonly HashSet<string> payloadPaths = new(StringComparer.Ordinal);
    private readonly HashSet<string> sourceKinds = new(StringComparer.Ordinal);
    private SourceBoundaryV2? closeBoundary;
    private IReadOnlyList<SourceFinalDrainV2>? finalDrains;
    private long payloadBytes;
    private bool disposed;
    private bool streamsSealed;

    internal SourceSessionStreamsV2(string directory, CurrentRecordingManifest manifest, SourceCaptureProfileV2 profile,
        SourceDeclaration initialSource, SourceEpochPacketV2 initialEpoch)
    {
        ValidateProfile(profile); this.directory = directory; this.manifest = manifest;
        this.profile = SourceSessionJson.Copy(profile); format = SourceSessionWireFormat.ForProfile(profile);
        Ledger = new(manifest.SessionId, manifest.TimelineId, manifest.SourceEnvironment!, profile.Limits, format);
        try
        {
            foreach (string file in SourceSessionContractV2.StreamFiles)
            {
                streams.Add(file, new FileStream(Path.Combine(directory, file), FileMode.CreateNew, FileAccess.Write, FileShare.Read, 65536));
                counts.Add(file, 0);
            }
            AppendEpoch(Ledger.StageEpoch(initialEpoch));
            AppendSegment(Ledger.StageSource(initialSource, initialEpoch.StartingPosition));
        }
        catch { Dispose(); throw; }
    }
    internal static void ValidateProfile(SourceCaptureProfileV2 profile)
    {
        var l = profile.Limits; var format = SourceSessionWireFormat.ForProfile(profile);
        if (profile.Schema != format.Schema("source-capture-profile") || profile.ProfileId != format.ProfileId
            || profile.InputProfile != "native-logical-v1" || profile.PublicationProfileId != SourceSessionContractV2.PublicationProfileId
            || profile.PublicationProfileDefinitionSha256 != SourceSessionContractV2.PublicationProfileDefinitionSha256
            || !profile.EagerScope.SequenceEqual(new[] { "persistent", "interaction", "referents", "catalog" })
            || !profile.NonClaims.SequenceEqual(SourceSessionContractV2.NonClaims)
            || l.MaxCaptureBytes is < 1 or > 64 * 1024 * 1024 || l.MaxPayloadBytes < l.MaxCaptureBytes || l.MaxPayloadBytes > 512L * 1024 * 1024
            || l.MaxRowBytes is < 512 or > 1024 * 1024 || l.MaxRowsPerStream is < 1 or > 65536
            || l.MaxBytesPerStream < l.MaxRowBytes || l.MaxBytesPerStream > 64L * 1024 * 1024
            || l.MaxSegments is < 1 or > 256 || l.MaxEpochs is < 1 or > 256 || l.MaxRetiringEpochs is < 1 or > 2
            || l.MaxMetadataPackets is < 1 or > 32 || l.MaxCopiedPayloadBytes is < 1 or > 128L * 1024 * 1024
            || l.MaxPendingInputs is < 1 or > 128 || l.EncodingDeadlineMs != 2000 || l.CloseBarrierGraceMs is < 1 or > 3000)
            throw new InvalidDataException("source_profile_invalid");
    }
    internal SourceCaptureProfileV2 Profile => SourceSessionJson.Copy(profile);
    internal SourceSessionStatusV2 Status => Ledger.Status;
    internal void AppendEpoch(SourceEpochAdmissionV2 admission)
    {
        if (Ledger.EpochRow(admission.Epoch.EpochId, admission.Epoch.Context.StreamGeneration) != admission.Epoch)
            throw new InvalidDataException("source_epoch_not_owned");
        Append("source-attachment-epochs.jsonl", admission.Epoch);
        if (admission.Boundary != null) AppendBoundary(admission.Boundary);
    }
    internal void AppendSegment(SourceSegmentV2 segment)
    { Ledger.AssertIssuedSegment(segment); Append("source-segments.jsonl", segment); sourceKinds.Add(segment.Declaration.SourceKind); }
    internal void AppendBoundary(SourceBoundaryV2 boundary)
    {
        Ledger.AssertIssuedBoundary(boundary);
        Append("source-boundaries.jsonl", boundary);
        if (boundary.Kind == "close") closeBoundary = boundary;
    }
    internal PublicCaptureReferenceV2 Persist(FrozenPublicCaptureV2 value)
    {
        var capture = value.Capture; Identity(value.EpochId, capture.CaptureId, capture.SnapshotId, capture.ScopeId, capture.StreamGeneration);
        CheckSize(capture.Bytes);
        // The producer reserves the copy budget before this ownership copy and retains that charge until this work item leaves.
        capture = capture with { Bytes = (byte[])capture.Bytes.Clone() };
        CheckBytes(capture.Bytes, capture.Sha256);
        using var body = JsonDocument.Parse(capture.Bytes); var root = body.RootElement;
        if (root.ValueKind != JsonValueKind.Object || root.GetProperty("schema").GetString() != "sts2.player-environment/native-logical-observation-1"
            || root.GetProperty("input_profile").GetString() != "native-logical-v1" || root.GetProperty("snapshot_id").GetString() != capture.SnapshotId
            || root.GetProperty("protocol_version").GetString() != manifest.SourceEnvironment!.PlayerEnvironmentProtocol
            || root.GetProperty("session").GetProperty("runtime_instance_id").GetString() != manifest.SourceEnvironment.RuntimeInstanceId
            || root.GetProperty("session").GetProperty("environment_fingerprint").GetString() != manifest.SourceEnvironment.EnvironmentFingerprint
            || root.GetProperty("information_policy").GetProperty("includes_hidden_information").GetBoolean())
            throw new InvalidDataException("source_capture_identity_invalid");
        SourceCaptureCodec.Validate(root, capture.SnapshotId, capture.ScopeId, capture.StreamGeneration);
        string relative = BlobPath("public-captures", capture.Sha256);
        var reference = new PublicCaptureReferenceV2(value.EpochId, capture.CaptureId, capture.SnapshotId, capture.ScopeId,
            capture.StreamGeneration, capture.CapturedAt, capture.Bytes.LongLength, capture.Sha256, relative);
        string key = value.EpochId + ":" + capture.CaptureId;
        if (captures.TryGetValue(key, out var previous))
        {
            if (previous != reference) throw new InvalidDataException("source_capture_id_conflict");
            VerifyBlob(relative, reference.Sha256, reference.ByteCount); return previous;
        }
        if (captures.Count >= profile.Limits.MaxRowsPerStream) Fail("source_capture_count_capacity");
        PersistBytes(relative, capture.Bytes); captures.Add(key, reference); return reference;
    }
    internal PublicCatalogReferenceV2 Persist(FrozenPublicCatalogV2 value)
    {
        var catalog = value.Catalog; Identity(value.EpochId, catalog.CatalogRef, catalog.SnapshotId, catalog.ScopeId, catalog.StreamGeneration);
        CheckSize(catalog.Bytes); catalog = catalog with { Bytes = (byte[])catalog.Bytes.Clone() };
        CheckBytes(catalog.Bytes, catalog.PayloadSha256);
        var actions = SourceCatalogCodec.Decode(catalog.Bytes);
        if (actions.Count != catalog.TotalCount || actions.Count > 65536 || SourceCatalogCodec.Digest(actions) != catalog.StructuralDigest)
            throw new InvalidDataException("source_catalog_digest_mismatch");
        string relative = BlobPath("public-catalogs", catalog.PayloadSha256);
        var reference = new PublicCatalogReferenceV2(value.EpochId, catalog.CatalogRef, catalog.SnapshotId, catalog.ScopeId,
            catalog.StreamGeneration, catalog.TotalCount, catalog.StructuralDigest, catalog.Bytes.LongLength, catalog.PayloadSha256, relative);
        string key = value.EpochId + ":" + catalog.CatalogRef;
        if (catalogs.TryGetValue(key, out var previous))
        {
            if (previous != reference) throw new InvalidDataException("source_catalog_id_conflict");
            VerifyBlob(relative, reference.PayloadSha256, reference.ByteCount); return previous;
        }
        if (catalogs.Count >= profile.Limits.MaxRowsPerStream) Fail("source_catalog_count_capacity");
        PersistBytes(relative, catalog.Bytes); catalogs.Add(key, reference); return reference;
    }
    internal void Observe(SourceObservationPacketV2 packet)
    {
        Ledger.ValidateOriginalRange(packet.Position);
        if (packet.GapAfterIndex != null && packet.Capture == null && packet.Catalog == null)
        {
            if (packet.SourceSeam != "source_gap" || packet.SourceIndex != "0" || packet.Phase != "retention_overflow"
                || packet.Completeness != "failed" || packet.MissingReason != "retention_overflow")
                throw new InvalidDataException("source_diagnostic_gap_invalid");
            ulong after = SourceSessionContract.Index(new(packet.Position.StreamGeneration, packet.GapAfterIndex));
            foreach (var piece in Ledger.UnpausedGapPieces(packet.Position, after))
            {
                string through = piece.Through.ToString(System.Globalization.CultureInfo.InvariantCulture);
                ObserveCore(packet with { Position = packet.Position with { PublicationIndex = through },
                    GapAfterIndex = piece.After.ToString(System.Globalization.CultureInfo.InvariantCulture) });
            }
            Ledger.RecordDurable(packet.Position, packet.GapAfterIndex, observation: false);
            return;
        }
        ObserveCore(packet);
    }
    private void ObserveCore(SourceObservationPacketV2 packet)
    {
        Healthy(); var epoch = Ledger.EpochRow(packet.Position.EpochId, packet.Position.StreamGeneration);
        ulong index = SourceSessionContractV2.Index(packet.Position);
        string segment = Ledger.SegmentFor(packet.Position);
        if (Ledger.IsPaused(packet.Position))
        { Ledger.RecordDurable(packet.Position, packet.GapAfterIndex, observation: false); return; }
        SourceSessionContract.Identifier(packet.SourceSeam); SourceSessionContract.Identifier(packet.Phase);
        _ = SourceSessionContract.Index(new(packet.Position.StreamGeneration, packet.SourceIndex));
        if (packet.Completeness is not ("complete" or "partial" or "capacity_exceeded" or "failed"))
            throw new InvalidDataException("source_observation_completeness_invalid");
        if (packet.Capture != null && packet.Capture.EpochId != packet.Position.EpochId
            || packet.Catalog != null && packet.Catalog.EpochId != packet.Position.EpochId
            || packet.GameContinuityId != epoch.Context.GameContinuityId)
            throw new InvalidDataException("source_observation_epoch_mismatch");
        var capture = packet.Capture == null ? null : Persist(packet.Capture);
        var catalog = packet.Catalog == null ? null : Persist(packet.Catalog);
        Join(capture, catalog, packet.Completeness == "complete");
        if (packet.OwnerOccurrence != null)
        {
            SourceSessionContract.Identifier(packet.OwnerOccurrence);
            if (capture != null)
            {
                using var body = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(directory, capture.PayloadRef)));
                if (body.RootElement.GetProperty("owner_occurrence").GetProperty("occurrence_id").GetString() != packet.OwnerOccurrence)
                    throw new InvalidDataException("source_observation_owner_occurrence_mismatch");
            }
        }
        if (packet.Completeness == "complete" && (capture == null || catalog == null || packet.MissingReason != null)
            || packet.Completeness != "complete" && string.IsNullOrWhiteSpace(packet.MissingReason)
            || capture != null && capture.SnapshotId != packet.SnapshotId)
            throw new InvalidDataException("source_full_reference_incomplete");
        if (packet.GapAfterIndex != null && SourceSessionContract.Index(new(packet.Position.StreamGeneration, packet.GapAfterIndex)) >= index)
            throw new InvalidDataException("source_gap_interval_invalid");
        var row = new SourcePublicObservationV2(format.Schema("public-observation"), counts["public-observations.jsonl"] + 1,
            manifest.SessionId, manifest.TimelineId, packet.Position.EpochId, segment, packet.Position,
            packet.SourceSeam, packet.SourceIndex, packet.Phase, packet.SnapshotId, packet.OwnerOccurrence,
            packet.GameContinuityId, packet.Completeness, capture, catalog, packet.MissingReason, packet.GapAfterIndex);
        string key = packet.Position.EpochId + ":" + packet.Position.PublicationIndex;
        string signature = SourceSessionContract.Sha256(SourceSessionJson.Bytes(row with { Sequence = 0 }));
        if (observations.TryGetValue(key, out string? previous))
        { if (previous != signature) throw new InvalidDataException("source_observation_identity_conflict"); return; }
        Append("public-observations.jsonl", row); observations.Add(key, signature);
        Ledger.RecordDurable(packet.Position, packet.GapAfterIndex, missing: packet.MissingReason != null);
    }
    internal void BindInput(SourceInputTokenV2 token, FrozenPublicCaptureV2? capture, FrozenPublicCatalogV2? catalog)
    {
        _ = Ledger.ExactInput(token);
        if (capture != null && capture.EpochId != token.PrePosition.EpochId || catalog != null && catalog.EpochId != token.PrePosition.EpochId)
            throw new InvalidDataException("source_input_epoch_mismatch");
        var pre = capture == null ? null : Persist(capture); var relation = catalog == null ? null : Persist(catalog);
        Join(pre, relation); Ledger.BindInput(token, pre, relation);
    }
    internal void CompleteInput(SourceInputTokenV2 token, SourceInputOutcomeV2 outcome)
    {
        Healthy(); var input = Ledger.ExactInput(token); outcome = SourceSessionJson.Copy(outcome);
        ValidateOutcome(input, outcome); string signature = SourceSessionContract.Sha256(SourceSessionJson.Bytes(outcome));
        if (input.TerminalSignature != null)
        { if (input.TerminalSignature != signature) throw new InvalidDataException("source_input_completion_conflict"); return; }
        var row = new SourceNativeInputWitnessV2(format.Schema("native-input-witness"), counts["native-input-witnesses.jsonl"] + 1,
            manifest.SessionId, manifest.TimelineId, token.InputId, token.PrePosition.EpochId, token.SegmentId, token.PrePosition,
            input.Capture, input.Catalog, outcome, DateTimeOffset.UtcNow)
        { InputPrefixOrdinal = token.InputPrefixOrdinal, BasisOrder = format.Ordered ? input.BasisOrder ??
            new(SourceSessionContractV3.UnprovenBasis, SourceSessionContractV3.OrderUnprovenReason) : null };
        Append("native-input-witnesses.jsonl", row); Ledger.CompleteInput(token, signature);
    }
    private void ValidateOutcome(SourceSessionEpochLedgerV2.Input input, SourceInputOutcomeV2 outcome)
    {
        if (outcome.MappingStatus is not ("exact" or "unmapped" or "ambiguous" or "capture_missing")
            || outcome.Delivery is not ("rejected_before_input" or "delivered" or "partially_delivered" or "unknown")
            || outcome.MatchCount is < 0 or > 65536 || outcome.Stages.Count > 16)
            throw new InvalidDataException("source_input_outcome_invalid");
        SourceSessionContractV2.Text(outcome.NativeMechanism);
        if (outcome.ReasonCode != null) SourceSessionContractV2.Text(outcome.ReasonCode);
        foreach (var stage in outcome.Stages)
        {
            SourceSessionContractV2.Text(stage.Stage); SourceSessionContractV2.Text(stage.Delivery); SourceSessionContractV2.Text(stage.Evidence);
            if (stage.Delivery is not ("rejected_before_input" or "delivered" or "partially_delivered" or "unknown"))
                throw new InvalidDataException("source_input_stage_delivery_invalid");
        }
        if (outcome.MappingStatus == "exact")
        {
            if (outcome.MatchCount != 1 || outcome.SelectedAction == null || input.Capture == null || input.Catalog == null)
                throw new InvalidDataException("source_exact_input_basis_missing");
            Join(input.Capture, input.Catalog, requireFull: true);
            var original = SourceCatalogCodec.Decode(File.ReadAllBytes(Path.Combine(directory, input.Catalog.PayloadRef)));
            var matches = original.Where(x => x.ActionId == outcome.SelectedAction.ActionId).ToArray();
            if (matches.Length != 1 || SourceCatalogCodec.Digest(matches) != SourceCatalogCodec.Digest(new[] { outcome.SelectedAction }))
                throw new InvalidDataException("source_selected_action_not_in_original_catalog");
        }
        else if (outcome.SelectedAction != null || outcome.MappingStatus == "ambiguous" && outcome.MatchCount < 2
            || outcome.MappingStatus is "unmapped" or "capture_missing" && outcome.MatchCount != 0)
            throw new InvalidDataException("source_input_mapping_invalid");
        if (format.Ordered && input.BasisOrder?.Status != SourceSessionContractV3.FrozenBasis && outcome.MappingStatus != "capture_missing")
            throw new InvalidDataException("source_unproven_input_mapping_invalid");
        if (input.Capture == null && outcome.MappingStatus != "capture_missing")
            throw new InvalidDataException("source_missing_capture_mapping_invalid");
    }
    internal void PrepareClose(IReadOnlyList<SourceNativeSealV2> completed)
    {
        Healthy(); if (closeBoundary == null) throw new InvalidOperationException("source_close_boundary_required");
        foreach (var token in Ledger.PendingInputs)
        {
            var input = Ledger.ExactInput(token);
            CompleteInput(token, new(input.Capture == null ? "capture_missing" : "unmapped", 0, null,
                "source_session_close", "unknown", "session_closed_before_input_completion", Array.Empty<SourceInputStageV2>()));
        }
        finalDrains = Ledger.FinalDrains(completed);
        foreach (var stream in streams.Values) stream.Flush(true);
    }
    internal void SealStreams()
    {
        RequirePreparedClose();
        Dispose();
        streamsSealed = true;
    }
    internal void WriteCloseReceipt()
    {
        if (!streamsSealed) throw new InvalidOperationException("source_streams_not_sealed");
        if (!Status.AccountingComplete) throw new InvalidOperationException(Status.Error ?? "source_accounting_failed");
        if (closeBoundary == null || finalDrains == null) throw new InvalidOperationException("source_final_drains_required");
        var hashes = streams.Keys.ToDictionary(file => file, file => SourceSessionContract.Sha256(File.ReadAllBytes(Path.Combine(directory, file))), StringComparer.Ordinal);
        var status = Status;
        var receipt = new
        {
            schema = format.Schema("source-session-close"), session_id = manifest.SessionId, timeline_id = manifest.TimelineId,
            status = "closed", accounting_complete = true, final_position = closeBoundary.Position,
            sealed_epochs = closeBoundary.SealedEpochs, final_drains = finalDrains, counts, stream_sha256 = hashes,
            gap_count = status.Gaps, input_count = status.Inputs, epoch_count = status.Epochs,
            source_kinds = sourceKinds.Order(StringComparer.Ordinal).ToArray()
        };
        string path = Path.Combine(directory, "source-close-receipt.json"), temporary = path + ".tmp";
        var receiptObject = System.Text.Json.Nodes.JsonNode.Parse(SourceSessionJson.Bytes(receipt))!.AsObject();
        if (format.Ordered) receiptObject["final_input_prefix_ordinal"] = Ledger.FinalInputPrefixOrdinal;
        WriteNew(temporary, SourceSessionJson.Bytes(receiptObject)); File.Move(temporary, path);
    }
    internal void RequirePreparedClose()
    {
        Healthy();
        if (finalDrains == null) throw new InvalidOperationException("source_final_drains_required");
    }
    internal void WriteCoverage() => File.WriteAllBytes(Path.Combine(directory, "source-coverage.json"), SourceSessionJson.Bytes(new
    { schema = format.Schema("source-coverage"), status = Status, non_claims = SourceSessionContractV2.NonClaims }));
    internal void WriteFailure()
    {
        if (Status.AccountingComplete) return;
        string path = Path.Combine(directory, "source-accounting-failure.json");
        if (!File.Exists(path)) WriteNew(path, SourceSessionJson.Bytes(new
        { schema = format.Schema("source-accounting-failure"), code = Status.Error, accounting_complete = false, counts }));
    }
    private void Join(PublicCaptureReferenceV2? capture, PublicCatalogReferenceV2? catalog, bool requireFull = false)
    {
        if (capture != null && (!captures.TryGetValue(capture.EpochId + ":" + capture.CaptureId, out var actual) || actual != capture))
            throw new InvalidDataException("source_capture_not_owned");
        if (catalog != null && (!catalogs.TryGetValue(catalog.EpochId + ":" + catalog.CatalogRef, out var relation) || relation != catalog))
            throw new InvalidDataException("source_catalog_not_owned");
        IReadOnlyList<SourcePublicAction>? actions = null;
        if (catalog != null)
        { VerifyBlob(catalog.PayloadRef, catalog.PayloadSha256, catalog.ByteCount); actions = SourceCatalogCodec.Decode(File.ReadAllBytes(Path.Combine(directory, catalog.PayloadRef))); }
        if (capture != null)
        {
            VerifyBlob(capture.PayloadRef, capture.Sha256, capture.ByteCount);
            using var body = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(directory, capture.PayloadRef)));
            SourceCaptureCodec.Validate(body.RootElement, capture.SnapshotId, capture.ScopeId, capture.StreamGeneration, requireFull, actions);
        }
        if (catalog == null) return;
        if (capture == null || capture.EpochId != catalog.EpochId || capture.SnapshotId != catalog.SnapshotId
            || capture.ScopeId != catalog.ScopeId || capture.StreamGeneration != catalog.StreamGeneration)
            throw new InvalidDataException("source_capture_catalog_join_invalid");
        using var json = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(directory, capture.PayloadRef)));
        var descriptor = json.RootElement.GetProperty("catalog");
        if (descriptor.GetProperty("catalog_ref").GetString() != catalog.CatalogRef || descriptor.GetProperty("snapshot_id").GetString() != catalog.SnapshotId
            || descriptor.GetProperty("scope_id").GetString() != catalog.ScopeId || descriptor.GetProperty("stream_generation").GetString() != catalog.StreamGeneration
            || descriptor.GetProperty("status").GetString() != "complete" || descriptor.GetProperty("total_count").GetInt32() != catalog.TotalCount
            || descriptor.GetProperty("digest").GetString() != catalog.StructuralDigest)
            throw new InvalidDataException("source_capture_catalog_descriptor_mismatch");
    }
    private void Identity(string epochId, string id, string snapshot, string scope, string generation)
    {
        SourceSessionContract.Identifier(id); SourceSessionContract.Identifier(snapshot);
        if (Ledger.EpochRow(epochId, generation).Context.ScopeId != scope)
            throw new InvalidDataException("source_payload_scope_or_generation_mismatch");
    }
    private static string BlobPath(string family, string hash) => family + "/sha256/" + hash[..2] + "/" + hash + ".bin";
    private void CheckBytes(byte[] bytes, string hash)
    {
        CheckSize(bytes);
        if (!SourceSessionContract.IsSha256(hash) || SourceSessionContract.Sha256(bytes) != hash)
            throw new InvalidDataException("source_payload_hash_mismatch");
    }
    private void CheckSize(byte[] bytes)
    { if (bytes.Length == 0 || bytes.Length > profile.Limits.MaxCaptureBytes) Fail("source_capture_byte_capacity"); }
    private void PersistBytes(string relative, byte[] bytes)
    {
        string path = Path.Combine(directory, relative);
        if (payloadPaths.Contains(relative)) { VerifyBlob(relative, SourceSessionContract.Sha256(bytes), bytes.LongLength); return; }
        if (bytes.LongLength > profile.Limits.MaxPayloadBytes - payloadBytes) Fail("source_payload_total_capacity");
        Directory.CreateDirectory(Path.GetDirectoryName(path)!); WriteNew(path, bytes);
        payloadPaths.Add(relative); payloadBytes += bytes.LongLength;
    }
    private void VerifyBlob(string relative, string hash, long length)
    {
        string path = Path.Combine(directory, relative);
        if (!File.Exists(path) || new FileInfo(path).Length != length || SourceSessionContract.Sha256(File.ReadAllBytes(path)) != hash)
            throw new InvalidDataException("source_blob_missing_or_changed");
    }
    private void Append<T>(string file, T row)
    {
        Healthy(); byte[] bytes = SourceSessionJson.Bytes(row); var stream = streams[file];
        if (bytes.Length > profile.Limits.MaxRowBytes || counts[file] >= profile.Limits.MaxRowsPerStream
            || bytes.Length > profile.Limits.MaxBytesPerStream - stream.Length) Fail("source_stream_capacity");
        try { stream.Write(bytes); stream.Flush(); counts[file]++; }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        { Ledger.MarkFailure("source_disk_append_uncertain"); throw; }
    }
    private void Fail(string code) { Ledger.MarkFailure(code); throw new IOException(code); }
    private void Healthy()
    {
        if (disposed) throw new ObjectDisposedException(nameof(SourceSessionStreamsV2));
        if (!Status.AccountingComplete) throw new InvalidOperationException(Status.Error ?? "source_accounting_failed");
    }
    private static void WriteNew(string path, byte[] bytes)
    { using var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read); stream.Write(bytes); stream.Flush(true); }
    public void Dispose()
    {
        if (disposed) return;
        disposed = true;
        RecordingResourceCleanup.DisposeAll(streams.Values);
    }
}
