using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment.NativeLogical;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector.Tests;

public sealed class NativeLogicalCoreTests
{
    private readonly Xunit.Abstractions.ITestOutputHelper output;
    public NativeLogicalCoreTests(Xunit.Abstractions.ITestOutputHelper output) { this.output = output; }
    private static JsonElement Json(string text) => JsonDocument.Parse(text).RootElement.Clone();
    private static NativeLogicalAction Action(int i, string? subject = null) => new("action-" + i, "native_input", i % 2 == 0 ? "pick" : "inspect", "卡牌 " + i, subject,
        new[] { new NativeLogicalArgument("target", "目标-" + i) }, "native");
    private static NativeLogicalCatalog Catalog(IEnumerable<NativeLogicalAction> actions, Func<long>? clock = null, NativeLogicalLimits? limits = null) => new("snapshot", "generation", "scope", actions, 120000, clock ?? (() => 0), limits: limits);
    private static NativeLogicalPublicFrame Frame(string owner = "A", string occurrence = "1", string binding = "1") => new("generation", new("runtime", "fingerprint"),
        new(owner, occurrence, binding, null, null), "interactive", new("persistent", new JsonObject { ["value"] = 1 }),
        new("interaction", "selector", "ready", "选择", "surface", new(new JsonObject { ["nested"] = new JsonArray(1, 2) }, new JsonObject { ["kind"] = "selector" }), Array.Empty<PlayerEnvironmentInteractionCapability>()),
        new[] { new PlayerEnvironmentReferent("public-card", "card", "card", "甲", new(true, true, false, false, "displayed"), "card", new JsonObject { ["name"] = "甲" }) },
        new("fair", "current", false, "explicit"), new[] { new NativeLogicalLeaf("pick", "甲", "public-card", Array.Empty<NativeLogicalArgument>(), "native") { BindingKey = "private-leaf" } },
        new("complete", Array.Empty<string>()));
    [Theory]
    [InlineData(200)] [InlineData(500)] [InlineData(10000)]
    public void FullRelationPaginationPrefixUnionAndResolve(int count)
    {
        var original = Enumerable.Range(0, count).Select(i => Action(i)).ToArray();
        var catalog = Catalog(original); var assembled = new List<NativeLogicalAction>(); string? cursor = null;
        do { var page = catalog.List("generation", cursor: cursor, limit: 73); Assert.Equal(count, page.TotalCount); assembled.AddRange(page.Actions); cursor = page.NextCursor; } while (cursor != null);
        Assert.Equal(original.Select(a => a.ActionId), assembled.Select(a => a.ActionId));
        var union = new List<NativeLogicalAction>();
        foreach (string verb in new[] { "pick", "inspect" })
        {
            cursor = null;
            do { var page = catalog.List("generation", Json("{\"verb\":\"" + verb + "\"}"), cursor, 67); Assert.Equal(count / 2, page.FilteredCount); union.AddRange(page.Actions); cursor = page.NextCursor; } while (cursor != null);
        }
        Assert.Equal(count, union.Select(a => a.ActionId).Distinct().Count());
        foreach (var action in original.Take(2).Append(original[^1]))
        {
            var expression = JsonSerializer.SerializeToElement(new { verb = action.Verb, subject_referent_id = action.SubjectReferentId, arguments = action.Arguments }, NativeLogicalWire.Options);
            Assert.Equal(action.ActionId, catalog.Resolve("generation", expression).Action?.ActionId);
        }
        byte[] encoded = NativeLogicalWire.Encode(catalog.List("generation", limit: count));
        Assert.True(encoded.Length <= new NativeLogicalLimits().MaxPageBytes);
    }
    [Fact]
    public void SharedIndependentDigestAndGrammarFixturesAgree()
    {
        using var doc = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "native-logical-v1.json")));
        foreach (var fixture in doc.RootElement.GetProperty("digest_cases").EnumerateArray())
        {
            var actions = JsonSerializer.Deserialize<NativeLogicalAction[]>(fixture.GetProperty("actions"), NativeLogicalWire.Options)!;
            Assert.Equal(fixture.GetProperty("sha256").GetString(), NativeLogicalCatalog.Digest(actions));
        }
        foreach (var fixture in doc.RootElement.GetProperty("invalid_action_cases").EnumerateArray())
        {
            var actions = JsonSerializer.Deserialize<NativeLogicalAction[]>(fixture.GetProperty("actions"), NativeLogicalWire.Options)!;
            Assert.Throws<NativeLogicalException>(() => NativeLogicalCatalog.Digest(actions));
        }
        var catalog = Catalog(new[] { Action(0) });
        foreach (var fixture in doc.RootElement.GetProperty("expression_cases").EnumerateArray())
        {
            JsonElement expression = fixture.GetProperty("expression");
            if (fixture.GetProperty("prefix_valid").GetBoolean()) catalog.List("generation", expression);
            else Assert.Throws<NativeLogicalException>(() => catalog.List("generation", expression));
            Assert.Equal(fixture.GetProperty("resolve_valid").GetBoolean(), catalog.Resolve("generation", expression).Status != "invalid_expression");
        }
        foreach (var raw in doc.RootElement.GetProperty("raw_invalid_expressions").EnumerateArray())
            Assert.Equal("invalid_expression", catalog.Resolve("generation", Json(raw.GetString()!)).Status);
    }
    [Fact]
    public void NullAbsentEmptyAndArgumentOrderRemainDifferent()
    {
        var a = Action(1, null) with { Verb = "pick", Arguments = Array.Empty<NativeLogicalArgument>() };
        var b = a with { ActionId = "b", SubjectReferentId = "" };
        var c = a with { ActionId = "c", Arguments = new[] { new NativeLogicalArgument("first", "a"), new NativeLogicalArgument("second", "b") } };
        var catalog = Catalog(new[] { a, b, c });
        Assert.Equal(3, catalog.List("generation", Json("{}")).FilteredCount);
        Assert.Equal(2, catalog.List("generation", Json("{\"subject_referent_id\":null}")).FilteredCount);
        Assert.Equal(1, catalog.List("generation", Json("{\"subject_referent_id\":\"\"}")).FilteredCount);
        Assert.Equal(0, catalog.List("generation", Json("{\"arguments\":[{\"role\":\"second\",\"referent_id\":\"b\"}]}" )).FilteredCount);
        Assert.NotEqual(NativeLogicalCatalog.Digest(new[] { a }), NativeLogicalCatalog.Digest(new[] { a with { SubjectReferentId = "" } }));
        Assert.Equal("no_match", catalog.Resolve("generation", Json("{\"verb\":\"pick\",\"subject_referent_id\":null,\"arguments\":[{\"role\":\"first\",\"referent_id\":\"a\"}]}" )).Status);
    }
    [Fact]
    public void EquivalentMembersRemainAmbiguousAndMalformedUnicodeFails()
    {
        var a = Action(1); var catalog = Catalog(new[] { a, a with { ActionId = "second" } });
        var expression = JsonSerializer.SerializeToElement(new { verb = a.Verb, subject_referent_id = a.SubjectReferentId, arguments = a.Arguments }, NativeLogicalWire.Options);
        Assert.Equal("ambiguous", catalog.Resolve("generation", expression).Status);
        Assert.Throws<NativeLogicalException>(() => NativeLogicalCatalog.Digest(new[] { a with { Label = "\ud800" } }));
        Assert.Equal("generation_mismatch", catalog.Resolve("another", expression).Status);
    }
    [Fact]
    public void CatalogCursorsBindGenerationRelationFilterAndExpiry()
    {
        long now = 0; var a = Catalog(Enumerable.Range(0, 4).Select(i => Action(i)), () => now);
        var b = Catalog(Enumerable.Range(0, 4).Select(i => Action(i)), () => now);
        var page = a.List("generation", limit: 1);
        Assert.Throws<NativeLogicalException>(() => b.List("generation", cursor: page.NextCursor));
        Assert.Throws<NativeLogicalException>(() => a.List("generation", Json("{\"verb\":\"pick\"}"), page.NextCursor));
        Assert.Throws<NativeLogicalException>(() => a.List("other", cursor: page.NextCursor));
        now = 120000; Assert.Throws<NativeLogicalException>(() => a.List("generation", cursor: page.NextCursor));
    }
    [Fact]
    public void PageByteCapacityNeverSkipsMember()
    {
        var catalog = Catalog(new[] { Action(0) with { Label = new string('界', 2000) }, Action(1) });
        var small = catalog.List("generation", maxPageBytes: 100);
        Assert.Equal("page_budget_too_small", small.Status); Assert.Empty(small.Actions); Assert.Null(small.NextCursor); Assert.True(small.MinimumRequiredBytes > 100);
        Assert.Equal("action-0", catalog.List("generation", maxPageBytes: small.MinimumRequiredBytes).Actions[0].ActionId);
        Assert.Throws<NativeLogicalException>(() => Catalog(new[] { Action(0) with { Label = new string('x', 1000) } }, limits: new(MaxPageBytes: 300)));
        Assert.Throws<NativeLogicalException>(() => Catalog(Enumerable.Range(0, 3).Select(i => Action(i)), limits: new(MaxActions: 2)));
    }
    [Fact]
    public void ProjectorFreezesNestedValuesAndReusesIdentityThenDistinctABA()
    {
        var projector = new NativeLogicalProjector(); var frame = Frame();
        var one = projector.Freeze(frame, NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UnixEpoch, 120000, () => 0);
        var repeat = projector.Freeze(frame, NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UtcNow, 120000, () => 0);
        Assert.Equal(one.SnapshotId, repeat.SnapshotId); Assert.Equal(one.Catalog.Descriptor.Digest, repeat.Catalog.Descriptor.Digest);
        Assert.NotEqual(one.Catalog.Descriptor.CatalogRef, repeat.Catalog.Descriptor.CatalogRef);
        byte[] before = one.CopyPayload(); frame.Persistent!.Content["value"] = 99; frame.Interaction.Content.Surface["nested"]![0] = 9; frame.Referents[0].Properties!["name"] = "changed";
        one.Observation.Persistent!.Content["value"] = 500;
        Assert.Equal(before, one.CopyPayload());
        var changed = projector.Freeze(frame, NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UtcNow, 120000, () => 0);
        Assert.NotEqual(one.SnapshotId, changed.SnapshotId);
        var b = projector.Freeze(Frame("B", "2"), NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UtcNow, 120000, () => 0);
        var a = projector.Freeze(Frame("A", "3"), NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UtcNow, 120000, () => 0);
        Assert.NotEqual(one.SnapshotId, a.SnapshotId); Assert.NotEqual(a.SnapshotId, b.SnapshotId);
        var binding = projector.Freeze(Frame("A", "3", "2"), NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UtcNow, 120000, () => 0);
        Assert.NotEqual(a.SnapshotId, binding.SnapshotId);
        var privateLeafChanged = Frame("A", "3", "2") with { Leaves = new[] { Frame().Leaves[0] with { BindingKey = "replacement" } } };
        Assert.NotEqual(binding.SnapshotId, projector.Freeze(privateLeafChanged, NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UtcNow, 120000, () => 0).SnapshotId);
        Assert.DoesNotContain("private-leaf", Encoding.UTF8.GetString(one.CopyPayload()));
    }
    [Fact]
    public void ScopedProjectionHasExplicitOmissionsAndSameSnapshot()
    {
        var projector = new NativeLogicalProjector();
        var full = projector.Freeze(Frame(), NativeLogicalProjector.ScopeFields, "full", DateTimeOffset.UtcNow, 1000, () => 0);
        var scoped = projector.Freeze(Frame(), new[] { "interaction" }, "scoped", DateTimeOffset.UtcNow, 1000, () => 0);
        Assert.Equal(full.SnapshotId, scoped.SnapshotId); Assert.Null(scoped.Observation.Persistent); Assert.Empty(scoped.Observation.Referents);
        Assert.False(scoped.Observation.Completeness.FullReferenceComplete); Assert.Equal("not_captured", scoped.Observation.Catalog.Status); Assert.Null(scoped.Observation.Catalog.TotalCount);
        Assert.Throws<NativeLogicalException>(() => projector.Freeze(Frame(), new[] { "catalog", "interaction" }, "bad", DateTimeOffset.UtcNow, 1000, () => 0));
    }
    [Fact]
    public void CatalogFreezesCallerLists()
    {
        var args = new List<NativeLogicalArgument> { new("target", "x") }; var list = new List<NativeLogicalAction> { Action(0) with { Arguments = args } };
        var catalog = Catalog(list); string digest = catalog.Descriptor.Digest!; args.Clear(); list.Clear();
        Assert.Single(catalog.Actions); Assert.Single(catalog.Actions[0].Arguments); Assert.Equal(digest, NativeLogicalCatalog.Digest(catalog.Actions));
        Assert.Throws<NotSupportedException>(() => ((IList<NativeLogicalArgument>)catalog.Actions[0].Arguments).Clear());
    }
    [Fact]
    public void FrozenBufferActualChargeSurvivesExpiryWhileEncoderOwnsIt()
    {
        long now = 0; var store = new NativeLogicalCaptureStore(() => now, new(MaxRetainedBytes: 6, RetentionMs: 100));
        byte[] bytes = Encoding.UTF8.GetBytes("你好"); using var encoding = store.AcquireEncoding(bytes);
        var capture = store.Seal(encoding, "snapshot", new("runtime", "fp"), "generation", "scope", DateTimeOffset.UnixEpoch);
        bytes[0] = 0; Assert.Equal(6, store.ChargedBytes);
        string handle = store.Retain("a", capture.CaptureId); store.Release("b", handle); store.ReleaseCapture(capture.CaptureId);
        Assert.True(store.IsAvailable(capture.CaptureId)); Assert.Equal("你好", Encoding.UTF8.GetString(Convert.FromBase64String(store.Read(capture.CaptureId, capture.ReadCursor).DataBase64)));
        store.Release("a", handle); Assert.False(store.IsAvailable(capture.CaptureId)); Assert.Equal(6, store.ChargedBytes);
        Assert.Throws<NativeLogicalException>(() => store.AcquireEncoding(new byte[] { 1 }));
        now = 200; store.Sweep(); Assert.Equal(6, store.ChargedBytes); encoding.Dispose(); Assert.Equal(0, store.ChargedBytes);
    }
    [Fact]
    public void ReadChunkAssemblyHashAndCrossCaptureCursor()
    {
        var store = new NativeLogicalCaptureStore(() => 0); byte[] bytes = Encoding.UTF8.GetBytes("冻结\n😀" + new string('x', 10000));
        using var encoding = store.AcquireEncoding(bytes); var a = store.Seal(encoding, "a", new("runtime", "fp"), "generation", "scope", DateTimeOffset.UnixEpoch);
        var b = store.Seal(encoding, "a", new("runtime", "fp"), "generation", "scope", DateTimeOffset.UnixEpoch);
        var assembled = new List<byte>(); string? cursor = a.ReadCursor;
        do { var chunk = store.Read(a.CaptureId, cursor!, 73); assembled.AddRange(Convert.FromBase64String(chunk.DataBase64)); cursor = chunk.NextCursor; } while (cursor != null);
        Assert.Equal(bytes, assembled.ToArray()); Assert.Equal(a.Sha256, NativeLogicalWire.Hash(assembled.ToArray())); Assert.Equal(bytes.Length, store.ChargedBytes);
        Assert.Throws<NativeLogicalException>(() => store.Read(b.CaptureId, a.ReadCursor));
        Assert.Throws<NativeLogicalException>(() => store.Read(a.CaptureId, a.ReadCursor, 1024 * 1024 + 1));
    }
    [Fact]
    public void AllSharedWireSamplesHaveStrictRequiredFieldsAndCorrectBytes()
    {
        using var doc = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "native-logical-v1.json")));
        var samples = doc.RootElement.GetProperty("wire_samples");
        T Decode<T>(string name) => NativeLogicalDecoder.Decode<T>(Encoding.UTF8.GetBytes(samples.GetProperty(name).GetRawText()));
        Decode<NativeLogicalCapabilities>("capabilities"); Decode<NativeLogicalObservation>("observation"); Decode<NativeLogicalCapture>("capture");
        Decode<NativeLogicalCatalogPage>("catalog_page"); Decode<NativeLogicalResolve>("resolve"); Decode<NativeLogicalAttachRequest>("attach_request"); Decode<NativeLogicalAttachReply>("attach");
        Decode<NativeLogicalEvent>("event"); Decode<NativeLogicalEventBatch>("event_batch"); Decode<NativeLogicalAwaitReply>("await"); Decode<NativeLogicalResult>("result"); Decode<NativeLogicalObservationContext>("observation_context");
        Decode<NativeLogicalCurrentRequest>("current_request"); Decode<NativeLogicalCurrentReply>("current"); Decode<NativeLogicalCurrentReply>("current_retained"); Decode<NativeLogicalCurrentReply>("current_failed");
        Decode<NativeLogicalRetainRequest>("retain_request"); Decode<NativeLogicalRetainReply>("retain"); Decode<NativeLogicalRetainReply>("retain_expired");
        Decode<NativeLogicalReleaseRequest>("release_request"); Decode<NativeLogicalReleaseReply>("release");
        Decode<NativeLogicalRenewRequest>("renew_request"); Decode<NativeLogicalRenewReply>("renew"); Decode<NativeLogicalRenewReply>("renew_expired");
        Decode<NativeLogicalCancelWaitRequest>("cancel_wait_request"); Decode<NativeLogicalCancelWaitReply>("cancel_wait"); Decode<NativeLogicalCancelWaitReply>("cancel_wait_not_pending");
        Decode<NativeLogicalDetachRequest>("detach_request"); Decode<NativeLogicalDetachReply>("detach"); Decode<NativeLogicalDetachReply>("detach_absent");
        var read = Decode<NativeLogicalReadChunk>("read"); byte[] bytes = Convert.FromBase64String(read.DataBase64);
        Assert.Equal(read.TotalBytes, bytes.Length); Assert.Equal(read.Sha256, NativeLogicalWire.Hash(bytes));
        Assert.Equal("snapshot-fixture", NativeLogicalDecoder.Decode<NativeLogicalObservation>(bytes).SnapshotId);
        var bad = JsonNode.Parse(samples.GetProperty("observation").GetRawText())!.AsObject(); bad.Remove("persistent");
        Assert.Throws<NativeLogicalException>(() => NativeLogicalDecoder.Decode<NativeLogicalObservation>(Encoding.UTF8.GetBytes(bad.ToJsonString())));
        bad["persistent"] = null; bad["extra"] = 1;
        Assert.Throws<NativeLogicalException>(() => NativeLogicalDecoder.Decode<NativeLogicalObservation>(Encoding.UTF8.GetBytes(bad.ToJsonString())));
        var malformed = JsonNode.Parse(samples.GetProperty("event").GetRawText())!; malformed["publication_index"] = "9007199254740993123456789";
        Assert.Throws<NativeLogicalException>(() => NativeLogicalDecoder.Decode<NativeLogicalEvent>(Encoding.UTF8.GetBytes(malformed.ToJsonString())));
        malformed["publication_index"] = "01";
        Assert.Throws<NativeLogicalException>(() => NativeLogicalDecoder.Decode<NativeLogicalEvent>(Encoding.UTF8.GetBytes(malformed.ToJsonString())));
        var omittedNull = JsonNode.Parse(NativeLogicalWire.Encode(Action(0)))!.AsObject(); omittedNull.Remove("subject_referent_id");
        Assert.Throws<NativeLogicalException>(() => NativeLogicalDecoder.Decode<NativeLogicalAction>(Encoding.UTF8.GetBytes(omittedNull.ToJsonString())));
    }
    [Theory]
    [InlineData(200)] [InlineData(500)] [InlineData(10000)]
    public void ActualCaptureCatalogExportTransferAndRetentionCharge(int count)
    {
        var watch = System.Diagnostics.Stopwatch.StartNew();
        var refs = Enumerable.Range(0, count).Select(i => new PlayerEnvironmentReferent("ref-" + i, "card", "card", "卡 " + i, new(true, true, false, false, "displayed"), "card", new JsonObject { ["text"] = "公开 😀" })).ToArray();
        var leaves = refs.Select((r, i) => new NativeLogicalLeaf(i % 2 == 0 ? "pick" : "inspect", r.Label!, r.ReferentId, Array.Empty<NativeLogicalArgument>(), "native") { BindingKey = "private-" + i }).ToArray();
        var frame = Frame() with { Referents = refs, Leaves = leaves }; var store = new NativeLogicalCaptureStore(() => 0); var projector = new NativeLogicalProjector();
        var actual = projector.Capture(frame, NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UnixEpoch, 120000, () => 0, store);
        var export = store.ExportFrozen(actual.Capture.CaptureId); byte[] captureBytes = export.CopyCaptureBytes(); byte[] catalogBytes = export.Catalog!.CopyBytes();
        Assert.Equal(actual.Capture.Sha256, NativeLogicalWire.Hash(captureBytes)); Assert.Equal(export.Catalog.PayloadSha256, NativeLogicalWire.Hash(catalogBytes));
        var actions = NativeLogicalDecoder.Decode<NativeLogicalAction[]>(catalogBytes); Assert.Equal(count, actions.Length); Assert.Equal(actual.Catalog!.Descriptor.Digest, NativeLogicalCatalog.Digest(actions));
        Assert.Equal(captureBytes.Length + catalogBytes.Length + count * 8L, store.ChargedBytes); Assert.Equal(3, store.ChargedBuffers);
        string? cursor = actual.Capture.ReadCursor; var chunks = new List<byte>();
        do { var chunk = store.Read(actual.Capture.CaptureId, cursor!, 65536); chunks.AddRange(Convert.FromBase64String(chunk.DataBase64)); cursor = chunk.NextCursor; } while (cursor != null);
        Assert.Equal(captureBytes, chunks.ToArray());
        var prefix = actual.Catalog.List("generation", Json("{\"verb\":\"pick\"}"), limit: 100);
        Assert.Equal(count / 2, prefix.FilteredCount);
        var expression = JsonSerializer.SerializeToElement(new { verb = actions[^1].Verb, subject_referent_id = actions[^1].SubjectReferentId, arguments = actions[^1].Arguments }, NativeLogicalWire.Options);
        Assert.Equal(actions[^1].ActionId, actual.Catalog.Resolve("generation", expression).Action?.ActionId);
        captureBytes[0] = 0; catalogBytes[0] = 0;
        Assert.Equal(actual.Capture.Sha256, NativeLogicalWire.Hash(export.CopyCaptureBytes())); Assert.Equal(export.Catalog.PayloadSha256, NativeLogicalWire.Hash(export.Catalog.CopyBytes()));
        output.WriteLine($"native-logical actual members={count} observation_bytes={actual.Capture.ByteCount} catalog_bytes={export.Catalog.ByteCount} charged_peak={store.ChargedBytes} elapsed_ms={watch.ElapsedMilliseconds}");
        store.ReleaseCapture(actual.Capture.CaptureId); Assert.Equal(0, store.ChargedBytes); Assert.Throws<NativeLogicalException>(() => actual.Catalog.List("generation"));
    }
    [Fact]
    public void ReaderPinExtendsRetentionAndIssuesNewCursorWithoutRewritingCapture()
    {
        long now = 0; var store = new NativeLogicalCaptureStore(() => now, new(RetentionMs: 100));
        using var encoding = store.AcquireEncoding(new byte[] { 1, 2, 3 }); var capture = store.Seal(encoding, "snapshot", new("runtime", "fp"), "generation", "scope", DateTimeOffset.UnixEpoch);
        now = 50; var retention = store.RetainReference("reader", capture.CaptureId); now = 100; store.Sweep();
        Assert.Equal(capture, retention.Capture); Assert.True(store.IsAvailable(capture.CaptureId));
        Assert.Throws<NativeLogicalException>(() => store.Read(capture.CaptureId, capture.ReadCursor));
        Assert.Equal(3, Convert.FromBase64String(store.Read(capture.CaptureId, retention.ReadCursor).DataBase64).Length);
        now = 150; store.Sweep(); Assert.False(store.IsAvailable(capture.CaptureId)); Assert.Equal(3, store.ChargedBytes); encoding.Dispose(); Assert.Equal(0, store.ChargedBytes);
    }
    [Fact]
    public void CaptureCountAndCompleteRelationBytesAreAdmittedTogether()
    {
        var store = new NativeLogicalCaptureStore(() => 0, new(MaxCaptures: 2)); using var encoding = store.AcquireEncoding(new byte[] { 1 });
        store.Seal(encoding, "snapshot", new("runtime", "fp"), "generation", "scope", DateTimeOffset.UnixEpoch);
        store.Seal(encoding, "snapshot", new("runtime", "fp"), "generation", "scope", DateTimeOffset.UnixEpoch);
        Assert.Throws<NativeLogicalException>(() => store.Seal(encoding, "snapshot", new("runtime", "fp"), "generation", "scope", DateTimeOffset.UnixEpoch));
        var projection = new NativeLogicalProjector().Freeze(Frame(), NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UnixEpoch, 120000, () => 0);
        var tinyStore = new NativeLogicalCaptureStore(() => 0, new(MaxRetainedBytes: projection.CopyPayload().Length + projection.Catalog.StorageByteCount - 1));
        Assert.Throws<NativeLogicalException>(() => tinyStore.SealProjection(projection, Frame().Session, "generation", "scope", DateTimeOffset.UnixEpoch)); Assert.Equal(0, tinyStore.ChargedBytes);
        Assert.Throws<NativeLogicalException>(() => new NativeLogicalProjector(new(MaxCaptureBytes: 500)).Freeze(Frame(), NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UnixEpoch, 120000, () => 0));
    }

    [Fact]
    public async Task CatalogReadExpiryCannotReplenishBytesBeforeActualReaderRelease()
    {
        long now = 0; var store = new NativeLogicalCaptureStore(() => Interlocked.Read(ref now), new(RetentionMs: 100));
        var catalog = Catalog(Enumerable.Range(0, 10000).Select(i => Action(i)), () => Interlocked.Read(ref now));
        using var encoding = store.AcquireEncoding(new byte[] { 1 });
        var capture = store.Seal(encoding, "snapshot", new("runtime", "fp"), "generation", "scope", DateTimeOffset.UnixEpoch, catalog);
        long fullCharge = store.ChargedBytes;
        using var entered = new ManualResetEventSlim(); using var release = new ManualResetEventSlim();
        // A legitimate reader takes a borrow before checking availability; pause at that exact boundary.
        catalog.BindRetention(() => { entered.Set(); release.Wait(TimeSpan.FromSeconds(5)); return true; },
            () => store.BorrowCatalog(catalog));
        var reader = Task.Run(() => catalog.List("generation", limit: 1));
        Assert.True(entered.Wait(TimeSpan.FromSeconds(2))); Assert.Equal(1, store.InFlightCatalogReads);
        Interlocked.Exchange(ref now, 100); store.Sweep();
        Assert.False(store.IsAvailable(capture.CaptureId)); Assert.Equal(fullCharge, store.ChargedBytes);
        release.Set(); Assert.Single((await reader).Actions); Assert.Equal(1, store.ChargedBytes); encoding.Dispose(); Assert.Equal(0, store.ChargedBytes);
    }

    [Fact]
    public void PublicNestedMalformedUnicodeFailsBeforeSerializerReplacement()
    {
        var frame = Frame(); frame.Persistent!.Content["bad"] = "\ud800";
        Assert.Throws<NativeLogicalException>(() => new NativeLogicalProjector().Freeze(frame, NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UnixEpoch, 120000, () => 0));
    }

    [Fact]
    public void SharedNegativeWireCasesRejectNullRequiredMembers()
    {
        using var doc = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "native-logical-v1.json")));
        foreach (var fixture in doc.RootElement.GetProperty("invalid_wire_cases").EnumerateArray())
        {
            byte[] wire = Encoding.UTF8.GetBytes(fixture.GetProperty("wire").GetRawText());
            void Decode()
            {
                switch (fixture.GetProperty("type").GetString())
                {
                    case "catalog_page": NativeLogicalDecoder.Decode<NativeLogicalCatalogPage>(wire); break;
                    case "observation": NativeLogicalDecoder.Decode<NativeLogicalObservation>(wire); break;
                    case "capabilities": NativeLogicalDecoder.Decode<NativeLogicalCapabilities>(wire); break;
                    case "event_batch": NativeLogicalDecoder.Decode<NativeLogicalEventBatch>(wire); break;
                    case "attach_request": NativeLogicalDecoder.Decode<NativeLogicalAttachRequest>(wire); break;
                    case "current": case "current_retained": NativeLogicalDecoder.Decode<NativeLogicalCurrentReply>(wire); break;
                    case "retain": NativeLogicalDecoder.Decode<NativeLogicalRetainReply>(wire); break;
                    case "renew_expired": NativeLogicalDecoder.Decode<NativeLogicalRenewReply>(wire); break;
                    case "cancel_wait": NativeLogicalDecoder.Decode<NativeLogicalCancelWaitReply>(wire); break;
                    case "result": NativeLogicalDecoder.Decode<NativeLogicalResult>(wire); break;
                    default: throw new InvalidOperationException("Unmapped negative fixture type.");
                }
            }
            Assert.Equal("invalid_wire", Assert.Throws<NativeLogicalException>(Decode).Code);
        }
        Assert.Equal("invalid_wire", Assert.Throws<NativeLogicalException>(() => NativeLogicalDecoder.Decode<NativeLogicalAction[]>(Encoding.UTF8.GetBytes("[null]"))).Code);
    }
    [Fact]
    public void RecursiveArrayAndDictionaryNullabilityPreservesDeclaredNullableValues()
    {
        var valid = new NestedCollectionWire(new[] { new[] { "required" } }, new string?[] { null, "value" },
            new Dictionary<string, IReadOnlyList<string>> { ["key"] = new[] { "required" } },
            new Dictionary<string, string?> { ["key"] = null });
        byte[] original = NativeLogicalWire.Encode(valid);
        var decoded = NativeLogicalDecoder.Decode<NestedCollectionWire>(original);
        Assert.Null(decoded.NullableStrings[0]); Assert.Null(decoded.NullableDictionary["key"]);
        foreach (Action<JsonNode> mutation in new Action<JsonNode>[]
        {
            node => node["required_arrays"]![0] = null,
            node => node["required_arrays"]![0]![0] = null,
            node => node["required_dictionary"]!["key"] = null,
            node => node["required_dictionary"]!["key"]![0] = null
        })
        {
            JsonNode invalid = JsonNode.Parse(original)!; mutation(invalid);
            Assert.Equal("invalid_wire", Assert.Throws<NativeLogicalException>(() => NativeLogicalDecoder.Decode<NestedCollectionWire>(Encoding.UTF8.GetBytes(invalid.ToJsonString()))).Code);
        }
        var observation = new NativeLogicalProjector().Freeze(Frame(), NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UnixEpoch, 120000, () => 0).Observation;
        byte[] explicitlyNullable = NativeLogicalWire.Encode(observation with { Persistent = null, Referents = new[] { observation.Referents[0] with { Label = null, Properties = null, State = observation.Referents[0].State with { Enabled = null, Focused = null } } } });
        var nullableObservation = NativeLogicalDecoder.Decode<NativeLogicalObservation>(explicitlyNullable);
        Assert.Null(nullableObservation.Persistent); Assert.Null(nullableObservation.Referents[0].Label);
        Assert.Null(nullableObservation.Referents[0].State.Enabled); Assert.Null(nullableObservation.Referents[0].Properties);
    }
    private sealed record NestedCollectionWire(string[][] RequiredArrays, string?[] NullableStrings,
        IReadOnlyDictionary<string, IReadOnlyList<string>> RequiredDictionary,
        IReadOnlyDictionary<string, string?> NullableDictionary);

    [Fact]
    public void ProducerCompletenessCertificatePrecedesAllRequestedScopeAndRetention()
    {
        using var doc = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "native-logical-v1.json")));
        var projector = new NativeLogicalProjector(); var store = new NativeLogicalCaptureStore(() => 0);
        var original = projector.Freeze(Frame(), NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UnixEpoch, 120000, () => 0);
        foreach (var fixture in doc.RootElement.GetProperty("source_completeness_cases").EnumerateArray())
        {
            var certificate = NativeLogicalDecoder.Decode<NativeLogicalSourceCompleteness>(Encoding.UTF8.GetBytes(fixture.GetProperty("certificate").GetRawText()));
            var frame = Frame() with { SourceCompleteness = certificate, Leaves = Array.Empty<NativeLogicalLeaf>(), Status = fixture.GetProperty("observation_status").GetString()! };
            if (!fixture.GetProperty("accepted").GetBoolean())
            {
                // Malformed public content would fail another gate if the source certificate were skipped.
                frame.Persistent!.Content["bad"] = "\ud800";
                foreach (var scope in new[] { NativeLogicalProjector.ScopeFields, (IReadOnlyList<string>)new[] { "interaction" } })
                {
                    Assert.Equal("source_capture_incomplete", Assert.Throws<NativeLogicalException>(() => projector.Freeze(frame, scope, "scope", DateTimeOffset.UnixEpoch, 120000, () => 0)).Code);
                    Assert.Equal("source_capture_incomplete", Assert.Throws<NativeLogicalException>(() => projector.Capture(frame, scope, "scope", DateTimeOffset.UnixEpoch, 120000, () => 0, store)).Code);
                }
                Assert.Equal(0, store.ChargedBytes); Assert.Equal(0, store.ChargedBuffers);
                Assert.Equal(original.SnapshotId, projector.Freeze(Frame(), NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UnixEpoch, 120000, () => 0).SnapshotId);
            }
            else
            {
                var captured = projector.Capture(frame, NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UnixEpoch, 120000, () => 0, store);
                var exported = store.ExportFrozen(captured.Capture.CaptureId);
                var observation = NativeLogicalDecoder.Decode<NativeLogicalObservation>(exported.CopyCaptureBytes());
                Assert.True(observation.Completeness.FullReferenceComplete); Assert.Equal("complete", observation.Catalog.Status);
                Assert.Equal(0, observation.Catalog.TotalCount); Assert.Empty(captured.Catalog!.Actions);
                Assert.Equal(frame.Status, observation.Status); store.ReleaseCapture(captured.Capture.CaptureId);
                // Re-establish the original current frame before the next source-certificate negative.
                original = projector.Freeze(Frame(), NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UnixEpoch, 120000, () => 0);
            }
        }
    }

    [Fact]
    public void PrivateBindingKeysRespectAggregateCaptureBudgetBeforeIdentityChanges()
    {
        var limits = new NativeLogicalLimits(MaxCaptureBytes: 4096, MaxFieldBytes: 1024);
        var projector = new NativeLogicalProjector(limits);
        var original = projector.Freeze(Frame(), NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UnixEpoch, 120000, () => 0);
        var frame = Frame() with
        {
            Leaves = Enumerable.Range(0, 20).Select(i => new NativeLogicalLeaf("pick", "甲", "public-card", Array.Empty<NativeLogicalArgument>(), "native")
                { BindingKey = new string('x', 1000) + i }).ToArray()
        };
        Assert.True(NativeLogicalWire.EncodeBounded(frame, limits.MaxCaptureBytes).Length < limits.MaxCaptureBytes);
        Assert.All(frame.Leaves, leaf => Assert.True(Encoding.UTF8.GetByteCount(leaf.BindingKey) <= limits.MaxFieldBytes));
        Assert.Equal("capacity_exceeded", Assert.Throws<NativeLogicalException>(() => projector.Freeze(frame, NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UnixEpoch, 120000, () => 0)).Code);
        Assert.Equal(original.SnapshotId, projector.Freeze(Frame(), NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UnixEpoch, 120000, () => 0).SnapshotId);
    }
    [Fact]
    public void ResultStagesPreserveKnownFactsWithFiniteBoundedWireFields()
    {
        using var doc = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "native-logical-v1.json")));
        byte[] sample = Encoding.UTF8.GetBytes(doc.RootElement.GetProperty("wire_samples").GetProperty("result").GetRawText());
        var result = NativeLogicalDecoder.Decode<NativeLogicalResult>(sample);
        Assert.Equal("partially_delivered", result.Delivery); Assert.Equal("unknown", result.Execution);
        Assert.Equal(new[] { "focus", "confirm" }, result.Stages.Select(stage => stage.Stage));
        Assert.Equal("delivered", result.Stages[0].Delivery); Assert.Equal("rejected_before_input", result.Stages[1].Delivery);
        var empty = NativeLogicalDecoder.Decode<NativeLogicalResult>(NativeLogicalWire.Encode(result with { Stages = Array.Empty<NativeLogicalInputStage>() }));
        Assert.Empty(empty.Stages); Assert.Equal("unknown", empty.Execution);
        foreach (Action<JsonNode> mutate in new Action<JsonNode>[]
        {
            node => node.AsObject().Remove("stages"),
            node => node["stages"] = null,
            node => node["stages"] = new JsonArray((JsonNode?)null),
            node => node["stages"] = JsonSerializer.SerializeToNode(Enumerable.Repeat(result.Stages[0], 17), NativeLogicalWire.Options),
            node => node["stages"]![0]!["stage"] = new string('界', 43),
            node => node["stages"]![0]!["evidence"] = new string('x', 129),
            node => node["stages"]![0]!["delivery"] = "accepted",
            node => node["stages"]![0]!["evidence"] = null
        })
        {
            JsonNode malformed = JsonNode.Parse(sample)!; mutate(malformed);
            Assert.Equal("invalid_wire", Assert.Throws<NativeLogicalException>(() => NativeLogicalDecoder.Decode<NativeLogicalResult>(Encoding.UTF8.GetBytes(malformed.ToJsonString()))).Code);
        }
        var atLimit = result with { Stages = Enumerable.Repeat(result.Stages[0] with { Stage = new string('x', 128), Evidence = new string('x', 128) }, 16).ToArray() };
        Assert.Equal(16, NativeLogicalDecoder.Decode<NativeLogicalResult>(NativeLogicalWire.Encode(atLimit)).Stages.Count);
    }

    [Fact]
    public void CurrentRetainAndReleaseUseOneStoreAndCoherentReferences()
    {
        long now = 0; var store = new NativeLogicalCaptureStore(() => now, new(RetentionMs: 100)); var projector = new NativeLogicalProjector();
        var request = new NativeLogicalCurrentRequest("reader", NativeLogicalProjector.ScopeFields, null);
        var current = projector.Current(Frame(), request, DateTimeOffset.UnixEpoch, 100, () => now, store, "actual-game-id");
        Assert.Equal("captured", current.Status); Assert.Null(current.Reason); Assert.Null(current.Retention);
        Assert.Equal(current.Capture!.SnapshotId, current.Context!.ObservationRef); Assert.Equal(current.Capture.CaptureId, current.Context.CaptureRef);
        Assert.Equal("actual-game-id", current.Context.GameContinuityId); NativeLogicalDecoder.Decode<NativeLogicalCurrentReply>(NativeLogicalWire.Encode(current));
        var repeat = projector.Current(Frame(), request, DateTimeOffset.UnixEpoch, 100, () => now, store);
        Assert.Equal(current.Capture.SnapshotId, repeat.Capture!.SnapshotId); Assert.NotEqual(current.Capture.ScopeId, repeat.Capture.ScopeId); Assert.Null(repeat.Context!.GameContinuityId);
        var stale = projector.Current(Frame(), request with { ExpectedSnapshotId = "old-snapshot" }, DateTimeOffset.UnixEpoch, 100, () => now, store);
        Assert.Equal("stale", stale.Status); Assert.Null(stale.Context); Assert.Null(stale.Capture);
        var partial = projector.Current(Frame(), request with { EagerScope = new[] { "interaction" } }, DateTimeOffset.UnixEpoch, 100, () => now, store);
        Assert.Equal("partial", partial.Status); Assert.Equal("scope_omission", partial.Reason);
        var incomplete = projector.Current(Frame() with { SourceCompleteness = new("partial", new[] { "native_list_items" }) }, request, DateTimeOffset.UnixEpoch, 100, () => now, store);
        Assert.Equal("source_capture_incomplete", incomplete.Status); Assert.Null(incomplete.Capture);
        now = 50;
        var retain = store.RetainPublic(new("reader", current.Capture.CaptureId)); Assert.Equal("retained", retain.Status);
        Assert.Equal(current.Capture, retain.Retention!.Capture); NativeLogicalDecoder.Decode<NativeLogicalRetainReply>(NativeLogicalWire.Encode(retain));
        now = 100; store.Sweep(); Assert.True(store.IsAvailable(current.Capture.CaptureId));
        Assert.Throws<NativeLogicalException>(() => store.Read(current.Capture.CaptureId, current.Capture.ReadCursor));
        Assert.True(store.Read(current.Capture.CaptureId, retain.Retention.ReadCursor).Complete);
        Assert.Equal("cursor_mismatch", Assert.Throws<NativeLogicalException>(() => store.ReleasePublic(new("other", retain.Retention.RetentionHandleId))).Code);
        Assert.True(store.IsAvailable(current.Capture.CaptureId));
        var release = store.ReleasePublic(new("reader", retain.Retention.RetentionHandleId)); Assert.True(release.Released);
        Assert.Equal(release, store.ReleasePublic(new("reader", retain.Retention.RetentionHandleId))); Assert.False(store.IsAvailable(current.Capture.CaptureId));
        Assert.Equal("payload_expired", store.RetainPublic(new("reader", current.Capture.CaptureId)).Status);
    }
    [Fact]
    public void IndexedMaximumPagesPreserveExactMemberEncodingAndFilteredIntegrity()
    {
        var original = Enumerable.Range(0, 10000).Select(i => Action(i) with { Label = "空\n界 🐉 café e\u0301 / " + i }).ToArray();
        var catalog = Catalog(original); string? cursor = null; int position = 0;
        do
        {
            var page = catalog.List("generation", cursor: cursor, limit: 10000, maxPageBytes: 1024 * 1024);
            byte[] encoded = NativeLogicalWire.Encode(page); Assert.True(encoded.Length <= 1024 * 1024); Assert.NotEmpty(page.Actions);
            Assert.Equal(original.Skip(position).Take(page.Actions.Count).Select(NativeLogicalWire.Encode), page.Actions.Select(NativeLogicalWire.Encode));
            using var wire = JsonDocument.Parse(encoded);
            var rowBytes = wire.RootElement.GetProperty("actions").EnumerateArray().Select(row => Encoding.UTF8.GetBytes(row.GetRawText())).ToArray();
            Assert.Equal(page.Actions.Select(NativeLogicalWire.Encode), rowBytes);
            var empty = page with { Actions = Array.Empty<NativeLogicalAction>() };
            Assert.Equal(encoded.Length, NativeLogicalWire.Encode(empty).Length + rowBytes.Sum(row => row.Length) + page.Actions.Count - 1);
            Assert.Equal(catalog.Descriptor.Digest, page.FilteredDigest); Assert.Equal(10000, page.FilteredCount);
            position += page.Actions.Count; cursor = page.NextCursor;
        } while (cursor is not null);
        Assert.Equal(10000, position);
        var filtered = catalog.List("generation", Json("{\"verb\":\"pick\"}"), limit: 10000);
        Assert.Equal(5000, filtered.FilteredCount); Assert.Equal(NativeLogicalCatalog.Digest(original.Where(row => row.Verb == "pick")), filtered.FilteredDigest);
        Assert.Equal(original.Where(row => row.Verb == "pick").Select(row => row.ActionId).Take(filtered.Actions.Count), filtered.Actions.Select(row => row.ActionId));
    }

    [Fact]
    public void FreshRetainAfterOriginalCursorExpiryUsesOtherPinWithoutRewritingCapture()
    {
        long now = 0; var store = new NativeLogicalCaptureStore(() => now, new(RetentionMs: 100));
        var current = new NativeLogicalProjector().Current(Frame(), new("creator", NativeLogicalProjector.ScopeFields, null), DateTimeOffset.UnixEpoch, 100, () => now, store);
        var original = current.Capture!; now = 50;
        var other = store.RetainPublic(new("other", original.CaptureId)).Retention!;
        now = 100; store.Sweep(); Assert.True(store.IsAvailable(original.CaptureId));
        Assert.Throws<NativeLogicalException>(() => store.Read(original.CaptureId, original.ReadCursor));
        var fresh = store.RetainPublic(new("reader", original.CaptureId)); Assert.Equal("retained", fresh.Status);
        Assert.Equal(original, fresh.Retention!.Capture); Assert.True(store.Read(original.CaptureId, fresh.Retention.ReadCursor).Complete);
        now = 150;
        // An expired foreign handle is absent; releasing its token cannot touch the live reader's pin.
        Assert.True(store.ReleasePublic(new("reader", other.RetentionHandleId)).Released); Assert.True(store.IsAvailable(original.CaptureId));
        store.ReleasePublic(new("reader", fresh.Retention.RetentionHandleId)); Assert.False(store.IsAvailable(original.CaptureId)); Assert.Equal(0, store.ChargedBytes);
    }

    [Fact]
    public void CatalogReferenceLookupSharesExistingCaptureAndReaderLifetime()
    {
        long now = 0; var store = new NativeLogicalCaptureStore(() => now, new(RetentionMs: 100));
        var captured = new NativeLogicalProjector().Capture(Frame(), NativeLogicalProjector.ScopeFields, "scope", DateTimeOffset.UnixEpoch, 100, () => now, store);
        string reference = captured.Catalog!.Descriptor.CatalogRef;
        Assert.Same(captured.Catalog, store.CatalogByReference(reference));
        Assert.Equal("generation_mismatch", store.CatalogByReference(reference).Resolve("foreign-generation", Json("{\"verb\":\"pick\",\"subject_referent_id\":\"public-card\",\"arguments\":[]}")).Status);
        now = 50; var retention = store.RetainPublic(new("reader", captured.Capture.CaptureId)).Retention!;
        now = 100; store.Sweep(); Assert.Same(captured.Catalog, store.CatalogByReference(reference));
        store.ReleasePublic(new("reader", retention.RetentionHandleId));
        Assert.Equal("expired", Assert.Throws<NativeLogicalException>(() => store.CatalogByReference(reference)).Code); Assert.Equal(0, store.ChargedBytes);
    }

}
