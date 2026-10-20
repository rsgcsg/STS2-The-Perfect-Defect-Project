using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Connector.PlayerEnvironment.Witness;
using STS2HumanAnnotator.Core;
using STS2HumanAnnotator.Mod;
using Xunit;

namespace STS2HumanAnnotator.SourceNative.Tests;

public sealed class P1NativeScopeInputsTests
{
    private static ProcessLocalNativeWitnessFrame Frame(string status = "settling", bool external = false,
        bool hidden = false, string captureStatus = "complete", object? point = null,
        string? interaction = null, string? contextKind = null)
    {
        PlayerEnvironmentBoundAction[] actions = point == null ? [] :
            [new("current-S-bound", "activate", "current-map", "exact-point", Array.Empty<PlayerEnvironmentBoundActionArgument>(), "Choose point")];
        var snapshot = new PlayerEnvironmentSnapshot("1.0.0", PlayerEnvironmentContract.SnapshotSchema,
            "current-S", 1, DateTimeOffset.UnixEpoch, status, null,
            new("current-owner", interaction ?? (point == null ? "combat_turn" : "map_navigation"), status, null, "surface",
                new(new JsonObject(), new JsonObject { ["kind"] = contextKind }),
                Array.Empty<PlayerEnvironmentInteractionCapability>()), Array.Empty<PlayerEnvironmentReferent>(),
            new("sts2.player-environment/bound-actions-1", "complete", actions.Length, actions.Length, 512, "native", actions),
            Array.Empty<PlayerEnvironmentReadOpportunity>(), new("complete", "complete", "native", Array.Empty<string>(), Array.Empty<string>()),
            new("runtime", "environment"), new("player_visible_v1", "current", hidden, "omit"));
        var basis = new JsonObject { ["schema"] = "sts2.player-environment/public-basis-1", ["capture_status"] = captureStatus,
            ["snapshot"] = JsonSerializer.SerializeToNode(snapshot, EvidenceJson.Options), ["reads"] = new JsonArray(), ["missing"] = new JsonArray() };
        return new(snapshot, null!, new string('a', 64), external,
            point == null ? new Dictionary<string, object>() : new Dictionary<string, object> { ["exact-point"] = point },
            actions.Select(action => action.BoundActionId).ToHashSet(StringComparer.Ordinal)) {
            PublicBasis = new(snapshot.SnapshotId, JsonSerializer.SerializeToUtf8Bytes(basis, EvidenceJson.Options), DateTimeOffset.UnixEpoch)
        };
    }

    [Fact]
    public void NativeEndTurnSemanticIngressRetainsTheOriginalSubjectlessInputThroughSettlingAcceptance()
    {
        // Actual NativeEndTurnPatch supplies semanticSelection and leaves the
        // optional expectedAction null. Selftest06 had this exact source shape.
        var semantic = new ProcessLocalObservedAction("end_turn", null, new Dictionary<string, object>());
        var expected = P1NativeScopeInputs.PublicInput("EndPlayerTurnAction", null, semantic);
        Assert.Same(semantic, expected);
        var h = Frame();
        Assert.Null(P1NativeScopeInputs.CurrentPublicAction(h, expected));
        Assert.True(P1NativeScopeInputs.CanRetainQueuedEndTurn("EndPlayerTurnAction", expected, h, Array.Empty<string>()));
        var accepted = h.ResolveAcceptedInput(expected!, semantic);
        Assert.Equal("exact_native_input", accepted.Status);
        Assert.Equal("end_turn|-|", accepted.NativeInput!.ActionKey); Assert.Null(accepted.NativeInput.SubjectReferentId);
        Assert.Empty(h.Snapshot.BoundActions.Actions); // Source retention grants no delivery authority.
        Assert.Equal("settling", h.Snapshot.Status);
    }

    [Fact]
    public void CompleteCombatSourceBehindPotionPopupKeepsExactNativeInputWithoutPublicDelivery()
    {
        // Rolling01 had complete popup context, but the input is accepted by
        // the combat owner. The popup catalog need not advertise that gesture.
        var frame = Frame("interactive", interaction: "potion_popup", contextKind: "combat");
        var end = new ProcessLocalObservedAction("end_turn", null, new Dictionary<string, object>());
        object card = new(); var play = new ProcessLocalObservedAction("play", card, new Dictionary<string, object>());
        Assert.True(P1NativeScopeInputs.CanRetainCombatSource(frame, Array.Empty<string>()));
        Assert.True(P1NativeScopeInputs.CanRetainQueuedEndTurn("EndPlayerTurnAction", end, frame, Array.Empty<string>()));
        Assert.Equal("exact_native_input", frame.ResolveAcceptedInput(end, end).Status);
        Assert.Equal("exact_native_input", frame.ResolveAcceptedInput(play, play).Status);
        Assert.Null(P1NativeScopeInputs.CurrentPublicAction(frame, end));
        Assert.Null(P1NativeScopeInputs.CurrentPublicAction(frame, play));
        Assert.Empty(frame.Snapshot.BoundActions.Actions);
        foreach (var invalid in new[] {
            Frame(interaction: "potion_popup", contextKind: "map"),
            Frame(interaction: "potion_popup"), Frame(interaction: "event_option", contextKind: "combat"),
            Frame(interaction: "potion_popup", contextKind: "combat", hidden: true),
            Frame(interaction: "potion_popup", contextKind: "combat", external: true),
            Frame(interaction: "potion_popup", contextKind: "combat", captureStatus: "partial") })
            Assert.False(P1NativeScopeInputs.CanRetainCombatSource(invalid, Array.Empty<string>()));
        Assert.False(P1NativeScopeInputs.CanRetainCombatSource(frame, new[] { "required_read_evidence_unavailable" }));
    }

    [Fact]
    public void EndTurnSourceCannotBypassHiddenControllerIncompleteReadOrEnvironmentBlockers()
    {
        var end = new ProcessLocalObservedAction("end_turn", null, new Dictionary<string, object>());
        foreach (var h in new[] { Frame(external: true), Frame(hidden: true), Frame(captureStatus: "partial") })
            Assert.False(P1NativeScopeInputs.CanRetainQueuedEndTurn("EndPlayerTurnAction", end, h, Array.Empty<string>()));
        Assert.False(P1NativeScopeInputs.CanRetainQueuedEndTurn("EndPlayerTurnAction", end, Frame(), new[] { "required_read_evidence_unavailable" }));
        Assert.Null(P1NativeScopeInputs.PublicInput("PlayCardAction", null, end));
        Assert.Null(P1NativeScopeInputs.PublicInput("EndPlayerTurnAction", null, end with { Subject = new object() }));
        Assert.Null(P1NativeScopeInputs.PublicInput("EndPlayerTurnAction", null, end with { Arguments = new Dictionary<string, object> { ["target"] = new object() } }));
    }

    [Fact]
    public void MapExecutionResolvesCurrentPublicActivateWhileKeepingNativeTravelSeparate()
    {
        object exactPoint = new();
        var originalPublic = new ProcessLocalObservedAction("activate", exactPoint, new Dictionary<string, object>());
        var nativeSemantic = originalPublic with { Verb = "travel" };
        var execution = Frame("interactive", point: exactPoint);
        Assert.Null(P1NativeScopeInputs.CurrentPublicAction(execution, nativeSemantic)); // Selftest06's wrong role.
        var current = P1NativeScopeInputs.CurrentPublicAction(execution, originalPublic);
        Assert.Equal("current-S-bound", current!.BoundActionId);
        Assert.Equal("activate", current.Verb); Assert.Equal("travel", nativeSemantic.Verb);
        // Existing schema3 explicitly permits the owning native verb to differ
        // from the public verb when the exact current BoundAction is bound.
        var action = new SemanticActionReference("map-witness", 1, "map-decision", "run", "VoteForMapCoordAction", 1, "original-H") {
            BoundAction = new(current.BoundActionId, current.Verb, current.SubjectReferentId, new Dictionary<string, string>(), current.Label)
        };
        const string key = "travel|exact-point|";
        var state = new JsonObject { ["domain"] = "map_navigation" };
        var space = new ExecutionSemanticActionSpaceEvidence(3, ExecutionSemanticActionSpaceContract.Schema, "map-witness", "before_execution",
            "captured", "map_navigation", EvidenceIdentity.Sha256Json(state), state, new string('a', 64),
            new[] { new ExecutionSemanticAction(key, "travel", "exact-point", new Dictionary<string, string>(), "native_map_owner_fixture") },
            key, "exact_once", 1, new[] { "native_map_owner_fixture" }, Array.Empty<string>(), null) { HumanBoundActionId = current.BoundActionId };
        Assert.Empty(ExecutionSemanticActionSpaceValidator.Validate(space, action));
        Assert.Contains("execution_semantic_human_action_mismatch", ExecutionSemanticActionSpaceValidator.Validate(
            space with { HumanBoundActionId = "stale-H-bound" }, action));
        Assert.Null(P1NativeScopeInputs.CurrentPublicAction(execution, originalPublic with { Subject = new object() }));
        Assert.Same(originalPublic, P1NativeScopeInputs.PublicInput("VoteForMapCoordAction", originalPublic, nativeSemantic));
    }

    [Fact]
    public void DeferredFailureIssueKeepsOriginalDetailsAndOccurrenceIdentity()
    {
        const string detail = "pre_frame_not_complete_interactive;current_status=settling;catalog=complete;candidates=0";
        var row = JsonSerializer.SerializeToNode(P1NativeScopeInputs.Failure("witness", "decision", detail, "original-H", "fail_closed", null), EvidenceJson.Options)!;
        Assert.Equal(detail, row["detail"]!.GetValue<string>());
        Assert.Equal("original-H", row["snapshot_id"]!.GetValue<string>());
        Assert.Equal("witness", row["action_witness_id"]!.GetValue<string>());
    }
}
