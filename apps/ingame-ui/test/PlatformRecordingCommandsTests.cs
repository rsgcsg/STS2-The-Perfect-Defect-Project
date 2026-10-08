using System.Text.Json;
using System.Text.Json.Nodes;
using STS2HumanAnnotator.Core;
using STS2PlatformLiveUi;
using Xunit;

namespace STS2PlatformLiveUiTests;

public sealed class PlatformRecordingCommandsTests
{
    internal static RecordingApplicationStatus Status(string? profile = SourceSessionContractV3.ProfileId,
        RecordingLifecycleState state = RecordingLifecycleState.Recording) =>
        new(RecordingApplicationContract.StatusSchema, DateTimeOffset.UtcNow, 1,
            new(state, "recording-1", DateTimeOffset.UtcNow, "private detail"),
            profile is null ? null : new("recording-1", "timeline-1", "run-1", profile, "/private/recording", DateTimeOffset.UtcNow, null),
            new(0, 0, 0, 0), null, null, null,
            new("source_bridge", "healthy", "healthy", "/private/disk-error", DateTimeOffset.UtcNow), null!,
            new("idle", null, null, null), "source_recording", "private detail", null, null, [], 0)
        {
            SourceV2 = profile is SourceSessionContractV2.ProfileId or SourceSessionContractV3.ProfileId
                ? new("epoch-1", "segment-1", new("agent_native_ui", "operator", "declaration-1", false),
                    7, 3, 1, 1, 0, false, "/private/source-error") : null
        };

    internal static JsonObject Request(string kind = "start_new_session") => new()
    {
        ["schema"] = PlatformRecordingCommands.RequestSchema, ["runtime_instance_id"] = "game-1",
        ["recording_session_id"] = "recording-1", ["command"] = new JsonObject
        {
            ["schema"] = SourceSessionContractV2.CommandSchema, ["command_id"] = Guid.NewGuid().ToString("D"),
            ["kind"] = kind,
            ["capture_profile_id"] = kind == "start_new_session" ? SourceSessionContractV3.ProfileId : null,
            ["source_declaration"] = kind is "start_new_session" or "change_source" ? new JsonObject
            {
                ["source_kind"] = "agent_native_ui", ["actor_id"] = "operator",
                ["declaration_id"] = "explicit-declaration", ["machine_verifiable"] = false
            } : null,
            ["expected_source_segment_id"] = kind == "change_source" ? "segment-1" : null
        }
    };

    internal static PlatformRecordingRequest Read(JsonObject body)
    { using var json = JsonDocument.Parse(body.ToJsonString()); return PlatformRecordingCommands.Read(json.RootElement); }

    [Theory]
    [InlineData("start_new_session", RecordingCommandKind.StartNewSession)]
    [InlineData("pause", RecordingCommandKind.Pause)]
    [InlineData("resume", RecordingCommandKind.Resume)]
    [InlineData("change_source", RecordingCommandKind.ChangeSource)]
    [InlineData("close", RecordingCommandKind.Close)]
    public void ExactTypedLifecycleRetainsDeclaredSourceAndOriginalExpectation(string kind, RecordingCommandKind expected)
    {
        var request = Read(Request(kind));
        Assert.Equal("game-1", request.RuntimeInstanceId);
        Assert.Equal("recording-1", request.RecordingSessionId);
        Assert.Equal(expected, request.Command.Kind);
        Assert.Equal(SourceSessionContractV2.CommandSchema, request.Command.Schema);
        Assert.Equal(kind == "start_new_session" ? SourceSessionContractV3.ProfileId : null, request.Command.CaptureProfileId);
        Assert.Equal(kind == "change_source" ? "segment-1" : null, request.Command.ExpectedSourceSegmentId);
        if (kind is "start_new_session" or "change_source") Assert.False(request.Command.SourceDeclaration!.MachineVerifiable);
        else Assert.Null(request.Command.SourceDeclaration);
    }

    [Theory]
    [InlineData("schema")]
    [InlineData("extra")]
    [InlineData("kind")]
    [InlineData("profile")]
    [InlineData("command_id")]
    [InlineData("actor")]
    [InlineData("attestation")]
    [InlineData("missing_null")]
    public void StrictFieldsAndNoHumanAttestationAreEnforcedBeforeDispatch(string defect)
    {
        var body = Request(); var command = (JsonObject)body["command"]!;
        switch (defect)
        {
            case "schema": command["schema"] = RecordingApplicationContract.CommandSchema; break;
            case "extra": body["action"] = "not a recording command"; break;
            case "kind": command["kind"] = 0; break;
            case "profile": command["capture_profile_id"] = SourceSessionContractV2.ProfileId; break;
            case "command_id": command["command_id"] = "not-a-uuid"; break;
            case "actor": command["source_declaration"]!["actor_id"] = "/private/path"; break;
            case "attestation": command["source_declaration"]!["machine_verifiable"] = true; break;
            case "missing_null": command.Remove("expected_source_segment_id"); break;
        }
        Assert.Throws<ArgumentException>(() => Read(body));
    }

    [Fact]
    public void DuplicateAndInapplicableFieldsCannotOverrideTheExactCommand()
    {
        string duplicate = Request().ToJsonString().Replace("\"runtime_instance_id\":\"game-1\"",
            "\"runtime_instance_id\":\"old-game\",\"runtime_instance_id\":\"game-1\"", StringComparison.Ordinal);
        using var json = JsonDocument.Parse(duplicate);
        Assert.Throws<ArgumentException>(() => PlatformRecordingCommands.Read(json.RootElement));
        var pause = Request("pause"); pause["command"]!["capture_profile_id"] = SourceSessionContractV3.ProfileId;
        Assert.Throws<ArgumentException>(() => Read(pause));
        var change = Request("change_source"); change["command"]!["expected_source_segment_id"] = null;
        Assert.Throws<ArgumentException>(() => Read(change));
    }

    [Theory]
    [InlineData(null, RecordingApplicationContract.CommandSchema)]
    [InlineData("full-run-read-rich-v1", RecordingApplicationContract.CommandSchema)]
    [InlineData(SourceSessionContract.ProfileId, RecordingApplicationContract.SourceCommandSchema)]
    [InlineData(SourceSessionContractV2.ProfileId, SourceSessionContractV2.CommandSchema)]
    [InlineData(SourceSessionContractV3.ProfileId, SourceSessionContractV2.CommandSchema)]
    public void ProductionModelHandoffChoosesTheActiveOwnersSchemaAndWaitsForClose(string? profile, string schema)
    {
        var current = Status(profile);
        var after = PlatformCollectionHandoff.Prepare("recording-1", "original-command", () => current, (command, session) =>
        {
            Assert.Equal(schema, command.Schema);
            Assert.Equal("recording-1", session);
            Assert.Null(command.CaptureProfileId); Assert.Null(command.SourceDeclaration); Assert.Null(command.ExpectedSourceSegmentId);
            current = current with { Lifecycle = current.Lifecycle with { State = RecordingLifecycleState.Closing },
                Closeout = new("closing", DateTimeOffset.UtcNow, null, null) };
            return new(true, true, "closing", "pending", current.Lifecycle);
        });
        Assert.False(PlatformCollectionHandoff.Ready(after));
    }

    [Fact]
    public void StaleRuntimeNeverCallsOwnerAndStaleSessionRemainsOwnerRejected()
    {
        var request = Read(Request("close")); var status = Status(); int calls = 0;
        Assert.Throws<PlatformRecordingPreconditionException>(() => PlatformRecordingCommands.Execute(request,
            () => "new-game", () => status, (_, _) => { calls++; throw new Exception(); }));
        Assert.Equal(0, calls);
        status = status with { Lifecycle = status.Lifecycle with { SessionId = "new-session" } };
        var result = PlatformRecordingCommands.Execute(request, () => "game-1", () => status, (_, expected) =>
        {
            Assert.Equal("recording-1", expected); calls++;
            return new(false, false, "recording_session_changed", "private detail", status.Lifecycle);
        });
        Assert.False(result.Accepted); Assert.Equal("recording_session_changed", result.Code);
        Assert.Equal("new-session", result.Status.RecordingSessionId);
    }

    [Fact]
    public void PublicStatusHasOwnerSourceCountsAndSanitizedHealthWithoutPrivatePaths()
    {
        var result = PlatformRecordingCommands.Project("game-1", Status());
        Assert.Equal(7, result.Source!.Observations); Assert.Equal(3, result.Source.Inputs);
        Assert.False(result.Source.AccountingComplete);
        Assert.Equal("source_accounting_failed", result.Source.Error);
        Assert.Equal("recording_health_failed", result.Health.Error);
        Assert.False(result.Source.Declaration.MachineVerifiable);
        string json = JsonSerializer.Serialize(result);
        Assert.DoesNotContain("/private", json); Assert.DoesNotContain("private detail", json);
        Assert.Null(PlatformRecordingCommands.Project("game-1", Status("legacy-profile")).Source);
    }

    [Theory]
    [InlineData("D")]
    [InlineData("N")]
    [InlineData("B")]
    [InlineData("P")]
    public void OriginalModelPreparationIsDetachedBeforeQueueAndKeepsLegacyGuidForms(string format)
    {
        string id = Guid.NewGuid().ToString(format);
        PlatformModelPreparationRequest preparation;
        using (var document = JsonDocument.Parse(JsonSerializer.Serialize(new {
            runtime_instance_id = "game-1", recording_session_id = "recording-1", command_id = id })))
            preparation = PlatformRecordingCommands.ReadModelPreparation(document.RootElement);
        // This is the exact queue, with a disposed original JsonDocument before native dispatch.
        var queue = new STS2Connector.MainThreadWorkQueue();
        var current = Status("legacy-profile");
        var result = queue.Enqueue(() => {
            Assert.Equal("game-1", preparation.RuntimeInstanceId);
            return PlatformCollectionHandoff.Prepare(preparation.RecordingSessionId, preparation.CommandId,
                () => current, (command, expected) => {
                    Assert.Equal(id, command.CommandId); Assert.Equal("recording-1", expected);
                    current = current with { Lifecycle = current.Lifecycle with { State = RecordingLifecycleState.Closing },
                        Closeout = new("closing", null, null, null) };
                    return new(true, true, "closing", "pending", current.Lifecycle);
                });
        });
        Assert.False(result.IsCompleted); queue.Drain(1);
        Assert.False(PlatformCollectionHandoff.Ready(result.GetAwaiter().GetResult()));
    }
}
