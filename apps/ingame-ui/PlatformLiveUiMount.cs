using System;

namespace STS2PlatformLiveUi;

/// <summary>One main-thread mount; cancellation also invalidates a queued callback.</summary>
internal sealed class PlatformLiveUiMount(
    Action<Action> schedule,
    Func<bool> treeAvailable,
    Action attach,
    Func<bool> attached,
    Action prepare,
    Action ready,
    Action<Exception> failed,
    Action cleanup)
{
    private enum Phase { New, Pending, Attaching, Ready, Cancelled, Failed }
    private Phase _phase;
    private bool _cleaned;

    internal bool IsReady => _phase == Phase.Ready;

    internal void Begin()
    {
        if (_phase != Phase.New)
            return;
        _phase = Phase.Pending;
        try { schedule(Mount); }
        catch (Exception exception) { Fail(exception); }
    }

    internal void Cancel()
    {
        if (_phase is Phase.Cancelled or Phase.Failed)
            return;
        _phase = Phase.Cancelled;
        Clean();
    }

    private void Mount()
    {
        if (_phase != Phase.Pending)
            return;
        try
        {
            if (!treeAvailable())
            {
                Cancel();
                return;
            }
            _phase = Phase.Attaching;
            attach();
            if (_phase != Phase.Attaching)
                return;
            if (!attached())
                throw new InvalidOperationException("Live UI nodes did not enter the expected SceneTree.");
            prepare();
            if (_phase != Phase.Attaching)
                return;
            _phase = Phase.Ready;
            ready();
        }
        catch (Exception exception) { Fail(exception); }
    }

    private void Fail(Exception exception)
    {
        if (_phase is Phase.Cancelled or Phase.Failed)
            return;
        _phase = Phase.Failed;
        try { Report(exception); }
        finally { Clean(); }
    }

    private void Clean()
    {
        if (_cleaned)
            return;
        _cleaned = true;
        try { cleanup(); }
        catch (Exception exception) { Report(exception); }
    }

    private void Report(Exception exception)
    {
        // A diagnostic sink must not turn a failed panel into a game-startup crash.
        try { failed(exception); }
        catch { }
    }
}
