using System.Reflection;
using Xunit;
using STS2HumanAnnotator.Mod;

namespace STS2HumanAnnotator.SourceNative.Tests;

public sealed class SourceNativeSelectorCompletionReceiptTests
{
    private static void OriginalCallback() { }
    private static void OtherCallback() { }
    private static readonly MethodBase Callback = typeof(SourceNativeSelectorCompletionReceiptTests)
        .GetMethod(nameof(OriginalCallback), BindingFlags.NonPublic | BindingFlags.Static)!;
    private static readonly MethodBase Other = typeof(SourceNativeSelectorCompletionReceiptTests)
        .GetMethod(nameof(OtherCallback), BindingFlags.NonPublic | BindingFlags.Static)!;

    [Fact]
    public void ExactOriginalPendingTaskOwnerCallbackAndResultAreRequired()
    {
        object owner = new(); var original = new TaskCompletionSource<int?>();
        var proof = NativeSourceSelectorCompletionReceipt.Capture<int?>(owner, Callback, original.Task, 4, (a,b) => a == b)!;
        Assert.False(proof.Accepted(owner, Callback));
        original.SetResult(4);
        Assert.True(proof.Accepted(owner, Callback));
        Assert.False(proof.Accepted(new(), Callback));
        Assert.False(proof.Accepted(owner, Other));
        // A later screen task is irrelevant: the receipt retains the exact original Task.
        var replacement = new TaskCompletionSource<int?>(); replacement.SetResult(4);
        var originalStillPending = new TaskCompletionSource<int?>();
        var wrong = NativeSourceSelectorCompletionReceipt.Capture<int?>(owner, Callback,
            originalStillPending.Task, 4, (a,b) => a == b)!;
        Assert.False(wrong.Accepted(owner, Callback));
        Assert.True(proof.Accepted(owner, Callback));
    }
    [Fact]
    public void SuccessfulOriginalReceiptSurvivesItsSynchronousContinuationOpeningANewTask()
    {
        object owner = new(); var original = new TaskCompletionSource<int?>();
        var next = new TaskCompletionSource<int?>(); Task<int?> current = original.Task;
        var proof = NativeSourceSelectorCompletionReceipt.Capture<int?>(owner, Callback, original.Task, 4, (a,b) => a == b)!;
        original.Task.ContinueWith(_ => current = next.Task, CancellationToken.None,
            TaskContinuationOptions.ExecuteSynchronously, TaskScheduler.Default);
        original.SetResult(4);
        Assert.Same(next.Task, current); Assert.False(current.IsCompleted);
        Assert.True(proof.Accepted(owner, Callback));
    }

    [Fact]
    public void PrecompletedCanceledFaultedAndWrongResultDoNotSupplyReceipt()
    {
        object owner = new();
        Assert.Null(NativeSourceSelectorCompletionReceipt.Capture<int?>(owner, Callback,
            Task.FromResult<int?>(4), 4, (a,b) => a == b));
        foreach (string failure in new[] { "canceled", "faulted", "wrong" })
        {
            var original = new TaskCompletionSource<int?>();
            var proof = NativeSourceSelectorCompletionReceipt.Capture<int?>(owner, Callback, original.Task, 4, (a,b) => a == b)!;
            if (failure == "canceled") original.SetCanceled();
            else if (failure == "faulted") original.SetException(new InvalidOperationException("native fixture"));
            else original.SetResult(3);
            Assert.False(proof.Accepted(owner, Callback));
        }
    }
    [Fact]
    public void EqualDefinitionDoesNotReplaceExactOriginalCardObject()
    {
        object owner = new(), card = new(), anotherOccurrence = new();
        var original = new TaskCompletionSource<IEnumerable<object>>();
        var proof = NativeSourceSelectorCompletionReceipt.Capture<IEnumerable<object>>(owner, Callback, original.Task,
            new[] { card }, (actual, expected) => actual.Take(2).ToArray() is { Length: 1 } one
                && ReferenceEquals(one[0], expected.Single()))!;
        original.SetResult(new[] { anotherOccurrence });
        Assert.False(proof.Accepted(owner, Callback));
    }
}
