namespace STS2HumanAnnotator.Core;

/// <summary>
/// Coordinates recording closure after native terminal observation. Native end
/// precedes the game-over decision owner becoming ready; only that exact ready
/// callback may release a normal terminal seal. This class proves no successor.
/// Calls are serialized by the recorder's lifecycle gate.
/// </summary>
public sealed class TerminalAutoSeal
{
    private string? _sessionId;
    private string? _runId;
    private bool _abandoned;

    public void ObserveNativeEnded(string sessionId, string runId, bool abandoned)
    {
        _sessionId = sessionId;
        _runId = runId;
        _abandoned = abandoned;
    }

    public bool TakeOnProcessFrame(string? sessionId, string runId) =>
        Take(sessionId, runId, _abandoned);

    public bool TakeOnDecisionOwnerReady(string? sessionId, string runId, string domain) =>
        Take(sessionId, runId, !_abandoned && domain == "game_over");

    // A later exact native launch is an exit from the preceding run's
    // terminal owner. Close the preceding segment as unknown if readiness
    // never arrived; it cannot be carried into the new run.
    public bool TakeOnNextNativeLaunch(string? sessionId, string runId) =>
        Take(sessionId, runId, eligible: true);

    private bool Take(string? sessionId, string runId, bool eligible)
    {
        if (!eligible || _sessionId == null || _runId == null
            || !string.Equals(_sessionId, sessionId, StringComparison.Ordinal)
            || !string.Equals(_runId, runId, StringComparison.Ordinal))
            return false;
        Reset();
        return true;
    }

    public void Reset()
    {
        _sessionId = null;
        _runId = null;
        _abandoned = false;
    }
}
