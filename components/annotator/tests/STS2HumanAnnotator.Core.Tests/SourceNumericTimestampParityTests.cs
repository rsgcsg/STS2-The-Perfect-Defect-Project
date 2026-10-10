using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Core.Tests;

public sealed class SourceNumericTimestampParityTests
{
    private static readonly JsonSerializerOptions Json = new(EvidenceJson.Options) { DefaultIgnoreCondition = JsonIgnoreCondition.Never };
    private static byte[] Bytes(JsonNode value) => Encoding.UTF8.GetBytes(value.ToJsonString(Json) + "\n");
    private static SourceSessionAuditResult Audit(SourceSessionV2Tests.Fixture f, int version) =>
        version == 3 ? SourceSessionAuditV3.Audit(f.Store.DirectoryPath) : SourceSessionAuditV2.Audit(f.Store.DirectoryPath);

    [Theory]
    [InlineData(2)]
    [InlineData(3)]
    public void TypedSharedRawAuditorRejectsWrongNumericAndTimestampValuesWithOriginalHashesRepaired(int version)
    {
        foreach (string mutation in new[] { "float_version", "float_source_version", "bool_input_count", "float_epoch_count", "bool_stream_count",
            "invalid_created_at", "invalid_calendar", "invalid_offset", "utc_underflow", "null_recorded_at", "numeric_recorded_at", "bool_captured_at" })
        {
            using var f = new SourceSessionV2Tests.Fixture(version: version); var store = f.Store;
            store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity"));
            var input = store.ReserveSourceInputV2("typed-parity", new("title", "generation-title", "1"));
            if (version == 3) store.SealSourceInputOrderV3(input, new(SourceSessionContractV3.FrozenBasis, null));
            var payload = f.Payload("title"); store.BindSourceInputBasisV2(input, payload.Capture, payload.Catalog);
            store.CompleteSourceInputV2(input, new("exact", 1, SourceSessionV2Tests.Action, "fixture.native", "delivered", null, Array.Empty<SourceInputStageV2>()));
            f.Close("title", 1, new[] { new SourceNativeSealV2("title", "generation-title", "1", "1") });
            Assert.Equal("pass", Audit(f, version).Status);
            string file = "recording-manifest.json"; var value = Read(file);
            if (mutation == "float_version") value["schema_version"] = JsonValue.Create((double)version);
            else if (mutation == "float_source_version") value["source_schema_version"] = JsonValue.Create((double)version);
            else if (mutation is "bool_input_count" or "float_epoch_count" or "bool_stream_count")
            {
                file = "source-close-receipt.json"; value = Read(file);
                if (mutation == "bool_input_count") value["input_count"] = true;
                else if (mutation == "float_epoch_count") value["epoch_count"] = JsonValue.Create(1.0);
                else value["counts"]!["native-input-witnesses.jsonl"] = true;
            }
            else if (mutation is "null_recorded_at" or "numeric_recorded_at" or "bool_captured_at")
            {
                file = "native-input-witnesses.jsonl"; value = Read(file);
                if (mutation == "null_recorded_at") value["recorded_at"] = null;
                else if (mutation == "numeric_recorded_at") value["recorded_at"] = 1;
                else value["pre_capture"]!["captured_at"] = false;
            }
            else value["created_at"] = mutation switch {
                "invalid_calendar" => "2026-02-30T00:00:00Z", "invalid_offset" => "2026-10-09T00:00:00+14:01",
                "utc_underflow" => "0001-01-01T00:00:00+14:00", _ => "not-a-date" };
            // JsonNode writes integral doubles without a decimal. Preserve the
            // malformed JSON number token so this is the independent repro.
            string encoded = Encoding.UTF8.GetString(Bytes(value));
            if (mutation == "float_version") encoded = encoded.Replace($"\"schema_version\":{version}", $"\"schema_version\":{version}.0", StringComparison.Ordinal);
            else if (mutation == "float_source_version") encoded = encoded.Replace($"\"source_schema_version\":{version}", $"\"source_schema_version\":{version}.0", StringComparison.Ordinal);
            else if (mutation == "float_epoch_count") encoded = encoded.Replace("\"epoch_count\":1", "\"epoch_count\":1.0", StringComparison.Ordinal);
            using var malformed = JsonDocument.Parse(encoded);
            Assert.Equal(JsonValueKind.Object, malformed.RootElement.ValueKind);
            File.WriteAllBytes(PathOf(file), Encoding.UTF8.GetBytes(encoded));
            if (file.EndsWith(".jsonl", StringComparison.Ordinal))
            {
                var receipt = Read("source-close-receipt.json"); receipt["stream_sha256"]![file] = SourceSessionContract.Sha256(File.ReadAllBytes(PathOf(file)));
                File.WriteAllBytes(PathOf("source-close-receipt.json"), Bytes(receipt));
            }
            var audit = Audit(f, version); Assert.True(audit.Status == "fail", mutation + ":" + string.Join(",", audit.Errors));
            string PathOf(string name) => Path.Combine(store.DirectoryPath, name);
            JsonObject Read(string name) => JsonNode.Parse(File.ReadAllBytes(PathOf(name)))!.AsObject();
        }
    }

    [Theory]
    [InlineData(2)]
    [InlineData(3)]
    public void EverySuccessorEpochRequiresItsExactOriginalBoundaryAfterCountAndHashRepair(int version)
    {
        using var f = new SourceSessionV2Tests.Fixture(version: version); var store = f.Store;
        store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity"));
        var transition = new SourceNativeTransitionV2("setup-required", "setup_handoff", "native.setup",
            "title-continuity", "run-continuity", "new", null, null);
        store.AppendSourceEpochV2(store.AdmitSourceEpochV2(f.Epoch("run", "title", new("title", "generation-title", "1", "1"), transition, "run-continuity")));
        store.AppendPublicObservationV2(f.Packet("run", 1, "run-continuity"));
        f.Close("run", 1, new[] { new SourceNativeSealV2("title", "generation-title", "1", "1"), new("run", "generation-run", "1", "1") });
        Assert.Equal("pass", Audit(f, version).Status);
        string path = Path.Combine(store.DirectoryPath, "source-boundaries.jsonl");
        var boundaries = File.ReadLines(path).Select(row => JsonNode.Parse(row)!.AsObject())
            .Where(row => row["kind"]!.GetValue<string>() != "epoch_transition").ToArray();
        for (int i = 0; i < boundaries.Length; i++) boundaries[i]["sequence"] = i + 1;
        File.WriteAllBytes(path, boundaries.SelectMany(Bytes).ToArray());
        string receiptPath = Path.Combine(store.DirectoryPath, "source-close-receipt.json");
        var receipt = JsonNode.Parse(File.ReadAllBytes(receiptPath))!;
        receipt["counts"]!["source-boundaries.jsonl"] = boundaries.Length;
        receipt["stream_sha256"]!["source-boundaries.jsonl"] = SourceSessionContract.Sha256(File.ReadAllBytes(path));
        File.WriteAllBytes(receiptPath, Bytes(receipt));
        var audit = Audit(f, version); Assert.Equal("fail", audit.Status); Assert.Contains("source_epoch_boundary_accounting_incomplete", audit.Errors);
    }
}
