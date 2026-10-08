namespace STS2HumanAnnotator.Core;

/// <summary>Additional order supplement over the common strict Source raw audit.</summary>
internal static class SourceSessionOrderAuditV3
{
    internal static void Validate(IReadOnlyList<SourceAttachmentEpochV2> epochs,
        IReadOnlyList<SourceSegmentV2> segments, IReadOnlyList<SourceBoundaryV2> boundaries,
        IReadOnlyList<SourceNativeInputWitnessV2> inputs, IReadOnlyList<SourceFinalDrainV2> drains,
        string finalInputOrdinal)
    {
        static void Require(bool condition, string code)
        { if (!condition) throw new InvalidDataException(code); }
        static ulong Cut(string? value) => value == null ? throw new InvalidDataException("source_input_fence_missing")
            : SourceSessionContractV3.Ordinal(value);
        ulong final = Cut(finalInputOrdinal);
        Require(final == (ulong)inputs.Count, "source_input_ordinal_accounting_incomplete");
        var ordered = inputs.OrderBy(x => Cut(x.InputPrefixOrdinal)).ToArray();
        for (int i = 0; i < ordered.Length; i++)
        {
            var input = ordered[i]; ulong ordinal = Cut(input.InputPrefixOrdinal);
            Require(ordinal == (ulong)i + 1, "source_input_ordinal_not_contiguous");
            SourceSessionContractV3.Validate(new(input.InputPrefixOrdinal!, input.BasisOrder
                ?? throw new InvalidDataException("source_input_basis_order_missing")));
            int epochIndex = Enumerable.Range(0, epochs.Count).Single(x => epochs[x].EpochId == input.EpochId);
            ulong epochStart = Cut(epochs[epochIndex].AfterInputOrdinal);
            ulong epochEnd = Cut(boundaries[^1].SealedEpochs.Single(x => x.EpochId == input.EpochId).AfterInputOrdinal);
            Require(SourceSessionContractV2.Index(input.PrePosition) >= SourceSessionContractV2.Index(epochs[epochIndex].InitialPosition),
                "source_input_before_initial_reservation");
            Require(epochStart < ordinal && ordinal <= epochEnd, "source_input_epoch_fence_mismatch");
            int segmentIndex = Enumerable.Range(0, segments.Count).Single(x => segments[x].SegmentId == input.SegmentId);
            ulong segmentStart = Cut(segments[segmentIndex].AfterInputOrdinal);
            ulong segmentEnd = segmentIndex + 1 == segments.Count ? final : Cut(segments[segmentIndex + 1].AfterInputOrdinal);
            Require(segmentStart < ordinal && ordinal <= segmentEnd, "source_input_actor_fence_mismatch");
            if (i > 0)
            {
                var prior = ordered[i - 1]; int priorEpoch = Enumerable.Range(0, epochs.Count).Single(x => epochs[x].EpochId == prior.EpochId);
                Require(priorEpoch <= epochIndex && (priorEpoch != epochIndex
                    || SourceSessionContractV2.Index(prior.PrePosition) <= SourceSessionContractV2.Index(input.PrePosition)),
                    "source_input_native_prefix_order_regressed");
            }
            foreach (var boundary in boundaries)
            {
                int boundaryEpoch = Enumerable.Range(0, epochs.Count).Single(x => epochs[x].EpochId == boundary.Position.EpochId);
                int relation = epochIndex == boundaryEpoch ? SourceSessionContractV2.Index(input.PrePosition).CompareTo(SourceSessionContractV2.Index(boundary.Position))
                    : epochIndex.CompareTo(boundaryEpoch);
                ulong fence = Cut(boundary.AfterInputOrdinal);
                Require(relation == 0 || (relation < 0 ? ordinal <= fence : ordinal > fence), "source_input_boundary_fence_mismatch");
            }
            if (input.BasisOrder!.Status == SourceSessionContractV3.UnprovenBasis)
                Require(input.PreCapture == null && input.Catalog == null && input.Outcome.MappingStatus == "capture_missing"
                    && input.Outcome.SelectedAction == null && input.Outcome.MatchCount == 0, "source_unproven_input_has_capture");
        }
        Require(Cut(epochs[0].AfterInputOrdinal) == 0 && Cut(segments[0].AfterInputOrdinal) == 0,
            "source_initial_input_fence_invalid");
        ulong previous = 0;
        foreach (var segment in segments)
        { ulong cut = Cut(segment.AfterInputOrdinal); Require(previous <= cut && cut <= final, "source_actor_input_fence_invalid"); previous = cut; }
        previous = 0;
        foreach (var epoch in epochs)
        {
            ulong cut = Cut(epoch.AfterInputOrdinal); Require(previous <= cut && cut <= final, "source_epoch_input_fence_invalid");
            if (epoch.PredecessorSeal != null) Require(Cut(epoch.PredecessorSeal.AfterInputOrdinal) == cut, "source_epoch_predecessor_input_fence_mismatch");
            var seal = boundaries[^1].SealedEpochs.Single(x => x.EpochId == epoch.EpochId);
            Require(cut <= Cut(seal.AfterInputOrdinal) && Cut(seal.AfterInputOrdinal) <= final, "source_seal_input_fence_invalid");
            previous = cut;
        }
        previous = 0; ulong? pause = null;
        foreach (var boundary in boundaries)
        {
            ulong cut = Cut(boundary.AfterInputOrdinal);
            Require(previous <= cut && cut <= final, "source_boundary_input_fence_invalid");
            if (boundary.Kind == "pause") pause = cut;
            else if (pause != null)
            {
                Require(cut == pause, "source_input_admitted_while_paused");
                if (boundary.Kind is "resume" or "close") pause = null;
            }
            foreach (var interval in boundary.PausedIntervals)
                Require(Cut(interval.AfterInputOrdinal) == cut && Cut(interval.ThroughInputOrdinal) == cut,
                    "source_paused_input_fence_invalid");
            if (boundary.Kind == "epoch_transition")
                Require(Cut(epochs.Single(x => x.EpochId == boundary.Position.EpochId).AfterInputOrdinal) == cut,
                    "source_epoch_boundary_input_fence_mismatch");
            previous = cut;
        }
        Require(Cut(boundaries[^1].AfterInputOrdinal) == final
            && Cut(boundaries[^1].SealedEpochs.Single(x => x.EpochId == epochs[^1].EpochId).AfterInputOrdinal) == final,
            "source_final_input_fence_mismatch");
        foreach (var drain in drains)
            Require(Cut(drain.AfterInputOrdinal) == Cut(boundaries[^1].SealedEpochs.Single(x => x.EpochId == drain.EpochId).AfterInputOrdinal),
                "source_final_drain_input_fence_mismatch");
    }
}

/// <summary>Strict V3 entry point over the common Source audit engine; V2 still rejects V3.</summary>
public static class SourceSessionAuditV3
{
    public static SourceSessionAuditResult Audit(string recordingDirectory) => SourceSessionAuditV2.AuditVersion(recordingDirectory, 3);
}
