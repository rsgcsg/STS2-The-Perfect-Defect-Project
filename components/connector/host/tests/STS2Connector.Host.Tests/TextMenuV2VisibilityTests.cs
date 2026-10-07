using System.Text.Json.Nodes;
using STS2Connector.PlayerEnvironment;
using STS2Connector.PlayerEnvironment.Protocol;
using Xunit;

namespace STS2Connector;

public sealed class TextMenuV2VisibilityTests
{
    private static PlayerEnvironmentReferent Ref(string id, string role, JsonNode properties) =>
        new(id, role, "entity", "public name", new(true, true, false, false, "native_visible_fact"),
            $"sts2.player-environment/referent/{role}-1", properties);

    private static PlayerEnvironmentSnapshot Snapshot()
    {
        JsonNode context = JsonNode.Parse("""
        {"kind":"combat","player":{"hand":[{"name":"Strike","description":"visible card body"}],
          "orbs":[{"entity_id":"logical-orb","name":"Lightning","description":"unopened orb body","passive_value":3,"evoke_value":8,"queue_index":0}],
          "statuses":[{"name":"Strength","type":"Buff","amount":2,"description":"unopened player power"}],
          "companions":[{"name":"companion","hp":5,"statuses":[{"name":"Power","amount":1,"description":"unopened companion power"}]}]},
          "enemies":[{"name":"enemy","hp":20,"intents":[{"type":"Attack","label":"7","title":"unopened intent title","description":"unopened intent body"}],
            "statuses":[{"name":"Strength","amount":1,"description":"unopened enemy power"}]}]}
        """)!;
        return new("1.0.0", PlayerEnvironmentContract.SnapshotSchema, "source", 1,
            DateTimeOffset.UnixEpoch, "interactive", null,
            new("interaction", "combat_turn", "ready", null, "sts2.player-environment/surface/combat_turn-1",
                new(new JsonObject { ["kind"] = "combat_turn" }, context), Array.Empty<PlayerEnvironmentInteractionCapability>()),
            new[] { Ref("player", "player", context["player"]!.DeepClone()),
                Ref("enemy", "enemy", context["enemies"]![0]!.DeepClone()),
                Ref("logical-orb", "orb", context["player"]!["orbs"]![0]!.DeepClone()),
                Ref("card", "card", context["player"]!["hand"]![0]!.DeepClone()) },
            new("sts2.player-environment/bound-actions-1", "complete", 0, 0, 512, "native", Array.Empty<PlayerEnvironmentBoundAction>()),
            Array.Empty<PlayerEnvironmentReadOpportunity>(),
            new("complete", "public", "complete", Array.Empty<string>(), Array.Empty<string>()),
            new("runtime", "environment"), new("player_visible_v1", "current_page", false, "omit"));
    }

    [Fact]
    public void RootProjectionRemovesOnlyKnownUnopenedBodiesAndPreservesOriginalSnapshot()
    {
        var source = Snapshot();
        string originalContext = source.Interaction.Content.Context.ToJsonString();
        string[] originalRefs = source.Referents.Select(value => value.Properties!.ToJsonString()).ToArray();
        var projected = TextMenuV2Visibility.Sanitize(source);
        string[] published = projected.Referents.Select(value => value.Properties!.ToJsonString()).ToArray();
        Assert.DoesNotContain("unopened", projected.Interaction.Content.Context.ToJsonString());
        Assert.All(published, value => Assert.DoesNotContain("unopened", value));
        Assert.Contains("visible card body", projected.Interaction.Content.Context.ToJsonString());
        Assert.Contains("visible card body", projected.Referents.Single(value => value.Role == "card").Properties!.ToJsonString());
        Assert.Equal(3, projected.Interaction.Content.Context["player"]!["orbs"]![0]!["passive_value"]!.GetValue<int>());
        Assert.Equal("7", projected.Interaction.Content.Context["enemies"]![0]!["intents"]![0]!["label"]!.GetValue<string>());
        Assert.Equal(2, projected.Interaction.Content.Context["player"]!["statuses"]![0]!["amount"]!.GetValue<int>());
        Assert.Equal(originalContext, source.Interaction.Content.Context.ToJsonString());
        Assert.Equal(originalRefs, source.Referents.Select(value => value.Properties!.ToJsonString()).ToArray());
    }

    [Fact]
    public void EnteredNativeTipBodyRemainsAvailableAfterRootDidNotExposeIt()
    {
        var root = TextMenuV2Visibility.Sanitize(Snapshot());
        Assert.DoesNotContain("unopened orb body", root.Interaction.Content.Context.ToJsonString());
        var entered = Snapshot() with
        {
            Interaction = Snapshot().Interaction with { Kind = "orb_tips", Stage = "native_information_page",
                Content = new(JsonNode.Parse("""{"kind":"native_tips","tips":{"text_tips":[{"title":"Lightning","description":"now actually rendered orb body"}],"card_previews":[]}}""")!,
                    new JsonObject { ["kind"] = "native_tips" }) },
            Referents = Array.Empty<PlayerEnvironmentReferent>()
        };
        var published = TextMenuV2Visibility.Sanitize(entered);
        Assert.Contains("now actually rendered orb body", published.Interaction.Content.Surface.ToJsonString());
        Assert.Equal(entered.Interaction.Content.Surface.ToJsonString(), published.Interaction.Content.Surface.ToJsonString());
    }
}
