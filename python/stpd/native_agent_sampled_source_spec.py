"""Closed direct native sample research identities; no trainer or executable registry.

Evidence owns original integrity/grammar. These identities own student reexpression,
known-prefix admission, sparse N and conservative train-only exposure grouping.
"""

from __future__ import annotations

import copy
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.blobs import MAX_BLOB_BYTES

from .canonical import semantic_hash
from .native_sampled_carry_spec import FEATURE_PROJECTION, INPUT_SPEC, INPUT_SPEC_BODY, STATE_FORMAT

PROFILE = "native_agent_sampled_v1"
RAW_SCHEMA = "stpd/native-agent-sampled-original-bundle-v1"
ADMISSION_SCHEMA = "stpd/native-agent-sampled-admission-v1"
PARTITION_SCHEMA = "stpd/native-agent-sampled-partition-v1"
SOURCE_SCHEMA = "stpd/native-agent-sampled-training-source-v1"
VALIDATION_SCHEMA = "stpd/native-agent-sampled-source-validation-v1"
RUN_IDENTITY_SCHEMA = "stpd/native-agent-sampled-run-view-v1"
SOURCE_KIND = "agent_protocol"
FIXTURE_COHORT = "synthetic_conformance"
FIXTURE_RELATION = "sampled-student-identity-fixture-v1"
QUALIFICATION = "synthetic_known_native_machine_samples_student_reexpression_only"
MAX_ORIGINAL_BYTES = 64 * 1024 * 1024
MAX_ORIGINAL_FILES = 3334
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_EVENTS = 16384
MAX_SOURCE_BYTES = MAX_BLOB_BYTES
MAX_RAW_REFERENCES = 32
MAX_KNOWN_SAMPLES = 1024

RELATION_BODY = {
    "schema": "stpd/native-agent-sampled-producer-student-relation-v1",
    "id": FIXTURE_RELATION,
    "version": "1.0.0",
    "cohort": FIXTURE_COHORT,
    "source_kind": SOURCE_KIND,
    "producer_input_spec": INPUT_SPEC,
    "producer_wire_projection": {key: FEATURE_PROJECTION[key] for key in ("id", "version")},
    "producer_state_format": STATE_FORMAT,
    "producer_input_spec_body_sha256": semantic_hash(INPUT_SPEC_BODY),
    "producer_input_spec_body_encoding": "canonical_json_without_terminal_newline",
    "student_input_spec": INPUT_SPEC,
    "student_input_spec_body_sha256": semantic_hash(INPUT_SPEC_BODY),
    "student_input_spec_body_encoding": "canonical_json_without_terminal_newline",
    "feature_projection": FEATURE_PROJECTION,
    "private_state": "not_observed_or_equal_to_student_W",
    "real_native_admission": False,
    "production_teacher": "unsupported_by_this_fixture_identity_relation",
}
RELATION_SPEC = {
    "id": RELATION_BODY["id"],
    "version": "1.0.0",
    "sha256": semantic_hash(RELATION_BODY),
}
PROJECTION_BODY = {
    "schema": "stpd/native-agent-sampled-projection-spec-v1",
    "id": "native-agent-known-samples-student-carry",
    "version": "1.0.0",
    "source_profile": PROFILE,
    "producer_student_relation": RELATION_SPEC,
    "student_input_spec": INPUT_SPEC,
    "student_input_spec_body_sha256": semantic_hash(INPUT_SPEC_BODY),
    "student_input_spec_body_encoding": "canonical_json_without_terminal_newline",
    "feature_projection": FEATURE_PROJECTION,
    "known_context": "original_offered_proposal_ACKoffer_matching_completedNext_and_directive",
    "first": "original_state_1_previous_consumption_null_explicit_segment_start",
    "order": "original_consumption_state_versions_and_event_locators",
    "advance": "shared_eligible_changed_NativeUnit_once_per_occurrence",
    "unlabelled": "known_context_retained_with_sparse_N_and_original_complete_C",
    "reset": "original_session_segment_start_only_no_middle_prefix_repair",
    "tail": "first_missing_or_uncertain_context_censors_continuous_remainder",
    "duplicates": "readiness_has_no_new_student_advance_or_N",
    "game_identity": "absent_no_seed_session_token_or_later_Current_inference",
    "group": "all_original_offers_related_by_protocol_runtime_instance_id",
    "split": "train_only_no_dev_test_Gold_or_independence_claim",
    "I": False,
    "F": False,
    "limits": {
        "original_bytes": MAX_ORIGINAL_BYTES,
        "original_files": MAX_ORIGINAL_FILES,
        "file_bytes": MAX_FILE_BYTES,
        "events": MAX_EVENTS,
        "source_bytes": MAX_SOURCE_BYTES,
        "raw_references": MAX_RAW_REFERENCES,
        "known_samples": MAX_KNOWN_SAMPLES,
    },
}
PROJECTION_SPEC = {
    "id": PROJECTION_BODY["id"],
    "version": "1.0.0",
    "sha256": semantic_hash(PROJECTION_BODY),
}
TARGET_BODY = {
    "schema": "stpd/native-agent-sampled-N-target-spec-v1",
    "id": "native-agent-original-delivered-handle-N",
    "version": "1.0.0",
    "N": "unique_original_handle_Act_full_C_member_and_exact_original_request_Result",
    "delivery": "delivered_with_execution_not_native_rejected",
    "ready_summary": "actual_known_complete_summary_unlabelled_no_fake_terminal",
    "expression": "unsupported_target",
    "unknown": "context_before_uncertain_action_may_survive_unlabelled_then_censored_tail",
    "features": "target_directive_receipt_script_state_and_feedback_never_enter_I_F_off_input",
    "non_claims": [
        "Human_origin",
        "teacher_private_state_equals_student_W",
        "Commit",
        "causal_successor",
        "native_game_continuity",
        "independent_generalization",
        "whole_game",
        "policy_quality",
        "training_permission",
        "real_native_fixture_origin",
    ],
}
TARGET_SPEC = {
    "id": TARGET_BODY["id"],
    "version": "1.0.0",
    "sha256": semantic_hash(TARGET_BODY),
}


# Fixed reviewed producer definition. This is a source contract, not a claim that
# arbitrary manifest bytes ran in a real game or that the collector is qualified.
TEACHER_PRODUCER: dict[str, Any] = {
    "source_commit": "014052a05e1ade73d1fd5681d2d1b875870a17df",
    "artifact_schema": "stpd/native-program-teacher-artifact-v1",
    "artifact_id": "stpd-native-public-program-teacher-50bbe123d4c4564e",
    "artifact_sha256": "50bbe123d4c4564e21c762dd5a3616c2618e7296c93cb067b3d9ba2bebac97f0",
    "adapter": {
        "id": "stpd-native-public-program-teacher",
        "version": "1.0.0",
        "protocol": "sts2.policy-runtime/agent-session-ndjson-1",
        "code_sha256": "11f14be4015d54aabbea496382550940447c36fd886dff3cbb48f661c5ec9f61",
    },
    "input_spec": {
        "id": "stpd-native-program-teacher-current-v1",
        "version": "1.0.0",
        "sha256": "d31163fbfe29bfec0be0c8c92ae5b13e4266fbe8915ff4c1762686e8cf7a6eb4",
    },
    "input_spec_body": {
        "schema": "stpd/native-program-teacher-input-spec-v1",
        "id": "stpd-native-program-teacher-current-v1",
        "version": "1.0.0",
        "profile": "native-logical-v1",
        "policy_input": "qualified_complete_current_observation_and_complete_original_C",
        "history_mode": "sampled_current",
        "eager_scope": ["persistent", "interaction", "referents", "catalog"],
        "readiness": "unchanged_unit_or_empty_nonterminal_current_no_consume_no_state_advance",
        "terminal": "qualified_public_ready_summary_may_consume_without_N",
        "state": "explicit_script_state_proposed_then_exact_ACK_committed",
        "reset": "fresh_live_session_and_explicit_segment_only",
        "state_recovery": "none",
        "I": False,
        "F": False,
        "learned": False,
    },
    "agent_spec": {
        "schema": "stpd/native-program-teacher-agent-spec-v1",
        "id": "stpd-native-public-program-teacher",
        "version": "1.0.0",
        "teacher": {"id": "native-public-demonstration-v1", "version": "1.0.2"},
        "learned": False,
        "model_bindings": [],
        "scores": None,
        "choice": "explicit_public_teacher_original_complete_C_member",
        "timing": "explicit_Await_while_expected_public_owner_or_focus_pending",
        "recheck_timeout_ms": 250,
        "arrival": "fresh_qualified_public_observation_not_delivery_timer_or_Close_proof",
        "unsupported": "Close_with_original_reason",
        "unknown_delivery": "Runtime_handoff_never_resubmit",
        "state_recovery": "none",
        "max_browse_choices": 12,
    },
    "state_format_version": "stpd/native-program-teacher-state-v1",
    "state_recovery": {"mode": "none", "max_state_bytes": 0, "model_bindings": []},
    "history_mode": "sampled_current",
    "consumption_mode": "once_per_occurrence",
    "scores": None,
    "runtime_provenance": {
        "dependency_lock_sha256": (
            "f3a1b71e5eed8eaa61adabbf8f067f342379b7110bc72840388618c1a04b78ef"
        ),
        "python_implementation": "cpython",
        "python_version": [3, 11, 15],
        "python_cache_tag": "cpython-311",
        "external_inference_dependencies": [],
    },
    "code_files": [
        {
            "path": "spireagent/__init__.py",
            "bytes": 92,
            "sha256": "a435248a0b36172b087ba28537bad85d306eb23c56962c26c7c866cb306b76f3",
        },
        {
            "path": "spireagent/encoding.py",
            "bytes": 2876,
            "sha256": "828d2bdcf6dd0b9b167ab2274c7c3e951fad2575d65b95a14c8df3f16eae9295",
        },
        {
            "path": "spireagent/json_boundary.py",
            "bytes": 3228,
            "sha256": "94ca1d9dbdabc607b20a1b2bec051dc60bf75f3f4f9513efbb87adb4e7794284",
        },
        {
            "path": "stpd/__init__.py",
            "bytes": 1077,
            "sha256": "17c7607d2e373d71b22be4463627c37188fcf7ff2864042d1dc9ad87c848529b",
        },
        {
            "path": "stpd/contracts.py",
            "bytes": 10106,
            "sha256": "2fca5fd6f67e53047361119c73680c84b69a518502b560500da57740d1e67bfa",
        },
        {
            "path": "stpd/linear_q.py",
            "bytes": 6061,
            "sha256": "d4a5c82bee955d2b83ab4bfe9e6045a735802bfc7f793f3976a229cdde964ee5",
        },
        {
            "path": "stpd/representation.py",
            "bytes": 16692,
            "sha256": "eba6fe67f52bfac2c64ddb644fb3cd893459453f93e926f74dd29caed07646b0",
        },
        {
            "path": "stpd/canonical.py",
            "bytes": 1509,
            "sha256": "9a6d37f92991ffaed680ff11b80fae962c5a51c08848effd53c41271dc34a592",
        },
        {
            "path": "stpd/fullrun/__init__.py",
            "bytes": 84,
            "sha256": "88010cf057c96baf8339b453e4036d36f2f3d4a863f5623120d578b3de8c1178",
        },
        {
            "path": "stpd/fullrun/contracts.py",
            "bytes": 17726,
            "sha256": "f22d5c384a7251450233fa6a7bf7597687df63043800480bb5852a55c937bfea",
        },
        {
            "path": "stpd/fullrun/representation.py",
            "bytes": 4774,
            "sha256": "013f6d1aea15f90739de6fbf35daf23f4b897391b868e97f0328486cd1fdf4a1",
        },
        {
            "path": "stpd/fullrun/semantic_projection.py",
            "bytes": 5683,
            "sha256": "2387b95d00a146fa3abe457b2dc5f57fda4b670352767fc713fee6e89755ea13",
        },
        {
            "path": "stpd/policy/__init__.py",
            "bytes": 1395,
            "sha256": "612bc5643dcf65fa0e065b43ce07584d46850ab3888ec80099d216024592e72f",
        },
        {
            "path": "stpd/fullrun/native_structured_inputs.py",
            "bytes": 16230,
            "sha256": "68eefdf91960bbe0af8db4985ab3fbd44e0e6f8091e715bc924f3e2664dd5fa6",
        },
        {
            "path": "stpd/fullrun/native_structured_sequences.py",
            "bytes": 4956,
            "sha256": "070584ef0a77f0fd034f4abf23caadf0f20311845f7f4d7fff7121e4a44c9e99",
        },
        {
            "path": "stpd/fullrun/structured_inputs.py",
            "bytes": 6370,
            "sha256": "5a95e168f528b8c6e1be3ad93deb62c0d93355c0354f8111f2993073cadf7d62",
        },
        {
            "path": "stpd/fullrun/structured_tree.py",
            "bytes": 15922,
            "sha256": "f07626ca4a61f2fa446b5ac19c0900e442045047928b952ca198c9ac67b5b49a",
        },
        {
            "path": "stpd/fullrun/text_menu_inputs.py",
            "bytes": 21063,
            "sha256": "5fa9d79468330e31481c2d557af08a42afee9f6a7ab5aa8adc8b6b8ffe5b1d5f",
        },
        {
            "path": "stpd/policy/native_public_teacher.py",
            "bytes": 16765,
            "sha256": "759ca9468fdd16ca33dbd592411def477a5b41f0692f7bbc3b5498eb425f4dd9",
        },
        {
            "path": "stpd/policy/native_teacher_agent.py",
            "bytes": 19481,
            "sha256": "b6d1be5941936c7f3b4c46b9d78bfe710472c132ca44a67dca745af35089794f",
        },
        {
            "path": "stpd/policy/native_task.py",
            "bytes": 3628,
            "sha256": "336f589c6e1bea742e8b4f0ed53d67feca9b9b29c1aacba55708c98c7f6f3754",
        },
    ],
}
TEACHER_COHORT = "declared_native_machine_teacher"
TEACHER_RELATION_BODY = {
    "schema": "stpd/native-agent-sampled-producer-student-relation-v1",
    "id": "native-program-teacher-current-to-sampled-student-v1",
    "version": "1.0.0",
    "source_kind": SOURCE_KIND,
    "supported_declarations": [FIXTURE_COHORT, TEACHER_COHORT],
    "producer_definition": TEACHER_PRODUCER,
    "producer_input_spec": TEACHER_PRODUCER["input_spec"],
    "producer_input_spec_body_sha256": TEACHER_PRODUCER["input_spec"]["sha256"],
    "producer_input_spec_body_encoding": "canonical_json_with_one_newline",
    "producer_wire_projection": {
        key: TEACHER_PRODUCER["input_spec"][key] for key in ("id", "version")
    },
    "producer_state_format": TEACHER_PRODUCER["state_format_version"],
    "student_input_spec": INPUT_SPEC,
    "student_input_spec_body_sha256": semantic_hash(INPUT_SPEC_BODY),
    "student_input_spec_body_encoding": "canonical_json_without_terminal_newline",
    "feature_projection": FEATURE_PROJECTION,
    "private_state": "explicit_teacher_script_state_is_not_student_W_or_I_F",
    "real_native_origin": "separate_explicit_declaration_and_execution_qualification_required",
}
TEACHER_RELATION_SPEC = {
    "id": TEACHER_RELATION_BODY["id"],
    "version": "1.0.0",
    "sha256": semantic_hash(TEACHER_RELATION_BODY),
}
TEACHER_PROJECTION_BODY = {
    **PROJECTION_BODY,
    "id": "native-program-teacher-known-samples-student-carry",
    "producer_student_relation": TEACHER_RELATION_SPEC,
}
TEACHER_PROJECTION_SPEC = {
    "id": TEACHER_PROJECTION_BODY["id"],
    "version": "1.0.0",
    "sha256": semantic_hash(TEACHER_PROJECTION_BODY),
}
TEACHER_QUALIFICATION = "declared_native_program_teacher_known_samples_student_reexpression_only"


# New fixed Map-timed teacher candidate; historical1.0.2 definitions above remain immutable.
MAP_TEACHER_PRODUCER: dict[str, Any] = {
    "source_commit": "ccfebd7c851667702f12736e56784abada97cc05",
    "artifact_schema": "stpd/native-program-teacher-artifact-v1",
    "artifact_id": "stpd-native-public-program-teacher-e590c1ec158d6369",
    "artifact_sha256": "e590c1ec158d636909e9a3bcddd539b46e8e2e3a72c4303d721019635cb4ecac",
    "adapter": {
        "code_sha256": "ab4380a02a6969acb38a55697d6cf695f727b3e6f1cf0aacd24647185d19413b",
        "id": "stpd-native-public-program-teacher",
        "protocol": "sts2.policy-runtime/agent-session-ndjson-1",
        "version": "1.1.0",
    },
    "input_spec": {
        "id": "stpd-native-program-teacher-current-v1",
        "sha256": "d31163fbfe29bfec0be0c8c92ae5b13e4266fbe8915ff4c1762686e8cf7a6eb4",
        "version": "1.0.0",
    },
    "input_spec_body": {
        "F": False,
        "I": False,
        "eager_scope": ["persistent", "interaction", "referents", "catalog"],
        "history_mode": "sampled_current",
        "id": "stpd-native-program-teacher-current-v1",
        "learned": False,
        "policy_input": "qualified_complete_current_observation_and_complete_original_C",
        "profile": "native-logical-v1",
        "readiness": "unchanged_unit_or_empty_nonterminal_current_no_consume_no_state_advance",
        "reset": "fresh_live_session_and_explicit_segment_only",
        "schema": "stpd/native-program-teacher-input-spec-v1",
        "state": "explicit_script_state_proposed_then_exact_ACK_committed",
        "state_recovery": "none",
        "terminal": "qualified_public_ready_summary_may_consume_without_N",
        "version": "1.0.0",
    },
    "agent_spec": {
        "arrival": "fresh_qualified_public_observation_not_delivery_timer_or_Close_proof",
        "choice": "explicit_public_teacher_original_complete_C_member",
        "id": "stpd-native-public-program-teacher",
        "learned": False,
        "max_browse_choices": 12,
        "model_bindings": [],
        "recheck_timeout_ms": 250,
        "schema": "stpd/native-program-teacher-agent-spec-v1",
        "scores": None,
        "state_recovery": "none",
        "task_spec": {
            "administrative_actions": [],
            "censoring": [
                "budget",
                "disconnect",
                "unknown_delivery",
                "source_gap",
                "unsupported_surface",
                "external_stop",
            ],
            "completion": "qualified_public_game_over_summary_ready",
            "goal": "one_native_run_to_ready_terminal_summary",
            "id": "native-standard-run-ready-summary",
            "navigation": (
                "fixed_public_map_travel_timing_then_AgentSpec_choice_until_summary_ready"
            ),
            "return_to_menu_required": False,
            "schema": "stpd/native-task-spec-v1",
            "timing_learned": False,
            "version": "1.1.0",
        },
        "teacher": {"id": "native-public-demonstration-v1", "version": "1.0.4"},
        "timing": "explicit_Await_public_map_travel_or_expected_public_owner_or_focus_pending",
        "timing_policy": {
            "catalog": "complete_unchanged",
            "id": "stpd-native-public-map-travel-timing-v1",
            "learned": False,
            "otherwise": "AgentSpec_declared_choice",
            "positive": "Await_known_cursor_any_event_after_exact_ACK",
            "predicate": "exact_native_map_information_page_map_navigation_schema_traveling_true",
            "recheck_timeout_ms": 250,
            "version": "1.0.0",
        },
        "unknown_delivery": "Runtime_handoff_never_resubmit",
        "unsupported": "Close_with_original_reason",
        "version": "1.1.0",
    },
    "runtime_provenance": {
        "dependency_lock_sha256": (
            "f3a1b71e5eed8eaa61adabbf8f067f342379b7110bc72840388618c1a04b78ef"
        ),
        "external_inference_dependencies": [],
        "python_cache_tag": "cpython-311",
        "python_implementation": "cpython",
        "python_version": [3, 11, 15],
    },
    "code_files": [
        {
            "bytes": 92,
            "path": "spireagent/__init__.py",
            "sha256": "a435248a0b36172b087ba28537bad85d306eb23c56962c26c7c866cb306b76f3",
        },
        {
            "bytes": 2876,
            "path": "spireagent/encoding.py",
            "sha256": "828d2bdcf6dd0b9b167ab2274c7c3e951fad2575d65b95a14c8df3f16eae9295",
        },
        {
            "bytes": 3228,
            "path": "spireagent/json_boundary.py",
            "sha256": "94ca1d9dbdabc607b20a1b2bec051dc60bf75f3f4f9513efbb87adb4e7794284",
        },
        {
            "bytes": 1077,
            "path": "stpd/__init__.py",
            "sha256": "17c7607d2e373d71b22be4463627c37188fcf7ff2864042d1dc9ad87c848529b",
        },
        {
            "bytes": 10106,
            "path": "stpd/contracts.py",
            "sha256": "2fca5fd6f67e53047361119c73680c84b69a518502b560500da57740d1e67bfa",
        },
        {
            "bytes": 6061,
            "path": "stpd/linear_q.py",
            "sha256": "d4a5c82bee955d2b83ab4bfe9e6045a735802bfc7f793f3976a229cdde964ee5",
        },
        {
            "bytes": 16692,
            "path": "stpd/representation.py",
            "sha256": "eba6fe67f52bfac2c64ddb644fb3cd893459453f93e926f74dd29caed07646b0",
        },
        {
            "bytes": 1509,
            "path": "stpd/canonical.py",
            "sha256": "9a6d37f92991ffaed680ff11b80fae962c5a51c08848effd53c41271dc34a592",
        },
        {
            "bytes": 84,
            "path": "stpd/fullrun/__init__.py",
            "sha256": "88010cf057c96baf8339b453e4036d36f2f3d4a863f5623120d578b3de8c1178",
        },
        {
            "bytes": 17726,
            "path": "stpd/fullrun/contracts.py",
            "sha256": "f22d5c384a7251450233fa6a7bf7597687df63043800480bb5852a55c937bfea",
        },
        {
            "bytes": 4774,
            "path": "stpd/fullrun/representation.py",
            "sha256": "013f6d1aea15f90739de6fbf35daf23f4b897391b868e97f0328486cd1fdf4a1",
        },
        {
            "bytes": 5683,
            "path": "stpd/fullrun/semantic_projection.py",
            "sha256": "2387b95d00a146fa3abe457b2dc5f57fda4b670352767fc713fee6e89755ea13",
        },
        {
            "bytes": 1395,
            "path": "stpd/policy/__init__.py",
            "sha256": "612bc5643dcf65fa0e065b43ce07584d46850ab3888ec80099d216024592e72f",
        },
        {
            "bytes": 16230,
            "path": "stpd/fullrun/native_structured_inputs.py",
            "sha256": "68eefdf91960bbe0af8db4985ab3fbd44e0e6f8091e715bc924f3e2664dd5fa6",
        },
        {
            "bytes": 4956,
            "path": "stpd/fullrun/native_structured_sequences.py",
            "sha256": "070584ef0a77f0fd034f4abf23caadf0f20311845f7f4d7fff7121e4a44c9e99",
        },
        {
            "bytes": 6370,
            "path": "stpd/fullrun/structured_inputs.py",
            "sha256": "5a95e168f528b8c6e1be3ad93deb62c0d93355c0354f8111f2993073cadf7d62",
        },
        {
            "bytes": 15922,
            "path": "stpd/fullrun/structured_tree.py",
            "sha256": "f07626ca4a61f2fa446b5ac19c0900e442045047928b952ca198c9ac67b5b49a",
        },
        {
            "bytes": 21063,
            "path": "stpd/fullrun/text_menu_inputs.py",
            "sha256": "5fa9d79468330e31481c2d557af08a42afee9f6a7ab5aa8adc8b6b8ffe5b1d5f",
        },
        {
            "bytes": 16984,
            "path": "stpd/policy/native_public_teacher.py",
            "sha256": "c1d4256689cc9382ccf3b1c3fac4322450284afd0af250cb5b96ac5b61765321",
        },
        {
            "bytes": 19687,
            "path": "stpd/policy/native_teacher_agent.py",
            "sha256": "3b458ee46860ab3bcb475b5f53266c99b0d6ce99a640e691a96d2fa02a1fd316",
        },
        {
            "bytes": 5451,
            "path": "stpd/policy/native_task.py",
            "sha256": "fab346c1ca7b7f75fc7e7822b8209a8ae185a750a0590219539663b11137e5c2",
        },
    ],
    "state_format_version": "stpd/native-program-teacher-state-v1",
    "state_recovery": {"mode": "none", "max_state_bytes": 0, "model_bindings": []},
    "history_mode": "sampled_current",
    "consumption_mode": "once_per_occurrence",
    "scores": None,
}
MAP_TEACHER_RELATION_BODY = {
    **TEACHER_RELATION_BODY,
    "id": "native-program-teacher-map-timed-current-to-sampled-student-v1",
    "producer_definition": MAP_TEACHER_PRODUCER,
    "producer_input_spec": MAP_TEACHER_PRODUCER["input_spec"],
}
MAP_TEACHER_RELATION_SPEC = {
    "id": MAP_TEACHER_RELATION_BODY["id"],
    "version": "1.0.0",
    "sha256": semantic_hash(MAP_TEACHER_RELATION_BODY),
}
MAP_TEACHER_PROJECTION_BODY = {
    **PROJECTION_BODY,
    "id": "native-program-teacher-map-timed-known-samples-student-carry",
    "producer_student_relation": MAP_TEACHER_RELATION_SPEC,
}
MAP_TEACHER_PROJECTION_SPEC = {
    "id": MAP_TEACHER_PROJECTION_BODY["id"],
    "version": "1.0.0",
    "sha256": semantic_hash(MAP_TEACHER_PROJECTION_BODY),
}


# New current-focus Teacher1.0.5; both historical producer definitions stay fixed.
FOCUS_TEACHER_PRODUCER: dict[str, Any] = {
    "source_commit": "66a4162b495522d777691ae814508a2e10bcc2b0",
    "artifact_schema": "stpd/native-program-teacher-artifact-v1",
    "artifact_id": "stpd-native-public-program-teacher-13e0b1361e83feba",
    "artifact_sha256": "13e0b1361e83feba2f58f3384dec152048f05284044bbbf5987c3592bd18a5c2",
    "adapter": {
        "code_sha256": "0965453b4565729454f4f95149067b6be3b175283805ef76d3c644b1e2725cc0",
        "id": "stpd-native-public-program-teacher",
        "protocol": "sts2.policy-runtime/agent-session-ndjson-1",
        "version": "1.1.0"
    },
    "input_spec": {
        "id": "stpd-native-program-teacher-current-v1",
        "sha256": "d31163fbfe29bfec0be0c8c92ae5b13e4266fbe8915ff4c1762686e8cf7a6eb4",
        "version": "1.0.0"
    },
    "input_spec_body": {
        "F": False,
        "I": False,
        "eager_scope": [
            "persistent",
            "interaction",
            "referents",
            "catalog"
        ],
        "history_mode": "sampled_current",
        "id": "stpd-native-program-teacher-current-v1",
        "learned": False,
        "policy_input": "qualified_complete_current_observation_and_complete_original_C",
        "profile": "native-logical-v1",
        "readiness": "unchanged_unit_or_empty_nonterminal_current_no_consume_no_state_advance",
        "reset": "fresh_live_session_and_explicit_segment_only",
        "schema": "stpd/native-program-teacher-input-spec-v1",
        "state": "explicit_script_state_proposed_then_exact_ACK_committed",
        "state_recovery": "none",
        "terminal": "qualified_public_ready_summary_may_consume_without_N",
        "version": "1.0.0"
    },
    "agent_spec": {
        "arrival": "fresh_qualified_public_observation_not_delivery_timer_or_Close_proof",
        "choice": "explicit_public_teacher_original_complete_C_member",
        "id": "stpd-native-public-program-teacher",
        "learned": False,
        "max_browse_choices": 12,
        "model_bindings": [],
        "recheck_timeout_ms": 250,
        "schema": "stpd/native-program-teacher-agent-spec-v1",
        "scores": None,
        "state_recovery": "none",
        "task_spec": {
            "administrative_actions": [],
            "censoring": [
                "budget",
                "disconnect",
                "unknown_delivery",
                "source_gap",
                "unsupported_surface",
                "external_stop"
            ],
            "completion": "qualified_public_game_over_summary_ready",
            "goal": "one_native_run_to_ready_terminal_summary",
            "id": "native-standard-run-ready-summary",
            "navigation": (
                "fixed_public_map_travel_timing_then_AgentSpec_choice_until_summary_ready"
            ),
            "return_to_menu_required": False,
            "schema": "stpd/native-task-spec-v1",
            "timing_learned": False,
            "version": "1.1.0"
        },
        "teacher": {
            "id": "native-public-demonstration-v1",
            "version": "1.0.5"
        },
        "timing": "explicit_Await_public_map_travel_or_expected_public_owner_or_focus_pending",
        "timing_policy": {
            "catalog": "complete_unchanged",
            "id": "stpd-native-public-map-travel-timing-v1",
            "learned": False,
            "otherwise": "AgentSpec_declared_choice",
            "positive": "Await_known_cursor_any_event_after_exact_ACK",
            "predicate": "exact_native_map_information_page_map_navigation_schema_traveling_true",
            "recheck_timeout_ms": 250,
            "version": "1.0.0"
        },
        "unknown_delivery": "Runtime_handoff_never_resubmit",
        "unsupported": "Close_with_original_reason",
        "version": "1.1.0"
    },
    "runtime_provenance": {
        "dependency_lock_sha256": (
            "f3a1b71e5eed8eaa61adabbf8f067f342379b7110bc72840388618c1a04b78ef"
        ),
        "external_inference_dependencies": [],
        "python_cache_tag": "cpython-311",
        "python_implementation": "cpython",
        "python_version": [
            3,
            11,
            15
        ]
    },
    "code_files": [
        {
            "bytes": 92,
            "path": "spireagent/__init__.py",
            "sha256": "a435248a0b36172b087ba28537bad85d306eb23c56962c26c7c866cb306b76f3"
        },
        {
            "bytes": 2876,
            "path": "spireagent/encoding.py",
            "sha256": "828d2bdcf6dd0b9b167ab2274c7c3e951fad2575d65b95a14c8df3f16eae9295"
        },
        {
            "bytes": 3228,
            "path": "spireagent/json_boundary.py",
            "sha256": "94ca1d9dbdabc607b20a1b2bec051dc60bf75f3f4f9513efbb87adb4e7794284"
        },
        {
            "bytes": 1077,
            "path": "stpd/__init__.py",
            "sha256": "17c7607d2e373d71b22be4463627c37188fcf7ff2864042d1dc9ad87c848529b"
        },
        {
            "bytes": 10106,
            "path": "stpd/contracts.py",
            "sha256": "2fca5fd6f67e53047361119c73680c84b69a518502b560500da57740d1e67bfa"
        },
        {
            "bytes": 6061,
            "path": "stpd/linear_q.py",
            "sha256": "d4a5c82bee955d2b83ab4bfe9e6045a735802bfc7f793f3976a229cdde964ee5"
        },
        {
            "bytes": 16692,
            "path": "stpd/representation.py",
            "sha256": "eba6fe67f52bfac2c64ddb644fb3cd893459453f93e926f74dd29caed07646b0"
        },
        {
            "bytes": 1509,
            "path": "stpd/canonical.py",
            "sha256": "9a6d37f92991ffaed680ff11b80fae962c5a51c08848effd53c41271dc34a592"
        },
        {
            "bytes": 84,
            "path": "stpd/fullrun/__init__.py",
            "sha256": "88010cf057c96baf8339b453e4036d36f2f3d4a863f5623120d578b3de8c1178"
        },
        {
            "bytes": 17726,
            "path": "stpd/fullrun/contracts.py",
            "sha256": "f22d5c384a7251450233fa6a7bf7597687df63043800480bb5852a55c937bfea"
        },
        {
            "bytes": 4774,
            "path": "stpd/fullrun/representation.py",
            "sha256": "013f6d1aea15f90739de6fbf35daf23f4b897391b868e97f0328486cd1fdf4a1"
        },
        {
            "bytes": 5683,
            "path": "stpd/fullrun/semantic_projection.py",
            "sha256": "2387b95d00a146fa3abe457b2dc5f57fda4b670352767fc713fee6e89755ea13"
        },
        {
            "bytes": 1395,
            "path": "stpd/policy/__init__.py",
            "sha256": "612bc5643dcf65fa0e065b43ce07584d46850ab3888ec80099d216024592e72f"
        },
        {
            "bytes": 16230,
            "path": "stpd/fullrun/native_structured_inputs.py",
            "sha256": "68eefdf91960bbe0af8db4985ab3fbd44e0e6f8091e715bc924f3e2664dd5fa6"
        },
        {
            "bytes": 4956,
            "path": "stpd/fullrun/native_structured_sequences.py",
            "sha256": "070584ef0a77f0fd034f4abf23caadf0f20311845f7f4d7fff7121e4a44c9e99"
        },
        {
            "bytes": 6370,
            "path": "stpd/fullrun/structured_inputs.py",
            "sha256": "5a95e168f528b8c6e1be3ad93deb62c0d93355c0354f8111f2993073cadf7d62"
        },
        {
            "bytes": 15922,
            "path": "stpd/fullrun/structured_tree.py",
            "sha256": "f07626ca4a61f2fa446b5ac19c0900e442045047928b952ca198c9ac67b5b49a"
        },
        {
            "bytes": 21063,
            "path": "stpd/fullrun/text_menu_inputs.py",
            "sha256": "5fa9d79468330e31481c2d557af08a42afee9f6a7ab5aa8adc8b6b8ffe5b1d5f"
        },
        {
            "bytes": 17709,
            "path": "stpd/policy/native_public_teacher.py",
            "sha256": "46479831e94092e53b6e6d69fba77b1f160f32cf4807c071788fc46f446f3e25"
        },
        {
            "bytes": 19687,
            "path": "stpd/policy/native_teacher_agent.py",
            "sha256": "3b458ee46860ab3bcb475b5f53266c99b0d6ce99a640e691a96d2fa02a1fd316"
        },
        {
            "bytes": 5451,
            "path": "stpd/policy/native_task.py",
            "sha256": "fab346c1ca7b7f75fc7e7822b8209a8ae185a750a0590219539663b11137e5c2"
        }
    ],
    "state_format_version": "stpd/native-program-teacher-state-v1",
    "state_recovery": {
        "mode": "none",
        "max_state_bytes": 0,
        "model_bindings": []
    },
    "history_mode": "sampled_current",
    "consumption_mode": "once_per_occurrence",
    "scores": None
}
FOCUS_TEACHER_RELATION_BODY = {
    **TEACHER_RELATION_BODY,
    "id": "native-program-teacher-current-focus-to-sampled-student-v1",
    "producer_definition": FOCUS_TEACHER_PRODUCER,
    "producer_input_spec": FOCUS_TEACHER_PRODUCER["input_spec"],
}
FOCUS_TEACHER_RELATION_SPEC = {
    "id": FOCUS_TEACHER_RELATION_BODY["id"],
    "version": "1.0.0",
    "sha256": semantic_hash(FOCUS_TEACHER_RELATION_BODY),
}
FOCUS_TEACHER_PROJECTION_BODY = {
    **PROJECTION_BODY,
    "id": "native-program-teacher-current-focus-known-samples-student-carry",
    "producer_student_relation": FOCUS_TEACHER_RELATION_SPEC,
}
FOCUS_TEACHER_PROJECTION_SPEC = {
    "id": FOCUS_TEACHER_PROJECTION_BODY["id"],
    "version": "1.0.0",
    "sha256": semantic_hash(FOCUS_TEACHER_PROJECTION_BODY),
}


# Fixed Teacher1.0.6 after exact Evidence E/pin/lock/installed-verifier promotion.
# Historical1.0.5/1.0.4/1.0.2 definitions above retain their original bytes.
OWNED_STALE_TEACHER_PRODUCER: dict[str, Any] = {
    "source_commit": "10a18a3c3ec01326de77f8cd5c50c27c640e441d",
    "artifact_schema": "stpd/native-program-teacher-artifact-v1",
    "artifact_id": "stpd-native-public-program-teacher-2c13312124738ce3",
    "artifact_sha256": "2c13312124738ce317aac236ba901ec0d8d4d0bd50fb43e0cb8dd3ef1f54df0f",
    "adapter": {
        "code_sha256": "aafcfed4885967690ddd97e000cc5aa4b783fc8f5ce64ccce23520c99c3ed57a",
        "id": "stpd-native-public-program-teacher",
        "protocol": "sts2.policy-runtime/agent-session-ndjson-1",
        "version": "1.2.0",
    },
    "input_spec": {
        "id": "stpd-native-program-teacher-current-v1",
        "sha256": "d31163fbfe29bfec0be0c8c92ae5b13e4266fbe8915ff4c1762686e8cf7a6eb4",
        "version": "1.0.0",
    },
    "input_spec_body": {
        "F": False,
        "I": False,
        "eager_scope": ["persistent", "interaction", "referents", "catalog"],
        "history_mode": "sampled_current",
        "id": "stpd-native-program-teacher-current-v1",
        "learned": False,
        "policy_input": "qualified_complete_current_observation_and_complete_original_C",
        "profile": "native-logical-v1",
        "readiness": "unchanged_unit_or_empty_nonterminal_current_no_consume_no_state_advance",
        "reset": "fresh_live_session_and_explicit_segment_only",
        "schema": "stpd/native-program-teacher-input-spec-v1",
        "state": "explicit_script_state_proposed_then_exact_ACK_committed",
        "state_recovery": "none",
        "terminal": "qualified_public_ready_summary_may_consume_without_N",
        "version": "1.0.0",
    },
    "agent_spec": {
        "arrival": "fresh_qualified_public_observation_not_delivery_timer_or_Close_proof",
        "choice": "explicit_public_teacher_original_complete_C_member",
        "execution_policy": {
            "current_mode": "reader_owned_v1",
            "known_stale": "fresh_changed_current_v1",
            "max_consecutive_known_stale_rejections": 3,
            "max_known_stale_rejections": 8,
            "operational_outcome": "known_not_started_v1",
            "schema": "sts2.policy-runtime/agent-execution-policy-1",
        },
        "id": "stpd-native-public-program-teacher",
        "intention_correction": "changed_native_unit_and_exact_new_ConsumeACK_only",
        "learned": False,
        "max_browse_choices": 12,
        "model_bindings": [],
        "operational_outcome": "validate_last_emitted_intention_then_stage_narrow_correction",
        "recheck_timeout_ms": 250,
        "schema": "stpd/native-program-teacher-agent-spec-v1",
        "scores": None,
        "state_recovery": "none",
        "task_spec": {
            "administrative_actions": [],
            "censoring": [
                "budget",
                "disconnect",
                "unknown_delivery",
                "source_gap",
                "unsupported_surface",
                "external_stop",
            ],
            "completion": "qualified_public_game_over_summary_ready",
            "goal": "one_native_run_to_ready_terminal_summary",
            "id": "native-standard-run-ready-summary",
            "navigation": (
                "fixed_public_map_travel_timing_then_AgentSpec_choice_until_summary_ready"
            ),
            "return_to_menu_required": False,
            "schema": "stpd/native-task-spec-v1",
            "timing_learned": False,
            "version": "1.1.0",
        },
        "teacher": {"id": "native-public-demonstration-v1", "version": "1.0.6"},
        "timing": "explicit_Await_public_map_travel_or_expected_public_owner_or_focus_pending",
        "timing_policy": {
            "catalog": "complete_unchanged",
            "id": "stpd-native-public-map-travel-timing-v1",
            "learned": False,
            "otherwise": "AgentSpec_declared_choice",
            "positive": "Await_known_cursor_any_event_after_exact_ACK",
            "predicate": "exact_native_map_information_page_map_navigation_schema_traveling_true",
            "recheck_timeout_ms": 250,
            "version": "1.0.0",
        },
        "unknown_delivery": "Runtime_handoff_never_resubmit",
        "unsupported": "Close_with_original_reason",
        "version": "1.2.0",
    },
    "code_files": [
        {
            "bytes": 92,
            "path": "spireagent/__init__.py",
            "sha256": "a435248a0b36172b087ba28537bad85d306eb23c56962c26c7c866cb306b76f3",
        },
        {
            "bytes": 2876,
            "path": "spireagent/encoding.py",
            "sha256": "828d2bdcf6dd0b9b167ab2274c7c3e951fad2575d65b95a14c8df3f16eae9295",
        },
        {
            "bytes": 3228,
            "path": "spireagent/json_boundary.py",
            "sha256": "94ca1d9dbdabc607b20a1b2bec051dc60bf75f3f4f9513efbb87adb4e7794284",
        },
        {
            "bytes": 1077,
            "path": "stpd/__init__.py",
            "sha256": "17c7607d2e373d71b22be4463627c37188fcf7ff2864042d1dc9ad87c848529b",
        },
        {
            "bytes": 10106,
            "path": "stpd/contracts.py",
            "sha256": "2fca5fd6f67e53047361119c73680c84b69a518502b560500da57740d1e67bfa",
        },
        {
            "bytes": 6061,
            "path": "stpd/linear_q.py",
            "sha256": "d4a5c82bee955d2b83ab4bfe9e6045a735802bfc7f793f3976a229cdde964ee5",
        },
        {
            "bytes": 16692,
            "path": "stpd/representation.py",
            "sha256": "eba6fe67f52bfac2c64ddb644fb3cd893459453f93e926f74dd29caed07646b0",
        },
        {
            "bytes": 1509,
            "path": "stpd/canonical.py",
            "sha256": "9a6d37f92991ffaed680ff11b80fae962c5a51c08848effd53c41271dc34a592",
        },
        {
            "bytes": 84,
            "path": "stpd/fullrun/__init__.py",
            "sha256": "88010cf057c96baf8339b453e4036d36f2f3d4a863f5623120d578b3de8c1178",
        },
        {
            "bytes": 17726,
            "path": "stpd/fullrun/contracts.py",
            "sha256": "f22d5c384a7251450233fa6a7bf7597687df63043800480bb5852a55c937bfea",
        },
        {
            "bytes": 4774,
            "path": "stpd/fullrun/representation.py",
            "sha256": "013f6d1aea15f90739de6fbf35daf23f4b897391b868e97f0328486cd1fdf4a1",
        },
        {
            "bytes": 5683,
            "path": "stpd/fullrun/semantic_projection.py",
            "sha256": "2387b95d00a146fa3abe457b2dc5f57fda4b670352767fc713fee6e89755ea13",
        },
        {
            "bytes": 1395,
            "path": "stpd/policy/__init__.py",
            "sha256": "612bc5643dcf65fa0e065b43ce07584d46850ab3888ec80099d216024592e72f",
        },
        {
            "bytes": 16230,
            "path": "stpd/fullrun/native_structured_inputs.py",
            "sha256": "68eefdf91960bbe0af8db4985ab3fbd44e0e6f8091e715bc924f3e2664dd5fa6",
        },
        {
            "bytes": 4956,
            "path": "stpd/fullrun/native_structured_sequences.py",
            "sha256": "070584ef0a77f0fd034f4abf23caadf0f20311845f7f4d7fff7121e4a44c9e99",
        },
        {
            "bytes": 6370,
            "path": "stpd/fullrun/structured_inputs.py",
            "sha256": "5a95e168f528b8c6e1be3ad93deb62c0d93355c0354f8111f2993073cadf7d62",
        },
        {
            "bytes": 15922,
            "path": "stpd/fullrun/structured_tree.py",
            "sha256": "f07626ca4a61f2fa446b5ac19c0900e442045047928b952ca198c9ac67b5b49a",
        },
        {
            "bytes": 21063,
            "path": "stpd/fullrun/text_menu_inputs.py",
            "sha256": "5fa9d79468330e31481c2d557af08a42afee9f6a7ab5aa8adc8b6b8ffe5b1d5f",
        },
        {
            "bytes": 17779,
            "path": "stpd/policy/native_public_teacher.py",
            "sha256": "76706155f079da38fb4daea700932eacc0aca1c8835c2622e89ae8194d0f435d",
        },
        {
            "bytes": 23933,
            "path": "stpd/policy/native_teacher_agent.py",
            "sha256": "6365a3292103aea754dc64dbe352471ee00f52aa1aaa6c0ea0e638c48fdfae56",
        },
        {
            "bytes": 10672,
            "path": "stpd/policy/native_operational_outcome.py",
            "sha256": "67d12532ab28c634619a63ef0aaf0124137223da6df300b94177082d303076e7",
        },
        {
            "bytes": 5451,
            "path": "stpd/policy/native_task.py",
            "sha256": "fab346c1ca7b7f75fc7e7822b8209a8ae185a750a0590219539663b11137e5c2",
        },
    ],
    "runtime_provenance": {
        "dependency_lock_sha256": (
            "46eb838668b984bc4f0cab203a7b3c759115cf05fe2107b21a1730dd9808feea"
        ),
        "external_inference_dependencies": [],
        "python_cache_tag": "cpython-311",
        "python_implementation": "cpython",
        "python_version": [3, 11, 15],
    },
    "state_format_version": "stpd/native-program-teacher-state-v1",
    "state_recovery": {"mode": "none", "max_state_bytes": 0, "model_bindings": []},
    "history_mode": "sampled_current",
    "consumption_mode": "once_per_occurrence",
    "scores": None,
    "execution_policy": {
        "current_mode": "reader_owned_v1",
        "known_stale": "fresh_changed_current_v1",
        "max_consecutive_known_stale_rejections": 3,
        "max_known_stale_rejections": 8,
        "operational_outcome": "known_not_started_v1",
        "schema": "sts2.policy-runtime/agent-execution-policy-1",
    },
}
OWNED_STALE_TEACHER_RELATION_BODY = {
    **TEACHER_RELATION_BODY,
    "id": "native-program-teacher-owned-stale-to-sampled-student-v1",
    "producer_definition": OWNED_STALE_TEACHER_PRODUCER,
    "producer_input_spec": OWNED_STALE_TEACHER_PRODUCER["input_spec"],
}
OWNED_STALE_TEACHER_RELATION_SPEC = {
    "id": OWNED_STALE_TEACHER_RELATION_BODY["id"],
    "version": "1.0.0",
    "sha256": semantic_hash(OWNED_STALE_TEACHER_RELATION_BODY),
}
OWNED_STALE_TEACHER_PROJECTION_BODY = {
    **PROJECTION_BODY,
    "id": "native-program-teacher-owned-stale-known-samples-student-carry",
    "producer_student_relation": OWNED_STALE_TEACHER_RELATION_SPEC,
}
OWNED_STALE_TEACHER_PROJECTION_SPEC = {
    "id": OWNED_STALE_TEACHER_PROJECTION_BODY["id"],
    "version": "1.0.0",
    "sha256": semantic_hash(OWNED_STALE_TEACHER_PROJECTION_BODY),
}


def _closed_relations() -> tuple[
    tuple[tuple[str, ...], dict[str, Any], dict[str, Any], dict[str, Any]], ...
]:
    """Only frozen producer/relation/projection tuples; no executable registry."""
    return (
        ((FIXTURE_COHORT, TEACHER_COHORT), OWNED_STALE_TEACHER_RELATION_BODY,
         OWNED_STALE_TEACHER_RELATION_SPEC, OWNED_STALE_TEACHER_PROJECTION_SPEC),
        ((FIXTURE_COHORT, TEACHER_COHORT), FOCUS_TEACHER_RELATION_BODY,
         FOCUS_TEACHER_RELATION_SPEC, FOCUS_TEACHER_PROJECTION_SPEC),
        ((FIXTURE_COHORT, TEACHER_COHORT), MAP_TEACHER_RELATION_BODY,
         MAP_TEACHER_RELATION_SPEC, MAP_TEACHER_PROJECTION_SPEC),
        ((FIXTURE_COHORT,), RELATION_BODY, RELATION_SPEC, PROJECTION_SPEC),
        ((FIXTURE_COHORT, TEACHER_COHORT), TEACHER_RELATION_BODY,
         TEACHER_RELATION_SPEC, TEACHER_PROJECTION_SPEC),
    )


def _relation_tuple(
    relation: object, cohort: object
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not isinstance(cohort, str) or not isinstance(relation, dict):
        raise BoundaryError("native_agent_sampled_source", "unsupported_producer_student_relation")
    for cohorts, body, spec, projection in _closed_relations():
        if cohort in cohorts and json_bytes(relation) == json_bytes(spec):
            return body, spec, projection
    raise BoundaryError("native_agent_sampled_source", "unsupported_producer_student_relation")


def relation_body(relation: object, cohort: object) -> dict[str, Any]:
    body, _, _ = _relation_tuple(relation, cohort)
    return copy.deepcopy(body)


def checked_relation(relation: object, cohort: object) -> dict[str, Any]:
    _, spec, _ = _relation_tuple(relation, cohort)
    return copy.deepcopy(spec)


def relation_specs(relation: object, cohort: object) -> tuple[dict[str, Any], dict[str, Any]]:
    _, _, projection = _relation_tuple(relation, cohort)
    return copy.deepcopy(projection), copy.deepcopy(TARGET_SPEC)


def qualification(relation: object, cohort: object) -> str:
    relation_body(relation, cohort)
    return QUALIFICATION if cohort == FIXTURE_COHORT else TEACHER_QUALIFICATION


def checked_specs(
    projection: object,
    target: object,
    relation: object = RELATION_SPEC,
    cohort: object = FIXTURE_COHORT,
) -> None:
    expected_projection, expected_target = relation_specs(relation, cohort)
    if json_bytes(projection) != json_bytes(expected_projection) or json_bytes(
        target
    ) != json_bytes(expected_target):
        raise BoundaryError("native_agent_sampled_source", "projection_target_identity")


def validation_identity(
    source_sha256: str,
    cohort: str,
    relation: object,
    raw_refs: object,
    *,
    projection_spec: object,
    target_spec: object,
) -> dict[str, Any]:
    """Exact source/contract references; only the source owner proves the immutable raw replay."""
    from spireagent.json_boundary import digest, object_fields

    digest(source_sha256, "native_agent_sampled.source_sha256")
    checked_relation(relation, cohort)
    checked_specs(projection_spec, target_spec, relation, cohort)
    if not isinstance(raw_refs, list) or not 0 < len(raw_refs) <= MAX_RAW_REFERENCES:
        raise BoundaryError("native_agent_sampled_source", "typed_raw_references_required")
    refs = [
        object_fields(ref, {"raw_id", "admission_id"}, "native_agent_sampled.reference")
        for ref in raw_refs
    ]
    for ref in refs:
        digest(ref["raw_id"], "native_agent_sampled.raw_id")
        digest(ref["admission_id"], "native_agent_sampled.admission_id")
    if len({r["raw_id"] for r in refs}) != len(refs) or refs != sorted(
        refs, key=lambda r: r["raw_id"]
    ):
        raise BoundaryError("native_agent_sampled_source", "raw_reference_order_or_duplicates")
    return {
        "schema": VALIDATION_SCHEMA,
        "source_profile": PROFILE,
        "source_sha256": source_sha256,
        "cohort": cohort,
        "producer_student_relation": copy.deepcopy(relation),
        "input_spec_sha256": INPUT_SPEC["sha256"],
        "projection_spec": copy.deepcopy(projection_spec),
        "target_spec": copy.deepcopy(target_spec),
        "raw_refs": copy.deepcopy(refs),
        "validation": "typed_native_original_known_prefix_reverified",
    }
