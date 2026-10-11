namespace STS2HumanAnnotator.Core;

internal static class RecordingResourceCleanup
{
    // A failed flush/dispose must not strand the remaining streams or owner lease.
    internal static void DisposeAll(IEnumerable<IDisposable?> resources)
    {
        List<Exception>? failures = null;
        foreach (IDisposable? resource in resources)
        {
            try { resource?.Dispose(); }
            catch (Exception exception) { (failures ??= new()).Add(exception); }
        }
        if (failures != null) throw new AggregateException("Recording resource cleanup failed.", failures);
    }
}
