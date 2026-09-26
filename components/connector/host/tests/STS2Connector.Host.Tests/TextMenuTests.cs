using System.Collections.Concurrent;
using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.Authority;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

public sealed class TextMenuTests
{
    [Fact]
    public void InformationNavigationChangesOnlyCursorAndDoesNotDeliverOrReplayNativeInput()
    {
        int nativeCalls = 0;
        var frame = Frame(() => { nativeCalls++; return NativeInputResult.Delivered("fixture"); });
        var executor = Executor(() => frame);
        var root = executor.Observe();
        Assert.Equal(new[] { "select", "open_information" }, root.MenuActions.Actions.Select(a => a.Verb));
        var enter = Request(root, "open_information", "enter");
        var result = executor.Submit(enter);
        Assert.Equal("applied", result.Status);
        Assert.Equal("text_menu", result.EffectDomain);
        Assert.Null(result.NativeDelivery);
        Assert.Equal("information", result.Successor!.Menu.Cursor);
        Assert.Equal(root.Menu.NativeSnapshotId, result.Successor.Menu.NativeSnapshotId);
        Assert.NotEqual(root.SnapshotId, result.Successor.SnapshotId);
        Assert.Same(root.Persistent, result.Successor.Persistent);
        Assert.Same(root.Interaction.Content, result.Successor.Interaction.Content);
        Assert.Equal(0, nativeCalls);
        Assert.Same(result, executor.Submit(enter));
        Assert.Same(result, executor.Find("enter"));
        for (int i = 0; i < 3; i++)
        {
            Assert.Equal(result.Successor.SnapshotId, executor.Observe().SnapshotId);
            Assert.Equal(result.Successor.Sequence, executor.Observe().Sequence);
        }
        Assert.Equal(0, nativeCalls);
        var list = executor.Submit(Request(executor.Observe(), "open_relic_tips", "list"));
        Assert.Equal(new[] { "show_tip", "back" }, list.Successor!.MenuActions.Actions.Select(a => a.Verb));
        var tip = executor.Submit(Request(executor.Observe(), "show_tip", "tip"));
        Assert.Equal("native_input", tip.EffectDomain);
        Assert.Equal("delivered", tip.NativeDelivery);
        Assert.Equal(1, nativeCalls);
        var back = executor.Submit(Request(executor.Observe(), "back", "back"));
        Assert.Equal("information", back.Successor!.Menu.Cursor);
        Assert.Equal(1, nativeCalls);
    }

    [Theory]
    [InlineData("map_navigation")]
    [InlineData("combat_turn")]
    [InlineData("reward_claim")]
    [InlineData("card_reward_selection")]
    [InlineData("event_option")]
    [InlineData("shop_inventory")]
    [InlineData("rest_site")]
    [InlineData("treasure_room")]
    [InlineData("combat_hand_selection")]
    public void PresentationDoesNotInventHandShopOrSelectorMenus(string kind)
    {
        var frame = Frame();
        frame = frame with { Page = frame.Page with { Interaction = frame.Page.Interaction with { Kind = kind } } };
        var projection = new TextMenuSession().Observe(frame);
        Assert.Equal(new[] { "select", "open_information" }, projection.Snapshot.MenuActions.Actions.Select(a => a.Verb));
        Assert.Equal("root", projection.Snapshot.Menu.Cursor);
        Assert.Equal("complete", projection.Snapshot.MenuActions.Status);
    }

    [Fact]
    public void OwnerChangeResetsCursorAndRejectsPriorNativeAndNavigationBindings()
    {
        int calls = 0;
        var frame = Frame(() => { calls++; return NativeInputResult.Delivered("test"); });
        var executor = Executor(() => frame);
        var old = executor.Observe();
        var oldRequest = Request(old, "select", "old-leaf");
        executor.Submit(Request(old, "open_information", "enter"));
        var oldBack = Request(executor.Observe(), "back", "old-back");
        frame = frame with { OwnerKey = "next-page" };
        Assert.Equal("root", executor.Observe().Menu.Cursor);
        Assert.Equal("stale_snapshot", executor.Submit(oldRequest).ReasonCode);
        Assert.Equal("stale_snapshot", executor.Submit(oldBack).ReasonCode);
        Assert.Equal(0, calls);
    }

    [Fact]
    public void PublicContentAndNativeBindingChangesInvalidateEvenAccidentallyReusedPageToken()
    {
        var frame = Frame();
        var executor = Executor(() => frame);
        var old = executor.Observe();
        frame = frame with { Page = frame.Page with { Persistent = new("facts", new JsonObject { ["hp"] = 8 }) } };
        Assert.NotEqual(old.SnapshotId, executor.Observe().SnapshotId);
        var second = executor.Observe();
        frame = frame with { Leaves = frame.Leaves.Select(l => l with { Key = l.Key + "-new" }).ToArray() };
        Assert.NotEqual(second.SnapshotId, executor.Observe().SnapshotId);
    }

    [Theory]
    [InlineData("settling", "complete")]
    [InlineData("interactive", "incomplete")]
    [InlineData("visible_unsupported", "complete")]
    public void UnavailablePageNeverAuthorizesEvenSystemNavigation(string status, string completeness)
    {
        var frame = Frame();
        var executor = Executor(() => frame);
        var old = executor.Observe();
        frame = frame with { Page = frame.Page with { Status = status,
            Completeness = frame.Page.Completeness with { Status = completeness } } };
        var current = executor.Observe();
        Assert.Empty(current.MenuActions.Actions);
        Assert.Equal("unavailable", current.MenuActions.Status);
        Assert.Equal("stale_snapshot", executor.Submit(Request(old, "open_information", "stale")).ReasonCode);
    }

    [Fact]
    public void ControllerRejectionCannotNavigateOrDispatch()
    {
        int calls = 0;
        var frame = Frame(() => { calls++; return NativeInputResult.Delivered("test"); });
        var executor = Executor(() => frame, _ => MutationAdmission.Reject("controller_lease_stale", "stale"));
        var root = executor.Observe();
        foreach (string verb in new[] { "select", "open_information" })
        {
            Assert.Equal("controller_lease_stale", executor.Submit(Request(root, verb, verb)).ReasonCode);
            Assert.Equal(root.SnapshotId, executor.Observe().SnapshotId);
        }
        Assert.Equal(0, calls);
    }

    [Fact]
    public void LeaseEndOrHandoffResetsPresentationWithoutDeliveringAnything()
    {
        string? controller = "client:1";
        var frame = Frame();
        var executor = new TextMenuExecutor(new object(), new(), () => frame,
            _ => MutationAdmission.Allow(new MutationAttribution(
                "runtime", "client", "instance", "test", "Test", "1", "lease", 1)),
            () => controller);
        executor.Submit(Request(executor.Observe(), "open_information", "enter"));
        Assert.Equal("information", executor.Observe().Menu.Cursor);
        var oldBack = Request(executor.Observe(), "back", "stale-back");
        controller = null;
        Assert.Equal("root", executor.Observe().Menu.Cursor);
        Assert.Equal("stale_snapshot", executor.Submit(oldBack).ReasonCode);
    }

    [Fact]
    public void EmptyCurrentNativeCatalogDoesNotClaimInteractiveOrInheritCapabilities()
    {
        var frame = Frame() with { Leaves = Array.Empty<TextMenuLeaf>() };
        var snapshot = new TextMenuSession().Observe(frame).Snapshot;
        Assert.Equal("visible_unsupported", snapshot.Status);
        Assert.Equal("unavailable", snapshot.MenuActions.Status);
        Assert.Empty(snapshot.Interaction.Capabilities);
    }

    [Fact]
    public void UnifiedRequestNamespaceRejectsLegacyOrOtherActionReuse()
    {
        var ledger = new ConcurrentDictionary<string, string>();
        var frame = Frame();
        var executor = Executor(() => frame, ledger: ledger);
        var request = Request(executor.Observe(), "open_information", "same");
        ledger["same"] = PlayerEnvironmentService.ActionRequestFingerprint(request with { InputProfile = null });
        Assert.Equal("request_id_conflict", executor.Submit(request).ReasonCode);
        Assert.Equal("root", executor.Observe().Menu.Cursor);
        var result = executor.Submit(request with { RequestId = "new" });
        Assert.Equal("applied", result.Status);
        Assert.Equal("request_id_conflict", executor.Submit(request with
            { RequestId = "new", BoundActionId = "different" }).ReasonCode);
    }

    [Fact]
    public void UnknownNativeInputCannotRetryOrBecomeSuccessfulNavigation()
    {
        int calls = 0;
        var frame = Frame(() => { calls++; throw new InvalidOperationException("may have delivered"); });
        var executor = Executor(() => frame);
        var request = Request(executor.Observe(), "select", "unknown");
        var result = executor.Submit(request);
        Assert.Equal("unknown", result.Status);
        Assert.Equal("unknown", result.NativeDelivery);
        Assert.Equal("never", result.Retry);
        Assert.Null(result.Successor);
        Assert.Same(result, executor.Submit(request));
        Assert.Equal(1, calls);
    }

    [Fact]
    public void NativeRejectionAndPostDeliveryReadFailureKeepDistinctTruth()
    {
        bool readFails = false;
        var frame = Frame(() => NativeInputResult.Rejected("not_ready", "native guard"));
        var executor = Executor(() => readFails ? throw new InvalidOperationException("read") : frame);
        var rejected = executor.Submit(Request(executor.Observe(), "select", "reject"));
        Assert.Equal("not_applied", rejected.Status);
        Assert.Equal("not_delivered", rejected.NativeDelivery);
        frame = Frame(() => { readFails = true; return NativeInputResult.Delivered("fixture-native-handler"); });
        var delivered = executor.Submit(Request(executor.Observe(), "select", "delivery"));
        Assert.Equal("applied", delivered.Status);
        Assert.Equal("delivered", delivered.NativeDelivery);
        Assert.Equal("successor_observation_unavailable", delivered.ReasonCode);
        Assert.Null(delivered.Successor);
    }

    [Fact]
    public void DuplicateOrHiddenBindingsAndUnknownGroupsAreRejected()
    {
        var frame = Frame();
        Assert.Throws<InvalidOperationException>(() => new TextMenuSession().Observe(frame with
            { Leaves = new[] { frame.Leaves[0], frame.Leaves[0] } }));
        Assert.Throws<InvalidOperationException>(() => new TextMenuSession().Observe(frame with
            { Leaves = new[] { frame.Leaves[0] with { SubjectReferentId = "hidden" } } }));
        Assert.Throws<InvalidOperationException>(() => new TextMenuSession().Observe(frame with
            { Leaves = new[] { frame.Leaves[0] with { Group = "shop_categories" } } }));
    }

    [Fact]
    public void JsonKeepsExplicitNoNativeDeliveryAndNeverExportsPrivateDispatchOrLegacyCatalog()
    {
        var frame = Frame();
        var executor = Executor(() => frame);
        var result = executor.Submit(Request(executor.Observe(), "open_information", "json"));
        var json = JsonNode.Parse(JsonSerializer.Serialize(result, ConnectorMod._jsonOptions))!;
        Assert.True(json.AsObject().ContainsKey("native_delivery"));
        Assert.Null(json["native_delivery"]);
        Assert.Null(json["successor"]!["bound_actions"]);
        Assert.Null(json["successor"]!["reads"]);
        Assert.Equal(TextMenuContract.ResultSchema, json["schema"]!.GetValue<string>());
        Assert.DoesNotContain("dispatch", json.ToJsonString().ToLowerInvariant());
    }

    private static TextMenuExecutor Executor(Func<TextMenuFrame> capture,
        Func<MutationAuthorizationRequest, MutationAdmission>? authorize = null,
        ConcurrentDictionary<string, string>? ledger = null) => new(new object(),
            ledger ?? new ConcurrentDictionary<string, string>(), capture,
            authorize ?? (_ => MutationAdmission.Allow(new MutationAttribution(
                "runtime", "client", "client-instance", "test", "Test", "1", "lease", 1))));

    private static PlayerEnvironmentActionRequest Request(TextMenuSnapshot snapshot, string verb, string id) =>
        new(id, snapshot.SnapshotId, snapshot.MenuActions.Actions.Single(a => a.Verb == verb).ActionId,
            "client", "lease", 1, TextMenuContract.Profile);

    private static TextMenuFrame Frame(Func<NativeInputResult>? dispatch = null)
    {
        dispatch ??= () => NativeInputResult.Delivered("synthetic");
        var referent = new PlayerEnvironmentReferent("public-card", "card", "card", "Example card",
            new(true, true, false, false, "synthetic-visible"), null, null);
        var page = new PlayerEnvironmentSnapshot("1.0.0", PlayerEnvironmentContract.SnapshotSchema,
            "native-snapshot", 1, DateTimeOffset.UnixEpoch, "interactive", null,
            new("native-page", "combat_turn", "ready", null, "synthetic",
                new(new JsonObject { ["kind"] = "combat_turn" }, new JsonObject()),
                Array.Empty<PlayerEnvironmentInteractionCapability>()),
            new[] { referent }, new("bound-actions-1", "complete", 0, 0, 512, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
            Array.Empty<PlayerEnvironmentReadOpportunity>(), new("complete", "complete", "complete", Array.Empty<string>(), Array.Empty<string>()),
            new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));
        return new(page, "native-owner", new[]
        {
            new TextMenuLeaf("card", "root", "select", "Select Example card", "public-card",
                Array.Empty<PlayerEnvironmentBoundActionArgument>(), dispatch),
            new TextMenuLeaf("tip", "relic_tips", "show_tip", "Show visible relic tip", null,
                Array.Empty<PlayerEnvironmentBoundActionArgument>(), dispatch)
        });
    }
}
