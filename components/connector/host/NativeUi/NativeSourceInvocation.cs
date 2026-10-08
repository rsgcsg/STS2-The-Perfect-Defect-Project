using System;
using System.Threading.Tasks;

namespace STS2Connector.NativeUi;

// A private proof of an actual native method invocation, not a gameplay phase
// machine. Only that method's entry/return/finalizer may establish this witness.
internal sealed class NativeSourceInvocation<T> where T : class
{
    internal sealed class Ticket
    {
        internal T Fact { get; private set; }
        private readonly int entryThread = Environment.CurrentManagedThreadId;
        private State state = State.MethodEntry;
        private Task? returnedTask;
        private enum State { MethodEntry, ReturnedTask, Completed, Invalid }
        internal Ticket(T fact) => Fact = fact;
        internal bool IsActive => state == State.MethodEntry
            ? Environment.CurrentManagedThreadId == entryThread
            : state == State.ReturnedTask && returnedTask is { IsCompleted: false };
        internal bool IsValid => state != State.Invalid;
        internal bool IsCompleted => state == State.Completed;
        internal bool InMethod => state == State.MethodEntry;
        internal void Invalidate() => state = State.Invalid;
        internal void Returned(Task? task)
        {
            returnedTask = task;
            state = task is { IsCompleted: false } ? State.ReturnedTask : State.Invalid;
        }
        internal void Completed(T fact) { Fact = fact; state = State.Completed; }
    }
    private Ticket? current;
    internal Ticket Begin(T fact)
    {
        current?.Invalidate();
        return current = new(fact);
    }
    internal bool IsCurrent(Ticket? ticket) => ticket is { IsValid: true } && ReferenceEquals(current, ticket);
    internal Ticket? Active => current is { IsActive: true } ticket ? ticket : null;
    internal Ticket? Completed => current is { IsCompleted: true } ticket ? ticket : null;
    internal void Returned(Ticket? ticket, Task? actualTask)
    {
        if (IsCurrent(ticket) && ticket!.InMethod) ticket.Returned(actualTask);
    }
    internal void Complete(Ticket? ticket, T fact)
    {
        if (IsCurrent(ticket) && ticket!.InMethod) ticket.Completed(fact);
    }
    internal void Finalized(Ticket? ticket, Exception? exception)
    {
        if (IsCurrent(ticket) && (exception != null || ticket!.InMethod)) ticket!.Invalidate();
    }
    internal void Invalidate(Ticket? ticket)
    {
        if (IsCurrent(ticket)) ticket!.Invalidate();
    }
    internal void Invalidate() => current?.Invalidate();
}
