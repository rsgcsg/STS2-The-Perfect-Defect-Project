using System;
using System.Collections.Generic;
using STS2Connector.PlayerEnvironment.NativeLogical;

namespace STS2Connector.PlayerEnvironment;

internal sealed partial class NativeLogicalService
{
    private const string SourceOrderUnproven = "source_prefix_capture_order_unproven";
    // Only the native thread enters or changes these short acquisition scopes.
    // There is no lock across Prepare or any native callback, and asynchronous
    // encoder completion is deliberately absent from this proof.
    private readonly List<SourceNativeFreeze> sourceNativeFreezes = new();
    private sealed class SourceNativeFreeze(NativeLogicalService owner,
        NativeLogicalSourceRecordingAttachment original, NativeLogicalSourceEpoch epoch, bool invalid) : IDisposable
    {
        internal readonly NativeLogicalSourceRecordingAttachment Original = original;
        internal readonly NativeLogicalSourceEpoch Epoch = epoch;
        internal bool Invalid = invalid;
        private bool finished;
        internal bool Proven => !Invalid && ReferenceEquals(owner.sourceRecorder, Original)
            && !Original.Disposed && !Original.Closing && ReferenceEquals(Original.Current, Epoch);
        internal bool Seal()
        {
            owner.AssertMainThread(); bool proven = Proven; Dispose(); return proven;
        }
        public void Dispose()
        {
            if (finished) return;
            finished = true;
            if (owner.sourceNativeFreezes.Count == 0 || !ReferenceEquals(owner.sourceNativeFreezes[^1], this))
            { owner.InvalidateSourceNativeFreeze(); owner.sourceNativeFreezes.Remove(this); return; }
            owner.sourceNativeFreezes.RemoveAt(owner.sourceNativeFreezes.Count - 1);
        }
    }
    private SourceNativeFreeze? BeginSourceNativeFreeze()
    {
        AssertMainThread();
        if (sourceRecorder is not { OrderedBasis: true, Disposed: false } original) return null;
        bool ancestor = sourceNativeFreezes.Count != 0;
        InvalidateSourceNativeFreeze();
        if (sourceNativeFreezes.Count >= 128)
        { FailSource(original, "source_native_freeze_capacity"); return null; }
        var scope = new SourceNativeFreeze(this, original, original.Current, ancestor);
        sourceNativeFreezes.Add(scope); return scope;
    }
    private void InvalidateSourceNativeFreeze()
    {
        foreach (var scope in sourceNativeFreezes) scope.Invalid = true;
    }
    internal NativeLogicalSourceBoundary ReadSourceCommandBoundary(NativeLogicalSourceRecordingAttachment original)
    {
        AssertMainThread(); InvalidateSourceNativeFreeze();
        return ReadSourceBoundary(original);
    }
}
