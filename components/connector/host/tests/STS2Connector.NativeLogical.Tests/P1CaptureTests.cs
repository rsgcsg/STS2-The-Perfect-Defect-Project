using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector.Tests;

public sealed class P1CaptureTests
{
    private static readonly JsonSerializerOptions Options = new()
    { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower };
    private static readonly DateTimeOffset Timestamp = DateTimeOffset.Parse("2026-10-15T00:00:00+00:00");

    private static JsonObject Basis() => new()
    {
        ["protocol_version"] = "1.0.0", ["schema"] = PlayerEnvironmentContract.SnapshotSchema,
        ["snapshot_id"] = "state-fixture", ["sequence"] = 1, ["observed_at"] = "2026-10-15T00:00:00+00:00",
        ["status"] = "visible_unsupported", ["persistent"] = null,
        ["interaction"] = new JsonObject
        {
            ["interaction_id"] = "interaction-fixture", ["kind"] = "combat_turn", ["stage"] = "fixture",
            ["prompt"] = null, ["content_schema"] = "sts2.player-environment/surface/combat_turn-1",
            ["content"] = new JsonObject { ["surface"] = new JsonObject { ["kind"] = "combat_turn" },
                ["context"] = new JsonObject { ["kind"] = "combat" } }, ["capabilities"] = new JsonArray()
        },
        ["referents"] = new JsonArray(),
        ["bound_actions"] = new JsonObject
        {
            ["schema"] = "sts2.player-environment/bound-actions-1", ["status"] = "complete",
            ["materialized_count"] = 0, ["total_count"] = 0, ["limit"] = 512,
            ["ordering_semantics"] = "canonical_public_order", ["actions"] = new JsonArray()
        },
        ["reads"] = new JsonArray(),
        ["completeness"] = new JsonObject
        {
            ["status"] = "partial", ["visible_information"] = "synthetic_contract_fixture",
            ["interaction_discovery"] = "read_only", ["missing"] = new JsonArray("fixture_native_state"),
            ["hidden_by_policy"] = new JsonArray("hidden_rng", "draw_pile_true_order")
        },
        ["session"] = new JsonObject { ["runtime_instance_id"] = "runtime-fixture", ["environment_fingerprint"] = "environment-fixture" },
        ["information_policy"] = new JsonObject
        {
            ["id"] = "player_visible_v1", ["scope"] = "current_public_information",
            ["includes_hidden_information"] = false, ["unknown_field_behavior"] = "omit_and_mark_incomplete"
        }
    };

    private static P1CapturedRead[] Details(int count = 12) => Enumerable.Range(0, count).Select(index =>
        new P1CapturedRead("detail-" + index, "surface_card", null,
            // First two model one exact native object reused across contexts;
            // different native clones/definitions are never merged here.
            index < 2 ? "card-shared" : "card-" + index, index == 0 ? "deck" : "hand", null,
            "available", "sts2.player-environment/p1-current-card-display-c1-1",
            new JsonObject { ["entity_id"] = index < 2 ? "card-shared" : "card-" + index,
                ["definition_id"] = "STRIKE", ["name"] = "Strike", ["type"] = "Attack", ["cost"] = "1",
                ["rarity"] = "Basic", ["is_upgraded"] = false, ["is_selected"] = false,
                ["description"] = "public-" + index }, Array.Empty<string>())).ToArray();
    private static P1CaptureCoverage[] Coverage(P1CapturedRead[] reads) => reads.Select(read =>
        new P1CaptureCoverage("read_instance:" + read.InstanceId, true, read.Availability,
            new[] { read.InstanceId }, read.Missing)).ToArray();
    private static FrozenPublicObservation Freeze(JsonObject? basis = null, P1CapturedRead[]? reads = null,
        P1CaptureCoverage[]? coverage = null, bool supported = true)
    {
        reads ??= Details();
        return P1CaptureAssembly.Freeze(basis ?? Basis(), reads, coverage ?? Coverage(reads),
            supported, "private-run-metadata", Options, Timestamp);
    }
    private static byte[] Copy(SealedObservationStore store, SealedObservationCapture capture)
    {
        using var stream = new MemoryStream();
        string? cursor = capture.FirstCursor;
        while (cursor != null)
        {
            var chunk = store.Read(capture.CaptureId, cursor, 1024, SealedObservationProfile.P1Capture);
            Assert.Equal(P1CaptureContract.ChunkSchema, chunk.Schema);
            stream.Write(Convert.FromBase64String(chunk.DataBase64)); cursor = chunk.NextCursor;
        }
        return stream.ToArray();
    }

    [Fact]
    public void TwelveSameKindDetailsKeepDuplicateEntitiesDisplayContextsAndRequiredCoverage()
    {
        var frozen = Freeze(); var packet = JsonNode.Parse(frozen.Bytes)!;
        Assert.Equal(12, packet["reads"]!.AsArray().Count);
        Assert.Equal("card-shared", packet["reads"]![0]!["target_entity_id"]!.GetValue<string>());
        Assert.Equal("card-shared", packet["reads"]![1]!["target_entity_id"]!.GetValue<string>());
        Assert.Equal("deck", packet["reads"]![0]!["display_context"]!.GetValue<string>());
        Assert.Equal("hand", packet["reads"]![1]!["display_context"]!.GetValue<string>());
        Assert.Equal(24, packet["coverage"]!.AsArray().Count);
        Assert.False(packet["agent_admissible"]!.GetValue<bool>());
        Assert.False(packet["creates_mutation_authority"]!.GetValue<bool>());
        Assert.Equal("unsupported", packet["p1_status"]!.GetValue<string>());
        Assert.Equal("partial", packet["scope_status"]!.GetValue<string>());
        Assert.Equal(PlayerEnvironmentContract.SnapshotSchema, packet["native_basis"]!["schema"]!.GetValue<string>());
        Assert.DoesNotContain("private-run-metadata", Encoding.UTF8.GetString(frozen.Bytes));
        Assert.Equal("private-run-metadata", frozen.GameContinuityId);
    }

    [Fact]
    public void EveryReaderAndArchivalCopyUseFrozenBytesDespiteLaterJsonOrCallerBufferMutation()
    {
        var basis = Basis(); var reads = Details();
        FrozenPublicObservation frozen = Freeze(basis, reads);
        byte[] original = (byte[])frozen.Bytes.Clone(); int captures = 0;
        var store = new SealedObservationStore();
        var capture = store.Capture(() => { captures++; return frozen; });
        basis["snapshot_id"] = "changed";
        reads[0].Content!["description"] = "changed-after-main-thread-return";
        frozen.Bytes[0] ^= 1; // Even the caller's byte buffer is not the store's copy.
        Assert.Equal(original, Copy(store, capture));
        Assert.Equal(original, Copy(store, capture));
        Assert.Equal(1, captures); Assert.Equal(1, store.CaptureCount);
        Assert.Equal(P1CaptureContract.CaptureSchema, capture.Schema);
        Assert.Equal(P1CaptureContract.Profile, capture.InputProfile);
        Assert.DoesNotContain("changed-after-main-thread-return", Encoding.UTF8.GetString(Copy(store, capture)));
    }

    [Fact]
    public void ChangedReadPayloadCannotReuseOldDigestEvenWhenSnapshotAndDescriptorsAreIdentical()
    {
        var reads = Details(); var store = new SealedObservationStore();
        var first = store.Capture(() => Freeze(reads: reads));
        byte[] original = Copy(store, first);
        reads[0].Content!["description"] = "current-payload-changed";
        var second = store.Capture(() => Freeze(reads: reads));
        Assert.Equal(first.SourceSnapshotId, second.SourceSnapshotId);
        Assert.NotEqual(first.Sha256, second.Sha256);
        Assert.NotEqual(first.CaptureId, second.CaptureId);
        Assert.Equal(original, Copy(store, first));
    }

    [Fact]
    public void MissingRequiredDescriptionOrReadIsExplicitPartialAndUnadmitted()
    {
        var reads = Details();
        reads[0] = reads[0] with { Availability = "unsupported", Content = null,
            Missing = new[] { "current_display_description" } };
        JsonNode packet = JsonNode.Parse(Freeze(reads: reads).Bytes)!;
        Assert.Equal("partial", packet["scope_status"]!.GetValue<string>());
        Assert.Equal("unsupported", packet["reads"]![0]!["availability"]!.GetValue<string>());
        Assert.Equal("current_display_description", packet["reads"]![0]!["missing"]![0]!.GetValue<string>());
        Assert.False(packet["agent_admissible"]!.GetValue<bool>());
        Assert.Equal("unsupported", JsonNode.Parse(Freeze(supported: false).Bytes)!["scope_status"]!.GetValue<string>());
    }

    [Fact]
    public void ActualDisplayFormatterFailureDoesNotPromoteLegacyFallbackOrLoseTheContext()
    {
        var legacy = new JsonObject { ["entity_id"] = "same-card", ["name"] = "Card",
            ["description"] = "legacy generic fallback" };
        var failed = P1CaptureAssembly.CurrentCardDetail("detail-failed", legacy, "same-card", "draw", null,
            () => throw new InvalidOperationException("formatter failed"));
        Assert.Equal("unsupported", failed.Availability);
        Assert.Contains("current_display_description", failed.Missing);
        Assert.Null(failed.Content!["description"]);
        Assert.Equal("draw", failed.DisplayContext);
        Assert.Equal("legacy generic fallback", legacy["description"]!.GetValue<string>());
        legacy["description"] = "actual current hand description";
        var successful = P1CaptureAssembly.CurrentCardDetail("detail-good", legacy, "same-card", "hand", null,
            () => "actual current hand description");
        Assert.Equal("available", successful.Availability);
        Assert.Equal("actual current hand description", successful.Content!["description"]!.GetValue<string>());
        Assert.NotEqual(failed.InstanceId, successful.InstanceId);
        var drifted = P1CaptureAssembly.CurrentCardDetail("detail-drift", legacy, "same-card", "hand", null,
            () => "different current description");
        Assert.Contains("current_display_description_changed_during_capture", drifted.Missing);
        Assert.Equal("unsupported", drifted.Availability);
        var unknown = P1CaptureAssembly.CurrentCardDetail("detail-unknown", legacy, "same-card", "unqualified_preview", null,
            () => throw new Exception("must not invoke a formatter in an unknown context"));
        Assert.Equal(new[] { "unqualified_card_display_context" }, unknown.Missing);
        Assert.Contains("current_card_native_binding", P1CaptureAssembly.CurrentCardDetail(
            "missing-native", legacy, "same-card", "deck", null, null).Missing);
    }

    [Theory]
    [InlineData(200)]
    [InlineData(500)]
    [InlineData(10_000)]
    public void CaptureScaleKeepsAllInstancesOrExplicitlyRejectsTheAnnouncedByteBound(int count)
    {
        var reads = Details(count);
        var began = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            var frozen = Freeze(reads: reads);
            began.Stop();
            Assert.Equal(count, JsonNode.Parse(frozen.Bytes)!["reads"]!.AsArray().Count);
            Assert.InRange(frozen.Bytes.Length, 1, SealedObservationContract.MaxCapsuleBytes);
            Console.WriteLine($"C1 synthetic instances={count} bytes={frozen.Bytes.Length} freeze_ms={began.Elapsed.TotalMilliseconds:F3}");
        }
        catch (SealedObservationException error) when (error.Code == "too_large")
        { Console.WriteLine($"C1 synthetic instances={count} explicit_too_large freeze_ms={began.Elapsed.TotalMilliseconds:F3}"); }
    }

    [Fact]
    public void ReentryOrOwnerDriftNeverPublishesAConsistentCaptureEvenAfterOwnerReturns()
    {
        using (var scope = new P1CaptureGuard())
        {
            P1CaptureGuard.ObserveSnapshotBuild();
            P1CaptureGuard.InvalidateActive(); // Native input may change and restore the same owner.
            Assert.Equal("inconsistent_capture", Assert.Throws<SealedObservationException>(() => scope.RequireStable(true)).Code);
        }
        using (var scope = new P1CaptureGuard())
        {
            P1CaptureGuard.ObserveSnapshotBuild();
            Assert.Throws<SealedObservationException>(() => scope.RequireStable(false));
        }
        using (var outer = new P1CaptureGuard())
        {
            P1CaptureGuard.ObserveSnapshotBuild();
            Assert.Throws<SealedObservationException>(() => new P1CaptureGuard());
            Assert.Throws<SealedObservationException>(() => outer.RequireStable(true));
        }
        using (var scope = new P1CaptureGuard())
        {
            P1CaptureGuard.ObserveSnapshotBuild();
            Assert.Throws<SealedObservationException>(P1CaptureGuard.ObserveSnapshotBuild);
            Assert.Throws<SealedObservationException>(() => scope.RequireStable(true));
        }
        using (var scope = new P1CaptureGuard())
        { P1CaptureGuard.ObserveSnapshotBuild(); scope.RequireStable(true); }
    }

    [Fact]
    public void SharedRetentionKeepsProfilesSeparateAndExpiresWithoutNativeAccess()
    {
        long clock = 0; var store = new SealedObservationStore(() => clock, ttlMs: 10);
        var p1 = store.Capture(() => Freeze());
        var old = store.Capture(() => new(Encoding.UTF8.GetBytes("legacy"), "legacy-snapshot",
            new("runtime", "environment"), null, Timestamp));
        Assert.Equal(SealedObservationContract.Schema, old.Schema);
        Assert.Equal(TextMenuV2Contract.Profile, old.InputProfile);
        Assert.Equal("profile_mismatch", Assert.Throws<SealedObservationException>(() => store.Read(p1.CaptureId, p1.FirstCursor)).Code);
        Assert.Throws<SealedObservationException>(() => store.Release(p1.CaptureId));
        Assert.Equal("profile_mismatch", Assert.Throws<SealedObservationException>(() =>
            store.Read(old.CaptureId, old.FirstCursor, 1024, SealedObservationProfile.P1Capture)).Code);
        Assert.NotEmpty(Copy(store, p1));
        clock = 10;
        Assert.Equal("expired", Assert.Throws<SealedObservationException>(() => Copy(store, p1)).Code);
        Assert.Equal(2, store.CaptureCount);
    }

    [Fact]
    public void LimitsAndMalformedRelationsFailClosedBeforePublishingAHandle()
    {
        Assert.Equal("too_large", Assert.Throws<SealedObservationException>(() => Freeze(reads: Details(10_001))).Code);
        var reads = Details(); reads[1] = reads[1] with { InstanceId = reads[0].InstanceId };
        Assert.Equal("inconsistent_capture", Assert.Throws<SealedObservationException>(() => Freeze(reads: reads)).Code);
        var coverage = Coverage(Details()); coverage[0] = coverage[0] with { ReadInstanceIds = new[] { "foreign" } };
        Assert.Throws<SealedObservationException>(() => Freeze(coverage: coverage));
        var store = new SealedObservationStore(maxCapsuleBytes: 1024);
        Assert.Equal("too_large", Assert.Throws<SealedObservationException>(() => store.Capture(() => Freeze())).Code);
        Assert.Equal(1, store.CaptureCount);
        var oversized = Details(1);
        oversized[0].Content!["description"] = new string('x', SealedObservationContract.MaxCapsuleBytes);
        Assert.Equal("too_large", Assert.Throws<SealedObservationException>(() => Freeze(reads: oversized)).Code);
    }

    [Fact]
    public void PortableProducerEmitsTheSdkContractFixture()
    {
        FrozenPublicObservation frozen = Freeze();
        if (Environment.GetEnvironmentVariable("P1_CAPTURE_FIXTURE_OUT") is { } destination)
            File.WriteAllBytes(destination, frozen.Bytes);
        string fixture = Path.Combine(AppContext.BaseDirectory, "p1-capture-c1.json");
        Assert.Equal(frozen.Bytes, File.ReadAllBytes(fixture));
    }
}
