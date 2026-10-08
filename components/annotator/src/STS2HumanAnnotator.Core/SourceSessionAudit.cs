using System.Text.Json;

namespace STS2HumanAnnotator.Core;

/// <summary>Verify recorded source facts and exact bytes; never infer operator origin or causal proof.</summary>
public static class SourceSessionAudit
{
    public static SourceSessionAuditResult Audit(string recordingDirectory)
    {
        string directory = Path.GetFullPath(recordingDirectory);
        string session = "";
        long observationCount = 0, inputCount = 0, gaps = 0;
        var kinds = new HashSet<string>(StringComparer.Ordinal);
        var errors = new List<string>();
        try
        {
            CheckPaths(directory);
            if (File.Exists(Path.Combine(directory, "source-accounting-failure.json")))
                throw new InvalidDataException("source_accounting_failed");
            CurrentRecordingManifest manifest = SourceSessionJson.Read<CurrentRecordingManifest>(
                Path.Combine(directory, "recording-manifest.json"));
            SourceCaptureProfile profile = SourceSessionJson.Read<SourceCaptureProfile>(
                Path.Combine(directory, "capture-profile.json"));
            session = manifest.SessionId;
            SourceSessionStreams.ValidateProfile(profile);
            if (manifest.Schema != SourceSessionContract.ManifestSchema || manifest.SchemaVersion != 1
                || manifest.SourceSchemaVersion != 1 || manifest.SourceEnvironment == null
                || manifest.CaptureProfileId != SourceSessionContract.ProfileId
                || manifest.CaptureProfileSha256 != SourceSessionContract.Sha256(File.ReadAllBytes(
                    Path.Combine(directory, "capture-profile.json")))
                || manifest.DecisionSchemaVersion != null || manifest.TextInputSchemaVersion != null)
                throw new InvalidDataException("source_manifest_profile_invalid");
            SourceSessionContract.ValidateEnvironment(manifest.SourceEnvironment);
            using JsonDocument receiptDoc = JsonDocument.Parse(File.ReadAllBytes(
                Path.Combine(directory, "source-close-receipt.json")));
            JsonElement receipt = receiptDoc.RootElement;
            if (receipt.GetProperty("schema").GetString() != SourceSessionContract.CloseSchema
                || receipt.GetProperty("session_id").GetString() != session
                || receipt.GetProperty("timeline_id").GetString() != manifest.TimelineId
                || receipt.GetProperty("status").GetString() != "closed"
                || !receipt.GetProperty("accounting_complete").GetBoolean())
                throw new InvalidDataException("source_close_receipt_invalid");
            SourceClockReference close = receipt.GetProperty("boundary_clock").Deserialize<SourceClockReference>(
                SourceSessionJson.Options) ?? throw new InvalidDataException("source_close_clock_missing");
            ulong closeIndex = SourceSessionContract.Index(close);
            var segments = ReadRows<SourceSegment>("source-segments.jsonl", SourceSessionContract.SegmentSchema,
                value => value.Schema, value => value.Sequence, value => value.SessionId, value => value.TimelineId);
            if (segments.Count == 0 || segments.Count > profile.Limits.MaxSegments)
                throw new InvalidDataException("source_segments_missing_or_excessive");
            var segmentMap = new Dictionary<string, SourceSegment>(StringComparer.Ordinal);
            ulong previousBoundary = 0;
            SourceSegment? previous = null;
            foreach (SourceSegment segment in segments)
            {
                SourceSessionContract.Identifier(segment.SegmentId); SourceSessionContract.Validate(segment.Declaration);
                ulong index = SourceSessionContract.Index(segment.BoundaryClock);
                if (!segmentMap.TryAdd(segment.SegmentId, segment)
                    || segment.PreviousSegmentId != previous?.SegmentId
                    || segment.BoundaryClock.StreamGeneration != close.StreamGeneration
                    || index < previousBoundary || index > closeIndex)
                    throw new InvalidDataException("source_segment_binding_invalid");
                kinds.Add(segment.Declaration.SourceKind);
                previous = segment; previousBoundary = index;
            }
            var boundaries = ReadRows<SourceBoundary>("source-boundaries.jsonl", SourceSessionContract.BoundarySchema,
                value => value.Schema, value => value.Sequence, value => value.SessionId, value => value.TimelineId);
            SourceClockReference? paused = null;
            ulong lastBoundary = SourceSessionContract.Index(segments[0].BoundaryClock);
            if (boundaries.Count == 0 || boundaries[^1].Kind != "close"
                || boundaries.Count(value => value.Kind == "close") != 1)
                throw new InvalidDataException("source_close_boundary_missing");
            foreach (SourceBoundary boundary in boundaries)
            {
                ulong index = SourceSessionContract.Index(boundary.Clock);
                if (!segmentMap.ContainsKey(boundary.SegmentId) || index < lastBoundary || index > closeIndex
                    || boundary.Clock.StreamGeneration != close.StreamGeneration)
                    throw new InvalidDataException("source_boundary_clock_invalid");
                if (boundary.Kind == "pause" && paused == null && boundary.GapAfter == null && boundary.GapReason == null)
                    paused = boundary.Clock;
                else if (boundary.Kind == "resume" && paused != null && boundary.GapAfter == paused
                    && boundary.GapReason == "recording_paused")
                { if (index > SourceSessionContract.Index(paused)) gaps++; paused = null; }
                else if (boundary.Kind == "close" && boundary.Clock == close)
                { if (paused != null && index > SourceSessionContract.Index(paused)) gaps++; }
                else throw new InvalidDataException("source_boundary_transition_invalid");
                lastBoundary = index;
            }
            foreach (SourceSegment segment in segments.Skip(1))
            {
                ulong index = SourceSessionContract.Index(segment.BoundaryClock);
                SourceBoundary? pause = boundaries.LastOrDefault(value => value.Kind == "pause"
                    && SourceSessionContract.Index(value.Clock) <= index);
                SourceBoundary? resume = boundaries.LastOrDefault(value => value.Kind == "resume"
                    && SourceSessionContract.Index(value.Clock) < index);
                if (pause == null || resume != null && resume.Sequence > pause.Sequence)
                    throw new InvalidDataException("source_change_not_paused");
            }
            var captures = new Dictionary<string, PublicCaptureReference>(StringComparer.Ordinal);
            var catalogs = new Dictionary<string, PublicCatalogReference>(StringComparer.Ordinal);
            var actions = new Dictionary<string, IReadOnlyList<SourcePublicAction>>(StringComparer.Ordinal);
            var observations = ReadRows<SourcePublicObservation>("public-observations.jsonl",
                SourceSessionContract.ObservationSchema, v => v.Schema, v => v.Sequence, v => v.SessionId, v => v.TimelineId);
            ulong lastObservation = SourceSessionContract.Index(segments[0].BoundaryClock);
            foreach (SourcePublicObservation observation in observations)
            {
                ulong index = SourceSessionContract.Index(observation.Clock);
                if (observation.Clock.StreamGeneration != close.StreamGeneration || index <= lastObservation || index > closeIndex
                    || observation.ScopeId != profile.ScopeId || !segmentMap.ContainsKey(observation.SegmentId))
                    throw new InvalidDataException("source_observation_clock_invalid");
                SourceSegment segment = segments.Last(value => SourceSessionContract.Index(value.BoundaryClock) < index);
                if (segment.SegmentId != observation.SegmentId || InPausedInterval(index))
                    throw new InvalidDataException("source_observation_segment_or_pause_invalid");
                SourceSessionContract.Identifier(observation.SourceSeam); SourceSessionContract.Identifier(observation.Phase);
                _ = SourceSessionContract.Index(new(close.StreamGeneration, observation.SourceIndex));
                Join(observation.Capture, observation.Catalog, observation.Completeness == "complete");
                if (observation.Capture != null && observation.SnapshotId != observation.Capture.SnapshotId
                    || observation.Completeness == "complete"
                    && (observation.Capture == null || observation.Catalog == null || observation.MissingReason != null)
                    || observation.Completeness != "complete" && string.IsNullOrWhiteSpace(observation.MissingReason)
                    || observation.Completeness is not ("complete" or "partial" or "capacity_exceeded" or "failed"))
                    throw new InvalidDataException("source_observation_completeness_invalid");
                if (observation.GapAfterIndex != null
                    && SourceSessionContract.Index(new(close.StreamGeneration, observation.GapAfterIndex)) >= index)
                    throw new InvalidDataException("source_gap_interval_invalid");
                if (observation.MissingReason != null || observation.GapAfterIndex != null) gaps++;
                lastObservation = index;
            }
            observationCount = observations.Count;
            var inputs = ReadRows<SourceNativeInputWitness>("native-input-witnesses.jsonl",
                SourceSessionContract.InputSchema, v => v.Schema, v => v.Sequence, v => v.SessionId, v => v.TimelineId);
            var inputIds = new HashSet<string>(StringComparer.Ordinal);
            foreach (SourceNativeInputWitness input in inputs)
            {
                SourceSessionContract.Identifier(input.InputId);
                ulong index = SourceSessionContract.Index(input.PreClock);
                if (!inputIds.Add(input.InputId) || !segmentMap.TryGetValue(input.SegmentId, out SourceSegment? segment)
                    || input.PreClock.StreamGeneration != close.StreamGeneration || index > closeIndex
                    || index < SourceSessionContract.Index(segment.BoundaryClock) || InPausedInterval(index))
                    throw new InvalidDataException("source_input_identity_invalid");
                int segmentIndex = segments.IndexOf(segment);
                if (segmentIndex + 1 < segments.Count
                    && index > SourceSessionContract.Index(segments[segmentIndex + 1].BoundaryClock))
                    throw new InvalidDataException("source_input_relabelled");
                Join(input.PreCapture, input.Catalog);
                SourceInputOutcome outcome = input.Outcome;
                SourceSessionContract.Identifier(outcome.NativeMechanism);
                if (outcome.MappingStatus is not ("exact" or "unmapped" or "ambiguous" or "capture_missing")
                    || outcome.Delivery is not ("rejected_before_input" or "delivered" or "partially_delivered" or "unknown")
                    || outcome.MatchCount < 0 || outcome.MatchCount > 65536)
                    throw new InvalidDataException("source_input_outcome_invalid");
                if (outcome.MappingStatus == "exact")
                {
                    if (outcome.MatchCount != 1 || outcome.SelectedAction == null || input.PreCapture == null || input.Catalog == null)
                        throw new InvalidDataException("source_exact_input_basis_missing");
                    Join(input.PreCapture, input.Catalog, requireFull: true);
                    var matches = actions[input.Catalog.CatalogRef]
                        .Where(value => value.ActionId == outcome.SelectedAction.ActionId).ToArray();
                    if (matches.Length != 1 || SourceCatalogCodec.Digest(matches)
                        != SourceCatalogCodec.Digest(new[] { outcome.SelectedAction }))
                        throw new InvalidDataException("source_selected_action_not_in_original_catalog");
                }
                else if (outcome.SelectedAction != null || outcome.MappingStatus == "ambiguous" && outcome.MatchCount < 2
                    || outcome.MappingStatus is "unmapped" or "capture_missing" && outcome.MatchCount != 0)
                    throw new InvalidDataException("source_input_mapping_invalid");
                if (input.PreCapture == null && outcome.MappingStatus != "capture_missing")
                    throw new InvalidDataException("source_input_capture_missing");
            }
            inputCount = inputs.Count;
            if (receipt.GetProperty("gap_count").GetInt64() != gaps
                || !receipt.GetProperty("source_kinds").EnumerateArray().Select(x => x.GetString()!)
                    .SequenceEqual(kinds.Order(StringComparer.Ordinal)))
                throw new InvalidDataException("source_close_summary_mismatch");
            foreach (string file in new[] { "semantic-boundary-trace.jsonl", "canonical-transitions.jsonl",
                "native-semantic-discriminator.jsonl", "invalidations.jsonl" })
                if (File.ReadAllBytes(Path.Combine(directory, file)).Length != 0)
                    throw new InvalidDataException("source_session_contains_human_semantics");

            List<T> ReadRows<T>(string file, string schema, Func<T, string> getSchema, Func<T, long> getSequence,
                Func<T, string> getSession, Func<T, string> getTimeline)
            {
                string path = Path.Combine(directory, file);
                if (new FileInfo(path).Length > profile.Limits.MaxBytesPerStream)
                    throw new InvalidDataException("source_stream_byte_limit");
                var rows = new List<T>();
                foreach (string line in File.ReadLines(path))
                {
                    if (string.IsNullOrWhiteSpace(line)) throw new InvalidDataException("source_stream_empty_row");
                    if (System.Text.Encoding.UTF8.GetByteCount(line) + 1 > profile.Limits.MaxRowBytes)
                        throw new InvalidDataException("source_stream_row_limit");
                    T row = JsonSerializer.Deserialize<T>(line, SourceSessionJson.Options)
                        ?? throw new InvalidDataException("source_stream_row_missing");
                    if (getSchema(row) != schema || getSequence(row) != rows.Count + 1
                        || getSession(row) != session || getTimeline(row) != manifest.TimelineId)
                        throw new InvalidDataException("source_stream_identity_or_sequence_invalid");
                    rows.Add(row);
                    if (rows.Count > profile.Limits.MaxRowsPerStream) throw new InvalidDataException("source_stream_count_limit");
                }
                if (receipt.GetProperty("counts").GetProperty(file).GetInt64() != rows.Count
                    || receipt.GetProperty("stream_sha256").GetProperty(file).GetString()
                    != SourceSessionContract.Sha256(File.ReadAllBytes(path)))
                    throw new InvalidDataException("source_stream_close_hash_mismatch");
                return rows;
            }
            bool InPausedInterval(ulong index)
            {
                SourceClockReference? begin = null;
                foreach (SourceBoundary boundary in boundaries)
                {
                    if (boundary.Kind == "pause") begin = boundary.Clock;
                    else if (boundary.Kind is "resume" or "close" && begin != null)
                    { if (index > SourceSessionContract.Index(begin) && index <= SourceSessionContract.Index(boundary.Clock)) return true; begin = null; }
                }
                return false;
            }
            void Join(PublicCaptureReference? capture, PublicCatalogReference? catalog, bool requireFull = false)
            {
                if (capture != null)
                {
                    if (captures.TryGetValue(capture.CaptureId, out var previousCapture) && previousCapture != capture)
                        throw new InvalidDataException("source_capture_identity_conflict");
                    captures[capture.CaptureId] = capture;
                    byte[] bytes = Blob(capture.PayloadRef, capture.Sha256, capture.ByteCount, "public-captures");
                    using JsonDocument body = JsonDocument.Parse(bytes);
                    JsonElement root = body.RootElement;
                    if (root.GetProperty("schema").GetString() != "sts2.player-environment/native-logical-observation-1"
                        || root.GetProperty("input_profile").GetString() != "native-logical-v1"
                        || root.GetProperty("snapshot_id").GetString() != capture.SnapshotId
                        || root.GetProperty("protocol_version").GetString() != manifest.SourceEnvironment!.PlayerEnvironmentProtocol
                        || root.GetProperty("session").GetProperty("runtime_instance_id").GetString() != manifest.SourceEnvironment!.RuntimeInstanceId
                        || root.GetProperty("session").GetProperty("environment_fingerprint").GetString() != manifest.SourceEnvironment.EnvironmentFingerprint
                        || root.GetProperty("information_policy").GetProperty("includes_hidden_information").GetBoolean()
                        || capture.ScopeId != profile.ScopeId || capture.StreamGeneration != close.StreamGeneration)
                        throw new InvalidDataException("source_capture_identity_invalid");
                    SourceCaptureCodec.Validate(root, capture, requireFull);
                }
                if (catalog == null) return;
                if (capture == null || capture.SnapshotId != catalog.SnapshotId || capture.ScopeId != catalog.ScopeId
                    || capture.StreamGeneration != catalog.StreamGeneration)
                    throw new InvalidDataException("source_capture_catalog_join_invalid");
                if (catalogs.TryGetValue(catalog.CatalogRef, out var previousCatalog) && previousCatalog != catalog)
                    throw new InvalidDataException("source_catalog_identity_conflict");
                catalogs[catalog.CatalogRef] = catalog;
                var decoded = SourceCatalogCodec.Decode(Blob(catalog.PayloadRef, catalog.PayloadSha256,
                    catalog.ByteCount, "public-catalogs"));
                if (decoded.Count != catalog.TotalCount || decoded.Count > 65536
                    || SourceCatalogCodec.Digest(decoded) != catalog.StructuralDigest)
                    throw new InvalidDataException("source_catalog_digest_mismatch");
                actions[catalog.CatalogRef] = decoded;
                using JsonDocument json = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(directory, capture.PayloadRef)));
                JsonElement descriptor = json.RootElement.GetProperty("catalog");
                SourceCaptureCodec.Validate(json.RootElement, capture, requireFull, decoded);
                if (descriptor.GetProperty("catalog_ref").GetString() != catalog.CatalogRef
                    || descriptor.GetProperty("snapshot_id").GetString() != catalog.SnapshotId
                    || descriptor.GetProperty("scope_id").GetString() != catalog.ScopeId
                    || descriptor.GetProperty("stream_generation").GetString() != catalog.StreamGeneration
                    || descriptor.GetProperty("status").GetString() != "complete"
                    || descriptor.GetProperty("total_count").GetInt32() != catalog.TotalCount
                    || descriptor.GetProperty("digest").GetString() != catalog.StructuralDigest)
                    throw new InvalidDataException("source_capture_catalog_descriptor_mismatch");
            }
            byte[] Blob(string relative, string hash, long length, string family)
            {
                if (!SourceSessionContract.IsSha256(hash)
                    || relative != family + "/sha256/" + hash[..2] + "/" + hash + ".bin"
                    || length is < 1 || length > profile.Limits.MaxCaptureBytes)
                    throw new InvalidDataException("source_blob_reference_invalid");
                byte[] bytes = File.ReadAllBytes(Path.Combine(directory, relative));
                if (bytes.LongLength != length || SourceSessionContract.Sha256(bytes) != hash)
                    throw new InvalidDataException("source_blob_missing_or_changed");
                return bytes;
            }
        }
        catch (Exception exception) when (exception is InvalidDataException or IOException or UnauthorizedAccessException or JsonException
            or InvalidOperationException or KeyNotFoundException or ArgumentException or FormatException
            or NullReferenceException)
        { errors.Add(exception is InvalidDataException ? exception.Message : "source_audit_" + exception.GetType().Name); }
        return new(SourceSessionContract.AuditSchema, errors.Count == 0 ? "pass" : "fail", session,
            observationCount, inputCount, gaps, kinds.Order(StringComparer.Ordinal).ToArray(),
            errors, SourceSessionContract.NonClaims);
    }

    internal static void CheckPaths(string directory)
    {
        if (!Directory.Exists(directory) || new DirectoryInfo(directory).Attributes.HasFlag(FileAttributes.ReparsePoint))
            throw new InvalidDataException("source_directory_missing_or_link");
        var pending = new Stack<string>(); pending.Push(directory);
        var allowed = new HashSet<string>(SourceSessionContract.StreamFiles, StringComparer.Ordinal)
        {
            "recording-manifest.json", "capture-profile.json", "source-close-receipt.json",
            "source-coverage.json", "source-accounting-failure.json", "performance-profile.json",
            "recording-owner.lock", "run-journal.jsonl", "invalidations.jsonl",
            "semantic-boundary-trace.jsonl", "canonical-transitions.jsonl", "native-semantic-discriminator.jsonl"
        };
        while (pending.TryPop(out string? current))
            foreach (FileSystemInfo entry in new DirectoryInfo(current).EnumerateFileSystemInfos())
            {
                if (entry.Attributes.HasFlag(FileAttributes.ReparsePoint)) throw new InvalidDataException("source_symlink_forbidden");
                if (entry is DirectoryInfo) pending.Push(entry.FullName);
                else
                {
                    string relative = Path.GetRelativePath(directory, entry.FullName).Replace('\\', '/');
                    if (!allowed.Contains(relative) && !System.Text.RegularExpressions.Regex.IsMatch(relative,
                            "^(public-captures|public-catalogs)/sha256/[0-9a-f]{2}/[0-9a-f]{64}\\.bin$"))
                        throw new InvalidDataException("source_unexpected_raw_file");
                }
            }
    }
}
