using System;
using System.Runtime.CompilerServices;
using Godot;
using MegaCrit.Sts2.Core.Nodes.Screens.GameOverScreen;
using MegaCrit.Sts2.Core.Nodes.Events;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;

namespace STS2Platform.NativeFoundation;

/// <summary>
/// One process-local STS2 fact that a new player decision owner has reached an
/// exact native-ready seam. This observation does not claim that Connector can
/// publish a complete fair-player frame; consumers must capture and validate
/// that frame independently at the same synchronous seam.
/// </summary>
public sealed record NativeDecisionOwnerReadyObservation(
    string Domain,
    object NativeOwner,
    string NativeOwnerType,
    string NativeMechanism);

public static class NativeDecisionOwnerReadyProvider
{
    public const string CombatTurnDomain = "combat_turn";
    public const string CombatTurnMechanism =
        "CombatManager.TurnStarted->NEndTurnButton.OnTurnStarted.postfix";

    public const string GameOverDomain = "game_over";
    public const string GameOverMechanism =
        "NGameOverScreen.AnimateIn->NGameOverContinueButton.OnEnable.postfix";

    public const string MapProceedMechanism =
        "RunManager.ProceedFromTerminalRewardsScreen->NMapScreen.Open.return";

    public const string EventProceedMechanism =
        "NEventRoom.Proceed->NMapScreen.Open.return";

    // Called at the synchronous native Proceed return, only when that invocation
    // changed its exact map owner from closed to open. Animations are not proof.
    public static bool ObserveMapProceedReady(NMapScreen screen) =>
        ObserveMapReady(screen, MapProceedMechanism);

    public static bool ObserveEventProceedReady(NMapScreen screen) =>
        ObserveMapReady(screen, EventProceedMechanism);

    private static bool ObserveMapReady(NMapScreen screen, string nativeMechanism)
    {
        if (!ReferenceEquals(NMapScreen.Instance, screen) || !screen.IsOpen
            || !screen.IsVisibleInTree() || !ActiveScreenContext.Instance.IsCurrent(screen)
            || RunManager.Instance.DebugOnlyGetState() is not { } run
            || run.IsGameOver || RunManager.Instance.IsCleaningUp
            || run.Players.Count != 1 || LocalContext.GetMe(run) == null)
            return false;
        Observed?.Invoke(new NativeDecisionOwnerReadyObservation(
            "map_navigation", screen, screen.GetType().FullName ?? screen.GetType().Name,
            nativeMechanism));
        return true;
    }

    // SetupLayout awaits native UI work before its options become available.
    // Publish the exact completed layout seam rather than polling a settling frame.
    public static bool ObserveEventLayoutReady(NEventLayout layout)
    {
        NEventRoom? room = NEventRoom.Instance;
        RunState? run = RunManager.Instance.DebugOnlyGetState();
        if (room == null || run?.CurrentRoom is not EventRoom || run.IsGameOver
            || RunManager.Instance.IsCleaningUp || run.Players.Count != 1
            || !ReferenceEquals(room.Layout, layout) || !layout.IsInsideTree()
            || !layout.IsVisibleInTree() || NMapScreen.Instance?.IsOpen == true
            || RunManager.Instance.EventSynchronizer?.GetLocalEvent() is not { } model
            || !ReferenceEquals(model.Node, layout)) return false;
        Observed?.Invoke(new NativeDecisionOwnerReadyObservation("event_option", layout,
            layout.GetType().FullName ?? layout.GetType().Name, "NEventLayout.OnSetupComplete.postfix"));
        return true;
    }

    private static readonly ConditionalWeakTable<NGameOverScreen, RunState> GameOverOwners = new();

    public static event Action<NativeDecisionOwnerReadyObservation>? Observed;

    internal static void RegisterGameOver(NGameOverScreen screen, RunState run) =>
        GameOverOwners.Add(screen, run);

    /// <summary>
    /// Native Enable has completed on the exact intro Continue control. The
    /// factory-bound run must still own the terminal screen; a leaderboard,
    /// stale screen, abandoned run or cleanup is not this decision boundary.
    /// This emits no action identity or terminal-action completion.
    /// </summary>
    internal static bool ObserveGameOverReady(NGameOverContinueButton button)
    {
        if (NOverlayStack.Instance?.Peek() is not NGameOverScreen screen
            || !GameOverOwners.TryGetValue(screen, out RunState? run)
            || !ReferenceEquals(RunManager.Instance.DebugOnlyGetState(), run)
            || RunManager.Instance.IsCleaningUp || RunManager.Instance.IsAbandoned
            || !run.IsGameOver || run.GameMode != GameMode.Standard
            || run.Players.Count != 1 || LocalContext.GetMe(run) == null
            || !ActiveScreenContext.Instance.IsCurrent(screen)
            || !ReferenceEquals(screen.GetNodeOrNull<NGameOverContinueButton>("%ContinueButton"), button)
            || !screen.IsVisibleInTree() || !button.IsVisibleInTree()
            || !button.IsEnabled || button.MouseFilter == Control.MouseFilterEnum.Ignore)
        {
            return false;
        }

        Observed?.Invoke(new NativeDecisionOwnerReadyObservation(
            GameOverDomain, screen, screen.GetType().FullName ?? screen.GetType().Name,
            GameOverMechanism));
        return true;
    }

    /// <summary>
    /// Called only by the exact-version composition patch after STS2 has run
    /// its player-turn input-owner callback. The semantic checks reject enemy,
    /// stale and non-play-phase callbacks before publishing an observation.
    /// </summary>
    internal static bool ObservePlayerCombatTurnReady(CombatState state)
    {
        RunState? run = RunManager.Instance.DebugOnlyGetState();
        if (run?.CurrentRoom is not CombatRoom
            || !ReferenceEquals(state.RunState, run)
            || !ReferenceEquals(CombatManager.Instance.DebugOnlyGetState(), state)
            || state.CurrentSide != CombatSide.Player
            || !CombatManager.Instance.IsInProgress)
        {
            return false;
        }

        Player? player = LocalContext.GetMe(state);
        PlayerCombatState? combat = player?.PlayerCombatState;
        if (player == null
            || combat == null
            || !NativeCombatDecisionProvider.IsSemanticPlayPhase(player, combat))
        {
            return false;
        }

        Observed?.Invoke(new NativeDecisionOwnerReadyObservation(
            CombatTurnDomain,
            state,
            state.GetType().FullName ?? state.GetType().Name,
            CombatTurnMechanism));
        return true;
    }
}
