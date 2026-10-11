namespace STS2HumanAnnotator.Core;

public sealed record SourceEpochAdmissionV2(SourceAttachmentEpochV2 Epoch, SourceBoundaryV2? Boundary);

/// <summary>Short metadata admission gate. Every method is filesystem-free; disk writes use the store's separate gate.</summary>
internal sealed class SourceSessionEpochLedgerV2
{
    private sealed class Epoch(SourceAttachmentEpochV2 row)
    {
        internal readonly SourceAttachmentEpochV2 Row = row;
        internal SourceNativeSealV2? Seal;
        internal ulong Boundary = SourceSessionContractV2.Index(row.StartingPosition);
        internal ulong Durable = SourceSessionContractV2.Index(row.StartingPosition);
        internal ulong HighestKnown = SourceSessionContractV2.Index(row.StartingPosition);
        internal ulong? PausedAfter;
        internal ulong? PausedAfterInput;
    }
    internal sealed class Input(SourceInputTokenV2 token)
    {
        internal readonly SourceInputTokenV2 Token = token;
        internal PublicCaptureReferenceV2? Capture;
        internal PublicCatalogReferenceV2? Catalog;
        internal bool BasisBound;
        internal SourceBasisOrderV3? BasisOrder;
        internal string? TerminalSignature;
    }
    private readonly object metadataGate = new();
    private readonly string sessionId, timelineId;
    private readonly RecorderEnvironmentIdentity environment;
    private readonly SourceSessionLimitsV2 limits;
    private readonly SourceSessionWireFormat format;
    private ulong lastInputOrdinal;
    private readonly Dictionary<string, Epoch> epochs = new(StringComparer.Ordinal);
    private readonly List<SourceSegmentV2> segments = new();
    private readonly List<SourceBoundaryV2> boundaries = new();
    private readonly List<SourcePausedIntervalV2> paused = new();
    private readonly Dictionary<string, Input> inputs = new(StringComparer.Ordinal);
    private string currentEpoch = "";
    private bool isPaused, closing;
    private string? failure;
    private long observationCount, inputCount, gapCount;
    private int pendingInputs;

    internal SourceSessionEpochLedgerV2(string sessionId, string timelineId, RecorderEnvironmentIdentity environment,
        SourceSessionLimitsV2 limits, SourceSessionWireFormat format)
    {
        SourceSessionContract.Identifier(sessionId); SourceSessionContract.Identifier(timelineId);
        SourceSessionContract.ValidateEnvironment(environment);
        this.sessionId = sessionId; this.timelineId = timelineId; this.environment = environment; this.limits = limits; this.format = format;
    }
    internal SourceEpochAdmissionV2 StageEpoch(SourceEpochPacketV2 packet)
    {
        lock (metadataGate)
        {
            Healthy(); if (closing) throw new InvalidOperationException("source_closing");
            SourceSessionContract.Identifier(packet.EpochId);
            if (epochs.ContainsKey(packet.EpochId)) throw new InvalidDataException("source_epoch_id_conflict");
            if (epochs.Count >= limits.MaxEpochs) Fail("source_epoch_capacity");
            ValidateContext(packet.Context);
            ulong start = Position(packet.StartingPosition, packet.EpochId, packet.Context.StreamGeneration);
            ulong initial = Position(packet.InitialPosition, packet.EpochId, packet.Context.StreamGeneration);
            if (start == ulong.MaxValue || initial != start + 1)
                throw new InvalidDataException("source_initial_reservation_invalid");
            SourceBoundaryV2? boundary = null;
            if (epochs.Count == 0)
            {
                if (packet.PreviousEpochId != null || packet.PredecessorSeal != null || packet.Transition != null)
                    throw new InvalidDataException("source_initial_epoch_invalid");
            }
            else
            {
                var previous = epochs[currentEpoch];
                if (packet.PreviousEpochId != currentEpoch || packet.PredecessorSeal == null || packet.Transition == null
                    || packet.Context.StreamGeneration == previous.Row.Context.StreamGeneration)
                    throw new InvalidDataException("source_epoch_chain_invalid");
                SourceSessionContractV2.Validate(packet.Transition);
                if (packet.Transition.Kind is not ("setup_handoff" or "cleanup")
                    || packet.Transition.PreviousGameContinuityId == packet.Transition.GameContinuityId
                    || packet.Transition.PreviousGameContinuityId != previous.Row.Context.GameContinuityId
                    || packet.Transition.GameContinuityId != packet.Context.GameContinuityId)
                    throw new InvalidDataException("source_epoch_transition_invalid");
                packet = packet with { PredecessorSeal = StampSeal(previous, packet.PredecessorSeal) };
                ValidateSeal(previous, packet.PredecessorSeal);
                previous.Seal = packet.PredecessorSeal;
                var intervals = ClosePausedEpoch(previous);
                boundary = NewBoundary("epoch_transition", packet.StartingPosition,
                    new[] { packet.PredecessorSeal }, intervals, packet.Transition);
            }
            var context = packet.Context with
            {
                EagerScope = Array.AsReadOnly(packet.Context.EagerScope.ToArray()),
                SeamCoverage = new System.Collections.ObjectModel.ReadOnlyDictionary<string, SourceSeamCoverage>(
                    new Dictionary<string, SourceSeamCoverage>(packet.Context.SeamCoverage, StringComparer.Ordinal))
            };
            var row = new SourceAttachmentEpochV2(format.Schema("source-attachment-epoch"), epochs.Count + 1,
                sessionId, timelineId, packet.EpochId, packet.PreviousEpochId, context, packet.StartingPosition,
                packet.InitialPosition, packet.PredecessorSeal, packet.Transition, DateTimeOffset.UtcNow)
            { AfterInputOrdinal = format.Fence(lastInputOrdinal) };
            var epoch = new Epoch(row); if (isPaused) { epoch.PausedAfter = start; epoch.PausedAfterInput = lastInputOrdinal; }
            epochs.Add(row.EpochId, epoch); currentEpoch = row.EpochId;
            return new(row, boundary);
        }
    }
    internal SourceSegmentV2 StageSource(SourceDeclaration declaration, SourceNativePositionV2 position,
        string? expectedSegment = null)
    {
        lock (metadataGate)
        {
            Healthy(); SourceSessionContract.Validate(declaration); var epoch = CheckCurrentBoundary(position);
            if (segments.Count != 0 && (!isPaused || closing)) throw new InvalidOperationException("source_change_requires_pause");
            if (segments.Count != 0 && expectedSegment != segments[^1].SegmentId)
                throw new InvalidOperationException("source_segment_changed");
            if (segments.Count >= limits.MaxSegments) Fail("source_segment_capacity");
            var row = new SourceSegmentV2(format.Schema("source-segment"), segments.Count + 1,
                sessionId, timelineId, "source-segment-" + Guid.NewGuid().ToString("N"),
                segments.Count == 0 ? null : segments[^1].SegmentId, declaration, position, DateTimeOffset.UtcNow) { AfterInputOrdinal = format.Fence(lastInputOrdinal) };
            segments.Add(row); epoch.Boundary = SourceSessionContractV2.Index(position); return row;
        }
    }
    internal SourceBoundaryV2 StageBoundary(string kind, SourceNativePositionV2 position,
        IReadOnlyList<SourceNativeSealV2>? seals = null, SourceNativeTransitionV2? transition = null)
    {
        lock (metadataGate)
        {
            Healthy(); var epoch = CheckCurrentBoundary(position);
            if (segments.Count == 0) throw new InvalidOperationException("source_segment_required");
            var closedIntervals = new List<SourcePausedIntervalV2>();
            if (kind == "pause" && !isPaused && !closing)
            { isPaused = true; epoch.PausedAfter = SourceSessionContractV2.Index(position); epoch.PausedAfterInput = lastInputOrdinal; }
            else if (kind is "resume" or "close" && !closing && (kind == "close" || isPaused))
            {
                if (seals != null)
                    foreach (var seal in seals) { var old = EpochFor(seal.EpochId, seal.StreamGeneration); var original = StampSeal(old, seal); ValidateSeal(old, original); old.Seal = original; }
                foreach (var old in epochs.Values)
                    if (old.PausedAfter != null)
                        closedIntervals.AddRange(ClosePausedEpoch(old, old.Row.EpochId == currentEpoch
                            ? SourceSessionContractV2.Index(position) : null));
                isPaused = false; if (kind == "close") closing = true;
            }
            else if (kind is "launch" or "terminal" && transition != null && !closing)
            {
                SourceSessionContractV2.Validate(transition);
                if (transition.Kind != kind || transition.PreviousGameContinuityId != epoch.Row.Context.GameContinuityId
                    || transition.GameContinuityId != epoch.Row.Context.GameContinuityId)
                    throw new InvalidDataException("source_native_boundary_continuity_invalid");
            }
            else throw new InvalidOperationException("source_boundary_transition_invalid");
            epoch.Boundary = SourceSessionContractV2.Index(position);
            var originalSeals = kind == "close" ? epochs.Values.OrderBy(x => x.Row.Sequence)
                .Select(x => x.Seal ?? throw new InvalidDataException("source_final_epoch_unsealed")).ToArray()
                : seals ?? Array.Empty<SourceNativeSealV2>();
            return NewBoundary(kind, position, originalSeals, closedIntervals, transition);
        }
    }
    private SourceBoundaryV2 NewBoundary(string kind, SourceNativePositionV2 position,
        IReadOnlyList<SourceNativeSealV2> seals, IReadOnlyList<SourcePausedIntervalV2> intervals,
        SourceNativeTransitionV2? transition)
    {
        if (boundaries.Count >= limits.MaxRowsPerStream) Fail("source_boundary_capacity");
        var row = new SourceBoundaryV2(format.Schema("source-boundary"), boundaries.Count + 1,
            sessionId, timelineId, kind, segments[^1].SegmentId, position,
            Array.AsReadOnly(seals.ToArray()), Array.AsReadOnly(intervals.ToArray()), transition, DateTimeOffset.UtcNow)
        { AfterInputOrdinal = format.Fence(lastInputOrdinal) };
        boundaries.Add(row); return row;
    }
    private IReadOnlyList<SourcePausedIntervalV2> ClosePausedEpoch(Epoch epoch, ulong? through = null)
    {
        if (epoch.PausedAfter is not { } after) return Array.Empty<SourcePausedIntervalV2>();
        ulong end = through ?? (epoch.Seal != null ? SealIndex(epoch.Seal.ReservedThrough) :
            throw new InvalidDataException("source_paused_epoch_unsealed"));
        if (end < after) throw new InvalidDataException("source_paused_interval_invalid");
        var row = new SourcePausedIntervalV2(epoch.Row.EpochId, epoch.Row.Context.StreamGeneration,
            after.ToString(System.Globalization.CultureInfo.InvariantCulture), end.ToString(System.Globalization.CultureInfo.InvariantCulture))
        { AfterInputOrdinal = format.Fence(epoch.PausedAfterInput ?? lastInputOrdinal), ThroughInputOrdinal = format.Fence(lastInputOrdinal) };
        epoch.PausedAfter = null; epoch.PausedAfterInput = null; paused.Add(row); if (end > after) gapCount++; return new[] { row };
    }
    internal SourceInputTokenV2 ReserveInput(string inputId, SourceNativePositionV2 pre)
    {
        lock (metadataGate)
        {
            Healthy(); SourceSessionContract.Identifier(inputId);
            if (isPaused || closing || segments.Count == 0) throw new InvalidOperationException("source_input_not_recording");
            var epoch = CheckCurrentBoundary(pre);
            epoch.HighestKnown = Math.Max(epoch.HighestKnown, SourceSessionContractV2.Index(pre));
            if (inputs.ContainsKey(inputId)) throw new InvalidDataException("source_input_id_conflict");
            if (pendingInputs >= limits.MaxPendingInputs
                || inputs.Count >= limits.MaxRowsPerStream) Fail("source_input_capacity");
            if (format.Ordered && lastInputOrdinal == ulong.MaxValue) Fail("source_input_ordinal_capacity");
            var token = new SourceInputTokenV2(this, inputId, sessionId, timelineId, segments[^1].SegmentId, pre,
                format.Fence(format.Ordered ? ++lastInputOrdinal : 0));
            inputs.Add(inputId, new(token)); pendingInputs++; return token;
        }
    }
    internal Input ExactInput(SourceInputTokenV2 token)
    {
        lock (metadataGate)
        {
            Healthy();
            if (!ReferenceEquals(token.Owner, this) || !inputs.TryGetValue(token.InputId, out var input)
                || !ReferenceEquals(input.Token, token) || token.SessionId != sessionId || token.TimelineId != timelineId)
                throw new InvalidDataException("source_input_token_mismatch");
            return input;
        }
    }
    internal void SetInputOrder(SourceInputTokenV2 token, SourceBasisOrderV3 order)
    {
        lock (metadataGate)
        {
            var input = ExactInput(token);
            if (!format.Ordered) return;
            SourceSessionContractV3.Validate(new(token.InputPrefixOrdinal!, order));
            if (input.BasisOrder != null && input.BasisOrder != order) throw new InvalidDataException("source_input_order_changed");
            input.BasisOrder = order;
        }
    }
    internal void BindInput(SourceInputTokenV2 token, PublicCaptureReferenceV2? capture, PublicCatalogReferenceV2? catalog)
    {
        lock (metadataGate)
        {
            var input = ExactInput(token);
            if (input.BasisBound && (input.Capture != capture || input.Catalog != catalog))
                throw new InvalidDataException("source_input_basis_changed");
            if (format.Ordered && input.BasisOrder?.Status != SourceSessionContractV3.FrozenBasis && (capture != null || catalog != null))
                throw new InvalidDataException("source_unproven_input_has_capture");
            input.Capture = capture; input.Catalog = catalog; input.BasisBound = true;
        }
    }
    internal bool CompleteInput(SourceInputTokenV2 token, string signature)
    {
        lock (metadataGate)
        {
            var input = ExactInput(token);
            if (input.TerminalSignature != null)
            {
                if (input.TerminalSignature != signature) throw new InvalidDataException("source_input_completion_conflict");
                return false;
            }
            input.TerminalSignature = signature; inputCount++; pendingInputs--; return true;
        }
    }
    internal IReadOnlyList<SourceInputTokenV2> PendingInputs
    { get { lock (metadataGate) return inputs.Values.Where(x => x.TerminalSignature == null).Select(x => x.Token).ToArray(); } }
    internal string SegmentFor(SourceNativePositionV2 position)
    {
        lock (metadataGate)
        {
            var epoch = EpochFor(position.EpochId, position.StreamGeneration); ulong index = SourceSessionContractV2.Index(position);
            if (index <= SourceSessionContractV2.Index(epoch.Row.StartingPosition))
                throw new InvalidDataException("source_observation_before_attach");
            return segments.LastOrDefault(x => epochs[x.BoundaryPosition.EpochId].Row.Sequence < epoch.Row.Sequence
                || x.BoundaryPosition.EpochId == position.EpochId && SourceSessionContractV2.Index(x.BoundaryPosition) < index)?.SegmentId
                ?? throw new InvalidDataException("source_observation_segment_missing");
        }
    }
    internal void ValidateOriginalRange(SourceNativePositionV2 position)
    {
        lock (metadataGate)
        {
            var epoch = EpochFor(position.EpochId, position.StreamGeneration);
            if (epoch.Seal != null && SourceSessionContractV2.Index(position) > SealIndex(epoch.Seal.ReservedThrough))
                throw new InvalidDataException("source_original_epoch_range_exceeded");
        }
    }
    internal bool IsPaused(SourceNativePositionV2 position)
    {
        lock (metadataGate)
        {
            var epoch = EpochFor(position.EpochId, position.StreamGeneration); ulong index = SourceSessionContractV2.Index(position);
            return epoch.PausedAfter is { } after && index > after || paused.Any(x => x.EpochId == position.EpochId
                && index > SealIndex(x.AfterIndex) && index <= SealIndex(x.ThroughIndex));
        }
    }
    internal SourceAttachmentEpochV2 EpochRow(string id, string generation)
    { lock (metadataGate) return EpochFor(id, generation).Row; }
    internal IReadOnlyList<(ulong After, ulong Through)> UnpausedGapPieces(SourceNativePositionV2 position, ulong after)
    {
        lock (metadataGate)
        {
            var epoch = EpochFor(position.EpochId, position.StreamGeneration); ulong through = SourceSessionContractV2.Index(position);
            if (after >= through) throw new InvalidDataException("source_gap_interval_invalid");
            var intervals = paused.Where(x => x.EpochId == position.EpochId)
                .Select(x => (After: SealIndex(x.AfterIndex), Through: SealIndex(x.ThroughIndex))).ToList();
            if (epoch.PausedAfter is { } open) intervals.Add((open, through));
            ulong cursor = after + 1; var result = new List<(ulong After, ulong Through)>();
            foreach (var interval in intervals.OrderBy(x => x.After))
            {
                if (interval.After == ulong.MaxValue) continue;
                ulong start = Math.Max(cursor, interval.After + 1), end = Math.Min(through, interval.Through);
                if (end < start) continue;
                if (start > cursor) result.Add((cursor - 1, start - 1));
                if (end == ulong.MaxValue) return result.AsReadOnly();
                cursor = end + 1;
            }
            if (cursor <= through) result.Add((cursor - 1, through));
            return result.AsReadOnly();
        }
    }
    internal void RecordDurable(SourceNativePositionV2 position, string? gapAfterIndex = null, bool observation = true, bool missing = false)
    {
        lock (metadataGate)
        {
            Healthy(); var epoch = EpochFor(position.EpochId, position.StreamGeneration);
            ulong index = SourceSessionContractV2.Index(position);
            ValidateOriginalRange(position);
            epoch.HighestKnown = Math.Max(epoch.HighestKnown, index);
            if (index <= epoch.Durable) return;
            ulong after = gapAfterIndex == null ? index - 1 : SealIndex(gapAfterIndex);
            if (after > epoch.Durable && !PausedCovers(epoch, epoch.Durable, after))
                throw new InvalidDataException("source_durable_prefix_gap_unaccounted");
            if (observation && (missing || gapAfterIndex != null)) gapCount++;
            epoch.Durable = index; if (observation) observationCount++;
        }
    }
    private bool PausedCovers(Epoch epoch, ulong after, ulong through) => through <= after
        || paused.Any(x => x.EpochId == epoch.Row.EpochId && SealIndex(x.AfterIndex) <= after && SealIndex(x.ThroughIndex) >= through)
        || epoch.PausedAfter is { } open && open <= after;
    internal IReadOnlyList<SourceFinalDrainV2> FinalDrains(IReadOnlyList<SourceNativeSealV2> completed)
    {
        lock (metadataGate)
        {
            Healthy(); if (!closing) throw new InvalidOperationException("source_close_boundary_required");
            var result = new List<SourceFinalDrainV2>();
            foreach (var epoch in epochs.Values.OrderBy(x => x.Row.Sequence))
            {
                var seal = epoch.Seal ?? throw new InvalidDataException("source_final_epoch_unsealed");
                var final = completed.SingleOrDefault(x => x.EpochId == epoch.Row.EpochId && x.StreamGeneration == epoch.Row.Context.StreamGeneration)
                    ?? throw new InvalidDataException("source_final_drain_missing");
                ValidateSeal(epoch, final);
                ulong reserved = SealIndex(seal.ReservedThrough), high = SealIndex(final.CompletedThrough);
                if (final.ReservedThrough != seal.ReservedThrough || high != reserved || epoch.PausedAfter != null)
                    throw new InvalidDataException("source_final_drain_incomplete");
                if (epoch.Durable < reserved && PausedCovers(epoch, epoch.Durable, reserved)) epoch.Durable = reserved;
                bool terminal = inputs.Values.Where(x => x.Token.PrePosition.EpochId == epoch.Row.EpochId).All(x => x.TerminalSignature != null);
                if (epoch.Durable != reserved || !terminal) throw new InvalidDataException("source_final_drain_incomplete");
                result.Add(new(epoch.Row.EpochId, epoch.Row.Context.StreamGeneration, seal.ReservedThrough,
                    final.CompletedThrough, epoch.Durable.ToString(System.Globalization.CultureInfo.InvariantCulture), terminal)
                { AfterInputOrdinal = seal.AfterInputOrdinal });
            }
            if (completed.Count != result.Count) throw new InvalidDataException("source_final_drain_count_invalid");
            return result.AsReadOnly();
        }
    }
    internal string? FinalInputPrefixOrdinal { get { lock (metadataGate) return format.Fence(lastInputOrdinal); } }
    private SourceNativeSealV2 StampSeal(Epoch epoch, SourceNativeSealV2 seal) => seal with
    { AfterInputOrdinal = epoch.Seal?.AfterInputOrdinal ?? format.Fence(lastInputOrdinal) };
    internal void AssertIssuedBoundary(SourceBoundaryV2 row)
    {
        lock (metadataGate)
            if (row.Sequence < 1 || row.Sequence > boundaries.Count || !ReferenceEquals(boundaries[(int)row.Sequence - 1], row))
                throw new InvalidDataException("source_boundary_not_issued");
    }
    internal void AssertIssuedSegment(SourceSegmentV2 row)
    {
        lock (metadataGate)
            if (row.Sequence < 1 || row.Sequence > segments.Count || !ReferenceEquals(segments[(int)row.Sequence - 1], row))
                throw new InvalidDataException("source_segment_not_issued");
    }
    internal SourceSessionStatusV2 Status
    {
        get { lock (metadataGate) return new(currentEpoch, segments.Count == 0 ? "" : segments[^1].SegmentId,
            segments.Count == 0 ? new("unknown", "unassigned", "unassigned") : segments[^1].Declaration,
            observationCount, inputCount, pendingInputs, epochs.Count, gapCount, failure == null, failure); }
    }
    internal void MarkFailure(string code) { lock (metadataGate) failure ??= code; }
    private void Fail(string code) { failure ??= code; throw new InvalidDataException(code); }
    private void Healthy() { if (failure != null) throw new InvalidOperationException(failure); }
    private Epoch EpochFor(string id, string generation) => epochs.TryGetValue(id, out var epoch)
        && epoch.Row.Context.StreamGeneration == generation ? epoch : throw new InvalidDataException("source_epoch_generation_mismatch");
    private Epoch CheckCurrentBoundary(SourceNativePositionV2 position)
    {
        var epoch = EpochFor(position.EpochId, position.StreamGeneration); ulong index = SourceSessionContractV2.Index(position);
        if (position.EpochId != currentEpoch || index < Math.Max(epoch.Boundary, epoch.HighestKnown)
            || epoch.Seal != null && index > SealIndex(epoch.Seal.ReservedThrough))
            throw new InvalidDataException("source_native_boundary_order_invalid");
        return epoch;
    }
    private static ulong Position(SourceNativePositionV2 position, string id, string generation) =>
        position.EpochId == id && position.StreamGeneration == generation ? SourceSessionContractV2.Index(position)
            : throw new InvalidDataException("source_epoch_position_mismatch");
    private static ulong SealIndex(string value) => SourceSessionContract.Index(new("generation", value));
    private static void ValidateSeal(Epoch epoch, SourceNativeSealV2 seal)
    {
        if (seal.EpochId != epoch.Row.EpochId || seal.StreamGeneration != epoch.Row.Context.StreamGeneration
            || SealIndex(seal.CompletedThrough) > SealIndex(seal.ReservedThrough)
            || SealIndex(seal.ReservedThrough) < Math.Max(epoch.Boundary, epoch.HighestKnown)
            || SealIndex(seal.ReservedThrough) < epoch.Durable
            || epoch.Seal != null && epoch.Seal.ReservedThrough != seal.ReservedThrough)
            throw new InvalidDataException("source_epoch_seal_invalid");
    }
    private void ValidateContext(SourceEpochContextV2 context)
    {
        SourceSessionContract.Identifier(context.ScopeId); SourceSessionContract.Identifier(context.StreamGeneration);
        if (context.GameContinuityId != null) SourceSessionContract.Identifier(context.GameContinuityId);
        if (context.PublicationProfileId != SourceSessionContractV2.PublicationProfileId
            || context.PublicationProfileDefinitionSha256 != SourceSessionContractV2.PublicationProfileDefinitionSha256
            || !context.EagerScope.SequenceEqual(new[] { "persistent", "interaction", "referents", "catalog" })
            || context.Environment != environment || context.SeamCoverage.Count > 256)
            throw new InvalidDataException("source_epoch_context_invalid");
        foreach (var (seam, coverage) in context.SeamCoverage)
        {
            SourceSessionContract.Identifier(seam); SourceSessionContract.Identifier(coverage.Version);
            if (coverage.Coverage is not ("complete_at_seam" or "sampled" or "unsupported"))
                throw new InvalidDataException("source_seam_coverage_invalid");
        }
    }
}
