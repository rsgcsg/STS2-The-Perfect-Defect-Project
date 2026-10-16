using System.Reflection;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using HarmonyLib;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Models.Enchantments;
using MegaCrit.Sts2.Core.Nodes.Cards;
using STS2Platform.NativeFoundation;

namespace STS2Connector;

[Collection("Native detached preview isolation")]
public sealed class NativeDetachedPreviewTests
{
    private const BindingFlags Flags = BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
    private static FieldInfo Field(Type type, string name) => type.GetField(name, Flags)!;
    private static CardModel Mutable(Type type)
    {
        // Actual exact-game constructor/copy methods. This local canonical
        // fixture avoids global ModelDb.Init/type discovery and has no owner,
        // Godot tree, native run, mutation command or installed Mod.
        var canonical = (CardModel)Activator.CreateInstance(type)!;
        Field(typeof(CardModel), "_canonicalInstance").SetValue(canonical, canonical);
        return canonical.ToMutable();
    }
    private static CardModel Mutable<T>() where T : CardModel => Mutable(typeof(T));
    private static string Values(CardModel card) => JsonSerializer.Serialize(new
    {
        type = card.GetType().Name, card.IsMutable, card.CurrentUpgradeLevel,
        keywords = card.GetKeywordsWithSources(KeywordSources.Local).Order().ToArray(),
        cost = card.EnergyCost.GetWithModifiers(CostModifiers.Local), card.EnergyCost.WasJustUpgraded,
        vars = card.DynamicVars.Values.Select(value => new { value.Name, value.BaseValue, value.EnchantedValue, value.PreviewValue, value.WasJustUpgraded }),
        enchantment = card.Enchantment?.GetType().Name, amount = card.Enchantment?.Amount,
        enchantVars = card.Enchantment?.DynamicVars.Values.Select(value => new { value.Name, value.BaseValue, value.EnchantedValue, value.PreviewValue })
    });

    [Theory]
    [InlineData(typeof(StrikeDefect))]
    [InlineData(typeof(DefendDefect))]
    [InlineData(typeof(Zap))]
    [InlineData(typeof(Dualcast))]
    [InlineData(typeof(Bash))]
    [InlineData(typeof(Neutralize))]
    [InlineData(typeof(Acrobatics))]
    [InlineData(typeof(Adrenaline))]
    [InlineData(typeof(Afterimage))]
    [InlineData(typeof(Chill))]
    [InlineData(typeof(Bodyguard))]
    [InlineData(typeof(Convergence))]
    [InlineData(typeof(SwordBoomerang))]
    [InlineData(typeof(Footwork))]
    [InlineData(typeof(Royalties))]
    [InlineData(typeof(TheSmith))]
    public void RealNativeUpgradeMatchesNativeCopyWithoutChangingOriginal(Type type)
    {
        CardModel original = Mutable(type);
        string before = Values(original);
        CardModel expected = (CardModel)original.MutableClone();
        expected.UpgradeInternal(); expected.UpgradePreviewType = CardUpgradePreviewType.Deck;
        Assert.True(NativeDetachedCardPreviewFactory.TryDeckUpgrade(original, out var result, out var gap), gap);
        Assert.Same(original, result!.Original); Assert.NotSame(original, result.Display);
        Assert.Equal(Values(expected), Values(result.Display)); Assert.Equal(before, Values(original));
        Assert.Equal(CardUpgradePreviewType.Deck, result.Display.UpgradePreviewType);
        Assert.Null(result.Display.Pile); Assert.Null(original.Pile);
        Assert.All(result.Display.DynamicVars.Values, value => Assert.DoesNotContain(value, original.DynamicVars.Values));
    }

    [Theory]
    [InlineData(typeof(Adroit))]
    [InlineData(typeof(Sharp))]
    public void RealNativeSingleEnchantMatchesNativePreviewSequence(Type type)
    {
        CardModel original = Mutable<StrikeDefect>(); string before = Values(original);
        var canonical = (EnchantmentModel)Activator.CreateInstance(type)!;
        var expected = (CardModel)original.MutableClone();
        var expectedEnchantment = canonical.ToMutable();
        expected.EnchantInternal(expectedEnchantment, 3); expected.IsEnchantmentPreview = true; expectedEnchantment.ModifyCard();
        Assert.True(NativeDetachedCardPreviewFactory.TrySingleEnchant(original, canonical, 3, out var result, out var gap), gap);
        Assert.Equal(Values(expected), Values(result!.Display)); Assert.Equal(before, Values(original));
        Assert.True(result.Display.IsEnchantmentPreview); Assert.Same(original, result.Original);
        Assert.Null(original.Enchantment); Assert.Null(result.Display.Pile);
    }

    private static (CardModel Original, NCard Node, Action Observer) EnchantedUiFixture()
    {
        CardModel original = Mutable<StrikeDefect>();
        original.EnchantInternal(new Sharp().ToMutable(), 2);
        var node = (NCard)RuntimeHelpers.GetUninitializedObject(typeof(NCard));
        NativeDetachedCardCopy.UiModel(node) = original;
        MethodInfo method = typeof(NCard).GetMethod("OnEnchantmentChanged", Flags)!;
        var observer = (Action)method.CreateDelegate(typeof(Action), node);
        original.EnchantmentChanged += observer;
        return (original, node, observer);
    }

    [Fact]
    public void RealNativeUiObserverRemainsOnOriginalAndDoesNotRunOnDetachedCopy()
    {
        var (original, node, observer) = EnchantedUiFixture();
        string before = Values(original);
        object? events = Field(typeof(CardModel), "EnchantmentChanged").GetValue(original);
        object? vars = Field(typeof(CardModel), "_dynamicVars").GetValue(original);
        object? subscribed = NativeDetachedCardCopy.UiSubscribedEnchantment(node);
        Assert.True(NativeDetachedCardPreviewFactory.TryDeckUpgrade(original, out var preview, out var gap), gap);
        Assert.Same(events, Field(typeof(CardModel), "EnchantmentChanged").GetValue(original));
        Assert.Same(vars, Field(typeof(CardModel), "_dynamicVars").GetValue(original));
        Assert.Same(original, NativeDetachedCardCopy.UiModel(node));
        Assert.Same(subscribed, NativeDetachedCardCopy.UiSubscribedEnchantment(node));
        Assert.Equal(before, Values(original)); Assert.NotSame(original.Enchantment, preview!.Display.Enchantment);
        Assert.Null(Field(typeof(CardModel), "EnchantmentChanged").GetValue(preview.Display));
        Assert.Same(node, observer.Target);
    }

    private static int calls;
    private static bool CountAndSkip() { calls++; return false; }
    private sealed class Patched : IDisposable
    {
        private readonly Harmony harmony = new("native-detached-preview-test-" + Guid.NewGuid().ToString("N"));
        private readonly MethodBase method;
        internal Patched(MethodBase method)
        {
            this.method = method; calls = 0;
            harmony.Patch(method, prefix: new HarmonyMethod(typeof(NativeDetachedPreviewTests).GetMethod(nameof(CountAndSkip), Flags)!));
        }
        public void Dispose() => harmony.Unpatch(method, HarmonyPatchType.Prefix, harmony.Id);
    }

    [Theory]
    [InlineData(typeof(StrikeDefect), "OnUpgrade")]
    [InlineData(typeof(DynamicVar), "SetOwner")]
    [InlineData(typeof(CardModel), "get_Enchantment")]
    [InlineData(typeof(CardModel), "DeepCloneFields")]
    public void PatchedKnownNativeHookOrGetterIsRejectedBeforeFirstCall(Type type, string method)
    {
        CardModel original = Mutable<StrikeDefect>();
        using var patch = new Patched(type.GetMethod(method, Flags)!);
        Assert.False(NativeDetachedCardPreviewFactory.TryDeckUpgrade(original, out var result, out var gap));
        Assert.Null(result); Assert.NotNull(gap); Assert.Equal(0, calls);
    }

    [Fact]
    public void StructuralBridgeClearsCopiedCallbackBeforeDeepCopy()
    {
        CardModel original = Mutable<StrikeDefect>();
        original.EnchantInternal(new Sharp().ToMutable(), 2);
        calls = 0; original.EnchantmentChanged += () => calls++;
        object? events = Field(typeof(CardModel), "EnchantmentChanged").GetValue(original);
        // Isolated bridge ordering probe: this deliberately unknown observer
        // would run during ordinary native MutableClone.DeepCloneFields.
        // Production qualification refuses it before using the bridge.
        CardModel copy = NativeDetachedCardCopy.CreateQualifiedCopy(original);
        Assert.Equal(0, calls); Assert.NotSame(original.Enchantment, copy.Enchantment);
        Assert.Same(events, Field(typeof(CardModel), "EnchantmentChanged").GetValue(original));
        Assert.False(NativeDetachedCardPreviewFactory.TryDeckUpgrade(original, out _, out _));
        Assert.Equal(0, calls);
    }

    [Fact]
    public void UnknownCopiedCallbackNeverRuns()
    {
        CardModel original = Mutable<StrikeDefect>(); calls = 0;
        original.EnchantmentChanged += () => calls++;
        Assert.False(NativeDetachedCardPreviewFactory.TryDeckUpgrade(original, out _, out _));
        Assert.Equal(0, calls); Assert.Equal(0, original.CurrentUpgradeLevel);
    }

    private sealed class UnknownOwnerVar() : DynamicVar("Unknown", 1)
    {
        public override void SetOwner(AbstractModel owner) { calls++; base.SetOwner(owner); }
    }

    [Fact]
    public void UnknownEffectiveVariableOverrideNeverRuns()
    {
        CardModel original = Mutable<StrikeDefect>();
        var vars = (Dictionary<string, DynamicVar>)Field(typeof(DynamicVarSet), "_vars").GetValue(original.DynamicVars)!;
        vars["Unknown"] = new UnknownOwnerVar(); calls = 0;
        Assert.False(NativeDetachedCardPreviewFactory.TryDeckUpgrade(original, out _, out _)); Assert.Equal(0, calls);
    }

    [Fact]
    public void NativeCopyExceptionLeavesOriginalObjectsAndEventsUnchanged()
    {
        CardModel original = Mutable<StrikeDefect>();
        Field(typeof(CardModel), "_temporaryStarCosts").SetValue(original, null);
        object? vars = Field(typeof(CardModel), "_dynamicVars").GetValue(original);
        Assert.False(NativeDetachedCardPreviewFactory.TryDeckUpgrade(original, out var result, out var gap));
        Assert.Null(result); Assert.Equal("native_detached_upgrade_preview_failed", gap);
        Assert.Same(vars, Field(typeof(CardModel), "_dynamicVars").GetValue(original));
        Assert.Null(Field(typeof(CardModel), "_temporaryStarCosts").GetValue(original));
        Assert.Null(original.Enchantment); Assert.Equal(0, original.CurrentUpgradeLevel);
    }
}

[CollectionDefinition("Native detached preview isolation", DisableParallelization = true)]
public sealed class NativeDetachedPreviewIsolation;
