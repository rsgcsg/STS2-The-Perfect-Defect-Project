using System.Reflection;
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.Rewards;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using STS2Connector.NativeUi;

namespace STS2Connector.Host.Tests;

public sealed class CardRewardAlternativePresentationBindingsTests
{
    [Fact]
    public void SameLabelAndReversedVisualOrderStillDeliverNativeCreationTarget()
    {
        NCardRewardSelectionScreen screen = Bare<NCardRewardSelectionScreen>();
        CardRewardAlternative first = Alternative("Skip", PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward);
        CardRewardAlternative second = Alternative("Skip", PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward);
        object firstButton = new();
        object secondButton = new();

        var oldPositionOrder = new[]
        {
            (Button: firstButton, X: 100),
            (Button: secondButton, X: 0)
        }.OrderBy(entry => entry.X).Select(entry => entry.Button).ToArray();
        Assert.Same(secondButton, oldPositionOrder[0]); // Old index-based delivery is wrong.

        var alternatives = new[] { first, second };
        Assert.False(CardRewardAlternativePresentationBindings.TryResolveButton(
            screen, first, alternatives, new[] { secondButton, firstButton }, out _));
        var scope = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
        CardRewardAlternativePresentationBindings.ObserveCreated(firstButton);
        CardRewardAlternativePresentationBindings.ObserveCreated(secondButton);
        scope.Finish(nativeSucceeded: true);

        Assert.True(CardRewardAlternativePresentationBindings.TryResolveButton(
            screen, first, alternatives, new[] { secondButton, firstButton }, out var delivered));
        Assert.Same(firstButton, delivered);
        Assert.True(CardRewardAlternativePresentationBindings.TryResolveButton(
            screen, second, alternatives, new[] { secondButton, firstButton }, out delivered));
        Assert.Same(secondButton, delivered);
    }

    [Fact]
    public void MissingExtraDuplicateAndChangedNativeListHaveNoBinding()
    {
        var screen = Bare<NCardRewardSelectionScreen>();
        var first = Alternative("Skip", PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward);
        var second = Alternative("Reroll", PostAlternateCardRewardAction.DoNothing);
        object firstButton = new();
        object secondButton = new();
        var alternatives = new List<CardRewardAlternative> { first, second };

        var missing = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
        CardRewardAlternativePresentationBindings.ObserveCreated(firstButton);
        missing.Finish(nativeSucceeded: true);
        Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
            screen, alternatives, new[] { firstButton }, out _));

        var duplicate = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
        CardRewardAlternativePresentationBindings.ObserveCreated(firstButton);
        CardRewardAlternativePresentationBindings.ObserveCreated(firstButton);
        duplicate.Finish(nativeSucceeded: true);
        Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
            screen, alternatives, new[] { firstButton, secondButton }, out _));

        var nullCreated = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
        CardRewardAlternativePresentationBindings.ObserveCreated(firstButton);
        CardRewardAlternativePresentationBindings.ObserveCreated(null);
        nullCreated.Finish(nativeSucceeded: true);
        Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
            screen, alternatives, new[] { firstButton, secondButton }, out _));

        var extra = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
        CardRewardAlternativePresentationBindings.ObserveCreated(firstButton);
        CardRewardAlternativePresentationBindings.ObserveCreated(secondButton);
        CardRewardAlternativePresentationBindings.ObserveCreated(new object());
        extra.Finish(nativeSucceeded: true);
        Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
            screen, alternatives, new[] { firstButton, secondButton }, out _));

        var complete = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
        CardRewardAlternativePresentationBindings.ObserveCreated(firstButton);
        CardRewardAlternativePresentationBindings.ObserveCreated(secondButton);
        complete.Finish(nativeSucceeded: true);
        Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
            screen, alternatives, new[] { firstButton }, out _)); // Deferred add.
        Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
            screen, alternatives, new[] { firstButton, secondButton, new object() }, out _)); // Old child still mounted.
        Assert.True(CardRewardAlternativePresentationBindings.TryCapture(
            screen, alternatives, new[] { firstButton, secondButton }, out _));
        Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
            screen, new[] { second, first }, new[] { firstButton, secondButton }, out _));
        alternatives[0] = second;
        Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
            screen, alternatives, new[] { firstButton, secondButton }, out _));
    }

    [Fact]
    public void NestedRefreshAndExceptionInvalidateBothGenerations()
    {
        var firstScreen = Bare<NCardRewardSelectionScreen>();
        var secondScreen = Bare<NCardRewardSelectionScreen>();
        var first = Alternative("Skip", PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward);
        var second = Alternative("Reroll", PostAlternateCardRewardAction.DoNothing);
        object firstButton = new();
        object secondButton = new();

        var outer = CardRewardAlternativePresentationBindings.BeginRefresh(firstScreen, new[] { first });
        CardRewardAlternativePresentationBindings.ObserveCreated(firstButton);
        var inner = CardRewardAlternativePresentationBindings.BeginRefresh(secondScreen, new[] { second });
        CardRewardAlternativePresentationBindings.ObserveCreated(secondButton);
        inner.Finish(nativeSucceeded: true);
        outer.Finish(nativeSucceeded: true);
        Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
            firstScreen, new[] { first }, new[] { firstButton }, out _));
        Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
            secondScreen, new[] { second }, new[] { secondButton }, out _));

        var failed = CardRewardAlternativePresentationBindings.BeginRefresh(firstScreen, new[] { first });
        CardRewardAlternativePresentationBindings.ObserveCreated(firstButton);
        failed.Finish(nativeSucceeded: false);
        Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
            firstScreen, new[] { first }, new[] { firstButton }, out _));
    }

    [Fact]
    public void BeginningAnotherGenerationRevokesPreviouslyCompleteBindingImmediately()
    {
        var screen = Bare<NCardRewardSelectionScreen>();
        var alternative = Alternative("Skip", PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward);
        object oldButton = new();
        object newButton = new();
        CardRewardAlternative[] alternatives = { alternative };

        var original = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
        CardRewardAlternativePresentationBindings.ObserveCreated(oldButton);
        original.Finish(nativeSucceeded: true);
        Assert.True(CardRewardAlternativePresentationBindings.TryResolveButton(
            screen, alternative, alternatives, new[] { oldButton }, out _));

        var replacement = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
        Assert.False(CardRewardAlternativePresentationBindings.TryResolveButton(
            screen, alternative, alternatives, new[] { oldButton }, out _));
        CardRewardAlternativePresentationBindings.ObserveCreated(newButton);
        replacement.Finish(nativeSucceeded: true);
        Assert.False(CardRewardAlternativePresentationBindings.TryResolveButton(
            screen, alternative, alternatives, new[] { oldButton }, out _));
        Assert.True(CardRewardAlternativePresentationBindings.TryResolveButton(
            screen, alternative, alternatives, new[] { newButton }, out var resolved));
        Assert.Same(newButton, resolved);
    }

    [Fact]
    public void SameScreenReentryAndOutOfOrderFinishCannotRestoreEitherGeneration()
    {
        var screen = Bare<NCardRewardSelectionScreen>();
        var alternative = Alternative("Skip", PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward);
        object firstButton = new();
        object secondButton = new();
        CardRewardAlternative[] alternatives = { alternative };

        var outer = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
        CardRewardAlternativePresentationBindings.ObserveCreated(firstButton);
        var inner = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
        CardRewardAlternativePresentationBindings.ObserveCreated(secondButton);
        outer.Finish(nativeSucceeded: true); // Violates LIFO; both are poisoned.
        inner.Finish(nativeSucceeded: true);

        Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
            screen, alternatives, new[] { firstButton }, out _));
        Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
            screen, alternatives, new[] { secondButton }, out _));
    }

    [Fact]
    public void CrossThreadFinishCannotPublishAndDoesNotPoisonNextSameThreadRefresh()
    {
        var screen = Bare<NCardRewardSelectionScreen>();
        var alternative = Alternative("Skip", PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward);
        object oldButton = new();
        object nextButton = new();
        CardRewardAlternative[] alternatives = { alternative };
        var scope = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
        CardRewardAlternativePresentationBindings.ObserveCreated(oldButton);
        try
        {
            var worker = new Thread(() => scope.Finish(nativeSucceeded: true));
            worker.Start();
            Assert.True(worker.Join(TimeSpan.FromSeconds(5)));
            Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
                screen, alternatives, new[] { oldButton }, out _));
        }
        finally
        {
            scope.Finish(nativeSucceeded: false);
        }

        var next = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
        try
        {
            CardRewardAlternativePresentationBindings.ObserveCreated(nextButton);
            next.Finish(nativeSucceeded: true);
            Assert.True(CardRewardAlternativePresentationBindings.TryResolveButton(
                screen, alternative, alternatives, new[] { nextButton }, out var resolved));
            Assert.Same(nextButton, resolved);
        }
        finally
        {
            next.Finish(nativeSucceeded: false);
        }
    }

    [Fact]
    public void CrossThreadCreateCannotBorrowSynchronousRefreshScope()
    {
        var screen = Bare<NCardRewardSelectionScreen>();
        var alternative = Alternative("Skip", PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward);
        object button = new();
        CardRewardAlternative[] alternatives = { alternative };
        var scope = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
        try
        {
            var worker = new Thread(() =>
                CardRewardAlternativePresentationBindings.ObserveCreated(button));
            worker.Start();
            Assert.True(worker.Join(TimeSpan.FromSeconds(5)));
            scope.Finish(nativeSucceeded: true);
            Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
                screen, alternatives, new[] { button }, out _));
        }
        finally
        {
            scope.Finish(nativeSucceeded: false);
        }
    }

    [Fact]
    public void ConcurrentSameScreenRefreshOnAnotherThreadPoisonsBothGenerations()
    {
        var screen = Bare<NCardRewardSelectionScreen>();
        var alternative = Alternative("Skip", PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward);
        object firstButton = new();
        object secondButton = new();
        CardRewardAlternative[] alternatives = { alternative };
        var first = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
        try
        {
            var worker = new Thread(() =>
            {
                var second = CardRewardAlternativePresentationBindings.BeginRefresh(screen, alternatives);
                CardRewardAlternativePresentationBindings.ObserveCreated(secondButton);
                second.Finish(nativeSucceeded: true);
            });
            worker.Start();
            Assert.True(worker.Join(TimeSpan.FromSeconds(5)));
            CardRewardAlternativePresentationBindings.ObserveCreated(firstButton);
            first.Finish(nativeSucceeded: true);
            Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
                screen, alternatives, new[] { firstButton }, out _));
            Assert.False(CardRewardAlternativePresentationBindings.TryCapture(
                screen, alternatives, new[] { secondButton }, out _));
        }
        finally
        {
            first.Finish(nativeSucceeded: false);
        }
    }

    [Fact]
    public void NoHookWithNoAlternativeHasNoAlternativeToAuthorize()
    {
        var screen = Bare<NCardRewardSelectionScreen>();
        Assert.True(CardRewardAlternativePresentationBindings.TryCapture(
            screen, Array.Empty<CardRewardAlternative>(), Array.Empty<object>(), out var pairs));
        Assert.Empty(pairs);
        Assert.False(CardRewardAlternativePresentationBindings.TryResolveButton(
            screen, Alternative("Skip", PostAlternateCardRewardAction.EndSelectionAndDoNotCompleteReward),
            Array.Empty<CardRewardAlternative>(), Array.Empty<object>(), out _));
    }

    private static T Bare<T>() where T : class =>
        (T)RuntimeHelpers.GetUninitializedObject(typeof(T));

    private static CardRewardAlternative Alternative(
        string optionId, PostAlternateCardRewardAction afterSelected)
    {
        CardRewardAlternative alternative = Bare<CardRewardAlternative>();
        typeof(CardRewardAlternative)
            .GetField("<OptionId>k__BackingField", BindingFlags.Instance | BindingFlags.NonPublic)!
            .SetValue(alternative, optionId);
        typeof(CardRewardAlternative)
            .GetField("<AfterSelected>k__BackingField", BindingFlags.Instance | BindingFlags.NonPublic)!
            .SetValue(alternative, afterSelected);
        return alternative;
    }
}
