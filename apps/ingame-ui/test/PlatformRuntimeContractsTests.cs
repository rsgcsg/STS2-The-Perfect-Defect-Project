using System.Text.Json;
using Xunit;

namespace STS2PlatformLiveUi.Tests;

public sealed class PlatformRuntimeContractsTests
{
    private static readonly JsonSerializerOptions Options = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };

    private static Dictionary<string, object?> Native() => new()
    {
        ["schema"] = NativeAgentRuntimeStatus.CurrentSchema,
        ["runtime"] = new { version = "synthetic", code_sha256 = new string('a', 64) },
        ["run_id"] = "run-original", ["agent_manifest_sha256"] = new string('b', 64),
        ["agent"] = new { manifest_id = "manifest-original", agent_id = "agent-synthetic", agent_version = "1",
            provider = "fixture", architecture = "fixture", artifact_id = "package-original", artifact_sha256 = new string('c', 64),
            adapter = new { id = "adapter-synthetic", version = "1", protocol = "sts2.policy-runtime/agent-session-ndjson-1", code_sha256 = new string('d', 64) } },
        ["lifecycle"] = "running", ["mode"] = "human", ["controller"] = "released",
        ["autonomy_budget"] = new { max_submissions = 16, submissions_used = 0, max_policy_calls = 32, policy_calls_used = 0,
            deadline_ms = 60000, elapsed_ms = 0, state = "inactive", exhausted_reason = (string?)null, ended_reason = (string?)null },
        ["tainted"] = false, ["taint_reason"] = null, ["refreshing"] = false,
        ["invalidations"] = Array.Empty<string>(), ["errors"] = Array.Empty<string>(), ["environment"] = null,
        ["session"] = null, ["last_observation"] = null, ["last_directive"] = null, ["last_result"] = null,
        ["pending_request"] = null
    };

    private static IPlatformRuntimeStatus Parse(Dictionary<string, object?> status) =>
        JsonSerializer.Deserialize<IPlatformRuntimeStatus>(JsonSerializer.Serialize(status, Options), Options)!;

    [Fact]
    public void NativeJsonRetainsItsOperationalNamespaceWithoutLegacyPolicyOrReceipt()
    {
        IPlatformRuntimeStatus parsed = Parse(Native());
        NativeAgentRuntimeStatus native = Assert.IsType<NativeAgentRuntimeStatus>(parsed);
        Assert.Equal("run-original", native.RunId);
        Assert.Equal("agent-synthetic", native.Agent.AgentId);
        Assert.Equal("human", native.Mode);
        Assert.Null(native.Session);
        Assert.Null(native.LastResult);
        string roundTrip = JsonSerializer.Serialize<IPlatformRuntimeStatus>(native, Options);
        using JsonDocument document = JsonDocument.Parse(roundTrip);
        Assert.Equal(NativeAgentRuntimeStatus.CurrentSchema, document.RootElement.GetProperty("schema").GetString());
        Assert.False(document.RootElement.TryGetProperty("policy", out _));
        Assert.False(document.RootElement.TryGetProperty("last_receipt", out _));
    }

    [Theory]
    [InlineData("policy")]
    [InlineData("last_receipt")]
    [InlineData("last_decision")]
    [InlineData("private_model_state")]
    public void NativeNamespaceRejectsForeignOrPrivateFields(string field)
    {
        Dictionary<string, object?> status = Native();
        status[field] = new { id = "foreign" };
        Assert.Throws<JsonException>(() => Parse(status));
    }

    [Theory]
    [InlineData("agent_manifest_sha256")]
    [InlineData("pending_request")]
    [InlineData("session")]
    [InlineData("last_result")]
    public void NativeNamespaceRequiresEveryDeclaredFieldEvenWhenNullable(string field)
    {
        Dictionary<string, object?> status = Native();
        status.Remove(field);
        Assert.Throws<JsonException>(() => Parse(status));
    }

    [Fact]
    public void NativeNestedIdentityRejectsSilentMissingConstructorFields()
    {
        Dictionary<string, object?> status = Native();
        status["agent"] = new { manifest_id = "manifest-original" };
        Assert.Throws<JsonException>(() => Parse(status));
    }

    [Fact]
    public void NativeRoundTripPreservesRequiredNullFieldsUnderUiSerializerOptions()
    {
        IPlatformRuntimeStatus native = Parse(Native());
        var options = new JsonSerializerOptions(Options) { DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull };
        string encoded = JsonSerializer.Serialize(native, options);
        IPlatformRuntimeStatus? decoded = JsonSerializer.Deserialize<IPlatformRuntimeStatus>(encoded, options);
        Assert.IsType<NativeAgentRuntimeStatus>(decoded);
    }

    [Fact]
    public void OriginalPendingRequestRemainsDistinctFromResultAndNewAcquisition()
    {
        Dictionary<string, object?> status = Native();
        status["pending_request"] = new { request_id = "request-original", run_id = "run-original", runtime_instance_id = "game-original",
            session_id = "session-original", submission_epoch = 3, basis_acquisition_id = "basis-original",
            snapshot_id = "snapshot-original", action_id = "action-original", status = "unresolved", reason = "expired" };
        NativeAgentRuntimeStatus native = Assert.IsType<NativeAgentRuntimeStatus>(Parse(status));
        Assert.Null(native.LastResult);
        Assert.Equal("request-original", native.PendingRequest!.RequestId);
        Assert.Equal("basis-original", native.PendingRequest.BasisAcquisitionId);
        Assert.Equal("unresolved", native.PendingRequest.Status);
        Assert.Equal(3, native.PendingRequest.SubmissionEpoch);
    }

    [Fact]
    public void NativeFieldsCannotBeDisguisedAsLegacyPolicyStatus()
    {
        Dictionary<string, object?> status = Native();
        status["schema"] = PolicyRuntimeStatus.CurrentSchema;
        Assert.Throws<JsonException>(() => Parse(status));
    }

    [Fact]
    public void ReconciliationUsesExistingExplicitRecoveryLaneWithoutEnablingAuto()
    {
        Assert.True(PlatformNativeWorkbenchCommands.Recovery("models.reconcile"));
        Assert.False(PlatformNativeWorkbenchCommands.Recovery("models.takeover"));
    }
}
