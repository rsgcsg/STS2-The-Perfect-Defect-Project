using System.Reflection;
using System.Runtime.CompilerServices;
using Godot;
using HarmonyLib;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Localization;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using STS2Connector.NativeUi;
using STS2Connector.PlayerEnvironment;
using STS2Platform.NativeFoundation;

namespace STS2Connector;

// Exact native types/preferences/field seam, with deliberately uninitialized
// UI owners: this tests source-fact projection, not an entered Godot screen or
// native callback delivery. No filter or selection callback is invoked.
[Collection("Native detached preview isolation")]
public sealed class NativeSelectorPublicFactsTests
{
    private static T Bare<T>() where T : class => (T)RuntimeHelpers.GetUninitializedObject(typeof(T));
    private static NativeLogicalGridState State(Type type, int count = 2, Task? completion = null,
        bool manual = true, string[]? controls = null, int selected = 0)
    {
        var owner = (NCardGridSelectionScreen)RuntimeHelpers.GetUninitializedObject(type);
        CardModel[] cards = Enumerable.Range(0, count).Select(_ => (CardModel)new DefendDefect()).ToArray();
        var prefs = new CardSelectorPrefs(new LocString("card_selection", "TO_DISCARD"), 0, count) { RequireManualConfirmation = manual };
        return new(owner, Bare<NCardGrid>(), completion ?? new TaskCompletionSource<IEnumerable<CardModel>>().Task,
            cards, cards.Take(selected).ToArray(), prefs, "selecting", PileType.Deck, Bare<NPeekButton>(), false,
            Array.Empty<CardModel>(), (controls ?? Array.Empty<string>()).ToDictionary(verb => verb, _ => Bare<NButton>()));
    }

    [Theory]
    [InlineData(typeof(NDeckCardSelectScreen), "deck_card", "native_preview_at_max")]
    [InlineData(typeof(NDeckUpgradeSelectScreen), "deck_upgrade", "native_preview_at_max")]
    [InlineData(typeof(NDeckEnchantSelectScreen), "deck_enchant", "native_preview_at_max")]
    [InlineData(typeof(NSimpleCardSelectScreen), "simple", "native_manual_completion")]
    [InlineData(typeof(NCombatPileCardSelectScreen), "combat_pile", "native_manual_completion_with_dynamic_roster")]
    public void ExactNativeFamilyHasItsOwnCompletionRule(Type type, string family, string transition)
    {
        Assert.True(NativeSelectorPublicFacts.MechanicsQualified(type));
        Assert.Equal(family, NativeSelectorPublicFacts.Family(type));
        Assert.Equal(transition, NativeSelectorPublicFacts.AddTransition(type, manual: true));
    }

    [Fact]
    public void FullRosterAndExistingCompletionTaskAreRetainedWithoutANewTracker()
    {
        NativeLogicalGridState state = State(typeof(NDeckCardSelectScreen), count: 64, selected: 1);
        NativeSelectorPreviewFacts facts = NativeSelectorPublicFacts.Capture(state);
        Assert.Same(state.Owner, facts.Request.Owner); Assert.Same(state.Completion, facts.Request.Completion);
        Assert.Equal(64, facts.Candidates.Count); Assert.Equal(state.Cards, facts.Candidates.Select(row => row.Original));
        Assert.Equal(state.Selected, facts.Request.Selected); Assert.All(facts.Candidates, row => Assert.Null(row.Detached));
        Assert.False(facts.RequiresAggregateAfterPresentation);
    }

    [Fact]
    public void SameScreenAndRosterWithNewNativeTaskIsAnotherOccurrence()
    {
        NativeLogicalGridState first = State(typeof(NDeckCardSelectScreen));
        var nextTask = new TaskCompletionSource<IEnumerable<CardModel>>().Task;
        NativeLogicalGridState next = first with { Completion = nextTask };
        NativeSelectorPreviewFacts a = NativeSelectorPublicFacts.Capture(first), b = NativeSelectorPublicFacts.Capture(next);
        Assert.Same(a.Request.Owner, b.Request.Owner); Assert.Same(a.Request.Cards, b.Request.Cards);
        Assert.NotSame(a.Request.Completion, b.Request.Completion); Assert.Same(nextTask, b.Request.Completion);
    }

    [Fact]
    public void FinishAndEmptyOutcomeComeFromCurrentControlsRatherThanMinMax()
    {
        NativeSelectorPreviewFacts none = NativeSelectorPublicFacts.Capture(State(typeof(NDeckCardSelectScreen)));
        Assert.False(none.Request.CanFinishNow); Assert.False(none.Request.CanDeclineNow);
        NativeSelectorPreviewFacts finish = NativeSelectorPublicFacts.Capture(State(typeof(NDeckCardSelectScreen), controls: new[] { "open_preview" }));
        Assert.True(finish.Request.CanFinishNow); Assert.True(finish.Request.CanDeclineNow);
        NativeSelectorPreviewFacts close = NativeSelectorPublicFacts.Capture(State(typeof(NDeckCardSelectScreen), controls: new[] { "cancel_selection" }, selected: 1));
        Assert.True(close.Request.CanDeclineNow); Assert.False(close.Request.CanFinishNow);
    }

    [UnsafeAccessor(UnsafeAccessorKind.Field, Name = "_cardRows")]
    private static extern ref List<List<NGridCardHolder>> GridRows(NCardGrid grid);

    [Fact]
    public void DynamicPileKeepsActualHolderCountSeparateFromFullLogicalRoster()
    {
        NativeLogicalGridState state = State(typeof(NCombatPileCardSelectScreen), count: 64, manual: false);
        GridRows(state.Grid) = new() { new() { Bare<NGridCardHolder>(), Bare<NGridCardHolder>() }, new() { Bare<NGridCardHolder>() } };
        NativeSelectorPreviewFacts facts = NativeSelectorPublicFacts.Capture(state);
        Assert.Equal(64, facts.Request.Cards.Count); Assert.Equal(3, facts.Request.DisplayedHolderCount);
        Assert.Equal("native_completion_with_display_count_clamp", facts.Request.AddTransition);
        Assert.Equal(64, facts.Candidates.Count);
    }

    private static int calls;
    private static bool UnexpectedPrefsGetter() { calls++; return true; }

    [Fact]
    public void PatchedNativePrefsGetterIsRefusedBeforeReadingAndRosterIsNotDropped()
    {
        NativeLogicalGridState state = State(typeof(NDeckCardSelectScreen), count: 8);
        MethodInfo getter = typeof(CardSelectorPrefs).GetProperty(nameof(CardSelectorPrefs.MinSelect))!.GetMethod!;
        var harmony = new Harmony("selector-public-facts-negative-" + Guid.NewGuid().ToString("N")); calls = 0;
        harmony.Patch(getter, prefix: new HarmonyMethod(typeof(NativeSelectorPublicFactsTests).GetMethod(nameof(UnexpectedPrefsGetter), BindingFlags.Static | BindingFlags.NonPublic)!));
        try
        {
            NativeSelectorPreviewFacts facts = NativeSelectorPublicFacts.Capture(state);
            Assert.False(facts.Request.MechanicsQualified); Assert.Null(facts.Request.MinSelect); Assert.Equal(0, calls);
            Assert.Equal(8, facts.Candidates.Count); Assert.Equal(state.Cards, facts.Request.Cards);
            Assert.All(facts.Candidates, row => Assert.False(row.ModelReady));
            Assert.Contains("native_selector_mechanics_call_closure_unqualified", facts.Missing);
        }
        finally { harmony.Unpatch(getter, HarmonyPatchType.Prefix, harmony.Id); }
    }

    [Fact]
    public void TransformDelegateIsNotInvokedOrQualifiedAsGenericStaging()
    {
        NativeSelectorPreviewFacts facts = NativeSelectorPublicFacts.Capture(State(typeof(NDeckTransformSelectScreen), count: 3));
        Assert.False(facts.Request.MechanicsQualified); Assert.Equal(3, facts.Candidates.Count);
        Assert.All(facts.Candidates, row => Assert.False(row.ModelReady));
    }

    [Fact]
    public void CompletedOriginalTaskCannotAdvertiseFinishOrDecline()
    {
        NativeSelectorPreviewFacts facts = NativeSelectorPublicFacts.Capture(State(typeof(NDeckCardSelectScreen),
            completion: Task.CompletedTask, controls: new[] { "confirm", "cancel_selection" }));
        Assert.False(facts.Request.CanFinishNow); Assert.False(facts.Request.CanDeclineNow);
    }

    private static NativeSelectorPublicProjection.Presentation RenderShape(CardModel _, PileType pile, CardPreviewMode mode) =>
        new(new() { ["entity_id"] = "must_be_replaced", ["name"] = "native text", ["description"] = "native description" }, Array.Empty<string>());

    [Fact]
    public void PublicProjectionRetainsEveryOriginalAndUsesTheExistingRequestIdentity()
    {
        NativeLogicalGridState state = State(typeof(NDeckCardSelectScreen), count: 64, selected: 1);
        NativeSelectorPreviewFacts facts = NativeSelectorPublicFacts.Capture(state);
        var aliases = state.Cards.Select((card, index) => (card, id: "original-" + index)).ToDictionary(pair => pair.card, pair => pair.id);
        var result = NativeSelectorPublicProjection.Project(facts, "same-owner", "same-original-task", card => aliases[card], PileType.Deck, RenderShape);
        Assert.Equal(64, result.Options.Count); Assert.Empty(result.Missing);
        Assert.Equal("same-original-task", result.Request["entity_id"]!.GetValue<string>());
        Assert.True(result.Request["preview_information_complete"]!.GetValue<bool>());
        Assert.Equal("original-0", result.Request["selected_entity_ids"]![0]!.GetValue<string>());
        foreach (var option in result.Options)
        {
            Assert.Equal(option!["subject_entity_id"]!.GetValue<string>(), option["before"]!["entity_id"]!.GetValue<string>());
            Assert.Null(option["after"]); Assert.Equal("not_applicable", option["after_availability"]!.GetValue<string>());
        }
    }

    [Fact]
    public void DetachedAfterPresentationKeepsOriginalSubjectAndExactNativeDisplayContext()
    {
        NativeLogicalGridState state = State(typeof(NDeckCardSelectScreen), count: 1);
        NativeSelectorPreviewFacts facts = NativeSelectorPublicFacts.Capture(state);
        CardModel detached = new DefendDefect();
        var preview = new NativeDetachedCardPreview(state.Cards[0], detached, NativeDetachedPreviewKind.DeckUpgrade, PileType.None, CardPreviewMode.Upgrade);
        facts = facts with { RequiresAggregateAfterPresentation = true, Candidates = new[] { new NativeSelectorCandidatePreview(state.Cards[0], preview, true, "native_deck_upgrade_after", Array.Empty<string>()) } };
        int originals = 0, afters = 0;
        var result = NativeSelectorPublicProjection.Project(facts, "owner", "original-task", card =>
        {
            Assert.Same(state.Cards[0], card); return "original-subject";
        }, PileType.Deck, (card, pile, mode) =>
        {
            if (ReferenceEquals(card, detached)) { afters++; Assert.Equal(PileType.None, pile); Assert.Equal(CardPreviewMode.Upgrade, mode); }
            else { originals++; Assert.Same(state.Cards[0], card); Assert.Equal(PileType.Deck, pile); Assert.Equal(CardPreviewMode.Normal, mode); }
            return RenderShape(card, pile, mode);
        });
        Assert.Equal(1, originals); Assert.Equal(1, afters);
        Assert.Equal("original-subject", result.Options[0]!["after"]!["entity_id"]!.GetValue<string>());
        Assert.Equal("available", result.Options[0]!["after_availability"]!.GetValue<string>());
    }

    [Fact]
    public void MissingRequiredAfterOrBeforeTextMakesWholeAggregateIncompleteWithoutDroppingRows()
    {
        NativeLogicalGridState state = State(typeof(NDeckCardSelectScreen), count: 64);
        NativeSelectorPreviewFacts facts = NativeSelectorPublicFacts.Capture(state) with { RequiresAggregateAfterPresentation = true };
        var result = NativeSelectorPublicProjection.Project(facts, "owner", "task", _ => "original", PileType.Deck,
            (_, _, _) => new(new() { ["description"] = null }, new[] { "native_required_description_gap" }));
        Assert.Equal(64, result.Options.Count);
        Assert.False(result.Request["preview_information_complete"]!.GetValue<bool>());
        Assert.Contains("native_selector_required_after_presentation_unavailable", result.Missing);
        Assert.Contains("native_required_description_gap", result.Missing);
        Assert.All(result.Options, option => Assert.Equal("unavailable", option!["after_availability"]!.GetValue<string>()));
    }
    private static Func<CardModel, CardTransformation> NativeDefaultTransformProjection()
    {
        MethodInfo? method = NativeSelectorPublicFacts.DefaultTransformProjectionMethod;
        Assert.NotNull(method);
        Assert.Equal(typeof(CardSelectCmd).Module, method.Module);
        Assert.Equal("<FromDeckForTransformation>b__22_1", method.Name);
        return (Func<CardModel, CardTransformation>)method.CreateDelegate(typeof(Func<CardModel, CardTransformation>),
            RuntimeHelpers.GetUninitializedObject(method.DeclaringType!));
    }

    [Fact]
    public void PublicBasisDefaultTransformCapturesOnlyOriginalsWithoutInvokingProjectionAndDoesNotQualifySourcePreview()
    {
        var state = State(typeof(NDeckTransformSelectScreen), count: 3);
        var projection = NativeDefaultTransformProjection();
        typeof(NDeckTransformSelectScreen).GetField("_cardToTransformation", BindingFlags.Instance | BindingFlags.NonPublic)!
            .SetValue(state.Owner, projection);
        Assert.True(NativeSelectorPublicFacts.DefaultTransformProjectionQualified(projection));
        var pair = NativeLogicalGridCapture.CaptureProjectionFacts(state, publicBasisOnly: false);
        var facts = pair.PublicBasis;
        Assert.True(facts.ModelFactsReady); Assert.Empty(facts.Missing);
        Assert.Equal("deck_transform", facts.Request.Family);
        Assert.Equal("native_preview_at_max", facts.Request.AddTransition);
        Assert.False(facts.RequiresAggregateAfterPresentation);
        Assert.All(facts.Candidates, candidate => Assert.Null(candidate.Detached));
        Assert.False(NativeSelectorPublicFacts.Capture(state).Request.MechanicsQualified);
        Assert.False(pair.Native.Request.MechanicsQualified);
        Assert.Same(facts.Request.Owner, pair.Native.Request.Owner);
        Assert.Same(facts.Request.Completion, pair.Native.Request.Completion);
        Assert.Equal(state.Cards, pair.Native.Candidates.Select(candidate => candidate.Original));
        var result = NativeSelectorPublicProjection.Project(facts, "owner", "task", _ => "original", PileType.Deck, RenderShape);
        Assert.All(result.Options, option => { Assert.Null(option!["after"]); Assert.Equal("not_applicable", option["after_availability"]!.GetValue<string>()); });
    }

    [Fact]
    public void PublicBasisTransformRefusesCustomAndMulticastProjectionsBeforeAnyInvocation()
    {
        var state = State(typeof(NDeckTransformSelectScreen));
        int calls = 0;
        Func<CardModel, CardTransformation> custom = card => { calls++; return new CardTransformation(card); };
        typeof(NDeckTransformSelectScreen).GetField("_cardToTransformation", BindingFlags.Instance | BindingFlags.NonPublic)!
            .SetValue(state.Owner, custom);
        var facts = NativeSelectorPublicFacts.Capture(state, originalOnlyTransform: true);
        Assert.False(facts.Request.MechanicsQualified);
        Assert.Contains("custom_transform_preview_unqualified", facts.Missing);
        Assert.Equal(0, calls);
        var combined = (Func<CardModel, CardTransformation>)Delegate.Combine(NativeDefaultTransformProjection(), custom);
        Assert.False(NativeSelectorPublicFacts.DefaultTransformProjectionQualified(combined));
        Assert.Equal(0, calls);
    }

}
