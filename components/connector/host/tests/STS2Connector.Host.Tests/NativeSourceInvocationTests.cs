using STS2Connector.NativeUi;
using Xunit;

namespace STS2Connector;

public sealed class NativeSourceInvocationTests
{
    [Fact]
    public async Task MethodEntryProofIsSynchronousAndRequiresActualUnfinishedReturn()
    {
        var source = new NativeSourceInvocation<object>();
        object fact = new();
        var ticket = source.Begin(fact);
        Assert.Same(ticket, source.Active); // First native body preview can use this proof.
        Assert.Null(await Task.Factory.StartNew(() => source.Active, CancellationToken.None,
            TaskCreationOptions.LongRunning, TaskScheduler.Default)); // It is not a global phase guess.
        var native = new TaskCompletionSource();
        source.Returned(ticket, native.Task);
        source.Finalized(ticket, null);
        Assert.Same(ticket, source.Active);
        Assert.Null(source.Completed);
        native.SetResult();
        Assert.Null(source.Active);
        Assert.Null(source.Completed); // Native Task completion is not a public completion fact.
    }

    [Fact]
    public void MissingCompletedFaultedOrCanceledTaskCannotLeaveEntryProofAlive()
    {
        Task?[] returned = { null, Task.CompletedTask, Task.FromException(new Exception("native")),
            Task.FromCanceled(new CancellationToken(true)) };
        foreach (Task? task in returned)
        {
            var source = new NativeSourceInvocation<object>();
            var ticket = source.Begin(new());
            source.Returned(ticket, task);
            source.Finalized(ticket, null);
            Assert.Null(source.Active);
            Assert.Null(source.Completed);
            Assert.False(source.IsCurrent(ticket));
            source.Returned(ticket, new TaskCompletionSource().Task);
            Assert.Null(source.Active); // An invalid proof cannot be resurrected.
        }
        var missingReturn = new NativeSourceInvocation<object>();
        var entry = missingReturn.Begin(new());
        missingReturn.Finalized(entry, null);
        Assert.Null(missingReturn.Active);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void LaterNativeTaskFaultOrCancellationEndsProofWithoutInventingCommit(bool cancel)
    {
        var source = new NativeSourceInvocation<object>();
        var ticket = source.Begin(new());
        var native = new TaskCompletionSource();
        source.Returned(ticket, native.Task);
        Assert.Same(ticket, source.Active);
        if (cancel) native.SetCanceled(); else native.SetException(new InvalidOperationException("native"));
        Assert.Null(source.Active);
        Assert.Null(source.Completed);
    }

    [Fact]
    public void ReplacementAndOldReturnOrFinalizerCannotEraseOrReplaceNewProof()
    {
        var source = new NativeSourceInvocation<object>();
        var old = source.Begin(new());
        var current = source.Begin(new());
        var native = new TaskCompletionSource();
        source.Returned(current, native.Task);
        source.Returned(old, Task.CompletedTask);
        source.Finalized(old, new InvalidOperationException());
        source.Invalidate(old);
        source.Complete(old, new());
        Assert.Same(current, source.Active);
        Assert.False(source.IsCurrent(old));
        // A second return cannot swap the actual Task acquired by this invocation.
        source.Returned(current, Task.CompletedTask);
        Assert.Same(current, source.Active);
        source.Invalidate();
        source.Returned(current, new TaskCompletionSource().Task);
        Assert.Null(source.Active);
    }

    [Fact]
    public void StartCompletionAndItsFinalizerDoNotAlterIndependentMultiProof()
    {
        var start = new NativeSourceInvocation<object>();
        var multi = new NativeSourceInvocation<object>();
        var entering = start.Begin(new());
        var phase = multi.Begin(new());
        multi.Returned(phase, new TaskCompletionSource().Task);
        object bound = new();
        start.Complete(entering, bound);
        start.Finalized(entering, null);
        Assert.Same(bound, start.Completed!.Fact);
        Assert.Same(phase, multi.Active);
        start.Finalized(entering, new Exception());
        Assert.Null(start.Completed);
        Assert.Same(phase, multi.Active);
    }

    [Fact]
    public void CompletedDisplayProofRequiresMethodReturnAndCannotSurviveExceptionOrReplacement()
    {
        var source = new NativeSourceInvocation<object>();
        Assert.Null(source.Completed); // Late attach/current fields cannot establish origin.
        var ticket = source.Begin(new());
        Assert.Null(source.Completed);
        object display = new();
        source.Complete(ticket, display);
        Assert.Same(display, source.Completed!.Fact);
        Assert.Null(source.Active);
        source.Finalized(ticket, new InvalidOperationException());
        Assert.Null(source.Completed);
        source.Complete(ticket, display);
        Assert.Null(source.Completed);
        var replacement = source.Begin(new());
        source.Complete(ticket, display);
        Assert.Null(source.Completed);
        source.Complete(replacement, display);
        Assert.Same(replacement, source.Completed);
        source.Invalidate();
        Assert.Null(source.Completed);
    }
}
