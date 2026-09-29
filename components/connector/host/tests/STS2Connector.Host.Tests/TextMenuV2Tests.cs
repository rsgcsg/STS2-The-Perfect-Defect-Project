using System.Collections.Concurrent;
using System.Text.Json.Nodes;
using STS2Connector.Authority;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

public sealed class TextMenuV2Tests
{
    [Fact]
    public void NativeSemanticCatalogMustCoverEveryExactCardAndTarget()
    {
        TextMenuFrame frame = Frame(targeted: true) with
        {
            Page = Frame(targeted: true).Page with
            {
                Referents = Frame(targeted: true).Page.Referents.Concat(new[]
                {
                    new PlayerEnvironmentReferent("card-b", "card", "entity", "Defend",
                        new(true, true, false, false, "native_visible_fact"), null, null)
                }).ToArray()
            }
        };
        var combat = new CombatTurnSurface("combat_turn", "room", true)
        {
            PlayableCards = new[]
            {
                new VisibleCombatCommandOption("card-a", "Strike", new[] { "enemy-a" }),
                new VisibleCombatCommandOption("card-b", "Defend", Array.Empty<string>())
            }
        };
        TextMenuFrame Capture(params NativeUiBoundAction[] bindings) =>
            PlayerEnvironmentService.AttachSemanticCardPlaysFromBindings(
                frame with { CardPlays = Array.Empty<TextMenuLeaf>(), CardPlayCatalogComplete = false },
                combat, bindings, "combat-interaction", _ => NativeInputResult.Delivered("test"));

        NativeUiBoundAction cardA = Binding("card-a", "enemy-a");
        NativeUiBoundAction cardB = Binding("card-b");
        TextMenuFrame exact = Capture(cardA, cardB);
        Assert.True(exact.CardPlayCatalogComplete);
        Assert.Equal(2, exact.CardPlays.Count);
        Assert.False(Capture(cardA).CardPlayCatalogComplete);
        Assert.False(Capture(cardA, cardA).CardPlayCatalogComplete);
        Assert.False(Capture(Binding("card-a", "enemy-a", "unobserved"), cardB)
            .CardPlayCatalogComplete);
        Assert.False(Capture(Binding("card-a"), cardB).CardPlayCatalogComplete);
    }

    [Fact]
    public void TargetedCardStagesOnlyTextBeforeOneExactNativePlay()
    {
        int calls = 0;
        TextMenuFrame frame = Frame(targeted: true, () =>
        {
            calls++;
            return NativeInputResult.Delivered("native-game-play");
        });
        var executor = Executor(() => frame);
        TextMenuV2Snapshot root = executor.Observe();
        Assert.Equal(TextMenuV2Contract.Profile, root.InputProfile);
        Assert.Equal(new[] { "end_turn", "select_card", "open_information" },
            root.MenuActions.Actions.Select(action => action.Verb));
        Assert.DoesNotContain(root.MenuActions.Actions, action => action.Verb == "begin_card_play");

        var card = executor.Submit(Request(root, "select_card", "card"));
        Assert.Equal("applied", card.Status);
        Assert.Equal("text_menu", card.EffectDomain);
        Assert.Null(card.NativeDelivery);
        Assert.Equal("card_targets", card.Successor!.Menu.Cursor);
        Assert.Equal(new[] { "card" }, card.Successor.Menu.Selection.Select(item => item.Role));
        Assert.Equal(root.Menu.NativeSnapshotId, card.Successor.Menu.NativeSnapshotId);
        Assert.Equal(0, calls);
        TextMenuV2Snapshot repeated = executor.Observe();
        Assert.Equal(card.Successor.SnapshotId, repeated.SnapshotId);
        Assert.Equal(card.Successor.Menu.Selection, repeated.Menu.Selection);

        var target = executor.Submit(Request(card.Successor, "select_target", "target"));
        Assert.Equal("card_confirmation", target.Successor!.Menu.Cursor);
        Assert.Equal(new[] { "card", "target" },
            target.Successor.Menu.Selection.Select(item => item.Role));
        Assert.Equal(0, calls);
        TextMenuAction play = target.Successor.MenuActions.Actions.Single(action => action.Verb == "play");
        Assert.Equal("card-a", play.SubjectReferentId);
        Assert.Equal("enemy-a", Assert.Single(play.Arguments).ReferentId);

        var result = executor.Submit(Request(target.Successor, "play", "play"));
        Assert.Equal("applied", result.Status);
        Assert.Equal("delivered", result.NativeDelivery);
        Assert.Equal(1, calls);
        Assert.Same(result, executor.Submit(Request(target.Successor, "play", "play")));
        Assert.Equal(1, calls);
    }

    [Fact]
    public void CardOnlyConfirmationAndCancelNeverInventATargetOrNativeDelivery()
    {
        int calls = 0;
        var frame = Frame(targeted: false, () =>
        {
            calls++;
            return NativeInputResult.Delivered("native-game-play");
        });
        var executor = Executor(() => frame);
        var card = executor.Submit(Request(executor.Observe(), "select_card", "card"));
        Assert.Equal("card_confirmation", card.Successor!.Menu.Cursor);
        TextMenuAction play = card.Successor.MenuActions.Actions.Single(action => action.Verb == "play");
        Assert.Empty(play.Arguments);
        var cancel = executor.Submit(Request(card.Successor, "cancel_selection", "cancel"));
        Assert.Equal("root", cancel.Successor!.Menu.Cursor);
        Assert.Empty(cancel.Successor.Menu.Selection);
        Assert.Null(cancel.NativeDelivery);
        Assert.Equal(0, calls);
    }

    [Fact]
    public void SourceChangeAndControllerReleaseClearSelectionAndRejectOldBinding()
    {
        int calls = 0;
        string? controller = "client:1";
        TextMenuFrame frame = Frame(targeted: true, () =>
        {
            calls++;
            return NativeInputResult.Delivered("native-game-play");
        });
        var executor = Executor(() => frame, () => controller);
        var card = executor.Submit(Request(executor.Observe(), "select_card", "card"));
        var oldTarget = Request(card.Successor!, "select_target", "old-target");
        frame = frame with { Page = frame.Page with { SnapshotId = "new-native-source" } };
        Assert.Equal("root", executor.Observe().Menu.Cursor);
        Assert.Equal("stale_snapshot", executor.Submit(oldTarget).ReasonCode);
        Assert.Equal(0, calls);

        var next = executor.Submit(Request(executor.Observe(), "select_card", "next-card"));
        Assert.Equal("card_targets", next.Successor!.Menu.Cursor);
        controller = null;
        Assert.Equal("root", executor.Observe().Menu.Cursor);
        Assert.Equal("stale_snapshot", executor.Submit(
            Request(next.Successor, "select_target", "released")).ReasonCode);
        Assert.Equal(0, calls);
    }

    [Fact]
    public void PrivatePairChangeWithReusedNativePageTokenInvalidatesSelection()
    {
        int calls = 0;
        TextMenuFrame frame = Frame(targeted: true, () =>
        {
            calls++;
            return NativeInputResult.Delivered("native-game-play");
        });
        var executor = Executor(() => frame);
        var card = executor.Submit(Request(executor.Observe(), "select_card", "card"));
        var target = executor.Submit(Request(card.Successor!, "select_target", "target"));
        var oldPlay = Request(target.Successor!, "play", "stale-play");
        frame = frame with { CardPlays = frame.CardPlays.Select(leaf =>
            leaf with { Key = "changed-private-binding" }).ToArray() };
        Assert.Equal("root", executor.Observe().Menu.Cursor);
        Assert.Equal("stale_snapshot", executor.Submit(oldPlay).ReasonCode);
        Assert.Equal(0, calls);
    }

    [Fact]
    public void ControllerRejectionCannotChangeSelectionOrDispatch()
    {
        TextMenuFrame frame = Frame(targeted: true);
        var executor = new TextMenuV2Executor(new object(), new(), () => frame,
            _ => MutationAdmission.Reject("controller_lease_stale", "stale"));
        var root = executor.Observe();
        var result = executor.Submit(Request(root, "select_card", "rejected"));
        Assert.Equal("controller_lease_stale", result.ReasonCode);
        Assert.Equal("root", executor.Observe().Menu.Cursor);
        Assert.Equal(root.SnapshotId, executor.Observe().SnapshotId);
    }

    [Fact]
    public void IncompleteCatalogAndUnknownNativeDeliveryNeverClaimPlaySuccess()
    {
        TextMenuFrame frame = Frame(targeted: true);
        var executor = Executor(() => frame);
        var card = executor.Submit(Request(executor.Observe(), "select_card", "card"));
        frame = frame with { CardPlayCatalogComplete = false };
        TextMenuV2Snapshot unavailable = executor.Observe();
        Assert.Equal("visible_unsupported", unavailable.Status);
        Assert.Empty(unavailable.MenuActions.Actions);
        Assert.Equal("stale_snapshot", executor.Submit(
            Request(card.Successor!, "select_target", "stale")).ReasonCode);

        int calls = 0;
        frame = Frame(targeted: false, () =>
        {
            calls++;
            throw new InvalidOperationException("native delivery uncertain");
        });
        var unknownExecutor = Executor(() => frame);
        var selected = unknownExecutor.Submit(Request(unknownExecutor.Observe(), "select_card", "select"));
        var request = Request(selected.Successor!, "play", "unknown");
        var unknown = unknownExecutor.Submit(request);
        Assert.Equal("unknown", unknown.Status);
        Assert.Equal("unknown", unknown.NativeDelivery);
        Assert.Equal("never", unknown.Retry);
        Assert.Null(unknown.Successor);
        Assert.Same(unknown, unknownExecutor.Submit(request));
        Assert.Equal(1, calls);
    }

    private static TextMenuV2Executor Executor(Func<TextMenuFrame> capture,
        Func<string?>? controller = null) => new(new object(), new ConcurrentDictionary<string, string>(),
            capture, _ => MutationAdmission.Allow(new MutationAttribution(
                "runtime", "client", "instance", "test", "Test", "1", "lease", 1)), controller);

    private static PlayerEnvironmentActionRequest Request(
        TextMenuV2Snapshot snapshot, string verb, string requestId) => new(
            requestId, snapshot.SnapshotId,
            snapshot.MenuActions.Actions.Single(action => action.Verb == verb).ActionId,
            "client", "lease", 1, TextMenuV2Contract.Profile);

    private static TextMenuFrame Frame(bool targeted, Func<NativeInputResult>? dispatch = null)
    {
        dispatch ??= () => NativeInputResult.Delivered("fixture");
        var card = new PlayerEnvironmentReferent("card-a", "card", "entity", "Strike",
            new(true, true, false, false, "native_visible_fact"), null, null);
        var enemy = new PlayerEnvironmentReferent("enemy-a", "enemy", "entity", "Jaw Worm",
            new(true, true, false, false, "native_visible_fact"), null, null);
        var page = new PlayerEnvironmentSnapshot("1.0.0", PlayerEnvironmentContract.SnapshotSchema,
            "native-source", 1, DateTimeOffset.UnixEpoch, "interactive", null,
            new("combat", "combat_turn", "ready", null,
                "sts2.player-environment/surface/combat_turn-1",
                new(new JsonObject { ["kind"] = "combat_turn" }, new JsonObject { ["kind"] = "combat" }),
                Array.Empty<PlayerEnvironmentInteractionCapability>()),
            new[] { card, enemy }, new("sts2.player-environment/bound-actions-1",
                "complete", 0, 0, 512, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
            Array.Empty<PlayerEnvironmentReadOpportunity>(),
            new("complete", "complete", "complete", Array.Empty<string>(), Array.Empty<string>()),
            new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));
        var root = new[]
        {
            new TextMenuLeaf("end", "root", "end_turn", "End turn", null,
                Array.Empty<PlayerEnvironmentBoundActionArgument>(), dispatch),
            new TextMenuLeaf("tip", "relic_tips", "show_relic_tips", "Tip", null,
                Array.Empty<PlayerEnvironmentBoundActionArgument>(), dispatch),
            new TextMenuLeaf("begin", "root", "begin_card_play", "Begin Strike", "card-a",
                Array.Empty<PlayerEnvironmentBoundActionArgument>(), dispatch)
        };
        var pair = new TextMenuLeaf("exact-play", "root", "play", "Play Strike", "card-a",
            targeted ? new[] { new PlayerEnvironmentBoundActionArgument("target", "enemy-a") }
                : Array.Empty<PlayerEnvironmentBoundActionArgument>(), dispatch);
        return new TextMenuFrame(page, "native-combat-owner", root)
        { CardPlays = new[] { pair }, CardPlayCatalogComplete = true };
    }

    private static NativeUiBoundAction Binding(string cardId, params string[] targetIds) => new(
        new NativeUiActionCandidate("play:" + cardId, "play_card", "play_card", "Play",
            new Dictionary<string, string> { ["card_id"] = cardId },
            targetIds.Length == 0
                ? new Dictionary<string, NativeUiOperandDomain>()
                : new Dictionary<string, NativeUiOperandDomain>
                {
                    ["target_id"] = new("entity_ids", targetIds)
                },
            new[] { new ActionEntityBinding("card", cardId) }.Concat(
                targetIds.Select(id => new ActionEntityBinding("target", id))).ToArray(),
            "native_ui_binding"));
}
