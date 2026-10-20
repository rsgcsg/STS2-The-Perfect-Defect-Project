using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Tests;

public sealed class P1NativeRecordingTests
{
    private static CurrentRecordingManifest Manifest() => new(2, CurrentRecordingContract.ManifestSchema,
        "session-p1-synthetic", "timeline-p1-synthetic", DateTimeOffset.UtcNow, "test", new string('a', 40), "test",
        P1NativeRecordingContract.ProfileId, EvidenceIdentity.Sha256Json(P1NativeRecordingContract.Profile),
        P1NativeRecordingContract.Profile.SupportedActionFamilies, P1NativeRecordingContract.Profile.NonClaims)
        { P1NativeSchemaVersion = 1, P1NativeOrigin = "agent_native_ui", CloseSchemaVersion = 1 };
    private static byte[] Basis()
    {
        var value = JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "p1-complete-combat.json")))!;
        return Encoding.UTF8.GetBytes(new JsonObject { ["schema"] = "sts2.player-environment/public-basis-1", ["revision"] = "1",
            ["capture_scope"] = "current_public_snapshot_and_advertised_read_instances", ["capture_status"] = "complete",
            ["snapshot"] = value["snapshot"]!.DeepClone(), ["reads"] = value["reads"]!.DeepClone(), ["missing"] = new JsonArray() }.ToJsonString());
    }
    private static JsonObject Decision(P1NativeBasisReference basis, JsonNode action, string id) => new() {
        ["kind"] = "decision_accepted", ["run_id"] = "run-synthetic", ["decision_id"] = "decision-" + id,
        ["action_witness_id"] = id, ["native_action_type"] = id == "play" ? "PlayCardAction" : "EndPlayerTurnAction",
        ["native_queue_id"] = id == "play" ? 1 : 2, ["native_mechanism"] = "game_action",
        ["basis"] = JsonSerializer.SerializeToNode(basis, EvidenceJson.Options), ["bound_action"] = action.DeepClone(),
        ["native_receipt"] = new JsonObject { ["kind"] = "game_action_accepted", ["carrier_ref"] = id } };

    [Fact]
    public void ProductionStoreAndPackerPreserveExactBasisAndTwoSemanticActionsWithoutPickupLabels()
    {
        string? exported = Environment.GetEnvironmentVariable("STS2_P1_FIXTURE_OUT");
        string root = exported ?? Path.Combine(Path.GetTempPath(), "p1-native-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var manifest = Manifest(); string directory;
            using (var store = RecordingSessionStore.Create(root, manifest, P1NativeRecordingContract.Profile))
            {
                directory = store.DirectoryPath;
                byte[] bytes = Basis(); string snapshot = JsonNode.Parse(bytes)!["snapshot"]!["snapshot_id"]!.GetValue<string>();
                var basis = store.PersistP1NativeBasis(snapshot, bytes);
                Assert.Equal(bytes, File.ReadAllBytes(Path.Combine(directory, basis.PayloadRef)));
                var actions = JsonNode.Parse(bytes)!["snapshot"]!["bound_actions"]!["actions"]!.AsArray();
                var play = actions.First(a => a!["verb"]!.GetValue<string>() == "play" && a["label"]!.GetValue<string>() == "Play Zap")!;
                var end = actions.First(a => a!["verb"]!.GetValue<string>() == "end_turn")!;
                store.AppendRunEvent(new(2, CurrentRecordingContract.RunJournalSchema, "event-1", manifest.SessionId,
                    "run-synthetic", manifest.TimelineId, 1, DateTimeOffset.UtcNow, "run_observed_in_progress", null, snapshot,
                    "Synthetic fixture only; no native execution or Human origin."));
                // Pickup preparation/cancellation has no accepted semantic append API call.
                foreach (var pair in new[] { ("play", play), ("end", end) })
                {
                    store.AppendP1NativeEvent(Decision(basis, pair.Item2, pair.Item1));
                    store.AppendP1NativeEvent(new JsonObject { ["kind"] = "lifecycle", ["run_id"] = "run-synthetic",
                        ["decision_id"] = "decision-" + pair.Item1, ["action_witness_id"] = pair.Item1, ["phase"] = "finished",
                        ["successor_basis"] = JsonSerializer.SerializeToNode(basis, EvidenceJson.Options), ["successor_missing_reason"] = null });
                }
                store.AppendP1NativeEvent(new JsonObject { ["kind"] = "observation", ["run_id"] = "run-synthetic",
                    ["reason"] = "native_decision_owner_ready", ["basis"] = JsonSerializer.SerializeToNode(basis, EvidenceJson.Options) });
            }
            var audit = P1NativeSessionBundlePacker.Audit(directory); Assert.Equal("pass", audit.Status); Assert.Equal(2, audit.AcceptedCount);
            var packed = P1NativeSessionBundlePacker.Pack(directory, "synthetic-worker", "p1-core-fixture", Path.Combine(root, "bundles"), new string('b', 40));
            Assert.Equal("packed", packed.Status);
            var outer = JsonNode.Parse(File.ReadAllText(Path.Combine(packed.BundleDirectory, "session-bundle-manifest.json")))!;
            Assert.False(outer["content_identity"]!["human_origin_attestation"]!["attested"]!.GetValue<bool>());
            Assert.Equal(packed.BundleContentId, EvidenceIdentity.Sha256File(Path.Combine(packed.BundleDirectory, "content-identity.json")));
            if (exported != null) File.WriteAllText(Path.Combine(root, "production-fixture-result.json"), JsonSerializer.Serialize(packed, EvidenceJson.IndentedOptions));
        }
        finally { if (exported == null) Directory.Delete(root, true); }
    }

    [Fact]
    public void AnUndeclaredLegacyTextInputAppendStillPermanentlyPreventsSuccessfulP1Close()
    {
        string root = Path.Combine(Path.GetTempPath(), "p1-native-undeclared-text-" + Guid.NewGuid().ToString("N"));
        try
        {
            var store = RecordingSessionStore.Create(root, Manifest(), P1NativeRecordingContract.Profile);
            string directory = store.DirectoryPath;
            Assert.Null(store.Manifest.TextInputSchemaVersion);
            Assert.False(HumanTextInputObservationContract.Supports(store.Manifest.TextInputSchemaVersion));
            // Manifest rejection precedes observation decoding, matching the real failed legacy ingress.
            var failure = Assert.Throws<InvalidDataException>(() => store.AppendHumanTextInputObservation(null!));
            Assert.Contains("not declared by this manifest", failure.Message);
            Assert.Equal("failed", store.GetSnapshot().AppendHealth);
            Assert.Throws<InvalidDataException>(() => store.Dispose());
            store.Dispose(); // Resource cleanup cannot turn a failed Close into a successful seal.
            Assert.False(store.GetSnapshot().Closed);
            Assert.Equal("failed", store.GetSnapshot().AppendHealth);
            Assert.True(File.Exists(Path.Combine(directory, "human-text-input-failure.json")));
            Assert.False(File.Exists(Path.Combine(directory, P1NativeRecordingContract.CloseFile)));
            Assert.False(File.Exists(Path.Combine(directory, "session-close-receipt.json")));
        }
        finally { if (Directory.Exists(root)) Directory.Delete(root, true); }
    }

    [Fact]
    public void DurablePrefixPreservesPendingOriginalBytesWhileTheSessionContinues()
    {
        string? exported = Environment.GetEnvironmentVariable("STS2_P1_PREFIX_FIXTURE_OUT");
        string root = exported ?? Path.Combine(Path.GetTempPath(), "p1-native-prefix-" + Guid.NewGuid().ToString("N"));
        try
        {
            P1NativePrefixResult prefix; string directory; byte[] prefixEvents;
            using (var store = RecordingSessionStore.Create(root, Manifest(), P1NativeRecordingContract.Profile))
            {
                directory = store.DirectoryPath; byte[] bytes = Basis(); var original = JsonNode.Parse(bytes)!;
                string snapshot = original["snapshot"]!["snapshot_id"]!.GetValue<string>();
                var basis = store.PersistP1NativeBasis(snapshot, bytes);
                store.AppendRunEvent(new(2, CurrentRecordingContract.RunJournalSchema, "prefix-journal-1", store.Manifest.SessionId,
                    "run-synthetic", store.Manifest.TimelineId, 1, DateTimeOffset.UtcNow, "run_observed_in_progress", null, snapshot,
                    "Synthetic durable prefix fixture; no native execution or Human origin."));
                store.AppendP1NativeEvent(Decision(basis, original["snapshot"]!["bound_actions"]!["actions"]![0]!, "play"));
                prefix = store.ExportP1NativePrefix();
                Assert.Equal(1, prefix.Watermark.PendingCount); Assert.Equal(1, prefix.Watermark.AcceptedCount);
                Assert.Equal(0, prefix.Watermark.TerminalCount); Assert.False(store.GetSnapshot().Closed);
                string raw = Path.Combine(prefix.PrefixDirectory, "raw");
                prefixEvents = File.ReadAllBytes(Path.Combine(raw, P1NativeRecordingContract.EventFile));
                Assert.Equal(prefixEvents.LongLength, prefix.Watermark.EventsByteCount);
                Assert.Equal(EvidenceIdentity.Sha256Bytes(prefixEvents), prefix.Watermark.EventsSha256);
                Assert.Equal(bytes, File.ReadAllBytes(Path.Combine(raw, basis.PayloadRef)));
                Assert.False(File.Exists(Path.Combine(raw, P1NativeRecordingContract.CloseFile)));
                Assert.False(File.Exists(Path.Combine(raw, "session-close-receipt.json")));
                var manifest = JsonNode.Parse(File.ReadAllBytes(prefix.ManifestPath))!;
                Assert.Equal("open", manifest["status"]!.GetValue<string>());
                Assert.Equal("durable_prefix", manifest["scope"]!.GetValue<string>());
                store.AppendP1NativeEvent(new JsonObject { ["kind"] = "lifecycle", ["run_id"] = "run-synthetic",
                    ["decision_id"] = "decision-play", ["action_witness_id"] = "play", ["phase"] = "finished",
                    ["successor_basis"] = JsonSerializer.SerializeToNode(basis, EvidenceJson.Options), ["successor_missing_reason"] = null });
            }
            Assert.Equal("pass", P1NativeSessionBundlePacker.Audit(directory).Status);
            Assert.Equal(2, File.ReadAllLines(Path.Combine(directory, P1NativeRecordingContract.EventFile)).Length);
            Assert.Equal(prefixEvents, File.ReadAllBytes(Path.Combine(prefix.PrefixDirectory, "raw", P1NativeRecordingContract.EventFile)));
            Assert.Equal(prefix.ManifestSha256, EvidenceIdentity.Sha256File(prefix.ManifestPath));
            if (exported != null) File.WriteAllText(Path.Combine(root, "production-prefix-result.json"), JsonSerializer.Serialize(prefix, EvidenceJson.IndentedOptions));
        }
        finally { if (exported == null && Directory.Exists(root)) Directory.Delete(root, true); }
    }

    [Fact]
    public void CloseGivesAnAcceptedUnfinishedActionAnExplicitUnknownDisposition()
    {
        string root = Path.Combine(Path.GetTempPath(), "p1-native-pending-" + Guid.NewGuid().ToString("N"));
        try
        {
            string directory;
            using (var store = RecordingSessionStore.Create(root, Manifest(), P1NativeRecordingContract.Profile))
            {
                directory = store.DirectoryPath; byte[] bytes = Basis(); var original = JsonNode.Parse(bytes)!;
                var basis = store.PersistP1NativeBasis(original["snapshot"]!["snapshot_id"]!.GetValue<string>(), bytes);
                store.AppendP1NativeEvent(Decision(basis, original["snapshot"]!["bound_actions"]!["actions"]![0]!, "play"));
            }
            var rows = File.ReadAllLines(Path.Combine(directory, P1NativeRecordingContract.EventFile)); Assert.Equal(2, rows.Length);
            var terminal = JsonNode.Parse(rows[1])!; Assert.Equal("unknown", terminal["phase"]!.GetValue<string>());
            Assert.Equal("native_terminal_unobserved", terminal["successor_missing_reason"]!.GetValue<string>());
        }
        finally { if (Directory.Exists(root)) Directory.Delete(root, true); }
    }
}
