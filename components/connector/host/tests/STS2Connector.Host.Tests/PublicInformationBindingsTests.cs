using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

public sealed class PublicInformationBindingsTests
{
    [Theory]
    [InlineData("held")]
    [InlineData("no-current-card-play")]
    public void NativeRenderedCardSubjectIsBoundBeforeRequiredInformation(string phase)
    {
        var page = Page() with
        {
            Referents = Page().Referents.Where(value =>
                !value.Role.Contains("card", StringComparison.Ordinal)).ToArray(),
            Interaction = Page().Interaction with { Kind = phase == "held"
                ? "combat_card_operation" : "combat_turn", Stage = phase }
        };
        var before = JsonSerializer.Serialize(page);
        var bindings = new PublicInformationBindings(page);
        int renderedReads = 0;
        var subject = NativeTextMenuInformation.BindCardTipSubject(bindings, "rendered-held", true, () =>
        {
            renderedReads++;
            return NativeLogicalPresentation.RenderedCardSubject("rendered-held", "Defend",
                "1", "Gain 5 Block.", facts => facts["displayed_star_cost"] = "0");
        });
        Assert.NotNull(subject);
        Assert.Equal(1, renderedReads);
        Assert.Equal("card", subject!.Role);
        Assert.Equal("Defend", subject.Label);
        Assert.Equal("1", subject.Properties!["displayed_cost"]!.GetValue<string>());
        Assert.Equal("0", subject.Properties["displayed_star_cost"]!.GetValue<string>());
        Assert.Equal(phase, bindings.Page.Interaction.Stage);
        Assert.True(bindings.Complete);
        Assert.Null(NativeTextMenuFrameBuilder.CloseIncompleteInformationBindings(bindings.Page, "owner"));
        var leaf = PublicInformationBindings.Leaf("exact-rendered-holder", "card_tips", "show_card_tips",
            subject, () => NativeInputResult.Delivered("exact source fixture"));
        var observed = Browse(new TextMenuV2Session(), Frame(bindings.Page, leaf), "card_tips");
        Assert.Equal("rendered-held", observed.MenuActions.Actions.Single(value =>
            value.Verb == "show_card_tips").SubjectReferentId);
        Assert.Equal(before, JsonSerializer.Serialize(page));
    }

    [Fact]
    public void LegacyCardTipBindingNeverReadsOrAddsRenderedSubjects()
    {
        var bindings = new PublicInformationBindings(Page());
        Assert.Null(NativeTextMenuInformation.BindCardTipSubject(bindings, "not-current", false,
            () => throw new InvalidOperationException("Legacy must not read rendered subjects")));
        Assert.Contains("public_information_binding_card_subject", bindings.Page.Completeness.Missing);
        Assert.DoesNotContain(bindings.Page.Referents, value => value.ReferentId == "not-current");
        var legacy = new PublicInformationBindings(Page());
        Assert.NotNull(NativeTextMenuInformation.BindCardTipSubject(legacy, "card-a", false,
            () => throw new InvalidOperationException("Legacy existing subject must not read")));
        Assert.True(legacy.Complete);
    }

    [Theory]
    [InlineData("null")]
    [InlineData("unreadable")]
    [InlineData("foreign-id")]
    [InlineData("foreign-facts-id")]
    [InlineData("wrong-role")]
    [InlineData("wrong-kind")]
    [InlineData("invisible")]
    [InlineData("wrong-schema")]
    [InlineData("empty-label")]
    [InlineData("wrong-title")]
    [InlineData("missing-cost")]
    [InlineData("missing-description")]
    public void ActiveRenderedCardWithMissingOrForeignFactsRemainsPartial(string failure)
    {
        var bindings = new PublicInformationBindings(Page());
        var supplied = NativeLogicalPresentation.RenderedCardSubject("rendered-current", "Defend", "1", "Block");
        if (failure == "foreign-id") supplied = supplied with { ReferentId = "foreign" };
        else if (failure == "wrong-role") supplied = supplied with { Role = "enemy" };
        else if (failure == "wrong-kind") supplied = supplied with { Kind = "control" };
        else if (failure == "invisible") supplied = supplied with { State = supplied.State with { Visible = false } };
        else if (failure == "wrong-schema") supplied = supplied with { PropertiesSchema = "foreign" };
        else if (failure == "empty-label") supplied = supplied with { Label = "" };
        else if (failure == "foreign-facts-id") supplied.Properties!["card_referent_id"] = "foreign";
        else if (failure == "wrong-title") supplied.Properties!["displayed_title"] = "Other card";
        else if (failure == "missing-cost") supplied.Properties!.AsObject().Remove("displayed_cost");
        else if (failure == "missing-description") supplied.Properties!.AsObject().Remove("displayed_description");
        Assert.Null(NativeTextMenuInformation.BindCardTipSubject(bindings, "rendered-current", true, () =>
            failure == "unreadable" ? throw new InvalidOperationException("Rendered source unreadable")
                : failure == "null" ? null : supplied));
        Assert.False(bindings.Complete);
        Assert.Contains("public_information_binding_card_subject", bindings.Page.Completeness.Missing);
        if (failure == "unreadable")
            Assert.Contains("public_information_binding_card_tip_source_unreadable", bindings.Page.Completeness.Missing);
        Assert.DoesNotContain(bindings.Page.Referents, value => value.ReferentId == "rendered-current");
        Assert.Empty(NativeTextMenuFrameBuilder.CloseIncompleteInformationBindings(bindings.Page, "owner")!.Leaves);
    }

    [Theory]
    [InlineData("wrong-role")]
    [InlineData("invisible")]
    [InlineData("missing-label")]
    [InlineData("full-page")]
    public void ExistingCardSubjectOwnsItsRoleVisibilityAndCurrentPageFacts(string condition)
    {
        var existing = Ref("current-page-card", "card", "Selector Defend") with
        {
            PropertiesSchema = "sts2.player-environment/referent/card-1",
            Properties = JsonNode.Parse("""{"definition_id":"DEFEND","cost":"2","description":"Shown selector text"}""")
        };
        if (condition == "wrong-role") existing = existing with { Role = "enemy" };
        else if (condition == "invisible") existing = existing with { State = existing.State with { Visible = false } };
        else if (condition == "missing-label") existing = existing with { Label = null };
        var page = Page() with { Referents = new[] { existing } };
        var before = JsonSerializer.Serialize(page);
        var bindings = new PublicInformationBindings(page);
        var subject = NativeTextMenuInformation.BindCardTipSubject(bindings, existing.ReferentId, true,
            () => throw new InvalidOperationException("Existing subject may not be replaced"));
        if (condition == "full-page")
        {
            Assert.Same(existing, subject);
            Assert.Equal("2", subject!.Properties!["cost"]!.GetValue<string>());
            Assert.Equal("Shown selector text", subject.Properties["description"]!.GetValue<string>());
            Assert.True(bindings.Complete);
        }
        else
        {
            Assert.Null(subject);
            Assert.False(bindings.Complete);
        }
        Assert.Equal(before, JsonSerializer.Serialize(page));
        Assert.Equal(JsonSerializer.Serialize(existing), JsonSerializer.Serialize(bindings.Page.Referents.Single()));
    }

    [Fact]
    public void RenderedSubjectFreezesOneCopyAndDoesNotBecomeAnotherCatalogOrHandReader()
    {
        var page = Page();
        var before = JsonSerializer.Serialize(page);
        var bindings = new PublicInformationBindings(page);
        var supplied = NativeLogicalPresentation.RenderedCardSubject("rendered-current", "Defend", "1", "Block");
        int reads = 0;
        var subject = NativeTextMenuInformation.BindCardTipSubject(bindings, supplied.ReferentId, true,
            () => { reads++; return supplied; });
        supplied.Properties!["displayed_title"] = "Changed later";
        supplied.Properties["displayed_cost"] = "99";
        Assert.NotNull(subject);
        Assert.Equal("Defend", subject!.Properties!["displayed_title"]!.GetValue<string>());
        Assert.Equal("1", subject.Properties["displayed_cost"]!.GetValue<string>());
        Assert.Same(subject, NativeTextMenuInformation.BindCardTipSubject(bindings, supplied.ReferentId, true,
            () => throw new InvalidOperationException("No second rendered read")));
        Assert.Equal(1, reads);
        Assert.Single(bindings.Page.Referents, value => value.ReferentId == supplied.ReferentId);
        Assert.Same(page.Interaction.Content.Context, bindings.Page.Interaction.Content.Context);
        Assert.Same(page.BoundActions, bindings.Page.BoundActions);
        Assert.Equal(before, JsonSerializer.Serialize(page));
        Assert.Null(bindings.Card("another-active-card-without-facts"));
        Assert.False(bindings.Complete);
    }

    [Fact]
    public void ExactNativeCardHolderTypeDoesNotBorrowClickableAvailability()
    {
        Assert.Equal(typeof(Godot.Control), typeof(MegaCrit.Sts2.Core.Nodes.Cards.Holders.NCardHolder).BaseType);
        Assert.Equal(typeof(MegaCrit.Sts2.Core.Nodes.Cards.Holders.NCardHolder),
            typeof(MegaCrit.Sts2.Core.Nodes.Cards.Holders.NHandCardHolder).BaseType);
        Assert.False(typeof(MegaCrit.Sts2.Core.Nodes.GodotExtensions.NClickableControl)
            .IsAssignableFrom(typeof(MegaCrit.Sts2.Core.Nodes.Cards.Holders.NHandCardHolder)));
    }

    [Fact]
    public void NativeUnavailableCreatureTooltipDoesNotRequireAFrozenSubject()
    {
        var page = Page() with
        {
            Status = "settling", Referents = Array.Empty<PlayerEnvironmentReferent>(),
            Interaction = Page().Interaction with
            {
                Content = new(new JsonObject { ["kind"] = "no_action" },
                    new JsonObject { ["kind"] = "combat_transition", ["phase"] = "setup" })
            }
        };
        var bindings = new PublicInformationBindings(page);
        int subjectReads = 0;
        // v0.111 Creature.HoverTips returns empty before IsInProgress, despite a
        // visible current hitbox. Exercise the same production binding seam.
        Assert.Null(bindings.CreatureTooltipSubject(() => false,
            () => { subjectReads++; return "creature-not-in-setup-context"; }));
        Assert.Equal(0, subjectReads);
        Assert.True(bindings.Complete);
        Assert.Empty(bindings.Page.Completeness.Missing);
        Assert.Equal("settling", bindings.Page.Status);
    }

    [Fact]
    public void UnreadableNativeCreatureTooltipCapabilityRemainsPartialWithoutSubjectLookup()
    {
        var bindings = new PublicInformationBindings(Page());
        int subjectReads = 0;
        Assert.Null(bindings.CreatureTooltipSubject(
            () => throw new InvalidOperationException("native capability unreadable"),
            () => { subjectReads++; return "player"; }));
        Assert.Equal(0, subjectReads);
        Assert.Contains("public_information_binding_creature_tip_source_unreadable",
            bindings.Page.Completeness.Missing);
        Assert.False(bindings.Complete);
        Assert.Equal("partial", bindings.Page.Completeness.Status);
    }

    [Fact]
    public void AvailableNativeCreatureTooltipStillRequiresItsExactFrozenSubject()
    {
        var bindings = new PublicInformationBindings(Page());
        var order = new List<string>();
        Assert.Equal("enemy", bindings.CreatureTooltipSubject(
            () => { order.Add("native capability"); return true; },
            () => { order.Add("subject"); return "enemy"; })!.ReferentId);
        Assert.Equal(new[] { "native capability", "subject" }, order);
        Assert.True(bindings.Complete);
        Assert.Null(bindings.CreatureTooltipSubject(() => true, () => "foreign-creature"));
        Assert.Contains("public_information_binding_creature_subject", bindings.Page.Completeness.Missing);
        Assert.False(bindings.Complete);
    }

    [Fact]
    public void NativeLocalEmptyOrbKeepsItsFrozenHudPlayerDuringCombatSetup()
    {
        var page = Page() with
        {
            Status = "settling", Referents = Array.Empty<PlayerEnvironmentReferent>(),
            Interaction = Page().Interaction with
            {
                Content = new(new JsonObject { ["kind"] = "no_action" },
                    new JsonObject { ["kind"] = "combat_transition", ["phase"] = "setup" })
            }
        };
        var before = JsonSerializer.Serialize(page);
        var bindings = new PublicInformationBindings(page);
        PlayerEnvironmentReferent slot = bindings.EmptyOrb("ui-empty-slot", "player", 0, 3)!;
        Assert.NotNull(slot);
        Assert.Equal("player", slot.Properties!["owner_referent_id"]!.GetValue<string>());
        PlayerEnvironmentReferent owner = bindings.Page.Referents.Single(value => value.ReferentId == "player");
        Assert.Equal("Defect", owner.Label);
        Assert.Equal("player", owner.Role);
        Assert.Equal(new[] { "character_name", "entity_id" },
            ((JsonObject)owner.Properties!).Select(pair => pair.Key).Order().ToArray());
        Assert.True(bindings.Complete);
        Assert.Equal(before, JsonSerializer.Serialize(page));
        // The new subject is not a substitute for absent active combat facts.
        Assert.Null(bindings.Power("power", "player", "STRENGTH", 2, true));
        Assert.Null(bindings.Intent("intent", "player", 0, 1, true));
        Assert.False(bindings.Complete);
    }

    [Theory]
    [InlineData("foreign")]
    [InlineData("missing-hud")]
    [InlineData("wrong-id")]
    [InlineData("missing-name")]
    [InlineData("invisible-owner")]
    public void EmptyOrbCannotInventAnOwnerFromUnrelatedOrMissingHud(string missing)
    {
        var page = Page() with { Referents = Array.Empty<PlayerEnvironmentReferent>() };
        string owner = missing == "foreign" ? "foreign-creature" : "player";
        if (missing == "missing-hud") page = page with { Persistent = null };
        else if (missing == "invisible-owner")
            page = page with { Referents = new[] { Ref("player", "player", "Defect") with
                { State = new(false, false, false, false, "unavailable") } } };
        else if (missing is "wrong-id" or "missing-name")
        {
            JsonObject hud = (JsonObject)page.Persistent!.Content.DeepClone();
            if (missing == "wrong-id") hud["player"]!["entity_id"] = "other-player";
            else hud["player"]!.AsObject().Remove("character_name");
            page = page with { Persistent = page.Persistent with { Content = hud } };
        }
        var bindings = new PublicInformationBindings(page);
        Assert.Null(bindings.EmptyOrb("ui-empty-slot", owner, 0, 3));
        Assert.False(bindings.Complete);
        Assert.DoesNotContain(bindings.Page.Referents, value => value.ReferentId == "ui-empty-slot");
    }
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

    [Theory]
    [InlineData("native_combat_pile_selection", "Discard strike")]
    [InlineData("native_generated_card_choice", "Generated strike")]
    [InlineData("native_simple_card_selection", "Simple selector strike")]
    public void NonHandSelectorCardsRetainCurrentPageFactsWithCombatHandContext(
        string kind, string title)
    {
        PlayerEnvironmentSnapshot page = Page(); // Combat context contains two unrelated hand cards.
        var card = JsonNode.Parse("""
        {"entity_id":"page-card","definition_id":"SELECTOR_STRIKE","name":"replace-title",
          "type":"Attack","cost":"2","description":"Already shown selector card body",
          "rarity":"Common","is_upgraded":true,"is_selected":false}
        """)!.AsObject();
        card["name"] = title;
        var surface = new JsonObject { ["kind"] = kind,
            ["cards"] = new JsonArray(card), ["selectable_card_entity_ids"] = new JsonArray("page-card") };
        // The actual production projection supplies the visible selector card's
        // typed role/label/full properties; it is not an invented generic entity.
        PlayerEnvironmentReferent[] selectorRefs = PlayerEnvironmentService.ProjectFactReferents(surface).Values.ToArray();
        page = page with
        {
            Interaction = page.Interaction with { Kind = kind, Stage = kind == "native_generated_card_choice" ? "choosing" : "selecting",
                ContentSchema = $"sts2.player-environment/surface/{kind}-1",
                Content = new(surface, page.Interaction.Content.Context) },
            Referents = page.Referents.Concat(selectorRefs).ToArray()
        };
        var bindings = new PublicInformationBindings(page);
        var subject = bindings.Card("page-card");
        Assert.NotNull(subject);
        Assert.Equal("card", subject!.Role);
        Assert.Equal(title, subject.Label);
        Assert.Equal("2", subject.Properties!["cost"]!.GetValue<string>());
        Assert.Equal("Already shown selector card body", subject.Properties["description"]!.GetValue<string>());
        Assert.True(bindings.Complete);
        var tip = PublicInformationBindings.Leaf("page-tip", "card_tips", "show_card_tips", subject,
            () => NativeInputResult.Delivered("same selector holder"));
        var frame = Frame(bindings.Page, tip) with
        {
            Leaves = Frame(bindings.Page, tip).Leaves.Append(new TextMenuLeaf("select-page-card", "root",
                "select", "Select " + title, "page-card", Array.Empty<PlayerEnvironmentBoundActionArgument>(),
                () => NativeInputResult.Delivered("same native selector"))).ToArray()
        };
        var session = new TextMenuV2Session();
        var root = session.Observe(frame).Snapshot;
        Assert.Equal("complete", root.MenuActions.Status);
        Assert.Contains(root.MenuActions.Actions, action => action.Verb == "select" && action.SubjectReferentId == "page-card");
        var tips = Browse(session, frame, "card_tips");
        Assert.Contains(tips.MenuActions.Actions, action => action.Verb == "show_card_tips"
            && action.SubjectReferentId == "page-card" && action.Label == "Show " + title + " tips");
    }

    [Fact]
    public void FullPageCardFactsTakePrecedenceOverContextualHandEnrichment()
    {
        var page = Page();
        page = page with { Referents = page.Referents.Select(value => value.ReferentId == "card-a"
            ? value with { Role = "card", PropertiesSchema = "sts2.player-environment/referent/card-1",
                Properties = JsonNode.Parse("""{"definition_id":"STRIKE","cost":"2","is_upgraded":true}""") }
            : value).ToArray() };
        var bindings = new PublicInformationBindings(page);
        var card = bindings.Card("card-a")!;
        Assert.Equal("2", card.Properties!["cost"]!.GetValue<string>());
        Assert.True(card.Properties["is_upgraded"]!.GetValue<bool>());
        Assert.True(bindings.Complete);
    }

    [Fact]
    public void NonCardPublicRolesCannotBecomeTipCardSubjects()
    {
        var bindings = new PublicInformationBindings(Page());
        Assert.Null(bindings.Card("enemy"));
        Assert.Equal("partial", bindings.Page.Completeness.Status);
        Assert.Contains("public_information_binding_card_subject", bindings.Page.Completeness.Missing);
    }

    [Fact]
    public void EveryTipFamilyPublishesVisibleSubjectsAndExactPublicOwnersWithoutUnopenedBodies()
    {
        var bindings = new PublicInformationBindings(Page());
        var declarations = new[]
        {
            ("card_tips", bindings.Card("card-a"), (string?)null),
            ("relic_tips", bindings.Relic("relic"), (string?)null),
            ("orb_tips", bindings.OrbPresentation("ui-orb", "player", "LIGHTNING", "Lightning", 0, 3, true, true, "3", false, null), "player"),
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
        Assert.Contains("displayed_passive_text", json);
        Assert.DoesNotContain("passive_value", json);
        Assert.Equal("Attack 7", bindings.Page.Referents.Single(value => value.ReferentId == "intent-a").Label);
    }

    [Fact]
    public void UiOrbDescriptorsDoNotEraseLogicalValuesOrRequireLogicalModelMembership()
    {
        var page = Page();
        page = page with { Referents = page.Referents.Select(value => value.ReferentId == "orb"
            ? value with { PropertiesSchema = "sts2.player-environment/referent/orb-1",
                Properties = page.Interaction.Content.Context["player"]!["orbs"]![0]!.DeepClone() } : value).ToArray() };
        string logicalBefore = page.Interaction.Content.Context.ToJsonString();
        var bindings = new PublicInformationBindings(page);
        var ui = bindings.OrbPresentation("native-ui-node", "player", "LIGHTNING", "Lightning", 1, 4,
            true, true, "12", true, "21")!;
        Assert.True(bindings.Complete);
        Assert.Equal("control", ui.Kind);
        Assert.Equal("orb_ui", ui.Role);
        Assert.Equal("native_current_orb_ui", ui.Properties!["presentation_basis"]!.GetValue<string>());
        Assert.Equal("12", ui.Properties["displayed_passive_text"]!.GetValue<string>());
        Assert.Null(ui.Properties["passive_value"]);
        Assert.Null(ui.Properties["evoke_value"]);
        Assert.Null(ui.Properties["queue_index"]);
        Assert.Null(ui.Properties["is_next_to_evoke"]);
        Assert.Null(ui.Properties["description"]);
        Assert.Equal(logicalBefore, bindings.Page.Interaction.Content.Context.ToJsonString());
        var logical = bindings.Page.Referents.Single(value => value.ReferentId == "orb");
        Assert.Equal(3, logical.Properties!["passive_value"]!.GetValue<int>());
        Assert.Equal(8, logical.Properties["evoke_value"]!.GetValue<int>());
        Assert.Equal(0, logical.Properties["queue_index"]!.GetValue<int>());
        Assert.True(logical.Properties["is_next_to_evoke"]!.GetValue<bool>());
        Assert.Equal("orb", logical.Role);
        var emptyUi = bindings.EmptyOrb("native-empty-node", "player", 3, 4)!;
        Assert.Equal("control", emptyUi.Kind);
        Assert.False(emptyUi.Properties!["occupied"]!.GetValue<bool>());
        Assert.Equal(4, emptyUi.Properties["slot_count"]!.GetValue<int>()); // UI capacity may differ legitimately.
    }

    [Fact]
    public void ActualVisibleEmptyAmountTextIsCapturedWithoutNumericInference()
    {
        var bindings = new PublicInformationBindings(Page());
        var ui = bindings.OrbPresentation("ui-empty-text", "player", "LIGHTNING", "Lightning", 0, 1,
            true, true, "", true, "");
        Assert.NotNull(ui);
        Assert.True(bindings.Complete);
        Assert.Equal("", ui!.Properties!["displayed_passive_text"]!.GetValue<string>());
        Assert.Equal("", ui.Properties["displayed_evoke_text"]!.GetValue<string>());
    }

    [Fact]
    public void PlasmaHiddenAmountLabelsAreValidButMissingRequiredNodesAreNot()
    {
        var bindings = new PublicInformationBindings(Page());
        var plasma = bindings.OrbPresentation("ui-plasma", "player", "PLASMA", "Plasma", 0, 1,
            true, false, "must not expose hidden label", false, "must not expose hidden label");
        Assert.NotNull(plasma);
        Assert.True(bindings.Complete);
        Assert.False(plasma!.Properties!["passive_text_visible"]!.GetValue<bool>());
        Assert.False(plasma.Properties["evoke_text_visible"]!.GetValue<bool>());
        Assert.Null(plasma.Properties["displayed_passive_text"]);
        Assert.Null(plasma.Properties["displayed_evoke_text"]);
        Assert.Null(bindings.OrbPresentation("bad-ui", "player", "PLASMA", "Plasma", 0, 1,
            false, false, null, false, null));
        Assert.False(bindings.Complete);
    }

    [Theory]
    [InlineData("card")]
    [InlineData("power-owner")]
    [InlineData("power-membership")]
    [InlineData("power-amount")]
    [InlineData("intent-owner")]
    [InlineData("intent-membership")]
    [InlineData("intent-count")]
    [InlineData("orb-owner")]
    [InlineData("orb-label-nodes")]
    [InlineData("orb-visible-text")]
    [InlineData("slot-owner")]
    [InlineData("slot-range")]
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
            "orb-owner" => bindings.OrbPresentation("ui-orb", "not-current", "LIGHTNING", "Lightning", 0, 3, true, true, "3", false, null),
            "orb-label-nodes" => bindings.OrbPresentation("ui-orb", "player", "LIGHTNING", "Lightning", 0, 3, false, false, null, false, null),
            "orb-visible-text" => bindings.OrbPresentation("ui-orb", "player", "LIGHTNING", "Lightning", 0, 3, true, true, null, false, null),
            "slot-owner" => bindings.EmptyOrb("slot", "not-current", 2, 3),
            _ => bindings.EmptyOrb("slot", "player", 3, 3)
        };
        Assert.Null(invalid);
        Assert.Equal("partial", bindings.Page.Completeness.Status);
        Assert.Contains(bindings.Page.Completeness.Missing, value => value.StartsWith("public_information_binding_"));
        var observed = new TextMenuV2Session().Observe(Frame(bindings.Page)).Snapshot;
        Assert.Equal("unavailable", observed.MenuActions.Status);
        Assert.Empty(observed.MenuActions.Actions);
    }

    [Theory]
    [InlineData("settling", "settling")]
    [InlineData("interactive", "visible_unsupported")]
    [InlineData("observed", "visible_unsupported")]
    [InlineData("visible_unsupported", "visible_unsupported")]
    public void IncompleteBindingsPreserveOnlyAlreadyDeclaredNativeSettling(
        string nativeStatus, string expectedStatus)
    {
        var page = Page();
        page = page with
        {
            Status = nativeStatus,
            Interaction = page.Interaction with { Stage = nativeStatus == "settling" ? "settling" : "ready" },
            Completeness = page.Completeness with { Status = "partial",
                Missing = new[] { "public_information_binding_orb_capture_inconsistent" } }
        };
        var closed = NativeTextMenuFrameBuilder.CloseIncompleteInformationBindings(page, "owner");
        Assert.NotNull(closed);
        Assert.Equal(expectedStatus, closed!.Page.Status);
        Assert.Equal("partial", closed.Page.Completeness.Status);
        Assert.Equal(page.Completeness.Missing, closed.Page.Completeness.Missing);
        Assert.Empty(closed.Leaves);
        var published = new TextMenuV2Session().Observe(closed).Snapshot;
        Assert.Equal(expectedStatus, published.Status);
        Assert.Equal("partial", published.Completeness.Status);
        Assert.Equal("unavailable", published.MenuActions.Status);
        Assert.Empty(published.MenuActions.Actions);
    }

    [Fact]
    public void CompleteInformationMappingDoesNotChangeNativeReadiness()
    {
        var page = Page() with { Status = "settling" };
        Assert.Null(NativeTextMenuFrameBuilder.CloseIncompleteInformationBindings(page, "owner"));
    }

    [Fact]
    public void DyingVisualIsNotARequiredIntentButMissingFactsForCurrentNativeOwnerStillFailClosed()
    {
        PlayerEnvironmentSnapshot page = Page();
        page = page with
        {
            Interaction = page.Interaction with { Content = new(page.Interaction.Content.Surface,
                JsonNode.Parse("""{"kind":"combat","player":{"player_entity_id":"player","hand":[]},"enemies":[]}""")!) },
            Referents = page.Referents.Where(value => value.Role == "player").ToArray()
        };
        object dyingNode = new(), playerNode = new();
        var retired = NativeCreatureTipOwner.Resolve(new[] { dyingNode }, new[] { playerNode }, new[] { dyingNode });
        var bindings = new PublicInformationBindings(page);
        Assert.Equal(NativeCreatureTipOwnerScope.Retired, retired.Scope);
        // Positive native retirement excludes the old visual before attempting a
        // public intent binding. It must not poison otherwise complete root input.
        var frame = Frame(bindings.Page) with
        {
            Leaves = new[] { new TextMenuLeaf("native-end", "root", "end_turn", "End turn", null,
                Array.Empty<PlayerEnvironmentBoundActionArgument>(), () => NativeInputResult.Delivered("native guard")) }
        };
        Assert.Equal("complete", new TextMenuV2Session().Observe(frame).Snapshot.MenuActions.Status);

        var current = NativeCreatureTipOwner.Resolve(new[] { dyingNode }, new[] { dyingNode, playerNode }, Array.Empty<object>());
        Assert.Equal(NativeCreatureTipOwnerScope.Current, current.Scope);
        Assert.Null(bindings.Intent("current-intent", "enemy", 0, 1, true));
        Assert.Equal("partial", bindings.Page.Completeness.Status);
        Assert.Equal("unavailable", new TextMenuV2Session().Observe(Frame(bindings.Page)).Snapshot.MenuActions.Status);
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
