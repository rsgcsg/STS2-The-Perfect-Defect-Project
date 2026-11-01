using System.Reflection;
using System.Runtime.CompilerServices;
using HarmonyLib;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Localization;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Models.Enchantments;
using MegaCrit.Sts2.Core.Models.Relics;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using STS2Connector.NativeUi;
using STS2Platform.NativeFoundation;

namespace STS2Connector;

// Source-fact/projection fixtures over exact native methods. The UI owner is
// deliberately uninitialized; this does not claim an entered Reference screen.
[Collection("Native detached preview isolation")]
public sealed class NativeDeterministicTransformPreviewTests
{
    private const BindingFlags Flags = BindingFlags.Static | BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
    private static T Bare<T>() where T : class => (T)RuntimeHelpers.GetUninitializedObject(typeof(T));
    private sealed class MaulRegistration : IDisposable
    {
        private readonly Dictionary<ModelId, AbstractModel> registry = (Dictionary<ModelId, AbstractModel>)typeof(ModelDb).GetField("_contentById", Flags)!.GetValue(null)!;
        private readonly ModelId id = ModelDb.GetId<Maul>();
        private readonly AbstractModel? previous;
        private readonly ModelId clawsId = ModelDb.GetId<Claws>();
        private readonly AbstractModel? previousClaws;
        internal MaulRegistration()
        {
            registry.TryGetValue(id, out previous); registry[id] = previous ?? new Maul();
            registry.TryGetValue(clawsId, out previousClaws); registry[clawsId] = previousClaws ?? new Claws();
        }
        public void Dispose()
        {
            if (previous != null) registry[id] = previous; else registry.Remove(id);
            if (previousClaws != null) registry[clawsId] = previousClaws; else registry.Remove(clawsId);
        }
    }
    private static CardModel Source()
    {
        bool registered = ModelDb.Contains(typeof(StrikeDefect));
        var canonical = registered ? ModelDb.Card<StrikeDefect>() : new StrikeDefect();
        if (!registered) typeof(CardModel).GetField("_canonicalInstance", Flags)!.SetValue(canonical, canonical);
        CardModel original = canonical.ToMutable();
        original.UpgradeInternal();
        var enchantment = (ModelDb.Contains(typeof(Sharp)) ? ModelDb.Enchantment<Sharp>() : new Sharp()).ToMutable();
        original.EnchantInternal(enchantment, 3); enchantment.ModifyCard();
        _ = original.DynamicVars; _ = original.EnergyCost;
        return original;
    }
    private static Func<CardModel, CardTransformation> Projection()
    {
        var relic = ModelDb.Relic<Claws>().ToMutable();
        return typeof(Claws).GetMethod("<AfterObtained>b__6_1", Flags)!
            .CreateDelegate<Func<CardModel, CardTransformation>>(relic);
    }
    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "_cardToTransformation")]
    private static extern ref Func<CardModel, CardTransformation> BoundProjection(NDeckTransformSelectScreen owner);
    private static NativeLogicalGridState State(CardModel original, Func<CardModel, CardTransformation> projection)
    {
        var owner = Bare<NDeckTransformSelectScreen>(); BoundProjection(owner) = projection;
        var prefs = new CardSelectorPrefs(new LocString("card_selection", "TO_DISCARD"), 0, 6) { RequireManualConfirmation = true };
        return new(owner, Bare<NCardGrid>(), new TaskCompletionSource<IEnumerable<CardModel>>().Task,
            new[] { original }, Array.Empty<CardModel>(), prefs, "selecting", PileType.Deck, Bare<NPeekButton>(), false,
            Array.Empty<CardModel>(), new Dictionary<string, NButton> { ["confirm_selection"] = Bare<NButton>() });
    }
    private static NativeSelectorPublicProjection.Presentation Shape(CardModel card, PileType _, CardPreviewMode __) =>
        new(new() { ["definition_id"] = card.GetType().Name, ["is_upgraded"] = card.IsUpgraded }, Array.Empty<string>());

    [Fact]
    public void ExactNativeClawsAfterRetainsUpgradeEnchantmentAndOriginalSubjectInReferenceProducer()
    {
        using var registry = new MaulRegistration();
        CardModel original = Source(); var projection = Projection();
        Assert.True(NativeDetachedCardPreviewFactory.TryDeterministicTransform(original, projection, out var preview, out var gap), gap);
        Assert.IsType<Maul>(preview!.Display); Assert.Null(preview.Display.Owner);
        Assert.True(preview.Display.IsUpgraded); Assert.IsType<Sharp>(preview.Display.Enchantment);
        Assert.Equal(3, preview.Display.Enchantment!.Amount); Assert.Equal(PileType.Deck, preview.DisplayPile);
        NativeSelectorPreviewFacts facts = NativeSelectorPublicFacts.Capture(State(original, projection), originalOnlyTransform: true);
        Assert.True(facts.ModelFactsReady, string.Join(",", facts.Missing.Concat(facts.Candidates.SelectMany(row => row.Missing))));
        Assert.True(facts.RequiresAggregateAfterPresentation);
        var result = NativeSelectorPublicProjection.Project(facts, "owner", "request", card => {
            Assert.Same(original, card); return "source-subject";
        }, PileType.Deck, Shape);
        Assert.Empty(result.Missing);
        Assert.Equal("deterministic_native_after", result.Request["transformation_preview"]!.GetValue<string>());
        Assert.Equal("source-subject", result.Options[0]!["before"]!["entity_id"]!.GetValue<string>());
        Assert.Equal("source-subject", result.Options[0]!["after"]!["entity_id"]!.GetValue<string>());
        Assert.Equal("native_deterministic_transform_after", result.Options[0]!["display_context"]!.GetValue<string>());
        Assert.Equal("available", result.Options[0]!["after_availability"]!.GetValue<string>());
    }
    private static int calls;
    private static void UnexpectedFactory() => calls++;

    [Fact]
    public void ForeignDelegateAndPatchedNativeFactoryAreRefusedBeforeInvocation()
    {
        using var registry = new MaulRegistration(); CardModel original = Source(); calls = 0;
        Func<CardModel, CardTransformation> foreign = card => { calls++; return new(card); };
        Assert.False(NativeDetachedCardPreviewFactory.TryDeterministicTransform(original, foreign, out _, out _));
        Assert.Equal(0, calls);
        var projection = Projection(); var harmony = new Harmony("claws-read-negative-" + Guid.NewGuid().ToString("N"));
        var factory = typeof(Claws).GetMethod("CreateMaulFromOriginal", Flags)!;
        harmony.Patch(factory, prefix: new HarmonyMethod(typeof(NativeDeterministicTransformPreviewTests).GetMethod(nameof(UnexpectedFactory), Flags)!));
        try
        {
            Assert.False(NativeDetachedCardPreviewFactory.TryDeterministicTransform(original, projection, out _, out _));
            var facts = NativeSelectorPublicFacts.Capture(State(original, projection), originalOnlyTransform: true);
            Assert.False(facts.Request.MechanicsQualified); Assert.Null(facts.TransformationPreview);
            Assert.Equal(0, calls);
        }
        finally { harmony.UnpatchAll(harmony.Id); }
    }

    [Fact]
    public void RandomDefaultTransformationKeepsItsOriginalOnlyPublicShape()
    {
        var method = NativeSelectorPublicFacts.DefaultTransformProjectionMethod!;
        object singleton = method.DeclaringType!.GetField("<>9", Flags)!.GetValue(null)!;
        var projection = method.CreateDelegate<Func<CardModel, CardTransformation>>(singleton);
        NativeSelectorPreviewFacts facts = NativeSelectorPublicFacts.Capture(State(Source(), projection), originalOnlyTransform: true);
        Assert.Null(facts.TransformationPreview); Assert.False(facts.RequiresAggregateAfterPresentation);
        var result = NativeSelectorPublicProjection.Project(facts, "owner", "request", _ => "subject", PileType.Deck, Shape);
        Assert.False(result.Request.ContainsKey("transformation_preview"));
        Assert.Null(result.Options[0]!["after"]);
        Assert.Equal("not_applicable", result.Options[0]!["after_availability"]!.GetValue<string>());
        Assert.Equal("native_original_only", result.Options[0]!["display_context"]!.GetValue<string>());
    }
}
