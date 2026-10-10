using System.Text.Json;
using System.Text.Json.Nodes;
using STS2HumanAnnotator.Core;
using STS2PlatformLiveUi;
using Xunit;

namespace STS2PlatformLiveUiTests;

public sealed class PlatformModelPreparationV2Tests
{
    internal static RecordingApplicationStatus Source(string kind = "agent_protocol",
        RecordingLifecycleState state = RecordingLifecycleState.Recording) =>
        PlatformRecordingCommandsTests.Status() with {
            Lifecycle = PlatformRecordingCommandsTests.Status().Lifecycle with { State = state },
            Health = new("source_bridge", "healthy", "healthy", null, DateTimeOffset.UtcNow),
            SourceV2 = new("epoch-1", "segment-1", new(kind, "operator", "explicit-declaration", false),
                7, 3, 1, 1, 4, true, null),
            Environment = new(null!, null!, null!, "1.0.0", "game-1", "fingerprint", "exact", "modset")
        };
    internal static JsonObject Request(string? profile = "native-logical-v1", string schema = "sts2.policy-runtime/agent-session-status-1") => new()
    {
        ["schema"] = PlatformRecordingCommands.ModelRequestSchemaV2,
        ["runtime_instance_id"] = "game-1", ["recording_session_id"] = "recording-1", ["source_segment_id"] = "segment-1",
        ["command_id"] = Guid.NewGuid().ToString("D"),
        ["model_context"] = new JsonObject { ["runtime_status_schema"] = schema, ["runtime_run_id"] = "run-1",
            ["input_profile"] = profile, ["recovery_epoch"] = 19 }
    };
    internal static PlatformModelPreparationRequestV2 Read(JsonObject request)
    { using var document = JsonDocument.Parse(request.ToJsonString()); return PlatformRecordingCommands.ReadModelPreparationV2(document.RootElement); }

    [Theory]
    [InlineData(RecordingLifecycleState.Recording, true, "retained_agent_protocol")]
    [InlineData(RecordingLifecycleState.Paused, false, "paused")]
    public void CompatibleExplicitProtocolIsRetainedWithoutCloseOrAutomaticResume(RecordingLifecycleState state, bool ready, string disposition)
    {
        var status = Source(state: state);
        var result = PlatformCollectionHandoff.PrepareForModel(Read(Request()), () => "game-1", () => status,
            (_, _) => throw new Exception("no owner mutation admitted"));
        Assert.Equal(ready, result.ReadyForModel); Assert.Equal(disposition, result.RecordingDisposition);
        Assert.Equal(state.ToString().ToLowerInvariant(), result.Status.RecordingLifecycle);
        Assert.Equal("explicit-declaration", result.Status.SourceDeclaration!.DeclarationId);
        Assert.False(result.Status.SourceDeclaration.MachineVerifiable);
        Assert.False(PlatformCollectionHandoff.Ready(status)); // v1 Ready/Closed invariant is unchanged
    }

    [Fact]
    public void PausedProtocolIsNotDestroyedByKnownLegacyCloseOnlyContext()
    {
        var status = Source(state: RecordingLifecycleState.Paused);
        var result = PlatformCollectionHandoff.PrepareForModel(Read(Request(null, "sts2.policy-runtime/status-1")),
            () => "game-1", () => status, (_, _) => throw new Exception("paused source must not close"));
        Assert.False(result.ReadyForModel); Assert.Equal("paused", result.RecordingDisposition);
        Assert.Equal("paused", result.Status.RecordingLifecycle);
        Assert.Equal("agent_protocol", result.Status.SourceDeclaration!.SourceKind);
        Assert.False(PlatformCollectionHandoff.Ready(status));
    }

    [Theory]
    [InlineData("declared_human")]
    [InlineData("agent_native_ui")]
    [InlineData("unknown")]
    [InlineData("agent_protocol")]
    public void NonprotocolOrKnownLegacyInputClosesThroughTheCurrentOwner(string kind)
    {
        var current = Source(kind);
        var request = kind == "agent_protocol" ? Request(null, "sts2.policy-runtime/status-1") : Request();
        int commands = 0;
        var result = PlatformCollectionHandoff.PrepareForModel(Read(request), () => "game-1", () => current, (command, session) => {
            commands++; Assert.Equal(SourceSessionContractV2.CommandSchema, command.Schema);
            Assert.Equal(RecordingCommandKind.Close, command.Kind); Assert.Equal("recording-1", session);
            current = current with { Lifecycle = current.Lifecycle with { State = RecordingLifecycleState.Closing }, Closeout = new("closing", null, null, null) };
            return new(true, true, "closing", "owner pending", current.Lifecycle);
        });
        Assert.Equal(1, commands); Assert.False(result.ReadyForModel); Assert.Equal("close_pending", result.RecordingDisposition);
        Assert.Equal(kind, result.Status.SourceDeclaration!.SourceKind);
    }

    [Theory]
    [InlineData("accounting")]
    [InlineData("error")]
    [InlineData("declaration")]
    [InlineData("environment")]
    [InlineData("append")]
    [InlineData("source_missing")]
    public void UnsafeSourceStateBlocksWithoutDestroyingRecording(string defect)
    {
        var status = Source();
        status = defect switch {
            "accounting" => status with { SourceV2 = status.SourceV2! with { AccountingComplete = false } },
            "error" => status with { SourceV2 = status.SourceV2! with { Error = "disk_failed" } },
            "declaration" => status with { SourceV2 = status.SourceV2! with { Declaration = new("agent_protocol", "/private", "declaration", false) } },
            "environment" => status with { Environment = status.Environment! with { RuntimeInstanceId = "replacement-game" } },
            "append" => status with { Health = status.Health with { Append = "failed" } },
            _ => status with { SourceV2 = null }
        };
        var request = Request(); if (defect == "source_missing") request["source_segment_id"] = null;
        var result = PlatformCollectionHandoff.PrepareForModel(Read(request), () => "game-1", () => status, (_, _) => throw new Exception());
        Assert.False(result.ReadyForModel); Assert.Equal("blocked", result.RecordingDisposition);
    }

    [Theory]
    [InlineData("runtime")]
    [InlineData("session")]
    [InlineData("segment")]
    public void QueuedExactContextDriftRejectsBeforeRecorderMutation(string defect)
    {
        var request = Request(); request[defect switch { "runtime" => "runtime_instance_id", "session" => "recording_session_id", _ => "source_segment_id" }] = "stale";
        Assert.Throws<PlatformRecordingPreconditionException>(() => PlatformCollectionHandoff.PrepareForModel(Read(request),
            () => "game-1", () => Source(), (_, _) => throw new Exception()));
    }

    [Theory]
    [InlineData("null_native")]
    [InlineData("unknown_schema")]
    [InlineData("bool_epoch")]
    [InlineData("extra")]
    [InlineData("unknown_profile")]
    public void StrictDetachedContextRejectsBeforeDispatch(string defect)
    {
        var request = Request(); var context = (JsonObject)request["model_context"]!;
        if (defect == "null_native") context["input_profile"] = null;
        if (defect == "unknown_profile") context["input_profile"] = "unknown";
        if (defect == "unknown_schema") context["runtime_status_schema"] = "unknown";
        if (defect == "bool_epoch") context["recovery_epoch"] = true;
        if (defect == "extra") context["actor"] = "not authority";
        Assert.Throws<ArgumentException>(() => Read(request));
        Assert.Null(Read(Request(null, "sts2.policy-runtime/status-1")).ModelContext.InputProfile);
    }
}
