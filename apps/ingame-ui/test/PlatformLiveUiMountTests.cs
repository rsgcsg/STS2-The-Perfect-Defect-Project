using Xunit;

namespace STS2PlatformLiveUi;

public sealed class PlatformLiveUiMountTests
{
    private sealed class Host
    {
        internal readonly Queue<Action> Idle = new();
        internal bool Busy = true;
        internal bool Live = true;
        internal bool InTree;
        internal bool SilentlyRejectAdd;
        internal bool PrepareFails;
        internal int Attaches;
        internal int Prepares;
        internal int Ready;
        internal int Cleanups;
        internal readonly List<Exception> Failures = new();
        internal PlatformLiveUiMount Create() => new(
            callback => Idle.Enqueue(callback), () => Live,
            () =>
            {
                Attaches++;
                if (Busy)
                    throw new InvalidOperationException("Parent is setting up children.");
                InTree = !SilentlyRejectAdd;
            },
            () => InTree,
            () =>
            {
                Prepares++;
                if (PrepareFails)
                    throw new InvalidOperationException("Panel prepare failed.");
            },
            () => Ready++, exception => Failures.Add(exception),
            () => { Cleanups++; InTree = false; });
        internal void Drain() { while (Idle.TryDequeue(out var callback)) callback(); }
    }

    [Fact]
    public void BusyStartupDoesNotAttachUntilTheIdleBoundary()
    {
        var host = new Host();
        var mount = host.Create();
        mount.Begin();
        Assert.Equal(0, host.Attaches);
        Assert.False(mount.IsReady);
        host.Busy = false;
        host.Drain();
        Assert.True(mount.IsReady);
        Assert.Equal(1, host.Attaches);
        Assert.Equal(1, host.Prepares);
        Assert.Equal(1, host.Ready);
        Assert.Empty(host.Failures);
    }

    [Fact]
    public void ShutdownCancelsAnAlreadyQueuedMount()
    {
        var host = new Host();
        var mount = host.Create();
        mount.Begin();
        host.Live = false;
        mount.Cancel();
        mount.Cancel();
        host.Drain();
        Assert.False(mount.IsReady);
        Assert.Equal(0, host.Attaches);
        Assert.Equal(0, host.Ready);
        Assert.Equal(1, host.Cleanups);
    }

    [Fact]
    public void DuplicateBeginsAndDuplicateCallbacksMountOnlyOnce()
    {
        var host = new Host { Busy = false };
        var mount = host.Create();
        mount.Begin();
        mount.Begin();
        Assert.Single(host.Idle);
        var callback = host.Idle.Dequeue();
        callback();
        callback();
        mount.Begin();
        Assert.Equal(1, host.Attaches);
        Assert.Equal(1, host.Ready);
        Assert.True(mount.IsReady);
    }

    [Theory]
    [InlineData(true, false, false)]
    [InlineData(false, true, false)]
    [InlineData(false, false, true)]
    public void BusyRejectedAddOrPrepareFailureNeverPublishesReady(
        bool busy, bool silentlyReject, bool prepareFails)
    {
        var host = new Host { Busy = busy, SilentlyRejectAdd = silentlyReject, PrepareFails = prepareFails };
        var mount = host.Create();
        mount.Begin();
        host.Drain();
        mount.Begin();
        mount.Cancel();
        Assert.False(mount.IsReady);
        Assert.Equal(0, host.Ready);
        Assert.Single(host.Failures);
        Assert.Equal(1, host.Cleanups);
        Assert.Empty(host.Idle);
    }

    [Fact]
    public void MissingTreeBeforeTheDeferredCallbackCancelsWithoutAttaching()
    {
        var host = new Host { Busy = false };
        var mount = host.Create();
        mount.Begin();
        host.Live = false;
        host.Drain();
        Assert.False(mount.IsReady);
        Assert.Equal(0, host.Attaches);
        Assert.Equal(0, host.Ready);
        Assert.Equal(1, host.Cleanups);
    }

    [Fact]
    public void CancellationDuringAttachCannotPublishLateReady()
    {
        var queue = new Queue<Action>();
        int ready = 0, cleanup = 0, prepare = 0;
        PlatformLiveUiMount? mount = null;
        mount = new(queue.Enqueue, () => true, () => mount!.Cancel(), () => true,
            () => prepare++, () => ready++, _ => { }, () => cleanup++);
        mount.Begin();
        queue.Dequeue()();
        Assert.False(mount.IsReady);
        Assert.Equal(0, prepare);
        Assert.Equal(0, ready);
        Assert.Equal(1, cleanup);
    }
}
