using System.Text.Json.Nodes;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Runs;
using STS2Connector.PlayerEnvironment.Witness;

namespace STS2HumanAnnotator.Mod;

internal static partial class RecorderRuntime
{
    // Called immediately after the original synchronous capture, before the native body resumes.
    // Reads only current fields/properties; never re-invokes card CanPlay/Hook.ShouldPlay or mutates input.
    private static JsonObject CaptureP1CardReadiness(ProcessLocalNativeWitnessFrame frame, NHandCardHolder? holder, string seam)
    {
        var combat = CombatManager.Instance;
        var hand = NPlayerHand.Instance;
        var player = holder?.CardModel?.Owner;
        return new JsonObject {
            ["capture_seam"] = seam, ["capture_snapshot_id"] = frame.Snapshot.SnapshotId,
            ["snapshot_observed_at"] = frame.Snapshot.ObservedAt, ["diagnostic_observed_at"] = DateTimeOffset.UtcNow,
            ["capture_stage_generation"] = Volatile.Read(ref _cardStageGeneration),
            ["exact_holder_witness_id"] = holder == null ? null : NativeWitnessIdentity.Get(holder, "card_holder"),
            ["exact_card_witness_id"] = holder?.CardModel is { } card ? NativeWitnessIdentity.Get(card, "card") : null,
            ["combat_in_progress"] = combat.IsInProgress, ["player_actions_disabled"] = combat.PlayerActionsDisabled,
            ["player_phase"] = player?.PlayerCombatState?.Phase.ToString(),
            ["part_of_player_turn"] = player == null ? null : combat.IsPartOfPlayerTurn(player),
            ["hand_in_card_play"] = hand?.InCardPlay, ["hand_current_mode"] = hand?.CurrentMode.ToString(),
            ["hand_is_peeking"] = hand?.PeekButton?.IsPeeking,
            ["native_action_executing"] = RunManager.Instance.ActionExecutor.IsRunning,
            ["extra_turn_player_count"] = combat.PlayersTakingExtraTurn.Count,
            ["player_in_extra_turn"] = player == null ? null : combat.PlayersTakingExtraTurn.Contains(player),
            ["card_can_play_predicate"] = "not_reinvoked_diagnostic_reads_only",
            ["public_snapshot_status"] = frame.Snapshot.Status,
            ["public_bound_action_count"] = frame.Snapshot.BoundActions.TotalCount
        };
    }

    private static void BindP1CardCaptureDiagnostics(JsonObject? frozen, string source, string stagedDisposition)
    {
        if (!IsP1Recording || frozen == null || HumanActionScope.Current is not { } context) return;
        var value = (JsonObject)frozen.DeepClone();
        value["capture_source"] = source;
        value["staged_selection_disposition"] = stagedDisposition;
        value["scope_stage_generation"] = Volatile.Read(ref _cardStageGeneration);
        value["native_input_binding"] = context.NativeInputBinding;
        context.P1CaptureDiagnostics = value;
    }
}
