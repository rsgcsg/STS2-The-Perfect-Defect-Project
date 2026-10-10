using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.Json.Nodes;
using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Core.Tests;

public sealed class SourceSessionV2Tests
{
    private static readonly JsonSerializerOptions Json = new(EvidenceJson.Options)
    { DefaultIgnoreCondition = JsonIgnoreCondition.Never, PropertyNameCaseInsensitive = false };
    private static byte[] Bytes<T>(T value) => Encoding.UTF8.GetBytes(JsonSerializer.Serialize(value, Json) + "\n");
    private static SourceDeclaration Agent(string declaration = "declaration-first") => new("agent_protocol", "actor-fixture", declaration, false);
    private static SourceNativePositionV2 Position(string epoch, int index) => new(epoch, "generation-" + epoch, index.ToString());
    private static SourceNativeSealV2 Seal(string epoch, int reserved, int completed) => new(epoch, "generation-" + epoch, reserved.ToString(), completed.ToString());
    private static SourceNativeTransitionV2 Setup(string previous, string current) => new("setup-witness", "setup_handoff",
        "RunManager.SetUpNewSingleplayer.prefix->actual_State", previous, current, "new", null, null);
    internal static readonly SourcePublicAction Action = new("action-fixture", "native_input", "focus_target", "目标 🧪",
        "referent-fixture", Array.Empty<SourceActionArgument>(), "native");

    internal sealed class Fixture : IDisposable
    {
        internal readonly string Root = Path.Combine(Path.GetTempPath(), "source-v2-tests-" + Guid.NewGuid().ToString("N"));
        internal readonly RecorderEnvironmentIdentity Environment = new(new("fixture-game", "fixture-commit", new string('a', 64), "game-mvid"),
            new("Connector", "fixture", new string('b', 40), new string('c', 64), new string('d', 64), "connector-mvid"),
            new("Annotator", "fixture", new string('e', 40), new string('f', 64), new string('a', 64), "annotator-mvid"),
            "1.0.0", "runtime-fixture", "environment-fixture", "fixture", "modset-fixture");
        internal readonly SourceCaptureProfileV2 Profile;
        internal readonly RecordingSessionStore Store;
        internal Fixture(SourceSessionLimitsV2? limits = null, int version = 2)
        {
            Profile = new("sts2.annotator/source-capture-profile-" + version, "native-logical-source-v" + version, "native-logical-v1",
                SourceSessionContractV2.PublicationProfileId, SourceSessionContractV2.PublicationProfileDefinitionSha256,
                new[] { "persistent", "interaction", "referents", "catalog" }, limits ?? new(), SourceSessionContractV2.NonClaims);
            var manifest = new CurrentRecordingManifest(version, "sts2.annotator/source-session-manifest-" + version, "session-source-v2", "timeline-source-v2",
                DateTimeOffset.UnixEpoch, "fixture", new string('e', 40), "fixture-platform", Profile.ProfileId,
                SourceSessionContractV2.ProfileDigest(Profile), Array.Empty<string>(), SourceSessionContractV2.NonClaims)
            { SourceSchemaVersion = version, SourceEnvironment = Environment, RecoverySchemaVersion = 1 };
            Store = version == 3 ? RecordingSessionStore.CreateSourceV3(Root, manifest, Profile, Agent(), Epoch("title", null, null, null, "title-continuity"))
                : RecordingSessionStore.CreateSourceV2(Root, manifest, Profile, Agent(), Epoch("title", null, null, null, "title-continuity"));
        }
        internal SourceEpochPacketV2 Epoch(string id, string? previous, SourceNativeSealV2? seal,
            SourceNativeTransitionV2? transition, string continuity) => new(id, previous,
                new(SourceSessionContractV2.PublicationProfileId, SourceSessionContractV2.PublicationProfileDefinitionSha256,
                    "scope-" + id, "generation-" + id, Profile.EagerScope,
                    new Dictionary<string, SourceSeamCoverage> { ["fixture.owner"] = new("1", "complete_at_seam") },
                    Environment, continuity), Position(id, 0), Position(id, 1), seal, transition);
        internal (FrozenPublicCaptureV2 Capture, FrozenPublicCatalogV2 Catalog) Payload(string epoch)
        {
            string snapshot = "snapshot-" + epoch, scope = "scope-" + epoch, generation = "generation-" + epoch;
            byte[] catalogBytes = Bytes(new[] { Action }); string structural = SourceCatalogCodec.Digest(new[] { Action });
            var catalog = new FrozenPublicCatalog("catalog-" + epoch, snapshot, scope, generation, 1,
                structural, catalogBytes, SourceSessionContract.Sha256(catalogBytes));
            byte[] captureBytes = Bytes(new
            {
                schema = "sts2.player-environment/native-logical-observation-1", protocol_version = "1.0.0",
                input_profile = "native-logical-v1", snapshot_id = snapshot, revision = 1,
                observed_at = DateTimeOffset.UnixEpoch, status = "interactive",
                session = new { runtime_instance_id = Environment.RuntimeInstanceId, environment_fingerprint = Environment.EnvironmentFingerprint },
                persistent = (object?)null,
                interaction = new { interaction_id = "interaction-" + epoch, kind = "fixture", stage = "ready", prompt = (string?)null,
                    content_schema = "fixture-public", content = new { surface = new { kind = "fixture" }, context = new { kind = "fixture" } },
                    capabilities = Array.Empty<object>() },
                referents = new[] { new { referent_id = "referent-fixture", role = "target", kind = "entity", label = "公开目标",
                    state = new { visible = true, enabled = true, selected = false, focused = false, observation_basis = "native_visible_fact" },
                    properties_schema = (string?)null, properties = (object?)null } },
                completeness = new { status = "complete", included = Profile.EagerScope, missing = Array.Empty<string>(), full_reference_complete = true },
                information_policy = new { id = "fair-player", scope = "entered-native-page", includes_hidden_information = false, unknown_field_behavior = "explicit" },
                owner_occurrence = new { owner_id = "owner-" + epoch, occurrence_id = "occurrence-" + epoch, binding_revision = "binding-" + epoch,
                    focus_referent_id = (string?)null, focus_occurrence = (string?)null },
                catalog = new { catalog_ref = catalog.CatalogRef, snapshot_id = snapshot, scope_id = scope, stream_generation = generation,
                    status = "complete", total_count = 1, digest = structural, ordering_semantics = "native_public_order", access_methods = new[] { "catalog", "resolve" } }
            });
            return (new(epoch, new("capture-" + epoch, snapshot, scope, generation, DateTimeOffset.UnixEpoch,
                captureBytes, SourceSessionContract.Sha256(captureBytes))), new(epoch, catalog));
        }
        internal SourceObservationPacketV2 Packet(string epoch, int index, string continuity)
        {
            var values = Payload(epoch);
            return new(Position(epoch, index), "fixture.owner", index.ToString(), "after", values.Capture.Capture.SnapshotId,
                "occurrence-" + epoch, continuity, "complete", values.Capture, values.Catalog);
        }
        internal void Close(string epoch, int index, IReadOnlyList<SourceNativeSealV2> requested, IReadOnlyList<SourceNativeSealV2>? completed = null)
        {
            var boundary = Store.AdmitSourceBoundaryV2("close", Position(epoch, index), requested);
            Store.AppendSourceBoundaryV2(boundary); Store.PrepareSourceCloseV2(completed ?? requested); Store.Dispose();
        }
        public void Dispose()
        {
            try { Store.Dispose(); } catch (Exception) { }
            try { Directory.Delete(Root, true); } catch (IOException) { }
        }
    }

    [Theory]
    [InlineData("missing_reason")]
    [InlineData("missing_machine_verifiable")]
    [InlineData("missing_transition_nullable")]
    [InlineData("missing_default_limit")]
    [InlineData("extra_manifest_human_claim")]
    [InlineData("extra_environment_field")]
    [InlineData("missing_artifact_product")]
    public void StrictSourceV2AuditRejectsMissingNullableDefaultsAndUnknownFieldsAfterHashRepair(string mutation)
    {
        using var f = new Fixture();
        f.Store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity"));
        var token = f.Store.ReserveSourceInputV2("input-strict", Position("title", 1));
        var basis = f.Payload("title"); f.Store.BindSourceInputBasisV2(token, basis.Capture, basis.Catalog);
        f.Store.CompleteSourceInputV2(token, new("exact", 1, Action, "fixture.native_input", "delivered", null, Array.Empty<SourceInputStageV2>()));
        var launch = new SourceNativeTransitionV2("launch-strict", "launch", "RunManager.Launch.postfix",
            "title-continuity", "title-continuity", "new", null, null);
        f.Store.AppendSourceBoundaryV2(f.Store.AdmitSourceBoundaryV2("launch", Position("title", 1), transition: launch));
        f.Close("title", 1, new[] { Seal("title", 1, 1) });
        Assert.Equal("pass", SourceSessionAuditV2.Audit(f.Store.DirectoryPath).Status);
        string changed;
        if (mutation == "missing_reason")
        { changed = "native-input-witnesses.jsonl"; MutateRows(changed, rows => rows[0]["outcome"]!.AsObject().Remove("reason_code")); }
        else if (mutation == "missing_machine_verifiable")
        { changed = "source-segments.jsonl"; MutateRows(changed, rows => rows[0]["declaration"]!.AsObject().Remove("machine_verifiable")); }
        else if (mutation == "missing_transition_nullable")
        { changed = "source-boundaries.jsonl"; MutateRows(changed, rows => rows[0]["transition"]!.AsObject().Remove("graceful")); }
        else if (mutation == "missing_default_limit")
        {
            changed = "capture-profile.json"; var profile = Read(changed); profile["limits"]!.AsObject().Remove("max_epochs"); Write(changed, profile);
            var manifest = Read("recording-manifest.json"); manifest["capture_profile_sha256"] = SourceSessionContract.Sha256(File.ReadAllBytes(FilePath(changed)));
            Write("recording-manifest.json", manifest);
        }
        else
        {
            changed = "recording-manifest.json"; var manifest = Read(changed);
            if (mutation == "extra_manifest_human_claim") manifest["human_origin_attested"] = true;
            else if (mutation == "extra_environment_field") manifest["source_environment"]!["human_origin_attested"] = true;
            else manifest["source_environment"]!["connector"]!.AsObject().Remove("product");
            Write(changed, manifest);
        }
        if (changed.EndsWith(".jsonl", StringComparison.Ordinal))
        {
            var receipt = Read("source-close-receipt.json"); receipt["stream_sha256"]![changed] = SourceSessionContract.Sha256(File.ReadAllBytes(FilePath(changed)));
            Write("source-close-receipt.json", receipt);
        }
        Assert.Equal("fail", SourceSessionAuditV2.Audit(f.Store.DirectoryPath).Status);
        string FilePath(string file) => Path.Combine(f.Store.DirectoryPath, file);
        JsonObject Read(string file) => JsonNode.Parse(File.ReadAllBytes(FilePath(file)))!.AsObject();
        void Write(string file, JsonNode value) => File.WriteAllBytes(FilePath(file), Bytes(value));
        void MutateRows(string file, Action<JsonObject[]> mutate)
        {
            var rows = File.ReadLines(FilePath(file)).Select(line => JsonNode.Parse(line)!.AsObject()).ToArray();
            mutate(rows); File.WriteAllBytes(FilePath(file), rows.SelectMany(Bytes).ToArray());
        }
    }

    [Fact]
    public void ObservationCannotReplaceItsOriginalCapturedOwnerOccurrence()
    {
        using var f = new Fixture();
        var packet = f.Packet("title", 1, "title-continuity") with { OwnerOccurrence = "invented-owner-occurrence" };
        Assert.Equal("source_observation_owner_occurrence_mismatch",
            Assert.Throws<InvalidDataException>(() => f.Store.AppendPublicObservationV2(packet)).Message);
    }

    [Fact]
    public void OneVisibleStorePreservesOriginalEpochAndActorAcrossPausedSetupLaunchAndDelayedCompletion()
    {
        using var f = new Fixture(); var store = f.Store;
        store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity"));
        var original = store.ReserveSourceInputV2("input-before", Position("title", 1));
        store.AppendSourceBoundaryV2(store.AdmitSourceBoundaryV2("pause", Position("title", 1)));
        var admission = store.AdmitSourceEpochV2(f.Epoch("run", "title", Seal("title", 3, 1),
            Setup("title-continuity", "run-continuity"), "run-continuity"));
        store.AppendSourceEpochV2(admission);
        var changed = store.AdmitSourceDeclarationV2(Agent("declaration-second") with { ActorId = "actor-second" },
            original.SegmentId, Position("run", 0)); store.AppendSourceDeclarationV2(changed);
        store.AppendPublicObservationV2(f.Packet("title", 2, "title-continuity"));
        store.AppendPublicObservationV2(f.Packet("title", 3, "title-continuity"));
        store.AppendPublicObservationV2(f.Packet("run", 1, "run-continuity"));
        store.AppendSourceBoundaryV2(store.AdmitSourceBoundaryV2("resume", Position("run", 1)));
        var basis = f.Payload("title"); store.BindSourceInputBasisV2(original, basis.Capture, basis.Catalog);
        store.CompleteSourceInputV2(original, new("exact", 1, Action, "fixture.native_input", "delivered", null,
            new[] { new SourceInputStageV2("native_dispatch", "delivered", "original_request") }));
        store.AppendPublicObservationV2(f.Packet("run", 2, "run-continuity"));
        var launch = new SourceNativeTransitionV2("launch-witness", "launch", "RunManager.Launch.postfix",
            "run-continuity", "run-continuity", "new", null, null);
        store.AppendSourceBoundaryV2(store.AdmitSourceBoundaryV2("launch", Position("run", 2), transition: launch));
        var unfinished = store.ReserveSourceInputV2("input-unknown", Position("run", 2));
        store.AppendPublicObservationV2(f.Packet("run", 3, "run-continuity"));
        var terminal = new SourceNativeTransitionV2("terminal-witness", "terminal", "RunManager.OnEnded.postfix",
            "run-continuity", "run-continuity", null, null, true);
        store.AppendSourceBoundaryV2(store.AdmitSourceBoundaryV2("terminal", Position("run", 3), transition: terminal));
        f.Close("run", 3, new[] { Seal("title", 3, 1), Seal("run", 3, 3) }, new[] { Seal("title", 3, 3), Seal("run", 3, 3) });
        var inputs = File.ReadLines(Path.Combine(store.DirectoryPath, "native-input-witnesses.jsonl"))
            .Select(x => JsonSerializer.Deserialize<SourceNativeInputWitnessV2>(x, Json)!).ToArray();
        Assert.Equal(original.SegmentId, inputs[0].SegmentId); Assert.Equal("title", inputs[0].EpochId);
        Assert.Equal("delivered", inputs[0].Outcome.Delivery); Assert.Equal("unknown", inputs[1].Outcome.Delivery);
        Assert.Equal(changed.SegmentId, unfinished.SegmentId); Assert.Equal("run", unfinished.PrePosition.EpochId);
        Assert.Equal(basis.Capture.Capture.Bytes, File.ReadAllBytes(Path.Combine(store.DirectoryPath, inputs[0].PreCapture!.PayloadRef)));
        using var receipt = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(store.DirectoryPath, "source-close-receipt.json")));
        var drains = receipt.RootElement.GetProperty("final_drains").EnumerateArray().ToArray();
        Assert.Equal(2, drains.Length); Assert.Equal("3", drains[0].GetProperty("sealed_reserved_through").GetString());
        Assert.Equal("3", drains[0].GetProperty("completed_through").GetString());
        Assert.Equal("3", drains[0].GetProperty("durable_through").GetString());
        Assert.Equal(3, store.GetSourceStatusV2()!.Observations); Assert.Equal(2, store.GetSourceStatusV2()!.Inputs);
        Assert.NotEqual("pass", SourceSessionAudit.Audit(store.DirectoryPath).Status);
    }

    [Fact]
    public async Task NativePrefixTokenAdmissionDoesNotWaitForTheExistingDiskGateOrWriteAnyFile()
    {
        using var f = new Fixture();
        object diskGate = typeof(RecordingSessionStore).GetField("_gate", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(f.Store)!;
        var before = Directory.GetFiles(f.Store.DirectoryPath).ToDictionary(x => x, x => File.GetLastWriteTimeUtc(x));
        SourceInputTokenV2? token = null;
        var held = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        using var release = new ManualResetEventSlim(false);
        var writer = Task.Run(() => { lock (diskGate) { held.SetResult(true); release.Wait(TimeSpan.FromSeconds(5)); } });
        await held.Task.WaitAsync(TimeSpan.FromSeconds(2));
        try
        {
            token = await Task.Run(() => f.Store.ReserveSourceInputV2("prefix-input", Position("title", 1)))
                .WaitAsync(TimeSpan.FromSeconds(2));
        }
        finally { release.Set(); await writer.WaitAsync(TimeSpan.FromSeconds(2)); }
        Assert.NotNull(token); Assert.Equal(1, f.Store.GetSourceStatusV2()!.PendingInputs);
        Assert.All(before, item => Assert.Equal(item.Value, File.GetLastWriteTimeUtc(item.Key)));
        f.Store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity"));
        f.Close("title", 1, new[] { Seal("title", 1, 1) });
    }

    [Theory]
    [InlineData(1)]
    [InlineData(3)]
    public void CloseCannotUseRowCountsOrAnEarlierCompletedWatermarkToProveMissingOriginalReservations(int completed)
    {
        using var f = new Fixture(); f.Store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity"));
        f.Store.AppendSourceBoundaryV2(f.Store.AdmitSourceBoundaryV2("close", Position("title", 3), new[] { Seal("title", 3, 1) }));
        Assert.Throws<InvalidDataException>(() => f.Store.PrepareSourceCloseV2(new[] { Seal("title", 3, completed) }));
        Assert.False(f.Store.GetSourceStatusV2()!.AccountingComplete);
        Assert.False(File.Exists(Path.Combine(f.Store.DirectoryPath, "source-close-receipt.json")));
    }

    [Fact]
    public void AnOriginalLostIntervalCrossingPauseKeepsItsUnpausedGapAndClosesThePausedSuffix()
    {
        using var f = new Fixture(); f.Store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity"));
        f.Store.AppendSourceBoundaryV2(f.Store.AdmitSourceBoundaryV2("pause", Position("title", 2)));
        f.Store.AppendPublicObservationV2(new(Position("title", 5), "source_gap", "0", "retention_overflow", null, null,
            "title-continuity", "failed", null, null, "retention_overflow", "1"));
        f.Close("title", 5, new[] { Seal("title", 5, 5) });
        var observations = File.ReadLines(Path.Combine(f.Store.DirectoryPath, "public-observations.jsonl"))
            .Select(x => JsonSerializer.Deserialize<SourcePublicObservationV2>(x, Json)!).ToArray();
        Assert.Equal(2, observations.Length); Assert.Equal("2", observations[1].Position.PublicationIndex);
        Assert.Equal("1", observations[1].GapAfterIndex); Assert.Equal("retention_overflow", observations[1].MissingReason);
        Assert.Equal(2, f.Store.GetSourceStatusV2()!.Gaps);
    }

    [Fact]
    public void ForeignStoreTokensAndContinuityChangesWithoutTypedNativeWitnessCannotRebindTheCurrentEpoch()
    {
        using var first = new Fixture(); using var second = new Fixture();
        var token = first.Store.ReserveSourceInputV2("original", Position("title", 1));
        Assert.Throws<InvalidDataException>(() => second.Store.BindSourceInputBasisV2(token, null, null));
        Assert.Throws<InvalidDataException>(() => first.Store.AdmitSourceEpochV2(first.Epoch("run", "title", Seal("title", 1, 1), null, "run-continuity")));
        Assert.Equal(1, first.Store.GetSourceStatusV2()!.Epochs);
        Assert.Equal("title", token.PrePosition.EpochId);
    }

    [Fact]
    public void IncludedInteractionNullCannotBePromotedByACompleteOuterV2Envelope()
    {
        using var f = new Fixture(); var packet = f.Packet("title", 1, "title-continuity");
        var body = System.Text.Json.Nodes.JsonNode.Parse(packet.Capture!.Capture.Bytes)!.AsObject(); body["interaction"] = null;
        byte[] altered = Encoding.UTF8.GetBytes(body.ToJsonString(Json) + "\n");
        var capture = packet.Capture.Capture with { Bytes = altered, Sha256 = SourceSessionContract.Sha256(altered) };
        Assert.Throws<InvalidDataException>(() => f.Store.AppendPublicObservationV2(packet with { Capture = new("title", capture) }));
        Assert.False(f.Store.GetSourceStatusV2()!.AccountingComplete);
        Assert.False(File.Exists(Path.Combine(f.Store.DirectoryPath, "source-close-receipt.json")));
    }

    [Fact]
    public void SourceV2BoundsAndUtf8StageFieldsRejectCapacityOrInvalidUnicodeBeforeSealing()
    {
        Assert.Throws<InvalidDataException>(() => SourceSessionContractV2.Text("\ud800"));
        Assert.Throws<InvalidDataException>(() => SourceSessionContractV2.Text(new string('中', 43)));
        using var f = new Fixture(new(MaxPendingInputs: 1));
        f.Store.ReserveSourceInputV2("first", Position("title", 1));
        Assert.Throws<InvalidDataException>(() => f.Store.ReserveSourceInputV2("second", Position("title", 1)));
        Assert.False(f.Store.GetSourceStatusV2()!.AccountingComplete);
        Assert.Equal("source_input_capacity", f.Store.GetSourceStatusV2()!.Error);
    }
    [Fact]
    public void FinalCloseRejectsCompletionAboveTheOriginalSealEvenWhenAllOriginalPositionsAreDurable()
    {
        using var f = new Fixture(); f.Store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity"));
        f.Store.AppendSourceBoundaryV2(f.Store.AdmitSourceBoundaryV2("close", Position("title", 1), new[] { Seal("title", 1, 0) }));
        Assert.Throws<InvalidDataException>(() => f.Store.PrepareSourceCloseV2(new[] { Seal("title", 1, 100) }));
        Assert.False(File.Exists(Path.Combine(f.Store.DirectoryPath, "source-close-receipt.json")));
    }
    [Fact]
    public void NativeStageDeliveryCannotUseArbitraryTextAndActualSeamsCannotMasqueradeAsDiagnosticGaps()
    {
        using var f = new Fixture(); var token = f.Store.ReserveSourceInputV2("input-stage", Position("title", 1));
        f.Store.BindSourceInputBasisV2(token, null, null);
        Assert.Throws<InvalidDataException>(() => f.Store.CompleteSourceInputV2(token, new("capture_missing", 0, null,
            "fixture", "unknown", null, new[] { new SourceInputStageV2("dispatch", "invented-success", "fixture") })));
        using var gap = new Fixture();
        Assert.Throws<InvalidDataException>(() => gap.Store.AppendPublicObservationV2(new(Position("title", 3), "native_owner_ready", "1",
            "retention_overflow", null, null, "title-continuity", "failed", null, null, "retention_overflow", "0")));
    }

    [Fact]
    public void ForgedAppendRowsCannotRewriteTheAlreadyIssuedCloseBoundaryOrDeclaration()
    {
        using var f = new Fixture(); f.Store.AppendPublicObservationV2(f.Packet("title", 1, "title-continuity"));
        var close = f.Store.AdmitSourceBoundaryV2("close", Position("title", 1), new[] { Seal("title", 1, 1) });
        Assert.Throws<InvalidDataException>(() => f.Store.AppendSourceBoundaryV2(close with
        { Position = Position("title", 999), SealedEpochs = Array.Empty<SourceNativeSealV2>() }));
        Assert.False(File.Exists(Path.Combine(f.Store.DirectoryPath, "source-close-receipt.json")));
        using var segment = new Fixture(); segment.Store.AdmitSourceBoundaryV2("pause", Position("title", 1));
        var issued = segment.Store.AdmitSourceDeclarationV2(Agent("changed"), segment.Store.GetSourceStatusV2()!.SegmentId, Position("title", 1));
        Assert.Throws<InvalidDataException>(() => segment.Store.AppendSourceDeclarationV2(issued with
        { Declaration = issued.Declaration with { ActorId = "forged-actor" } }));
    }

    [Fact]
    public void OriginalSealCannotRetreatBelowAnIssuedInputPositionOrAcceptLateRowsBeyondItsRange()
    {
        using var input = new Fixture(); input.Store.ReserveSourceInputV2("input-high", Position("title", 3));
        Assert.Throws<InvalidDataException>(() => input.Store.AdmitSourceBoundaryV2("close", Position("title", 3), new[] { Seal("title", 2, 2) }));
        using var row = new Fixture(); row.Store.AppendPublicObservationV2(row.Packet("title", 1, "title-continuity"));
        row.Store.AppendSourceEpochV2(row.Store.AdmitSourceEpochV2(row.Epoch("run", "title", Seal("title", 1, 1),
            Setup("title-continuity", "run-continuity"), "run-continuity")));
        Assert.Throws<InvalidDataException>(() => row.Store.AppendPublicObservationV2(new(Position("title", 2), "source_gap", "0",
            "retention_overflow", null, null, "title-continuity", "failed", null, null, "retention_overflow", "1")));
    }

}
