using STS2Connector.LiveHost;

namespace STS2Connector.Tests;

public sealed class LiveObservationLifecycleTests
{
    [Fact]
    public void TreasureSkipDoesNotPublishBeforeTheSelectableRelicHolderIsReady()
    {
        Assert.False(TreasureRoomSurfaceReader.IsDecisionReady(true, true));
        Assert.True(TreasureRoomSurfaceReader.IsDecisionReady(true, false));
        Assert.False(TreasureRoomSurfaceReader.IsDecisionReady(false, false));
    }

    [Fact]
    public void ExactOpenModalOwnsInputAheadOfMenuOverlayAndRoomLayers()
    {
        Assert.Equal(InputOwnerLayer.Modal, ActiveInputResolver.SelectLayer(
            hasVisibleOverlay: true,
            mapIsOpen: true,
            hasMenuSubmenu: true,
            hasOpenModal: true));
        Assert.True(ActiveInputResolver.IsActiveLayer(
            InputOwnerLayer.Modal,
            hasVisibleOverlay: true,
            mapIsOpen: true,
            hasMenuSubmenu: true,
            hasOpenModal: true));
        Assert.False(ActiveInputResolver.IsActiveLayer(
            InputOwnerLayer.Menu,
            hasVisibleOverlay: false,
            mapIsOpen: false,
            hasMenuSubmenu: true,
            hasOpenModal: true));
    }

    [Fact]
    public void PersistentStateMountFailureSettlesOnlyInsideABoundedWindow()
    {
        var window = new BoundedSettlingWindow(TimeSpan.FromSeconds(20));
        DateTimeOffset start = DateTimeOffset.Parse("2026-08-13T00:00:00Z");

        Assert.True(window.Observe(condition: true, start));
        Assert.True(window.Observe(condition: true, start.AddSeconds(20)));
        Assert.False(window.Observe(condition: true, start.AddSeconds(21)));
        Assert.False(window.Observe(condition: false, start.AddSeconds(22)));
        Assert.True(window.Observe(condition: true, start.AddSeconds(40)));
    }

    [Fact]
    public void MenuToRunEntryWithoutAMountedOwnerIsASettlingTransition()
    {
        Assert.True(LiveObservationReader.ClassifyMenuOrRunEntryNoInputTransition(
            runInProgress: false,
            hasBlockingSurface: false,
            sourceType: "menu_or_no_run"));
    }

    [Theory]
    [InlineData(true, false, "menu_or_no_run")]
    [InlineData(false, true, "menu_or_no_run")]
    [InlineData(false, false, "NMainMenu")]
    [InlineData(false, false, "unknown_modal")]
    public void MenuRunEntryTransitionDoesNotMaskARealOrUnknownOwner(
        bool runInProgress,
        bool hasBlockingSurface,
        string sourceType)
    {
        Assert.False(LiveObservationReader.ClassifyMenuOrRunEntryNoInputTransition(
            runInProgress,
            hasBlockingSurface,
            sourceType));
    }

    [Fact]
    public void InProgressCombatWithoutMountedRoomOrHandIsBoundedSetupSettling()
    {
        Assert.Equal(CombatNoInputPhase.Setup, LiveObservationReader.ClassifyCombatNoInputTransition(
            runInProgress: true,
            currentRoomIsCombat: true,
            combatIsStarting: false,
            combatInProgress: true,
            combatStatePresent: true,
            hasBlockingSurface: false,
            liveCombatRoomPresent: false,
            liveCombatHandPresent: false));
    }

    [Theory]
    [InlineData(true, true, true)]
    [InlineData(false, false, false)]
    public void MountedCombatOrBlockingOwnerIsNotReclassifiedAsNoInput(
        bool liveCombatRoomPresent,
        bool liveCombatHandPresent,
        bool expectedMounted)
    {
        Assert.Equal(CombatNoInputPhase.None, LiveObservationReader.ClassifyCombatNoInputTransition(
            runInProgress: true,
            currentRoomIsCombat: true,
            combatIsStarting: false,
            combatInProgress: true,
            combatStatePresent: true,
            hasBlockingSurface: !expectedMounted,
            liveCombatRoomPresent,
            liveCombatHandPresent));
    }

    [Fact]
    public void CompletedEventPresentationIsASettlingStateWithoutAuthority()
    {
        Assert.True(LiveObservationReader.ClassifyEventNoInputTransition(
            runInProgress: true,
            currentRoomIsEvent: true,
            hasBlockingSurface: false,
            sourceType: "run_without_visible_overlay",
            eventRoomNodePresent: true,
            inDialogue: false));
    }

    [Fact]
    public void CurrentEventModelWithoutMountedRoomIsASettlingTransition()
    {
        Assert.True(LiveObservationReader.ClassifyEventRoomMountNoInputTransition(
            runInProgress: true,
            currentRoomIsEvent: true,
            hasBlockingSurface: false,
            sourceType: "run_without_visible_overlay",
            eventRoomNodePresent: false));
    }

    [Theory]
    [InlineData(false, true, false, "run_without_visible_overlay", false)]
    [InlineData(true, false, false, "run_without_visible_overlay", false)]
    [InlineData(true, true, true, "run_without_visible_overlay", false)]
    [InlineData(true, true, false, "unknown_modal", false)]
    [InlineData(true, true, false, "run_without_visible_overlay", true)]
    public void EventRoomMountTransitionDoesNotMaskOtherOwners(
        bool runInProgress,
        bool currentRoomIsEvent,
        bool hasBlockingSurface,
        string sourceType,
        bool eventRoomNodePresent)
    {
        Assert.False(LiveObservationReader.ClassifyEventRoomMountNoInputTransition(
            runInProgress,
            currentRoomIsEvent,
            hasBlockingSurface,
            sourceType,
            eventRoomNodePresent));
    }

    [Theory]
    [InlineData(false, true, false, "run_without_visible_overlay", true, false)]
    [InlineData(true, false, false, "run_without_visible_overlay", true, false)]
    [InlineData(true, true, true, "run_without_visible_overlay", true, false)]
    [InlineData(true, true, false, "overlay", true, false)]
    [InlineData(true, true, false, "run_without_visible_overlay", false, false)]
    [InlineData(true, true, false, "run_without_visible_overlay", true, true)]
    public void EventTransitionDoesNotHideARealOrUnknownInputOwner(
        bool runInProgress,
        bool currentRoomIsEvent,
        bool hasBlockingSurface,
        string sourceType,
        bool eventRoomNodePresent,
        bool inDialogue)
    {
        Assert.False(LiveObservationReader.ClassifyEventNoInputTransition(
            runInProgress,
            currentRoomIsEvent,
            hasBlockingSurface,
            sourceType,
            eventRoomNodePresent,
            inDialogue));
    }

    [Fact]
    public void TreasureScreenOwnershipHandoffIsSettling()
    {
        Assert.True(TreasureRoomSurfaceReader.ClassifyScreenHandoff(
            runInProgress: true,
            currentRoomIsTreasure: true,
            uiRoomIsLive: true,
            ownsCurrentScreen: false));
    }

    [Theory]
    [InlineData(false, true, true, false)]
    [InlineData(true, false, true, false)]
    [InlineData(true, true, false, false)]
    [InlineData(true, true, true, true)]
    public void TreasureHandoffDoesNotMaskOtherStates(
        bool runInProgress,
        bool currentRoomIsTreasure,
        bool uiRoomIsLive,
        bool ownsCurrentScreen)
    {
        Assert.False(TreasureRoomSurfaceReader.ClassifyScreenHandoff(
            runInProgress,
            currentRoomIsTreasure,
            uiRoomIsLive,
            ownsCurrentScreen));
    }
}
