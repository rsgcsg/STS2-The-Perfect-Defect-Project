namespace STS2HumanAnnotator.Core;

/// <summary>Recording UI/lifecycle facts only; never an action or successor authority.</summary>
public sealed record ContinuousRecordingStatus(
    bool Armed, bool NativeStartObserved, bool Resumed, bool Interrupted,
    string Outcome, bool TerminalObserved, bool BoundaryComplete,
    int SealedSegments, int FinishedRuns, string SealState);

public sealed class ContinuousRecording
{
    private bool _nativeStart, _resumed, _interrupted, _terminal, _sealed;
    private string _outcome = "unknown";
    private string _sealState = "not_open";
    private string? _captureProfileId, _p1NativeOrigin;
    public bool Armed { get; private set; }
    public int SealedSegments { get; private set; }
    public int FinishedRuns { get; private set; }

    public void Arm(string? captureProfileId = null, string? p1NativeOrigin = null)
    {
        _captureProfileId = captureProfileId;
        _p1NativeOrigin = p1NativeOrigin;
        Armed = true;
    }
    public void Disarm() => Armed = false;

    // A new session changes identity, not the operator's selected recording
    // profile or origin. StartSession remains the configuration validator.
    public RecordingCommand? NextSessionCommand(string commandId) => !Armed ? null : new(
        commandId, RecordingCommandKind.StartNewSession,
        CaptureProfileId: _captureProfileId) { P1NativeOrigin = _p1NativeOrigin };

    public void BeginSession()
    {
        _nativeStart = _resumed = _interrupted = _terminal = _sealed = false;
        _outcome = "unknown";
        _sealState = "recording";
    }

    public void ObserveLaunch(string journalKind)
    {
        // Saved Launch is not fresh start evidence. An unknown setup cannot upgrade it.
        _nativeStart |= journalKind == "run_started_native";
        _resumed |= journalKind == "run_resumed_native";
    }

    public void Pause()
    {
        _interrupted = true;
        Disarm();
    }

    public bool ObserveTerminal(bool victory, bool abandoned)
    {
        if (_terminal || _sealed) return false;
        _terminal = true;
        _outcome = abandoned ? "abandoned" : victory ? "victory" : "defeat";
        return true;
    }

    public void RequestSeal() => _sealState = "sealing";

    public void MarkSealed()
    {
        if (_sealed) return;
        _sealed = true;
        _sealState = "sealed";
        SealedSegments++;
        if (_terminal) FinishedRuns++;
    }

    public ContinuousRecordingStatus Snapshot() => new(
        Armed, _nativeStart, _resumed, _interrupted, _outcome, _terminal,
        _nativeStart && !_resumed && !_interrupted && _terminal,
        SealedSegments, FinishedRuns, _sealState);
}
