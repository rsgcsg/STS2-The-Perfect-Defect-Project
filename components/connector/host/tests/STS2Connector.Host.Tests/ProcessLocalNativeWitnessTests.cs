using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Connector.PlayerEnvironment.Witness;
using STS2Platform.NativeFoundation;
using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.Host.Tests;

public sealed class ProcessLocalNativeWitnessTests
{
    [Fact]
    public void EndTurnNativeInputRequiresTheExactSubjectlessOperandlessShape()
    {
        var frame = Frame(new NativeEntityRegistry(), Array.Empty<PlayerEnvironmentBoundAction>(), "settling", "complete");
        var expected = new ProcessLocalObservedAction("end_turn", null, new Dictionary<string, object>());
        var exact = frame.ResolveAcceptedInput(expected, expected);
        Assert.Equal("exact_native_input", exact.Status); Assert.Null(exact.NativeInput!.SubjectReferentId);
        Assert.Equal("end_turn|-|", exact.NativeInput.ActionKey);
        Assert.Equal("zero", frame.ResolveAcceptedInput(expected, expected with { Subject = new object() }).Status);
        Assert.Equal("zero", frame.ResolveAcceptedInput(expected, expected with { Arguments = new Dictionary<string, object> { ["target"] = new object() } }).Status);
        var badCard = expected with { Verb = "play" };
        Assert.Equal("zero", frame.ResolveAcceptedInput(badCard, badCard).Status);
    }

    [Fact]
    public void QueuedPotionInputKeepsNativePotionIdentityWithNoPublicDeliveryCatalog()
    {
        var frame = Frame(new NativeEntityRegistry(), Array.Empty<PlayerEnvironmentBoundAction>(), "settling", "complete");
        object potion = new(), target = new();
        var expected = new ProcessLocalObservedAction("use", potion, new Dictionary<string, object> { ["target"] = target });
        var exact = frame.ResolveAcceptedInput(expected, expected);
        Assert.Equal("exact_native_input", exact.Status);
        Assert.StartsWith("potion_", exact.NativeInput!.SubjectReferentId);
        Assert.StartsWith("use|potion_", exact.NativeInput.ActionKey);
        Assert.Null(exact.BoundAction);
        Assert.Empty(frame.Snapshot.BoundActions.Actions);
        Assert.Equal("zero", frame.ResolveAcceptedInput(expected, expected with { Subject = new object() }).Status);
        Assert.Equal("zero", frame.ResolveAcceptedInput(expected,
            expected with { Arguments = new Dictionary<string, object> { ["target"] = new object() } }).Status);
    }

    [Fact]
    public void AcceptedInputUsesExactScopedOperandsWithoutPublishingDeliveryAction()
    {
        var frame = Frame(new NativeEntityRegistry(), Array.Empty<PlayerEnvironmentBoundAction>(), "settling", "complete");
        object card = new(), target = new();
        var expected = new ProcessLocalObservedAction("play", card, new Dictionary<string, object> { ["target"] = target });
        var exact = frame.ResolveAcceptedInput(expected, expected with { Arguments = new Dictionary<string, object> { ["target"] = target } });
        Assert.Equal("exact_native_input", exact.Status);
        Assert.Null(exact.BoundAction);
        Assert.Null(exact.BoundActionId);
        Assert.NotNull(exact.NativeInput);
        Assert.Empty(frame.Snapshot.BoundActions.Actions);
        Assert.Equal("zero", frame.ResolveAcceptedInput(expected, expected with { Subject = new object() }).Status);
        Assert.Equal("zero", frame.ResolveAcceptedInput(expected, expected with { Arguments = new Dictionary<string, object> { ["target"] = new object() } }).Status);
        Assert.Equal("zero", frame.ResolveAcceptedInput(expected, expected with { Verb = "discard" }).Status);
        Assert.Equal("zero", Frame(new NativeEntityRegistry(), Array.Empty<PlayerEnvironmentBoundAction>(),
            "settling", "complete", externalController: true).ResolveAcceptedInput(expected, expected).Status);
        Assert.Equal("zero", Frame(new NativeEntityRegistry(), Array.Empty<PlayerEnvironmentBoundAction>(),
            "settling", "complete", completeness: "partial").ResolveAcceptedInput(expected, expected).Status);
    }

    [Fact]
    public void SemanticActionKeyIsDeterministicAndRoleOrdered()
    {
        string first = NativeSemanticActionCatalog.BuildKey(
            "play",
            "card-1",
            new Dictionary<string, string>
            {
                ["z_target"] = "enemy-2",
                ["a_mode"] = "normal"
            });
        string second = NativeSemanticActionCatalog.BuildKey(
            "play",
            "card-1",
            new Dictionary<string, string>
            {
                ["a_mode"] = "normal",
                ["z_target"] = "enemy-2"
            });

        Assert.Equal(first, second);
        Assert.Equal("play|card-1|a_mode=normal,z_target=enemy-2", first);
    }

    [Fact]
    public void ExactNativeInstanceSelectsOneDuplicateLookingCard()
    {
        var entities = new NativeEntityRegistry();
        var first = new object();
        var second = new object();
        string firstId = entities.GetId(first, "card");
        string secondId = entities.GetId(second, "card");
        PlayerEnvironmentBoundAction firstAction = Action("action-first", "play", firstId);
        PlayerEnvironmentBoundAction secondAction = Action("action-second", "play", secondId);
        ProcessLocalNativeWitnessFrame frame = Frame(
            entities,
            firstAction,
            secondAction);

        ProcessLocalNativeMatch result = frame.Resolve(new ProcessLocalObservedAction(
            "play",
            second,
            new Dictionary<string, object>()));

        Assert.Equal("exact_unique", result.Status);
        Assert.Equal(1, result.MatchCount);
        Assert.Equal("action-second", result.BoundActionId);
    }

    [Fact]
    public void ExactTargetReferenceDisambiguatesTargetedAction()
    {
        var entities = new NativeEntityRegistry();
        var card = new object();
        var firstTarget = new object();
        var secondTarget = new object();
        string cardId = entities.GetId(card, "card");
        PlayerEnvironmentBoundAction first = Action(
            "action-first-target",
            "play",
            cardId,
            new PlayerEnvironmentBoundActionArgument(
                "target",
                entities.GetId(firstTarget, "enemy")));
        PlayerEnvironmentBoundAction second = Action(
            "action-second-target",
            "play",
            cardId,
            new PlayerEnvironmentBoundActionArgument(
                "target",
                entities.GetId(secondTarget, "enemy")));

        ProcessLocalNativeMatch result = Frame(entities, first, second).Resolve(
            new ProcessLocalObservedAction(
                "play",
                card,
                new Dictionary<string, object> { ["target"] = firstTarget }));

        Assert.Equal("exact_unique", result.Status);
        Assert.Equal("action-first-target", result.BoundActionId);
    }

    [Fact]
    public void MissingAndAmbiguousMappingsFailClosed()
    {
        var entities = new NativeEntityRegistry();
        var card = new object();
        string cardId = entities.GetId(card, "card");
        ProcessLocalNativeWitnessFrame missingFrame = Frame(
            entities,
            Action("action-card", "play", cardId));
        ProcessLocalNativeMatch missing = missingFrame.Resolve(
            new ProcessLocalObservedAction(
                "play",
                new object(),
                new Dictionary<string, object>()));

        PlayerEnvironmentBoundAction duplicateA = Action("action-a", "play", cardId);
        PlayerEnvironmentBoundAction duplicateB = Action("action-b", "play", cardId);
        ProcessLocalNativeMatch ambiguous = Frame(entities, duplicateA, duplicateB).Resolve(
            new ProcessLocalObservedAction(
                "play",
                card,
                new Dictionary<string, object>()));

        Assert.Equal("zero", missing.Status);
        Assert.Null(missing.BoundActionId);
        Assert.Equal("ambiguous", ambiguous.Status);
        Assert.Equal(2, ambiguous.MatchCount);
        Assert.Null(ambiguous.BoundActionId);
    }

    [Fact]
    public void IncompleteOrNonInteractiveFrameCannotMatch()
    {
        var entities = new NativeEntityRegistry();
        var card = new object();
        PlayerEnvironmentBoundAction action = Action(
            "action-card",
            "play",
            entities.GetId(card, "card"));
        ProcessLocalNativeWitnessFrame frame = Frame(
            entities,
            new[] { action },
            snapshotStatus: "settling",
            projectionStatus: "complete");

        ProcessLocalNativeMatch result = frame.Resolve(new ProcessLocalObservedAction(
            "play",
            card,
            new Dictionary<string, object>()));

        Assert.Equal("frame_not_authoritative", result.Status);
        Assert.Equal(0, result.MatchCount);
    }

    [Fact]
    public void NativeInputNeedsExactOwnerOperationAndSubjectFromFrozenCatalog()
    {
        var owner = new object();
        var card = new object();
        var entities = new NativeEntityRegistry();
        var action = Action("select-a", "select", entities.GetId(card, "card"));
        var frame = Frame(entities, new[] { action }, "interactive", "complete",
            new[] { new ProcessLocalNativeInputBinding("select-a", "native_select", new[] { owner }, new[] { card }) });
        Assert.Equal("select-a", frame.ResolveNativeInput(owner, "native_select", card).BoundActionId);
        Assert.Null(frame.ResolveNativeInput(new object(), "native_select", card).BoundActionId);
        Assert.Null(frame.ResolveNativeInput(owner, "native_deselect", card).BoundActionId);
        Assert.Null(frame.ResolveNativeInput(owner, "native_select", new object()).BoundActionId);
        Assert.True(frame.HasNativeInputOwner(owner));
        Assert.False(frame.HasNativeInputOwner(new object()));
    }

    [Fact]
    public void NativeInputRejectsAmbiguityAndIncompleteCatalog()
    {
        var owner = new object();
        var entities = new NativeEntityRegistry();
        var actions = new[] { Action("a", "confirm", null), Action("b", "confirm", null) };
        var bindings = actions.Select(action => new ProcessLocalNativeInputBinding(
            action.BoundActionId, "confirm", new[] { owner }, Array.Empty<object>())).ToArray();
        Assert.Equal("ambiguous", Frame(entities, actions, "interactive", "complete", bindings)
            .ResolveNativeInput(owner, "confirm").Status);
        var incomplete = Frame(entities, actions, "interactive", "partial", bindings);
        Assert.Null(incomplete.ResolveNativeInput(owner, "confirm").BoundActionId);
        Assert.False(incomplete.HasNativeInputOwner(owner));
    }

    [Theory]
    [InlineData("select_native_combat_pile_card", "select")]
    [InlineData("deselect_native_combat_pile_card", "deselect")]
    [InlineData("select_simple_card", "select")]
    [InlineData("select_native_deck_card", "select")]
    [InlineData("select_deck_upgrade_card", "select")]
    [InlineData("toggle_deck_transform_card", "select")]
    [InlineData("toggle_card", "deselect")]
    public void FullLogicalGridProvidesCanonicalNativeCallbackJoinWithoutLosingExactCardIdentity(string legacyOperation, string verb)
    {
        object owner = new(), first = new(), second = new();
        var entities = new NativeEntityRegistry();
        string ownerId = entities.GetId(owner, "screen"), firstId = entities.GetId(first, "card"), secondId = entities.GetId(second, "card");
        var action = Action("full-grid-first", verb, firstId);
        var other = Action("full-grid-second", verb, secondId);
        var inputs = new Dictionary<string, PlayerEnvironmentNativeBinding> {
            [action.BoundActionId] = GridBinding(verb, ownerId, firstId, NativeLogicalGridInputKind.Toggle),
            [other.BoundActionId] = GridBinding(verb, ownerId, secondId, NativeLogicalGridInputKind.Toggle) };
        var frozen = PlayerEnvironmentNativeWitness.CaptureNativeInputBindings(inputs,
            new Dictionary<string, object> { [ownerId] = owner, [firstId] = first, [secondId] = second });
        var frame = Frame(entities, new[] { action, other }, "interactive", "complete", frozen);
        Assert.Equal("zero", frame.ResolveNativeInput(owner, legacyOperation, first).Status); // The proved pre-fix faultline.
        Assert.Equal(action.BoundActionId, frame.ResolveNativeInput(owner, verb, first).BoundActionId);
        Assert.Equal(other.BoundActionId, frame.ResolveNativeInput(owner, verb, second).BoundActionId);
        Assert.Equal("zero", frame.ResolveNativeInput(new object(), verb, first).Status);
        Assert.Equal("zero", frame.ResolveNativeInput(owner, verb, new object()).Status);
    }

    [Theory]
    [InlineData("open_preview", "confirm")]
    [InlineData("confirm_selection", "confirm")]
    [InlineData("confirm", "confirm")]
    [InlineData("cancel_preview", "cancel")]
    [InlineData("cancel_selection", "cancel")]
    public void GridControlCallbackJoinRequiresTheExactPressedOriginalControl(string nativeVerb, string callbackVerb)
    {
        object owner = new(), control = new(), otherControl = new();
        var entities = new NativeEntityRegistry();
        string ownerId = entities.GetId(owner, "screen"), controlId = entities.GetId(control, "control");
        var action = Action("exact-control", "confirm", controlId);
        var bindings = new Dictionary<string, PlayerEnvironmentNativeBinding> {
            [action.BoundActionId] = GridBinding(nativeVerb, ownerId, controlId, NativeLogicalGridInputKind.Control) };
        var frozen = PlayerEnvironmentNativeWitness.CaptureNativeInputBindings(bindings,
            new Dictionary<string, object> { [ownerId] = owner, [controlId] = control });
        var frame = Frame(entities, new[] { action }, "interactive", "complete", frozen);
        Assert.Equal(action.BoundActionId, frame.ResolveNativeInput(owner, callbackVerb, control).BoundActionId);
        Assert.Equal(action.BoundActionId, frame.ResolveNativeInput(owner, nativeVerb, control).BoundActionId);
        Assert.Null(frame.ResolveNativeInput(owner, callbackVerb, otherControl).BoundActionId);
        Assert.Null(frame.ResolveNativeInput(new object(), callbackVerb, control).BoundActionId);
    }

    private static PlayerEnvironmentNativeBinding GridBinding(string verb, string ownerId, string subjectId, NativeLogicalGridInputKind kind)
    {
        var candidate = new NativeUiActionCandidate("grid-fixture", "native_logical_grid_input", verb, "Same visible label",
            new Dictionary<string, string>(), new Dictionary<string, NativeUiOperandDomain>(),
            new[] { new ActionEntityBinding("screen", ownerId), new ActionEntityBinding(kind == NativeLogicalGridInputKind.Control ? "control" : "card", subjectId) }, "exact_native_logical_grid_input");
        // NativeCallbackVerb uses only this already frozen input's kind/verb, never State/current UI.
        var spec = new NativeLogicalGridInputSpec("grid-fixture", "leaf", "request", verb, verb, "selecting", "Same visible label",
            subjectId, kind, null!);
        return new(new NativeUiBoundAction(candidate) { GridInput = spec }, new Dictionary<string, string>());
    }

    private static PlayerEnvironmentBoundAction Action(
        string id,
        string verb,
        string? subject,
        params PlayerEnvironmentBoundActionArgument[] arguments) =>
        new(id, verb, "interaction-test", subject, arguments, id);

    private static ProcessLocalNativeWitnessFrame Frame(
        NativeEntityRegistry entities,
        params PlayerEnvironmentBoundAction[] actions) =>
        Frame(entities, actions, "interactive", "complete");

    private static ProcessLocalNativeWitnessFrame Frame(
        NativeEntityRegistry entities,
        IReadOnlyList<PlayerEnvironmentBoundAction> actions,
        string snapshotStatus,
        string projectionStatus,
        IReadOnlyList<ProcessLocalNativeInputBinding>? nativeInputs = null,
        bool externalController = false, string completeness = "complete")
    {
        var snapshot = new PlayerEnvironmentSnapshot(
            PlayerEnvironmentContract.ProtocolVersion,
            PlayerEnvironmentContract.SnapshotSchema,
            "snapshot-test",
            1,
            DateTimeOffset.UnixEpoch,
            snapshotStatus,
            null,
            new PlayerEnvironmentInteraction(
                "interaction-test",
                "combat_turn",
                "choosing",
                null,
                "surface-test",
                new PlayerEnvironmentInteractionContent(new JsonObject(), new JsonObject()),
                Array.Empty<PlayerEnvironmentInteractionCapability>()),
            Array.Empty<PlayerEnvironmentReferent>(),
            new PlayerEnvironmentBoundActionProjection(
                "sts2.player-environment/bound-actions-1",
                projectionStatus,
                actions.Count,
                actions.Count,
                512,
                "test",
                actions),
            Array.Empty<PlayerEnvironmentReadOpportunity>(),
            new PlayerEnvironmentCompleteness(
                completeness,
                "test",
                "test",
                Array.Empty<string>(),
                Array.Empty<string>()),
            new PlayerEnvironmentSessionReference("runtime-test", "environment-test"),
            new PlayerEnvironmentInformationPolicy(
                "player_visible_v1",
                "test",
                false,
                "omit"));
        return new ProcessLocalNativeWitnessFrame(
            snapshot,
            null!,
            new string('a', 64),
            externalController,
            entities.CaptureExactReferences(
                actions.SelectMany(action => action.Arguments
                        .Select(argument => argument.ReferentId)
                    .Append(action.SubjectReferentId))
                    .Where(referentId => referentId != null)
                    .Cast<string>()),
            actions.Select(action => action.BoundActionId).ToHashSet(StringComparer.Ordinal),
            nativeInputs: nativeInputs);
    }
}
