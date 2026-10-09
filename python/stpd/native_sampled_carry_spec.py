"""Pure sampled-current contract shared by deployment and Source3 projection.

Feature encoding remains the historical native structured projection. This module
owns acquisition and segment semantics, never training, storage or native legality.
"""
from __future__ import annotations

import copy
from typing import Any

from spireagent.json_boundary import BoundaryError
from .canonical import semantic_hash
from .fullrun.native_structured_inputs import (
    INPUT_SPEC as FEATURE_INPUT_SPEC, PROJECTION as FEATURE_PROJECTION,
    PROFILE, SCOPE,
)
from .native_graph_spec import NativeGraphControl, checked_control
from .policy.native_task import observe_ready_summary, ready_summary_task_spec

HISTORY_MODE = "sampled_current"
VIEW = "decision_sample_carry"
RECIPE = "source3-native-m2-k1d96-carry-N-sampled-v1"
STATE_FORMAT = "stpd/native-sampled-carry-state-v1"
RECHECK_TIMEOUT_MS = 250
RESET_REASONS = (
    "sample_segment_start", "recording_pause", "actor_handoff",
    "input_basis_missing", "input_order_unproven", "delivery_unknown",
    "environment_boundary", "context_truncated_start",
)
SAMPLE_STORAGE_LIMITS = {
    "max_samples": 1024, "max_files": 3072,
    "max_total_bytes": 512 * 1024 * 1024,
    "max_pending_bytes": 128 * 1024 * 1024, "max_pending_samples": 8,
    "max_metadata_bytes": 64 * 1024,
}
INPUT_SPEC_BODY = {
    "schema": "stpd/native-sampled-carry-input-spec-v1",
    "id": "stpd-native-sampled-carry-v1", "version": "1.0.0",
    "profile": PROFILE, "feature_projection": copy.deepcopy(FEATURE_PROJECTION),
    "feature_input_spec": copy.deepcopy(FEATURE_INPUT_SPEC),
    "eager_scope": list(SCOPE), "history_mode": HISTORY_MODE,
    "consumption_mode": "once_per_occurrence", "I": False, "F": False,
    "acquisition": "complete_current_and_complete_original_catalog",
    "publication_index": None,
    "sample": "changed_native_unit_with_nonempty_catalog_or_ready_summary",
    "unchanged_current": "readiness_only_no_consume_no_advance",
    "empty_catalog": "readiness_only_unless_ready_summary",
    "carry": "advance_once_then_commit_only_exact_ack",
    "reset": "fresh_live_session_explicit_segment",
    "reset_reasons": list(RESET_REASONS), "state_recovery": "none",
}
INPUT_SPEC = {"id": INPUT_SPEC_BODY["id"], "version": "1.0.0",
              "sha256": semantic_hash(INPUT_SPEC_BODY)}


def sampled_agent_spec(model_control: NativeGraphControl) -> dict[str, Any]:
    """The initial supported sampled Agent is exactly K1/D96 carry."""
    control = checked_control(model_control.to_dict())
    if control != NativeGraphControl():
        raise BoundaryError("sampled_carry", "k1d96_carry_required")
    return {
        "id": "stpd-native-sampled-carry-m2-agent", "version": "1.0.0",
        "acquisition": "eligible_current_decision_samples",
        "choice": "complete_catalog_greedy", "empty_catalog": "bounded_current_recheck",
        "timing_learned": False, "memory_slots": 1, "memory_width": 96,
        "model_control": control.to_dict(), "history_mode": HISTORY_MODE,
        "recheck_timeout_ms": RECHECK_TIMEOUT_MS,
        "segment": "one_live_runtime_session_until_post_sample_human_or_failure",
        "task_spec": ready_summary_task_spec(),
    }


def sample_eligible(observation: dict[str, Any], catalog: list[dict[str, Any]]) -> bool:
    """Apply only after the existing native whole-input qualifier succeeded."""
    return bool(catalog) or observe_ready_summary(observation).agent_task_complete


def checked_reset_reason(value: object) -> str:
    if value not in RESET_REASONS:
        raise BoundaryError("sampled_carry", "unsupported_reset_reason")
    return str(value)
