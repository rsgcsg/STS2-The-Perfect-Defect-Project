using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Mod;

[Collection("P1 recorder runtime")]
public sealed class P1NativeSelectorTests
{
    [Theory]
    [InlineData("MerchantCardRemovalEntry.OnTryPurchaseWrapper")]
    [InlineData("RestSiteSynchronizer.ChooseLocalOption")]
    public void FalseParentIsCancelledOnlyWithExactAcceptedChildAndNormallyCompletedFalseTask(string kind)
    {
        var witness = new JsonObject { ["selector_owner_witness_id"] = "selector", ["parent_action_witness_id"] = "parent",
            ["parent_owner_witness_id"] = "owner" };
        var capture = new RecorderRuntime.P1SelectorCapture("child", "child-decision", "NativeSelector.Close.Released",
            new("snapshot", new string('a', 64), 1, "basis"), new JsonObject { ["verb"] = "cancel" }, witness);
        var proof = RecorderRuntime.P1ExplicitSelectorCancellation(capture, true, true);
        Assert.NotNull(proof);
        var completion = new NativePostCommitCompletion("session", 1, "completion", "family", kind, "task", false,
            ActionWitnessId: "parent", NativeOperandWitnessId: "owner");
        var task = new NativeTaskCompletion("session", 1, "completion", "task", false) {
            NativeTaskStatus = "RanToCompletion", NativeBooleanResult = false };
        Assert.Equal("cancelled", RecorderRuntime.P1UiCompletionPhase(completion, task, proof));
        Assert.Equal("unknown", RecorderRuntime.P1UiCompletionPhase(completion, task, null));
        Assert.Equal("unknown", RecorderRuntime.P1UiCompletionPhase(completion, task with { NativeTaskStatus = "Faulted" }, proof));
        Assert.Equal("unknown", RecorderRuntime.P1UiCompletionPhase(completion, task with { NativeTaskStatus = "Canceled" }, proof));
        Assert.Equal("unknown", RecorderRuntime.P1UiCompletionPhase(completion, task with { NativeBooleanResult = null }, proof));
        Assert.Equal("unknown", RecorderRuntime.P1UiCompletionPhase(completion with { ActionWitnessId = "other" }, task, proof));
        Assert.Equal("unknown", RecorderRuntime.P1UiCompletionPhase(completion with { NativeOperandWitnessId = "other" }, task, proof));
        Assert.Equal("finished", RecorderRuntime.P1UiCompletionPhase(completion with { Succeeded = true },
            task with { Succeeded = true, NativeBooleanResult = true }, null));
        Assert.Null(RecorderRuntime.P1ExplicitSelectorCancellation(capture, true, false));
        Assert.Null(RecorderRuntime.P1ExplicitSelectorCancellation(capture, false, true));
        Assert.Null(RecorderRuntime.P1ExplicitSelectorCancellation(capture with { BoundAction = new JsonObject { ["verb"] = "select" } }, true, true));
    }

    [Fact]
    public void ProductionSelectorSinkPreservesOriginalHAndExactNativeReceiptWithoutLegacyParentDecision()
    {
        string root = Path.Combine(Path.GetTempPath(), "p1-selector-" + Guid.NewGuid().ToString("N"));
        try
        {
            var profile = P1NativeRecordingContract.Profile;
            var manifest = new CurrentRecordingManifest(2, CurrentRecordingContract.ManifestSchema, "session-selector", "timeline-selector",
                DateTimeOffset.UtcNow, "test", new string('a', 40), "test", profile.ProfileId, EvidenceIdentity.Sha256Json(profile),
                profile.SupportedActionFamilies, profile.NonClaims) { P1NativeSchemaVersion = 1, P1NativeOrigin = "agent_native_ui", CloseSchemaVersion = 1 };
            string directory;
            byte[] original = Encoding.UTF8.GetBytes("{\"schema\":\"sts2.player-environment/public-basis-1\",\"capture_status\":\"complete\",\"missing\":[],\"snapshot\":{\"snapshot_id\":\"selector-prefix\",\"selected\":[]},\"reads\":[{\"original\":\"all-advertised-selector-details\"}]}");
            using (var store = RecordingSessionStore.Create(root, manifest, profile))
            {
                directory = store.DirectoryPath;
                RecorderRuntime.TestSession(store, profile.ProfileId);
                var basis = store.PersistP1NativeBasis("selector-prefix", original);
                var action = new JsonObject { ["bound_action_id"] = "select-exact-1", ["verb"] = "select", ["subject_referent_id"] = "selected-card" };
                var witness = new JsonObject { ["selector_owner_witness_id"] = "screen-1", ["selected_subject_witness_id"] = "card-1",
                    ["parent_action_witness_id"] = "p1-reward-claim-1", ["parent_owner_witness_id"] = "reward-1", ["factory_mechanism"] = "Reward.SelectUnsynchronized->ShowScreen" };
                var capture = new RecorderRuntime.P1SelectorCapture("selector-input-1", "selector-decision-1", "NCardRewardSelectionScreen.SelectCard", basis, action, witness);
                // No legacy parent decision or BoundaryTracker exists in this production partial test context.
                RecorderRuntime.TestSelectorAccepted(capture, new { native_callback = capture.Mechanism, terminal = true, native_acceptance_proven = true });
                action["bound_action_id"] = "later-not-original"; witness["selected_subject_witness_id"] = "later-not-original";
                store.AppendP1NativeEvent(new JsonObject { ["kind"] = "lifecycle", ["decision_id"] = capture.DecisionId,
                    ["action_witness_id"] = capture.ActionWitnessId, ["phase"] = "finished", ["successor_basis"] = null,
                    ["successor_missing_reason"] = "test_no_native_successor" });
            }
            var row = JsonNode.Parse(File.ReadAllLines(Path.Combine(directory, P1NativeRecordingContract.EventFile))[0])!;
            Assert.Equal("select-exact-1", row["bound_action"]!["bound_action_id"]!.GetValue<string>());
            Assert.Equal("card-1", row["native_witness"]!["selected_subject_witness_id"]!.GetValue<string>());
            Assert.Equal("p1-reward-claim-1", row["native_witness"]!["parent_action_witness_id"]!.GetValue<string>());
            Assert.Equal("selector-input-1", row["native_receipt"]!["carrier_ref"]!.GetValue<string>());
            Assert.True(row["native_receipt"]!["selector_callback"]!["native_acceptance_proven"]!.GetValue<bool>());
            Assert.Equal(original, File.ReadAllBytes(Path.Combine(directory, row["basis"]!["payload_ref"]!.GetValue<string>())));
            Assert.Equal("pass", P1NativeSessionBundlePacker.Audit(directory).Status);
            Assert.Empty(File.ReadAllBytes(Path.Combine(directory, "canonical-transitions.jsonl")));
            Assert.Empty(File.ReadAllBytes(Path.Combine(directory, "semantic-boundary-trace.jsonl")));
        }
        finally { RecorderRuntime.TestSession(null, null); if (Directory.Exists(root)) Directory.Delete(root, true); }
    }
}
