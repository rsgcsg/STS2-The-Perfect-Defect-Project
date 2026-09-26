using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using STS2Connector.PlayerEnvironment.Witness;
using Xunit;

namespace STS2Connector;

public sealed class ProcessLocalTextMenuWitnessTests
{
    [Fact]
    public void ExactNativeReferencesSelectOneSameNamedCardWithoutDispatch()
    {
        var dispatches = new DispatchCounter();
        object hand = new();
        object firstCard = new();
        object secondCard = new();
        object firstHolder = new();
        object secondHolder = new();
        TextMenuFrame frame = Frame(hand, firstCard, firstHolder, dispatches);
        TextMenuLeaf other = frame.Leaves[0] with
        {
            Key = "begin-other", SubjectReferentId = "card-other",
            NativeWitness = Binding(hand, secondCard, secondHolder)
        };
        frame = frame with
        {
            Leaves = frame.Leaves.Append(other).ToArray(),
            Page = frame.Page with
            {
                Referents = frame.Page.Referents.Append(Referent("card-other")).ToArray()
            }
        };
        var capabilities = Capabilities();
        var frozen = new ProcessLocalTextMenuWitnessFrame(frame, capabilities, "source-digest", false);

        Assert.Same(capabilities, frozen.Capabilities);
        Assert.Equal("source-digest", frozen.SourceDigest);
        Assert.Equal("root", frozen.Snapshot.Menu.Cursor);
        Assert.Equal(4, frozen.Snapshot.MenuActions.Actions.Count);
        Assert.Equal("complete", frozen.Snapshot.MenuActions.Status);
        var first = frozen.Resolve(Observed(hand, firstCard, firstHolder));
        var second = frozen.Resolve(Observed(hand, secondCard, secondHolder));
        Assert.Equal("exact_unique", first.Status);
        Assert.Equal("card", first.Action!.SubjectReferentId);
        Assert.Equal("exact_unique", second.Status);
        Assert.Equal("card-other", second.Action!.SubjectReferentId);
        Assert.NotEqual(first.Action.ActionId, second.Action.ActionId);
        Assert.Equal(0, dispatches.Calls);
    }

    [Fact]
    public void WrongOwnerSubjectOrNamedArgumentsNeverGuessFromVisibleLabel()
    {
        var dispatches = new DispatchCounter();
        object hand = new();
        object card = new();
        object holder = new();
        var frozen = new ProcessLocalTextMenuWitnessFrame(
            Frame(hand, card, holder, dispatches), Capabilities(), "source", false);

        Assert.Equal("zero", frozen.Resolve(Observed(new object(), card, holder)).Status);
        Assert.Equal("zero", frozen.Resolve(Observed(hand, new object(), holder)).Status);
        Assert.Equal("zero", frozen.Resolve(Observed(hand, card, new object())).Status);
        Assert.Equal("zero", frozen.Resolve(new("begin_card_play", hand, card,
            new Dictionary<string, object> { ["different_role"] = holder })).Status);
        Assert.Equal("zero", frozen.Resolve(new("begin_card_play", hand, card,
            new Dictionary<string, object> { ["holder"] = holder, ["extra"] = new object() })).Status);
        Assert.Equal("zero", frozen.Resolve(new("end_turn", hand, card,
            new Dictionary<string, object> { ["holder"] = holder })).Status);
        Assert.Equal(0, dispatches.Calls);
    }

    [Fact]
    public void ExternalControlIncompleteAndAmbiguousFramesFailClosed()
    {
        var dispatches = new DispatchCounter();
        object hand = new();
        object card = new();
        object holder = new();
        TextMenuFrame frame = Frame(hand, card, holder, dispatches);
        var observed = Observed(hand, card, holder);

        Assert.Equal("controller_active", new ProcessLocalTextMenuWitnessFrame(
            frame, Capabilities(), "source", true).Resolve(observed).Status);
        TextMenuFrame incomplete = frame with { Page = frame.Page with
        {
            Completeness = frame.Page.Completeness with { Status = "incomplete" }
        } };
        Assert.Equal("frame_not_authoritative", new ProcessLocalTextMenuWitnessFrame(
            incomplete, Capabilities(), "source", false).Resolve(observed).Status);
        TextMenuFrame ambiguous = frame with { Leaves = frame.Leaves.Append(
            frame.Leaves[0] with { Key = "duplicate-native-binding" }).ToArray() };
        var match = new ProcessLocalTextMenuWitnessFrame(
            ambiguous, Capabilities(), "source", false).Resolve(observed);
        Assert.Equal("ambiguous", match.Status);
        Assert.Equal(2, match.MatchCount);
        Assert.Null(match.Action);
        Assert.Equal(0, dispatches.Calls);
    }

    [Fact]
    public void CaptureStartsAtRootWithoutChangingAgentCursorOrSerializingNativeObjects()
    {
        var dispatches = new DispatchCounter();
        object hand = new();
        object card = new();
        object holder = new();
        TextMenuFrame frame = Frame(hand, card, holder, dispatches);
        var agent = new TextMenuSession();
        TextMenuSnapshot root = agent.Observe(frame).Snapshot;
        string informationId = root.MenuActions.Actions.Single(action =>
            action.Verb == "open_information").ActionId;
        Assert.Equal("information", agent.Navigate(frame, root.SnapshotId,
            informationId).Menu.Cursor);

        var frozen = new ProcessLocalTextMenuWitnessFrame(frame, Capabilities(), "source", false);
        Assert.Equal("root", frozen.Snapshot.Menu.Cursor);
        Assert.Equal("information", agent.Observe(frame).Snapshot.Menu.Cursor);
        Assert.Equal("exact_unique", frozen.Resolve(Observed(hand, card, holder)).Status);
        string leafJson = JsonSerializer.Serialize(frame.Leaves[0]);
        Assert.DoesNotContain("NativeWitness", leafJson);
        Assert.DoesNotContain("Dispatch", leafJson);
        Assert.Equal("{}", JsonSerializer.Serialize(frame.Leaves[0].NativeWitness));
        string snapshotJson = JsonSerializer.Serialize(frozen.Snapshot);
        Assert.DoesNotContain("NativeWitness", snapshotJson);
        Assert.Equal(0, dispatches.Calls);
    }

    [Theory]
    [InlineData("cancel_card_play")]
    [InlineData("confirm_card")]
    public void HeldCardStageUsesExactCardPlayAndCardWithoutDispatch(string verb)
    {
        var dispatches = new DispatchCounter();
        object hand = new();
        object play = new();
        object card = new();
        object holder = new();
        TextMenuFrame baseFrame = Frame(hand, card, holder, dispatches);
        TextMenuFrame frame = baseFrame with
        {
            Leaves = new[] { baseFrame.Leaves[0] with
            {
                Key = verb, Verb = verb,
                NativeWitness = new(play, card,
                    new Dictionary<string, object>())
            } }
        };
        var frozen = new ProcessLocalTextMenuWitnessFrame(
            frame, Capabilities(), "source", false);

        Assert.Equal("exact_unique", frozen.Resolve(new(verb, play, card,
            new Dictionary<string, object>())).Status);
        Assert.Equal("zero", frozen.Resolve(new(verb, hand, card,
            new Dictionary<string, object>())).Status);
        Assert.Equal("zero", frozen.Resolve(new(verb, play, new object(),
            new Dictionary<string, object>())).Status);
        Assert.Equal("zero", frozen.Resolve(new(verb, play, card,
            new Dictionary<string, object> { ["holder"] = holder })).Status);
        Assert.Equal(0, dispatches.Calls);
    }

    [Fact]
    public void TargetConfirmationUsesExactCreatureNodeArgument()
    {
        var dispatches = new DispatchCounter();
        object hand = new();
        object play = new();
        object card = new();
        object holder = new();
        object targetNode = new();
        object targetEntity = new();
        TextMenuFrame baseFrame = Frame(hand, card, holder, dispatches);
        TextMenuFrame frame = baseFrame with
        {
            Leaves = new[] { baseFrame.Leaves[0] with
            {
                Key = "confirm-target", Verb = "confirm_target",
                NativeWitness = new(play, card,
                    new Dictionary<string, object> { ["target"] = targetNode })
            } }
        };
        var frozen = new ProcessLocalTextMenuWitnessFrame(
            frame, Capabilities(), "source", false);

        Assert.Equal("exact_unique", frozen.Resolve(new("confirm_target", play, card,
            new Dictionary<string, object> { ["target"] = targetNode })).Status);
        Assert.Equal("zero", frozen.Resolve(new("confirm_target", play, card,
            new Dictionary<string, object> { ["target"] = targetEntity })).Status);
        Assert.Equal("zero", frozen.Resolve(new("confirm_target", play, card,
            new Dictionary<string, object> { ["creature"] = targetNode })).Status);
        Assert.Equal("zero", frozen.Resolve(new("confirm_target", play, card,
            new Dictionary<string, object>())).Status);
        Assert.Equal(0, dispatches.Calls);
    }

    private static ProcessLocalObservedTextMenuAction Observed(
        object hand, object card, object holder) =>
        new("begin_card_play", hand, card,
            new Dictionary<string, object> { ["holder"] = holder });

    private static TextMenuNativeWitnessBinding Binding(
        object hand, object card, object holder) =>
        new(hand, card, new Dictionary<string, object> { ["holder"] = holder });

    private static PlayerEnvironmentReferent Referent(string id) =>
        new(id, "card", "card", "Same name",
            new(true, true, false, false, "synthetic-visible"), null, null);

    private sealed class DispatchCounter
    {
        internal int Calls;
        internal NativeInputResult Dispatch()
        {
            Calls++;
            return NativeInputResult.Delivered("fixture");
        }
    }

    private static TextMenuFrame Frame(
        object hand, object card, object holder, DispatchCounter dispatches)
    {
        var page = new PlayerEnvironmentSnapshot(
            "1.0.0", PlayerEnvironmentContract.SnapshotSchema,
            "native-snapshot", 1, DateTimeOffset.UnixEpoch, "interactive", null,
            new("native-page", "combat_turn", "ready", null, "synthetic",
                new(new JsonObject { ["kind"] = "combat_turn" }, new JsonObject()),
                Array.Empty<PlayerEnvironmentInteractionCapability>()),
            new[] { Referent("card") },
            new("bound-actions-1", "complete", 0, 0, 512, "native",
                Array.Empty<PlayerEnvironmentBoundAction>()),
            Array.Empty<PlayerEnvironmentReadOpportunity>(),
            new("complete", "complete", "complete", Array.Empty<string>(),
                Array.Empty<string>()),
            new("runtime", "environment"),
            new("player_visible_v1", "current_page", false, "omit"));
        return new(page, "native-owner", new[]
        {
            new TextMenuLeaf("begin", "root", "begin_card_play", "Begin Same name",
                "card", Array.Empty<PlayerEnvironmentBoundActionArgument>(),
                dispatches.Dispatch, Binding(hand, card, holder)),
            new TextMenuLeaf("end", "root", "end_turn", "End turn", null,
                Array.Empty<PlayerEnvironmentBoundActionArgument>(), dispatches.Dispatch),
            new TextMenuLeaf("tip", "relic_tips", "show_tip", "Show tip", null,
                Array.Empty<PlayerEnvironmentBoundActionArgument>(), dispatches.Dispatch)
        });
    }

    private static PlayerEnvironmentCapabilitiesResponse Capabilities() =>
        new(PlayerEnvironmentContract.ProtocolVersion, TextMenuContract.SnapshotSchema,
            PlayerEnvironmentContract.ActionSchema, TextMenuContract.ResultSchema,
            PlayerEnvironmentContract.ControlSchema, "implemented",
            new("host", "Host", "1", "runtime", "live_ui",
                new("source", "mvid", "sha")),
            new("game", "commit", "branch", 1,
                new("supported", true, "synthetic"),
                new("complete", "fingerprint", "test", Array.Empty<string>(), "synthetic")),
            "environment", Array.Empty<string>(), true, true, false,
            new(1000), Array.Empty<PlayerEnvironmentEvidenceProfile>(),
            Array.Empty<string>()) { InputProfile = TextMenuContract.Profile };
}
