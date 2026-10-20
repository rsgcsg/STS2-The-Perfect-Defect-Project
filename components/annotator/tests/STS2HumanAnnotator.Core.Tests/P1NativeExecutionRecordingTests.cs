using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Tests;

public sealed class P1NativeExecutionRecordingTests
{
    private static readonly DateTimeOffset At = DateTimeOffset.Parse("2026-10-19T00:00:00Z");
    private static SemanticActionReference Input(string type = "PlayCardAction", string target = "enemy") =>
        new("native-action", 1, "decision-native", "run-synthetic", type, 7, "human-start") {
            NativeInput = new(type == "EndPlayerTurnAction" ? "end_turn|-|" : "play|card|target=" + target,
                type == "EndPlayerTurnAction" ? "end_turn" : "play", type == "EndPlayerTurnAction" ? null : "card",
                type == "EndPlayerTurnAction" ? new Dictionary<string, string>() : new Dictionary<string, string> { ["target"] = target }, "original accepted"),
            NativeWitness = new("native_ui", type, type == "EndPlayerTurnAction" ? null : "exact-card-witness", new Dictionary<string, string>(), At),
            Mapping = new("exact_native_input", 1, "scoped_native_input_reference_equality", null)
        };
    private static ExecutionSemanticActionSpaceEvidence Space(SemanticActionReference input, string phase = "Play")
    {
        var native = input.NativeInput!;
        ExecutionSemanticAction[] actions = [new(native.ActionKey, native.Verb, native.SubjectReferentId, native.Arguments, "actual_native_validator_fixture")];
        var state = new JsonObject { ["player_phase"] = phase, ["energy"] = 2 };
        return new(3, ExecutionSemanticActionSpaceContract.Schema, input.ActionWitnessId, "before_execution", "captured", "combat_play_phase",
            EvidenceIdentity.Sha256Json(state), state, EvidenceIdentity.Sha256Json(actions.Select(action => action.Key).ToArray()), actions,
            native.ActionKey, "exact_once", 1, new[] { "native_validator_fixture" }, new[] { "synthetic_not_runtime" }, null) {
                HumanNativeActionKey = native.ActionKey };
    }
    private static JsonObject Capture(string snapshot) => new() { ["ui_catalog"] = new JsonObject { ["snapshot_id"] = snapshot } };
    private static P1NativeBasisReference Basis(string id = "execution-play") => new(id, new string('a', 64), 100, "original.json");

    [Theory]
    [InlineData("Start", 350)]
    [InlineData("InCardPlay", 25)]
    public void AcceptedStartAndInCardPlayQueuesUseFreshExecutionStateAndExactNativeInput(string humanReadiness, int interval)
    {
        var input = Input() with { HumanObservationSnapshotId = "human-" + humanReadiness };
        var row = P1NativeExecutionRecording.Create(input, At, At.AddMilliseconds(interval), Basis(), true, Space(input), null, Capture("execution-play"));
        Assert.Null(row["missing_reason"]); Assert.True(row["queued"]!.GetValue<bool>());
        Assert.Equal(interval, row["queue_interval_ms"]!.GetValue<double>());
        Assert.Equal("execution-play", row["basis"]!["snapshot_id"]!.GetValue<string>());
        Assert.Equal("Play", row["execution_action_space"]!["semantic_state"]!["player_phase"]!.GetValue<string>());
        Assert.Equal(input.NativeInput!.ActionKey, row["native_input"]!["action_key"]!.GetValue<string>());
        Assert.Null(row["bound_action"]);
    }

    [Fact]
    public void RetargetDeathAndMismatchedSnapshotRemainExplicitFailures()
    {
        var input = Input(); var original = Space(input);
        foreach (var invalid in new[] {
            original with { Actions = Array.Empty<ExecutionSemanticAction>(), ObservedMembership = "absent", ObservedMatchCount = 0 },
            original with { Actions = [original.Actions[0] with { Arguments = new Dictionary<string, string> { ["target"] = "changed" } }] }
        })
        {
            var row = P1NativeExecutionRecording.Create(input, At, At.AddMilliseconds(5), Basis(), true, invalid, null, Capture("execution-play"));
            Assert.NotNull(row["missing_reason"]);
            Assert.Equal("enemy", row["native_input"]!["arguments"]!["target"]!.GetValue<string>());
        }
        Assert.Contains("execution_capture_snapshot_mismatch", P1NativeExecutionRecording.Create(input, At, At, Basis(), true,
            original, null, Capture("different"))["missing_reason"]!.GetValue<string>());
    }

    [Fact]
    public void EndTurnNativeInputKeepsSubjectAbsentWithoutRelaxingCardSubjects()
    {
        var input = Input("EndPlayerTurnAction");
        Assert.Empty(RecordedNativeInputValidator.Validate(input));
        Assert.Empty(ExecutionSemanticActionSpaceValidator.Validate(Space(input), input));
        Assert.NotEmpty(RecordedNativeInputValidator.Validate(Input() with { NativeInput = Input().NativeInput! with { SubjectReferentId = null } }));
    }

    [Fact]
    public void ProductionStorePacksExecutionWithoutCountingItAsAnotherAcceptedAndPreservesPreExecutionCancel()
    {
        string? export = Environment.GetEnvironmentVariable("STS2_P1_EXECUTION_FIXTURE_OUT");
        string root = export ?? Path.Combine(Path.GetTempPath(), "p1-execution-" + Guid.NewGuid().ToString("N"));
        try
        {
            var profile = P1NativeRecordingContract.Profile;
            var manifest = new CurrentRecordingManifest(2, CurrentRecordingContract.ManifestSchema, "session-execution-synthetic", "timeline-execution",
                At, "test", new string('a', 40), "test", profile.ProfileId, EvidenceIdentity.Sha256Json(profile), profile.SupportedActionFamilies, profile.NonClaims) {
                P1NativeSchemaVersion = 1, P1NativeExecutionSchemaVersion = 1, P1NativeOrigin = "agent_native_ui", CloseSchemaVersion = 1 };
            string directory;
            using (var store = RecordingSessionStore.Create(root, manifest, profile))
            {
                directory = store.DirectoryPath;
                var fixture = JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "p1-complete-combat.json")))!;
                byte[] bytes = Encoding.UTF8.GetBytes(new JsonObject { ["schema"] = "sts2.player-environment/public-basis-1", ["revision"] = "1",
                    ["capture_scope"] = "current_public_snapshot_and_advertised_read_instances", ["capture_status"] = "complete",
                    ["snapshot"] = fixture["snapshot"]!.DeepClone(), ["reads"] = fixture["reads"]!.DeepClone(), ["missing"] = new JsonArray() }.ToJsonString());
                string snapshot = JsonNode.Parse(bytes)!["snapshot"]!["snapshot_id"]!.GetValue<string>();
                var basis = store.PersistP1NativeBasis(snapshot, bytes);
                var input = Input();
                store.AppendP1NativeEvent(new JsonObject { ["kind"] = "decision_accepted", ["run_id"] = input.RunId,
                    ["decision_id"] = input.RecordId, ["action_witness_id"] = input.ActionWitnessId, ["native_action_type"] = input.NativeActionType,
                    ["native_mechanism"] = "game_action", ["native_queue_id"] = 7, ["accepted_at"] = At,
                    ["basis"] = JsonSerializer.SerializeToNode(basis, EvidenceJson.Options), ["bound_action"] = null,
                    ["native_input"] = JsonSerializer.SerializeToNode(input.NativeInput, EvidenceJson.Options),
                    ["native_receipt"] = new JsonObject { ["kind"] = "game_action_accepted", ["carrier_ref"] = input.ActionWitnessId } });
                store.AppendP1NativeEvent(P1NativeExecutionRecording.Create(input, At, At.AddMilliseconds(250), basis, true, Space(input), null, Capture(snapshot)));
                store.AppendP1NativeEvent(new JsonObject { ["kind"] = "lifecycle", ["run_id"] = input.RunId, ["decision_id"] = input.RecordId,
                    ["action_witness_id"] = input.ActionWitnessId, ["phase"] = "cancelled", ["successor_basis"] = null,
                    ["successor_missing_reason"] = "native_cancel_after_start" });
                store.AppendP1NativeEvent(new JsonObject { ["kind"] = "decision_accepted", ["run_id"] = input.RunId,
                    ["decision_id"] = "decision-cancel", ["action_witness_id"] = "native-cancel", ["native_action_type"] = "PlayCardAction" });
                store.AppendP1NativeEvent(new JsonObject { ["kind"] = "lifecycle", ["run_id"] = input.RunId,
                    ["decision_id"] = "decision-cancel", ["action_witness_id"] = "native-cancel", ["phase"] = "cancelled",
                    ["successor_basis"] = null, ["successor_missing_reason"] = "cancelled_before_execution" });
                store.AppendP1NativeEvent(new JsonObject { ["kind"] = "issue", ["run_id"] = input.RunId,
                    ["reason"] = "accepted_native_action_ended_before_execution", ["native_witness"] = new JsonObject { ["action_witness_id"] = "native-cancel" } });
            }
            var result = P1NativeSessionBundlePacker.Pack(directory, "synthetic-execution", "execution-record-1", Path.Combine(root, "bundles"), new string('b', 40));
            Assert.Equal(2, result.CanonicalCount); // Execution is not another native acceptance.
            var rows = File.ReadAllLines(Path.Combine(directory, P1NativeRecordingContract.EventFile)).Select(line => JsonNode.Parse(line)!).ToArray();
            Assert.Single(rows, row => row["kind"]!.GetValue<string>() == "execution");
            Assert.DoesNotContain(rows, row => row["kind"]!.GetValue<string>() == "execution" && row["action_witness_id"]!.GetValue<string>() == "native-cancel");
            if (export != null) File.WriteAllText(Path.Combine(root, "production-fixture-result.json"), JsonSerializer.Serialize(result, EvidenceJson.IndentedOptions));
        }
        finally { if (export == null && Directory.Exists(root)) Directory.Delete(root, true); }
    }

    [Fact]
    public void ProductionPositiveExecutionFixtureUsesTheSameFullPublicAliasesWithAnEmptyDeliveryCatalog()
    {
        string? export = Environment.GetEnvironmentVariable("STS2_P1_EXECUTION_POSITIVE_OUT");
        string root = export ?? Path.Combine(Path.GetTempPath(), "p1-execution-positive-" + Guid.NewGuid().ToString("N"));
        try
        {
            var profile = P1NativeRecordingContract.Profile;
            var manifest = new CurrentRecordingManifest(2, CurrentRecordingContract.ManifestSchema, "session-execution-positive", "timeline-positive",
                At, "test", new string('a', 40), "test", profile.ProfileId, EvidenceIdentity.Sha256Json(profile), profile.SupportedActionFamilies, profile.NonClaims) {
                P1NativeSchemaVersion = 1, P1NativeExecutionSchemaVersion = 1, P1NativeOrigin = "agent_native_ui", CloseSchemaVersion = 1 };
            var fixture = JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "p1-complete-combat.json")))!;
            var human = new JsonObject { ["schema"] = "sts2.player-environment/public-basis-1", ["revision"] = "1",
                ["capture_scope"] = "current_public_snapshot_and_advertised_read_instances", ["capture_status"] = "complete",
                ["snapshot"] = fixture["snapshot"]!.DeepClone(), ["reads"] = fixture["reads"]!.DeepClone(), ["missing"] = new JsonArray() };
            var actions = human["snapshot"]!["bound_actions"]!["actions"]!.AsArray().Select(value => {
                string verb = value!["verb"]!.GetValue<string>(); string? subject = value["subject_referent_id"]?.GetValue<string>();
                var args = value["arguments"]!.AsArray().ToDictionary(argument => argument!["role"]!.GetValue<string>(),
                    argument => argument!["referent_id"]!.GetValue<string>(), StringComparer.Ordinal);
                string key = verb + "|" + (subject ?? "-") + "|" + string.Join(",", args.OrderBy(pair => pair.Key, StringComparer.Ordinal).Select(pair => pair.Key + "=" + pair.Value));
                return new ExecutionSemanticAction(key, verb, subject, args, "synthetic_logical_catalog_not_runtime");
            }).ToArray();
            var chosenPublic = human["snapshot"]!["bound_actions"]!["actions"]!.AsArray().First(value => value!["label"]!.GetValue<string>() == "Play Zap")!;
            var chosen = actions.Single(value => value.Verb == "play" && value.SubjectReferentId == chosenPublic["subject_referent_id"]!.GetValue<string>());
            string humanSnapshot = human["snapshot"]!["snapshot_id"]!.GetValue<string>(), executionSnapshot = humanSnapshot + "-execution";
            var execution = JsonNode.Parse(human.ToJsonString().Replace(humanSnapshot, executionSnapshot, StringComparison.Ordinal))!;
            execution["snapshot"]!["status"] = "settling"; execution["snapshot"]!["interaction"]!["stage"] = "settling";
            execution["snapshot"]!["bound_actions"]!["actions"] = new JsonArray();
            execution["snapshot"]!["bound_actions"]!["materialized_count"] = 0; execution["snapshot"]!["bound_actions"]!["total_count"] = 0;
            execution["snapshot"]!["interaction"]!["content"]!["surface"]!["can_end_turn"] = false;
            execution["snapshot"]!["interaction"]!["content"]!["surface"]!["playable_cards"] = new JsonArray();
            execution["snapshot"]!["interaction"]!["content"]!["surface"]!["usable_potions"] = new JsonArray();
            var input = Input() with { HumanObservationSnapshotId = humanSnapshot,
                NativeInput = new(chosen.Key, chosen.Verb, chosen.SubjectReferentId, chosen.Arguments, "Play Zap") };
            var space = Space(input) with { Actions = actions,
                SemanticCatalogDigest = EvidenceIdentity.Sha256Json(actions.Select(value => value.Key).Order(StringComparer.Ordinal).ToArray()) };
            string directory;
            using (var store = RecordingSessionStore.Create(root, manifest, profile))
            {
                directory = store.DirectoryPath;
                var h = store.PersistP1NativeBasis(humanSnapshot, Encoding.UTF8.GetBytes(human.ToJsonString()));
                var state = store.PersistP1NativeBasis(executionSnapshot, Encoding.UTF8.GetBytes(execution.ToJsonString()));
                store.AppendRunEvent(new(2, CurrentRecordingContract.RunJournalSchema, "journal-positive", manifest.SessionId, input.RunId,
                    manifest.TimelineId, 1, At, "run_observed_in_progress", null, humanSnapshot, "Synthetic producer fixture; no native or Human qualification."));
                store.AppendP1NativeEvent(new JsonObject { ["kind"] = "decision_accepted", ["run_id"] = input.RunId,
                    ["decision_id"] = input.RecordId, ["action_witness_id"] = input.ActionWitnessId, ["native_action_type"] = input.NativeActionType,
                    ["native_mechanism"] = "game_action", ["native_queue_id"] = 7, ["accepted_at"] = At,
                    ["basis"] = JsonSerializer.SerializeToNode(h, EvidenceJson.Options), ["bound_action"] = chosenPublic.DeepClone(), ["native_input"] = null,
                    ["native_witness"] = JsonSerializer.SerializeToNode(input.NativeWitness, EvidenceJson.Options),
                    ["native_receipt"] = new JsonObject { ["kind"] = "game_action_accepted", ["carrier_ref"] = input.ActionWitnessId }, ["missing_reason"] = null });
                store.AppendP1NativeEvent(P1NativeExecutionRecording.Create(input, At, At.AddMilliseconds(100), state, true, space, null, Capture(executionSnapshot)));
                store.AppendP1NativeEvent(new JsonObject { ["kind"] = "lifecycle", ["run_id"] = input.RunId, ["decision_id"] = input.RecordId,
                    ["action_witness_id"] = input.ActionWitnessId, ["phase"] = "finished",
                    ["successor_basis"] = JsonSerializer.SerializeToNode(h, EvidenceJson.Options), ["successor_missing_reason"] = null });
            }
            var packed = P1NativeSessionBundlePacker.Pack(directory, "synthetic-positive", "execution-positive-1", Path.Combine(root, "bundles"), new string('b', 40));
            Assert.Equal(1, packed.CanonicalCount);
            Assert.Equal("pass", P1NativeSessionBundlePacker.Audit(directory).Status);
            if (export != null) File.WriteAllText(Path.Combine(root, "production-fixture-result.json"), JsonSerializer.Serialize(packed, EvidenceJson.IndentedOptions));
        }
        finally { if (export == null && Directory.Exists(root)) Directory.Delete(root, true); }
    }
}
