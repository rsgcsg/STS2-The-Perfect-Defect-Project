namespace STS2HumanAnnotator.Core;

/// <summary>
/// Coordinates recording closure after native terminal observation. Native end
/// precedes the game-over decision owner becoming ready; only that exact ready
/// callback may release a normal terminal seal. This class proves no successor.
/// This gate coordinates closure, not successor proof.
/// </summary>
public sealed class TerminalAutoSeal
{
    private readonly object _gate = new();
    private string? _sessionId;
    private string? _runId;
    private bool _abandoned;

    public void ObserveNativeEnded(string sessionId, string runId, bool abandoned)
    {
        lock (_gate)
        {
            _sessionId = sessionId;
            _runId = runId;
            _abandoned = abandoned;
        }
    }

    /// <summary>
    /// Runs the recorder's exact boundary observation before allowing its
    /// automatic Close. A failed or empty observation still closes through the
    /// recorder's ordinary explicit-unknown disposition path.
    /// </summary>
    public void CompleteDecisionOwnerReady(
        string sessionId, string runId, string domain,
        Action observeBoundary, Action close)
    {
        try
        {
            observeBoundary();
        }
        finally
        {
            if (TakeOnDecisionOwnerReady(sessionId, runId, domain))
                close();
        }
    }

    public bool TakeOnProcessFrame(string? sessionId, string runId) =>
        Take(sessionId, runId, eligible: true, requireAbandoned: true);

    public bool TakeOnDecisionOwnerReady(string? sessionId, string runId, string domain) =>
        Take(sessionId, runId, eligible: domain == "game_over", requireAbandoned: false);

    // A later exact native launch is an exit from the preceding run's
    // terminal owner. Close the preceding segment as unknown if readiness
    // never arrived; it cannot be carried into the new run.
    public bool TakeOnNextNativeLaunch(string? sessionId, string runId) =>
        Take(sessionId, runId, eligible: true);

    private bool Take(string? sessionId, string runId, bool eligible, bool? requireAbandoned = null)
    {
        lock (_gate)
        {
            if (!eligible || _sessionId == null || _runId == null
                || requireAbandoned != null && _abandoned != requireAbandoned.Value
                || !string.Equals(_sessionId, sessionId, StringComparison.Ordinal)
                || !string.Equals(_runId, runId, StringComparison.Ordinal))
                return false;
            ResetUnsafe();
            return true;
        }
    }

    public void Reset()
    {
        lock (_gate) ResetUnsafe();
    }

    private void ResetUnsafe()
    {
        _sessionId = null;
        _runId = null;
        _abandoned = false;
    }
}
