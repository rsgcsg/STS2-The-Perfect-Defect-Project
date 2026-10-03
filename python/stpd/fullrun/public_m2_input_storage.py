"""Strict, lossless wire for caller-admitted public M2 observation sequences.

This verifies typed bytes and named lineage. It does not re-create the verified
public view, authorize data use, or prove Human origin from token IDs.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, fields

from tokenizers import Tokenizer

from spireagent.json_boundary import (
    BoundaryError,
    array,
    decode_json,
    digest,
    object_fields,
)

from ..canonical import semantic_hash
from ..light_action_codec import SPEC_SHA256, decode_action
from ..models.light_action_m2_training_data import LightActionM2TrainingStep
from .public_inputs import COMPACT_IDENTITY
from .public_m2_sequences import (
    EDGE_PROFILE,
    FEEDBACK_PROFILE,
    MAX_ROWS,
    PRIOR_ACTION_PROFILE,
    SCHEMA,
    SEQUENCE_PROFILE,
    PublicM2Chain,
    PublicM2EvidenceRow,
    PublicM2Input,
)

MAX_INPUT_BYTES = 256 * 1024 * 1024
MAX_TOKENIZER_BYTES = 8 * 1024 * 1024
_STAGE = "public_m2_input_storage"
_FIELDS = {
    "schema", "sequence_profile", "prior_action_profile", "feedback_profile",
    "edge_profile", "renderer", "source_binding_digest", "codec_fit_chain_ids",
    "codec_fit_transition_ids", "state_tokenizer_sha256", "action_codec_sha256",
    "max_state_tokens", "max_action_bytes", "reset_policy", "end_policy",
    "complete_game_claim", "physical_game_independence", "chains", "identity",
}


def public_m2_source_binding_digest(source_view_id: str, allocation_id: str) -> str:
    """Pin the exact public view and its frozen allocation, not use permission."""
    digest(source_view_id, _STAGE + ".source_view")
    digest(allocation_id, _STAGE + ".allocation")
    return str(semantic_hash({
        "schema": "stpd/public-m2-source-binding-v1",
        "source_view_id": source_view_id,
        "allocation_id": allocation_id,
    }))


def _strings(value: object, stage: str) -> tuple[str, ...]:
    items = array(value, stage)
    if any(type(item) is not str or not item for item in items):
        raise BoundaryError(_STAGE, "invalid_string_array")
    return tuple(items)


def _ints(value: object, stage: str) -> tuple[int, ...]:
    items = array(value, stage)
    if any(type(item) is not int for item in items):
        raise BoundaryError(_STAGE, "invalid_token_array")
    return tuple(items)


def _evidence(value: object) -> PublicM2EvidenceRow:
    row = object_fields(value, {field.name for field in fields(PublicM2EvidenceRow)}, _STAGE)
    result = PublicM2EvidenceRow(**row)
    result.validate()
    return result


def _step(value: object) -> LightActionM2TrainingStep:
    row = object_fields(value, {field.name for field in fields(LightActionM2TrainingStep)},
                        _STAGE)
    actions = tuple(_ints(action, _STAGE + ".action")
                    for action in array(row["byte_actions"], _STAGE + ".actions"))
    return LightActionM2TrainingStep(
        row["position"], _ints(row["page"], _STAGE + ".page"),
        _strings(row["action_ids"], _STAGE + ".action_ids"), actions,
        row["target_action_id"], row["previous_actual_action"], row["reset_before"],
    )


def read_public_m2_input(raw: bytes, state_tokenizer: bytes) -> PublicM2Input:
    """Decode exact canonical bytes into complete typed tuples; reject partial rows."""
    if (type(raw) is not bytes or not 0 < len(raw) <= MAX_INPUT_BYTES
            or type(state_tokenizer) is not bytes
            or not 0 < len(state_tokenizer) <= MAX_TOKENIZER_BYTES):
        raise BoundaryError(_STAGE, "payload_size_limit")
    data = object_fields(decode_json(raw), _FIELDS, _STAGE)
    if (
        data["schema"] != SCHEMA
        or data["sequence_profile"] != SEQUENCE_PROFILE
        or data["prior_action_profile"] != PRIOR_ACTION_PROFILE
        or data["feedback_profile"] != FEEDBACK_PROFILE
        or data["edge_profile"] != EDGE_PROFILE
        or data["renderer"] != COMPACT_IDENTITY
        or data["action_codec_sha256"] != SPEC_SHA256
        or data["reset_policy"] != "observed-segment-start-only"
        or data["end_policy"] != "last-selected-transition-proved-successor"
        or data["complete_game_claim"] is not False
        or data["physical_game_independence"] != "unresolved"
        or hashlib.sha256(state_tokenizer).hexdigest() != data["state_tokenizer_sha256"]
    ):
        raise BoundaryError(_STAGE, "profile_or_tokenizer_mismatch")
    digest(data["source_binding_digest"], _STAGE + ".source_binding")
    digest(data["identity"], _STAGE + ".identity")
    if (type(data["max_state_tokens"]) is not int
            or not 1 <= data["max_state_tokens"] <= 1_000_000
            or type(data["max_action_bytes"]) is not int
            or not 1 <= data["max_action_bytes"] <= 8192):
        raise BoundaryError(_STAGE, "invalid_limits")
    try:
        tokenizer = Tokenizer.from_str(state_tokenizer.decode("utf-8"))
        token_ids = set(tokenizer.get_vocab().values())
    except Exception as error:
        raise BoundaryError(_STAGE, "invalid_state_tokenizer") from error
    if tokenizer.truncation is not None or tokenizer.padding is not None or not token_ids:
        raise BoundaryError(_STAGE, "invalid_state_tokenizer")

    chains: list[PublicM2Chain] = []
    seen_chains: set[str] = set()
    seen_transitions: set[str] = set()
    seen_occurrences: set[tuple[str, str, int]] = set()
    run_splits: dict[str, str] = {}
    groups: dict[tuple[str, str, str, str], list[PublicM2EvidenceRow]] = {}
    total_rows = 0
    for item in array(data["chains"], _STAGE + ".chains"):
        chain = object_fields(item, {"chain_id", "split", "evidence", "steps"}, _STAGE)
        if type(chain["split"]) is not str or chain["split"] not in {"train", "dev"}:
            raise BoundaryError(_STAGE, "invalid_split")
        evidence = tuple(_evidence(row) for row in array(chain["evidence"], _STAGE))
        steps = tuple(_step(row) for row in array(chain["steps"], _STAGE))
        if not evidence or len(evidence) != len(steps):
            raise BoundaryError(_STAGE, "chain_length_mismatch")
        expected_id = semantic_hash({"profile": EDGE_PROFILE, "split": chain["split"],
                                     "evidence": [asdict(row) for row in evidence]})
        if (chain["chain_id"] != expected_id or expected_id in seen_chains):
            raise BoundaryError(_STAGE, "chain_identity_mismatch")
        seen_chains.add(expected_id)
        total_rows += len(steps)
        if total_rows > MAX_ROWS:
            raise BoundaryError(_STAGE, "row_limit")
        first = evidence[0]
        group = (first.source_archive_sha256, first.session_id,
                 first.native_run_id, first.recording_segment_id)
        if run_splits.setdefault(first.run_id, chain["split"]) != chain["split"]:
            raise BoundaryError(_STAGE, "run_crosses_split")
        for position, (row, step) in enumerate(zip(evidence, steps, strict=True)):
            occurrence = (row.session_id, row.native_run_id, row.action_sequence)
            if (row.transition_id in seen_transitions or occurrence in seen_occurrences
                    or (row.source_archive_sha256, row.session_id,
                        row.native_run_id, row.recording_segment_id) != group
                    or step.position != position or type(step.position) is not int
                    or step.reset_before is not (position == 0)
                    or step.previous_actual_action is not None
                    or not step.page or len(step.page) > data["max_state_tokens"]
                    or any(token not in token_ids for token in step.page)
                    or not step.action_ids or len(set(step.action_ids)) != len(step.action_ids)
                    or len(step.action_ids) != len(step.byte_actions)
                    or type(step.target_action_id) is not str
                    or step.action_ids.count(step.target_action_id) != 1):
                raise BoundaryError(_STAGE, "invalid_observation_row")
            for action in step.byte_actions:
                if len(action) - 2 > data["max_action_bytes"]:
                    raise BoundaryError(_STAGE, "action_limit")
                decode_action(action)
            if position and (evidence[position - 1].action_sequence + 1 != row.action_sequence
                             or evidence[position - 1].successor_frame_sha256
                             != row.pre_frame_sha256):
                raise BoundaryError(_STAGE, "unproved_chain_edge")
            seen_transitions.add(row.transition_id)
            seen_occurrences.add(occurrence)
            groups.setdefault(group, []).append(row)
        chains.append(PublicM2Chain(expected_id, chain["split"], evidence, steps))
    if not chains or {chain.split for chain in chains} != {"train", "dev"}:
        raise BoundaryError(_STAGE, "nonempty_train_dev_required")
    # A row boundary may not be introduced solely to reset memory or budget.
    starts = {chain.evidence[0].transition_id for chain in chains}
    for rows in groups.values():
        ordered = sorted(rows, key=lambda row: row.action_sequence)
        for left, right in zip(ordered, ordered[1:], strict=False):
            if (left.action_sequence + 1 == right.action_sequence
                    and left.successor_frame_sha256 == right.pre_frame_sha256
                    and right.transition_id in starts):
                raise BoundaryError(_STAGE, "artificial_chain_boundary")
    fit_chains = _strings(data["codec_fit_chain_ids"], _STAGE + ".fit_chains")
    fit_transitions = _strings(data["codec_fit_transition_ids"], _STAGE + ".fit_rows")
    by_id = {chain.chain_id: chain for chain in chains}
    if (not fit_chains or len(set(fit_chains)) != len(fit_chains)
            or any(chain_id not in by_id or by_id[chain_id].split != "train"
                   for chain_id in fit_chains)
            or fit_transitions != tuple(row.transition_id for chain_id in fit_chains
                                        for row in by_id[chain_id].evidence)):
        raise BoundaryError(_STAGE, "codec_fit_membership_mismatch")
    result = PublicM2Input(
        data["source_binding_digest"], fit_chains, fit_transitions, state_tokenizer,
        tuple(chains), data["max_state_tokens"], data["max_action_bytes"],
    )
    if result.identity != data["identity"] or result.payload_bytes() != raw:
        raise BoundaryError(_STAGE, "noncanonical_or_identity_mismatch")
    return result
