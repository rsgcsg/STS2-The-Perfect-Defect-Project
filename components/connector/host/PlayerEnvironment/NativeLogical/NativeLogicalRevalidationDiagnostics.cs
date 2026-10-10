using System;
using System.Linq;
using System.Text;
using System.Text.Json;

namespace STS2Connector.PlayerEnvironment.NativeLogical;

internal enum NativeLogicalRejectionGate
{
    SnapshotMismatch, BasisMissing, PublicFactsChanged, SourceIncomplete,
    ActionUnknown, NativeLeafMissing, RevalidationException
}

// Process-private, opt-in and non-authorizing. Only bounded opaque attribution
// and closed comparison groups leave this class; never frame values or keys.
internal sealed class NativeLogicalRevalidationDiagnostics
{
    internal const string EnvironmentVariable = "STS2_CONNECTOR_NATIVE_REVALIDATION_DIAGNOSTICS";
    internal const string Schema = "sts2.connector/native-revalidation-diagnostic-1";
    internal const int ProcessLimit = 200;
    internal const int IdentifierByteLimit = 256;
    private readonly object gate = new();
    private readonly Action<string> sink;
    private readonly bool configured;
    private int emitted;
    private bool stopped;

    internal NativeLogicalRevalidationDiagnostics(bool enabled, Action<string> sink)
    { configured = enabled; this.sink = sink; }
    internal bool Enabled { get { lock (gate) return configured && !stopped; } }
    internal void Reject(string? requestId, string expectedSnapshot, string actionId,
        string? projectedSnapshot, NativeLogicalRejectionGate rejection,
        NativeLogicalPublicFrame? basis = null, NativeLogicalPublicFrame? current = null)
    {
        lock (gate)
        {
            if (!configured || stopped) return;
            try
            {
                if (emitted == ProcessLimit)
                {
                    stopped = true;
                    sink(JsonSerializer.Serialize(new { schema = Schema, event_kind = "budget_exhausted", record_limit = ProcessLimit }));
                    return;
                }
                NativeLogicalFactChange changes = basis is not null && current is not null
                    ? NativeLogicalFactComparison.Compare(basis, current, collectAll: true) : NativeLogicalFactChange.None;
                var groups = Enum.GetValues<NativeLogicalFactChange>().Where(g => g != NativeLogicalFactChange.None && changes.HasFlag(g))
                    .Select(g => JsonNamingPolicy.SnakeCaseLower.ConvertName(g.ToString())).ToArray();
                sink(JsonSerializer.Serialize(new
                {
                    schema = Schema, event_kind = "rejected", sequence = ++emitted,
                    request_id = Bound(requestId), expected_snapshot_id = Bound(expectedSnapshot),
                    action_id = Bound(actionId), projected_snapshot_id = Bound(projectedSnapshot),
                    attribution_omitted = TooLong(requestId) || TooLong(expectedSnapshot) || TooLong(actionId) || TooLong(projectedSnapshot),
                    rejection_gate = JsonNamingPolicy.SnakeCaseLower.ConvertName(rejection.ToString()),
                    changed_fact_groups = groups
                }));
            }
            catch { stopped = true; /* Diagnostics cannot alter revalidation or dispatch. */ }
        }
    }
    private static bool TooLong(string? value) => value is not null && Encoding.UTF8.GetByteCount(value) > IdentifierByteLimit;
    private static string? Bound(string? value) => TooLong(value) ? null : value;
}
