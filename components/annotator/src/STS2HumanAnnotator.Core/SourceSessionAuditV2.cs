using System.Globalization;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.Json.Serialization.Metadata;

namespace STS2HumanAnnotator.Core;

/// <summary>Integrity of original epoch/position accounting; no native origin, coverage, causality or admission promotion.</summary>
public static class SourceSessionAuditV2
{
    // Source stream DTOs always serialize nullable members explicitly. Required
    // presence is independent of nullability and of record constructor defaults.
    // Only the shared recording manifest retains its historical omitted-null fields.
    private static JsonSerializerOptions StrictJson(bool manifest, SourceSessionWireFormat format)
    {
        var resolver = new DefaultJsonTypeInfoResolver();
        resolver.Modifiers.Add(info =>
        {
            if (info.Kind != JsonTypeInfoKind.Object) return;
            if (!format.Ordered)
                for (int i = info.Properties.Count - 1; i >= 0; i--)
                    if (SourceSessionWireFormat.IsOrderMember(info.Type, info.Properties[i].Name)) info.Properties.RemoveAt(i);
            foreach (var property in info.Properties)
            {
                bool omittedManifestExtension = manifest && info.Type == typeof(CurrentRecordingManifest)
                    && property.Name is "decision_schema_version" or "disposition_schema_version" or "close_schema_version"
                        or "recovery_schema_version" or "continuous_schema_version" or "text_input_schema_version";
                bool omittedManifestGameFact = manifest && info.Type == typeof(ExactGameIdentity)
                    && property.Name is "version" or "commit";
                property.IsRequired = !omittedManifestExtension && !omittedManifestGameFact;
            }
        });
        return new(SourceSessionJson.Options)
        {
            UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
            RespectNullableAnnotations = true,
            TypeInfoResolver = resolver
        };
    }
    public static SourceSessionAuditResult Audit(string recordingDirectory) => AuditVersion(recordingDirectory, 2);
    internal static SourceSessionAuditResult AuditVersion(string recordingDirectory, int version)
    {
        var format = SourceSessionWireFormat.ForVersion(version);
        var AuditJson = StrictJson(manifest: false, format);
        var ManifestAuditJson = StrictJson(manifest: true, format);
        string directory = Path.GetFullPath(recordingDirectory), session = "";
        long observationCount = 0, inputCount = 0, gaps = 0;
        var kinds = new HashSet<string>(StringComparer.Ordinal); var errors = new List<string>();
        try
        {
            CheckPaths(directory);
            Require(!File.Exists(Path.Combine(directory, "source-accounting-failure.json")), "source_accounting_failed");
            var manifest = Read<CurrentRecordingManifest>("recording-manifest.json");
            var profile = Read<SourceCaptureProfileV2>("capture-profile.json"); session = manifest.SessionId;
            SourceSessionStreamsV2.ValidateProfile(profile);
            Require(manifest.Schema == format.Schema("source-session-manifest") && manifest.SchemaVersion == version && manifest.SourceSchemaVersion == version
                && manifest.SourceEnvironment != null && manifest.CaptureProfileId == profile.ProfileId
                && manifest.CaptureProfileSha256 == SourceSessionContract.Sha256(File.ReadAllBytes(Path.Combine(directory, "capture-profile.json")))
                && manifest.DecisionSchemaVersion == null && manifest.TextInputSchemaVersion == null && manifest.CloseSchemaVersion == null
                && manifest.DispositionSchemaVersion == null && manifest.ContinuousSchemaVersion == null && manifest.SupportedFamilies.Count == 0
                && manifest.NonClaims.SequenceEqual(SourceSessionContractV2.NonClaims), "source_manifest_profile_invalid");
            SourceSessionContract.Identifier(session); SourceSessionContract.Identifier(manifest.TimelineId);
            var environment = manifest.SourceEnvironment!; SourceSessionContract.ValidateEnvironment(environment);
            using var receiptDocument = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(directory, "source-close-receipt.json")));
            var receipt = receiptDocument.RootElement; UniqueJson(receipt);
            var closeFields = new HashSet<string>(new[]
            { "schema", "session_id", "timeline_id", "status", "accounting_complete", "final_position", "sealed_epochs", "final_drains",
              "counts", "stream_sha256", "gap_count", "input_count", "epoch_count", "source_kinds" }, StringComparer.Ordinal);
            if (format.Ordered) closeFields.Add("final_input_prefix_ordinal");
            Require(receipt.EnumerateObject().Select(x => x.Name).ToHashSet(StringComparer.Ordinal).SetEquals(closeFields), "source_close_fields_invalid");
            Require(receipt.GetProperty("counts").EnumerateObject().Select(x => x.Name).ToHashSet(StringComparer.Ordinal).SetEquals(SourceSessionContractV2.StreamFiles)
                && receipt.GetProperty("stream_sha256").EnumerateObject().Select(x => x.Name).ToHashSet(StringComparer.Ordinal).SetEquals(SourceSessionContractV2.StreamFiles), "source_close_stream_inventory_invalid");
            Require(receipt.GetProperty("schema").GetString() == format.Schema("source-session-close")
                && receipt.GetProperty("session_id").GetString() == session && receipt.GetProperty("timeline_id").GetString() == manifest.TimelineId
                && receipt.GetProperty("status").GetString() == "closed" && receipt.GetProperty("accounting_complete").GetBoolean(), "source_close_receipt_invalid");
            var epochRows = Rows<SourceAttachmentEpochV2>("source-attachment-epochs.jsonl", format.Schema("source-attachment-epoch"));
            var segmentRows = Rows<SourceSegmentV2>("source-segments.jsonl", format.Schema("source-segment"));
            var boundaries = Rows<SourceBoundaryV2>("source-boundaries.jsonl", format.Schema("source-boundary"));
            var observations = Rows<SourcePublicObservationV2>("public-observations.jsonl", format.Schema("public-observation"));
            var inputs = Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl", format.Schema("native-input-witness"));
            observationCount = observations.Count; inputCount = inputs.Count;
            Require(epochRows.Count is > 0 && epochRows.Count <= profile.Limits.MaxEpochs && segmentRows.Count is > 0
                && segmentRows.Count <= profile.Limits.MaxSegments && boundaries.Count is > 0
                && boundaries[^1].Kind == "close" && boundaries.Count(x => x.Kind == "close") == 1, "source_epoch_segment_or_close_missing");
            var epochs = new Dictionary<string, SourceAttachmentEpochV2>(StringComparer.Ordinal);
            var ordinal = new Dictionary<string, int>(StringComparer.Ordinal);
            var seals = new Dictionary<string, SourceNativeSealV2>(StringComparer.Ordinal);
            for (int i = 0; i < epochRows.Count; i++)
            {
                var row = epochRows[i]; SourceSessionContract.Identifier(row.EpochId);
                Require(epochs.TryAdd(row.EpochId, row) && !epochRows.Take(i).Any(x => x.Context.StreamGeneration == row.Context.StreamGeneration), "source_epoch_identity_invalid");
                ordinal.Add(row.EpochId, i);
                var context = row.Context; SourceSessionContract.Identifier(context.ScopeId); SourceSessionContract.Identifier(context.StreamGeneration);
                Require(context.Environment == environment && context.PublicationProfileId == profile.PublicationProfileId
                    && context.PublicationProfileDefinitionSha256 == profile.PublicationProfileDefinitionSha256 && context.EagerScope.SequenceEqual(profile.EagerScope)
                    && context.SeamCoverage.Count <= 256, "source_epoch_context_invalid");
                if (context.GameContinuityId != null) SourceSessionContract.Identifier(context.GameContinuityId);
                foreach (var (seam, coverage) in context.SeamCoverage)
                { SourceSessionContract.Identifier(seam); SourceSessionContract.Identifier(coverage.Version); Require(coverage.Coverage is "complete_at_seam" or "sampled" or "unsupported", "source_seam_coverage_invalid"); }
                ulong start = Position(row.StartingPosition); Require(start < ulong.MaxValue && Position(row.InitialPosition) == start + 1, "source_initial_reservation_invalid");
                if (i == 0) Require(row.PreviousEpochId == null && row.PredecessorSeal == null && row.Transition == null, "source_initial_epoch_invalid");
                else
                {
                    var previous = epochRows[i - 1]; var transition = row.Transition;
                    Require(row.PreviousEpochId == previous.EpochId && row.PredecessorSeal != null && transition != null, "source_epoch_chain_invalid");
                    SourceSessionContractV2.Validate(transition!);
                    Require(transition!.Kind is "setup_handoff" or "cleanup" && transition.PreviousGameContinuityId == previous.Context.GameContinuityId
                        && transition.GameContinuityId == context.GameContinuityId && transition.PreviousGameContinuityId != transition.GameContinuityId,
                        "source_epoch_transition_invalid");
                    ValidateSeal(row.PredecessorSeal!); Require(row.PredecessorSeal!.EpochId == previous.EpochId, "source_epoch_seal_invalid");
                    seals.Add(previous.EpochId, row.PredecessorSeal!);
                }
            }
            var close = boundaries[^1]; var finalPosition = receipt.GetProperty("final_position").Deserialize<SourceNativePositionV2>(AuditJson)!;
            Require(close.Position == finalPosition && close.Position.EpochId == epochRows[^1].EpochId, "source_close_position_invalid");
            foreach (var seal in close.SealedEpochs)
            {
                ValidateSeal(seal);
                if (seals.TryGetValue(seal.EpochId, out var prior)) Require(prior == seal, "source_original_seal_rewritten");
                else seals.Add(seal.EpochId, seal);
            }
            Require(close.SealedEpochs.Count == epochs.Count && seals.Count == epochs.Count
                && close.SealedEpochs.Select(x => x.EpochId).Distinct().Count() == epochs.Count
                && SourceSessionJson.Bytes(close.SealedEpochs).SequenceEqual(SourceSessionJson.Bytes(receipt.GetProperty("sealed_epochs").Deserialize<SourceNativeSealV2[]>(AuditJson)!))
                && Position(close.Position) == Index(seals[close.Position.EpochId].ReservedThrough), "source_original_close_seals_invalid");
            foreach (var epoch in epochRows) Require(seals.ContainsKey(epoch.EpochId)
                && Index(seals[epoch.EpochId].ReservedThrough) >= Position(epoch.InitialPosition), "source_initial_reservation_not_sealed");
            var segments = new Dictionary<string, SourceSegmentV2>(StringComparer.Ordinal);
            SourceSegmentV2? previousSegment = null;
            foreach (var row in segmentRows)
            {
                SourceSessionContract.Identifier(row.SegmentId); SourceSessionContract.Validate(row.Declaration);
                Require(segments.TryAdd(row.SegmentId, row) && row.PreviousSegmentId == previousSegment?.SegmentId
                    && (previousSegment == null || Compare(previousSegment.BoundaryPosition, row.BoundaryPosition) <= 0), "source_segment_binding_invalid");
                InSeal(row.BoundaryPosition); kinds.Add(row.Declaration.SourceKind); previousSegment = row;
            }
            Require(segmentRows[0].BoundaryPosition == epochRows[0].StartingPosition, "source_initial_segment_invalid");
            var pausedIntervals = new List<SourcePausedIntervalV2>();
            bool paused = false; SourceNativePositionV2? pause = null; SourceNativePositionV2? lastBoundary = null;
            foreach (var row in boundaries)
            {
                InSeal(row.Position); Require(segments.ContainsKey(row.SegmentId) && (lastBoundary == null || Compare(lastBoundary, row.Position) <= 0), "source_boundary_position_invalid");
                if (row.Kind == "pause") { Require(!paused && row.Transition == null && row.PausedIntervals.Count == 0, "source_pause_transition_invalid"); paused = true; pause = row.Position; }
                else if (row.Kind is "resume" or "close")
                {
                    Require(row.Kind == "close" || paused, "source_resume_transition_invalid");
                    ValidateIntervals(row, paused ? pause : null); paused = false; pause = null;
                }
                else if (row.Kind == "epoch_transition")
                {
                    var epoch = epochs[row.Position.EpochId]; Require(epoch.Transition == row.Transition && epoch.StartingPosition == row.Position
                        && row.SealedEpochs.Count == 1 && row.SealedEpochs[0] == epoch.PredecessorSeal, "source_epoch_boundary_invalid");
                    ValidateIntervals(row, paused ? pause : null); if (paused) pause = row.Position;
                }
                else if (row.Kind is "launch" or "terminal")
                {
                    Require(row.Transition != null && row.Transition.Kind == row.Kind && row.PausedIntervals.Count == 0 && row.SealedEpochs.Count == 0, "source_native_boundary_invalid");
                    SourceSessionContractV2.Validate(row.Transition!); var continuity = epochs[row.Position.EpochId].Context.GameContinuityId;
                    Require(row.Transition!.PreviousGameContinuityId == continuity && row.Transition.GameContinuityId == continuity, "source_native_boundary_continuity_invalid");
                }
                else throw new InvalidDataException("source_boundary_kind_invalid");
                lastBoundary = row.Position;
            }
            var epochBoundaries = boundaries.Where(x => x.Kind == "epoch_transition").ToArray();
            Require(epochBoundaries.Length == epochRows.Count - 1 && epochRows.Skip(1).All(epoch =>
                epochBoundaries.Count(row => row.Position.EpochId == epoch.EpochId) == 1), "source_epoch_boundary_accounting_incomplete");
            foreach (var segment in segmentRows.Skip(1)) Require(boundaries.Any(x => x.Kind == "pause" && Compare(x.Position, segment.BoundaryPosition) <= 0
                && !boundaries.Any(y => y.Kind is "resume" or "close" && y.Sequence > x.Sequence && Compare(y.Position, segment.BoundaryPosition) < 0)), "source_change_not_paused");
            var payloads = new HashSet<string>(StringComparer.Ordinal); var captureIdentities = new Dictionary<string, PublicCaptureReferenceV2>();
            var catalogIdentities = new Dictionary<string, PublicCatalogReferenceV2>(); var catalogActions = new Dictionary<string, IReadOnlyList<SourcePublicAction>>();
            var captureOwners = new Dictionary<string, string>(StringComparer.Ordinal);
            var ranges = epochRows.ToDictionary(x => x.EpochId, _ => new List<(ulong After, ulong Through)>(), StringComparer.Ordinal);
            foreach (var interval in pausedIntervals) ranges[interval.EpochId].Add((Index(interval.AfterIndex), Index(interval.ThroughIndex)));
            var lastObservation = epochRows.ToDictionary(x => x.EpochId, x => Position(x.StartingPosition), StringComparer.Ordinal);
            var sourceIndexes = new Dictionary<string, ulong>(StringComparer.Ordinal);
            foreach (var row in observations)
            {
                ulong index = InSeal(row.Position); Require(row.EpochId == row.Position.EpochId && index > lastObservation[row.EpochId]
                    && !IsPaused(row.Position) && row.GameContinuityId == epochs[row.EpochId].Context.GameContinuityId, "source_observation_original_position_invalid");
                var originalSegment = segmentRows.LastOrDefault(x => Compare(x.BoundaryPosition, row.Position) < 0);
                Require(originalSegment?.SegmentId == row.SegmentId, "source_observation_original_segment_mismatch");
                SourceSessionContract.Identifier(row.SourceSeam); SourceSessionContract.Identifier(row.Phase); ulong sourceIndex = Index(row.SourceIndex);
                if (row.GapAfterIndex != null)
                {
                    Require(row.SourceSeam == "source_gap" && row.SourceIndex == "0" && row.Phase == "retention_overflow" && row.MissingReason == "retention_overflow"
                        && row.Completeness == "failed" && row.Capture == null && row.Catalog == null && Index(row.GapAfterIndex) < index, "source_diagnostic_gap_invalid");
                    ranges[row.EpochId].Add((Index(row.GapAfterIndex), index));
                }
                else
                {
                    string key = row.EpochId + ":" + row.SourceSeam;
                    Require(!sourceIndexes.TryGetValue(key, out ulong before) || sourceIndex > before, "source_source_index_order_invalid"); sourceIndexes[key] = sourceIndex;
                    ranges[row.EpochId].Add((index - 1, index));
                }
                Require((row.Capture == null || row.Capture.EpochId == row.EpochId) && (row.Catalog == null || row.Catalog.EpochId == row.EpochId), "source_observation_epoch_mismatch");
                Join(row.Capture, row.Catalog, row.Completeness == "complete");
                if (row.OwnerOccurrence != null)
                {
                    SourceSessionContract.Identifier(row.OwnerOccurrence);
                    Require(row.Capture == null || captureOwners[row.Capture.EpochId + ":" + row.Capture.CaptureId] == row.OwnerOccurrence,
                        "source_observation_owner_occurrence_mismatch");
                }
                Require(row.Completeness is "complete" or "partial" or "capacity_exceeded" or "failed"
                    && (row.Capture == null || row.Capture.SnapshotId == row.SnapshotId)
                    && (row.Completeness == "complete" ? row.Capture != null && row.Catalog != null && row.MissingReason == null : !string.IsNullOrWhiteSpace(row.MissingReason)), "source_full_reference_incomplete");
                gaps += row.MissingReason != null || row.GapAfterIndex != null ? 1 : 0; lastObservation[row.EpochId] = index;
            }
            var inputIds = new HashSet<string>(StringComparer.Ordinal);
            foreach (var row in inputs)
            {
                ulong index = InSeal(row.PrePosition); SourceSessionContract.Identifier(row.InputId);
                Require(row.EpochId == row.PrePosition.EpochId && inputIds.Add(row.InputId) && segments.TryGetValue(row.SegmentId, out var segment)
                    && Compare(segment.BoundaryPosition, row.PrePosition) <= 0 && !IsPaused(row.PrePosition), "source_input_original_binding_invalid");
                int segmentIndex = segmentRows.FindIndex(x => x.SegmentId == row.SegmentId);
                Require(segmentIndex + 1 == segmentRows.Count || Compare(row.PrePosition, segmentRows[segmentIndex + 1].BoundaryPosition) <= 0, "source_input_original_segment_mismatch");
                Require((row.PreCapture == null || row.PreCapture.EpochId == row.EpochId) && (row.Catalog == null || row.Catalog.EpochId == row.EpochId), "source_input_epoch_mismatch");
                Join(row.PreCapture, row.Catalog, row.Outcome.MappingStatus == "exact"); var outcome = row.Outcome;
                Require(outcome.MappingStatus is "exact" or "unmapped" or "ambiguous" or "capture_missing"
                    && outcome.Delivery is "rejected_before_input" or "delivered" or "partially_delivered" or "unknown"
                    && outcome.MatchCount is >= 0 and <= 65536 && outcome.Stages.Count <= 16, "source_input_outcome_invalid");
                SourceSessionContractV2.Text(outcome.NativeMechanism); if (outcome.ReasonCode != null) SourceSessionContractV2.Text(outcome.ReasonCode);
                foreach (var stage in outcome.Stages)
                { SourceSessionContractV2.Text(stage.Stage); SourceSessionContractV2.Text(stage.Delivery); SourceSessionContractV2.Text(stage.Evidence); Require(stage.Delivery is "rejected_before_input" or "delivered" or "partially_delivered" or "unknown", "source_input_stage_delivery_invalid"); }
                if (outcome.MappingStatus == "exact")
                {
                    Require(outcome.MatchCount == 1 && row.PreCapture != null && row.Catalog != null && outcome.SelectedAction != null, "source_exact_input_basis_missing");
                    var selected = outcome.SelectedAction!; var matches = catalogActions[row.EpochId + ":" + row.Catalog!.CatalogRef].Where(x => x.ActionId == selected.ActionId).ToArray();
                    Require(matches.Length == 1 && SourceCatalogCodec.Digest(matches) == SourceCatalogCodec.Digest(new[] { selected }), "source_selected_action_not_in_original_catalog");
                }
                else Require(outcome.SelectedAction == null && (outcome.MappingStatus != "ambiguous" || outcome.MatchCount >= 2)
                    && (outcome.MappingStatus is not ("unmapped" or "capture_missing") || outcome.MatchCount == 0), "source_input_mapping_invalid");
                Require(row.PreCapture != null || outcome.MappingStatus == "capture_missing", "source_input_capture_missing");
            }
            var drains = receipt.GetProperty("final_drains").Deserialize<SourceFinalDrainV2[]>(AuditJson)!;
            Require(drains.Length == epochs.Count && drains.Select(x => x.EpochId).Distinct().Count() == epochs.Count, "source_final_drain_count_invalid");
            foreach (var drain in drains)
            {
                var epoch = epochs[drain.EpochId]; var seal = seals[drain.EpochId]; ulong reserved = Index(seal.ReservedThrough);
                Require(drain.StreamGeneration == epoch.Context.StreamGeneration && drain.SealedReservedThrough == seal.ReservedThrough
                    && Index(drain.CompletedThrough) == reserved && Index(drain.DurableThrough) == reserved && drain.AdmittedInputsTerminal, "source_final_drain_incomplete");
                ulong covered = Position(epoch.StartingPosition);
                foreach (var range in ranges[epoch.EpochId].Where(x => x.Through > x.After).OrderBy(x => x.After))
                { Require(range.After <= covered && range.Through <= reserved, "source_original_durable_prefix_gap"); covered = Math.Max(covered, range.Through); }
                Require(covered == reserved, "source_original_durable_prefix_gap");
            }
            if (format.Ordered) SourceSessionOrderAuditV3.Validate(epochRows, segmentRows, boundaries, inputs, drains,
                receipt.GetProperty("final_input_prefix_ordinal").GetString()!);
            foreach (string file in new[] { "semantic-boundary-trace.jsonl", "canonical-transitions.jsonl", "native-semantic-discriminator.jsonl", "invalidations.jsonl" })
                Require(File.ReadAllBytes(Path.Combine(directory, file)).Length == 0, "source_session_contains_human_semantics");
            var payloadFiles = Directory.EnumerateFiles(directory, "*.bin", SearchOption.AllDirectories)
                .Select(x => Path.GetRelativePath(directory, x).Replace('\\', '/')).ToHashSet(StringComparer.Ordinal);
            Require(payloadFiles.SetEquals(payloads) && payloadFiles.Sum(x => new FileInfo(Path.Combine(directory, x)).Length) <= profile.Limits.MaxPayloadBytes, "source_payload_inventory_invalid");
            Require(receipt.GetProperty("epoch_count").GetInt32() == epochs.Count && receipt.GetProperty("input_count").GetInt64() == inputCount
                && receipt.GetProperty("gap_count").GetInt64() == gaps && receipt.GetProperty("source_kinds").Deserialize<string[]>(SourceSessionJson.Options)!.SequenceEqual(kinds.Order(StringComparer.Ordinal)), "source_close_summary_mismatch");

            T Read<T>(string file)
            {
                using var document = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(directory, file))); UniqueJson(document.RootElement);
                var allowed = format.Fields(typeof(T));
                Require(document.RootElement.EnumerateObject().All(x => allowed.Contains(x.Name)), "source_json_unknown_field");
                return document.RootElement.Deserialize<T>(typeof(T) == typeof(CurrentRecordingManifest) ? ManifestAuditJson : AuditJson)
                    ?? throw new InvalidDataException("source_json_missing");
            }
            List<T> Rows<T>(string file, string schema)
            {
                byte[] bytes = File.ReadAllBytes(Path.Combine(directory, file)); Require(bytes.LongLength <= profile.Limits.MaxBytesPerStream && (bytes.Length == 0 || bytes[^1] == 10), "source_stream_bytes_invalid");
                var rows = new List<T>(); var expected = format.Fields(typeof(T));
                foreach (string line in File.ReadLines(Path.Combine(directory, file)))
                {
                    Require(System.Text.Encoding.UTF8.GetByteCount(line) <= profile.Limits.MaxRowBytes && rows.Count < profile.Limits.MaxRowsPerStream, "source_row_capacity");
                    using var document = JsonDocument.Parse(line); var root = document.RootElement; UniqueJson(root);
                    Require(root.EnumerateObject().Select(x => x.Name).ToHashSet(StringComparer.Ordinal).SetEquals(expected)
                        && root.GetProperty("schema").GetString() == schema && root.GetProperty("sequence").GetInt64() == rows.Count + 1
                        && root.GetProperty("session_id").GetString() == session && root.GetProperty("timeline_id").GetString() == manifest.TimelineId, "source_row_binding_invalid");
                    rows.Add(root.Deserialize<T>(AuditJson)!);
                }
                Require(receipt.GetProperty("counts").GetProperty(file).GetInt64() == rows.Count
                    && receipt.GetProperty("stream_sha256").GetProperty(file).GetString() == SourceSessionContract.Sha256(bytes), "source_stream_receipt_mismatch");
                return rows;
            }
            ulong Position(SourceNativePositionV2 position)
            { Require(epochs.TryGetValue(position.EpochId, out var epoch) && position.StreamGeneration == epoch.Context.StreamGeneration, "source_epoch_position_mismatch"); return SourceSessionContractV2.Index(position); }
            ulong InSeal(SourceNativePositionV2 position)
            { ulong index = Position(position); Require(index >= Position(epochs[position.EpochId].StartingPosition) && index <= Index(seals[position.EpochId].ReservedThrough), "source_original_epoch_range_exceeded"); return index; }
            int Compare(SourceNativePositionV2 a, SourceNativePositionV2 b)
            { _ = Position(a); _ = Position(b); return a.EpochId == b.EpochId ? Position(a).CompareTo(Position(b)) : ordinal[a.EpochId].CompareTo(ordinal[b.EpochId]); }
            void ValidateSeal(SourceNativeSealV2 seal)
            { Require(epochs.TryGetValue(seal.EpochId, out var epoch) && seal.StreamGeneration == epoch.Context.StreamGeneration && Index(seal.CompletedThrough) <= Index(seal.ReservedThrough), "source_epoch_seal_invalid"); }
            bool IsPaused(SourceNativePositionV2 p) => pausedIntervals.Any(x => x.EpochId == p.EpochId && Index(x.AfterIndex) < Position(p) && Position(p) <= Index(x.ThroughIndex));
            void ValidateIntervals(SourceBoundaryV2 row, SourceNativePositionV2? after)
            {
                Require(after != null || row.PausedIntervals.Count == 0, "source_pause_interval_unexpected");
                if (after != null)
                {
                    var expectedEpoch = row.Kind == "epoch_transition" ? row.SealedEpochs.Single().EpochId : row.Position.EpochId;
                    Require(row.PausedIntervals.Count == 1, "source_pause_interval_unclosed"); var interval = row.PausedIntervals[0];
                    var epoch = epochs[expectedEpoch]; ulong end = row.Kind == "epoch_transition" ? Index(seals[expectedEpoch].ReservedThrough) : Position(row.Position);
                    Require(interval.EpochId == expectedEpoch && interval.StreamGeneration == epoch.Context.StreamGeneration
                        && interval.AfterIndex == after.PublicationIndex && Index(interval.ThroughIndex) == end && interval.Reason == "recording_paused", "source_pause_interval_invalid");
                }
                foreach (var interval in row.PausedIntervals) { pausedIntervals.Add(interval); if (Index(interval.ThroughIndex) > Index(interval.AfterIndex)) gaps++; }
            }
            byte[] Blob(string relative, string hash, long length, string family)
            {
                Require(SourceSessionContract.IsSha256(hash) && relative == family + "/sha256/" + hash[..2] + "/" + hash + ".bin" && length is > 0 && length <= profile.Limits.MaxCaptureBytes, "source_blob_reference_invalid");
                byte[] bytes = File.ReadAllBytes(Path.Combine(directory, relative)); Require(bytes.LongLength == length && SourceSessionContract.Sha256(bytes) == hash, "source_blob_missing_or_changed"); payloads.Add(relative); return bytes;
            }
            void Join(PublicCaptureReferenceV2? capture, PublicCatalogReferenceV2? catalog, bool full = false)
            {
                JsonDocument? document = null;
                try
                {
                    if (capture != null)
                    {
                        string key = capture.EpochId + ":" + capture.CaptureId; var epoch = epochs[capture.EpochId];
                        Require(capture.ScopeId == epoch.Context.ScopeId && capture.StreamGeneration == epoch.Context.StreamGeneration && (!captureIdentities.TryGetValue(key, out var prior) || prior == capture), "source_capture_identity_conflict");
                        SourceSessionContract.Identifier(capture.CaptureId); SourceSessionContract.Identifier(capture.SnapshotId); captureIdentities[key] = capture;
                        document = JsonDocument.Parse(Blob(capture.PayloadRef, capture.Sha256, capture.ByteCount, "public-captures")); var body = document.RootElement; UniqueJson(body);
                        Require(body.GetProperty("schema").GetString() == "sts2.player-environment/native-logical-observation-1" && body.GetProperty("input_profile").GetString() == "native-logical-v1"
                            && body.GetProperty("protocol_version").GetString() == environment.PlayerEnvironmentProtocol && body.GetProperty("snapshot_id").GetString() == capture.SnapshotId
                            && body.GetProperty("session").GetProperty("runtime_instance_id").GetString() == environment.RuntimeInstanceId
                            && body.GetProperty("session").GetProperty("environment_fingerprint").GetString() == environment.EnvironmentFingerprint
                            && !body.GetProperty("information_policy").GetProperty("includes_hidden_information").GetBoolean(), "source_capture_identity_invalid");
                        SourceCaptureCodec.Validate(body, capture.SnapshotId, capture.ScopeId, capture.StreamGeneration, full);
                        captureOwners[key] = body.GetProperty("owner_occurrence").GetProperty("occurrence_id").GetString()!;
                    }
                    if (catalog == null) return;
                    Require(capture != null && catalog.EpochId == capture.EpochId && catalog.SnapshotId == capture.SnapshotId && catalog.ScopeId == capture.ScopeId && catalog.StreamGeneration == capture.StreamGeneration, "source_capture_catalog_join_invalid");
                    string keyCatalog = catalog.EpochId + ":" + catalog.CatalogRef;
                    Require(!catalogIdentities.TryGetValue(keyCatalog, out var original) || original == catalog, "source_catalog_identity_conflict"); catalogIdentities[keyCatalog] = catalog;
                    var actions = SourceCatalogCodec.Decode(Blob(catalog.PayloadRef, catalog.PayloadSha256, catalog.ByteCount, "public-catalogs")); string digest = SourceCatalogCodec.Digest(actions);
                    Require(actions.Count == catalog.TotalCount && digest == catalog.StructuralDigest, "source_catalog_digest_mismatch");
                    var descriptor = document!.RootElement.GetProperty("catalog");
                    Require(descriptor.GetProperty("catalog_ref").GetString() == catalog.CatalogRef && descriptor.GetProperty("snapshot_id").GetString() == catalog.SnapshotId
                        && descriptor.GetProperty("scope_id").GetString() == catalog.ScopeId && descriptor.GetProperty("stream_generation").GetString() == catalog.StreamGeneration
                        && descriptor.GetProperty("status").GetString() == "complete" && descriptor.GetProperty("total_count").GetInt32() == catalog.TotalCount && descriptor.GetProperty("digest").GetString() == digest,
                        "source_capture_catalog_descriptor_mismatch");
                    SourceCaptureCodec.Validate(document.RootElement, capture!.SnapshotId, capture.ScopeId, capture.StreamGeneration, full, actions); catalogActions[keyCatalog] = actions;
                }
                finally { document?.Dispose(); }
            }
        }
        catch (Exception exception) when (exception is InvalidDataException or IOException or UnauthorizedAccessException or JsonException or InvalidOperationException
            or KeyNotFoundException or ArgumentException or FormatException or NullReferenceException or OverflowException)
        { errors.Add(exception is InvalidDataException ? exception.Message : "source_audit_" + exception.GetType().Name); }
        return new(format.Schema("source-session-audit"), errors.Count == 0 ? "pass" : "fail", session, observationCount, inputCount, gaps,
            kinds.Order(StringComparer.Ordinal).ToArray(), errors, SourceSessionContractV2.NonClaims);
    }
    private static ulong Index(string value) => SourceSessionContract.Index(new("generation", value));
    private static void Require(bool condition, string code) { if (!condition) throw new InvalidDataException(code); }
    private static void UniqueJson(JsonElement value)
    {
        if (value.ValueKind == JsonValueKind.Object)
        { var names = new HashSet<string>(StringComparer.Ordinal); foreach (var property in value.EnumerateObject()) { Require(names.Add(property.Name), "source_json_duplicate_field"); UniqueJson(property.Value); } }
        else if (value.ValueKind == JsonValueKind.Array) foreach (var item in value.EnumerateArray()) UniqueJson(item);
    }
    internal static void CheckPaths(string directory)
    {
        Require(Directory.Exists(directory) && !new DirectoryInfo(directory).Attributes.HasFlag(FileAttributes.ReparsePoint), "source_directory_missing_or_link");
        var allowed = new HashSet<string>(SourceSessionContractV2.StreamFiles, StringComparer.Ordinal)
        { "recording-manifest.json", "capture-profile.json", "source-close-receipt.json", "source-coverage.json", "source-accounting-failure.json", "performance-profile.json",
          "recording-owner.lock", "run-journal.jsonl", "invalidations.jsonl", "semantic-boundary-trace.jsonl", "canonical-transitions.jsonl", "native-semantic-discriminator.jsonl" };
        var pending = new Stack<string>(); pending.Push(directory);
        while (pending.TryPop(out var current))
            foreach (var entry in new DirectoryInfo(current).EnumerateFileSystemInfos())
            {
                Require(!entry.Attributes.HasFlag(FileAttributes.ReparsePoint), "source_symlink_forbidden");
                string path = Path.GetRelativePath(directory, entry.FullName).Replace('\\', '/');
                if (entry is DirectoryInfo)
                {
                    Require(System.Text.RegularExpressions.Regex.IsMatch(path, "^(public-captures|public-catalogs)(/sha256(/[0-9a-f]{2})?)?$"), "source_unexpected_raw_directory");
                    pending.Push(entry.FullName);
                }
                else Require(allowed.Contains(path) || System.Text.RegularExpressions.Regex.IsMatch(path, "^(public-captures|public-catalogs)/sha256/[0-9a-f]{2}/[0-9a-f]{64}\\.bin$"), "source_unexpected_raw_file");
            }
    }
}
