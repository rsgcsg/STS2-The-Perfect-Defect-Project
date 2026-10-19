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
        bool registered = ModelDb.Contains(type);
        var canonical = registered ? ModelDb.GetById<CardModel>(ModelDb.GetId(type)) : (CardModel)Activator.CreateInstance(type)!;
        if (!registered) Field(typeof(CardModel), "_canonicalInstance").SetValue(canonical, canonical);
        return canonical.ToMutable();
    }
    private static CardModel Mutable<T>() where T : CardModel => Mutable(typeof(T));
    private static EnchantmentModel CanonicalEnchantment(Type type) => ModelDb.Contains(type)
        ? ModelDb.GetById<EnchantmentModel>(ModelDb.GetId(type)) : (EnchantmentModel)Activator.CreateInstance(type)!;
    private static string Values(CardModel card) => JsonSerializer.Serialize(new
    {
        type = card.GetType().Name, card.IsMutable, card.CurrentUpgradeLevel,
        keywords = card.GetKeywordsWithSources(KeywordSources.Local).Order().ToArray(),
        cost = card.EnergyCost.GetWithModifiers(CostModifiers.Local), card.EnergyCost.WasJustUpgraded,
        vars = card.DynamicVars.Values.Select(value => new { value.Name, value.BaseValue, value.EnchantedValue, value.PreviewValue, value.WasJustUpgraded }),
        enchantment = card.Enchantment?.GetType().Name, amount = card.Enchantment?.Amount,
        enchantVars = card.Enchantment?.DynamicVars.Values.Select(value => new { value.Name, value.BaseValue, value.EnchantedValue, value.PreviewValue })
    });

    [Fact]
    public void FixedUpgradeMethodInventoryIsPinnedToTheAuditedNativeBodies()
    {
        Assert.Equal(new Guid("57785517-0b16-42b9-8b36-bad6fb28384b"), typeof(CardModel).Module.ModuleVersionId);
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        string[] names = NativeUpgradeMethodInventory.TypeNames.Order(StringComparer.Ordinal).ToArray();
        Assert.Equal(538, names.Length);
        foreach (string name in names)
        {
            Type type = typeof(CardModel).Assembly.GetType("MegaCrit.Sts2.Core.Models.Cards." + name, throwOnError: true)!;
            MethodInfo method = type.GetMethod("OnUpgrade", Flags | BindingFlags.DeclaredOnly)!;
            Assert.NotNull(method);
            hash.AppendData(Encoding.UTF8.GetBytes(name + "\n"));
            hash.AppendData(method.GetMethodBody()!.GetILAsByteArray()!);
            hash.AppendData(new byte[] { 0 });
        }
        Assert.Equal("77afdcdaa11ab1e3013548ed9dfca0799c86602445ea59e537cc7887183f3053",
            Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant());
    }

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
    [InlineData(typeof(Barrage))]
    [InlineData(typeof(BodySlam))]
    [InlineData(typeof(PerfectedStrike))]
    [InlineData(typeof(Stack))]
    [InlineData(typeof(Darkness))]
    [InlineData(typeof(Cascade))]
    [InlineData(typeof(Dirge))]
    [InlineData(typeof(Eradicate))]
    [InlineData(typeof(HeavenlyDrill))]
    [InlineData(typeof(Malaise))]
    [InlineData(typeof(MultiCast))]
    [InlineData(typeof(Skewer))]
    [InlineData(typeof(Tempest))]
    [InlineData(typeof(Volley))]
    [InlineData(typeof(Whirlwind))]
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
        Assert.All(result.Display.DynamicVars.Values, value =>
        {
            Assert.DoesNotContain(value, original.DynamicVars.Values);
            Assert.Same(result.Display, Field(typeof(DynamicVar), "_owner").GetValue(value));
        });
        Assert.All(original.DynamicVars.Values, value => Assert.Same(original, Field(typeof(DynamicVar), "_owner").GetValue(value)));
    }

    [Fact]
    public void ExactNativeXCostOverridesAreConstantReadOnlyFactsAndPatchedGetterIsNotInvoked()
    {
        var overrides = typeof(CardModel).Assembly.GetTypes()
            .Where(type => type.Namespace == "MegaCrit.Sts2.Core.Models.Cards" && type.IsSubclassOf(typeof(CardModel)))
            .Select(type => type.GetMethod("get_HasEnergyCostX", Flags | BindingFlags.DeclaredOnly))
            .OfType<MethodInfo>().OrderBy(method => method.DeclaringType!.Name, StringComparer.Ordinal).ToArray();
        Assert.Equal(new[] { "Cascade", "Dirge", "Eradicate", "HeavenlyDrill", "Malaise", "MultiCast", "Skewer", "Tempest", "Volley", "Whirlwind" },
            overrides.Select(method => method.DeclaringType!.Name));
        Assert.All(overrides, method => Assert.Equal(new byte[] { 0x17, 0x2a }, method.GetMethodBody()!.GetILAsByteArray()));
        CardModel original = Mutable<Tempest>();
        using var patch = new Patched(typeof(Tempest).GetMethod("get_HasEnergyCostX", Flags)!);
        Assert.False(NativeDetachedCardPreviewFactory.TryDeckUpgrade(original, out var result, out var gap));
        Assert.Null(result); Assert.NotNull(gap); Assert.Equal(0, calls);
        Assert.Equal(0, original.CurrentUpgradeLevel);
    }

    [Fact]
    public void DarknessUsesTheNativeBaseNoOpUpgradeAndRefusesItWhenPatched()
    {
        CardModel original = Mutable<Darkness>();
        MethodInfo method = typeof(Darkness).GetMethod("OnUpgrade", Flags)!;
        Assert.Equal(typeof(CardModel), method.DeclaringType);
        Assert.Equal(new byte[] { 0x2a }, method.GetMethodBody()!.GetILAsByteArray());
        Assert.True(NativeDetachedCardPreviewFactory.TryDeckUpgrade(original, out var preview, out var gap), gap);
        Assert.Equal(1, preview!.Display.CurrentUpgradeLevel);
        Assert.Equal(0, original.CurrentUpgradeLevel);
        using var patch = new Patched(typeof(CardModel).GetMethod("OnUpgrade", Flags | BindingFlags.DeclaredOnly)!);
        Assert.False(NativeDetachedCardPreviewFactory.TryDeckUpgrade(original, out _, out gap));
        Assert.Equal("native_preview_upgrade_closure_unqualified", gap);
        Assert.Equal(0, calls);
    }

    [Theory]
    [InlineData(typeof(Adroit))]
    [InlineData(typeof(Sharp))]
    [InlineData(typeof(Goopy))]
    [InlineData(typeof(Steady))]
    [InlineData(typeof(RoyallyApproved))]
    [InlineData(typeof(Glam))]
    [InlineData(typeof(Inky))]
    [InlineData(typeof(Spiral))]
    [InlineData(typeof(TezcatarasEmber))]
    [InlineData(typeof(Sown))]
    public void RealNativeSingleEnchantMatchesNativePreviewSequence(Type type)
    {
        CardModel original = Mutable<StrikeDefect>(); string before = Values(original);
        EnchantmentModel canonical = CanonicalEnchantment(type);
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
        original.EnchantInternal(CanonicalEnchantment(typeof(Sharp)).ToMutable(), 2);
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
    [InlineData(typeof(LocalCostModifier), "set_Amount")]
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
        original.EnchantInternal(CanonicalEnchantment(typeof(Sharp)).ToMutable(), 2);
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

    [Fact]
    public void CanonicalHoverCopyDoesNotWarmOriginalOrRegisterTemporaryModels()
    {
        bool existed = ModelDb.Contains(typeof(Shiv));
        if (!existed) ModelDb.Inject(typeof(Shiv));
        try
        {
            CardModel canonical = ModelDb.Card<Shiv>();
            object? vars = Field(typeof(CardModel), "_dynamicVars").GetValue(canonical);
            object? keywords = Field(typeof(CardModel), "_keywords").GetValue(canonical);
            object? pool = Field(typeof(CardModel), "_pool").GetValue(canonical);
            var registry = (Dictionary<ModelId, AbstractModel>)Field(typeof(ModelDb), "_contentById").GetValue(null)!;
            int count = registry.Count;
            CardModel expected = (CardModel)canonical.MutableClone();
            Assert.True(NativeDetachedCardPreviewFactory.TryCanonicalHoverCopy(canonical, out var copy, out var gap), gap);
            Assert.Equal(Values(expected), Values(copy!)); Assert.True(copy!.IsMutable);
            Assert.Same(vars, Field(typeof(CardModel), "_dynamicVars").GetValue(canonical));
            Assert.Same(keywords, Field(typeof(CardModel), "_keywords").GetValue(canonical));
            Assert.Same(pool, Field(typeof(CardModel), "_pool").GetValue(canonical));
            Assert.Equal(count, registry.Count); Assert.DoesNotContain(registry.Values, model => ReferenceEquals(model, copy));
            Assert.False(canonical.IsMutable); Assert.Same(canonical, copy.CanonicalInstance);
        }
        finally { if (!existed) ModelDb.Remove(typeof(Shiv)); }
    }

    [Theory]
    [InlineData(typeof(Shiv), "get_CanonicalKeywords")]
    [InlineData(typeof(DamageVar), "set_Props")]
    public void PatchedCanonicalHoverFactoryClosureNeverRuns(Type type, string method)
    {
        bool existed = ModelDb.Contains(typeof(Shiv));
        if (!existed) ModelDb.Inject(typeof(Shiv));
        try
        {
            CardModel canonical = ModelDb.Card<Shiv>();
            object? vars = Field(typeof(CardModel), "_dynamicVars").GetValue(canonical);
            object? keywords = Field(typeof(CardModel), "_keywords").GetValue(canonical);
            using var patch = new Patched(type.GetMethod(method, Flags)!);
            Assert.False(NativeDetachedCardPreviewFactory.TryCanonicalHoverCopy(canonical, out var copy, out _));
            Assert.Null(copy); Assert.Equal(0, calls);
            Assert.Same(vars, Field(typeof(CardModel), "_dynamicVars").GetValue(canonical));
            Assert.Same(keywords, Field(typeof(CardModel), "_keywords").GetValue(canonical));
        }
        finally { if (!existed) ModelDb.Remove(typeof(Shiv)); }
    }

    [Fact]
    public void UnknownCalculatedDelegateRejectsBeforeAnyInvocation()
    {
        CardModel original = Mutable<BodySlam>();
        CalculatedVar variable = original.DynamicVars.Values.OfType<CalculatedVar>().Single(); calls = 0;
        Field(typeof(CalculatedVar), "_multiplierCalc").SetValue(variable,
            new Func<CardModel, MegaCrit.Sts2.Core.Entities.Creatures.Creature?, decimal>((_, _) => { calls++; return 17; }));
        Assert.False(NativeDetachedCardPreviewFactory.TryDeckUpgrade(original, out _, out _)); Assert.Equal(0, calls);
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
        Assert.Null(result); Assert.Equal("native_preview_initialized_card_copy_facts_unqualified", gap);
        // The production guard refuses malformed native state before copying.
        // Separately force the actual native bridge failure to verify that a
        // partial NEW copy never changes the original's fields/events.
        Assert.Throws<ArgumentNullException>(() => NativeDetachedCardCopy.CreateQualifiedCopy(original));
        Assert.Same(vars, Field(typeof(CardModel), "_dynamicVars").GetValue(original));
        Assert.Null(Field(typeof(CardModel), "_temporaryStarCosts").GetValue(original));
        Assert.Null(original.Enchantment); Assert.Equal(0, original.CurrentUpgradeLevel);
    }
}

[CollectionDefinition("Native detached preview isolation", DisableParallelization = true)]
public sealed class NativeDetachedPreviewIsolation;
