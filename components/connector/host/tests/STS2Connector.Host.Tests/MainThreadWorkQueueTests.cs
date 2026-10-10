namespace STS2Connector.Tests;

public sealed class MainThreadWorkQueueTests
{
    [Fact]
    public async Task MoreThanOneFrameBudgetExecutesEveryAdmittedCallbackExactlyOnce()
    {
        var queue = new MainThreadWorkQueue();
        var executed = new List<int>();
        Task<int>[] tasks = Enumerable.Range(0, 31)
            .Select(index => queue.Enqueue(() => { executed.Add(index); return index; })).ToArray();

        Assert.Equal(10, queue.Drain(10));
        Assert.Equal(21, queue.PendingCount);
        Assert.False(tasks[10].IsCompleted);
        Assert.Equal(10, queue.Drain(10));
        Assert.Equal(10, queue.Drain(10));
        Assert.Equal(1, queue.Drain(10));
        Assert.Equal(0, queue.Drain(10));
        Assert.Equal(Enumerable.Range(0, 31), executed);
        Assert.Equal(Enumerable.Range(0, 31), await Task.WhenAll(tasks));
    }

    [Fact]
    public async Task FullAdmissionFailsImmediatelyWithoutExecutingOrDisturbingPendingWork()
    {
        var queue = new MainThreadWorkQueue(2);
        Task<int> first = queue.Enqueue(() => 1);
        Task<int> second = queue.Enqueue(() => 2);
        bool rejectedRan = false;
        Task<int> rejected = queue.Enqueue(() => { rejectedRan = true; return 3; });

        Assert.True(rejected.IsCompleted);
        await Assert.ThrowsAsync<MainThreadQueueFullException>(() => rejected);
        Assert.Equal(2, queue.PendingCount);
        Assert.Equal(2, queue.Drain(10));
        Assert.Equal(new[] { 1, 2 }, await Task.WhenAll(first, second));
        Assert.False(rejectedRan);
    }

    [Fact]
    public async Task QueuedCancellationRemovesMutationAndImmediatelyReleasesCapacity()
    {
        var queue = new MainThreadWorkQueue(1);
        bool mutationRan = false;
        for (int iteration = 0; iteration < 100; iteration++)
        {
            using var canceled = new CancellationTokenSource();
            Task<int> mutation = queue.Enqueue(() => { mutationRan = true; return 1; }, canceled.Token);
            canceled.Cancel();
            await Assert.ThrowsAnyAsync<OperationCanceledException>(() => mutation);
            Assert.Equal(0, queue.PendingCount);
        }
        Task<int> next = queue.Enqueue(() => 2);
        Assert.Equal(1, queue.Drain(10));
        Assert.Equal(2, await next);
        Assert.False(mutationRan);
    }

    [Fact]
    public async Task CancellationInMiddleKeepsRemainingFifoOrder()
    {
        var queue = new MainThreadWorkQueue(3);
        using var canceled = new CancellationTokenSource();
        var executed = new List<int>();
        Task<int> first = queue.Enqueue(() => { executed.Add(1); return 1; });
        Task<int> middle = queue.Enqueue(() => { executed.Add(2); return 2; }, canceled.Token);
        Task<int> last = queue.Enqueue(() => { executed.Add(3); return 3; });
        canceled.Cancel();
        Assert.Equal(2, queue.Drain(10));
        Assert.Equal(new[] { 1, 3 }, executed);
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => middle);
        Assert.Equal(new[] { 1, 3 }, await Task.WhenAll(first, last));
    }

    [Fact]
    public async Task PreCanceledAdmissionNeverConsumesCapacityOrRuns()
    {
        var queue = new MainThreadWorkQueue(1);
        using var canceled = new CancellationTokenSource();
        canceled.Cancel();
        bool mutationRan = false;
        Task<int> mutation = queue.Enqueue(() => { mutationRan = true; return 1; }, canceled.Token);
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => mutation);
        Assert.Equal(0, queue.PendingCount);
        Assert.Equal(0, queue.Drain(10));
        Assert.False(mutationRan);
    }

    [Fact]
    public async Task StartedMutationKeepsItsActualUnknownOutcomeAfterCancellation()
    {
        var queue = new MainThreadWorkQueue(1);
        using var canceled = new CancellationTokenSource();
        using var started = new ManualResetEventSlim();
        using var finish = new ManualResetEventSlim();
        int executions = 0;
        Task<string> mutation = queue.Enqueue(() =>
        {
            Interlocked.Increment(ref executions);
            started.Set();
            if (!finish.Wait(TimeSpan.FromSeconds(10))) throw new TimeoutException();
            return "unknown";
        }, canceled.Token);
        Task<int> drain = Task.Run(() => queue.Drain(10));
        try
        {
            Assert.True(started.Wait(TimeSpan.FromSeconds(10)));
            canceled.Cancel();
            Assert.False(mutation.IsCompleted);
            Assert.Equal(0, queue.PendingCount);
        }
        finally { finish.Set(); }
        Assert.Equal(1, await drain.WaitAsync(TimeSpan.FromSeconds(10)));
        Assert.Equal("unknown", await mutation);
        Assert.Equal(1, executions);
        Assert.Equal(0, queue.Drain(10));
    }

    [Fact]
    public async Task StartedFaultSurvivesCancellationAndDoesNotStopLaterCallbacks()
    {
        var queue = new MainThreadWorkQueue();
        using var canceled = new CancellationTokenSource();
        var nativeFault = new InvalidOperationException("native result unresolved");
        Task<int> mutation = queue.Enqueue<int>(() => { canceled.Cancel(); throw nativeFault; }, canceled.Token);
        Task<int> later = queue.Enqueue(() => 7);
        Assert.Equal(2, queue.Drain(10));
        Assert.Same(nativeFault, await Assert.ThrowsAsync<InvalidOperationException>(() => mutation));
        Assert.False(mutation.IsCanceled);
        Assert.Equal(7, await later);
    }
}
