using System.Reflection;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Rewards;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Rewards;
using STS2Platform.NativeFoundation;

namespace STS2Connector.Host.Tests;

public sealed class NativeCardRewardParentFactsTests
{
    [Fact]
    public void ExactSynchronousScreenBindingKeepsNativeParentAndTypedSkip()
    {
        CardCreationResult option = Option();
        CardReward parent = Parent(option);
        NCardRewardSelectionScreen screen = Bare<NCardRewardSelectionScreen>();
        CardRewardAlternative skip = Alternative(
            "Skip", PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward);

        using (NativeCardRewardDecisionProvider.BeginSynchronousOnSelect(parent))
            NativeCardRewardDecisionProvider.RegisterFromShowScreen(
                screen, Options(parent), new[] { skip });

        NativeCardRewardParentFacts fact = NativeCardRewardDecisionProvider.CaptureParentFacts(screen);
        Assert.Equal("captured", fact.Status);
        Assert.Same(parent, fact.ParentReward);
        NativeCardRewardAlternativeFact alternative = Assert.Single(fact.Alternatives);
        Assert.Same(skip, alternative.Alternative);
        Assert.Equal("Skip", alternative.OptionId);
        Assert.Equal(PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward,
            alternative.AfterSelected);

        NCardRewardSelectionScreen outside = Bare<NCardRewardSelectionScreen>();
        NativeCardRewardDecisionProvider.RegisterFromShowScreen(
            outside, Options(parent), new[] { skip });
        Assert.Equal("parent_not_bound",
            NativeCardRewardDecisionProvider.CaptureParentFacts(outside).Status);
    }

    [Fact]
    public void WrongCardsAndSecondShowScreenFailClosedInsteadOfBorrowingParent()
    {
        CardCreationResult owned = Option();
        CardCreationResult other = Option();
        Assert.Equal(owned.Card.GetType(), other.Card.GetType());
        Assert.NotSame(owned.Card, other.Card);
        CardReward parent = Parent(owned);
        NCardRewardSelectionScreen mismatch = Bare<NCardRewardSelectionScreen>();
        using (NativeCardRewardDecisionProvider.BeginSynchronousOnSelect(parent))
            NativeCardRewardDecisionProvider.RegisterFromShowScreen(
                mismatch, new[] { other }, Array.Empty<CardRewardAlternative>());
        Assert.Equal("ambiguous_show_screen",
            NativeCardRewardDecisionProvider.CaptureParentFacts(mismatch).Status);

        NCardRewardSelectionScreen copiedList = Bare<NCardRewardSelectionScreen>();
        using (NativeCardRewardDecisionProvider.BeginSynchronousOnSelect(parent))
            NativeCardRewardDecisionProvider.RegisterFromShowScreen(
                copiedList, new[] { owned }, Array.Empty<CardRewardAlternative>());
        Assert.Equal("ambiguous_show_screen",
            NativeCardRewardDecisionProvider.CaptureParentFacts(copiedList).Status);

        NCardRewardSelectionScreen first = Bare<NCardRewardSelectionScreen>();
        NCardRewardSelectionScreen second = Bare<NCardRewardSelectionScreen>();
        using (NativeCardRewardDecisionProvider.BeginSynchronousOnSelect(parent))
        {
            NativeCardRewardDecisionProvider.RegisterFromShowScreen(
                first, Options(parent), Array.Empty<CardRewardAlternative>());
            NativeCardRewardDecisionProvider.RegisterFromShowScreen(
                second, Options(parent), Array.Empty<CardRewardAlternative>());
        }
        Assert.Equal("ambiguous_show_screen",
            NativeCardRewardDecisionProvider.CaptureParentFacts(first).Status);
        Assert.Equal("ambiguous_show_screen",
            NativeCardRewardDecisionProvider.CaptureParentFacts(second).Status);
    }

    [Fact]
    public void RefreshPreservesOnlyExactParentAndRejectsOutcomeDrift()
    {
        CardCreationResult option = Option();
        CardReward parent = Parent(option);
        NCardRewardSelectionScreen screen = Bare<NCardRewardSelectionScreen>();
        CardRewardAlternative skip = Alternative(
            "Skip", PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward);
        using (NativeCardRewardDecisionProvider.BeginSynchronousOnSelect(parent))
            NativeCardRewardDecisionProvider.RegisterFromShowScreen(
                screen, Options(parent), new[] { skip });

        Options(parent)[0] = Option();
        Assert.Equal("parent_options_mismatch",
            NativeCardRewardDecisionProvider.CaptureParentFacts(screen).Status);
        NativeCardRewardDecisionProvider.Refresh(screen, Options(parent), new[] { skip });
        Assert.Equal("captured",
            NativeCardRewardDecisionProvider.CaptureParentFacts(screen).Status);

        CardRewardAlternative reroll = Alternative(
            "REROLL", PostAlternateCardRewardAction.DoNothing);
        NativeCardRewardDecisionProvider.Refresh(screen, Options(parent), new[] { reroll });
        NativeCardRewardParentFacts updated = NativeCardRewardDecisionProvider.CaptureParentFacts(screen);
        Assert.Same(parent, updated.ParentReward);
        Assert.Equal(PostAlternateCardRewardAction.DoNothing,
            Assert.Single(updated.Alternatives).AfterSelected);

        CardRewardAlternative sameNameDifferentInstance = Alternative(
            "REROLL", PostAlternateCardRewardAction.DoNothing);
        NativeCardRewardDecisionProvider.Refresh(
            screen, Options(parent), new[] { sameNameDifferentInstance });
        Assert.Same(sameNameDifferentInstance,
            Assert.Single(NativeCardRewardDecisionProvider.CaptureParentFacts(screen).Alternatives).Alternative);

        NativeCardRewardDecisionProvider.Refresh(screen, Options(parent), new[] { reroll });

        SetField(reroll, "<AfterSelected>k__BackingField",
            PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward);
        Assert.Equal("alternative_outcome_changed",
            NativeCardRewardDecisionProvider.CaptureParentFacts(screen).Status);

        NativeCardRewardDecisionProvider.Refresh(
            screen, new[] { Option() }, new[] { reroll });
        Assert.Equal("parent_options_mismatch",
            NativeCardRewardDecisionProvider.CaptureParentFacts(screen).Status);
    }

    [Fact]
    public void FailedRefreshInvalidatesPreviouslyBoundParent()
    {
        CardReward parent = Parent(Option());
        NCardRewardSelectionScreen screen = Bare<NCardRewardSelectionScreen>();
        using (NativeCardRewardDecisionProvider.BeginSynchronousOnSelect(parent))
            NativeCardRewardDecisionProvider.RegisterFromShowScreen(
                screen, Options(parent), Array.Empty<CardRewardAlternative>());

        Assert.Throws<NullReferenceException>(() => NativeCardRewardDecisionProvider.Refresh(
            screen, Options(parent), new CardRewardAlternative[] { null! }));
        Assert.Equal("refresh_registration_failed",
            NativeCardRewardDecisionProvider.CaptureParentFacts(screen).Status);
    }

    [Fact]
    public void NestedScopesRestoreOnlyTheirOwnSynchronousParent()
    {
        CardCreationResult outerOption = Option();
        CardCreationResult innerOption = Option();
        CardReward outerParent = Parent(outerOption);
        CardReward innerParent = Parent(innerOption);
        NCardRewardSelectionScreen outerScreen = Bare<NCardRewardSelectionScreen>();
        NCardRewardSelectionScreen innerScreen = Bare<NCardRewardSelectionScreen>();

        using (NativeCardRewardDecisionProvider.BeginSynchronousOnSelect(outerParent))
        {
            using (NativeCardRewardDecisionProvider.BeginSynchronousOnSelect(innerParent))
                NativeCardRewardDecisionProvider.RegisterFromShowScreen(
                    innerScreen, Options(innerParent), Array.Empty<CardRewardAlternative>());
            NativeCardRewardDecisionProvider.RegisterFromShowScreen(
                outerScreen, Options(outerParent), Array.Empty<CardRewardAlternative>());
        }

        Assert.Same(innerParent,
            NativeCardRewardDecisionProvider.CaptureParentFacts(innerScreen).ParentReward);
        Assert.Same(outerParent,
            NativeCardRewardDecisionProvider.CaptureParentFacts(outerScreen).ParentReward);
    }

    [Fact]
    public void ExceptionAndCrossThreadDisposalLeaveNoAmbientParent()
    {
        CardCreationResult option = Option();
        CardReward parent = Parent(option);
        NCardRewardSelectionScreen afterException = Bare<NCardRewardSelectionScreen>();
        try
        {
            using (NativeCardRewardDecisionProvider.BeginSynchronousOnSelect(parent))
                throw new InvalidOperationException("synthetic OnSelect failure");
        }
        catch (InvalidOperationException)
        {
            NativeCardRewardDecisionProvider.RegisterFromShowScreen(
                afterException, Options(parent), Array.Empty<CardRewardAlternative>());
        }
        Assert.Equal("parent_not_bound",
            NativeCardRewardDecisionProvider.CaptureParentFacts(afterException).Status);

        IDisposable scope = NativeCardRewardDecisionProvider.BeginSynchronousOnSelect(parent);
        try
        {
            NCardRewardSelectionScreen otherThread = Bare<NCardRewardSelectionScreen>();
            var worker = new Thread(() => NativeCardRewardDecisionProvider.RegisterFromShowScreen(
                otherThread, Options(parent), Array.Empty<CardRewardAlternative>()));
            worker.Start();
            Assert.True(worker.Join(TimeSpan.FromSeconds(5)));
            Assert.Equal("parent_not_bound",
                NativeCardRewardDecisionProvider.CaptureParentFacts(otherThread).Status);

            var disposer = new Thread(scope.Dispose);
            disposer.Start();
            Assert.True(disposer.Join(TimeSpan.FromSeconds(5)));
            NCardRewardSelectionScreen afterCrossThreadDispose = Bare<NCardRewardSelectionScreen>();
            NativeCardRewardDecisionProvider.RegisterFromShowScreen(
                afterCrossThreadDispose, Options(parent), Array.Empty<CardRewardAlternative>());
            Assert.Equal("parent_not_bound",
                NativeCardRewardDecisionProvider.CaptureParentFacts(afterCrossThreadDispose).Status);
        }
        finally
        {
            scope.Dispose();
        }
    }

    [Fact]
    public void OutOfOrderNestedCleanupInvalidatesPreviouslyBoundScreen()
    {
        CardCreationResult outerOption = Option();
        CardCreationResult innerOption = Option();
        CardReward outerParent = Parent(outerOption);
        CardReward innerParent = Parent(innerOption);
        IDisposable outer = NativeCardRewardDecisionProvider.BeginSynchronousOnSelect(outerParent);
        IDisposable inner = NativeCardRewardDecisionProvider.BeginSynchronousOnSelect(innerParent);
        try
        {
            NCardRewardSelectionScreen innerScreen = Bare<NCardRewardSelectionScreen>();
            NativeCardRewardDecisionProvider.RegisterFromShowScreen(
                innerScreen, Options(innerParent), Array.Empty<CardRewardAlternative>());

            outer.Dispose();
            inner.Dispose();
            Assert.Equal("selection_scope_out_of_order",
                NativeCardRewardDecisionProvider.CaptureParentFacts(innerScreen).Status);
            NCardRewardSelectionScreen next = Bare<NCardRewardSelectionScreen>();
            NativeCardRewardDecisionProvider.RegisterFromShowScreen(
                next, Options(outerParent), Array.Empty<CardRewardAlternative>());
            Assert.Equal("parent_not_bound",
                NativeCardRewardDecisionProvider.CaptureParentFacts(next).Status);
        }
        finally
        {
            inner.Dispose();
            outer.Dispose();
        }
    }

    private static CardReward Parent(params CardCreationResult[] options)
    {
        CardReward reward = Bare<CardReward>();
        SetField(reward, "_cards", options.ToList());
        return reward;
    }

    private static List<CardCreationResult> Options(CardReward parent) =>
        (List<CardCreationResult>)typeof(CardReward)
            .GetField("_cards", BindingFlags.Instance | BindingFlags.NonPublic)!
            .GetValue(parent)!;

    private static CardCreationResult Option()
    {
        Type type = typeof(CardModel).Assembly.GetTypes().First(value =>
            !value.IsAbstract && value.IsSubclassOf(typeof(CardModel))
            && !value.ContainsGenericParameters);
        return new CardCreationResult((CardModel)RuntimeHelpers.GetUninitializedObject(type));
    }

    private static CardRewardAlternative Alternative(
        string optionId, PostAlternateCardRewardAction afterSelected)
    {
        CardRewardAlternative alternative = Bare<CardRewardAlternative>();
        SetField(alternative, "<OptionId>k__BackingField", optionId);
        SetField(alternative, "<AfterSelected>k__BackingField", afterSelected);
        return alternative;
    }

    private static T Bare<T>() =>
        (T)RuntimeHelpers.GetUninitializedObject(typeof(T));

    private static void SetField(object target, string name, object value) =>
        target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic)!
            .SetValue(target, value);
}
