using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace STS2Connector;

internal sealed class MainThreadQueueFullException : Exception
{
    public MainThreadQueueFullException() : base("The native main-thread queue is full; no work was admitted.") { }
}

// Owns pending dispatch only. Starting under the same gate as cancellation is
// the boundary: once started, the delegate owns its actual result/exception.
internal sealed class MainThreadWorkQueue
{
    internal const int DefaultCapacity = 256;
    private readonly object _gate = new();
    private readonly LinkedList<WorkItem> _pending = new();
    private readonly int _capacity;

    internal MainThreadWorkQueue(int capacity = DefaultCapacity)
    {
        if (capacity <= 0) throw new ArgumentOutOfRangeException(nameof(capacity));
        _capacity = capacity;
    }

    internal int PendingCount { get { lock (_gate) return _pending.Count; } }

    internal Task<T> Enqueue<T>(Func<T> callback, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(callback);
        var item = new WorkItem<T>(callback);
        lock (_gate)
        {
            if (cancellationToken.IsCancellationRequested)
                return Task.FromCanceled<T>(cancellationToken);
            if (_pending.Count >= _capacity)
                return Task.FromException<T>(new MainThreadQueueFullException());

            item.Node = _pending.AddLast(item);
            // Register under the gate so cancellation cannot leave a queued
            // tombstone or race the queued -> started transition. Already
            // canceled tokens invoke synchronously; Monitor is reentrant.
            item.Cancellation = cancellationToken.Register(() => CancelPending(item, cancellationToken));
        }
        if (item.Task.IsCanceled) item.Cancellation.Dispose();
        return item.Task;
    }

    private void CancelPending(WorkItem item, CancellationToken token)
    {
        lock (_gate)
        {
            if (item.Node == null) return;
            _pending.Remove(item.Node);
            item.Node = null;
            item.Cancel(token);
        }
    }

    internal int Drain(int budget)
    {
        if (budget < 0) throw new ArgumentOutOfRangeException(nameof(budget));
        int processed = 0;
        while (processed < budget)
        {
            WorkItem item;
            lock (_gate)
            {
                if (_pending.First == null) break;
                item = _pending.First.Value;
                _pending.RemoveFirst();
                item.Node = null;
            }
            // Never dispose under _gate: a cancellation callback may be waiting
            // for that gate. Starting wins cancellation from this point onward.
            item.Cancellation.Dispose();
            item.Execute();
            processed++;
        }
        return processed;
    }

    private abstract class WorkItem
    {
        internal LinkedListNode<WorkItem>? Node;
        internal CancellationTokenRegistration Cancellation;
        internal abstract void Execute();
        internal abstract void Cancel(CancellationToken token);
    }

    private sealed class WorkItem<T>(Func<T> callback) : WorkItem
    {
        private readonly TaskCompletionSource<T> _completion =
            new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal Task<T> Task => _completion.Task;
        internal override void Cancel(CancellationToken token) => _completion.TrySetCanceled(token);
        internal override void Execute()
        {
            try { _completion.TrySetResult(callback()); }
            catch (Exception exception) { _completion.TrySetException(exception); }
        }
    }
}
