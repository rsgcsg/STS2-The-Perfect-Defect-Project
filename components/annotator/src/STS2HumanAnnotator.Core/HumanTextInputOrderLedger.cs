namespace STS2HumanAnnotator.Core;

/// <summary>Validate capture metadata against an append prefix, not game causality.
/// The caller owns contiguous append sequence and row validation. Rejected
/// evidence must never be retried through this ledger as a different row.</summary>
public sealed class HumanTextInputOrderLedger
{
    private readonly HashSet<(string Runtime, long Capture)> _seen = new();
    private readonly Dictionary<string, List<(long Sequence, long Maximum)>> _prefix = new();

    public string? Observe(HumanTextInputObservation row)
    {
        if (row.ObservationOrder?.CaptureOrdinal is not { } capture) return null;
        string runtime = row.Environment!.RuntimeInstanceId;
        if (!_seen.Add((runtime, capture))) return "human_text_input_capture_order_invalid";
        if (!_prefix.TryGetValue(runtime, out var entries))
        {
            entries = new();
            _prefix.Add(runtime, entries);
        }
        long watermark = row.ObservationOrder.CompletedAppendWatermark;
        // Binary search avoids rescanning a potentially long recording at each
        // input. Completion order can differ from observation order under nesting.
        int left = 0, right = entries.Count;
        while (left < right)
        {
            int middle = left + (right - left) / 2;
            if (entries[middle].Sequence <= watermark) left = middle + 1;
            else right = middle;
        }
        if (left > 0 && capture <= entries[left - 1].Maximum)
            return "human_text_input_capture_order_invalid";
        long previous = entries.Count == 0 ? 0 : entries[^1].Maximum;
        entries.Add((row.Sequence, Math.Max(capture, previous)));
        return null;
    }
}
