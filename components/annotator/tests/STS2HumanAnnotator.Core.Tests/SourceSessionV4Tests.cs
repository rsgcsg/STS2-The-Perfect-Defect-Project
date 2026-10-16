using System.Text.Json;
using System.Text.Json.Nodes;
using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Core.Tests;

public sealed class SourceSessionV4Tests
{
    private static readonly JsonSerializerOptions Json = new(EvidenceJson.Options) { DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.Never };
    internal static FrozenPublicBasisV4 Basis(SourceSessionV2Tests.Fixture f, bool partial = false)
    {
        const string id = "semantic-snapshot-title"; // Distinct from the native UI snapshot, by design.
        var session = new JsonObject { ["runtime_instance_id"] = f.Environment.RuntimeInstanceId,
            ["environment_fingerprint"] = f.Environment.EnvironmentFingerprint };
        var policy = new JsonObject { ["includes_hidden_information"] = false };
        var opportunity = new JsonObject { ["read_id"] = "read:run_deck", ["kind"] = "run_deck",
            ["target_referent_id"] = null, ["content_schema"] = "sts2.player-environment/read/run_deck-1",
            ["ordering_semantics"] = "unordered", ["visibility_basis"] = "player_openable_deck", ["hidden_by_policy"] = new JsonArray() };
        var complete = new JsonObject { ["status"] = "complete", ["missing"] = new JsonArray(), ["hidden_by_policy"] = new JsonArray() };
        var snapshot = new JsonObject { ["schema"] = "sts2.player-environment/snapshot-1", ["protocol_version"] = "1.0.0",
            ["snapshot_id"] = id, ["session"] = session.DeepClone(), ["information_policy"] = policy.DeepClone(),
            ["completeness"] = complete.DeepClone(), ["reads"] = new JsonArray(opportunity) };
        var response = new JsonObject { ["schema"] = "sts2.player-environment/read-1", ["protocol_version"] = "1.0.0",
            ["read_id"] = "read:run_deck", ["kind"] = "run_deck", ["expected_snapshot_id"] = id, ["observed_snapshot_id"] = id,
            ["session"] = session, ["information_policy"] = policy, ["target_referent_id"] = null,
            ["content_schema"] = "sts2.player-environment/read/run_deck-1", ["ordering_semantics"] = "unordered",
            ["visibility_basis"] = "player_openable_deck", ["completeness"] = complete,
            ["content"] = new JsonObject { ["kind"] = "run_deck", ["cards"] = new JsonArray() } };
        var body = new JsonObject { ["schema"] = SourceSessionContractV4.PublicBasisSchema, ["revision"] = "1",
            ["capture_scope"] = SourceSessionContractV4.PublicBasisScope, ["capture_status"] = partial ? "partial" : "complete",
            ["snapshot"] = snapshot, ["reads"] = new JsonArray(new JsonObject { ["read_id"] = "read:run_deck", ["kind"] = "run_deck",
                ["response"] = partial ? null : response, ["error"] = partial ? "native_unavailable" : null }),
            ["missing"] = partial ? new JsonArray("read:read:run_deck:native_unavailable") : new JsonArray() };
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(body, Json);
        return new("title", id, "scope-title", "generation-title", DateTimeOffset.UnixEpoch, bytes, SourceSessionContract.Sha256(bytes));
    }
    // Production Store + packer, using the retained Reference PE shape. The map,
    // ordered empty piles and physical EndTurn witnesses below are synthetic.
    // This is not a real Human session or proof of native effects/successor.
    [Fact]
    public void ReferenceShapedP1PrefixPacksAllOriginalPublicationsAndExactEndTurn()
    {
        var action = new SourcePublicAction("native-end-turn", "native_input", "end_turn", "End turn",
            null, Array.Empty<SourceActionArgument>(), "native");
        using var f = new SourceSessionV2Tests.Fixture(version: 4, action: action);
        string root = AppContext.BaseDirectory;
        while (!Directory.Exists(Path.Combine(root, "components", "connector", "sdk", "typescript", "test", "fixtures", "p1-global")))
            root = Directory.GetParent(root)?.FullName ?? throw new InvalidOperationException("fixture_root_missing");
        string fixtures = Path.Combine(root, "components", "connector", "sdk", "typescript", "test", "fixtures", "p1-global");
        var node = JsonNode.Parse(File.ReadAllBytes(Path.Combine(fixtures, "reference-combat_turn.json")))!;
        var supplement = JsonNode.Parse(File.ReadAllBytes(Path.Combine(fixtures, "synthetic-complete-combat.json")))!;
        var map = supplement["reads"]!.AsArray().Single(r => r!["kind"]!.GetValue<string>() == "map")!.DeepClone();
        node["reads"]!.AsArray().Add(map);
        node["snapshot"]!["reads"]!.AsArray().Add(supplement["snapshot"]!["reads"]!.AsArray()
            .Single(r => r!["kind"]!.GetValue<string>() == "map")!.DeepClone());
        foreach (var zone in node["reads"]!.AsArray().Single(r => r!["kind"]!.GetValue<string>() == "combat_piles")!["response"]!["content"]!["zones"]!.AsArray())
            if (zone!["zone"]!.GetValue<string>() != "draw") zone["ordering_semantics"] = "native_visible_order";
        FrozenPublicBasisV4 Freeze(JsonNode current, int captureTick = 0)
        {
            var snapshot = current["snapshot"]!;
            snapshot["session"] = new JsonObject { ["runtime_instance_id"] = f.Environment.RuntimeInstanceId,
                ["environment_fingerprint"] = f.Environment.EnvironmentFingerprint };
            snapshot["observed_at"] = DateTimeOffset.UnixEpoch.AddMilliseconds(captureTick);
            foreach (var read in current["reads"]!.AsArray())
            {
                read!["response"]!["expected_snapshot_id"] = snapshot["snapshot_id"]!.DeepClone();
                read["response"]!["observed_snapshot_id"] = snapshot["snapshot_id"]!.DeepClone();
                read["response"]!["observed_at"] = snapshot["observed_at"]!.DeepClone();
                read["response"]!["session"] = snapshot["session"]!.DeepClone();
                read["response"]!["information_policy"] = snapshot["information_policy"]!.DeepClone();
            }
            var body = new JsonObject { ["schema"] = SourceSessionContractV4.PublicBasisSchema, ["revision"] = "1",
                ["capture_scope"] = SourceSessionContractV4.PublicBasisScope, ["capture_status"] = "complete",
                ["snapshot"] = snapshot.DeepClone(), ["reads"] = current["reads"]!.DeepClone(), ["missing"] = new JsonArray() };
            byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(body, Json);
            return new("title", snapshot["snapshot_id"]!.GetValue<string>(), "scope-title", "generation-title",
                DateTimeOffset.UnixEpoch.AddMilliseconds(captureTick), bytes, SourceSessionContract.Sha256(bytes));
        }
        void Publish(int index, FrozenPublicBasisV4 basis) => f.Store.AppendPublicObservationV2(
            f.Packet("title", index, "title-continuity") with { PublicBasis = basis });
        var basis = Freeze(node); Publish(1, basis); Publish(2, Freeze(node, 1));
        basis = Freeze(node, 2);
        var token = f.Store.ReserveSourceInputV2("physical-end-turn", new("title", "generation-title", "2"));
        f.Store.SealSourceInputOrderV3(token, new(SourceSessionContractV3.FrozenBasis, null));
        var native = f.Payload("title"); f.Store.BindSourceInputBasisV2(token, native.Capture, native.Catalog, basis);
        f.Store.CompleteSourceInputV2(token, new("exact", 1, action, "native_end_turn_ui", "delivered", null, Array.Empty<SourceInputStageV2>())
        { NativeFacts = new("native_input", "original-end-button", null, new Dictionary<string, string>(),
            new[] { new SourceNativeCarrierV4("game_action_submitted", "original-end-action"), new("game_action_accepted", "original-end-action") }, null) });
        var settling = node.DeepClone(); var snap = settling["snapshot"]!;
        snap["snapshot_id"] = "semantic-settling"; snap["status"] = "settling";
        snap["bound_actions"]!["actions"] = new JsonArray(); snap["bound_actions"]!["total_count"] = 0;
        snap["bound_actions"]!["materialized_count"] = 0; snap["interaction"]!["capabilities"] = new JsonArray();
        var surface = snap["interaction"]!["content"]!["surface"]!;
        surface["can_end_turn"] = false; surface["playable_cards"] = new JsonArray();
        Publish(3, Freeze(settling, 3));
        node["snapshot"]!["snapshot_id"] = "semantic-new-public-boundary";
        node["snapshot"]!["persistent"]!["content"]!["player"]!["gold"] = 100;
        Publish(4, Freeze(node, 4)); Publish(5, Freeze(node, 5));
        f.Close("title", 5, new[] { new SourceNativeSealV2("title", "generation-title", "5", "5") });
        var audit = SourceSessionAuditV4.Audit(f.Store.DirectoryPath);
        Assert.True(audit.Status == "pass", string.Join(",", audit.Errors));
        string bundle = Path.Combine(f.Root, "bundle");
        var packed = SourceSessionBundlePackerV4.Pack(f.Store.DirectoryPath, "worker-source4-p1", "campaign-synthetic-prefix", bundle, new string('a', 40));
        Assert.Equal("pass", packed.Status);
        string? golden = Environment.GetEnvironmentVariable("STS2_SOURCE_V4_P1_GOLDEN");
        if (golden != null)
            foreach (var path in Directory.EnumerateFiles(bundle, "*", SearchOption.AllDirectories))
            { string output = Path.Combine(golden, Path.GetRelativePath(bundle, path)); Directory.CreateDirectory(Path.GetDirectoryName(output)!); File.Copy(path, output, overwrite: true); }
    }

    private static void Close(SourceSessionV2Tests.Fixture f) =>
        f.Close("title", 1, new[] { new SourceNativeSealV2("title", "generation-title", "1", "1") });

    [Theory]
    [InlineData(null)]
    [InlineData(true)]
    public void CanceledNativeStageRemainsContextPublicationWithoutNewInputOrCommit(bool? noSubmission)
    {
        using var f = new SourceSessionV2Tests.Fixture(version: 4);
        var stage = new SourceNativeStageV4("card_play_canceled", "original-play", "original-card",
            new Dictionary<string, string> { ["hand"] = "original-hand" }, "native_finished_false", noSubmission);
        f.Store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity") with {
            SourceSeam = "native_input_callback", Phase = "card_play_canceled", PublicBasis = Basis(f), NativeStage = stage });
        Close(f); Assert.Equal("pass", SourceSessionAuditV4.Audit(f.Store.DirectoryPath).Status);
        var row = JsonSerializer.Deserialize<SourcePublicObservationV2>(File.ReadLines(Path.Combine(f.Store.DirectoryPath,
            "public-observations.jsonl")).Single(), Json)!;
        Assert.Equal(noSubmission, row.CapturedParts!.NativeStage!.NoSubmission);
        Assert.Empty(File.ReadAllLines(Path.Combine(f.Store.DirectoryPath, "native-input-witnesses.jsonl")));
    }
    [Theory]
    [InlineData("phase")]
    [InlineData("false")]
    [InlineData("evidence")]
    [InlineData("arguments")]
    public void CancelStageRejectsUnqualifiedOrWrongPublicationContext(string invalid)
    {
        using var f = new SourceSessionV2Tests.Fixture(version: 4);
        var stage = new SourceNativeStageV4("card_play_canceled", "original-play", "original-card",
            new Dictionary<string, string> { ["hand"] = "original-hand" }, "native_finished_false", null);
        if (invalid == "false") stage = stage with { NoSubmission = false };
        if (invalid == "evidence") stage = stage with { Evidence = "normal_return" };
        if (invalid == "arguments") stage = stage with { Arguments = new Dictionary<string, string>() };
        Assert.Throws<InvalidDataException>(() => f.Store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity") with {
            SourceSeam = "native_input_callback", Phase = invalid == "phase" ? "no_op" : "card_play_canceled",
            PublicBasis = Basis(f), NativeStage = stage }));
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void ExistingStorePersistsNeutralBytesWhenNativeIsCompleteOrMissingWithoutAnAgent(bool nativeMissing)
    {
        using var f = new SourceSessionV2Tests.Fixture(version: 4); var basis = Basis(f);
        var packet = f.Packet("title", 1, "title-continuity") with { PublicBasis = basis };
        if (nativeMissing) packet = packet with { Capture = null, Catalog = null, SnapshotId = null,
            OwnerOccurrence = null, Completeness = "failed", MissingReason = "native_mapping_unsupported" };
        f.Store.AppendPublicObservationV2(packet);
        var token = f.Store.ReserveSourceInputV2("physical-input", packet.Position);
        f.Store.SealSourceInputOrderV3(token, new(SourceSessionContractV3.FrozenBasis, null));
        f.Store.BindSourceInputBasisV2(token, packet.Capture, packet.Catalog, basis);
        f.Store.CompleteSourceInputV2(token, new(nativeMissing ? "capture_missing" : "exact", nativeMissing ? 0 : 1,
            nativeMissing ? null : SourceSessionV2Tests.Action, "fixture.actual_input", "delivered", null, Array.Empty<SourceInputStageV2>())
        { NativeFacts = new("native_input", "original-owner", "original-card", new Dictionary<string, string> { ["target"] = "original-target" },
            new[] { new SourceNativeCarrierV4("game_action_submitted", "carrier-original"), new("game_action_accepted", "carrier-original") }, null) });
        Close(f);
        var audit = SourceSessionAuditV4.Audit(f.Store.DirectoryPath); Assert.True(audit.Status == "pass", string.Join(",", audit.Errors));
        Assert.Equal("fail", SourceSessionAuditV3.Audit(f.Store.DirectoryPath).Status);
        var row = JsonSerializer.Deserialize<SourceNativeInputWitnessV2>(File.ReadLines(Path.Combine(f.Store.DirectoryPath, "native-input-witnesses.jsonl")).Single(), Json)!;
        Assert.Equal(basis.Bytes, File.ReadAllBytes(Path.Combine(f.Store.DirectoryPath, row.CapturedParts!.PublicBasis!.PayloadRef)));
        Assert.NotEqual(row.PreCapture?.SnapshotId, row.CapturedParts.PublicBasis.SnapshotId);
        Assert.Equal("delivered", row.Outcome.Delivery); Assert.Equal("original-card", row.Outcome.NativeFacts!.SubjectRef);
        string bundle = Path.Combine(f.Root, "bundle");
        Assert.Equal("pass", SourceSessionBundlePackerV4.Pack(f.Store.DirectoryPath, "worker-source4", "campaign-source4", bundle, new string('a', 40)).Status);
        Assert.Equal(basis.Bytes, File.ReadAllBytes(Path.Combine(bundle, "export", row.CapturedParts.PublicBasis.PayloadRef)));
        string? golden = Environment.GetEnvironmentVariable("STS2_SOURCE_V4_GOLDEN");
        if (golden != null && !nativeMissing)
        {
            Directory.CreateDirectory(golden);
            foreach (var path in Directory.EnumerateFiles(bundle, "*", SearchOption.AllDirectories))
            { string output = Path.Combine(golden, Path.GetRelativePath(bundle, path)); Directory.CreateDirectory(Path.GetDirectoryName(output)!); File.Copy(path, output, overwrite: true); }
        }
    }

    [Fact]
    public void PartialNeutralOriginalAndMissingNeutralPartRemainExplicitWithoutDiscardingNativeFacts()
    {
        using var f = new SourceSessionV2Tests.Fixture(version: 4);
        f.Store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity") with { PublicBasis = Basis(f, partial: true) });
        var token = f.Store.ReserveSourceInputV2("original-input", new("title", "generation-title", "1"));
        f.Store.SealSourceInputOrderV3(token, new(SourceSessionContractV3.FrozenBasis, null));
        var native = f.Payload("title"); f.Store.BindSourceInputBasisV2(token, native.Capture, native.Catalog, publicBasisMissingReason: "public_capture_overflow");
        f.Store.CompleteSourceInputV2(token, new("exact", 1, SourceSessionV2Tests.Action, "fixture.actual_input", "unknown", "original_unknown", Array.Empty<SourceInputStageV2>()));
        Close(f); Assert.Equal("pass", SourceSessionAuditV4.Audit(f.Store.DirectoryPath).Status);
        var row = JsonSerializer.Deserialize<SourceNativeInputWitnessV2>(File.ReadLines(Path.Combine(f.Store.DirectoryPath, "native-input-witnesses.jsonl")).Single(), Json)!;
        Assert.NotNull(row.PreCapture); Assert.Null(row.CapturedParts!.PublicBasis);
        Assert.Equal("public_capture_overflow", row.CapturedParts.PublicBasisMissingReason); Assert.Equal("unknown", row.Outcome.Delivery);
    }

    [Theory]
    [InlineData("read_snapshot")]
    [InlineData("read_missing")]
    [InlineData("read_duplicate")]
    [InlineData("read_order")]
    [InlineData("scope")]
    [InlineData("session")]
    [InlineData("status")]
    public void NeutralPartCannotBeSubstitutedAfterRepairingItsByteHash(string mutation)
    {
        using var f = new SourceSessionV2Tests.Fixture(version: 4); var original = Basis(f); var node = JsonNode.Parse(original.Bytes)!;
        if (mutation == "read_snapshot") node["reads"]![0]!["response"]!["observed_snapshot_id"] = "other";
        if (mutation == "read_missing") node["reads"]!.AsArray().Clear();
        if (mutation == "read_duplicate") node["reads"]!.AsArray().Add(node["reads"]![0]!.DeepClone());
        if (mutation == "read_order") node["reads"]![0]!["response"]!["ordering_semantics"] = "hidden_order";
        if (mutation == "session") node["snapshot"]!["session"]!["runtime_instance_id"] = "another-runtime";
        if (mutation == "status") node["capture_status"] = "partial";
        byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(node, Json);
        var changed = original with { Bytes = bytes, Sha256 = SourceSessionContract.Sha256(bytes), ScopeId = mutation == "scope" ? "other-scope" : original.ScopeId };
        Assert.Throws<InvalidDataException>(() => f.Store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity") with { PublicBasis = changed }));
    }
}
