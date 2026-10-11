using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;

namespace STS2Connector.PlayerEnvironment;

/// <summary>One bounded result-only arena. Every byte block is allocated before
/// new request admission. Retired blocks stay in this bounded arena for reuse;
/// arena capacity, active reservation/retention charge and process RSS are
/// distinct measurements. A sender never allocates or re-encodes payload bytes.</summary>
internal sealed class RequestResultArena
{
    internal const long MaximumCapacityBytes = 512L * 1024 * 1024;
    internal const int DefaultSegmentBytes = 65_536;
    private readonly object gate = new();
    private readonly Stack<byte[]> free = new();
    private readonly Func<int, byte[]> allocator;
    private readonly int maxBlocks;
    private int allocatedBlocks, occupiedBlocks, peakOccupiedBlocks;
    internal int SegmentBytes { get; }
    internal long CapacityBytes { get; }
    internal long AllocatedCapacityBytes { get { lock (gate) return (long)allocatedBlocks * SegmentBytes; } }
    internal long ChargedBytes { get { lock (gate) return (long)occupiedBlocks * SegmentBytes; } }
    internal long PeakChargedBytes { get { lock (gate) return (long)peakOccupiedBlocks * SegmentBytes; } }

    internal RequestResultArena(long capacityBytes = MaximumCapacityBytes,
        int segmentBytes = DefaultSegmentBytes, Func<int, byte[]>? allocator = null)
    {
        if (capacityBytes <= 0 || capacityBytes > MaximumCapacityBytes
            || segmentBytes < 64 || segmentBytes > DefaultSegmentBytes
            || capacityBytes % segmentBytes != 0)
            throw new ArgumentOutOfRangeException(nameof(capacityBytes));
        CapacityBytes = capacityBytes; SegmentBytes = segmentBytes;
        maxBlocks = checked((int)(capacityBytes / segmentBytes));
        this.allocator = allocator ?? (length => new byte[length]);
    }

    internal ResultReservation? TryReserve(int reservationBytes, int maxEncodedBytes)
    {
        if (reservationBytes <= 0 || maxEncodedBytes <= 0)
            throw new ArgumentOutOfRangeException(nameof(reservationBytes));
        int count = checked((reservationBytes + SegmentBytes - 1) / SegmentBytes);
        int outputCount = checked((maxEncodedBytes + SegmentBytes - 1) / SegmentBytes);
        // One full, explicitly charged scratch block is always reserved too.
        if (count <= outputCount) throw new ArgumentException("Reservation must include its fixed scratch block.");
        lock (gate)
        {
            if (count > maxBlocks - occupiedBlocks) return null;
            var blocks = new byte[count][];
            int acquired = 0;
            try
            {
                for (; acquired < count; acquired++)
                {
                    if (free.Count > 0) blocks[acquired] = free.Pop();
                    else
                    {
                        if (allocatedBlocks == maxBlocks) throw new InvalidOperationException("Arena accounting differs from physical capacity.");
                        byte[] block = allocator(SegmentBytes);
                        if (block.Length != SegmentBytes) throw new InvalidOperationException("The controlled allocator changed a block capacity.");
                        blocks[acquired] = block; allocatedBlocks++;
                    }
                }
            }
            catch
            {
                for (int index = 0; index < acquired; index++) free.Push(blocks[index]);
                throw;
            }
            occupiedBlocks += count;
            peakOccupiedBlocks = Math.Max(peakOccupiedBlocks, occupiedBlocks);
            return new(this, blocks, maxEncodedBytes);
        }
    }

    private void Return(byte[][] blocks)
    {
        lock (gate)
        {
            foreach (byte[] block in blocks) free.Push(block);
            occupiedBlocks -= blocks.Length;
            if (occupiedBlocks < 0) throw new InvalidOperationException("A result block was returned twice.");
        }
    }

    internal sealed class ResultReservation : IDisposable
    {
        private readonly RequestResultArena arena;
        private byte[][]? blocks;
        private readonly int maximumLength;
        private int length;
        private bool prepared;
        private PreparedTerminal? staged;
        internal int Length => length;
        internal int MaximumLength => maximumLength;
        internal Span<byte> Scratch => (blocks ?? throw new ObjectDisposedException(nameof(ResultReservation)))[0];
        internal ResultReservation(RequestResultArena arena, byte[][] blocks, int maximumLength)
        { this.arena = arena; this.blocks = blocks; this.maximumLength = maximumLength; }

        internal void ResetEncoding()
        {
            if (blocks is null) throw new ObjectDisposedException(nameof(ResultReservation));
            if (prepared) throw new InvalidOperationException("A prepared terminal is already immutable.");
            length = 0;
        }
        internal void Append(byte value)
        {
            var owned = blocks ?? throw new ObjectDisposedException(nameof(ResultReservation));
            if (prepared) throw new InvalidOperationException("A prepared terminal is already immutable.");
            if (length == maximumLength) throw new ResultPayloadCapacityException();
            owned[1 + length / arena.SegmentBytes][length % arena.SegmentBytes] = value;
            length++;
        }
        internal void Append(ReadOnlySpan<byte> value)
        {
            var owned = blocks ?? throw new ObjectDisposedException(nameof(ResultReservation));
            if (prepared) throw new InvalidOperationException("A prepared terminal is already immutable.");
            if (value.Length > maximumLength - length) throw new ResultPayloadCapacityException();
            while (!value.IsEmpty)
            {
                int offset = length % arena.SegmentBytes;
                int count = Math.Min(arena.SegmentBytes - offset, value.Length);
                value[..count].CopyTo(owned[1 + length / arena.SegmentBytes].AsSpan(offset, count));
                value = value[count..]; length += count;
            }
        }

        internal PreparedTerminal Prepare()
        {
            byte[][] owned = blocks ?? throw new ObjectDisposedException(nameof(ResultReservation));
            if (prepared) throw new InvalidOperationException("A prepared terminal already exists.");
            int outputCount = (length + arena.SegmentBytes - 1) / arena.SegmentBytes;
            var retained = new byte[outputCount][];
            for (int index = 0; index < outputCount; index++) retained[index] = owned[index + 1];
            var unused = new byte[owned.Length - outputCount][];
            int next = 0;
            for (int index = 0; index < owned.Length; index++)
                if (index == 0 || index > outputCount) unused[next++] = owned[index];
            // Keep every spare/scratch block until the actual outcome decision.
            // Revocation before text-state commit can still encode its bounded
            // rejection without reacquiring capacity or allocating new bytes.
            var frozen = new FrozenTerminal(arena, retained, length, activated: false);
            prepared = true;
            staged = new(this, frozen, unused);
            return staged;
        }
        internal FrozenTerminal Freeze()
        {
            using PreparedTerminal terminal = Prepare();
            return terminal.Commit();
        }
        internal sealed class PreparedTerminal : IDisposable
        {
            private ResultReservation? owner;
            private FrozenTerminal? frozen;
            private byte[][]? unused;
            internal PreparedTerminal(ResultReservation owner, FrozenTerminal frozen, byte[][] unused)
            { this.owner = owner; this.frozen = frozen; this.unused = unused; }
            internal FrozenTerminal Commit()
            {
                ResultReservation current = owner ?? throw new ObjectDisposedException(nameof(PreparedTerminal));
                FrozenTerminal result = frozen!;
                current.blocks = null;
                current.staged = null;
                result.Activate();
                current.arena.Return(unused!);
                owner = null; frozen = null; unused = null;
                return result;
            }
            public void Dispose()
            {
                ResultReservation? current = owner; owner = null;
                if (current is null) return;
                frozen!.AbandonBeforeTransfer();
                frozen = null; unused = null; current.prepared = false;
                current.staged = null;
            }
        }
        public void Dispose()
        {
            staged?.Dispose();
            byte[][]? owned = Interlocked.Exchange(ref blocks, null);
            if (owned is not null) arena.Return(owned);
        }
    }

    internal sealed class FrozenTerminal
    {
        private readonly object gate = new();
        private readonly RequestResultArena arena;
        private byte[][]? segments;
        private bool retained = true;
        private bool activated;
        private int borrowers;
        internal int Length { get; }
        internal long ChargedCapacityBytes { get { lock (gate) return (long)(segments?.Length ?? 0) * arena.SegmentBytes; } }
        internal int Borrowers { get { lock (gate) return borrowers; } }
        internal FrozenTerminal(RequestResultArena arena, byte[][] segments, int length, bool activated = true)
        { this.arena = arena; this.segments = segments; Length = length; this.activated = activated; }
        internal void Activate() { activated = true; }
        internal void AbandonBeforeTransfer()
        {
            if (activated || borrowers != 0) throw new InvalidOperationException("A transferred terminal cannot be thawed.");
            segments = null; retained = false;
        }

        internal SenderLoan Borrow()
        {
            lock (gate)
            {
                if (!activated || !retained || segments is null) throw new ObjectDisposedException(nameof(FrozenTerminal));
                borrowers = checked(borrowers + 1);
                return new(this);
            }
        }
        internal void Retire()
        {
            lock (gate)
            {
                if (!retained) return;
                retained = false;
                ReturnIfUnused();
            }
        }
        private void ReturnIfUnused()
        {
            if (retained || borrowers != 0 || segments is null) return;
            byte[][] released = segments; segments = null;
            arena.Return(released);
        }
        private void ReleaseBorrower()
        {
            lock (gate)
            {
                if (borrowers == 0) throw new InvalidOperationException("A sender loan was released twice.");
                borrowers--; ReturnIfUnused();
            }
        }
        private void WriteTo(Stream target)
        {
            // A live borrower prevents both retention expiry and other borrowers
            // from returning these blocks. No result-owner lock spans IO.
            var frozen = segments ?? throw new ObjectDisposedException(nameof(FrozenTerminal));
            int remaining = Length;
            foreach (byte[] segment in frozen)
            {
                int count = Math.Min(remaining, arena.SegmentBytes);
                target.Write(segment, 0, count); remaining -= count;
            }
        }

        internal sealed class SenderLoan : IDisposable
        {
            private readonly object gate = new();
            private FrozenTerminal? original;
            internal SenderLoan(FrozenTerminal original) => this.original = original;
            internal int Length { get { lock (gate) return (original ?? throw new ObjectDisposedException(nameof(SenderLoan))).Length; } }
            internal void WriteTo(Stream target)
            {
                ArgumentNullException.ThrowIfNull(target);
                lock (gate) (original ?? throw new ObjectDisposedException(nameof(SenderLoan))).WriteTo(target);
            }
            public void Dispose()
            {
                lock (gate)
                {
                    FrozenTerminal? released = original; original = null;
                    released?.ReleaseBorrower();
                }
            }
        }
    }
}

internal sealed class ResultPayloadCapacityException : Exception
{
    internal ResultPayloadCapacityException() : base("The full terminal envelope exceeds its declared encoded bound.") { }
}
