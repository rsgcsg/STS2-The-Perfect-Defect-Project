using STS2Connector.PlayerEnvironment.Witness;
using MegaCrit.Sts2.Core.GameActions;
using STS2HumanAnnotator.Core;
using STS2Platform.NativeFoundation;

namespace STS2HumanAnnotator.Mod;

internal sealed class NativeActionLifecycleSubscription : IDisposable
{
    private readonly GameAction _action;
    private readonly NativeActionLifecycleObserver _nativeObserver;
    private readonly Action<NativeActionLifecycleSubscription, string> _observer;
    private bool _disposed;

    internal NativeActionLifecycleSubscription(
        GameAction action,
        string actionWitnessId,
        long actionSequence,
        string recordId,
        string? humanBoundActionId,
        Action<NativeActionLifecycleSubscription, string> observer,
        bool finishIsNativeCommit = true,
        ProcessLocalObservedAction? nativeSemanticSelection = null,
        string? semanticNativeActionType = null,
        string? humanNativeActionKey = null)
    {
        _action = action;
        ActionWitnessId = actionWitnessId;
        ActionSequence = actionSequence;
        RecordId = recordId;
        HumanBoundActionId = humanBoundActionId;
        HumanNativeActionKey = humanNativeActionKey;
        FinishIsNativeCommit = finishIsNativeCommit;
        NativeSemanticSelection = nativeSemanticSelection;
        SemanticNativeActionType = semanticNativeActionType;
        _observer = observer;
        _nativeObserver = new NativeActionLifecycleObserver(
            action,
            (_, phase) => _observer(this, phase));
    }

    internal RecorderRuntime.NestedUiInput? NestedInput { get; init; }

    internal SemanticActionReference? P1Input { get; init; }
    internal RecordedNativeInput? P1NativeInput { get; init; }
    internal DateTimeOffset P1AcceptedAt { get; init; }
    internal bool P1ExecutionCaptured { get; set; }

    internal GameAction Action => _action;
    internal string ActionWitnessId { get; }
    internal long ActionSequence { get; }
    internal string RecordId { get; }
    internal string? HumanBoundActionId { get; }
    internal string? HumanNativeActionKey { get; }
    internal bool FinishIsNativeCommit { get; }
    internal ProcessLocalObservedAction? NativeSemanticSelection { get; }
    internal string? SemanticNativeActionType { get; }
    internal string NativeActionType => _action.GetType().Name;
    internal uint? NativeQueueId => _action.Id;
    internal string NativeState => _action.State.ToString().ToLowerInvariant();

    public void Dispose()
    {
        if (_disposed)
            return;
        _disposed = true;
        _nativeObserver.Dispose();
    }
}
