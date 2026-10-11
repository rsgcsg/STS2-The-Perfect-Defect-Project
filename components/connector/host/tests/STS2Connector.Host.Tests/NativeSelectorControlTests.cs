using STS2Connector.LiveHost.Contracts;
using STS2Connector.NativeUi;
using Xunit;

namespace STS2Connector;

public sealed class NativeSelectorControlTests
{
    // Behavioral fixture of the inspected 9cb4 selector controls, not a native
    // rules implementation. Production reads these flags from the actual button.
    private sealed class NativeButton
    {
        internal bool Visible = true;
        internal bool Enabled;
        internal bool OwnerCurrent = true;
        internal int Clicks;
        internal bool Available => Visible && Enabled;
    }
    private static NativeInputResult Deliver(NativeButton? button) => NativeSelectorControl.Click(button,
        () => button?.OwnerCurrent == true, value => value.Available, value => value.Clicks++, "exact native selector click");
    private static VisibleCard Card(string id, bool selected) => new(id, "STRIKE", "Strike", "Attack", "1", null,
        "Visible card body", "Basic", false, selected, null);

    [Fact]
    public void PileEnabledEffectiveMinimumPublishesAndDeliversEvenBelowRequestedMinimum()
    {
        const int requestedMin = 2, currentEffectiveCount = 1, selectedCount = 1;
        var button = new NativeButton { Enabled = selectedCount >= Math.Min(requestedMin, currentEffectiveCount) };
        Assert.True(selectedCount < requestedMin); // The old adapter's extra veto would reject.
        var surface = new NativeCombatPileSelectionSurface(NativeCombatPileSelection.SurfaceKind,
            "selecting", "pile", "Choose 2", "discard", requestedMin, 2, selectedCount,
            new[] { "card" }, Array.Empty<string>(), new[] { "card" }, false, false,
            NativeSelectorControl.Available(button, value => value.Available), new[] { Card("card", true) });
        Assert.Equal(2, surface.MinSelect); // Requested preference remains descriptive.
        Assert.Contains(NativeCombatPileSelection.DescribeCommands(surface),
            command => command.Kind == NativeCombatPileSelection.ConfirmOperation);
        Assert.True(Deliver(button).Accepted);
        Assert.Equal(1, button.Clicks);
    }

    [Fact]
    public void SimpleZeroMinimumInitiallyEnabledConfirmationIsNotManualOnly()
    {
        var button = new NativeButton { Enabled = true }; // Native _Ready enables MinSelect=0 regardless manual flag.
        var surface = new NativeSimpleCardSelectionSurface(NativeSimpleCardSelection.SurfaceKind,
            "selecting", "simple", "Choose up to 1", 0, 1, 0, Array.Empty<string>(), new[] { "card" },
            Array.Empty<string>(), Cancelable: false, RequireManualConfirmation: false, CanCancel: false,
            CanConfirm: NativeSelectorControl.Available(button, value => value.Available), Cards: new[] { Card("card", false) });
        Assert.Contains(NativeSimpleCardSelection.DescribeCommands(surface),
            command => command.Kind == NativeSimpleCardSelection.ConfirmOperation);
        Assert.DoesNotContain(NativeSimpleCardSelection.DescribeCommands(surface),
            command => command.Kind == NativeSimpleCardSelection.CancelOperation);
        Assert.True(Deliver(button).Accepted); // Empty completion is distinct from cancel.
        Assert.Equal(1, button.Clicks);
    }

    [Fact]
    public void SimpleNativeAutoModeDisablesConfirmationAfterToggleAndCompletesWithoutFakeConfirm()
    {
        var button = new NativeButton { Enabled = false }; // OnCardClicked updates native manual-only confirm state.
        var surface = new NativeSimpleCardSelectionSurface(NativeSimpleCardSelection.SurfaceKind,
            "selecting", "simple", "Choose", 0, 2, 1, new[] { "card" }, new[] { "other" }, new[] { "card" },
            false, false, false, NativeSelectorControl.Available(button, value => value.Available),
            new[] { Card("card", true), Card("other", false) });
        Assert.DoesNotContain(NativeSimpleCardSelection.DescribeCommands(surface),
            command => command.Kind == NativeSimpleCardSelection.ConfirmOperation);
        Assert.Contains(NativeSimpleCardSelection.DescribeCommands(surface),
            command => command.Kind == NativeSimpleCardSelection.SelectOperation);
        Assert.False(Deliver(button).Accepted);
        Assert.Equal(0, button.Clicks);
        button.OwnerCurrent = false; // Native auto-completion retires the screen.
        button.Enabled = true;
        Assert.False(Deliver(button).Accepted);
        Assert.Equal(0, button.Clicks);
    }

    [Fact]
    public void UpgradePreviewUsesEnabledNativeConfirmWithoutGenericRawMinimumVeto()
    {
        var button = new NativeButton { Enabled = true };
        var original = Card("original", true);
        var preview = Card("preview-clone", false);
        var surface = new NativeDeckUpgradeSelectionSurface(NativeDeckUpgradeSelection.SurfaceKind,
            "preview", "upgrade", "Upgrade", 2, 2, 1, new[] { "original" },
            Array.Empty<string>(), Array.Empty<string>(), Cancelable: true,
            ShowingUpgradePreviews: false, CanToggleUpgradeView: false,
            CanCancelSelection: false, CanCancelPreview: true,
            CanConfirm: NativeSelectorControl.Available(button, value => value.Available),
            Cards: new[] { original }, PreviewCards: new[] { preview });
        var actions = NativeDeckUpgradeSelection.DescribeCommands(surface);
        Assert.Contains(actions, action => action.Kind == NativeDeckUpgradeSelection.ConfirmOperation);
        Assert.Contains(actions, action => action.Kind == NativeDeckUpgradeSelection.CancelPreviewOperation);
        Assert.DoesNotContain(actions, action => action.Kind == NativeDeckUpgradeSelection.CancelSelectionOperation);
        Assert.DoesNotContain(actions.SelectMany(action => action.EntityBindings!), binding => binding.EntityId == "preview-clone");
    }

    [Theory]
    [InlineData(false, true, true)]
    [InlineData(true, false, true)]
    [InlineData(true, true, false)]
    public void DisabledHiddenOrWrongOwnerRejectsBeforeNativeDelivery(bool enabled, bool visible, bool owner)
    {
        var button = new NativeButton { Enabled = enabled, Visible = visible, OwnerCurrent = owner };
        Assert.False(Deliver(button).Accepted);
        Assert.Equal(0, button.Clicks);
    }

    [Fact]
    public void MissingExactControlAndNativeExceptionDoNotCreateDeliveryOrRetry()
    {
        Assert.False(Deliver(null).Accepted);
        var button = new NativeButton { Enabled = true };
        Assert.Throws<InvalidOperationException>(() => NativeSelectorControl.Click(button, () => true,
            value => value.Available, value => { value.Clicks++; throw new InvalidOperationException("native callback failed"); }, "native"));
        Assert.Equal(1, button.Clicks);
    }
}
