using System;
using System.Reflection;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Multiplayer;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.NativeUi;
using STS2Connector.LiveHost.Contracts;

namespace STS2Connector.LiveHost;

internal enum CombatWithdrawalState { Pending, Expired }
internal sealed record CombatInputLifecycle(object Run, object Combat, object Player, int Turn);
internal sealed record CombatWithdrawalProof(string OwnerIdentity, CombatWithdrawalState State)
{
    internal string Identity => StableIdentityHash.Object(new { OwnerIdentity, State });
}
internal sealed record CombatAvailabilityEvidence(CombatInputLifecycle Lifecycle, string OwnerIdentity,
    int GameplayCommands, bool CompleteDiscovery, bool CanStartWithdrawal, bool CanContinueWithdrawal);

// One monotonic native-lifecycle window, not a profile/queue/action timer. UI
// controls and successful/rich captures do not create a fresh lifecycle budget.
internal sealed class CombatInputWithdrawalWindow
{
    internal const long BoundMs = 20_000;
    private readonly object gate = new();
    private CombatInputLifecycle? lifecycle;
    private long? started;
    internal static bool SameLifecycle(CombatInputLifecycle a, CombatInputLifecycle b) =>
        ReferenceEquals(a.Run, b.Run) && ReferenceEquals(a.Combat, b.Combat)
        && ReferenceEquals(a.Player, b.Player) && a.Turn == b.Turn;
    internal void ObserveLifecycle(CombatInputLifecycle? current)
    {
        lock (gate)
        {
            // Unknown/missing capture does not establish lifecycle replacement.
            if (current == null) return;
            if (lifecycle == null || !SameLifecycle(lifecycle, current))
            { lifecycle = current; started = null; }
        }
    }
    internal CombatWithdrawalProof? Observe(CombatAvailabilityEvidence? evidence, bool completeCapture, long now)
    {
        lock (gate)
        {
            if (evidence == null) return null;
            ObserveLifecycle(evidence.Lifecycle);
            if (!completeCapture || !evidence.CompleteDiscovery) return null;
            if (evidence.GameplayCommands > 0) { started = null; return null; }
            if (!evidence.CanStartWithdrawal && !(started != null && evidence.CanContinueWithdrawal)) return null;
            started ??= now;
            return new(evidence.OwnerIdentity, now - started.Value >= BoundMs
                ? CombatWithdrawalState.Expired : CombatWithdrawalState.Pending);
        }
    }
}

internal static class CombatInputAvailability
{
    internal static readonly CombatInputWithdrawalWindow Window = new();
    // The shipped private enum has no public typed accessor. This one audited
    // field is read only; no arbitrary field/method name or mutation is accepted.
    private static readonly FieldInfo? ButtonState = typeof(NEndTurnButton).GetField("_state",
        BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
    private static readonly object? DisabledState = ButtonState?.FieldType.IsEnum == true
        && Array.IndexOf(Enum.GetNames(ButtonState.FieldType), "Disabled") >= 0
        ? Enum.Parse(ButtonState.FieldType, "Disabled") : null;
    internal static bool IsNativeDisabled(NEndTurnButton button) => DisabledState != null
        && Equals(ButtonState!.GetValue(button), DisabledState) && !button.IsEnabled;

    internal static void ObserveNativeLifecycle()
    {
        try
        {
            var run = RunManager.Instance.DebugOnlyGetState();
            var combat = CombatManager.Instance.DebugOnlyGetState();
            var player = run == null ? null : LocalContext.GetMe(run);
            if (run != null && combat != null && player?.PlayerCombatState is { } state
                && ReferenceEquals(LocalContext.GetMe(combat), player)
                && ReferenceEquals(player.Creature.CombatState, combat))
                Window.ObserveLifecycle(new(run, combat, player, state.TurnNumber));
        }
        catch { /* An unknown scope never resets a known same-turn budget. */ }
    }

    internal static CombatAvailabilityEvidence? Capture(NCombatRoom room, NPlayerHand hand,
        MegaCrit.Sts2.Core.Entities.Players.Player player, NativeEntityRegistry entities,
        int gameplayCommands, bool completeDiscovery, bool openPlay)
    {
        try
        {
            var owner = NativeEndTurnInput.CaptureOwner(room, hand, player);
            var run = RunManager.Instance.DebugOnlyGetState();
            if (owner == null || run == null || room.Mode != CombatRoomMode.ActiveCombat
                || !ActiveScreenContext.Instance.IsCurrent(room)
                || hand.IsInCardSelection || hand.InCardPlay || hand.CurrentMode != NPlayerHand.Mode.Play
                || hand.PeekButton?.IsPeeking == true) return null;
            bool withdrawn = IsNativeDisabled(owner.Button) && NativeEndTurnInput.ReadCanTurnBeEnded(owner.Button);
            bool knownTransition = RunManager.Instance.ActionQueueSynchronizer.CombatState is
                ActionSynchronizerCombatState.EndTurnPhaseOne or ActionSynchronizerCombatState.NotPlayPhase
                || CombatManager.Instance.IsPlayerReadyToEndTurn(player) && CombatManager.Instance.PlayerActionsDisabled;
            string identity = StableIdentityHash.Object(new { run = entities.GetId(run, "run"),
                owner = NativeEndTurnInput.Identity(entities, owner.ExactScope) });
            return new(new(run, owner.Combat, player, owner.TurnNumber), identity,
                gameplayCommands, completeDiscovery, withdrawn && openPlay, withdrawn && (openPlay || knownTransition));
        }
        catch { return null; }
    }

    internal static LiveObservation Attach(LiveObservation draft, bool persistentComplete, long now)
    {
        bool complete = persistentComplete && draft.Surface is CombatTurnSurface
            && draft.Completeness.Missing.Count == 0
            && draft.Completeness.PlayerVisibleSemantics.StartsWith("contract_complete_for_immediate_combat_turn", StringComparison.Ordinal);
        var proof = Window.Observe(draft.CombatAvailabilityEvidence, complete, now);
        return draft with { CombatWithdrawal = proof };
    }
}
