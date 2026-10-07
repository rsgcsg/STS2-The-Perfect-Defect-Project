using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

public sealed class PublicInformationBindingsTests
{
    private static PlayerEnvironmentReferent Ref(string id, string role, string? label) =>
        new(id, role, "entity", label, new(true, true, false, false, "native_visible_fact"), null, null);

    private static PlayerEnvironmentSnapshot Page()
    {
        var context = JsonNode.Parse("""
        {"kind":"combat","player":{"player_entity_id":"player","hand":[
          {"entity_id":"card-a","name":"Strike","definition_id":"STRIKE","cost":"1","is_upgraded":false},
          {"entity_id":"card-b","name":"Strike","definition_id":"STRIKE","cost":"0","is_upgraded":true}],
          "statuses":[],"orbs":[{"entity_id":"orb","name":"Lightning","definition_id":"LIGHTNING",
            "passive_value":3,"evoke_value":8,"queue_index":0,"is_next_to_evoke":true}],"orb_slots":3},
          "enemies":[{"entity_id":"enemy","name":"Worm","statuses":[{"definition_id":"STRENGTH",
            "name":"Strength","amount":2,"type":"Buff","description":"unopened power body"}],
            "intents":[{"type":"Attack","label":"7","title":"unopened intent title","description":"unopened intent body"},
              {"type":"Defend","label":null}]}]}
        """)!;
        var hud = JsonNode.Parse("""
        {"scope":"active_single_player_run","run":{"act":1,"floor":2,"boss_icons":[{"slot":"primary","rendered_texture_path":"shown-icon"}]},
          "player":{"entity_id":"player","character_name":"Defect","gold":99,"hp":60,"max_hp":75,
            "relics":[{"entity_id":"relic","definition_id":"CORE","name":"Core","counter":2,
              "description":"unopened relic body","keywords":["unopened"],"card_previews":[]}],"potions":[]}}
        """)!;
        return new("1.0.0", PlayerEnvironmentContract.SnapshotSchema, "same-native-source", 1,
            DateTimeOffset.UnixEpoch, "interactive", new("sts2.player-environment/persistent/run-player-1", hud),
            new("interaction", "combat_turn", "ready", null, "sts2.player-environment/surface/combat_turn-1",
                new(new JsonObject { ["kind"] = "combat_turn" }, context), Array.Empty<PlayerEnvironmentInteractionCapability>()),
            new[] { Ref("card-a", "playable_card", "Strike"), Ref("card-b", "playable_card", "Strike"),
                Ref("player", "player", null), Ref("enemy", "enemy", "Worm"), Ref("orb", "orb", "Lightning") },
            new("sts2.player-environment/bound-actions-1", "complete", 0, 0, 512, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
            Array.Empty<PlayerEnvironmentReadOpportunity>(),
            new("complete", "public", "complete", Array.Empty<string>(), Array.Empty<string>()),
            new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));
    }
    private static TextMenuFrame Frame(PlayerEnvironmentSnapshot page, params NativeTextMenuInformationLeaf[] leaves) =>
        new(page, "owner", leaves.Select(leaf => new TextMenuLeaf(leaf.Key, leaf.Group, leaf.Verb,
            leaf.Label, leaf.SubjectReferentId, leaf.Arguments, leaf.Dispatch)).ToArray())
        { CardPlayCatalogComplete = true };
    private static TextMenuV2Snapshot Browse(TextMenuV2Session session, TextMenuFrame frame, string group)
    {
        var root = session.Observe(frame).Snapshot;
        var info = session.Apply(frame, root.SnapshotId,
            root.MenuActions.Actions.Single(action => action.Verb == "open_information").ActionId);
        return session.Apply(frame, info.SnapshotId,
            info.MenuActions.Actions.Single(action => action.Verb == "open_" + group).ActionId);
    }

    [Fact]
    public void DuplicateNamedCardsKeepTheirActualPublicInstanceFactsAndNativeClosures()
    {
        var bindings = new PublicInformationBindings(Page());
        var a = bindings.Card("card-a")!;
        var b = bindings.Card("card-b")!;
        Assert.Equal(a.Label, b.Label);
        Assert.Equal("1", a.Properties!["cost"]!.GetValue<string>());
        Assert.Equal("0", b.Properties!["cost"]!.GetValue<string>());
        int first = 0, second = 0;
        var frame = Frame(bindings.Page,
            PublicInformationBindings.Leaf("source-a", "card_tips", "show_card_tips", a,
                () => { first++; return NativeInputResult.Delivered("first exact holder"); }),
            PublicInformationBindings.Leaf("source-b", "card_tips", "show_card_tips", b,
                () => { second++; return NativeInputResult.Delivered("second exact holder"); }));
        var session = new TextMenuV2Session();
        var observed = Browse(session, frame, "card_tips");
        var choice = observed.MenuActions.Actions.Single(action => action.SubjectReferentId == "card-b");
        Assert.Equal("Show Strike tips", choice.Label);
        Assert.True(session.Observe(frame).Choices[choice.ActionId].Leaf!.Dispatch().Accepted);
        Assert.Equal(0, first);
        Assert.Equal(1, second);
        Assert.Equal(2, observed.MenuActions.Actions.Count(action => action.Verb == "show_card_tips"));
        // Same public names need not fabricate ordinal or opaque-ID features to differ in cost/upgrade.
        Assert.DoesNotContain("source-b", choice.Label);
    }

    [Fact]
    public void EveryTipFamilyPublishesVisibleSubjectsAndExactPublicOwnersWithoutUnopenedBodies()
    {
        var bindings = new PublicInformationBindings(Page());
        var declarations = new[]
        {
            ("card_tips", bindings.Card("card-a"), (string?)null),
            ("relic_tips", bindings.Relic("relic"), (string?)null),
            ("orb_tips", bindings.Orb("orb", "player"), "player"),
            ("orb_tips", bindings.EmptyOrb("empty-slot", "player", 2, 3), "player"),
            ("power_tips", bindings.Power("power", "enemy", "STRENGTH", 2, true), "enemy"),
            ("intent_tips", bindings.Intent("intent-a", "enemy", 0, 2, true), "enemy")
        };
        Assert.True(bindings.Complete);
        foreach (var (group, subject, owner) in declarations)
        {
            Assert.NotNull(subject);
            var leaf = PublicInformationBindings.Leaf("key-" + subject!.ReferentId, group, "show_" + group,
                subject, () => NativeInputResult.Delivered("same typed native source"), owner,
                owner == null ? null : bindings.OwnerLabel(owner));
            Assert.Contains(bindings.Page.Referents, value => value.ReferentId == leaf.SubjectReferentId && value.State.Visible);
            Assert.All(leaf.Arguments, argument => Assert.Contains(bindings.Page.Referents,
                value => value.ReferentId == argument.ReferentId && value.State.Visible));
            if (owner != null) Assert.Equal(owner, subject.Properties!["owner_referent_id"]!.GetValue<string>());
        }
        var json = JsonSerializer.Serialize(bindings.Page.Referents, new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower });
        Assert.DoesNotContain("unopened", json);
        Assert.Contains("owner_referent_id", json);
        Assert.Contains("passive_value", json);
        Assert.Equal("Attack 7", bindings.Page.Referents.Single(value => value.ReferentId == "intent-a").Label);
    }

    [Theory]
    [InlineData("card")]
    [InlineData("power-owner")]
    [InlineData("power-membership")]
    [InlineData("power-amount")]
    [InlineData("intent-owner")]
    [InlineData("intent-membership")]
    [InlineData("intent-count")]
    [InlineData("orb-membership")]
    [InlineData("slot-count")]
    public void UnresolvedRequiredMappingsMakeTheWholeMenuUnavailable(string failure)
    {
        var bindings = new PublicInformationBindings(Page());
        var invalid = failure switch
        {
            "card" => bindings.Card("not-current"),
            "power-owner" => bindings.Power("power", "not-current", "STRENGTH", 2, true),
            "power-membership" => bindings.Power("power", "enemy", "STRENGTH", 2, false),
            "power-amount" => bindings.Power("power", "enemy", "STRENGTH", 3, true),
            "intent-owner" => bindings.Intent("intent", "not-current", 0, 2, true),
            "intent-membership" => bindings.Intent("intent", "enemy", 0, 2, false),
            "intent-count" => bindings.Intent("intent", "enemy", 0, 3, true),
            "orb-membership" => bindings.Orb("not-current", "player"),
            _ => bindings.EmptyOrb("slot", "player", 2, 4)
        };
        Assert.Null(invalid);
        Assert.Equal("partial", bindings.Page.Completeness.Status);
        Assert.Contains(bindings.Page.Completeness.Missing, value => value.StartsWith("public_information_binding_"));
        var observed = new TextMenuV2Session().Observe(Frame(bindings.Page)).Snapshot;
        Assert.Equal("unavailable", observed.MenuActions.Status);
        Assert.Empty(observed.MenuActions.Actions);
    }

    [Fact]
    public void TopbarSubjectsHaveDistinctSemanticRolesAndOnlyFrozenShownHudFacts()
    {
        var bindings = new PublicInformationBindings(Page());
        foreach (string role in new[] { "deck", "map", "floor", "boss", "gold", "hp" })
        {
            var subject = bindings.Topbar("control-" + role, role)!;
            Assert.Equal("control", subject.Kind);
            Assert.Equal("topbar_" + role, subject.Role);
            Assert.Equal(role, subject.Properties!["control_role"]!.GetValue<string>());
            Assert.NotEqual(role, subject.Label); // Public readable role label, not opaque control ID.
            Assert.DoesNotContain("control-", subject.Label!);
        }
        Assert.Equal(60, bindings.Page.Referents.Single(value => value.ReferentId == "control-hp")
            .Properties!["hp"]!.GetValue<int>());
        Assert.Null(bindings.Page.Referents.Single(value => value.ReferentId == "control-boss").Properties!["bosses"]);
        Assert.True(bindings.Complete);
        Assert.Null(bindings.Topbar("bad", "hidden_global"));
        Assert.False(bindings.Complete);
    }

    [Fact]
    public void RewardMergeIncludesOnlyAppendedInformationSubjectsAndOwners()
    {
        var bindings = new PublicInformationBindings(Page());
        var gold = bindings.Topbar("topbar-gold", "gold")!;
        var leaf = PublicInformationBindings.Leaf("source", "topbar_tips", "show_topbar_tips", gold,
            () => NativeInputResult.Delivered("native hover"));
        var target = Page() with { Referents = Array.Empty<PlayerEnvironmentReferent>() };
        var merged = PublicInformationBindings.MergeRequired(target, bindings.Page, new[] { leaf });
        Assert.Single(merged.Referents);
        Assert.Equal("topbar-gold", merged.Referents[0].ReferentId);
        Assert.DoesNotContain(merged.Referents, value => value.Role == "enemy" || value.Role.Contains("card"));
    }

    [Fact]
    public void MissingRewardMergeOwnerIsExplicitlyPartial()
    {
        var bindings = new PublicInformationBindings(Page());
        var subject = bindings.Topbar("topbar-gold", "gold")!;
        var leaf = PublicInformationBindings.Leaf("source", "topbar_tips", "show_topbar_tips", subject,
            () => NativeInputResult.Delivered("native hover"), "missing-owner");
        var target = Page() with { Referents = Array.Empty<PlayerEnvironmentReferent>() };
        var merged = PublicInformationBindings.MergeRequired(target, bindings.Page, new[] { leaf });
        Assert.Equal("partial", merged.Completeness.Status);
        Assert.Contains("public_information_binding_reward_merge", merged.Completeness.Missing);
    }

    [Fact]
    public void InformationLabelChangeInvalidatesBasisEvenWhenNativeTokenAndSubjectsAreUnchanged()
    {
        var bindings = new PublicInformationBindings(Page());
        var subject = bindings.Card("card-a")!;
        var leaf = PublicInformationBindings.Leaf("same-key", "card_tips", "show_card_tips", subject,
            () => NativeInputResult.Delivered("exact"));
        var frame = Frame(bindings.Page, leaf);
        var session = new TextMenuV2Session();
        var old = Browse(session, frame, "card_tips");
        var changed = frame with { Leaves = frame.Leaves.Select(value => value with { Label = "Updated shown descriptor" }).ToArray() };
        var current = session.Observe(changed).Snapshot;
        Assert.NotEqual(old.SnapshotId, current.SnapshotId);
        Assert.Equal("root", current.Menu.Cursor);
        Assert.Throws<InvalidOperationException>(() => session.Apply(changed, old.SnapshotId,
            old.MenuActions.Actions.Single(value => value.Verb == "back").ActionId));
    }
}
