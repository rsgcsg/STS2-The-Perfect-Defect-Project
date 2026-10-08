using System.Text.Json;
using System.Text.Json.Serialization;

namespace STS2PlatformLiveUi;

/// <summary>Shared operational controls; each profile retains its own evidence and result namespace.</summary>
[JsonConverter(typeof(PlatformRuntimeStatusConverter))]
public interface IPlatformRuntimeStatus
{
    string Schema { get; }
    PolicyRuntimeSoftware Runtime { get; }
    string RunId { get; }
    string Lifecycle { get; }
    string Mode { get; }
    string Controller { get; }
    bool Tainted { get; }
    string? TaintReason { get; }
    bool Refreshing { get; }
    PolicyRuntimeEnvironmentStatus? Environment { get; }
    IReadOnlyList<string> Invalidations { get; }
    IReadOnlyList<string> Errors { get; }
}

public sealed class PlatformRuntimeStatusConverter : JsonConverter<IPlatformRuntimeStatus>
{
    public override IPlatformRuntimeStatus Read(ref Utf8JsonReader reader, Type type, JsonSerializerOptions options)
    {
        using JsonDocument document = JsonDocument.ParseValue(ref reader);
        JsonElement root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty("schema", out JsonElement schema))
            throw new JsonException("Runtime status discriminator is required.");
        if (schema.GetString() == PolicyRuntimeStatus.CurrentSchema)
        {
            if (root.TryGetProperty("agent", out _) || root.TryGetProperty("pending_request", out _))
                throw new JsonException("Native Agent fields cannot enter a Policy status.");
            return root.Deserialize<PolicyRuntimeStatus>(options) ?? throw new JsonException("Policy status absent.");
        }
        if (schema.GetString() == NativeAgentRuntimeStatus.CurrentSchema)
        {
            string[] fields = ["schema", "runtime", "run_id", "agent_manifest_sha256", "agent", "lifecycle",
                "mode", "controller", "autonomy_budget", "tainted", "taint_reason", "refreshing",
                "invalidations", "errors", "environment", "session", "last_observation", "last_directive",
                "last_result", "pending_request"];
            if (root.EnumerateObject().Count() != fields.Length || fields.Any(field => !root.TryGetProperty(field, out _)))
                throw new JsonException("Native Agent status fields are incomplete or foreign.");
            var nativeOptions = new JsonSerializerOptions(options)
            {
                RespectRequiredConstructorParameters = true,
                UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow
            };
            return root.Deserialize<NativeAgentRuntimeStatus>(nativeOptions) ?? throw new JsonException("Agent status absent.");
        }
        throw new JsonException("Runtime status schema is unsupported.");
    }

    public override void Write(Utf8JsonWriter writer, IPlatformRuntimeStatus value, JsonSerializerOptions options)
    {
        switch (value)
        {
            case PolicyRuntimeStatus policy: JsonSerializer.Serialize(writer, policy, options); break;
            case NativeAgentRuntimeStatus agent:
                JsonSerializer.Serialize(writer, agent, new JsonSerializerOptions(options) { DefaultIgnoreCondition = JsonIgnoreCondition.Never });
                break;
            default: throw new JsonException("Unsupported operational Runtime status.");
        }
    }
}

public sealed record PolicyRuntimeHttpStatusResponse(
    [property: JsonPropertyName("schema")] string Schema,
    [property: JsonPropertyName("status")] IPlatformRuntimeStatus Status);

public sealed record PolicyRuntimeTickResponse(
    [property: JsonPropertyName("schema")] string Schema,
    [property: JsonPropertyName("results")] IReadOnlyList<PolicyRuntimeTickResult> Results,
    [property: JsonPropertyName("status")] IPlatformRuntimeStatus Status);

public sealed record PolicyRuntimeTickResult(
    [property: JsonPropertyName("type")] string Type,
    [property: JsonPropertyName("status")] IPlatformRuntimeStatus? Status);

public sealed record PolicyRuntimeStatus(
    [property: JsonPropertyName("schema")] string Schema,
    [property: JsonPropertyName("runtime")] PolicyRuntimeSoftware Runtime,
    [property: JsonPropertyName("policy")] PolicyRuntimePolicy Policy,
    [property: JsonPropertyName("run_id")] string RunId,
    [property: JsonPropertyName("lifecycle")] string Lifecycle,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("controller")] string Controller,
    [property: JsonPropertyName("tainted")] bool Tainted,
    [property: JsonPropertyName("taint_reason")] string? TaintReason,
    [property: JsonPropertyName("refreshing")] bool Refreshing,
    [property: JsonPropertyName("last_snapshot_id")] string? LastSnapshotId,
    [property: JsonPropertyName("last_snapshot")] PolicyRuntimeSnapshotStatus? LastSnapshot,
    [property: JsonPropertyName("last_decision")] PolicyRuntimeDecisionStatus? LastDecision,
    [property: JsonPropertyName("last_receipt")] PolicyRuntimeReceiptStatus? LastReceipt,
    [property: JsonPropertyName("reads")] IReadOnlyList<PolicyRuntimeReadStatus> Reads,
    [property: JsonPropertyName("invalidations")] IReadOnlyList<string> Invalidations,
    [property: JsonPropertyName("errors")] IReadOnlyList<string> Errors,
    [property: JsonPropertyName("environment")] PolicyRuntimeEnvironmentStatus? Environment) : IPlatformRuntimeStatus
{
    public const string CurrentSchema = "sts2.policy-runtime/status-1";
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record NativeAgentRuntimeStatus(
    [property: JsonPropertyName("schema")] string Schema,
    [property: JsonPropertyName("runtime")] PolicyRuntimeSoftware Runtime,
    [property: JsonPropertyName("agent")] NativeAgentRuntimeIdentity Agent,
    [property: JsonPropertyName("agent_manifest_sha256")] string AgentManifestSha256,
    [property: JsonPropertyName("run_id")] string RunId,
    [property: JsonPropertyName("lifecycle")] string Lifecycle,
    [property: JsonPropertyName("mode")] string Mode,
    [property: JsonPropertyName("controller")] string Controller,
    [property: JsonPropertyName("autonomy_budget")] JsonElement AutonomyBudget,
    [property: JsonPropertyName("tainted")] bool Tainted,
    [property: JsonPropertyName("taint_reason")] string? TaintReason,
    [property: JsonPropertyName("refreshing")] bool Refreshing,
    [property: JsonPropertyName("invalidations")] IReadOnlyList<string> Invalidations,
    [property: JsonPropertyName("errors")] IReadOnlyList<string> Errors,
    [property: JsonPropertyName("environment")] PolicyRuntimeEnvironmentStatus? Environment,
    [property: JsonPropertyName("session")] NativeAgentRuntimeSession? Session,
    [property: JsonPropertyName("last_observation")] NativeAgentObservationStatus? LastObservation,
    [property: JsonPropertyName("last_directive")] JsonElement? LastDirective,
    [property: JsonPropertyName("last_result")] NativeAgentResultStatus? LastResult,
    [property: JsonPropertyName("pending_request")] NativeAgentPendingRequest? PendingRequest) : IPlatformRuntimeStatus
{
    public const string CurrentSchema = "sts2.policy-runtime/agent-session-status-1";
}

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record NativeAgentRuntimeIdentity(
    [property: JsonPropertyName("manifest_id")] string ManifestId,
    [property: JsonPropertyName("agent_id")] string AgentId,
    [property: JsonPropertyName("agent_version")] string AgentVersion,
    [property: JsonPropertyName("provider")] string Provider,
    [property: JsonPropertyName("architecture")] string Architecture,
    [property: JsonPropertyName("artifact_id")] string ArtifactId,
    [property: JsonPropertyName("artifact_sha256")] string ArtifactSha256,
    [property: JsonPropertyName("adapter")] NativeAgentAdapterIdentity Adapter);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record NativeAgentAdapterIdentity(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("version")] string Version,
    [property: JsonPropertyName("protocol")] string Protocol,
    [property: JsonPropertyName("code_sha256")] string CodeSha256);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record NativeAgentRuntimeSession(
    [property: JsonPropertyName("session_id")] string SessionId,
    [property: JsonPropertyName("recovery_epoch")] long RecoveryEpoch,
    [property: JsonPropertyName("profile")] string Profile,
    [property: JsonPropertyName("input_spec")] JsonElement InputSpec,
    [property: JsonPropertyName("stream_generation")] string? StreamGeneration,
    [property: JsonPropertyName("continuity_token")] string ContinuityToken,
    [property: JsonPropertyName("state_version")] long StateVersion,
    [property: JsonPropertyName("consumption_id")] string? ConsumptionId,
    [property: JsonPropertyName("prefix")] JsonElement Prefix,
    [property: JsonPropertyName("agent_state")] string AgentState);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record NativeAgentObservationStatus(
    [property: JsonPropertyName("acquisition_id")] string AcquisitionId,
    [property: JsonPropertyName("capture_sha256")] string CaptureSha256,
    [property: JsonPropertyName("snapshot_id")] string SnapshotId,
    [property: JsonPropertyName("revision")] long Revision,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("publication_index")] string? PublicationIndex,
    [property: JsonPropertyName("included")] IReadOnlyList<string> Included,
    [property: JsonPropertyName("missing")] IReadOnlyList<string> Missing,
    [property: JsonPropertyName("catalog_count")] long? CatalogCount);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record NativeAgentResultStatus(
    [property: JsonPropertyName("request_id")] string RequestId,
    [property: JsonPropertyName("snapshot_id")] string SnapshotId,
    [property: JsonPropertyName("action_id")] string? ActionId,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("delivery")] string? Delivery,
    [property: JsonPropertyName("execution")] string? Execution,
    [property: JsonPropertyName("effect")] string? Effect,
    [property: JsonPropertyName("cancel")] string? Cancel,
    [property: JsonPropertyName("reason")] string? Reason);

[JsonUnmappedMemberHandling(JsonUnmappedMemberHandling.Disallow)]
public sealed record NativeAgentPendingRequest(
    [property: JsonPropertyName("request_id")] string RequestId,
    [property: JsonPropertyName("run_id")] string RunId,
    [property: JsonPropertyName("runtime_instance_id")] string RuntimeInstanceId,
    [property: JsonPropertyName("session_id")] string SessionId,
    [property: JsonPropertyName("submission_epoch")] long SubmissionEpoch,
    [property: JsonPropertyName("basis_acquisition_id")] string BasisAcquisitionId,
    [property: JsonPropertyName("snapshot_id")] string SnapshotId,
    [property: JsonPropertyName("action_id")] string ActionId,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("reason")] string? Reason);

public sealed record PolicyRuntimeSoftware(
    [property: JsonPropertyName("version")] string Version,
    [property: JsonPropertyName("code_sha256")] string? CodeSha256);

public sealed record PolicyRuntimePolicy(
    [property: JsonPropertyName("manifest_id")] string ManifestId,
    [property: JsonPropertyName("policy_id")] string PolicyId,
    [property: JsonPropertyName("policy_version")] string PolicyVersion,
    [property: JsonPropertyName("provider")] string Provider,
    [property: JsonPropertyName("architecture")] string Architecture,
    [property: JsonPropertyName("artifact_sha256")] string ArtifactSha256);

public sealed record PolicyRuntimeSnapshotStatus(
    [property: JsonPropertyName("snapshot_id")] string SnapshotId,
    [property: JsonPropertyName("sequence")] int Sequence,
    [property: JsonPropertyName("status")] string Status,
    [property: JsonPropertyName("runtime_instance_id")] string RuntimeInstanceId,
    [property: JsonPropertyName("environment_fingerprint")] string EnvironmentFingerprint);

public sealed record PolicyRuntimeDecisionStatus(
    [property: JsonPropertyName("decision_id")] string DecisionId,
    [property: JsonPropertyName("candidate_digest")] string CandidateDigest,
    [property: JsonPropertyName("candidate_count")] int CandidateCount,
    [property: JsonPropertyName("scores")] IReadOnlyList<double> Scores,
    [property: JsonPropertyName("selected_index")] int? SelectedIndex,
    [property: JsonPropertyName("bound_action_id")] string? BoundActionId,
    [property: JsonPropertyName("bound_action_label")] string? BoundActionLabel);

public sealed record PolicyRuntimeReceiptStatus(
    [property: JsonPropertyName("request_id")] string RequestId,
    [property: JsonPropertyName("delivery")] string Delivery,
    [property: JsonPropertyName("reason_code")] string? ReasonCode,
    [property: JsonPropertyName("successor_snapshot_id")] string? SuccessorSnapshotId);

public sealed record PolicyRuntimeReadStatus(
    [property: JsonPropertyName("read_id")] string ReadId,
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("content_schema")] string ContentSchema,
    [property: JsonPropertyName("target_referent_id")] string? TargetReferentId);

public sealed record PolicyRuntimeEnvironmentStatus(
    [property: JsonPropertyName("runtime_instance_id")] string RuntimeInstanceId,
    [property: JsonPropertyName("environment_fingerprint")] string EnvironmentFingerprint,
    [property: JsonPropertyName("host_kind")] string HostKind,
    [property: JsonPropertyName("connector_protocol_version")] string ConnectorProtocolVersion,
    [property: JsonPropertyName("connector_version")] string ConnectorVersion,
    [property: JsonPropertyName("connector_source_revision")] string? ConnectorSourceRevision,
    [property: JsonPropertyName("connector_artifact_sha256")] string? ConnectorArtifactSha256,
    [property: JsonPropertyName("connector_module_version_id")] string? ConnectorModuleVersionId,
    [property: JsonPropertyName("game_version")] string? GameVersion,
    [property: JsonPropertyName("game_commit")] string? GameCommit,
    [property: JsonPropertyName("modset_status")] string ModsetStatus,
    [property: JsonPropertyName("modset_fingerprint")] string ModsetFingerprint,
    [property: JsonPropertyName("loaded_mod_ids")] IReadOnlyList<string> LoadedModIds);
