using System.Diagnostics;
using STS2HumanAnnotator.Core;

namespace STS2HumanAnnotator.Mod;

internal static partial class RecorderRuntime
{
    private static long _framePerformanceAt;
    private static RecordingSessionStore? _framePerformanceStore;
    private static RecordingLifecycleState _framePerformanceState;

    // Process-frame intervals include capture stalls between callbacks. Pause
    // is the same session's OFF baseline; transitions are excluded. This reads
    // no game state and never supplies readiness or recording evidence.
    private static void ObserveProcessFrameInterval()
    {
        long now = Stopwatch.GetTimestamp();
        var store = _store;
        var state = _lifecycle.State;
        if (store != null && ReferenceEquals(store, _framePerformanceStore) && state == _framePerformanceState
            && state is RecordingLifecycleState.Recording or RecordingLifecycleState.Paused && _framePerformanceAt != 0)
            store.ObservePerformanceInterval("game_process_frame_interval." + state.ToString().ToLowerInvariant(),
                _framePerformanceAt, now);
        _framePerformanceAt = now;
        _framePerformanceStore = store;
        _framePerformanceState = state;
    }
}
