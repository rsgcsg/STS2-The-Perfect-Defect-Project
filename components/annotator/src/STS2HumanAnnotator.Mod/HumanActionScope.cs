using STS2Connector.PlayerEnvironment.Witness;
using STS2HumanAnnotator.Core;
using STS2Platform.NativeFoundation;

namespace STS2HumanAnnotator.Mod;

internal readonly record struct NativeUiScopeEntry(
    bool Entered,
    bool DeferredFailure,
    string? ActionWitnessId = null,
    bool CarrierBindingFailed = false,
    NativeSourceInputInvocation? SourceInvocation = null,
    bool SourceInvocationBorrowed = false);

internal sealed record NativeUiAttemptOutcome(object Operand, Task<bool> Result);

internal sealed class HumanActionContext
{
    private readonly AcceptedRootActionGate _rootActionGate;
    private int _rejectedAcceptedIngress;

    internal HumanActionContext(
        string origin,
        string expectedNativeActionType,
        ProcessLocalObservedAction? expectedAction,
        ProcessLocalNativeWitnessFrame frame,
        ProcessLocalNativeSemanticCapture? nativeSemanticDecision,
        string? actionWitnessId,
        NativePostCommitCompletionExpectation? completionExpectation,
        HumanActionOccurrenceEvidence? occurrence,
        DateTimeOffset enteredAt,
        ProcessLocalObservedAction? nativeSemanticSelection = null,
        bool nativeInputBinding = false)
    {
        NativeInputBinding = nativeInputBinding;
        Origin = origin;
        ExpectedNativeActionType = expectedNativeActionType;
        ExpectedAction = expectedAction;
        Frame = frame;
        NativeSemanticDecision = nativeSemanticDecision;
        NativeSemanticSelection = nativeSemanticSelection;
        ActionWitnessId = actionWitnessId ?? $"scope-action-{Guid.NewGuid():N}";
        CompletionExpectation = completionExpectation;
        Occurrence = occurrence;
        EnteredAt = enteredAt;
        _rootActionGate = new AcceptedRootActionGate(expectedNativeActionType);
    }

    internal RecorderRuntime.NestedUiInput? NestedInput { get; set; }

    internal bool NativeInputBinding { get; }
    internal string Origin { get; }
    internal string ExpectedNativeActionType { get; }

    internal ProcessLocalObservedAction? ExpectedAction { get; }

    internal ProcessLocalNativeWitnessFrame Frame { get; }

    internal ProcessLocalNativeSemanticCapture? NativeSemanticDecision { get; }

    internal ProcessLocalObservedAction? NativeSemanticSelection { get; }

    internal string ActionWitnessId { get; }

    internal NativePostCommitCompletionExpectation? CompletionExpectation { get; }

    internal HumanActionOccurrenceEvidence? Occurrence { get; }

    internal NativeUiAttemptOutcome? NativeAttemptOutcome { get; set; }

    internal string? DeferredCarrierBindingFailure { get; set; }

    internal DateTimeOffset EnteredAt { get; }

    internal bool AcceptsRootAction(string nativeActionType) =>
        _rootActionGate.Accepts(nativeActionType);

    internal bool TryClaimRootAction(string nativeActionType) =>
        _rootActionGate.TryClaim(nativeActionType);

    internal bool RootActionClaimed => _rootActionGate.IsClaimed;

    // A different native callback must not consume the expected-root gate,
    // but the first such accepted callback still needs one failed-closed
    // disposition. This local bit makes that disposition idempotent without
    // introducing another root registry or completion ledger.
    internal bool TryClaimRejectedAcceptedIngress() =>
        Interlocked.Exchange(ref _rejectedAcceptedIngress, 1) == 0;
}

internal sealed class DeferredHumanActionFailure
{
    private readonly AcceptedRootActionGate _rootActionGate;

    internal DeferredHumanActionFailure(
        string expectedNativeActionType,
        string reasonCode,
        string detail,
        string? snapshotId,
        string evidenceLevel,
        HumanActionOccurrenceEvidence? occurrence)
    {
        ExpectedNativeActionType = expectedNativeActionType;
        _rootActionGate = new AcceptedRootActionGate(expectedNativeActionType);
        ReasonCode = reasonCode;
        Detail = detail;
        SnapshotId = snapshotId;
        EvidenceLevel = evidenceLevel;
        Occurrence = occurrence;
    }

    internal string ExpectedNativeActionType { get; }

    internal string ReasonCode { get; }

    internal string Detail { get; }

    internal string? SnapshotId { get; }

    internal string EvidenceLevel { get; }

    internal HumanActionOccurrenceEvidence? Occurrence { get; }

    internal NativeUiAttemptOutcome? NativeAttemptOutcome { get; set; }

    internal bool TryClaim(string nativeActionType) =>
        _rootActionGate.TryClaim(nativeActionType);
}

internal static class HumanActionScope
{
    [ThreadStatic]
    private static Stack<HumanActionContext>? _stack;

    [ThreadStatic]
    private static Stack<DeferredHumanActionFailure>? _deferredFailures;

    internal static HumanActionContext? Current =>
        _stack is { Count: > 0 } ? _stack.Peek() : null;

    internal static DeferredHumanActionFailure? CurrentDeferredFailure =>
        _deferredFailures is { Count: > 0 } ? _deferredFailures.Peek() : null;

    internal static void Enter(
        string origin,
        string expectedNativeActionType,
        ProcessLocalObservedAction? expectedAction,
        ProcessLocalNativeWitnessFrame frame,
        ProcessLocalNativeSemanticCapture? nativeSemanticDecision = null,
        string? actionWitnessId = null,
        NativePostCommitCompletionExpectation? completionExpectation = null,
        HumanActionOccurrenceEvidence? occurrence = null,
        ProcessLocalObservedAction? nativeSemanticSelection = null,
        bool nativeInputBinding = false)
    {
        _stack ??= new Stack<HumanActionContext>();
        _stack.Push(new HumanActionContext(
            origin,
            expectedNativeActionType,
            expectedAction,
            frame,
            nativeSemanticDecision,
            actionWitnessId,
            completionExpectation,
            occurrence,
            DateTimeOffset.UtcNow,
            nativeSemanticSelection,
            nativeInputBinding));
    }

    internal static void Exit()
    {
        if (_stack is { Count: > 0 })
            _stack.Pop();
    }

    internal static void EnterDeferredFailure(
        string expectedNativeActionType,
        string reasonCode,
        string detail,
        string? snapshotId,
        string evidenceLevel,
        HumanActionOccurrenceEvidence? occurrence = null)
    {
        _deferredFailures ??= new Stack<DeferredHumanActionFailure>();
        _deferredFailures.Push(new DeferredHumanActionFailure(
            expectedNativeActionType,
            reasonCode,
            detail,
            snapshotId,
            evidenceLevel,
            occurrence));
    }

    internal static void ExitDeferredFailure()
    {
        if (_deferredFailures is { Count: > 0 })
            _deferredFailures.Pop();
    }
}
