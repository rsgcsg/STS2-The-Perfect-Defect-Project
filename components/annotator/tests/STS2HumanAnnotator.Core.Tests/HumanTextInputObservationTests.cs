using System.Text.Json;
using System.Text.Json.Nodes;
using STS2HumanAnnotator.Core;
using Xunit;

namespace STS2HumanAnnotator.Core.Tests;

public sealed class HumanTextInputObservationTests
{
    [Theory]
    [InlineData(true, true, true, true, true, HumanTextInputObservationContract.AcceptedInput)]
    [InlineData(true, false, true, true, true, HumanTextInputObservationContract.NotMapped)]
    [InlineData(false, false, true, true, true, HumanTextInputObservationContract.CaptureFailed)]
    [InlineData(true, true, false, true, true, HumanTextInputObservationContract.RejectedOrCancelled)]
    [InlineData(true, true, true, false, true, HumanTextInputObservationContract.RejectedOrCancelled)]
    [InlineData(true, true, true, true, false, HumanTextInputObservationContract.NotMapped)]
    public void EndTurnNeedsExactPreInputMenuAndOneReturnedNativeRequest(
        bool captured, bool exactMenu, bool oneRequest, bool returned,
        bool runAssigned, string expected)
    {
        Assert.Equal(expected, HumanTextInputNativeProof.ClassifyEndTurnInput(
            captured, exactMenu, oneRequest, returned, runAssigned));
    }

    [Theory]
    [InlineData(true, true, true, true, true)]
    [InlineData(false, true, true, true, false)] // controller/non-mouse input
    [InlineData(true, false, true, true, false)] // not a left-button release
    [InlineData(true, true, false, true, false)] // observation entry unavailable
    [InlineData(true, true, true, false, false)] // some node is hovered, even if not a creature
    [InlineData(false, false, true, true, false)]
    public void NoHoverCancellationIsOnlyACandidateForExactMouseLeftRelease(
        bool exactMouseCarrier, bool leftButtonReleased,
        bool hoverObservationAvailable, bool hoveredNodeMissing, bool expected)
    {
        Assert.Equal(expected, HumanTextInputNativeProof.IsMouseNoHoverCancelCandidate(
            exactMouseCarrier, leftButtonReleased, hoverObservationAvailable,
            hoveredNodeMissing));
    }

    [Fact]
    public void MouseTargetProofRejectsAutoCancelEmptyHoverAndReplacedTarget()
    {
        object target = new();
        Assert.True(HumanTextInputNativeProof.MatchesTargetFinish(
            false, false, target, target));
        Assert.False(HumanTextInputNativeProof.MatchesTargetFinish(
            false, true, target, target));
        Assert.False(HumanTextInputNativeProof.MatchesTargetFinish(
            false, false, null, null));
        Assert.False(HumanTextInputNativeProof.MatchesTargetFinish(
            false, false, target, new object()));
        Assert.True(HumanTextInputNativeProof.MatchesTargetFinish(
            true, true, null, null));
        Assert.False(HumanTextInputNativeProof.MatchesTargetFinish(
            true, false, null, null));
    }

    [Fact]
    public void TwoNativeCallbacksCannotClaimTheSamePhysicalInput()
    {
        var claims = new HumanTextInputClaimGate();
        object input = new();
        Assert.True(claims.TryClaim(input, "session-a"));
        Assert.False(claims.TryClaim(input, "session-a"));
        Assert.True(claims.TryClaim(new object(), "session-a"));
        Assert.True(claims.TryClaim(input, "session-b"));
    }

    [Fact]
    public void AcceptedObservationPersistsAndAuditsWithoutChangingCanonicalCount()
    {
        string root = Temp();
        try
        {
            var profile = Profile();
            var manifest = Manifest(profile) with { TextInputSchemaVersion = 1 };
            string session;
            using (var store = RecordingSessionStore.Create(root, manifest, profile))
            {
                session = store.DirectoryPath;
                Journal(store, manifest);
                store.AppendHumanTextInputObservation(Accepted());
                Assert.Equal(0, store.GetSnapshot().Counters.Records);
            }
            Assert.Single(File.ReadAllLines(Path.Combine(session,
                HumanTextInputObservationContract.FileName)));
            Assert.Equal("pass", RecordingSessionAuditor.Audit(session).Status);
            Assert.Equal(0, RecordingSessionAuditor.Audit(session).ValidRecords);
            File.AppendAllText(Path.Combine(session, HumanTextInputObservationContract.FileName), " ");
            Assert.Equal("fail", RecordingSessionAuditor.Audit(session).Status);
        }
        finally { Delete(root); }
    }

    [Fact]
    public void AcceptedGateRejectsIndependentMissingFacts()
    {
        HumanTextInputObservation valid = Accepted();
        var invalid = new HumanTextInputObservation[]
        {
            valid with { Snapshot = null },
            valid with { ChosenAction = null },
            valid with { SnapshotSha256 = new string('0', 64) },
            valid with { Environment = null },
            valid with { MappingStatus = "not_mapped" },
            valid with { MatchCount = 0 },
            valid with { NativeCarrierWitnessId = null },
            valid with { ExternalControllerActive = true },
            valid with { NativeMechanism = "inferred" },
            valid with { ChosenAction = JsonNode.Parse("{\"action_id\":\"other\",\"kind\":\"native_input\",\"verb\":\"begin_card_play\",\"effect_domain\":\"native_input\"}")!.AsObject() },
            MutateSnapshot(valid, snapshot => snapshot["referents"] = new JsonArray()),
            MutateSnapshot(valid, snapshot => snapshot["interaction"]!["content"] = null),
            MutateSnapshot(valid, snapshot => snapshot["menu_actions"]!["actions"]!.AsArray().Add(
                JsonNode.Parse("{\"action_id\":\"card-1\",\"kind\":\"system_navigation\",\"verb\":\"open_information\",\"label\":\"Info\",\"subject_referent_id\":null,\"arguments\":[],\"effect_domain\":\"text_menu\"}"))),
        };
        foreach (HumanTextInputObservation row in invalid)
            Assert.NotEmpty(HumanTextInputObservationValidator.Validate(row));
    }

    [Theory]
    [InlineData(HumanTextInputObservationContract.ControllerConfirmedInputSignal, "confirm_card")]
    [InlineData(HumanTextInputObservationContract.ControllerCanceledInputSignal, "cancel_card_play")]
    [InlineData(HumanTextInputObservationContract.ControllerTargetFinishInput, "confirm_target")]
    [InlineData(HumanTextInputObservationContract.ControllerTargetCanceledInput, "cancel_card_play")]
    [InlineData(HumanTextInputObservationContract.MouseCanceledInputSignal, "cancel_card_play")]
    [InlineData(HumanTextInputObservationContract.MouseTargetFinishInput, "confirm_target")]
    [InlineData(HumanTextInputObservationContract.MouseTargetCanceledInput, "cancel_card_play")]
    public void ContinuationMechanismMustMatchFrozenNativeVerb(
        string mechanism, string verb)
    {
        HumanTextInputObservation row = Accepted();
        JsonObject snapshot = (JsonObject)row.Snapshot!.DeepClone();
        JsonObject chosen = (JsonObject)row.ChosenAction!.DeepClone();
        chosen["verb"] = verb;
        snapshot["menu_actions"]!["actions"]![0] = chosen.DeepClone();
        row = row with
        {
            Snapshot = snapshot,
            SnapshotSha256 = EvidenceIdentity.Sha256Json(snapshot),
            ChosenAction = chosen,
            NativeOwnerWitnessId = row.NativeCarrierWitnessId,
            NativeMechanism = mechanism
        };
        Assert.Empty(HumanTextInputObservationValidator.Validate(row));
        Assert.Contains("text_input_continuation_owner_mismatch",
            HumanTextInputObservationValidator.Validate(row with
            { NativeOwnerWitnessId = "different-owner" }));
        Assert.Contains("text_input_chosen_action_not_unique",
            HumanTextInputObservationValidator.Validate(row with
            { NativeMechanism = HumanTextInputObservationContract.NativeMechanism }));
        Assert.Contains("text_input_native_verb_mechanism_mismatch",
            HumanTextInputObservationValidator.Validate(row with
            {
                Disposition = HumanTextInputObservationContract.RejectedOrCancelled,
                ReasonCode = "native_callback_unproved",
                NativeMechanism = HumanTextInputObservationContract.NativeMechanism
            }));
        Assert.Contains("text_input_native_mechanism_invalid",
            HumanTextInputObservationValidator.Validate(row with
            { NativeMechanism = "inferred_from_later_state" }));
    }

    [Fact]
    public void EndTurnInputUsesTheExactSubjectlessMenuActionAndNativeRequest()
    {
        HumanTextInputObservation card = Accepted();
        JsonObject snapshot = (JsonObject)card.Snapshot!.DeepClone();
        JsonObject chosen = (JsonObject)card.ChosenAction!.DeepClone();
        chosen["verb"] = "end_turn";
        chosen["subject_referent_id"] = null;
        snapshot["menu_actions"]!["actions"]![0] = chosen.DeepClone();
        HumanTextInputObservation endTurn = card with
        {
            Snapshot = snapshot,
            SnapshotSha256 = EvidenceIdentity.Sha256Json(snapshot),
            ChosenAction = chosen,
            NativeMechanism = HumanTextInputObservationContract.EndTurnRequestSubmitted,
            NativeOwnerWitnessId = "button-1",
            NativeSubjectWitnessId = "button-1",
            NativeCarrierWitnessId = "request-1"
        };
        Assert.Empty(HumanTextInputObservationValidator.Validate(endTurn));
        JsonObject omittedSubject = (JsonObject)chosen.DeepClone();
        omittedSubject.Remove("subject_referent_id");
        JsonObject omittedSnapshot = (JsonObject)snapshot.DeepClone();
        omittedSnapshot["menu_actions"]!["actions"]![0] = omittedSubject.DeepClone();
        Assert.Contains("text_input_chosen_action_not_unique",
            HumanTextInputObservationValidator.Validate(endTurn with
        {
            Snapshot = omittedSnapshot,
            SnapshotSha256 = EvidenceIdentity.Sha256Json(omittedSnapshot),
            ChosenAction = omittedSubject
        }));
        Assert.Contains("text_input_native_verb_mechanism_mismatch",
            HumanTextInputObservationValidator.Validate(endTurn with
            { ChosenAction = (JsonObject)card.ChosenAction.DeepClone() }));
        Assert.Contains("text_input_exact_mapping_missing",
            HumanTextInputObservationValidator.Validate(endTurn with
            { NativeCarrierWitnessId = null }));
        Assert.Contains("text_input_end_turn_button_witness_mismatch",
            HumanTextInputObservationValidator.Validate(endTurn with
            { NativeSubjectWitnessId = "other-button" }));
        JsonObject wrongSubject = (JsonObject)chosen.DeepClone();
        wrongSubject["subject_referent_id"] = "card-1";
        Assert.Contains("text_input_chosen_action_not_unique",
            HumanTextInputObservationValidator.Validate(endTurn with
            { ChosenAction = wrongSubject }));
        JsonObject nonStringSubject = (JsonObject)chosen.DeepClone();
        nonStringSubject["subject_referent_id"] = 42;
        Assert.Contains("text_input_chosen_action_not_unique",
            HumanTextInputObservationValidator.Validate(endTurn with
            { ChosenAction = nonStringSubject }));
    }

    [Fact]
    public void FailureRowIsNontrainingAndHistoricalManifestHasNoStream()
    {
        HumanTextInputObservation cancelled = Accepted() with
        {
            Disposition = HumanTextInputObservationContract.RejectedOrCancelled,
            ReasonCode = "native_carrier_not_retained"
        };
        Assert.Empty(HumanTextInputObservationValidator.Validate(cancelled));
        HumanTextInputObservation ambiguous = cancelled with
        {
            Disposition = HumanTextInputObservationContract.NotMapped,
            MappingStatus = "ambiguous", MatchCount = 2,
            ReasonCode = "multiple_native_matches"
        };
        Assert.Empty(HumanTextInputObservationValidator.Validate(ambiguous));
        HumanTextInputObservation failed = Accepted() with
        {
            Snapshot = null, SnapshotSha256 = null, ChosenAction = null,
            Environment = null, MappingStatus = "capture_failed", MatchCount = 0,
            MappingBasis = "none", NativeCarrierWitnessId = null,
            NativeSubjectWitnessId = null,
            Disposition = HumanTextInputObservationContract.CaptureFailed,
            ReasonCode = "pre_capture_failed"
        };
        Assert.Empty(HumanTextInputObservationValidator.Validate(failed));
        string root = Temp();
        try
        {
            var profile = Profile();
            var manifest = Manifest(profile);
            using var store = RecordingSessionStore.Create(root, manifest, profile);
            Journal(store, manifest);
            Assert.False(File.Exists(Path.Combine(store.DirectoryPath,
                HumanTextInputObservationContract.FileName)));
            store.Dispose();
            Assert.Equal("pass", RecordingSessionAuditor.Audit(store.DirectoryPath).Status);
            File.WriteAllText(Path.Combine(store.DirectoryPath,
                HumanTextInputObservationContract.FileName), "");
            Assert.Contains("undeclared_human_text_input_stream",
                RecordingSessionAuditor.Audit(store.DirectoryPath).Errors.Keys);
        }
        finally { Delete(root); }
    }

    [Fact]
    public void FailedAppendCannotBeHiddenByLaterWritesOrCleanClose()
    {
        string root = Temp();
        try
        {
            var profile = Profile();
            var manifest = Manifest(profile) with { TextInputSchemaVersion = 1 };
            var store = RecordingSessionStore.Create(root, manifest, profile);
            Journal(store, manifest);
            Assert.Throws<InvalidDataException>(() => store.AppendHumanTextInputObservation(
                Accepted() with { Snapshot = null }));
            Assert.Equal("failed", store.GetSnapshot().AppendHealth);
            Assert.Throws<InvalidDataException>(() => store.AppendHumanTextInputObservation(Accepted()));
            store.AppendRunEvent(new RunJournalEvent(2, CurrentRecordingContract.RunJournalSchema,
                "journal-2", manifest.SessionId, "run-0001", manifest.TimelineId, 2,
                DateTimeOffset.UnixEpoch, "session_closed", null, null, null));
            Assert.Equal("failed", store.GetSnapshot().AppendHealth);
            Assert.Throws<InvalidDataException>(() => store.Dispose());
            Assert.False(File.Exists(Path.Combine(store.DirectoryPath, "session-close-receipt.json")));
            Assert.Contains("human_text_input_append_failure",
                RecordingSessionAuditor.Audit(store.DirectoryPath).Errors.Keys);
        }
        finally { Delete(root); }
    }

    [Fact]
    public void AuditRejectsDeletedRowAndUndeclaredStream()
    {
        string root = Temp();
        try
        {
            var profile = Profile();
            var manifest = Manifest(profile) with { TextInputSchemaVersion = 1 };
            string session;
            using (var store = RecordingSessionStore.Create(root, manifest, profile))
            {
                session = store.DirectoryPath;
                Journal(store, manifest);
                store.AppendHumanTextInputObservation(Accepted());
            }
            File.WriteAllText(Path.Combine(session, HumanTextInputObservationContract.FileName), "");
            Assert.Contains("human_text_input_close_seal_mismatch",
                RecordingSessionAuditor.Audit(session).Errors.Keys);
            File.Delete(Path.Combine(session, HumanTextInputObservationContract.FileName));
            Assert.Contains("human_text_input_stream_missing",
                RecordingSessionAuditor.Audit(session).Errors.Keys);
        }
        finally { Delete(root); }
    }

    [Fact]
    public void InterruptedRecoveryRetainsTheDeclaredSideStream()
    {
        string root = Temp(), recovered = Temp();
        try
        {
            var profile = Profile();
            var manifest = Manifest(profile) with
            {
                TextInputSchemaVersion = 1, RecoverySchemaVersion = 1
            };
            string session;
            using (var store = RecordingSessionStore.Create(root, manifest, profile))
            {
                session = store.DirectoryPath;
                Journal(store, manifest);
                store.AppendHumanTextInputObservation(Accepted() with
                {
                    Disposition = HumanTextInputObservationContract.RejectedOrCancelled,
                    ReasonCode = "native_carrier_not_retained"
                });
            }
            File.Delete(Path.Combine(session, "session-close-receipt.json"));
            string originalHash = EvidenceIdentity.Sha256File(Path.Combine(session,
                HumanTextInputObservationContract.FileName));
            var result = JsonSerializer.SerializeToElement(
                InterruptedRecordingRecovery.RecoverOne(root, recovered));
            Assert.Equal("recovered", result.GetProperty("status").GetString());
            string copy = Assert.Single(Directory.GetDirectories(recovered));
            Assert.Equal(originalHash, EvidenceIdentity.Sha256File(Path.Combine(copy,
                HumanTextInputObservationContract.FileName)));
            Assert.Equal("pass", RecordingSessionAuditor.Audit(copy).Status);
        }
        finally { Delete(root); Delete(recovered); }
    }

    [Fact]
    public void LegacyRowsDoNotAcquireNewOrderFieldsWhenWritten()
    {
        string json = JsonSerializer.Serialize(Accepted(), EvidenceJson.Options);
        Assert.DoesNotContain("observation_order", json);
        Assert.Empty(HumanTextInputObservationValidator.Validate(Accepted()));
        Assert.Contains("text_input_legacy_order_metadata",
            HumanTextInputObservationValidator.Validate(Accepted() with
            { ObservationOrder = new(1, 0) }));
    }

    [Fact]
    public void NestedCaptureOrderPersistsThroughPhysicalAppendAndCloseAudit()
    {
        string root = Temp();
        try
        {
            var profile = Profile();
            var manifest = Manifest(profile) with { TextInputSchemaVersion = 2 };
            string session;
            using (var store = RecordingSessionStore.Create(root, manifest, profile))
            {
                session = store.DirectoryPath;
                Journal(store, manifest);
                // Outer capture 1 precedes inner 2; inner completes first.
                store.AppendHumanTextInputObservation(Ordered(1, 2, 0));
                store.AppendHumanTextInputObservation(Ordered(2, 1, 0));
                // Only the later observation can know both completions.
                store.AppendHumanTextInputObservation(Ordered(3, 3, 2));
                Assert.Equal(0, store.GetSnapshot().Counters.Records);
            }
            var rows = File.ReadAllLines(Path.Combine(session,
                HumanTextInputObservationContract.FileName))
                .Select(line => JsonSerializer.Deserialize<HumanTextInputObservation>(
                    line, EvidenceJson.Options)!).ToArray();
            Assert.Equal(new long[] { 1, 2, 3 }, rows.Select(row => row.Sequence));
            Assert.Equal(new long?[] { 2, 1, 3 }, rows.Select(row => row.ObservationOrder!.CaptureOrdinal));
            Assert.Equal(new long[] { 0, 0, 2 }, rows.Select(row => row.ObservationOrder!.CompletedAppendWatermark));
            Assert.Equal("pass", RecordingSessionAuditor.Audit(session).Status);
            Assert.Equal(0, RecordingSessionAuditor.Audit(session).ValidRecords);
        }
        finally { Delete(root); }
    }

    [Theory]
    [InlineData(0, 0)]
    [InlineData(-1, 0)]
    [InlineData(1, -1)]
    [InlineData(1, 3)]
    public void OrderedRowsRejectOneInvalidFact(long capture, long watermark)
    {
        Assert.Contains("text_input_observation_order_invalid",
            HumanTextInputObservationValidator.Validate(Ordered(3, capture, watermark)));
    }

    [Fact]
    public void CompletedPrefixUsesMaximumCaptureNotJustLastAppend()
    {
        var ledger = new HumanTextInputOrderLedger();
        Assert.Null(ledger.Observe(Ordered(1, 100, 0)));
        Assert.Null(ledger.Observe(Ordered(2, 1, 0)));
        Assert.Equal("human_text_input_capture_order_invalid",
            ledger.Observe(Ordered(3, 50, 2)));
        var duplicate = new HumanTextInputOrderLedger();
        Assert.Null(duplicate.Observe(Ordered(1, 1, 0)));
        Assert.Equal("human_text_input_capture_order_invalid",
            duplicate.Observe(Ordered(2, 1, 0)));
    }

    [Fact]
    public void CaptureFailureRetainsCompletedPrefixWithoutInventingAnObservation()
    {
        var failed = Ordered(2, 2, 1) with {
            ObservationOrder = new(null, 1), Snapshot = null, SnapshotSha256 = null,
            ChosenAction = null, MappingStatus = "capture_failed", MatchCount = 0,
            Disposition = HumanTextInputObservationContract.CaptureFailed,
            ReasonCode = "capture_unavailable"
        };
        Assert.Empty(HumanTextInputObservationValidator.Validate(failed));
        var ledger = new HumanTextInputOrderLedger();
        Assert.Null(ledger.Observe(Ordered(1, 1, 0)));
        Assert.Null(ledger.Observe(failed));
        Assert.Null(ledger.Observe(Ordered(3, 3, 2)));
        // The native freeze can succeed before serialization fails. Keep that
        // known position while still exposing no usable observation or label.
        Assert.Empty(HumanTextInputObservationValidator.Validate(failed with {
            ObservationOrder = new(2, 1)
        }));
        Assert.NotEmpty(HumanTextInputObservationValidator.Validate(failed with {
            ObservationOrder = new(2, 1), Environment = null
        }));
    }

    [Fact]
    public void UnknownCapturePositionIsExplicitNullInSealedRecording()
    {
        string root = Temp();
        try
        {
            var profile = Profile();
            var manifest = Manifest(profile) with { TextInputSchemaVersion = 2 };
            string session;
            using (var store = RecordingSessionStore.Create(root, manifest, profile))
            {
                session = store.DirectoryPath;
                Journal(store, manifest);
                store.AppendHumanTextInputObservation(Ordered(1, 1, 0));
                store.AppendHumanTextInputObservation(Ordered(2, 2, 1) with {
                    ObservationOrder = new(null, 1), Environment = null,
                    Snapshot = null, SnapshotSha256 = null, ChosenAction = null,
                    MappingStatus = "capture_failed", MatchCount = 0,
                    Disposition = HumanTextInputObservationContract.CaptureFailed,
                    ReasonCode = "capture_unavailable"
                });
            }
            string[] rows = File.ReadAllLines(Path.Combine(session,
                HumanTextInputObservationContract.FileName));
            using var json = JsonDocument.Parse(rows[1]);
            var order = json.RootElement.GetProperty("observation_order");
            Assert.Equal(JsonValueKind.Null, order.GetProperty("capture_ordinal").ValueKind);
            Assert.Equal(1, order.GetProperty("completed_append_watermark").GetInt64());
            Assert.Equal("pass", RecordingSessionAuditor.Audit(session).Status);
        }
        finally { Delete(root); }
    }

    private static HumanTextInputObservation Ordered(long sequence, long capture, long watermark) =>
        Accepted() with {
            SchemaVersion = HumanTextInputObservationContract.SchemaVersion,
            Schema = HumanTextInputObservationContract.Schema,
            Sequence = sequence, RecordId = $"order-{sequence}",
            ObservationOrder = new(capture, watermark)
        };

    private static HumanTextInputObservation Accepted()
    {
        JsonObject action = JsonNode.Parse("{\"action_id\":\"card-1\",\"kind\":\"native_input\",\"verb\":\"begin_card_play\",\"label\":\"Play Strike\",\"subject_referent_id\":\"card-1\",\"arguments\":[],\"effect_domain\":\"native_input\"}")!.AsObject();
        JsonObject snapshot = JsonNode.Parse("{\"schema\":\"sts2.player-environment/text-menu-snapshot-1\",\"input_profile\":\"text-menu-v1\",\"snapshot_id\":\"snapshot-1\",\"status\":\"interactive\",\"completeness\":{\"status\":\"complete\"},\"session\":{\"runtime_instance_id\":\"runtime-1\",\"environment_fingerprint\":\"environment-1\"},\"menu\":{\"cursor\":\"root\"},\"menu_actions\":{\"status\":\"complete\",\"materialized_count\":1,\"total_count\":1,\"actions\":[]}}")!.AsObject();
        snapshot["interaction"] = JsonNode.Parse("{\"interaction_id\":\"combat-1\",\"kind\":\"combat_turn\",\"stage\":\"ready\"}");
        snapshot["protocol_version"] = "1.0.0";
        snapshot["sequence"] = 1;
        snapshot["observed_at"] = DateTimeOffset.UnixEpoch.ToString("O");
        snapshot["persistent"] = null;
        snapshot["interaction"]!["content_schema"] = "sts2.player-environment/surface/combat_turn-1";
        snapshot["interaction"]!["prompt"] = null;
        snapshot["interaction"]!["content"] = JsonNode.Parse("{\"surface\":{\"kind\":\"combat_turn\"},\"context\":{\"kind\":\"combat\"}}");
        snapshot["interaction"]!["capabilities"] = new JsonArray();
        snapshot["referents"] = JsonNode.Parse("[{\"referent_id\":\"card-1\",\"role\":\"card\",\"kind\":\"entity\",\"label\":\"Strike\",\"state\":{\"visible\":true,\"enabled\":true,\"selected\":false,\"focused\":false,\"observation_basis\":\"native_visible_fact\"},\"properties_schema\":null,\"properties\":null}]");
        snapshot["information_policy"] = JsonNode.Parse("{\"id\":\"player_visible_v1\",\"scope\":\"current_page\",\"includes_hidden_information\":false,\"unknown_field_behavior\":\"omit\"}");
        snapshot["completeness"] = JsonNode.Parse("{\"status\":\"complete\",\"visible_information\":\"complete\",\"interaction_discovery\":\"complete\",\"missing\":[],\"hidden_by_policy\":[]}");
        snapshot["menu"]!["native_snapshot_id"] = "native-1";
        snapshot["menu"]!["revision"] = 0;
        snapshot["menu_actions"]!["ordering_semantics"] = "native_order_with_fixed_information_groups";
        snapshot["menu_actions"]!["actions"]!.AsArray().Add(action.DeepClone());
        const string sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        var artifact = new ExactArtifactIdentity("artifact", "1", new string('b', 40), sha, sha,
            "11111111-1111-1111-1111-111111111111");
        var environment = new RecorderEnvironmentIdentity(
            new ExactGameIdentity("game", "commit", sha, "11111111-1111-1111-1111-111111111111"),
            artifact, artifact, "1.0.0", "runtime-1", "environment-1", "exact", sha);
        return new HumanTextInputObservation(1, HumanTextInputObservationContract.LegacySchema, 1,
            "text-1", "session-test", "timeline-test", "run-0001",
            DateTimeOffset.UnixEpoch, DateTimeOffset.UnixEpoch.AddSeconds(1), environment,
            snapshot, EvidenceIdentity.Sha256Json(snapshot), action, "exact_unique", 1,
            HumanTextInputObservationContract.ExactMappingBasis, "owner-1", "subject-1",
            "carrier-1", HumanTextInputObservationContract.NativeMechanism,
            HumanTextInputObservationContract.AcceptedInput, null, false);
    }

    private static HumanCaptureProfile Profile() => new(2,
        CurrentRecordingContract.CaptureProfileSchema, "profile-test",
        CurrentRecordingContract.RecordSchema,
        new[] { "ordinary_combat.play_card" },
        new[] { new CaptureReadRequirement("pre", "run_deck", true) },
        new[] { "test_only" });

    private static CurrentRecordingManifest Manifest(HumanCaptureProfile profile) => new(2,
        CurrentRecordingContract.ManifestSchema, "session-test", "timeline-test",
        DateTimeOffset.UnixEpoch, "1", new string('b', 40), "osx-arm64",
        profile.ProfileId, EvidenceIdentity.Sha256Json(profile),
        profile.SupportedActionFamilies, profile.NonClaims)
    { ContinuousSchemaVersion = 1, DispositionSchemaVersion = 1, CloseSchemaVersion = 1 };

    private static void Journal(RecordingSessionStore store, CurrentRecordingManifest manifest) =>
        store.AppendRunEvent(new RunJournalEvent(2, CurrentRecordingContract.RunJournalSchema,
            "journal-1", manifest.SessionId, "run-0001", manifest.TimelineId, 1,
            DateTimeOffset.UnixEpoch, "run_started", null, "snapshot-1", null));

    private static string Temp() => Path.Combine(Path.GetTempPath(), $"text-core-{Guid.NewGuid():N}");
    private static HumanTextInputObservation MutateSnapshot(
        HumanTextInputObservation original, Action<JsonObject> mutation)
    {
        JsonObject snapshot = (JsonObject)original.Snapshot!.DeepClone();
        mutation(snapshot);
        return original with { Snapshot = snapshot, SnapshotSha256 = EvidenceIdentity.Sha256Json(snapshot) };
    }
    private static void Delete(string path) { if (Directory.Exists(path)) Directory.Delete(path, true); }
}
