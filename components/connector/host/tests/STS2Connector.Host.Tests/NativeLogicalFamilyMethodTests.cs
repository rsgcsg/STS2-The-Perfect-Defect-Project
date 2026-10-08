using System.Reflection;
using System.Security.Cryptography;
using STS2Platform.GameMod;
using Godot;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.UI;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.CardLibrary;
using MegaCrit.Sts2.Core.Nodes.Screens.Capstones;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Runs;
using Xunit;

namespace STS2Connector;

// Reflection inspects exact native metadata only. These tests never initialize
// Godot or invoke native UI, and therefore claim source API conformance only.
public sealed class NativeLogicalFamilyMethodTests
{
    private const BindingFlags Flags = BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
    private static MethodInfo Method(Type owner, string name, params Type[] arguments) =>
        Assert.IsAssignableFrom<MethodInfo>(owner.GetMethod(name, Flags, arguments));
    [Fact]
    public void InformationPublicationSeamsExistAtExactTypedNativeApis()
    {
        Assert.Equal(typeof(void), Method(typeof(NCapstoneContainer), nameof(NCapstoneContainer.Open), typeof(ICapstoneScreen)).ReturnType);
        Assert.Equal(typeof(void), Method(typeof(NCapstoneContainer), nameof(NCapstoneContainer.Close)).ReturnType);
        Assert.Equal(typeof(void), Method(typeof(NInspectCardScreen), nameof(NInspectCardScreen.Open), typeof(List<CardModel>), typeof(int), typeof(bool)).ReturnType);
        Assert.Equal(typeof(void), Method(typeof(NInspectCardScreen), nameof(NInspectCardScreen.Close)).ReturnType);
        Assert.Equal(typeof(void), Method(typeof(ActiveScreenContext), nameof(ActiveScreenContext.Update)).ReturnType);
        Assert.Equal(typeof(void), Method(typeof(NDeckViewScreen), "DisplayCards").ReturnType);
        Assert.NotNull(typeof(NCardGrid).GetProperty(nameof(NCardGrid.IsShowingUpgrades))!.SetMethod);
        Assert.Equal(typeof(void), Method(typeof(NCardGrid), nameof(NCardGrid.SetCards), typeof(IReadOnlyList<CardModel>),
            typeof(PileType), typeof(List<SortingOrders>), typeof(Task)).ReturnType);
    }
    [Fact]
    public void RewardSourcesExistWithoutTreatingReturnedTasksAsCompletion()
    {
        Assert.Equal(typeof(NRewardsScreen), Method(typeof(NRewardsScreen), nameof(NRewardsScreen.ShowScreen),
            typeof(RewardsSet), typeof(bool), typeof(IRunState)).ReturnType);
        Assert.Equal(typeof(NCardRewardSelectionScreen), Method(typeof(NCardRewardSelectionScreen), nameof(NCardRewardSelectionScreen.ShowScreen),
            typeof(IReadOnlyList<CardCreationResult>), typeof(IReadOnlyList<CardRewardAlternative>)).ReturnType);
        Assert.Equal(typeof(void), Method(typeof(NCardRewardSelectionScreen), nameof(NCardRewardSelectionScreen.RefreshOptions),
            typeof(IReadOnlyList<CardCreationResult>), typeof(IReadOnlyList<CardRewardAlternative>)).ReturnType);
        Assert.Equal(typeof(Task<int?>), Method(typeof(NCardRewardSelectionScreen), nameof(NCardRewardSelectionScreen.OptionSelected)).ReturnType);
        foreach (Type owner in new[] { typeof(NRewardsScreen), typeof(NCardRewardSelectionScreen) })
            foreach (string name in new[] { "AfterOverlayOpened", "AfterOverlayShown", "AfterOverlayHidden", "AfterOverlayClosed" })
                Assert.Equal(typeof(void), Method(owner, name).ReturnType);
        foreach (string name in new[] { "UpdateScreenState", "BeforeRoomExit" }) Assert.Equal(typeof(void), Method(typeof(NRewardsScreen), name).ReturnType);
        foreach (string name in new[] { nameof(NRewardsScreen.RewardCollectedFrom), nameof(NRewardsScreen.RewardSkippedFrom) })
            Assert.Equal(typeof(void), Method(typeof(NRewardsScreen), name, typeof(Control)).ReturnType);
        Assert.Equal(typeof(void), Method(typeof(NRewardsScreen), "OnProceedButtonPressed", typeof(NButton)).ReturnType);
        Assert.Equal(typeof(void), Method(typeof(NOverlayStack), nameof(NOverlayStack.Push), typeof(IOverlayScreen)).ReturnType);
        Assert.Equal(typeof(void), Method(typeof(NOverlayStack), nameof(NOverlayStack.Remove), typeof(IOverlayScreen)).ReturnType);
        Assert.Equal(typeof(void), Method(typeof(NCardHolder), nameof(NCardHolder.SetClickable), typeof(bool)).ReturnType);
        foreach (string name in new[] { nameof(NClickableControl.Enable), nameof(NClickableControl.Disable) })
            Assert.Equal(typeof(void), Method(typeof(NClickableControl), name).ReturnType);
    }
    [Fact]
    public void DelayedCloseCallbackIsExactlyThePinnedVisibilityThenContextNativeBody()
    {
        Type owner = typeof(NInspectCardScreen);
        MethodInfo callback = Assert.IsAssignableFrom<MethodInfo>(owner.GetMethod(
            ConnectorNativeLogicalInspectionDeparture.NativeCallbackName,
            BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly, Type.EmptyTypes));
        Assert.Equal(owner, callback.DeclaringType); Assert.False(callback.IsStatic);
        Assert.Equal(typeof(void), callback.ReturnType);
        Assert.Equal(ConnectorNativeLogicalInspectionDeparture.GameModuleVersionId, owner.Assembly.ManifestModule.ModuleVersionId.ToString("D"));
        Assert.Equal(ConnectorNativeLogicalInspectionDeparture.GameAssemblySha256,
            Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(owner.Assembly.Location))).ToLowerInvariant());
        byte[] body = callback.GetMethodBody()!.GetILAsByteArray()!;
        Assert.Equal(18, body.Length);
        Assert.Equal(new byte[] { 0x02, 0x16, 0x28, 0x28, 0x6f, 0x2a },
            new[] { body[0], body[1], body[2], body[7], body[12], body[17] });
        Assert.Equal(typeof(CanvasItem).GetProperty(nameof(CanvasItem.Visible))!.SetMethod,
            callback.Module.ResolveMethod(BitConverter.ToInt32(body, 3)));
        Assert.Equal(typeof(ActiveScreenContext).GetProperty(nameof(ActiveScreenContext.Instance))!.GetMethod,
            callback.Module.ResolveMethod(BitConverter.ToInt32(body, 8)));
        Assert.Equal(Method(typeof(ActiveScreenContext), nameof(ActiveScreenContext.Update)),
            callback.Module.ResolveMethod(BitConverter.ToInt32(body, 13)));
        // Pin the actual Close TweenCallback delegate target, not a same-named
        // unused helper or a searched alternative compiler-generated method.
        byte[] close = Method(owner, nameof(NInspectCardScreen.Close)).GetMethodBody()!.GetILAsByteArray()!;
        Assert.Single(Enumerable.Range(0, close.Length - 5), i => close[i] == 0xfe && close[i + 1] == 0x06
            && BitConverter.ToInt32(close, i + 2) == callback.MetadataToken);
    }

}
