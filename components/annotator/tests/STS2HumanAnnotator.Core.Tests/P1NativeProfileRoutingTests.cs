using System.Text.Json.Nodes;
using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Mod;

// The production profile-routing partial is compiled unchanged into this test;
// only the runtime's session fields are supplied without Game or UI dependencies.
internal static partial class RecorderRuntime
{
    private static string? _activeCaptureProfileId;
    private static RecordingSessionStore? _store;
    private static string _currentRunId = "run-profile-routing";

    internal static void TestSession(RecordingSessionStore? store, string? profile)
    { _store = store; _activeCaptureProfileId = profile; }
    internal static bool TestLegacyEntry(bool issue, string reason, object evidence) =>
        TryRouteP1LegacyRecord(issue, reason, "native-callback", evidence);
}

public sealed class P1NativeProfileRoutingTests
{
    [Fact]
    public void ActualProfileIngressKeepsMechanicalFactsAndUnsupportedWitnessesInDeclaredP1Stream()
    {
        string? exported = Environment.GetEnvironmentVariable("STS2_P1_ROUTING_FIXTURE_OUT");
        string root = exported ?? Path.Combine(Path.GetTempPath(), "p1-profile-routing-" + Guid.NewGuid().ToString("N"));
        try
        {
            var profile = P1NativeRecordingContract.Profile;
            var manifest = new CurrentRecordingManifest(2, CurrentRecordingContract.ManifestSchema, "session-routing", "timeline-routing",
                DateTimeOffset.UtcNow, "test", new string('a', 40), "test", profile.ProfileId, EvidenceIdentity.Sha256Json(profile),
                profile.SupportedActionFamilies, profile.NonClaims) { P1NativeSchemaVersion = 1, P1NativeOrigin = "agent_native_ui", CloseSchemaVersion = 1 };
            string directory;
            using (var store = RecordingSessionStore.Create(root, manifest, profile))
            {
                directory = store.DirectoryPath;
                RecorderRuntime.TestSession(store, profile.ProfileId);
                Assert.True(RecorderRuntime.TestLegacyEntry(false, "native_automatic_transition", new { seam = "owner-ready", witness = "original-mechanical-1" }));
                Assert.True(RecorderRuntime.TestLegacyEntry(true, "unsupported_native_choice", new { action_witness_id = "original-input-1", parent_action_witness_id = "original-parent-1" }));
                RecorderRuntime.TestSession(store, "full-run-read-rich-1");
                Assert.False(RecorderRuntime.TestLegacyEntry(true, "legacy_profile_keeps_its_owner", new { witness = "not-p1" }));
                Assert.Equal("healthy", store.GetSnapshot().AppendHealth);
            }
            var rows = File.ReadAllLines(Path.Combine(directory, P1NativeRecordingContract.EventFile)).Select(line => JsonNode.Parse(line)!).ToArray();
            Assert.Equal(2, rows.Length);
            Assert.Equal("observation", rows[0]["kind"]!.GetValue<string>());
            Assert.True(rows[0].AsObject().ContainsKey("basis")); Assert.Null(rows[0]["basis"]);
            Assert.Equal("native_diagnostic_not_decision_boundary", rows[0]["basis_missing_reason"]!.GetValue<string>());
            Assert.Equal("original-mechanical-1", rows[0]["native_witness"]!["witness"]!.GetValue<string>());
            Assert.Equal("issue", rows[1]["kind"]!.GetValue<string>());
            Assert.Equal("original-input-1", rows[1]["native_witness"]!["action_witness_id"]!.GetValue<string>());
            Assert.Equal("original-parent-1", rows[1]["native_witness"]!["parent_action_witness_id"]!.GetValue<string>());
            foreach (string stream in new[] { "invalidations.jsonl", "semantic-boundary-trace.jsonl", "canonical-transitions.jsonl", "native-semantic-discriminator.jsonl" })
                Assert.Empty(File.ReadAllBytes(Path.Combine(directory, stream)));
            Assert.False(File.Exists(Path.Combine(directory, HumanTextInputObservationContract.FileName)));
            Assert.Equal("pass", P1NativeSessionBundlePacker.Audit(directory).Status);
        }
        finally { RecorderRuntime.TestSession(null, null); if (exported == null && Directory.Exists(root)) Directory.Delete(root, true); }
    }
}
