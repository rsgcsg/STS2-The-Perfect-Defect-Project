using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace STS2Connector.PlayerEnvironment.Protocol;

public static class NativeLogicalContract
{
    public const string Profile = "native-logical-v1";
    public const int MaxInputStages = 16;
    public const int MaxStageFieldBytes = 128;
    public const string ObservationSchema = "sts2.player-environment/native-logical-observation-1";
    public const string CaptureSchema = "sts2.player-environment/native-logical-capture-1";
    public const string EventSchema = "sts2.player-environment/native-logical-event-1";
    public const string CatalogPageSchema = "sts2.player-environment/native-logical-catalog-page-1";
    public const string ReadSchema = "sts2.player-environment/native-logical-read-1";
    public const string ResolveSchema = "sts2.player-environment/native-logical-resolve-1";
    public const string AttachSchema = "sts2.player-environment/native-logical-attach-1";
    public const string EventBatchSchema = "sts2.player-environment/native-logical-events-1";
    public const string AwaitSchema = "sts2.player-environment/native-logical-await-1";
    public const string CapabilitiesSchema = "sts2.player-environment/native-logical-capabilities-1";
    public const string ResultSchema = "sts2.player-environment/native-logical-result-1";
}

public sealed record NativeLogicalLimits(
    int MaxActions = 65536, int MaxCaptureBytes = 64 * 1024 * 1024,
    long MaxRetainedBytes = 512L * 1024 * 1024, int MaxEvents = 2048,
    int RetentionMs = 120000, int MaxReadBytes = 1024 * 1024,
    int MaxEncodedReadBytes = 2 * 1024 * 1024, int MaxPageBytes = 1024 * 1024,
    int MaxWaitMs = 30000, int MaxSubscriptions = 64, int MaxClientSubscriptions = 4,
    int MaxWaiters = 128, int MaxClientWaiters = 4, int MaxCaptures = 256,
    int MaxRetentionHandles = 1024, int MaxClientRetentionHandles = 64,
    int EncodingDeadlineMs = 2000, int MaxFieldBytes = 65536, int MaxCursorLength = 1024,
    int MaxWaitIdsPerSubscription = 65536, long MaxWaitIdMetadataBytes = 64L * 1024 * 1024,
    int MaxSourceSeams = 256, int MaxMetadataFieldBytes = 128, int MaxInFlightCatalogReads = 128);

public sealed record NativeLogicalArgument(string Role, string ReferentId);
public sealed record NativeLogicalAction(string ActionId, string Kind, string Verb, string Label,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] string? SubjectReferentId,
    IReadOnlyList<NativeLogicalArgument> Arguments, string EffectDomain);
public sealed record NativeLogicalLeaf(string Verb, string Label, string? SubjectReferentId,
    IReadOnlyList<NativeLogicalArgument> Arguments, string EffectDomain)
{
    [JsonIgnore] public string BindingKey { get; init; } = "";
}
public sealed record NativeLogicalCatalogDescriptor(string CatalogRef, string SnapshotId,
    string Status, long? TotalCount, string? Digest, string OrderingSemantics,
    IReadOnlyList<string> AccessMethods, string ScopeId, string StreamGeneration);
public sealed record NativeLogicalOwnerOccurrence(string OwnerId, string OccurrenceId,
    string BindingRevision, string? FocusReferentId, string? FocusOccurrence);
public sealed record NativeLogicalCompleteness(string Status, IReadOnlyList<string> Included,
    IReadOnlyList<string> Missing, bool FullReferenceComplete);
public sealed record NativeLogicalObservation(string ProtocolVersion, string Schema, string InputProfile,
    string SnapshotId, long Revision, DateTimeOffset ObservedAt, string Status,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] PlayerEnvironmentContent? Persistent,
    [property: JsonIgnore(Condition = JsonIgnoreCondition.Never)] PlayerEnvironmentInteraction? Interaction,
    IReadOnlyList<PlayerEnvironmentReferent> Referents, NativeLogicalCompleteness Completeness,
    PlayerEnvironmentSessionReference Session, PlayerEnvironmentInformationPolicy InformationPolicy,
    NativeLogicalOwnerOccurrence OwnerOccurrence, NativeLogicalCatalogDescriptor Catalog);
public sealed record NativeLogicalObservationContext(string ObservationRef, string CaptureRef,
    string? GameContinuityId, string StreamGeneration, string? PublicationCursor);
public sealed record NativeLogicalCapture(string Schema, string CaptureId, string SnapshotId,
    string InputProfile, PlayerEnvironmentSessionReference Session, string StreamGeneration,
    string ScopeId, string CaptureOrdinal, DateTimeOffset CapturedAt, DateTimeOffset ExpiresAt,
    int ByteCount, string Sha256, string ReadCursor);
public sealed record NativeLogicalReadChunk(string CaptureId, string Sha256, int Offset,
    int TotalBytes, string DataBase64, string? NextCursor, bool Complete, string Schema = NativeLogicalContract.ReadSchema);
public sealed record NativeLogicalCatalogPage(string Schema, string Status, string CatalogRef,
    string SnapshotId, string StreamGeneration, long TotalCount, string Digest,
    long FilteredCount, string FilteredDigest, IReadOnlyList<NativeLogicalAction> Actions,
    string? NextCursor, int? MinimumRequiredBytes);
public sealed record NativeLogicalResolve(string Status, NativeLogicalAction? Action, string Schema = NativeLogicalContract.ResolveSchema);
public sealed record NativeLogicalSeamCoverage(string SourceSeam, string Version, string Coverage);
public sealed record NativeLogicalAttachRequest(string ClientSessionId, IReadOnlyList<string> EagerScope,
    IReadOnlyList<NativeLogicalSeamCoverage> RequiredSeams, string DeliveryMode);
public sealed record NativeLogicalSubscription(string SubscriptionId, string ScopeId,
    IReadOnlyList<string> EagerScope, IReadOnlyList<NativeLogicalSeamCoverage> Coverage,
    string DeliveryMode, string StreamGeneration, string StartingCursor, DateTimeOffset ExpiresAt);
public sealed record NativeLogicalAttachReply(string Status, NativeLogicalSubscription? Subscription, string Schema = NativeLogicalContract.AttachSchema);
public sealed record NativeLogicalGap(string Reason, string FromPublicationIndex, string ThroughPublicationIndex);
public sealed record NativeLogicalEvent(string Schema, string Cursor, string StreamGeneration,
    string PublicationIndex, string Kind, string SourceSeam, string SourcePhase, string SourceIndex,
    string ScopeId, string? CaptureRef, string? MissingReason, string Coverage,
    NativeLogicalCapture? PayloadReference);
public sealed record NativeLogicalEventAvailability(NativeLogicalEvent Event, string Availability);
public sealed record NativeLogicalEventBatch(IReadOnlyList<NativeLogicalEventAvailability> Events,
    string NextCursor, string HighWatermark, string RetainedStartCursor, NativeLogicalGap? Gap,
    string Schema = NativeLogicalContract.EventBatchSchema);
public sealed record NativeLogicalControlBinding(string ControllerLeaseId, long ControllerGeneration);
public sealed record NativeLogicalAwaitReply(string Status, NativeLogicalEventAvailability? Event,
    NativeLogicalGap? Gap, string? Reason, string Schema = NativeLogicalContract.AwaitSchema);
public sealed record NativeLogicalInputStage(string Stage, string Delivery, string Evidence);
public sealed record NativeLogicalResult(string ProtocolVersion, string Schema, string InputProfile,
    string RequestId, string SnapshotId, NativeLogicalAction? Action, string Delivery,
    string Execution, string Effect, string Cancel, IReadOnlyList<NativeLogicalInputStage> Stages,
    string? Reason, string Retry,
    NativeLogicalObservationContext? ObservedFrame, PlayerEnvironmentAttribution? Attribution);

public sealed record NativeLogicalSourceClock(string PublicationIndexEncoding, string SourceIndexEncoding,
    string SourcePositionMeaning, string PublicationMeaning);
public sealed record NativeLogicalCapabilities(string ProtocolVersion, string Schema, string InputProfile,
    PlayerEnvironmentHostIdentity Host, PlayerEnvironmentGameIdentity Game, PlayerEnvironmentSessionReference Session,
    string StreamGeneration, IReadOnlyList<string> SupportedMethods, IReadOnlyList<string> ImplementedMechanisms,
    IReadOnlyList<NativeLogicalSeamCoverage> CaptureCoverage, NativeLogicalSourceClock SourceClock,
    NativeLogicalLimits Limits, PlayerEnvironmentControlPolicy ControlPolicy, IReadOnlyList<string> NonClaims);

public sealed record NativeLogicalRetentionReference(string RetentionHandleId, NativeLogicalCapture Capture,
    string ReadCursor, DateTimeOffset ExpiresAt);
