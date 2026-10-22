using System.Diagnostics;

namespace STS2HumanAnnotator.Core;

public sealed class RecordingPerformanceProfiler
{
    public const string ReportSchema = "sts2.human-annotator/recording-performance-profile-1";
    private const int MaximumSamplesPerPhase = 100_000;
    private readonly object _gate = new();
    private readonly Queue<RecordingPerformanceSlowSample> _slowSamples = new();
    [ThreadStatic] private static string? _activePhase;
    private readonly Dictionary<string, PhaseSamples> _phases = new(StringComparer.Ordinal);

    public T Measure<T>(string phase, Func<T> operation)
    {
        long started = Stopwatch.GetTimestamp();
        DateTimeOffset startedAt = DateTimeOffset.UtcNow;
        string? parent = _activePhase;
        _activePhase = phase;
        try
        {
            return operation();
        }
        finally
        {
            _activePhase = parent;
            long ended = Stopwatch.GetTimestamp();
            long microseconds = Microseconds(started, ended);
            ObserveMicroseconds(phase, microseconds);
            ObserveSlowInterval(phase, parent, startedAt, started, ended, microseconds);
        }
    }

    public void Measure(string phase, Action operation) =>
        Measure(
            phase,
            () =>
            {
                operation();
                return true;
            });

    public void ObserveMicroseconds(string phase, long microseconds)
    {
        if (string.IsNullOrWhiteSpace(phase))
            throw new ArgumentException("Performance phase must be non-empty.", nameof(phase));
        if (microseconds < 0)
            throw new ArgumentOutOfRangeException(nameof(microseconds));
        lock (_gate)
        {
            if (!_phases.TryGetValue(phase, out PhaseSamples? samples))
            {
                samples = new PhaseSamples();
                _phases.Add(phase, samples);
            }
            samples.Add(microseconds);
        }
    }

    // The caller owns both actual Stopwatch boundaries (e.g. process frames).
    // Deferred aggregate subphase observations never fabricate timed intervals.
    public void ObserveInterval(string phase, long startedTicks, long endedTicks)
    {
        if (startedTicks < 0 || endedTicks < startedTicks) throw new ArgumentOutOfRangeException(nameof(startedTicks));
        long microseconds = Microseconds(startedTicks, endedTicks);
        ObserveMicroseconds(phase, microseconds);
        ObserveSlowInterval(phase, _activePhase, null, startedTicks, endedTicks, microseconds);
    }

    private static long Microseconds(long startedTicks, long endedTicks) =>
        (long)Math.Ceiling((endedTicks - startedTicks) * 1_000_000d / Stopwatch.Frequency);

    private void ObserveSlowInterval(string phase, string? parent, DateTimeOffset? startedAt,
        long startedTicks, long endedTicks, long microseconds)
    {
        if (microseconds < 30_000) return;
        lock (_gate)
        {
            if (_slowSamples.Count == 256) _slowSamples.Dequeue();
            _slowSamples.Enqueue(new(phase, parent, startedAt, DateTimeOffset.UtcNow,
                startedTicks, endedTicks, microseconds, Environment.CurrentManagedThreadId));
        }
    }

    public RecordingPerformanceReport Snapshot(string sessionId)
    {
        lock (_gate)
        {
            return new RecordingPerformanceReport(
                ReportSchema,
                sessionId,
                DateTimeOffset.UtcNow,
                _phases
                    .OrderBy(pair => pair.Key, StringComparer.Ordinal)
                    .Select(pair => pair.Value.Summarize(pair.Key))
                    .ToArray(),
                new[]
                {
                    "Stopwatch timings are source/runtime diagnostics, not gameplay evidence.",
                    "A profile does not prove absence of game-frame stalls without an owner canary."
                }) { SlowSamples = _slowSamples.ToArray() };
        }
    }

    private sealed class PhaseSamples
    {
        private readonly List<long> _samples = new();
        private long _count;
        private long _total;
        private long _maximum;

        public void Add(long microseconds)
        {
            _count++;
            _total += microseconds;
            _maximum = Math.Max(_maximum, microseconds);
            if (_samples.Count < MaximumSamplesPerPhase)
                _samples.Add(microseconds);
        }

        public RecordingPerformancePhase Summarize(string phase)
        {
            long[] ordered = _samples.Order().ToArray();
            return new RecordingPerformancePhase(
                phase,
                _count,
                _samples.Count,
                _count - _samples.Count,
                _count == 0 ? 0 : _total / _count,
                Percentile(ordered, 0.50),
                Percentile(ordered, 0.95),
                Percentile(ordered, 0.99),
                _maximum,
                _total);
        }

        private static long Percentile(long[] ordered, double percentile)
        {
            if (ordered.Length == 0)
                return 0;
            int index = (int)Math.Ceiling(percentile * ordered.Length) - 1;
            return ordered[Math.Clamp(index, 0, ordered.Length - 1)];
        }
    }
}

public sealed record RecordingPerformancePhase(
    string Phase,
    long Count,
    int SampledCount,
    long DroppedSampleCount,
    long MeanUs,
    long P50Us,
    long P95Us,
    long P99Us,
    long MaxUs,
    long TotalUs);

public sealed record RecordingPerformanceReport(
    string Schema,
    string SessionId,
    DateTimeOffset GeneratedAt,
    IReadOnlyList<RecordingPerformancePhase> Phases,
    IReadOnlyList<string> NonClaims)
{
    public IReadOnlyList<RecordingPerformanceSlowSample> SlowSamples { get; init; } = Array.Empty<RecordingPerformanceSlowSample>();
}

public sealed record RecordingPerformanceSlowSample(string Phase, string? ParentPhase,
    DateTimeOffset? StartedAtUtc, DateTimeOffset ObservedAtUtc, long StartedAtStopwatchTicks,
    long EndedAtStopwatchTicks, long ElapsedUs, int ManagedThreadId);
