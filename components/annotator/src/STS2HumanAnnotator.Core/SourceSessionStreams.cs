using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace STS2HumanAnnotator.Core;

internal static class SourceSessionJson
{
    internal static JsonSerializerOptions Options { get; } = new(EvidenceJson.Options)
    { PropertyNameCaseInsensitive = false, DefaultIgnoreCondition = JsonIgnoreCondition.Never };
    internal static byte[] Bytes<T>(T value) => Encoding.UTF8.GetBytes(
        JsonSerializer.Serialize(value, Options) + "\n");
    internal static T Read<T>(string path) => JsonSerializer.Deserialize<T>(
        File.ReadAllBytes(path), Options) ?? throw new InvalidDataException("source_json_missing");
    internal static T Copy<T>(T value) => JsonSerializer.Deserialize<T>(
        Bytes(value), Options) ?? throw new InvalidDataException("source_copy_failed");
}

/// <summary>Append bookkeeping inside RecordingSessionStore; no independent lifecycle or causal authority.</summary>
internal sealed class SourceSessionStreams : IDisposable
{
    private readonly string directory;
    private readonly CurrentRecordingManifest manifest;
    private readonly SourceCaptureProfile profile;
    private readonly Dictionary<string, FileStream> streams = new(StringComparer.Ordinal);
    private readonly Dictionary<string, long> counts = new(StringComparer.Ordinal);
    private readonly List<SourceSegment> segments = new();
    private readonly List<SourceBoundary> boundaries = new();
    private readonly Dictionary<string, PublicCaptureReference> captures = new(StringComparer.Ordinal);
    private readonly Dictionary<string, PublicCatalogReference> catalogs = new(StringComparer.Ordinal);
    private readonly Dictionary<string, string> observations = new(StringComparer.Ordinal);
    private readonly Dictionary<string, SourceInputScope> pending = new(StringComparer.Ordinal);
    private readonly Dictionary<string, string> completed = new(StringComparer.Ordinal);
    private readonly HashSet<string> payloadPaths = new(StringComparer.Ordinal);
    private SourceClockReference boundaryClock;
    private SourceClockReference? pauseClock;
    private SourceClockReference? closeClock;
    private ulong lastObservationIndex;
    private long payloadBytes;
    private long gapCount;
    private bool failed;
    private bool disposed;
    private string? error;

    internal SourceSessionStreams(string directory, CurrentRecordingManifest manifest,
        SourceCaptureProfile profile, SourceDeclaration initialSource, SourceClockReference initialClock)
    {
        ValidateProfile(profile);
        SourceSessionContract.Validate(initialSource);
        SourceSessionContract.Index(initialClock);
        this.directory = directory; this.manifest = manifest;
        this.profile = SourceSessionJson.Copy(profile);
        boundaryClock = initialClock; lastObservationIndex = SourceSessionContract.Index(initialClock);
        try
        {
            foreach (string file in SourceSessionContract.StreamFiles)
            {
                streams.Add(file, new FileStream(Path.Combine(directory, file), FileMode.CreateNew,
                    FileAccess.Write, FileShare.Read, 65536));
                counts.Add(file, 0);
            }
            ChangeSourceCore(initialSource, initialClock);
        }
        catch { Dispose(); throw; }
    }

    internal static void ValidateProfile(SourceCaptureProfile value)
    {
        SourceSessionContract.Identifier(value.ScopeId);
        SourceSessionLimits limits = value.Limits;
        if (value.Schema != SourceSessionContract.ProfileSchema || value.ProfileId != SourceSessionContract.ProfileId
            || value.InputProfile != "native-logical-v1"
            || !value.EagerScope.SequenceEqual(new[] { "persistent", "interaction", "referents", "catalog" })
            || value.SeamCoverage.Count > 256
            || limits.MaxCaptureBytes is < 1 or > 64 * 1024 * 1024
            || limits.MaxPayloadBytes < limits.MaxCaptureBytes || limits.MaxPayloadBytes > 512L * 1024 * 1024
            || limits.MaxRowBytes is < 512 or > 1024 * 1024
            || limits.MaxRowsPerStream is < 1 or > 65536
            || limits.MaxBytesPerStream < limits.MaxRowBytes || limits.MaxBytesPerStream > 64L * 1024 * 1024
            || limits.MaxSegments is < 1 or > 256 || limits.MaxPendingInputs is < 1 or > 128)
            throw new InvalidDataException("source_profile_invalid");
        foreach (var (seam, coverage) in value.SeamCoverage)
        {
            SourceSessionContract.Identifier(seam); SourceSessionContract.Identifier(coverage.Version);
            if (coverage.Coverage is not ("complete_at_seam" or "sampled" or "unsupported"))
                throw new InvalidDataException("source_seam_coverage_invalid");
        }
    }

    internal SourceCaptureProfile Profile => SourceSessionJson.Copy(profile);
    internal SourceSessionStatus Status => new(segments[^1].SegmentId, segments[^1].Declaration,
        counts["public-observations.jsonl"], counts["native-input-witnesses.jsonl"],
        pending.Count, gapCount, !failed, error);

    internal SourceSegment ChangeSource(SourceDeclaration source, string expectedSegment,
        SourceClockReference clock, RecordingLifecycleState state)
    {
        Healthy();
        if (state != RecordingLifecycleState.Paused || pauseClock == null)
            throw new InvalidOperationException("source_change_requires_pause");
        if (segments[^1].SegmentId != expectedSegment)
            throw new InvalidOperationException("source_segment_changed");
        SourceSessionContract.Validate(source); CheckClock(clock, monotonicBoundary: true);
        if (segments.Count >= profile.Limits.MaxSegments)
            return Fail<SourceSegment>("source_segment_capacity");
        return ChangeSourceCore(source, clock);
    }

    private SourceSegment ChangeSourceCore(SourceDeclaration source, SourceClockReference clock)
    {
        var row = new SourceSegment(SourceSessionContract.SegmentSchema, segments.Count + 1,
            manifest.SessionId, manifest.TimelineId, "source-segment-" + Guid.NewGuid().ToString("N"),
            segments.Count == 0 ? null : segments[^1].SegmentId, source, clock, DateTimeOffset.UtcNow);
        Append("source-segments.jsonl", row);
        segments.Add(row); boundaryClock = clock;
        return row;
    }

    internal void Boundary(string kind, SourceClockReference clock, RecordingLifecycleState state)
    {
        Healthy(); CheckClock(clock, monotonicBoundary: true);
        if (!(kind == "pause" && state == RecordingLifecycleState.Paused && pauseClock == null
            || kind == "resume" && state == RecordingLifecycleState.Recording && pauseClock != null
            || kind == "close" && state == RecordingLifecycleState.Closing && closeClock == null))
            throw new InvalidOperationException("source_boundary_transition_invalid");
        SourceClockReference? gapAfter = kind == "resume" ? pauseClock : null;
        var row = new SourceBoundary(SourceSessionContract.BoundarySchema, boundaries.Count + 1,
            manifest.SessionId, manifest.TimelineId, kind, segments[^1].SegmentId, clock,
            gapAfter, gapAfter == null ? null : "recording_paused", DateTimeOffset.UtcNow);
        Append("source-boundaries.jsonl", row); boundaries.Add(row);
        if (gapAfter != null && SourceSessionContract.Index(clock) > SourceSessionContract.Index(gapAfter))
            gapCount++;
        if (kind == "pause") pauseClock = clock;
        if (kind == "resume") pauseClock = null;
        if (kind == "close")
        {
            closeClock = clock;
            if (pauseClock != null && SourceSessionContract.Index(clock) > SourceSessionContract.Index(pauseClock))
                gapCount++;
        }
        boundaryClock = clock;
    }

    internal PublicCaptureReference Persist(FrozenPublicCapture capture)
    {
        Healthy(); Identity(capture.CaptureId, capture.SnapshotId, capture.ScopeId, capture.StreamGeneration);
        if (capture.Bytes.Length > profile.Limits.MaxCaptureBytes) return Fail<PublicCaptureReference>("source_capture_byte_capacity");
        capture = capture with { Bytes = (byte[])capture.Bytes.Clone() };
        CheckBytes(capture.Bytes, capture.Sha256);
        using JsonDocument json = JsonDocument.Parse(capture.Bytes);
        JsonElement root = json.RootElement;
        if (root.ValueKind != JsonValueKind.Object
            || root.GetProperty("schema").GetString() != "sts2.player-environment/native-logical-observation-1"
            || root.GetProperty("input_profile").GetString() != "native-logical-v1"
            || root.GetProperty("snapshot_id").GetString() != capture.SnapshotId
            || root.GetProperty("session").GetProperty("runtime_instance_id").GetString() != manifest.SourceEnvironment!.RuntimeInstanceId
            || root.GetProperty("session").GetProperty("environment_fingerprint").GetString() != manifest.SourceEnvironment.EnvironmentFingerprint
            || root.GetProperty("information_policy").GetProperty("includes_hidden_information").GetBoolean())
            throw new InvalidDataException("source_capture_identity_invalid");
        string relative = "public-captures/sha256/" + capture.Sha256[..2] + "/" + capture.Sha256 + ".bin";
        var reference = new PublicCaptureReference(capture.CaptureId, capture.SnapshotId,
            capture.ScopeId, capture.StreamGeneration, capture.CapturedAt, capture.Bytes.LongLength, capture.Sha256, relative);
        if (captures.TryGetValue(capture.CaptureId, out var previous))
        {
            if (previous != reference) throw new InvalidDataException("source_capture_id_conflict");
            VerifyBlob(previous.PayloadRef, previous.Sha256, previous.ByteCount); return previous;
        }
        if (captures.Count >= profile.Limits.MaxRowsPerStream) return Fail<PublicCaptureReference>("source_capture_count_capacity");
        PersistBytes(relative, capture.Bytes); captures.Add(reference.CaptureId, reference);
        return reference;
    }

    internal PublicCatalogReference Persist(FrozenPublicCatalog catalog)
    {
        Healthy(); Identity(catalog.CatalogRef, catalog.SnapshotId, catalog.ScopeId, catalog.StreamGeneration);
        if (catalog.Bytes.Length > profile.Limits.MaxCaptureBytes) return Fail<PublicCatalogReference>("source_capture_byte_capacity");
        catalog = catalog with { Bytes = (byte[])catalog.Bytes.Clone() };
        CheckBytes(catalog.Bytes, catalog.PayloadSha256);
        IReadOnlyList<SourcePublicAction> actions = SourceCatalogCodec.Decode(catalog.Bytes);
        if (catalog.TotalCount != actions.Count || actions.Count > 65536
            || SourceCatalogCodec.Digest(actions) != catalog.StructuralDigest)
            throw new InvalidDataException("source_catalog_digest_mismatch");
        string relative = "public-catalogs/sha256/" + catalog.PayloadSha256[..2] + "/" + catalog.PayloadSha256 + ".bin";
        var reference = new PublicCatalogReference(catalog.CatalogRef, catalog.SnapshotId, catalog.ScopeId,
            catalog.StreamGeneration, catalog.TotalCount, catalog.StructuralDigest,
            catalog.Bytes.LongLength, catalog.PayloadSha256, relative);
        if (catalogs.TryGetValue(catalog.CatalogRef, out var previous))
        {
            if (previous != reference) throw new InvalidDataException("source_catalog_id_conflict");
            VerifyBlob(previous.PayloadRef, previous.PayloadSha256, previous.ByteCount); return previous;
        }
        if (catalogs.Count >= profile.Limits.MaxRowsPerStream) return Fail<PublicCatalogReference>("source_catalog_count_capacity");
        PersistBytes(relative, catalog.Bytes); catalogs.Add(reference.CatalogRef, reference);
        return reference;
    }

    internal void Observe(SourceObservationPacket packet)
    {
        Healthy(); CheckClock(packet.Clock);
        ulong index = SourceSessionContract.Index(packet.Clock);
        string key = packet.Clock.StreamGeneration + ":" + packet.Clock.PublicationIndex + ":" + packet.ScopeId;
        if (packet.ScopeId != profile.ScopeId || index <= SourceSessionContract.Index(segments[0].BoundaryClock))
            throw new InvalidDataException("source_observation_before_attach_or_scope_mismatch");
        SourceSessionContract.Identifier(packet.SourceSeam);
        _ = SourceSessionContract.Index(new(packet.Clock.StreamGeneration, packet.SourceIndex));
        SourceSessionContract.Identifier(packet.Phase);
        if (packet.Completeness is not ("complete" or "partial" or "capacity_exceeded" or "failed"))
            throw new InvalidDataException("source_observation_completeness_invalid");
        // A delayed encoder is classified by its original source position, never by the current actor.
        if (IsPaused(index) || closeClock != null && index > SourceSessionContract.Index(closeClock)) return;
        PublicCaptureReference? capture = packet.Capture == null ? null : Persist(packet.Capture);
        PublicCatalogReference? catalog = packet.Catalog == null ? null : Persist(packet.Catalog);
        Join(capture, catalog);
        if (packet.Completeness == "complete" && (capture == null || catalog == null || packet.MissingReason != null))
            throw new InvalidDataException("source_full_reference_incomplete");
        if (packet.Completeness != "complete" && string.IsNullOrWhiteSpace(packet.MissingReason))
            throw new InvalidDataException("source_missing_reason_required");
        if (capture != null && (capture.SnapshotId != packet.SnapshotId
            || capture.StreamGeneration != packet.Clock.StreamGeneration))
            throw new InvalidDataException("source_observation_capture_mismatch");
        if (packet.GapAfterIndex != null
            && SourceSessionContract.Index(new(packet.Clock.StreamGeneration, packet.GapAfterIndex)) >= index)
            throw new InvalidDataException("source_gap_interval_invalid");
        SourceSegment segment = segments.Last(value => SourceSessionContract.Index(value.BoundaryClock) < index);
        var row = new SourcePublicObservation(SourceSessionContract.ObservationSchema,
            counts["public-observations.jsonl"] + 1, manifest.SessionId, manifest.TimelineId, segment.SegmentId,
            packet.Clock, packet.ScopeId, packet.SourceSeam, packet.SourceIndex, packet.Phase,
            packet.SnapshotId, packet.OwnerOccurrence, packet.Completeness, capture, catalog,
            packet.MissingReason, packet.GapAfterIndex);
        string signature = SourceSessionContract.Sha256(SourceSessionJson.Bytes(row with { Sequence = 0 }));
        if (observations.TryGetValue(key, out string? previous))
        {
            if (previous != signature) throw new InvalidDataException("source_observation_identity_conflict");
            return;
        }
        if (index <= lastObservationIndex) throw new InvalidDataException("source_observation_order_invalid");
        Append("public-observations.jsonl", row);
        observations.Add(key, signature); lastObservationIndex = index;
        if (packet.MissingReason != null || packet.GapAfterIndex != null) gapCount++;
    }

    internal SourceInputScope Begin(string inputId, SourceClockReference clock,
        PublicCaptureReference? pre, PublicCatalogReference? catalog, RecordingLifecycleState state)
    {
        Healthy(); SourceSessionContract.Identifier(inputId); CheckClock(clock, monotonicBoundary: true);
        if (state != RecordingLifecycleState.Recording || pauseClock != null || closeClock != null)
            throw new InvalidOperationException("source_input_not_recording");
        if (IsPaused(SourceSessionContract.Index(clock)))
            throw new InvalidDataException("source_input_clock_in_paused_interval");
        if (pending.ContainsKey(inputId) || completed.ContainsKey(inputId))
            throw new InvalidDataException("source_input_id_conflict");
        if (pending.Count >= profile.Limits.MaxPendingInputs
            || pending.Count + completed.Count >= profile.Limits.MaxRowsPerStream)
            return Fail<SourceInputScope>("source_input_capacity");
        Join(pre, catalog);
        var scope = new SourceInputScope(this, inputId, segments[^1].SegmentId, clock, pre, catalog)
        { BasisBound = pre != null || catalog != null };
        pending.Add(inputId, scope); return scope;
    }

    internal SourceInputScope Bind(SourceInputScope scope, PublicCaptureReference? pre, PublicCatalogReference? catalog)
    {
        Healthy();
        if (!ReferenceEquals(scope.Owner, this) || !pending.TryGetValue(scope.InputId, out var exact)
            || !ReferenceEquals(scope, exact))
            throw new InvalidDataException("source_input_scope_not_current");
        Join(pre, catalog);
        if (scope.BasisBound)
        {
            if (scope.PreCapture != pre || scope.Catalog != catalog)
                throw new InvalidDataException("source_input_basis_changed");
            return scope;
        }
        var bound = new SourceInputScope(this, scope.InputId, scope.SegmentId, scope.PreClock, pre, catalog)
        { BasisBound = true };
        pending[scope.InputId] = bound;
        return bound;
    }

    internal void Complete(SourceInputScope scope, SourceInputOutcome outcome)
    {
        Healthy();
        if (!ReferenceEquals(scope.Owner, this)) throw new InvalidDataException("source_input_session_mismatch");
        outcome = SourceSessionJson.Copy(outcome);
        ValidateOutcome(scope, outcome);
        string signature = SourceSessionContract.Sha256(SourceSessionJson.Bytes(outcome));
        if (completed.TryGetValue(scope.InputId, out string? previous))
        {
            if (previous != signature) throw new InvalidDataException("source_input_completion_conflict");
            return;
        }
        if (!pending.TryGetValue(scope.InputId, out var exact) || !ReferenceEquals(exact, scope))
            throw new InvalidDataException("source_input_scope_not_current");
        var row = new SourceNativeInputWitness(SourceSessionContract.InputSchema,
            counts["native-input-witnesses.jsonl"] + 1, manifest.SessionId, manifest.TimelineId,
            scope.InputId, scope.SegmentId, scope.PreClock, scope.PreCapture, scope.Catalog, outcome, DateTimeOffset.UtcNow);
        Append("native-input-witnesses.jsonl", row);
        completed.Add(scope.InputId, signature); pending.Remove(scope.InputId);
    }

    private void ValidateOutcome(SourceInputScope scope, SourceInputOutcome value)
    {
        if (value.MappingStatus is not ("exact" or "unmapped" or "ambiguous" or "capture_missing")
            || value.Delivery is not ("rejected_before_input" or "delivered" or "partially_delivered" or "unknown")
            || value.MatchCount < 0 || value.MatchCount > 65536)
            throw new InvalidDataException("source_input_outcome_invalid");
        SourceSessionContract.Identifier(value.NativeMechanism);
        if (value.MappingStatus == "exact")
        {
            if (value.MatchCount != 1 || value.SelectedAction == null || scope.PreCapture == null || scope.Catalog == null)
                throw new InvalidDataException("source_exact_input_basis_missing");
            VerifyBlob(scope.Catalog.PayloadRef, scope.Catalog.PayloadSha256, scope.Catalog.ByteCount);
            var actions = SourceCatalogCodec.Decode(File.ReadAllBytes(Path.Combine(directory, scope.Catalog.PayloadRef)));
            var matches = actions.Where(a => a.ActionId == value.SelectedAction.ActionId).ToArray();
            if (matches.Length != 1 || SourceCatalogCodec.Digest(matches) != SourceCatalogCodec.Digest(new[] { value.SelectedAction }))
                throw new InvalidDataException("source_selected_action_not_in_original_catalog");
        }
        else if (value.SelectedAction != null
            || value.MappingStatus == "ambiguous" && value.MatchCount < 2
            || value.MappingStatus is "unmapped" or "capture_missing" && value.MatchCount != 0)
            throw new InvalidDataException("source_input_mapping_invalid");
        if (scope.PreCapture == null && value.MappingStatus != "capture_missing")
            throw new InvalidDataException("source_missing_capture_mapping_invalid");
    }

    internal void PrepareClose()
    {
        Healthy();
        if (closeClock == null) throw new InvalidOperationException("source_close_boundary_required");
        foreach (SourceInputScope scope in pending.Values.ToArray())
            Complete(scope, new(scope.PreCapture == null ? "capture_missing" : "unmapped", 0, null,
                "source_session_close", "unknown", "session_closed_before_input_completion"));
        foreach (FileStream stream in streams.Values) stream.Flush(true);
    }

    internal void WriteCloseReceipt()
    {
        Healthy();
        var hashes = streams.Keys.ToDictionary(file => file,
            file => SourceSessionContract.Sha256(File.ReadAllBytes(Path.Combine(directory, file))), StringComparer.Ordinal);
        var receipt = new
        {
            schema = SourceSessionContract.CloseSchema, session_id = manifest.SessionId,
            timeline_id = manifest.TimelineId, status = "closed", accounting_complete = true,
            boundary_clock = closeClock, counts, stream_sha256 = hashes, gap_count = gapCount,
            source_kinds = segments.Select(x => x.Declaration.SourceKind).Distinct(StringComparer.Ordinal).Order(StringComparer.Ordinal).ToArray()
        };
        string path = Path.Combine(directory, "source-close-receipt.json");
        string temporary = path + ".tmp";
        WriteNew(temporary, SourceSessionJson.Bytes(receipt)); File.Move(temporary, path);
    }

    internal void WriteCoverage() => File.WriteAllBytes(Path.Combine(directory, "source-coverage.json"),
        SourceSessionJson.Bytes(new { schema = "sts2.annotator/source-coverage-1", profile.ScopeId,
            profile.SeamCoverage, status = Status, non_claims = SourceSessionContract.NonClaims }));

    private bool IsPaused(ulong index)
    {
        SourceClockReference? start = null;
        foreach (SourceBoundary boundary in boundaries)
        {
            if (boundary.Kind == "pause") start = boundary.Clock;
            else if (boundary.Kind is "resume" or "close" && start != null)
            {
                if (index > SourceSessionContract.Index(start) && index <= SourceSessionContract.Index(boundary.Clock)) return true;
                start = null;
            }
        }
        return start != null && index > SourceSessionContract.Index(start);
    }

    private void Join(PublicCaptureReference? capture, PublicCatalogReference? catalog)
    {
        if (capture != null && (!captures.TryGetValue(capture.CaptureId, out var known) || known != capture))
            throw new InvalidDataException("source_capture_not_owned");
        if (catalog != null && (!catalogs.TryGetValue(catalog.CatalogRef, out var knownCatalog) || knownCatalog != catalog))
            throw new InvalidDataException("source_catalog_not_owned");
        if (catalog == null) return;
        if (capture == null || capture.SnapshotId != catalog.SnapshotId || capture.ScopeId != catalog.ScopeId
            || capture.StreamGeneration != catalog.StreamGeneration)
            throw new InvalidDataException("source_capture_catalog_join_invalid");
        VerifyBlob(capture.PayloadRef, capture.Sha256, capture.ByteCount);
        using JsonDocument json = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(directory, capture.PayloadRef)));
        JsonElement descriptor = json.RootElement.GetProperty("catalog");
        if (descriptor.GetProperty("catalog_ref").GetString() != catalog.CatalogRef
            || descriptor.GetProperty("snapshot_id").GetString() != catalog.SnapshotId
            || descriptor.GetProperty("scope_id").GetString() != catalog.ScopeId
            || descriptor.GetProperty("stream_generation").GetString() != catalog.StreamGeneration
            || descriptor.GetProperty("status").GetString() != "complete"
            || descriptor.GetProperty("total_count").GetInt32() != catalog.TotalCount
            || descriptor.GetProperty("digest").GetString() != catalog.StructuralDigest)
            throw new InvalidDataException("source_capture_catalog_descriptor_mismatch");
    }

    private void Identity(string id, string snapshot, string scope, string generation)
    {
        SourceSessionContract.Identifier(id); SourceSessionContract.Identifier(snapshot);
        if (scope != profile.ScopeId || generation != boundaryClock.StreamGeneration)
            throw new InvalidDataException("source_payload_scope_or_generation_mismatch");
    }
    private void CheckClock(SourceClockReference clock, bool monotonicBoundary = false)
    {
        ulong index = SourceSessionContract.Index(clock);
        if (clock.StreamGeneration != boundaryClock.StreamGeneration
            || index < SourceSessionContract.Index(segments[0].BoundaryClock)
            || monotonicBoundary && (index < SourceSessionContract.Index(boundaryClock) || index < lastObservationIndex))
            throw new InvalidDataException("source_clock_generation_or_order_mismatch");
    }
    private void CheckBytes(byte[] bytes, string hash)
    {
        if (bytes.Length == 0 || bytes.Length > profile.Limits.MaxCaptureBytes)
            Fail<object>("source_capture_byte_capacity");
        if (!SourceSessionContract.IsSha256(hash) || SourceSessionContract.Sha256(bytes) != hash)
            throw new InvalidDataException("source_payload_hash_mismatch");
    }
    private void PersistBytes(string relative, byte[] bytes)
    {
        string path = Path.Combine(directory, relative);
        if (payloadPaths.Contains(relative))
        { VerifyBlob(relative, SourceSessionContract.Sha256(bytes), bytes.LongLength); return; }
        if (bytes.LongLength > profile.Limits.MaxPayloadBytes - payloadBytes)
            Fail<object>("source_payload_total_capacity");
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        WriteNew(path, bytes);
        payloadPaths.Add(relative); payloadBytes += bytes.LongLength;
    }
    private void VerifyBlob(string relative, string hash, long length)
    {
        string path = Path.Combine(directory, relative);
        if (!File.Exists(path) || new FileInfo(path).Length != length
            || SourceSessionContract.Sha256(File.ReadAllBytes(path)) != hash)
            throw new InvalidDataException("source_blob_missing_or_changed");
    }
    private void Append<T>(string file, T value)
    {
        Healthy();
        byte[] bytes = SourceSessionJson.Bytes(value);
        FileStream stream = streams[file];
        if (bytes.Length > profile.Limits.MaxRowBytes || counts[file] >= profile.Limits.MaxRowsPerStream
            || bytes.Length > profile.Limits.MaxBytesPerStream - stream.Length)
            Fail<object>("source_stream_capacity");
        try { stream.Write(bytes); stream.Flush(); counts[file]++; }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        { Fail<object>("source_disk_append_uncertain"); }
    }
    internal void MarkFailure(string code)
    {
        if (failed) return;
        failed = true; error = code;
        // One bounded accounting object, not an unbounded retry/gap ledger.
        try
        {
            WriteNew(Path.Combine(directory, "source-accounting-failure.json"), SourceSessionJson.Bytes(new
            { schema = "sts2.annotator/source-accounting-failure-1", code, boundary_clock = boundaryClock, counts,
                accounting_complete = false }));
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException) { }
    }
    private T Fail<T>(string code) { MarkFailure(code); throw new IOException(code); }
    private void Healthy()
    {
        if (disposed) throw new ObjectDisposedException(nameof(SourceSessionStreams));
        if (failed) throw new IOException(error ?? "source_accounting_failed");
    }
    private static void WriteNew(string path, byte[] bytes)
    {
        using var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.Read);
        stream.Write(bytes); stream.Flush(true);
    }
    public void Dispose()
    {
        if (disposed) return;
        foreach (FileStream stream in streams.Values) stream.Dispose();
        disposed = true;
    }
}
