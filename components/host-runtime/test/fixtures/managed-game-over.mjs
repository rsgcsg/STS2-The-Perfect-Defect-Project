// Synthetic DTO from the current headless terminal owner. No game is started.
export function gameOver(state, { victory = false, stage = "intro" } = {}) {
  const playerRef = state.player.native_ref ?? "native-terminal-player";
  return { ...state, decision: "game_over", victory,
    player: { ...state.player, native_ref: playerRef, hp: 0 },
    terminal_public: {
      kind: "managed_game_over_presentation", stage,
      owner_ref: "native-terminal-owner", run_ref: "native-ended-runstate", player_ref: playerRef,
      ended_run_ref: "native-ended-serializable", history_ref: "native-ended-history",
      continue_control_ref: "native-terminal-continue", result: victory ? "win" : "loss", game_mode: "standard",
      can_advance_summary: stage === "intro", can_return: false, complete: stage === "intro",
      score: stage === "summary" ? 10 : null, floors_climbed: stage === "summary" ? 1 : null,
      ascension: stage === "summary" ? 0 : null,
      missing: stage === "intro" ? [] : ["managed_game_over_return_navigation"],
      other_controls: [{ control_ref: "native-terminal-view-run", semantic_id: "view_run", label: "View Run",
        enabled: false, action_support: "visible_unsupported",
        blocked_reason: "Run-history navigation is outside the C1 ordinary journey envelope." }]
    }
  };
}
