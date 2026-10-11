using System.Collections.Concurrent;
using System.Runtime.CompilerServices;
using System.Runtime.ExceptionServices;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector;
using STS2Connector.Authority;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2HumanAnnotator.Core;
using STS2HumanAnnotator.Mod;
using STS2Platform.NativeFoundation;
using Xunit;

[assembly: CollectionBehavior(DisableTestParallelization = true)]
namespace STS2HumanAnnotator.SourceNative.Tests;

public sealed class SourceNativeProducerTests
{
    private sealed class Lifetime(MutationControllerCoordinator owner) : INativeLogicalClientLifetimeDependency
    { public bool TryTouchActiveClient(string clientSessionId) => owner.TryTouchActiveClient(clientSessionId); }
    private static readonly JsonSerializerOptions Json = new(EvidenceJson.Options) { DefaultIgnoreCondition = JsonIgnoreCondition.Never };
    internal sealed class Fixture : IDisposable
    {
        private readonly BlockingCollection<Action> nativeCommands = new();
        private readonly Thread nativeThread;
        internal readonly string Root = Path.Combine(Path.GetTempPath(), "source-native-tests-" + Guid.NewGuid().ToString("N"));
        internal readonly RecorderEnvironmentIdentity EnvironmentIdentity = new(new("fixture-game", "fixture-commit", new string('a', 64), "game-mvid"),
            new("Connector", "fixture", new string('b', 40), new string('c', 64), new string('d', 64), "connector-mvid"),
            new("Annotator", "fixture", new string('e', 40), new string('f', 64), new string('a', 64), "annotator-mvid"),
            "1.0.0", "runtime-fixture", "environment-fixture", "fixture", "modset-fixture");
        internal NativeLogicalService Owner = null!;
        internal RequestNamespace Requests = null!;
        internal NativeLogicalSourceRecordingAttachment Attachment = null!;
        internal RecordingSessionStore Store = null!;
        internal SourceRecordingWorkerV2 Worker = null!;
        internal RunState? Actual;
        internal string? Continuity;
        internal string Surface = "title";
        internal Action? OnDispatch;
        internal int Dispatches, SourceRegistrations;
        internal Action? OnCapture;
        internal readonly object PhysicalOwner = new();
        internal int PhysicalMatchCount = 1;
        internal readonly MutationControllerCoordinator Controller = new("runtime-fixture", enableDeadlineTimer: false);
        internal readonly MutationClientRegistrationResult client;
        private readonly MutationLease lease;
        internal Fixture(int version = 2, Action<Fixture>? beforeInitialCapture = null)
        {
            client = Controller.Register(new("source-producer-test", "test", "Source producer", "1"));
            lease = Controller.Acquire(new(client.Client.ClientSessionId, null, null)).Controller!;
            nativeThread = new Thread(() => { foreach (var work in nativeCommands.GetConsumingEnumerable()) work(); }) { IsBackground = true };
            nativeThread.Start();
            Invoke(() =>
            {
                Requests = new("runtime-fixture", Controller.ValidateActiveControl, Controller.TryAdmitRequest, Controller.TryBegin,
                    (request, result) => Owner.NotifyOriginalTerminal(request, result), enableTimer: false);
                Owner = new(Frame, () => Continuity, Requests,
                    (work, _) => Task.FromResult(work()), () => true, new Lifetime(Controller), Controller.IsActiveClient,
                    () => Actual, () => (Capabilities(), new string('c', 64)),
                    request => { SourceRegistrations++; return Controller.Register(request); });
                Controller.ClientClosed += closure => { Requests.ClientClosed(closure); Owner.ExpireClient(closure.ClientSessionId, closure.Reason); };
                Owner.Initialize();
                Owner.InstallPublicationProfile(NativeLogicalPublicationProfile.ProfileId, NativeLogicalPublicationProfile.DefinitionSha256,
                    NativeLogicalPublicationProfile.RequiredCoverage);
                if (beforeInitialCapture != null) OnCapture = () => beforeInitialCapture(this);
                Attachment = Owner.AttachSource(version == 3);
                var epoch = SourceRecordingWorkerV2.Packet(Attachment.InitialEpoch, EnvironmentIdentity);
                var profile = new SourceCaptureProfileV2("sts2.annotator/source-capture-profile-" + version, "native-logical-source-v" + version,
                    "native-logical-v1", epoch.Context.PublicationProfileId, epoch.Context.PublicationProfileDefinitionSha256,
                    epoch.Context.EagerScope, new(), SourceSessionContractV2.NonClaims);
                var manifest = new CurrentRecordingManifest(version, "sts2.annotator/source-session-manifest-" + version, "session-fixture", "timeline-fixture", DateTimeOffset.UnixEpoch,
                    "fixture", new string('e', 40), "fixture-platform", profile.ProfileId, SourceSessionContractV2.ProfileDigest(profile),
                    Array.Empty<string>(), SourceSessionContractV2.NonClaims) { SourceSchemaVersion = version, SourceEnvironment = EnvironmentIdentity, RecoverySchemaVersion = 1 };
                Store = version == 3 ? RecordingSessionStore.CreateSourceV3(Root, manifest, profile, new("agent_protocol", "actor-original", "declaration-original"), epoch)
                    : RecordingSessionStore.CreateSourceV2(Root, manifest, profile, new("agent_protocol", "actor-original", "declaration-original"), epoch);
                Worker = new(Store, Attachment, EnvironmentIdentity);
            });
        }
        internal void Invoke(Action work)
        {
            using var done = new ManualResetEventSlim(); Exception? error = null;
            nativeCommands.Add(() => { try { work(); } catch (Exception e) { error = e; } finally { done.Set(); } });
            if (!done.Wait(5000)) throw new TimeoutException("Synthetic native owner command timed out.");
            if (error != null) ExceptionDispatchInfo.Capture(error).Throw();
        }
        internal T Invoke<T>(Func<T> work)
        { T result = default!; Invoke(() => { result = work(); }); return result; }
        internal void Setup(bool fresh, bool earlyCallback = true)
        {
            var previous = Actual; var next = (RunState)RuntimeHelpers.GetUninitializedObject(typeof(RunState));
            var invocation = NativeRunLifecycleProvider.StageSetup(next, previous, fresh);
            Actual = next; Continuity = "run-" + RuntimeHelpers.GetHashCode(next); Surface = fresh ? "prepared-new" : "prepared-saved";
            if (earlyCallback) Owner.Publish("native_owner_ready", "early_setup");
            NativeRunLifecycleProvider.FinishSetup(invocation, Actual);
        }
        internal NativeLogicalResult Input(string requestId) => SubmitInput(PrepareInput(requestId));
        internal PlayerEnvironmentActionRequest PrepareInput(string requestId)
        {
            var current = Owner.CurrentAsync(new(client.Client.ClientSessionId, NativeLogicalProjector.ScopeFields, null)).GetAwaiter().GetResult();
            var action = Assert.Single(Owner.Store.Catalog(current.Capture!.CaptureId).Actions);
            return new PlayerEnvironmentActionRequest(requestId, current.Capture.SnapshotId, action.ActionId, client.Client.ClientSessionId,
                lease.ControllerLeaseId, lease.ControllerGeneration, NativeLogicalContract.Profile);
        }
        internal NativeLogicalResult SubmitInput(PlayerEnvironmentActionRequest request)
        {
            var admitted = Requests.Admit(request); Assert.Equal("admitted", admitted.Status);
            Assert.True(Owner.RunAdmitted(request));
            using var terminal = admitted.OriginalCompletion!.GetAwaiter().GetResult();
            using var bytes = new MemoryStream(); terminal.WriteTo(bytes);
            return JsonSerializer.Deserialize<NativeLogicalResult>(bytes.ToArray(), ConnectorMod._jsonOptions)!;
        }
        internal TextMenuFrame Frame()
        {
            OnCapture?.Invoke();
            var page = new PlayerEnvironmentSnapshot("1.0.0", PlayerEnvironmentContract.SnapshotSchema, "fixture-source", 1, DateTimeOffset.UnixEpoch,
                "interactive", null, new("interaction", Surface, "ready", null, "fixture-surface",
                    new(new JsonObject { ["kind"] = Surface }, new JsonObject { ["kind"] = "fixture-context" }), Array.Empty<PlayerEnvironmentInteractionCapability>()),
                Array.Empty<PlayerEnvironmentReferent>(), new("sts2.player-environment/bound-actions-1", "complete", 0, 0, 65536, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
                Array.Empty<PlayerEnvironmentReadOpportunity>(), new("complete", "public", "complete", Array.Empty<string>(), Array.Empty<string>()),
                new("runtime-fixture", "environment-fixture"), new("player_visible_v1", "current_page", false, "omit"));
            var leaves = Enumerable.Range(0, Math.Max(1, PhysicalMatchCount)).Select(index => new TextMenuLeaf("binding-" + Surface + "-" + index,
                "root", "confirm", "Confirm", null, Array.Empty<PlayerEnvironmentBoundActionArgument>(),
                () => { Dispatches++; OnDispatch?.Invoke(); return NativeInputResult.Delivered("synthetic_native_callback"); },
                PhysicalMatchCount == 0 ? null : new TextMenuNativeWitnessBinding(PhysicalOwner, null, new Dictionary<string, object>(StringComparer.Ordinal)))).ToArray();
            return new(page, Surface, leaves) { GameContinuityId = Continuity };
        }
        private static PlayerEnvironmentCapabilitiesResponse Capabilities() => new(PlayerEnvironmentContract.ProtocolVersion, TextMenuContract.SnapshotSchema,
            PlayerEnvironmentContract.ActionSchema, TextMenuContract.ResultSchema, PlayerEnvironmentContract.ControlSchema, "implemented",
            new("host", "Host", "1", "runtime-fixture", "live_ui", new(new string('b', 40), "mvid", new string('d', 64))),
            new("game", "commit", "branch", 1, new("supported", true, "synthetic"), new("complete", "fingerprint", "test", Array.Empty<string>(), "synthetic")),
            "environment-fixture", Array.Empty<string>(), true, true, false, new(1000), Array.Empty<PlayerEnvironmentEvidenceProfile>(), Array.Empty<string>()) { InputProfile = TextMenuContract.Profile };
        internal T[] Rows<T>(string file) => File.ReadLines(Path.Combine(Store.DirectoryPath, file)).Select(x => JsonSerializer.Deserialize<T>(x, Json)!).ToArray();
        internal async Task Close()
        { Invoke(() => Worker.CommandBoundary("close")); await Worker.Completion.WaitAsync(TimeSpan.FromSeconds(6)); }
        public void Dispose()
        {
            Invoke(() => { Worker?.Dispose(); Requests?.Dispose(); Owner?.Dispose(); }); nativeCommands.CompleteAdding(); nativeThread.Join(2000);
            try { Store?.Dispose(); } catch { }
            try { Directory.Delete(Root, true); } catch (IOException) { }
        }
    }
    [Theory]
    [InlineData(2)]
    [InlineData(3)]
    public async Task TitlePreparedLaunchTerminalAndSummaryRemainOneStoreWithOriginalInputEpochAndActor(int version)
    {
        using var f = new Fixture(version); string originalEpoch = f.Attachment.InitialEpoch.EpochId;
        f.Invoke(() => f.OnDispatch = () =>
        {
            f.Setup(true); f.Owner.Publish("native_owner_ready", "inside_Launch");
            NativeRunLifecycleProvider.ObserveLaunch(f.Actual!);
        });
        var result = f.Invoke(() => f.Input("input-title")); Assert.Equal("delivered", result.Delivery);
        f.Invoke(() =>
        {
            f.Worker.CommandBoundary("pause");
            f.Worker.ChangeSource(new("declared_human", "actor-after", "declaration-after"), f.Store.GetSourceStatusV2()!.SegmentId);
            f.Worker.CommandBoundary("resume");
            NativeRunLifecycleProvider.ObserveTerminal(f.Actual, false);
            f.Surface = "summary"; f.Owner.Publish("native_terminal_entry", "summary_navigation");
        });
        await f.Close();
        var epochs = f.Rows<SourceAttachmentEpochV2>("source-attachment-epochs.jsonl"); Assert.Equal(2, epochs.Length);
        Assert.Equal(originalEpoch, epochs[0].EpochId); Assert.Equal("setup_handoff", epochs[1].Transition!.Kind);
        var input = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
        Assert.Equal(originalEpoch, input.EpochId); Assert.Equal("exact", input.Outcome.MappingStatus);
        var source = f.Rows<SourceSegmentV2>("source-segments.jsonl").Single(x => x.SegmentId == input.SegmentId);
        Assert.Equal("actor-original", source.Declaration.ActorId);
        var boundaries = f.Rows<SourceBoundaryV2>("source-boundaries.jsonl");
        Assert.Single(boundaries, x => x.Kind == "launch"); Assert.Single(boundaries, x => x.Kind == "terminal");
        Assert.All(boundaries.Where(x => x.Kind is "launch" or "terminal"), x => Assert.Equal(epochs[1].EpochId, x.Position.EpochId));
        using var receipt = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(f.Store.DirectoryPath, "source-close-receipt.json")));
        Assert.Equal(2, receipt.RootElement.GetProperty("final_drains").GetArrayLength()); Assert.True(receipt.RootElement.GetProperty("accounting_complete").GetBoolean());
        Assert.Equal(1, f.SourceRegistrations);
        Assert.Equal(1, f.Dispatches); Assert.False(File.Exists(Path.Combine(f.Store.DirectoryPath, "human-session-attestation.json")));
        var audit = version == 3 ? SourceSessionAuditV3.Audit(f.Store.DirectoryPath) : SourceSessionAuditV2.Audit(f.Store.DirectoryPath); Assert.True(audit.Status == "pass", string.Join(",", audit.Errors));
        string bundle = Path.Combine(f.Root, "bundle");
        var packed = version == 3 ? SourceSessionBundlePackerV3.Pack(f.Store.DirectoryPath, "worker-synthetic", "campaign-source-v3", bundle, new string('e', 40))
            : SourceSessionBundlePackerV2.Pack(f.Store.DirectoryPath, "worker-synthetic", "campaign-source-v2", bundle, new string('e', 40));
        Assert.Equal("pass", packed.Status);
        string? golden = Environment.GetEnvironmentVariable(version == 3 ? "STS2_SOURCE_V3_SYNTHETIC_GOLDEN" : "STS2_SOURCE_V2_SYNTHETIC_GOLDEN");
        if (!string.IsNullOrEmpty(golden)) CopyGolden(bundle, Path.GetFullPath(golden));

    }
    internal static void CopyGolden(string source, string output)
    {
        Directory.CreateDirectory(output);
        foreach (var path in Directory.EnumerateFiles(source, "*", SearchOption.AllDirectories))
        {
            string target = Path.Combine(output, Path.GetRelativePath(source, path));
            Directory.CreateDirectory(Path.GetDirectoryName(target)!); File.Copy(path, target, overwrite: false);
        }
    }
    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public async Task NoEarlyCallbackHandoffAndSavedPreparationNeverManufactureLaunch(bool fresh)
    {
        using var f = new Fixture(); f.Invoke(() => f.Setup(fresh, false)); await f.Close();
        var epochs = f.Rows<SourceAttachmentEpochV2>("source-attachment-epochs.jsonl"); Assert.Equal(2, epochs.Length);
        Assert.Equal(fresh ? "new" : "saved", epochs[1].Transition!.StartProvenance);
        Assert.DoesNotContain(f.Rows<SourceBoundaryV2>("source-boundaries.jsonl"), x => x.Kind == "launch");
        var audit = SourceSessionAuditV2.Audit(f.Store.DirectoryPath); Assert.True(audit.Status == "pass", string.Join(",", audit.Errors));
        string bundle = Path.Combine(f.Root, "prepared-bundle");
        Assert.Equal("pass", SourceSessionBundlePackerV2.Pack(f.Store.DirectoryPath, "prepared-synthetic", "campaign-source-v2", bundle, new string('e', 40)).Status);
        string? preparedGolden = Environment.GetEnvironmentVariable("STS2_SOURCE_V2_PREPARED_GOLDEN");
        if (fresh && !string.IsNullOrEmpty(preparedGolden)) CopyGolden(bundle, Path.GetFullPath(preparedGolden));
    }
    [Fact]
    public async Task PauseAcrossRolloverClosesOriginalIntervalsWithoutAutomaticChildSession()
    {
        using var f = new Fixture(); f.Invoke(() => { f.Worker.CommandBoundary("pause"); f.Setup(true); f.Owner.Publish("native_owner_ready", "still_paused"); });
        await f.Close(); var rows = f.Rows<SourceBoundaryV2>("source-boundaries.jsonl");
        Assert.Equal(2, rows.SelectMany(x => x.PausedIntervals).Count()); Assert.Equal(2, f.Store.GetSourceStatusV2()!.Epochs);
        Assert.All(rows.SelectMany(x => x.PausedIntervals), x => Assert.Equal("recording_paused", x.Reason));
        var audit = SourceSessionAuditV2.Audit(f.Store.DirectoryPath); Assert.True(audit.Status == "pass", string.Join(",", audit.Errors));
    }
    [Fact]
    public async Task CleanupAfterTerminalCarriesActualPrePostContinuityWithoutLegacyInProgressGate()
    {
        using var f = new Fixture(); f.Invoke(() => { f.Setup(true); NativeRunLifecycleProvider.ObserveLaunch(f.Actual!); NativeRunLifecycleProvider.ObserveTerminal(f.Actual, true); });
        f.Invoke(() => { var previous = f.Actual; f.Actual = null; f.Continuity = null; f.Surface = "title-after-cleanup"; NativeRunLifecycleProvider.ObserveCleanup(previous, null, true); });
        await f.Close(); var epochs = f.Rows<SourceAttachmentEpochV2>("source-attachment-epochs.jsonl"); Assert.Equal(3, epochs.Length);
        Assert.Equal("cleanup", epochs[2].Transition!.Kind); Assert.Null(epochs[2].Context.GameContinuityId);
        var audit = SourceSessionAuditV2.Audit(f.Store.DirectoryPath); Assert.True(audit.Status == "pass", string.Join(",", audit.Errors));
    }
    [Fact]
    public async Task ContinuityChangeWithoutTypedNativeWitnessFailsAccountingWhileNativePublicationStillProceeds()
    {
        using var f = new Fixture();
        f.Invoke(() => { f.Actual = (RunState)RuntimeHelpers.GetUninitializedObject(typeof(RunState)); f.Continuity = "unwitnessed-run"; f.Surface = "run"; f.Owner.Publish("native_owner_ready", "unwitnessed"); });
        await Assert.ThrowsAnyAsync<Exception>(() => f.Worker.Completion.WaitAsync(TimeSpan.FromSeconds(4)));
        Assert.False(f.Store.GetSourceStatusV2()!.AccountingComplete);
        Assert.False(File.Exists(Path.Combine(f.Store.DirectoryPath, "source-close-receipt.json")));
        Assert.NotEqual(f.Attachment.InitialEpoch.Subscription.StreamGeneration, f.Owner.Hub.StreamGeneration);
    }
    private sealed class SignalledSink(INativeLogicalSourceSink inner) : INativeLogicalSourceSink
    {
        internal readonly TaskCompletionSource BasisSeen = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public void Epoch(NativeLogicalSourceEpoch epoch) => inner.Epoch(epoch);
        public void Boundary(NativeLogicalSourceTransition transition, NativeLogicalSourcePosition position) => inner.Boundary(transition, position);
        public object? AdmitInput(NativeLogicalSourceInputPrefix prefix) => inner.AdmitInput(prefix);
        public void InputBasis(object token, string? capture, IDisposable? retained, string? missing)
        { try { inner.InputBasis(token, capture, retained, missing); } finally { BasisSeen.TrySetResult(); } }
        public void InputTerminal(object token, NativeLogicalSourceInputTerminal terminal) => inner.InputTerminal(token, terminal);
        public void AccountingFailed(string code) => inner.AccountingFailed(code);
    }
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task OriginalInputEncodingDeadlineCannotBeExtendedWhileBothEncoderAndDiskAreHeld(bool closeBeforeInputDrains)
    {
        using var f = new Fixture();
        var request = f.Invoke(() => f.PrepareInput("late-original-basis"));
        object diskGate = typeof(RecordingSessionStore).GetField("_gate", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(f.Store)!;
        object projector = typeof(NativeLogicalService).GetField("projector", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(f.Owner)!;
        object encoderGate = typeof(NativeLogicalProjector).GetField("gate", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(projector)!;
        using var diskEntered = new ManualResetEventSlim(); using var diskRelease = new ManualResetEventSlim();
        using var encoderEntered = new ManualResetEventSlim(); using var encoderRelease = new ManualResetEventSlim();
        var diskHeld = Task.Run(() => { lock (diskGate) { diskEntered.Set(); diskRelease.Wait(); } });
        var encoderHeld = Task.Run(() => { lock (encoderGate) { encoderEntered.Set(); encoderRelease.Wait(); } });
        var sink = new SignalledSink(f.Attachment.Sink!);
        try
        {
            Assert.True(diskEntered.Wait(TimeSpan.FromSeconds(2))); Assert.True(encoderEntered.Wait(TimeSpan.FromSeconds(2)));
            var result = f.Invoke(() =>
            {
                f.Attachment.Sink = sink;
                // These real metadata appends force the production disk worker
                // to wait on the held store gate before it can expire the input.
                f.Worker.CommandBoundary("pause"); f.Worker.CommandBoundary("resume");
                return f.SubmitInput(request);
            });
            Assert.Equal("delivered", result.Delivery);
            await Task.Delay(2200);
            encoderRelease.Set(); await encoderHeld;
            // This is the real original encoder callback while the disk worker
            // still cannot drain; no simulated prefix or terminal is injected.
            await sink.BasisSeen.Task.WaitAsync(TimeSpan.FromSeconds(3));
        }
        finally { encoderRelease.Set(); diskRelease.Set(); await encoderHeld; await diskHeld; }
        if (closeBeforeInputDrains) await f.Close();
        else Assert.True(await Task.Run(() => SpinWait.SpinUntil(() => f.Store.GetSourceStatusV2()!.Inputs == 1, TimeSpan.FromSeconds(3))));
        var input = Assert.Single(f.Rows<SourceNativeInputWitnessV2>("native-input-witnesses.jsonl"));
        Assert.Equal("capture_missing", input.Outcome.MappingStatus);
        Assert.Equal("source_input_basis_encoding_timeout", input.Outcome.ReasonCode);
        Assert.Null(input.PreCapture); Assert.Null(input.Catalog); Assert.Equal("delivered", input.Outcome.Delivery);
        if (!closeBeforeInputDrains) await f.Close();
        Assert.Equal("pass", SourceSessionAuditV2.Audit(f.Store.DirectoryPath).Status);
    }
    [Fact]
    public async Task HeldDiskGateCannotBlockNativeEpochAndPauseAdmission()
    {
        using var f = new Fixture();
        object diskGate = typeof(RecordingSessionStore).GetField("_gate", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(f.Store)!;
        using var entered = new ManualResetEventSlim(); using var release = new ManualResetEventSlim();
        var heldDisk = Task.Run(() => { lock (diskGate) { entered.Set(); release.Wait(); } });
        Assert.True(entered.Wait(TimeSpan.FromSeconds(2)));
        try
        {
            await Task.Run(() => f.Invoke(() => { f.Setup(true); f.Worker.CommandBoundary("pause"); }))
                .WaitAsync(TimeSpan.FromSeconds(2));
            Assert.Equal(2, f.Store.GetSourceStatusV2()!.Epochs);
        }
        finally { release.Set(); await heldDisk; }
        await f.Close();
        var audit = SourceSessionAuditV2.Audit(f.Store.DirectoryPath); Assert.True(audit.Status == "pass", string.Join(",", audit.Errors));
    }
    [Fact]
    public async Task MetadataSaturationDuringHeldDiskFailsSourceWithoutStoppingNativePublication()
    {
        using var f = new Fixture();
        object diskGate = typeof(RecordingSessionStore).GetField("_gate", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(f.Store)!;
        using var entered = new ManualResetEventSlim(); using var release = new ManualResetEventSlim();
        var heldDisk = Task.Run(() => { lock (diskGate) { entered.Set(); release.Wait(); } });
        Assert.True(entered.Wait(TimeSpan.FromSeconds(2)));
        try
        {
            await Task.Run(() => f.Invoke(() =>
            {
                f.Setup(true);
                for (int index = 0; index < 36; index++) NativeRunLifecycleProvider.ObserveLaunch(f.Actual!);
                f.Owner.Publish("native_owner_ready", "after_source_metadata_saturation");
            })).WaitAsync(TimeSpan.FromSeconds(2));
            Assert.False(f.Store.GetSourceStatusV2()!.AccountingComplete);
            Assert.Equal("source_metadata_packet_capacity", f.Store.GetSourceStatusV2()!.Error);
            Assert.NotEqual(f.Attachment.InitialEpoch.Subscription.StreamGeneration, f.Owner.Hub.StreamGeneration);
        }
        finally { release.Set(); await heldDisk; }
        await Assert.ThrowsAnyAsync<Exception>(() => f.Worker.Completion.WaitAsync(TimeSpan.FromSeconds(4)));
        Assert.False(File.Exists(Path.Combine(f.Store.DirectoryPath, "source-close-receipt.json")));
    }
    [Fact]
    public async Task ForeignClientRevocationCannotPoisonSourceButOriginalSourceClientRevocationFailsAccounting()
    {
        using var f = new Fixture();
        f.Invoke(() =>
        {
            var foreign = f.Controller.Register(new("unrelated-source-client", "test", "Unrelated", "1"));
            f.Controller.Revoke(new("runtime-fixture", foreign.Client.ClientSessionId));
            f.Setup(true);
        });
        Assert.True(f.Store.GetSourceStatusV2()!.AccountingComplete);
        f.Invoke(() => f.Controller.Revoke(new("runtime-fixture", f.Attachment.ClientId)));
        await Assert.ThrowsAnyAsync<Exception>(() => f.Worker.Completion.WaitAsync(TimeSpan.FromSeconds(4)));
        Assert.Equal("source_client_expired", f.Store.GetSourceStatusV2()!.Error);
        Assert.False(File.Exists(Path.Combine(f.Store.DirectoryPath, "source-close-receipt.json")));
    }
    [Fact]
    public async Task ExplicitNewAttachmentAfterOriginalRevocationUsesANewClientAndCannotReviveTheOldOwner()
    {
        using var f = new Fixture(); string originalClient = f.Attachment.ClientId;
        f.Invoke(() => f.Controller.Revoke(new("runtime-fixture", originalClient)));
        await Assert.ThrowsAnyAsync<Exception>(() => f.Worker.Completion.WaitAsync(TimeSpan.FromSeconds(4)));
        f.Invoke(() =>
        {
            using var next = f.Owner.AttachSource();
            Assert.NotEqual(originalClient, next.ClientId);
            Assert.Equal(2, f.SourceRegistrations);
            Assert.False(f.Controller.IsActiveClient(originalClient));
            Assert.True(f.Controller.IsActiveClient(next.ClientId));
            Assert.Throws<NativeLogicalException>(() => f.Attachment.ReadBoundary());
            Assert.Equal(next.InitialEpoch.EpochId, next.ReadBoundary().Position.EpochId);
        });
    }
    [Fact]
    public async Task OriginalClientRevocationDuringClosingRetainsFailureBeforeSuccessfulReceipt()
    {
        using var f = new Fixture();
        object diskGate = typeof(RecordingSessionStore).GetField("_gate", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(f.Store)!;
        using var entered = new ManualResetEventSlim(); using var release = new ManualResetEventSlim();
        var heldDisk = Task.Run(() => { lock (diskGate) { entered.Set(); release.Wait(); } });
        Assert.True(entered.Wait(TimeSpan.FromSeconds(2)));
        try
        {
            await Task.Run(() => f.Invoke(() =>
            {
                f.Worker.CommandBoundary("close");
                f.Controller.Revoke(new("runtime-fixture", f.Attachment.ClientId));
            })).WaitAsync(TimeSpan.FromSeconds(2));
        }
        finally { release.Set(); await heldDisk; }
        await Assert.ThrowsAnyAsync<Exception>(() => f.Worker.Completion.WaitAsync(TimeSpan.FromSeconds(4)));
        Assert.Equal("source_client_expired", f.Store.GetSourceStatusV2()!.Error);
        Assert.False(File.Exists(Path.Combine(f.Store.DirectoryPath, "source-close-receipt.json")));
    }
    [Fact]
    public async Task ForeignCurrentScopeCannotBeCopiedAsAnOriginalSourceEpoch()
    {
        using var f = new Fixture();
        var current = f.Invoke(() => f.Owner.CurrentAsync(new(f.client.Client.ClientSessionId, NativeLogicalProjector.ScopeFields, null)).GetAwaiter().GetResult());
        Assert.NotEqual(f.Attachment.InitialEpoch.Subscription.ScopeId, current.Capture!.ScopeId);
        Assert.Equal("source_capture_scope_mismatch", Assert.Throws<NativeLogicalException>(() => f.Attachment.CopyFrozen(current.Capture.CaptureId)).Code);
        Assert.True(f.Store.GetSourceStatusV2()!.AccountingComplete);
        await f.Close();
    }
}
