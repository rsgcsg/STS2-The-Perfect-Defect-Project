using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using STS2Connector.PlayerEnvironment.Protocol;

namespace STS2Connector.PlayerEnvironment.NativeLogical;

public sealed record NativeLogicalSourcePosition(string EpochId, string StreamGeneration, string PublicationIndex);
public sealed record NativeLogicalSourceSeal(string EpochId, string StreamGeneration, string ReservedThrough, string CompletedThrough);
public sealed record NativeLogicalSourceTransition(string WitnessId, string Kind, string Mechanism,
    string? PreviousContinuityId, string? ContinuityId, string? StartProvenance, bool? Graceful, bool? Victory);
public sealed record NativeLogicalSourceEpoch(string EpochId, string? PreviousEpochId, NativeLogicalSubscription Subscription,
    string PublicationProfileId, string PublicationProfileDefinitionSha256, string? ContinuityId,
    NativeLogicalSourcePosition StartingPosition, NativeLogicalSourcePosition InitialPosition,
    NativeLogicalSourceSeal? PredecessorSeal, NativeLogicalSourceTransition? Transition);
public sealed record NativeLogicalSourceBoundary(NativeLogicalSourcePosition Position, string CompletedThrough,
    long EncodingDeadlineMonotonicMs);
public sealed record NativeLogicalSourceInputPrefix(string RequestId, string ClientSessionId, string ActionId,
    NativeLogicalSourcePosition PrePosition, long EncodingDeadlineMonotonicMs, string NativeMechanism = "connector_native_logical_input");
public sealed record NativeLogicalSourceBasisOrder(string Status, string? ReasonCode);
public sealed record NativeLogicalSourceBasisMapping(string MappingStatus, int MatchCount, string? ActionId);
public sealed record NativeLogicalSourceInputTerminal(string Delivery, string? Reason, IReadOnlyList<NativeLogicalInputStage> Stages);

/// <summary>Passive typed sink. Prefix and native metadata callbacks must perform no I/O or native input.</summary>
public interface INativeLogicalSourceSink
{
    bool RequiresOrderedBasis => false;
    void Epoch(NativeLogicalSourceEpoch epoch);
    void Boundary(NativeLogicalSourceTransition transition, NativeLogicalSourcePosition position);
    object? AdmitInput(NativeLogicalSourceInputPrefix prefix);
    void InputBasis(object originalToken, string? captureId, IDisposable? retainedInput, string? missingReason);
    void InputFrozen(object originalToken, NativeLogicalSourceBasisOrder order) { }
    void InputMapping(object originalToken, NativeLogicalSourceBasisMapping mapping) { }
    void InputTerminal(object originalToken, NativeLogicalSourceInputTerminal terminal);
    void AccountingFailed(string code);
}

/// <summary>One passive source attachment; no Submit, controller, native operands, or dispatch callbacks.</summary>
public sealed class NativeLogicalSourceRecordingAttachment : IDisposable
{
    private readonly NativeLogicalService owner;
    internal readonly string RegistrationId, ClientId;
    internal INativeLogicalSourceSink? Sink;
    internal bool Closing, Disposed;
    internal readonly bool OrderedBasis;
    internal string? Failure;
    private readonly NativeLogicalSourceEpoch initialEpoch;
    internal NativeLogicalSourceEpoch Current;
    internal readonly Dictionary<string, NativeLogicalSourceEpoch> IssuedEpochs = new(StringComparer.Ordinal);
    internal NativeLogicalSourceRecordingAttachment(NativeLogicalService owner, string registrationId, string clientId,
        NativeLogicalSourceEpoch epoch, PlayerEnvironmentCapabilitiesResponse capabilities, string sourceDigest, bool orderedBasis)
    { OrderedBasis = orderedBasis; this.owner = owner; RegistrationId = registrationId; ClientId = clientId; Current = initialEpoch = epoch; Capabilities = capabilities; ConnectorSourceDigest = sourceDigest; IssuedEpochs.Add(epoch.EpochId, epoch); }
    public NativeLogicalSourceEpoch InitialEpoch => initialEpoch;
    public PlayerEnvironmentCapabilitiesResponse Capabilities { get; }
    public string ConnectorSourceDigest { get; }
    public void Activate(INativeLogicalSourceSink sink) => owner.ActivateSource(this, sink);
    public NativeLogicalSourceBoundary ReadCommandBoundary() => owner.ReadSourceCommandBoundary(this);
    public NativeLogicalSourceBoundary ReadBoundary() => owner.ReadSourceBoundary(this);
    public IReadOnlyList<NativeLogicalSourceSeal> Close() => owner.CloseSource(this);
    public NativeLogicalEventBatch Events(NativeLogicalSourceEpoch original, string afterCursor) => owner.SourceEvents(this, original, afterCursor);
    public NativeLogicalSourceBoundary ReadEpochBoundary(NativeLogicalSourceEpoch original) => owner.ReadSourceBoundary(this, original);
    public Task Wait(NativeLogicalSourceEpoch original, string afterCursor, CancellationToken cancellation) => owner.WaitSource(this, original, afterCursor, cancellation);
    public void Acknowledge(NativeLogicalSourceEpoch original, string cursor) => owner.AcknowledgeSource(this, original, cursor);
    public bool ReleaseEpoch(NativeLogicalSourceEpoch original) => owner.ReleaseSourceEpoch(this, original);
    public void Renew() => owner.RenewSource(this);
    public NativeLogicalSourceCopy CopyFrozen(string captureId) => owner.CopySourceFrozen(this, captureId);
    public void Dispose() => owner.DisposeSource(this);
}

/// <summary>Copy and parser scratch admission stays charged until its consumer finishes this exact work item.</summary>
public sealed class NativeLogicalSourceCopy : IDisposable
{
    private IDisposable? budget;
    private NativeLogicalFrozenInput? value;
    public NativeLogicalFrozenInput Value => value ?? throw new ObjectDisposedException(nameof(NativeLogicalSourceCopy));
    internal NativeLogicalSourceCopy(NativeLogicalFrozenInput value, IDisposable budget) { this.value = value; this.budget = budget; }
    public void Dispose() { value = null; Interlocked.Exchange(ref budget, null)?.Dispose(); }
}

public static class NativeLogicalSourceRecording
{
    // The recording application invokes this in its native command turn, before activating any sink.
    public static NativeLogicalSourceRecordingAttachment Attach(bool requireOrderedBasis = false) => PlayerEnvironmentService.NativeLogical.AttachSource(requireOrderedBasis);
}
