// Public protocol conformance data only. No game legality or learned model claim.
export function publicManifest() {
  return {
    schema: "sts2.policy-runtime/policy-manifest-1", manifest_id: "public-m2-fixture",
    policy: { id: "public-m2-fixture", version: "1.0.0", provider: "fixture", architecture: "fixture" },
    adapter: { id: "public-m2-fixture", version: "1.0.0",
      protocol: "sts2.policy-runtime/decision-only-ndjson-4", code_sha256: "c".repeat(64) },
    artifact: { id: "fixture-model", path: "model.json", sha256: "a".repeat(64) },
    representation: { id: "public_compact", version: "stpd-public-snapshot-compact-v2",
      input_schema: "sts2.player-environment/snapshot-1" },
    requirements: { connector_protocol_version: "1.0.0", environment: {
      host_kind: "test", connector_version: "1", connector_source_revision: "source",
      connector_artifact_sha256: "b".repeat(64), connector_module_version_id: "mvid",
      modset_status: "exact", modset_fingerprint: "modset", loaded_mod_ids: ["fixture-mod"] },
      reads: [], whole_decision_admission: true, candidate_order_digest: "sha256-json-bound-action-id-order",
      score_count_matches_candidate_count: true, selected_index: true, successor_required: true },
    support: { game_versions: ["fixture-game"], game_commits: ["fixture-commit"],
      interaction_kinds: ["combat_turn"], action_verbs: ["end_turn"] },
    adapter_config: { public_stateful_profile: "sts2.policy-runtime/public-observation-stateful-v1" },
    claims: { full_run: false, selector: false, catalog_filtered: false,
      creates_action_authority: false, creates_native_operands: false }
  };
}

export function publicSnapshot(sequence = 1, count = 2) {
  return {
    protocol_version: "1.0.0", schema: "sts2.player-environment/snapshot-1",
    snapshot_id: `snapshot-${sequence}`, sequence, observed_at: "2026-10-11T00:00:00.000Z",
    status: "interactive", persistent: null,
    interaction: { interaction_id: "interaction", kind: "combat_turn", stage: "ready",
      content_schema: "sts2.player-environment/surface/combat_turn-1",
      content: { surface: { kind: "combat_turn", can_end_turn: true },
        context: { kind: "combat", is_play_phase: true, turn_owner: "player" } }, capabilities: [] },
    referents: [], bound_actions: { schema: "sts2.player-environment/bound-actions-1",
      status: "complete", materialized_count: count, total_count: count, limit: 64,
      ordering_semantics: "connector_order", actions: Array.from({ length: count }, (_, index) => ({
        bound_action_id: `candidate-${index}`, verb: "end_turn", interaction_id: "interaction",
        subject_referent_id: null, arguments: [], label: "Same visible label" })) }, reads: [],
    completeness: { status: "complete", visible_information: "fixture", interaction_discovery: "fixture",
      missing: [], hidden_by_policy: [] },
    session: { runtime_instance_id: "runtime", environment_fingerprint: "environment" },
    information_policy: { id: "fair-player", scope: "fixture", includes_hidden_information: false,
      unknown_field_behavior: "reject" }
  };
}

export function publicCapabilities() {
  return {
    protocol_version: "1.0.0", snapshot_schema: "sts2.player-environment/snapshot-1",
    action_schema: "sts2.player-environment/action-1", receipt_schema: "sts2.player-environment/receipt-1",
    control_schema: "sts2.player-environment/control-1", status: "implemented",
    host: { id: "fixture", name: "fixture", version: "1", runtime_instance_id: "runtime",
      host_kind: "test", implementation: { source_revision: "source", module_version_id: "mvid",
        artifact_sha256: "b".repeat(64) } },
    game: { version: "fixture-game", commit: "fixture-commit", branch: null, main_assembly_hash: null,
      compatibility: { status: "supported", observation_allowed: true, detail: "fixture" },
      modset: { status: "exact", fingerprint: "modset", scope: "fixture", loaded_mod_ids: ["fixture-mod"],
        detail: "fixture" } },
    environment_fingerprint: "environment", verbs: ["end_turn"], snapshot_bound: true,
    single_controller: true, execution_available: true, control: { recommended_renewal_ms: 10000 },
    evidence_profiles: [], non_claims: ["synthetic protocol fixture"]
  };
}

export function publicCompletion(input) {
  return { continuity_token: input.control.continuity_token, episode_id: input.control.episode_id,
    segment_id: input.control.segment_id, observation_ordinal: input.control.observation_ordinal,
    snapshot_id: input.decision.bundle.observation.snapshot_id,
    sequence: input.decision.bundle.observation.sequence,
    previous_action_request_id: input.control.previous_action?.request_id ?? null };
}
