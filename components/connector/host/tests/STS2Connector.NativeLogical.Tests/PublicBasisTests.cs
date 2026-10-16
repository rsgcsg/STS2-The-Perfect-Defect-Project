using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector.Tests;

public sealed class PublicBasisTests
{
    private static readonly JsonSerializerOptions Options = new() { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    private static JsonObject Snapshot() => new()
    {
        ["schema"] = PlayerEnvironmentContract.SnapshotSchema, ["snapshot_id"] = "snapshot-original",
        ["session"] = new JsonObject { ["runtime_instance_id"] = "runtime-original", ["environment_fingerprint"] = "fingerprint-original" },
        ["information_policy"] = new JsonObject { ["includes_hidden_information"] = false },
        ["completeness"] = new JsonObject { ["status"] = "complete", ["missing"] = new JsonArray() },
        ["reads"] = new JsonArray(new JsonObject { ["read_id"] = "read:deck", ["kind"] = "run_deck", ["target_referent_id"] = null,
            ["content_schema"] = "sts2.player-environment/read/run_deck-1" })
    };
    private static PublicBasisRead Read(JsonObject snapshot) => new("read:deck", "run_deck", new JsonObject
    {
        ["schema"] = PlayerEnvironmentContract.ReadSchema, ["read_id"] = "read:deck", ["kind"] = "run_deck",
        ["expected_snapshot_id"] = "snapshot-original", ["observed_snapshot_id"] = "snapshot-original",
        ["session"] = snapshot["session"]!.DeepClone(), ["information_policy"] = snapshot["information_policy"]!.DeepClone(),
        ["content_schema"] = "sts2.player-environment/read/run_deck-1", ["target_referent_id"] = null,
        ["completeness"] = new JsonObject { ["status"] = "complete", ["missing"] = new JsonArray() },
        ["content"] = new JsonObject { ["kind"] = "run_deck", ["card_count"] = 0, ["cards"] = new JsonArray() }
    }, null);

    [Fact]
    public void SharedRetentionOwnsExactDetachedBytesAndRejectsWrongProfile()
    {
        var snapshot = Snapshot(); var read = Read(snapshot);
        var frozen = PublicBasisAssembly.Freeze(snapshot, new[] { read }, "run-original", Options);
        string original = Encoding.UTF8.GetString(frozen.Bytes);
        var store = new SealedObservationStore(); var handle = store.Capture(() => frozen);
        snapshot["snapshot_id"] = "later"; read.Response!["content"]!["card_count"] = 99;
        var chunk = store.Read(handle.CaptureId, handle.FirstCursor, SealedObservationContract.DefaultChunkBytes, SealedObservationProfile.PublicBasis);
        Assert.Equal(original, Encoding.UTF8.GetString(Convert.FromBase64String(chunk.DataBase64)));
        Assert.Throws<SealedObservationException>(() => store.Read(handle.CaptureId, handle.FirstCursor,
            SealedObservationContract.DefaultChunkBytes, SealedObservationProfile.P1Capture));
        Assert.True(store.Release(handle.CaptureId, SealedObservationProfile.PublicBasis).Released);
    }

    [Theory]
    [InlineData("missing")]
    [InlineData("duplicate")]
    [InlineData("foreign_snapshot")]
    [InlineData("foreign_session")]
    [InlineData("foreign_context")]
    [InlineData("error_with_response")]
    [InlineData("ordering")]
    [InlineData("visibility")]
    [InlineData("duplicate_advertisement")]
    [InlineData("hidden_policy")]
    public void RejectsInventedOrMismatchedReadInventory(string variant)
    {
        var snapshot = Snapshot(); var read = Read(snapshot); var reads = new[] { read };
        if (variant == "missing") reads = Array.Empty<PublicBasisRead>();
        if (variant == "duplicate") reads = new[] { read, read };
        if (variant == "foreign_snapshot") read.Response!["observed_snapshot_id"] = "other";
        if (variant == "foreign_session") read.Response!["session"]!["runtime_instance_id"] = "other";
        if (variant == "foreign_context") read.Response!["target_referent_id"] = "different-card";
        if (variant == "ordering") read.Response!["ordering_semantics"] = "different";
        if (variant == "hidden_policy") read.Response!["completeness"]!["hidden_by_policy"] = new JsonArray("different");
        if (variant == "visibility") read.Response!["visibility_basis"] = "different";
        if (variant == "duplicate_advertisement")
        {
            ((JsonArray)snapshot["reads"]!).Add(snapshot["reads"]![0]!.DeepClone());
            reads = new[] { read, read with { ReadId = "extra" } };
        }
        if (variant == "error_with_response") reads = new[] { read with { Error = "failed" } };
        Assert.Throws<SealedObservationException>(() => PublicBasisAssembly.Freeze(snapshot, reads, null, Options));
    }

    [Fact]
    public void MissingNativeReadRemainsAnExplicitPartialSource()
    {
        var frozen = PublicBasisAssembly.Freeze(Snapshot(), new[] { new PublicBasisRead("read:deck", "run_deck", null, "native_unavailable") }, null, Options);
        var source = JsonNode.Parse(frozen.Bytes)!;
        Assert.Equal("partial", source["capture_status"]!.GetValue<string>());
        Assert.Equal("read:read:deck:native_unavailable", source["missing"]![0]!.GetValue<string>());
        Assert.Null(source["reads"]![0]!["response"]);
    }

    [Fact]
    public void ByteLimitIsEnforcedWhileSerializing()
    {
        var snapshot = Snapshot(); snapshot["large_public_text"] = new string('x', SealedObservationContract.MaxCapsuleBytes);
        Assert.Throws<SealedObservationException>(() => PublicBasisAssembly.Freeze(snapshot, new[] { Read(snapshot) }, null, Options));
    }
}
