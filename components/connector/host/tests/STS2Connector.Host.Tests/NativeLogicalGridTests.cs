using System.Reflection;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using STS2Connector.NativeUi;
using Xunit;

namespace STS2Connector;

public sealed class NativeLogicalGridTests
{
    public static IEnumerable<object[]> NativeCallbackTypes()
    {
        yield return new object[] { typeof(NDeckCardSelectScreen), nameof(NativeLogicalGridCallbacks.PressDeck) };
        yield return new object[] { typeof(NDeckUpgradeSelectScreen), nameof(NativeLogicalGridCallbacks.PressUpgrade) };
        yield return new object[] { typeof(NDeckTransformSelectScreen), nameof(NativeLogicalGridCallbacks.PressTransform) };
        yield return new object[] { typeof(NDeckEnchantSelectScreen), nameof(NativeLogicalGridCallbacks.PressEnchant) };
        yield return new object[] { typeof(NSimpleCardSelectScreen), nameof(NativeLogicalGridCallbacks.PressSimple) };
        yield return new object[] { typeof(NCombatPileCardSelectScreen), nameof(NativeLogicalGridCallbacks.PressPile) };
    }

    [Theory]
    [MemberData(nameof(NativeCallbackTypes))]
    public void ExactGameAbiHasConcreteNativeOverrideAndMatchingClosedAccessor(Type owner, string accessorName)
    {
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;
        MethodInfo native = Assert.IsAssignableFrom<MethodInfo>(owner.GetMethod("OnCardClicked", flags));
        Assert.Equal(owner, native.DeclaringType);
        Assert.True(native.IsFamily);
        Assert.True(native.IsVirtual);
        Assert.False(native.IsAbstract);
        Assert.Equal(typeof(void), native.ReturnType);
        Assert.Equal(typeof(CardModel), Assert.Single(native.GetParameters()).ParameterType);
        Assert.Equal(typeof(NCardGridSelectionScreen), native.GetBaseDefinition().DeclaringType);
        MethodInfo accessor = typeof(NativeLogicalGridCallbacks).GetMethod(accessorName,
            BindingFlags.Static | BindingFlags.NonPublic)!;
        Assert.Equal(new[] { owner, typeof(CardModel) }, accessor.GetParameters().Select(parameter => parameter.ParameterType));
        UnsafeAccessorAttribute attribute = accessor.GetCustomAttribute<UnsafeAccessorAttribute>()!;
        Assert.Equal(UnsafeAccessorKind.Method, attribute.Kind);
        Assert.Equal("OnCardClicked", attribute.Name);
    }

    [Fact]
    public void ExactGameAccessorsResolveConcreteNativeMethodsBeforeNullReceiverBoundary()
    {
        // ABI-only: no scene, holder or live owner exists. The native callback
        // cannot receive gameplay input; lookup must resolve rather than fail
        // MissingMethod/InvalidProgram before the null instance boundary.
        Assert.Throws<NullReferenceException>(() => NativeLogicalGridCallbacks.PressDeck(null!, null!));
        Assert.Throws<NullReferenceException>(() => NativeLogicalGridCallbacks.PressUpgrade(null!, null!));
        Assert.Throws<NullReferenceException>(() => NativeLogicalGridCallbacks.PressTransform(null!, null!));
        Assert.Throws<NullReferenceException>(() => NativeLogicalGridCallbacks.PressEnchant(null!, null!));
        Assert.Throws<NullReferenceException>(() => NativeLogicalGridCallbacks.PressSimple(null!, null!));
        Assert.Throws<NullReferenceException>(() => NativeLogicalGridCallbacks.PressPile(null!, null!));
    }

    [Fact]
    public void UnsafeAccessorRetainsVirtualDispatchButDoesNotSearchInheritedMembers()
    {
        var derived = new CallbackFixture();
        Assert.Equal(2, ConcreteOverride(derived));
        // A matching base virtual slot still dispatches the actual override.
        Assert.Equal(2, BaseCallback(derived));
        Assert.Throws<MissingMethodException>(() => InheritedMember(derived));
    }
    private class BaseFixture { protected virtual int Callback() => 1; protected int BaseOnly() => 7; }
    private sealed class CallbackFixture : BaseFixture { protected override int Callback() => 2; }
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "Callback")]
    private static extern int ConcreteOverride(CallbackFixture owner);
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "Callback")]
    private static extern int BaseCallback(BaseFixture owner);
    [UnsafeAccessor(UnsafeAccessorKind.Method, Name = "BaseOnly")]
    private static extern int InheritedMember(CallbackFixture owner);

    [Theory]
    [InlineData(200)]
    [InlineData(500)]
    public void FullRosterInputsPreserveEveryActualModelBeyondHolderWindowAndNativeSourceOrder(int count)
    {
        // Exact CardModel references from the game assembly; no Godot node/holder
        // or scene is instantiated. This is a binding fixture, not a live run.
        CardModel[] models = Enumerable.Range(0, count).Select(_ => (CardModel)new DefendDefect()).ToArray();
        CardModel[] holderWindow = models.Take(24).ToArray();
        Assert.True(NativeLogicalGridState.UniqueReferences(models));
        Assert.False(NativeLogicalGridState.SameReferences(models, holderWindow));
        var delivered = new List<CardModel>();
        foreach (CardModel model in models)
        {
            NativeInputResult result = NativeLogicalCardInput.Dispatch(model, () => models.Any(current => ReferenceEquals(current, model)),
                exact => delivered.Add(exact), "native_model_input");
            Assert.True(result.Accepted);
        }
        Assert.Equal(count, delivered.Count);
        for (int index = 0; index < count; index++)
        {
            Assert.Same(models[index], delivered[index]);
            Assert.Equal(index, NativeLogicalGridState.ReferenceIndex(models, delivered[index]));
        }
        Assert.Same(models[^1], delivered[^1]);
        Assert.DoesNotContain(holderWindow, model => ReferenceEquals(model, models[^1]));
        Assert.False(NativeLogicalGridState.SameReferences(models, models.Reverse().ToArray()));
        Assert.True(NativeLogicalGridState.SameReferences(models, models.Reverse().ToArray(), ordered: false));
    }

    [Fact]
    public void InspectorReturnRestoresExactCapstoneBeforeScreenContextAdvances()
    {
        bool screenContextAdvanced = false;
        bool backEnabled = false;
        bool sameSource = true;
        NativeLogicalInspectorReturn.Restore(false, () => sameSource, () => backEnabled = true);
        Assert.True(backEnabled);
        Assert.False(screenContextAdvanced);
        backEnabled = false;
        sameSource = false;
        NativeLogicalInspectorReturn.Restore(false, () => sameSource, () => backEnabled = true);
        Assert.False(backEnabled);
        sameSource = true;
        NativeLogicalInspectorReturn.Restore(true, () => sameSource, () => backEnabled = true);
        Assert.False(backEnabled);
    }

    [Fact]
    public void NativeCallbackOwnsSelectionAndPreviewAndThrowRemainsUnknown()
    {
        object card = new();
        var selected = new HashSet<object>();
        int previewCalls = 0;
        bool peeking = false;
        void NativeCallback(object actual)
        {
            if (!selected.Add(actual)) selected.Remove(actual);
            else previewCalls++;
        }
        var select = NativeLogicalCardInput.Dispatch(card, () => !peeking, NativeCallback, "native_input");
        Assert.True(select.Accepted);
        Assert.Contains(card, selected);
        Assert.Equal(1, previewCalls);
        var deselect = NativeLogicalCardInput.Dispatch(card, () => !peeking, NativeCallback, "native_input");
        Assert.True(deselect.Accepted);
        Assert.Empty(selected);
        peeking = true;
        Assert.False(NativeLogicalCardInput.Dispatch(card, () => !peeking, NativeCallback, "native_input").Accepted);
        Assert.Empty(selected);
        peeking = false; // The same native owner/roster can be used after Peek return.
        Assert.True(NativeLogicalCardInput.Dispatch(card, () => !peeking, NativeCallback, "native_input").Accepted);
        var unknown = NativeLogicalCardInput.Dispatch(card, () => true, exact =>
        { selected.Remove(exact); throw new MissingMethodException("Native callback failed after input"); }, "native_input");
        Assert.Equal(NativeInputDelivery.Unknown, unknown.Delivery);
        Assert.Empty(selected);
        Assert.False(unknown.Accepted);
    }
}
