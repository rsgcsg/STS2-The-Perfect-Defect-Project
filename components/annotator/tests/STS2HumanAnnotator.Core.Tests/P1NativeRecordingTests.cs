using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Tests;

public sealed class P1NativeRecordingTests
{
    [Fact]
    public void InitialProgressOriginalRetainsRawBytesOnceAndAuditsTampering()
    {
        string root = Path.Combine(Path.GetTempPath(), "initial-progress-original-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            string directory;
            byte[] raw = { 255, 0, 13, 10, 123, 32, 125, 10 };
            var manifest = Manifest();
            using (var store = RecordingSessionStore.Create(root, manifest, P1NativeRecordingContract.Profile))
            {
                directory = store.DirectoryPath;
                Assert.Empty(InitialProgressOriginal.Audit(directory, manifest));
                store.TryPersistInitialProgressOriginal("run-0001", DateTimeOffset.UtcNow, 1, raw);
                store.TryPersistInitialProgressOriginal("run-0001", DateTimeOffset.UtcNow, 1, new byte[] { 1 });
                store.TryPersistInitialProgressOriginal("run-0002", DateTimeOffset.UtcNow, 1, null,
                    "resumed_run_initial_progress_not_observed");
            }
            var original = JsonSerializer.Deserialize<InitialProgressOriginal>(File.ReadAllBytes(
                Path.Combine(directory, "initial-progress", "run-0001.json")), EvidenceJson.Options)!;
            Assert.Equal("captured", original.Status);
            string payload = Path.Combine(directory, original.PayloadRef!);
            Assert.Equal(raw, File.ReadAllBytes(payload));
            Assert.Empty(InitialProgressOriginal.Audit(directory, manifest));
            var unknown = JsonSerializer.Deserialize<InitialProgressOriginal>(File.ReadAllBytes(
                Path.Combine(directory, "initial-progress", "run-0002.json")), EvidenceJson.Options)!;
            Assert.Equal("unknown", unknown.Status); Assert.Null(unknown.PayloadRef);
            Assert.Equal("resumed_run_initial_progress_not_observed", unknown.Reason);
            File.WriteAllBytes(payload, new byte[] { 1, 2 });
            Assert.Contains("initial_progress_original_invalid", InitialProgressOriginal.Audit(directory, manifest));
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void OptionalInitialProgressPersistenceAndDiagnosticFailuresCannotEscape()
    {
        string root = Path.Combine(Path.GetTempPath(), "initial-progress-io-failure-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            using var store = RecordingSessionStore.Create(root, Manifest(), P1NativeRecordingContract.Profile);
            File.WriteAllText(Path.Combine(store.DirectoryPath, "initial-progress"), "blocks-directory");
            bool diagnosed = false;
            Assert.False(store.TryPersistInitialProgressOriginal("run-0001", DateTimeOffset.UtcNow, 1,
                new byte[] { 1 }, reportFailure: error => { diagnosed = true; throw new IOException("diagnostic failed too"); }));
            Assert.True(diagnosed);
            Assert.Equal("healthy", store.GetSnapshot().AppendHealth);
        }
        finally { Directory.Delete(root, true); }
    }

    [Fact]
    public void P1BasisPersistenceDiagnosticUsesProductionStore()
    {
        string? output = Environment.GetEnvironmentVariable("STS2_P1_BASIS_DIAGNOSTIC_OUT");
        string root = Path.Combine(Path.GetTempPath(), "p1-basis-diagnostic-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            string? sample = Environment.GetEnvironmentVariable("STS2_P1_BASIS_DIAGNOSTIC_SAMPLE");
            var original = sample == null ? JsonNode.Parse(Basis())! : JsonNode.Parse(File.ReadAllBytes(sample))!;
            string directory; double appendMs;
            var elapsed = System.Diagnostics.Stopwatch.StartNew();
            long bytesWritten = 0;
            using (var store = RecordingSessionStore.Create(root, Manifest(), P1NativeRecordingContract.Profile))
            {
                directory = store.DirectoryPath;
                for (int index = 0; index < 128; index++)
                {
                    string snapshot = "diagnostic-snapshot-" + index;
                    original["snapshot"]!["snapshot_id"] = snapshot;
                    byte[] bytes = Encoding.UTF8.GetBytes(original.ToJsonString()); bytesWritten += bytes.Length;
                    var reference = store.PersistP1NativeBasis(snapshot, bytes);
                    Assert.Equal(reference, store.PersistP1NativeBasis(snapshot, bytes));
                }
                appendMs = elapsed.Elapsed.TotalMilliseconds;
            }
            elapsed.Stop();
            var profile = JsonNode.Parse(File.ReadAllText(Path.Combine(directory, "performance-profile.json")))!;
            Assert.Equal(128, Directory.GetFiles(Path.Combine(directory, "public-bases"), "*.json", SearchOption.AllDirectories).Length);
            Assert.True(File.Exists(Path.Combine(directory, P1NativeRecordingContract.CloseFile)));
            if (output != null) File.WriteAllText(output, JsonSerializer.Serialize(new { elapsed_ms = elapsed.Elapsed.TotalMilliseconds,
                append_ms = appendMs, close_ms = elapsed.Elapsed.TotalMilliseconds - appendMs,
                unique_basis_count = 128, persistence_calls = 256, unique_bytes = bytesWritten,
                profile }, EvidenceJson.IndentedOptions));
        }
        finally { Directory.Delete(root, true); }
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void ChangedPublicBasisCannotPublishDurablePrefixOrClose(bool prefix)
    {
        string root = Path.Combine(Path.GetTempPath(), "p1-basis-seal-failure-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        try
        {
            var store = RecordingSessionStore.Create(root, Manifest(), P1NativeRecordingContract.Profile);
            byte[] bytes = Basis(); string snapshot = JsonNode.Parse(bytes)!["snapshot"]!["snapshot_id"]!.GetValue<string>();
            var basis = store.PersistP1NativeBasis(snapshot, bytes);
            string directory = store.DirectoryPath;
            File.WriteAllText(Path.Combine(directory, basis.PayloadRef), "changed");
            if (prefix)
            {
                Assert.Throws<IOException>(() => store.ExportP1NativePrefix());
                Assert.Throws<InvalidDataException>(() => store.Dispose());
                Assert.False(Directory.Exists(Path.Combine(root, "p1-native-prefixes")));
            }
            else Assert.Throws<IOException>(() => store.Dispose());
            Assert.False(store.GetSnapshot().Closed);
            Assert.Equal("failed", store.GetSnapshot().AppendHealth);
            Assert.False(File.Exists(Path.Combine(directory, P1NativeRecordingContract.CloseFile)));
            Assert.False(File.Exists(Path.Combine(directory, "session-close-receipt.json")));
            store.Dispose();
        }
        finally { Directory.Delete(root, true); }
    }

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

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void InterruptedP1RecoveryKeepsOriginalPrefixAndOnlyClosesTheUnknownTail(bool torn)
    {
        string? exported = torn ? null : Environment.GetEnvironmentVariable("STS2_P1_RECOVERY_FIXTURE_OUT");
        string root = exported ?? Path.Combine(Path.GetTempPath(), "p1-recovery-" + Guid.NewGuid().ToString("N"));
        string recordings = Path.Combine(root, "recordings"), recovered = Path.Combine(root, "recovered-by-test");
        Directory.CreateDirectory(recordings);
        try
        {
            var manifest = Manifest() with { RecoverySchemaVersion = 1, ContinuousSchemaVersion = 1 };
            string directory; byte[] originalEvents, originalJournal;
            using (var store = RecordingSessionStore.Create(recordings, manifest, P1NativeRecordingContract.Profile))
            {
                directory = store.DirectoryPath; byte[] bytes = Basis(); var value = JsonNode.Parse(bytes)!;
                string snapshot = value["snapshot"]!["snapshot_id"]!.GetValue<string>();
                var basis = store.PersistP1NativeBasis(snapshot, bytes);
                store.AppendRunEvent(new(2, CurrentRecordingContract.RunJournalSchema, "recovery-journal-1", manifest.SessionId,
                    "run-synthetic", manifest.TimelineId, 1, DateTimeOffset.UtcNow, "run_observed_in_progress", null, snapshot,
                    "Synthetic interrupted recording, not Human or native game evidence."));
                var actions = value["snapshot"]!["bound_actions"]!["actions"]!.AsArray();
                store.AppendP1NativeEvent(Decision(basis, actions.First(a => a!["verb"]!.GetValue<string>() == "play")!, "play"));
                store.AppendP1NativeEvent(new JsonObject { ["kind"] = "lifecycle", ["run_id"] = "run-synthetic",
                    ["decision_id"] = "decision-play", ["action_witness_id"] = "play", ["phase"] = "finished",
                    ["successor_basis"] = JsonSerializer.SerializeToNode(basis, EvidenceJson.Options) });
                store.AppendP1NativeEvent(Decision(basis, actions.First(a => a!["verb"]!.GetValue<string>() == "end_turn")!, "end"));
                store.ExportP1NativePrefix(); // The normal writer's durable flush; no invented bytes.
                originalEvents = File.ReadAllBytes(Path.Combine(directory, P1NativeRecordingContract.EventFile));
                originalJournal = File.ReadAllBytes(Path.Combine(directory, "run-journal.jsonl"));
                var busy = JsonSerializer.SerializeToElement(InterruptedRecordingRecovery.RecoverOne(recordings, recovered));
                Assert.Equal(1, busy.GetProperty("busy").GetInt32());
            }
            // Simulate process loss between durable append and Close without
            // relying on Dispose's graceful unknown closure as crash evidence.
            File.WriteAllBytes(Path.Combine(directory, P1NativeRecordingContract.EventFile), originalEvents);
            File.WriteAllBytes(Path.Combine(directory, "run-journal.jsonl"), originalJournal);
            File.Delete(Path.Combine(directory, P1NativeRecordingContract.CloseFile));
            File.Delete(Path.Combine(directory, "session-close-receipt.json"));
            if (torn) File.AppendAllText(Path.Combine(directory, P1NativeRecordingContract.EventFile), "{\"kind\":");
            var before = Directory.GetFiles(directory, "*", SearchOption.AllDirectories).ToDictionary(p => p, EvidenceIdentity.Sha256File);
            var result = JsonSerializer.SerializeToElement(InterruptedRecordingRecovery.RecoverOne(recordings, recovered));
            foreach (var (path, hash) in before) Assert.Equal(hash, EvidenceIdentity.Sha256File(path));
            Assert.Equal(torn ? "incident" : "recovered", result.GetProperty("status").GetString());
            if (torn) { Assert.Empty(Directory.GetDirectories(recovered, "recovered-*")); return; }
            string copy = Assert.Single(Directory.GetDirectories(recovered, "recovered-*"));
            var rows = File.ReadAllLines(Path.Combine(copy, P1NativeRecordingContract.EventFile)).Select(line => JsonNode.Parse(line)!).ToArray();
            Assert.Equal(4, rows.Length); Assert.Equal("unknown", rows[3]["phase"]!.GetValue<string>());
            Assert.Equal("process_interrupted", rows[3]["reason"]!.GetValue<string>()); Assert.Null(rows[3]["successor_basis"]);
            Assert.Equal("pass", P1NativeSessionBundlePacker.Audit(copy).Status);
            var packed = P1NativeSessionBundlePacker.Pack(copy, "synthetic-recovery", "p1-recovery-fixture", Path.Combine(root, "bundles-by-test"), new string('b', 40));
            if (exported != null) File.WriteAllText(Path.Combine(root, "recovery-fixture-result.json"), JsonSerializer.Serialize(new {
                source = directory, recovered = copy, bundle = packed.BundleDirectory }, EvidenceJson.IndentedOptions));
            // Even a resealed stream hash cannot promote the offline tail to success.
            string eventFile = Path.Combine(copy, P1NativeRecordingContract.EventFile);
            File.WriteAllText(eventFile, File.ReadAllText(eventFile).Replace("\"phase\":\"unknown\"", "\"phase\":\"finished\""));
            var closePath = Path.Combine(copy, P1NativeRecordingContract.CloseFile);
            var close = JsonNode.Parse(File.ReadAllText(closePath))!;
            close["events_sha256"] = EvidenceIdentity.Sha256File(eventFile); File.WriteAllText(closePath, close.ToJsonString());
            Assert.Contains("recording_recovery_invalid", P1NativeSessionBundlePacker.Audit(copy).Errors);
        }
        finally { if (exported == null && Directory.Exists(root)) Directory.Delete(root, true); }
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
