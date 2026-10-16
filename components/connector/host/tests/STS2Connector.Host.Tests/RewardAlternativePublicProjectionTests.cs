using System.Text.Json;
using System.Text.Json.Nodes;
using STS2Connector.LiveHost.Contracts;
using STS2Connector.PlayerEnvironment;

namespace STS2Connector.Host.Tests;

public sealed class RewardAlternativePublicProjectionTests
{
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void ActualPublicProjectionRetainsOuterPermissionAndCompleteNativeAlternatives(bool canLeave)
    {
        var reward = new VisibleReward("reward", "card", "Choose", "Offered card group", true)
        {
            GroupId="reward", GroupRelation="independent", CardOptions=Array.Empty<VisibleCard>(), CardAlternativesComplete=true,
            CanSkipSelection=true,CanReroll=true,SelectionCompletesReward=true,
            CardAlternatives=new[] { new VisibleRewardCardAlternative("Skip", "Skip", "end_selection_and_do_not_complete_reward") { IsPureReturn=true },
                new VisibleRewardCardAlternative("REROLL", "Reroll", "do_nothing") }
        };
        var surface = new RewardClaimSurface("reward_claim","screen",new[]{reward},false,Array.Empty<VisibleCombatPotion>(),true,true)
        { CanLeaveWithUnclaimedRewards=canLeave };
        var content=PlayerEnvironmentService.ProjectVisibleFacts(surface,new RewardFlowLiveContext("reward_flow","room_rewards"));
        var wire=JsonNode.Parse(JsonSerializer.Serialize(content,ConnectorMod._jsonOptions))!;
        Assert.Equal(canLeave,wire["surface"]!["can_leave_with_unclaimed_rewards"]!.GetValue<bool>());
        var group=wire["surface"]!["rewards"]![0]!;
        Assert.True(group["card_alternatives_complete"]!.GetValue<bool>());
        Assert.Equal("independent",group["group_relation"]!.GetValue<string>());
        Assert.True(group["card_alternatives"]![0]!["is_pure_return"]!.GetValue<bool>());
        Assert.Equal("REROLL",group["card_alternatives"]![1]!["option_id"]!.GetValue<string>());
        Assert.Equal("do_nothing",group["card_alternatives"]![1]!["after_selected"]!.GetValue<string>());
        Assert.True(group["selection_completes_reward"]!.GetValue<bool>());
    }
}
