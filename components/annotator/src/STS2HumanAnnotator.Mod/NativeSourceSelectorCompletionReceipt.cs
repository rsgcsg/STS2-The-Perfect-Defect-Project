using System.Reflection;

namespace STS2HumanAnnotator.Mod;

/// <summary>One original pending native callback task; no current-state or replacement-task acceptance.</summary>
internal abstract class NativeSourceSelectorCompletionReceipt
{
    internal abstract bool Accepted(object exactOwner, MethodBase exactCallback);
    internal static NativeSourceSelectorCompletionReceipt? Capture<T>(object owner, MethodBase callback,
        Task<T>? originalTask, T expected, Func<T, T, bool> sameResult) =>
        originalTask is { IsCompleted: false } ? new Receipt<T>(owner, callback, originalTask, expected, sameResult) : null;

    private sealed class Receipt<T>(object owner, MethodBase callback, Task<T> task, T expected,
        Func<T, T, bool> sameResult) : NativeSourceSelectorCompletionReceipt
    {
        internal override bool Accepted(object exactOwner, MethodBase exactCallback)
        {
            if (!ReferenceEquals(owner, exactOwner) || callback != exactCallback || !task.IsCompletedSuccessfully) return false;
            try { return sameResult(task.GetAwaiter().GetResult(), expected); }
            catch { return false; } // A malformed native result is no receipt, and cannot strand the Source finalizer.
        }
    }
}
