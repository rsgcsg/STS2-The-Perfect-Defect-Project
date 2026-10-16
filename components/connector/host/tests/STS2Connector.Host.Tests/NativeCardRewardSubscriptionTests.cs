using System.Reflection;
using System.Runtime.CompilerServices;
using HarmonyLib;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Rewards;
using STS2Platform.NativeFoundation;

namespace STS2Connector.Host.Tests;

[Collection("Native run hook read isolation")]
public sealed class NativeCardRewardSubscriptionTests
{
    private static int Calls;
    private static void Observe() => Calls++;

    [Fact]
    public void CurrentExactHandlerIsObservedWithoutInvokingOrInferringHistory()
    {
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic;
        var reward = (CardReward)RuntimeHelpers.GetUninitializedObject(typeof(CardReward));
        var player = (Player)RuntimeHelpers.GetUninitializedObject(typeof(Player));
        typeof(Reward).GetField("<Player>k__BackingField", flags)!.SetValue(reward, player);
        FieldInfo subscription = typeof(Player).GetField("RelicObtained", flags)!;
        MethodInfo handler = typeof(CardReward).GetMethod("OnRelicObtained", flags)!;
        var native = (Action<RelicModel>)Delegate.CreateDelegate(typeof(Action<RelicModel>), reward, handler);
        Action<RelicModel> unrelated = _ => Calls++;
        Calls = 0;
        Assert.Equal("absent", NativeCardRewardAlternativePreview.NativeRelicRefreshSubscription(reward));
        subscription.SetValue(player, unrelated + native);
        Assert.Equal("attached", NativeCardRewardAlternativePreview.NativeRelicRefreshSubscription(reward));
        subscription.SetValue(player, native + native);
        Assert.Equal("unavailable", NativeCardRewardAlternativePreview.NativeRelicRefreshSubscription(reward));
        subscription.SetValue(player, unrelated);
        Assert.Equal("absent", NativeCardRewardAlternativePreview.NativeRelicRefreshSubscription(reward));
        var harmony = new Harmony("sts2-test-native-reward-subscription");
        try
        {
            subscription.SetValue(player, native);
            harmony.Patch(handler, prefix: new HarmonyMethod(typeof(NativeCardRewardSubscriptionTests).GetMethod(nameof(Observe), BindingFlags.Static | BindingFlags.NonPublic)));
            Assert.Equal("unavailable", NativeCardRewardAlternativePreview.NativeRelicRefreshSubscription(reward));
        }
        finally { harmony.UnpatchAll(harmony.Id); }
        Assert.Equal(0, Calls);
    }
}
