using STS2PlatformLiveUi;

namespace STS2Platform;

// The bridge calls this decision while holding its registration lock. The
// caller publishes only on true; health ambiguity never authorizes replacement.
internal static class WorkbenchRegistrationOwner
{
    internal static bool CanRegister(
        PlatformWorkbenchOpenRegistration? current,
        PlatformWorkbenchOpenRegistration candidate,
        string runtime,
        Func<PlatformWorkbenchOpenRegistration, bool> isDefinitivelyStale)
    {
        if (candidate.RuntimeInstanceId != runtime) return false;
        var active = current?.RuntimeInstanceId == runtime ? current : null;
        if (active is null) return candidate.ExpectedWorkbenchInstanceId is null;
        if (candidate.ExpectedWorkbenchInstanceId != active.WorkbenchInstanceId) return false;
        if (candidate.WorkbenchInstanceId == active.WorkbenchInstanceId
            && candidate.Url == active.Url) return true;
        return isDefinitivelyStale(active);
    }
}
