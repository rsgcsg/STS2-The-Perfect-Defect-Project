using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Core.Tests;

public sealed class SourceSessionTests
{
    private static readonly JsonSerializerOptions Json = new(EvidenceJson.Options)
    { DefaultIgnoreCondition = JsonIgnoreCondition.Never, PropertyNameCaseInsensitive = false };
    private static byte[] Bytes<T>(T value) => Encoding.UTF8.GetBytes(JsonSerializer.Serialize(value, Json) + "\n");
    private static SourceClockReference Clock(int index) => new("stream-fixture", index.ToString());
    private static SourceDeclaration Agent(string id = "declaration-agent") =>
        new("agent_native_ui", "actor-agent", id, false);
    private static readonly SourcePublicAction Action = new("action-fixture", "native_input", "focus_target",
        "Focus 中文 🧪", "creature-fixture", Array.Empty<SourceActionArgument>(), "native");

    private sealed class Fixture : IDisposable
    {
        internal Fixture(SourceSessionLimits? limits = null, string? requestedRoot = null)
        {
            Root = requestedRoot ?? Path.Combine(Path.GetTempPath(), "source-session-tests-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(Root);
            Environment = new(new("fixture-game", "fixture-commit", new string('a', 64), "game-mvid"),
                new("Connector", "fixture", new string('b', 40), new string('c', 64), new string('d', 64), "connector-mvid"),
                new("Annotator", "fixture", new string('e', 40), new string('f', 64), new string('a', 64), "annotator-mvid"),
                "1.0.0", "runtime-fixture", "environment-fixture", "fixture", "modset-fixture");
            Profile = new(SourceSessionContract.ProfileSchema, SourceSessionContract.ProfileId, "native-logical-v1",
                "scope-fixture", new[] { "persistent", "interaction", "referents", "catalog" },
                new Dictionary<string, SourceSeamCoverage>
                { ["fixture.owner"] = new("1", "complete_at_seam"), ["native_unwired"] = new("1", "unsupported") },
                limits ?? new(), SourceSessionContract.NonClaims);
            var manifest = new CurrentRecordingManifest(1, SourceSessionContract.ManifestSchema,
                "session-source-fixture", "timeline-source-fixture", DateTimeOffset.UnixEpoch,
                "fixture-recorder", new string('e', 40), "fixture-platform", Profile.ProfileId,
                SourceSessionContract.Sha256(Bytes(Profile)), Array.Empty<string>(), SourceSessionContract.NonClaims)
            { SourceSchemaVersion = 1, SourceEnvironment = Environment, RecoverySchemaVersion = 1 };
            Store = RecordingSessionStore.CreateSource(Root, manifest, Profile, Agent(), Clock(0));
            byte[] catalogBytes = Bytes(new[] { Action });
            string structural = SourceCatalogCodec.Digest(new[] { Action });
            Catalog = new("catalog-fixture", "snapshot-fixture", Profile.ScopeId, Clock(0).StreamGeneration,
                1, structural, catalogBytes, SourceSessionContract.Sha256(catalogBytes));
            byte[] captureBytes = Bytes(new
            {
                schema = "sts2.player-environment/native-logical-observation-1",
                protocol_version = "1.0.0", input_profile = "native-logical-v1", snapshot_id = "snapshot-fixture",
                revision = 1, observed_at = DateTimeOffset.UnixEpoch, status = "interactive",
                session = new { runtime_instance_id = Environment.RuntimeInstanceId,
                    environment_fingerprint = Environment.EnvironmentFingerprint },
                persistent = new { content_schema = "public-persistent-fixture", content = new { hp = 42 } },
                interaction = new { interaction_id = "interaction-fixture", kind = "selector", stage = "ready",
                    prompt = (string?)null, content_schema = "public-surface-fixture",
                    content = new { surface = new { kind = "selector", focus_referent_id = "creature-fixture" },
                        context = new { kind = "public-fixture" } }, capabilities = Array.Empty<object>() },
                referents = new[] { new { referent_id = "creature-fixture", role = "creature", kind = "entity",
                    label = "公开目标", state = new { visible = true, enabled = true, selected = false,
                        focused = true, observation_basis = "native_visible_fact" },
                    properties_schema = (string?)null, properties = (object?)null } },
                completeness = new { status = "complete", included = Profile.EagerScope, missing = Array.Empty<string>(),
                    full_reference_complete = true },
                information_policy = new { id = "fair-player", scope = "entered-native-page",
                    includes_hidden_information = false, unknown_field_behavior = "explicit" },
                owner_occurrence = new { owner_id = "owner-fixture", occurrence_id = "occurrence-fixture",
                    binding_revision = "binding-fixture", focus_referent_id = "creature-fixture",
                    focus_occurrence = "focus-occurrence-fixture" },
                catalog = new { catalog_ref = Catalog.CatalogRef, snapshot_id = Catalog.SnapshotId,
                    scope_id = Catalog.ScopeId, stream_generation = Catalog.StreamGeneration,
                    status = "complete", total_count = 1, digest = structural,
                    ordering_semantics = "native_public_order", access_methods = new[] { "catalog", "resolve" } }
            });
            Capture = new("capture-fixture", "snapshot-fixture", Profile.ScopeId, Clock(0).StreamGeneration,
                DateTimeOffset.UnixEpoch, captureBytes, SourceSessionContract.Sha256(captureBytes));
        }
        internal string Root { get; }
        internal RecorderEnvironmentIdentity Environment { get; }
        internal SourceCaptureProfile Profile { get; }
        internal RecordingSessionStore Store { get; }
        internal FrozenPublicCapture Capture { get; }
        internal FrozenPublicCatalog Catalog { get; }
        internal SourceObservationPacket Packet(int index) => new(Clock(index), Profile.ScopeId, "fixture.owner",
            index.ToString(), "after", Capture.SnapshotId, "occurrence-fixture", "complete", Capture, Catalog);
        internal (PublicCaptureReference Capture, PublicCatalogReference Catalog) Persist() =>
            (Store.PersistPublicCapture(Capture), Store.PersistPublicCatalog(Catalog));
        internal void Close(int index)
        { Store.RecordSourceBoundary("close", Clock(index), RecordingLifecycleState.Closing); Store.Dispose(); }
        public void Dispose()
        {
            if (!Store.GetSnapshot().Closed)
            {
                try { Close(100); }
                catch (Exception) { /* Failed fixtures deliberately cannot seal. */ }
            }
            try { Directory.Delete(Root, true); } catch (IOException) { }
        }
    }

    [Fact]
    public void SourceDeclarationIsExplicitAndNeverMachineProof()
    {
        foreach (string kind in new[] { "declared_human", "agent_native_ui", "agent_protocol", "unknown" })
            SourceSessionContract.Validate(new(kind, "actor-id", "declaration-id", false));
        Assert.Throws<InvalidDataException>(() => SourceSessionContract.Validate(new("human", "actor", "declaration")));
        Assert.Throws<InvalidDataException>(() => SourceSessionContract.Validate(Agent() with { MachineVerifiable = true }));
        Assert.Throws<InvalidDataException>(() => SourceSessionContract.Validate(Agent() with { ActorId = "secret\nvalue" }));
        Assert.Throws<InvalidDataException>(() => SourceSessionContract.Validate(null));
        Assert.Throws<InvalidDataException>(() => SourceSessionContract.Identifier(null));
        Assert.Throws<InvalidDataException>(() => SourceSessionContract.Identifier(".."));
        Assert.Throws<InvalidDataException>(() => SourceSessionContract.Index(new("generation", "01")));
        using var fixture = new Fixture();
        Assert.True(fixture.Store.IsSourceSession);
        Assert.Throws<InvalidOperationException>(() => fixture.Store.CaptureProfile);
    }

    [Fact]
    public void ExactBytesCatalogAndOriginalSegmentSurviveDelayedCompletionAndPause()
    {
        using var fixture = new Fixture();
        fixture.Store.AppendPublicObservation(fixture.Packet(1));
        var references = fixture.Persist();
        SourceInputScope before = fixture.Store.BeginSourceInput("input-before", Clock(3),
            references.Capture, references.Catalog, RecordingLifecycleState.Recording);
        string first = before.SegmentId;
        Assert.Throws<InvalidOperationException>(() => fixture.Store.ChangeSource(Agent("change"),
            first, Clock(4), RecordingLifecycleState.Recording));
        fixture.Store.RecordSourceBoundary("pause", Clock(5), RecordingLifecycleState.Paused);
        Assert.Throws<InvalidOperationException>(() => fixture.Store.ChangeSource(Agent("change"),
            "wrong-segment", Clock(6), RecordingLifecycleState.Paused));
        SourceSegment next = fixture.Store.ChangeSource(new("agent_protocol", "actor-protocol", "change", false),
            first, Clock(6), RecordingLifecycleState.Paused);
        fixture.Store.AppendPublicObservation(fixture.Packet(4)); // earlier source packet finishes late
        fixture.Store.AppendPublicObservation(fixture.Packet(7)); // paused interval is an explicit gap
        Assert.Throws<InvalidOperationException>(() => fixture.Store.BeginSourceInput("paused-input", Clock(7),
            references.Capture, references.Catalog, RecordingLifecycleState.Recording));
        var outcome = new SourceInputOutcome("exact", 1, Action, "fixture.native_callback", "delivered");
        fixture.Store.CompleteSourceInput(before, outcome);
        fixture.Store.CompleteSourceInput(before, outcome);
        Assert.Throws<InvalidDataException>(() => fixture.Store.CompleteSourceInput(before, outcome with { Delivery = "unknown" }));
        fixture.Store.RecordSourceBoundary("resume", Clock(8), RecordingLifecycleState.Recording);
        Assert.Throws<InvalidDataException>(() => fixture.Store.BeginSourceInput("stale-input", Clock(7),
            references.Capture, references.Catalog, RecordingLifecycleState.Recording));
        SourceInputScope after = fixture.Store.BeginSourceInput("input-after", Clock(9),
            references.Capture, references.Catalog, RecordingLifecycleState.Recording);
        Assert.Equal(next.SegmentId, after.SegmentId);
        fixture.Close(10); // unfinished input is explicit unknown, never invented native completion
        SourceSessionAuditResult audit = SourceSessionAudit.Audit(fixture.Store.DirectoryPath);
        Assert.True(audit.Status == "pass", string.Join(",", audit.Errors));
        Assert.Equal(2, audit.ObservationCount);
        Assert.Equal(2, audit.InputCount);
        Assert.Equal(1, audit.GapCount);
        var rows = File.ReadLines(Path.Combine(fixture.Store.DirectoryPath, "native-input-witnesses.jsonl"))
            .Select(line => JsonSerializer.Deserialize<SourceNativeInputWitness>(line, Json)!).ToArray();
        Assert.Equal(first, rows[0].SegmentId);
        Assert.Equal("unknown", rows[1].Outcome.Delivery);
        Assert.Equal(fixture.Capture.Bytes, File.ReadAllBytes(Path.Combine(fixture.Store.DirectoryPath, references.Capture.PayloadRef)));
        Assert.Equal(fixture.Catalog.Bytes, File.ReadAllBytes(Path.Combine(fixture.Store.DirectoryPath, references.Catalog.PayloadRef)));
    }

    [Fact]
    public void InputTokenPrecedesEncodingAndCannotAdoptANewPausedActor()
    {
        using var fixture = new Fixture();
        SourceInputScope early = fixture.Store.BeginSourceInput("early-input", Clock(1), null, null,
            RecordingLifecycleState.Recording);
        string segment = early.SegmentId;
        fixture.Store.RecordSourceBoundary("pause", Clock(2), RecordingLifecycleState.Paused);
        fixture.Store.ChangeSource(new("unknown", "actor-unknown", "declaration-unknown", false),
            segment, Clock(3), RecordingLifecycleState.Paused);
        var references = fixture.Persist(); // frozen pre-input values encode after the actor changes
        SourceInputScope bound = fixture.Store.BindSourceInputBasis(early, references.Capture, references.Catalog);
        Assert.Equal(segment, bound.SegmentId);
        Assert.Same(bound, fixture.Store.BindSourceInputBasis(bound, references.Capture, references.Catalog));
        Assert.Throws<InvalidDataException>(() => fixture.Store.BindSourceInputBasis(bound, null, null));
        fixture.Store.CompleteSourceInput(bound, new("exact", 1, Action, "fixture.native_callback", "delivered"));
        fixture.Store.RecordSourceBoundary("resume", Clock(4), RecordingLifecycleState.Recording);
        fixture.Close(5);
        Assert.Equal("pass", SourceSessionAudit.Audit(fixture.Store.DirectoryPath).Status);
    }

    [Fact]
    public void PublicationReplayIsIdempotentAndChangedReplayOrWrongCatalogCannotBind()
    {
        using var fixture = new Fixture();
        fixture.Store.AppendPublicObservation(fixture.Packet(1));
        fixture.Store.AppendPublicObservation(fixture.Packet(1));
        Assert.Equal(1, fixture.Store.GetSourceStatus()!.Observations);
        Assert.Throws<InvalidDataException>(() => fixture.Store.AppendPublicObservation(
            fixture.Packet(1) with { OwnerOccurrence = "different-owner" }));
        Assert.Throws<InvalidDataException>(() => fixture.Store.PersistPublicCapture(
            fixture.Capture with { Sha256 = new string('0', 64) }));
        Assert.Throws<InvalidDataException>(() => fixture.Store.PersistPublicCatalog(
            fixture.Catalog with { StructuralDigest = new string('0', 64) }));
        var refs = fixture.Persist();
        var scope = fixture.Store.BeginSourceInput("exact-input", Clock(2), refs.Capture, refs.Catalog,
            RecordingLifecycleState.Recording);
        Assert.Throws<InvalidDataException>(() => fixture.Store.CompleteSourceInput(scope,
            new("exact", 1, Action with { ActionId = "invented-action" }, "fixture.callback", "delivered")));
        fixture.Store.CompleteSourceInput(scope, new("exact", 1, Action, "fixture.callback", "delivered"));
        fixture.Close(3);
        Assert.Equal("pass", SourceSessionAudit.Audit(fixture.Store.DirectoryPath).Status);
    }

    [Fact]
    public void CatalogCodecDistinguishesNullEmptyUnicodeAndRejectsMalformedStructure()
    {
        // Exact multilingual digest case from Connector native-logical conformance v1.
        SourcePublicAction[] conformance =
        {
            new("action-a", "native_input", "选择", "空 / 🐉 / café / é", null,
                Array.Empty<SourceActionArgument>(), "native"),
            new("action-b", "native_input", "选择", "", "",
                new[] { new SourceActionArgument("目标", "référent-😀") }, "native"),
            new("action-c", "native_input", "选择", "換行\n保留", "卡牌甲",
                new[] { new SourceActionArgument("first", "甲"), new SourceActionArgument("second", "乙") }, "native")
        };
        Assert.Equal("e219492176272b9832799b109a2c770d7801b97e21abf64f6ced9faea8cbcf63",
            SourceCatalogCodec.Digest(conformance));
        Assert.NotEqual(SourceCatalogCodec.Digest(new[] { Action with { SubjectReferentId = null } }),
            SourceCatalogCodec.Digest(new[] { Action with { SubjectReferentId = "" } }));
        Assert.Throws<InvalidDataException>(() => SourceCatalogCodec.Digest(new[] { Action with { Label = "\ud800" } }));
        Assert.Throws<InvalidDataException>(() => SourceCatalogCodec.Digest(new[] { Action, Action }));
        Assert.Throws<InvalidDataException>(() => SourceCatalogCodec.Digest(new[] { Action with
        { Arguments = new[] { new SourceActionArgument("target", "one"), new SourceActionArgument("target", "two") } } }));
        Assert.Throws<InvalidDataException>(() => SourceCatalogCodec.Decode(
            Encoding.UTF8.GetBytes("[{\"action_id\":\"a\",\"kind\":\"native_input\",\"verb\":\"v\",\"label\":\"x\",\"label\":\"y\",\"subject_referent_id\":null,\"arguments\":[],\"effect_domain\":\"native_input\"}]")));
        Assert.Equal(Action.Label, SourceCatalogCodec.Decode(Bytes(new[] { Action }))[0].Label);
    }

    [Fact]
    public void ProfileViewsCannotChangeAccountingAndUnexpectedRawFilesCannotExport()
    {
        using var fixture = new Fixture();
        SourceCaptureProfile view = fixture.Store.SourceProfile!;
        ((IList<string>)view.EagerScope)[0] = "hidden";
        Assert.Equal("persistent", fixture.Store.SourceProfile!.EagerScope[0]);
        Assert.Throws<InvalidDataException>(() => SourceSessionContract.ValidateEnvironment(
            fixture.Environment with { Game = fixture.Environment.Game with { MainAssemblySha256 = "bad" } }));
        fixture.Store.AppendPublicObservation(fixture.Packet(1));
        fixture.Close(2);
        File.WriteAllText(Path.Combine(fixture.Store.DirectoryPath, "unexpected-private-data.json"), "fixture");
        SourceSessionAuditResult audit = SourceSessionAudit.Audit(fixture.Store.DirectoryPath);
        Assert.Contains("source_unexpected_raw_file", audit.Errors);
        Assert.Throws<InvalidDataException>(() => SourceSessionBundlePacker.Pack(fixture.Store.DirectoryPath,
            "worker", "campaign", Path.Combine(fixture.Root, "blocked-bundle"), new string('c', 40)));
    }

    [Fact]
    public void CapacityAndDiskFailureNeverProduceAFalseCloseOrBundle()
    {
        using var capacity = new Fixture(new(MaxRowsPerStream: 1));
        capacity.Store.AppendPublicObservation(capacity.Packet(1));
        Assert.Throws<IOException>(() => capacity.Store.AppendPublicObservation(capacity.Packet(2)));
        Assert.False(capacity.Store.GetSourceStatus()!.AccountingComplete);
        Assert.Throws<IOException>(() => capacity.Close(3));
        Assert.False(File.Exists(Path.Combine(capacity.Store.DirectoryPath, "source-close-receipt.json")));
        Assert.Equal("fail", SourceSessionAudit.Audit(capacity.Store.DirectoryPath).Status);
        using var disk = new Fixture();
        File.WriteAllText(Path.Combine(disk.Store.DirectoryPath, "public-captures"), "fixture blocks directory creation");
        Assert.ThrowsAny<IOException>(() => disk.Store.PersistPublicCapture(disk.Capture));
        Assert.False(disk.Store.GetSourceStatus()!.AccountingComplete);
        Assert.Throws<IOException>(() => disk.Close(3));
    }

    [Fact]
    public void GenericPackIsImmutableAndSourceSessionCannotEnterHumanBundle()
    {
        using var fixture = new Fixture();
        fixture.Store.AppendPublicObservation(fixture.Packet(1));
        var basis = fixture.Persist();
        SourceInputScope exact = fixture.Store.BeginSourceInput("input-exact-fixture", Clock(2),
            basis.Capture, basis.Catalog, RecordingLifecycleState.Recording);
        fixture.Store.CompleteSourceInput(exact, new("exact", 1, Action, "fixture.native_callback", "delivered"));
        fixture.Store.BeginSourceInput("input-unfinished-fixture", Clock(2),
            basis.Capture, basis.Catalog, RecordingLifecycleState.Recording);
        fixture.Close(3);
        string bundle = Path.Combine(fixture.Root, "source-bundle");
        var packed = SourceSessionBundlePacker.Pack(fixture.Store.DirectoryPath, "worker-fixture", "campaign-fixture",
            bundle, new string('c', 40));
        var retry = SourceSessionBundlePacker.Pack(fixture.Store.DirectoryPath, "worker-fixture", "campaign-fixture",
            bundle, new string('c', 40));
        Assert.Equal(packed.BundleContentId, retry.BundleContentId);
        Assert.Equal(2, packed.InputCount);
        Assert.Throws<IOException>(() => SourceSessionBundlePacker.Pack(fixture.Store.DirectoryPath,
            "other-worker", "campaign-fixture", bundle, new string('c', 40)));
        Assert.Throws<InvalidDataException>(() => SessionBundlePacker.Pack(fixture.Store.DirectoryPath,
            "worker-fixture", "campaign-fixture", Path.Combine(fixture.Root, "human-bundle"), new string('c', 40), true));
        using JsonDocument manifest = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(bundle, "source-session-bundle-manifest.json")));
        Assert.False(manifest.RootElement.GetProperty("human_origin_attested").GetBoolean());
        Assert.Equal(SourceSessionContract.BundleSchema, manifest.RootElement.GetProperty("schema").GetString());
        Assert.Equal(File.ReadAllBytes(Path.Combine(bundle, "raw", "public-observations.jsonl")),
            File.ReadAllBytes(Path.Combine(bundle, "export", "public-observations.jsonl")));
        string? golden = System.Environment.GetEnvironmentVariable("SOURCE_SESSION_FIXTURE_DIRECTORY");
        if (golden != null)
        {
            if (Directory.Exists(golden)) throw new IOException("Fixture destination must be new.");
            Copy(bundle, golden);
        }
        static void Copy(string source, string destination)
        {
            Directory.CreateDirectory(destination);
            foreach (string directory in Directory.GetDirectories(source, "*", SearchOption.AllDirectories))
                Directory.CreateDirectory(Path.Combine(destination, Path.GetRelativePath(source, directory)));
            foreach (string file in Directory.GetFiles(source, "*", SearchOption.AllDirectories))
                File.Copy(file, Path.Combine(destination, Path.GetRelativePath(source, file)));
        }
    }
}
