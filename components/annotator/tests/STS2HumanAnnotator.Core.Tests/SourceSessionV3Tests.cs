using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Core.Tests;

public sealed class SourceSessionV3Tests
{
    private static readonly JsonSerializerOptions Json = new(EvidenceJson.Options) { DefaultIgnoreCondition = JsonIgnoreCondition.Never };
    private static SourceNativePositionV2 Position(string epoch, int index) => new(epoch, "generation-" + epoch, index.ToString());
    private static SourceNativeSealV2 Seal(string epoch, int index) => new(epoch, "generation-" + epoch, index.ToString(), index.ToString());
    private static byte[] Bytes<T>(T value) => Encoding.UTF8.GetBytes(JsonSerializer.Serialize(value, Json) + "\n");
    private static void Frozen(RecordingSessionStore store, SourceInputTokenV2 token) =>
        store.SealSourceInputOrderV3(token, new(SourceSessionContractV3.FrozenBasis, null));
    private static void Complete(SourceSessionV2Tests.Fixture f, SourceInputTokenV2 token)
    {
        Frozen(f.Store, token); var payload = f.Payload(token.PrePosition.EpochId);
        f.Store.BindSourceInputBasisV2(token, payload.Capture, payload.Catalog);
        f.Store.CompleteSourceInputV2(token, new("exact", 1, SourceSessionV2Tests.Action, "fixture.native_input", "delivered", null, Array.Empty<SourceInputStageV2>()));
    }
    private static void SameCutSession(SourceSessionV2Tests.Fixture f)
    {
        var store = f.Store; store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity"));
        var first = store.ReserveSourceInputV2("input-a", Position("title", 1));
        Assert.Equal("1", first.InputPrefixOrdinal);
        store.AppendSourceBoundaryV2(store.AdmitSourceBoundaryV2("pause", Position("title", 1)));
        var actor = store.AdmitSourceDeclarationV2(new("declared_human", "actor-second", "declaration-second"), first.SegmentId, Position("title", 1));
        store.AppendSourceDeclarationV2(actor);
        store.AppendSourceBoundaryV2(store.AdmitSourceBoundaryV2("resume", Position("title", 1)));
        var second = store.ReserveSourceInputV2("input-b", Position("title", 1));
        Assert.Equal("2", second.InputPrefixOrdinal); Complete(f, second); Complete(f, first);
        var transition = new SourceNativeTransitionV2("setup-witness", "setup_handoff", "native.actual_setup",
            "title-continuity", "run-continuity", "new", null, null);
        store.AppendSourceEpochV2(store.AdmitSourceEpochV2(f.Epoch("run", "title", Seal("title", 1), transition, "run-continuity")));
        store.AppendPublicObservationV2(f.Packet("run", 1, "run-continuity"));
        var pending = store.ReserveSourceInputV2("input-c", Position("run", 1)); Assert.Equal("3", pending.InputPrefixOrdinal);
        f.Close("run", 1, new[] { Seal("title", 1), Seal("run", 1) });
    }
    [Fact]
    public void OriginalOrdinalsAndCutsSurviveSameWatermarkActorEpochAndOutOfOrderPersistence()
    {
        using var f = new SourceSessionV2Tests.Fixture(version: 3); SameCutSession(f);
        var inputs = File.ReadLines(Path.Combine(f.Store.DirectoryPath, "native-input-witnesses.jsonl"))
            .Select(line => JsonSerializer.Deserialize<SourceNativeInputWitnessV2>(line, Json)!).ToArray();
        Assert.Equal(new[] { "2", "1", "3" }, inputs.Select(x => x.InputPrefixOrdinal));
        Assert.Equal(new long[] { 1, 2, 3 }, inputs.Select(x => x.Sequence));
        Assert.Equal("unknown", inputs[2].Outcome.Delivery); Assert.Equal("capture_missing", inputs[2].Outcome.MappingStatus);
        Assert.Equal("unproven", inputs[2].BasisOrder!.Status);
        var receipt = JsonNode.Parse(File.ReadAllBytes(Path.Combine(f.Store.DirectoryPath, "source-close-receipt.json")))!;
        Assert.Equal("3", receipt["final_input_prefix_ordinal"]!.GetValue<string>());
        Assert.Equal("2", receipt["final_drains"]![0]!["after_input_ordinal"]!.GetValue<string>());
        Assert.Equal("3", receipt["final_drains"]![1]!["after_input_ordinal"]!.GetValue<string>());
        Assert.Equal("pass", SourceSessionAuditV3.Audit(f.Store.DirectoryPath).Status);
        Assert.Equal("fail", SourceSessionAuditV2.Audit(f.Store.DirectoryPath).Status);
        var result = SourceSessionBundlePackerV3.Pack(f.Store.DirectoryPath, "worker-fixture", "campaign-fixture", Path.Combine(f.Root, "bundle"), new string('a', 40));
        Assert.Equal("pass", result.Status);
    }
    [Theory]
    [InlineData("duplicate")]
    [InlineData("missing")]
    [InlineData("noncanonical")]
    [InlineData("actor")]
    [InlineData("epoch_seal")]
    [InlineData("drain")]
    [InlineData("pause")]
    [InlineData("close")]
    [InlineData("missing_order_reason")]
    [InlineData("unproven_capture")]
    public void HashRepairedMalformedOriginalOrdinalsAndFencesCannotPassTheSharedRawAuditor(string mutation)
    {
        using var f = new SourceSessionV2Tests.Fixture(version: 3); SameCutSession(f);
        Assert.Equal("pass", SourceSessionAuditV3.Audit(f.Store.DirectoryPath).Status);
        string file = "native-input-witnesses.jsonl";
        var inputs = Rows(file);
        if (mutation == "duplicate") inputs[1]["input_prefix_ordinal"] = "2";
        else if (mutation == "missing") inputs[1].Remove("input_prefix_ordinal");
        else if (mutation == "noncanonical") inputs[1]["input_prefix_ordinal"] = "01";
        else if (mutation == "actor") inputs[1]["segment_id"] = inputs[0]["segment_id"]!.DeepClone();
        else if (mutation == "missing_order_reason") inputs[1]["basis_order"]!.AsObject().Remove("reason_code");
        else if (mutation == "unproven_capture") inputs[1]["basis_order"] = new JsonObject { ["status"] = "unproven", ["reason_code"] = SourceSessionContractV3.OrderUnprovenReason };
        else if (mutation == "pause")
        {
            file = "source-boundaries.jsonl"; var rows = Rows(file); rows[1]["after_input_ordinal"] = "2"; WriteRows(file, rows);
        }
        else
        {
            var receipt = Read("source-close-receipt.json");
            if (mutation == "epoch_seal") receipt["sealed_epochs"]![0]!["after_input_ordinal"] = "3";
            else if (mutation == "drain") receipt["final_drains"]![0]!["after_input_ordinal"] = "3";
            else receipt["final_input_prefix_ordinal"] = "2";
            Write("source-close-receipt.json", receipt);
        }
        if (file == "native-input-witnesses.jsonl") WriteRows(file, inputs);
        var repaired = Read("source-close-receipt.json"); repaired["stream_sha256"]![file] = SourceSessionContract.Sha256(File.ReadAllBytes(Path.Combine(f.Store.DirectoryPath, file)));
        Write("source-close-receipt.json", repaired);
        Assert.Equal("fail", SourceSessionAuditV3.Audit(f.Store.DirectoryPath).Status);
        JsonObject Read(string name) => JsonNode.Parse(File.ReadAllBytes(Path.Combine(f.Store.DirectoryPath, name)))!.AsObject();
        void Write(string name, JsonNode value) => File.WriteAllBytes(Path.Combine(f.Store.DirectoryPath, name), Bytes(value));
        JsonObject[] Rows(string name) => File.ReadLines(Path.Combine(f.Store.DirectoryPath, name)).Select(line => JsonNode.Parse(line)!.AsObject()).ToArray();
        void WriteRows(string name, IEnumerable<JsonObject> rows) => File.WriteAllBytes(Path.Combine(f.Store.DirectoryPath, name), rows.SelectMany(Bytes).ToArray());
    }
    [Fact]
    public async Task OrderedInputAndFreezeAdmissionRemainFilesystemFreeWhileDiskGateIsHeld()
    {
        using var f = new SourceSessionV2Tests.Fixture(version: 3);
        object gate = typeof(RecordingSessionStore).GetField("_gate", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(f.Store)!;
        using var release = new ManualResetEventSlim(); var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var disk = Task.Run(() => { lock (gate) { entered.SetResult(); release.Wait(); } });
        await entered.Task.WaitAsync(TimeSpan.FromSeconds(2));
        SourceInputTokenV2 token;
        try
        {
            token = await Task.Run(() => { var issued = f.Store.ReserveSourceInputV2("held-disk", Position("title", 1)); Frozen(f.Store, issued); return issued; }).WaitAsync(TimeSpan.FromSeconds(2));
        }
        finally { release.Set(); await disk; }
        Assert.Equal("1", token.InputPrefixOrdinal); Complete(f, token);
        f.Store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity")); f.Close("title", 1, new[] { Seal("title", 1) });
        Assert.Equal("pass", SourceSessionAuditV3.Audit(f.Store.DirectoryPath).Status);
    }
    [Fact]
    public void OldSource2RawFieldsStayUnchangedAndOrderExtensionIsRejected()
    {
        using var f = new SourceSessionV2Tests.Fixture(); f.Store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity"));
        var token = f.Store.ReserveSourceInputV2("old-v2", Position("title", 1)); Assert.Null(token.InputPrefixOrdinal);
        Complete(f, token); f.Close("title", 1, new[] { Seal("title", 1) });
        string file = Path.Combine(f.Store.DirectoryPath, "native-input-witnesses.jsonl"); var row = JsonNode.Parse(File.ReadAllBytes(file))!.AsObject();
        Assert.False(row.ContainsKey("input_prefix_ordinal")); Assert.False(row.ContainsKey("basis_order"));
        Assert.Equal("pass", SourceSessionAuditV2.Audit(f.Store.DirectoryPath).Status);
        row["input_prefix_ordinal"] = "1"; File.WriteAllBytes(file, Bytes(row));
        var receiptPath = Path.Combine(f.Store.DirectoryPath, "source-close-receipt.json"); var receipt = JsonNode.Parse(File.ReadAllBytes(receiptPath))!;
        receipt["stream_sha256"]!["native-input-witnesses.jsonl"] = SourceSessionContract.Sha256(File.ReadAllBytes(file)); File.WriteAllBytes(receiptPath, Bytes(receipt));
        Assert.Equal("fail", SourceSessionAuditV2.Audit(f.Store.DirectoryPath).Status);
    }
}
