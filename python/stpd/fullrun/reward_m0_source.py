"""Bounded cached TRAIN rows joined to their original Human-time deck Reads.

Cached text is only a locator/byte-equality check. The installed owner verifier
and exact original proof/H/Read chain establish each selected row.
"""

from __future__ import annotations

import hashlib
import io
import tempfile
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, NoReturn

from sts2_platform_evidence.human_session_bundle_v3 import HumanSessionBundleV3Verifier

from spireagent.artifact_contracts import Manifest, Parent, Payload, Producer
from spireagent.json_boundary import (
    BoundaryError,
    FrozenObject,
    decode_json,
    digest,
    json_bytes,
    object_fields,
)
from spireagent.storage.store import ArtifactStore

from ..canonical import canonical_json, semantic_hash
from ..reward_m0_spec import (
    COHORT,
    DECK_SCHEMA,
    INPUT_SPECS,
    MAX_CACHED_BYTES,
    MAX_CHOICES,
    MAX_SOURCE_BYTES,
    SOURCE_SCHEMA,
    SOURCE_SPEC,
    RewardM0Config,
    checked_pair_limits,
)
from .contracts import TRANSITION_SCHEMA_V2
from .features import ModelSample
from .platform_bundle3 import _extract, _load
from .public_bc import VIEW_SCHEMA, bound_choice
from .public_inputs import COMPACT_IDENTITY
from .representation import reject_leakage

CARD_REQUIRED = frozenset({"definition_id", "type", "cost", "rarity", "is_upgraded", "is_selected"})
CARD_OPTIONAL = frozenset(
    {
        "name",
        "star_cost",
        "description",
        "existing_enchantment",
        "target_type",
        "can_play",
        "unplayable_reason",
    }
)
READ_CONTENT_SCHEMA = "sts2.player-environment/read/run_deck-1"
COMPLETE_DECK = "complete_for_player_run_deck_contents_without_semantic_order"
SURFACES = frozenset({"reward_claim", "card_reward_selection"})


def _fail(code: str) -> NoReturn:
    raise BoundaryError("reward_m0_source", code)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def selection(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, (list, tuple))
        or not 0 < len(value) <= MAX_CHOICES
        or any(not isinstance(item, str) for item in value)
        or len(set(value)) != len(value)
    ):
        _fail("explicit_unique_TRAIN_occurrences_required")
    return tuple(sorted(digest(item, "reward_m0.occurrence") for item in value))


def historical_binding(value: object) -> dict[str, Any]:
    fields = object_fields(
        value,
        {
            "dataset_id",
            "cached_view_id",
            "allocation_id",
            "training_input_id",
            "training_operation_id",
            "receipt_id",
        },
        "reward_m0.historical_binding",
    )
    for key, item in fields.items():
        digest(item, "reward_m0." + key, length=32 if key == "training_operation_id" else 64)
    return fields


def canonical_deck(content: object) -> dict[str, Any]:
    if (
        not isinstance(content, dict)
        or set(content) != {"kind", "card_count", "cards"}
        or content["kind"] != "run_deck"
        or type(content["card_count"]) is not int
        or not isinstance(content["cards"], list)
        or content["card_count"] != len(content["cards"])
        or content["card_count"] > 1000
    ):
        _fail("complete_deck_count_required")
    cards = []
    for raw in content["cards"]:
        if (
            not isinstance(raw, dict)
            or not set(raw) >= CARD_REQUIRED
            or set(raw) - CARD_REQUIRED - CARD_OPTIONAL - {"entity_id"}
        ):
            _fail("typed_public_card_required")
        card = {key: raw.get(key) for key in sorted(CARD_REQUIRED | CARD_OPTIONAL)}
        for key in ("definition_id", "type", "cost", "rarity"):
            if not isinstance(card[key], str) or not card[key]:
                _fail("typed_public_card_required")
        if any(type(card[key]) is not bool for key in ("is_upgraded", "is_selected")):
            _fail("typed_public_card_required")
        for key in ("name", "star_cost", "description", "target_type", "unplayable_reason"):
            if card[key] is not None and not isinstance(card[key], str):
                _fail("typed_public_card_required")
        if card["can_play"] is not None and type(card["can_play"]) is not bool:
            _fail("typed_public_card_required")
        enchantment = card["existing_enchantment"]
        if enchantment is not None:
            required = {"definition_id", "amount", "observation_source"}
            optional = {"name", "description"}
            if (
                not isinstance(enchantment, dict)
                or not required <= set(enchantment)
                or set(enchantment) - required - optional
                or type(enchantment["amount"]) is not int
                or any(
                    not isinstance(enchantment[key], str) or not enchantment[key]
                    for key in ("definition_id", "observation_source")
                )
                or any(
                    enchantment.get(key) is not None and not isinstance(enchantment[key], str)
                    for key in optional
                )
            ):
                _fail("typed_public_enchantment_required")
            card["existing_enchantment"] = {
                key: enchantment.get(key) for key in sorted(required | optional)
            }
        reject_leakage(card)
        cards.append(card)
    return {
        "schema": DECK_SCHEMA,
        "ordering_semantics": "unordered_multiset",
        "card_count": len(cards),
        "cards": sorted(cards, key=canonical_json),
    }


def deck_for_frame(frame: dict[str, Any], read_bytes: bytes) -> dict[str, Any]:
    snapshot = frame["snapshot"]
    evidence = [read for read in frame.get("reads", []) if read.get("kind") == "run_deck"]
    if len(evidence) != 1 or evidence[0].get("status") != "materialized":
        _fail("matching_materialized_run_deck_missing")
    read = evidence[0]
    session = snapshot["session"]
    if (
        read.get("snapshot_id") != snapshot["snapshot_id"]
        or frame["snapshot_id"] != snapshot["snapshot_id"]
        or read.get("runtime_instance_id") != session["runtime_instance_id"]
        or read.get("environment_fingerprint") != session["environment_fingerprint"]
    ):
        _fail("run_deck_H_binding_mismatch")
    advertised = [
        item
        for item in snapshot.get("reads", [])
        if item.get("kind") == "run_deck" and item.get("read_id") == read.get("read_id")
    ]
    completeness = read.get("completeness", {})
    if (
        len(advertised) != 1
        or read.get("content_schema") != READ_CONTENT_SCHEMA
        or advertised[0].get("content_schema") != READ_CONTENT_SCHEMA
        or advertised[0].get("snapshot_bound") is not True
        or advertised[0].get("ordering_semantics") != "unordered_multiset"
        or not isinstance(completeness, dict)
        or completeness.get("missing") != []
        or completeness.get("status") != "complete"
        or completeness.get("visible_information") != COMPLETE_DECK
        or completeness.get("hidden_by_policy") != []
        or read.get("payload_sha256") != _sha(read_bytes)
    ):
        _fail("complete_snapshot_bound_deck_required")
    digest(read["payload_sha256"], "reward_m0.read_payload_sha256")
    if read.get("payload_ref") != (
        "blobs/sha256/" + read["payload_sha256"][:2] + "/" + read["payload_sha256"] + ".json"
    ):
        _fail("read_payload_reference_mismatch")
    return canonical_deck(decode_json(read_bytes))


def append_deck(state_text: str, deck: dict[str, Any]) -> str:
    return (
        state_text
        + "\n[STPD_RUN_DECK version="
        + DECK_SCHEMA
        + "]\n"
        + canonical_json(deck)
        + ("\n[/STPD_RUN_DECK]")
    )


def _bytes(store: ArtifactStore, payload: Any, maximum: int) -> bytes:
    if payload.size > maximum:
        _fail("payload_capacity")
    return b"".join(store.read_payload(payload))


def _samples(store: ArtifactStore, view: Manifest, transitions: set[str]) -> dict[str, ModelSample]:
    payload = view.payload("samples")
    if payload.size > MAX_CACHED_BYTES:
        _fail("cached_samples_capacity")
    pending = b""
    found = {}
    # Complete streaming retains the existing Store's whole-payload integrity;
    # only explicitly selected transition rows are decoded/exposed to this view.
    needles = {item.encode("ascii") for item in transitions}
    tag = b'"transition_id":"'
    for chunk in store.read_payload(payload):
        pending += chunk
        lines = pending.split(b"\n")
        pending = lines.pop()
        for line in lines:
            position = line.rfind(tag)
            if position < 0 or line[position + len(tag) : position + len(tag) + 64] not in needles:
                continue
            value = decode_json(line)
            if value["transition_id"] not in transitions:
                continue
            if value["transition_id"] in found:
                _fail("cached_transition_not_unique")
            found[value["transition_id"]] = ModelSample(
                **{
                    **value,
                    "action_texts": tuple(value["action_texts"]),
                    "action_keys": tuple(value["action_keys"]),
                }
            )
    if pending or set(found) != transitions:
        _fail("cached_selected_samples_missing")
    return found


def _archives(store: ArtifactStore, view: Manifest) -> dict[str, tuple[str, Any]]:
    pending = [view.artifact_id]
    seen: set[str] = set()
    archives: dict[str, tuple[str, Any]] = {}
    while pending:
        identity = pending.pop()
        if identity in seen:
            continue
        if len(seen) >= 512:
            _fail("lineage_capacity")
        seen.add(identity)
        manifest = store.get_manifest(identity)
        if manifest.kind == "evidence":
            payload = manifest.payload("archive")
            if payload.sha256 in archives and archives[payload.sha256][0] != identity:
                _fail("archive_alias_requires_explicit_original_source")
            archives[payload.sha256] = (identity, payload)
        pending.extend(parent.artifact_id for parent in manifest.parents)
    return archives


def project_reward_m0_pair(
    store: ArtifactStore,
    view_id: str,
    occurrence_ids: object,
    paired_config: RewardM0Config,
    paired_limits: dict[str, int],
    historical: object,
) -> dict[str, Any]:
    ids = selection(occurrence_ids)
    paired_limits = checked_pair_limits(paired_limits)
    historical = historical_binding(historical)
    view = store.get_manifest(digest(view_id, "reward_m0.cached_view"))
    if (
        view.kind != "model_view"
        or view.parameters.value().get("schema") != VIEW_SCHEMA
        or view.parameters.value().get("serializer") != COMPACT_IDENTITY
        or sorted(parent.role for parent in view.parents) != ["allocation", "dataset"]
    ):
        _fail("exact_legacycompact_cached_view_required")
    allocation = store.get_manifest(view.parent("allocation"))
    if allocation.kind != "protocol" or allocation.parent("dataset") != view.parent("dataset"):
        _fail("cached_view_allocation_identity")
    dataset = store.get_manifest(view.parent("dataset"))
    original_input = store.get_manifest(historical["training_input_id"])
    original_binding = {
        "schema": "stpd/light-action-m0-training-binding-v1",
        "dataset_ids": [dataset.artifact_id],
        "model_view_id": view.artifact_id,
        "allocation_id": allocation.artifact_id,
        "training_operation_id": historical["training_operation_id"],
    }
    if (
        dataset.parameters.value().get("purpose") != "training"
        or dataset.parameters.value().get("sealed_test") is True
        or historical["cached_view_id"] != view.artifact_id
        or historical["allocation_id"] != allocation.artifact_id
        or historical["dataset_id"] != dataset.artifact_id
        or original_input.kind != "training_input"
        or original_input.parent("model_view") != view.artifact_id
        or original_input.parameters.value().get("training_binding") != original_binding
        or original_input.parameters.value().get("source_renderer") != COMPACT_IDENTITY
        or original_input.parameters.value().get("schema")
        != ("stpd/stage1a-light-action-m0-public-input-v1")
    ):
        _fail("historical_training_lineage_mismatch")
    members = decode_json(_bytes(store, allocation.payload("members"), MAX_SOURCE_BYTES))
    membership = {row["occurrence"]: row for row in members["members"]}
    if any(key not in membership or membership[key]["split"] != "train" for key in ids):
        _fail("original_TRAIN_membership_required")
    report = decode_json(_bytes(store, view.payload("dispositions"), MAX_SOURCE_BYTES))
    located = [row for row in report["rows"] if row.get("occurrence") in ids]
    if len(located) != len(ids) or len({row["occurrence"] for row in located}) != len(ids):
        _fail("cached_occurrence_not_unique")
    if any(row.get("split") != "train" or row.get("status") != "included" for row in located):
        _fail("original_included_TRAIN_required")
    if any(
        any(
            membership[row["occurrence"]][key] != row[key]
            for key in ("transition_id", "source_archive_sha256")
        )
        for row in located
    ):
        _fail("cached_membership_identity_mismatch")
    samples = _samples(store, view, {row["transition_id"] for row in located})
    archives = _archives(store, view)
    accepted, exclusions = [], []
    for source_sha in sorted({row["source_archive_sha256"] for row in located}):
        if source_sha not in archives:
            _fail("original_archive_missing")
        source_id, payload = archives[source_sha]
        with tempfile.TemporaryDirectory(prefix="reward-m0-original-") as name:
            directory = Path(name)
            _extract(_bytes(store, payload, MAX_CACHED_BYTES), directory)
            verified = HumanSessionBundleV3Verifier().verify(directory)
            if verified.status != "pass" or verified.value is None:
                _fail("typed_original_bundle_verification_failed")
            raw = directory / "raw"
            recording = _load(raw / "recording-manifest.json")
            required_proofs = {
                row["proof_ref"] for row in located if row["source_archive_sha256"] == source_sha
            }
            proofs = {}
            for line in (raw / "semantic-boundary-trace.jsonl").read_bytes().splitlines():
                event = decode_json(line)
                key = semantic_hash(event)
                if key in required_proofs:
                    proofs[key] = event
            if set(proofs) != required_proofs:
                _fail("original_proof_missing")
            canonical = {}
            required_witnesses = {proof["action"]["action_witness_id"] for proof in proofs.values()}
            for line in (raw / "canonical-transitions.jsonl").read_bytes().splitlines():
                value = decode_json(line)
                if value["action_witness_id"] in required_witnesses:
                    if value["action_witness_id"] in canonical:
                        _fail("original_transition_not_unique")
                    canonical[value["action_witness_id"]] = value
            if set(canonical) != required_witnesses:
                _fail("original_transition_missing")
            for row in located:
                if row["source_archive_sha256"] != source_sha:
                    continue
                sample = samples[row["transition_id"]]
                proof = proofs[row["proof_ref"]]
                original = canonical[proof["action"]["action_witness_id"]]
                if (
                    proof.get("kind") != "transition_proved"
                    or original["action_witness_id"] != proof["action"]["action_witness_id"]
                    or original["run_id"] != proof["run_id"]
                    or original["decision"] != proof["action"]["decision"]
                ):
                    _fail("original_proved_choice_required")
                ref = proof.get("human_observation_ref")
                if not isinstance(ref, dict) or ref["content_sha256"] != row["human_frame_sha256"]:
                    _fail("original_H_reference_mismatch")
                frame = _load(raw / ref["object_ref"])
                public, chosen = bound_choice(frame, ref, proof["action"], compact=True)
                run_id = recording["session_id"] + "/" + proof["run_id"]
                occurrence = semantic_hash(
                    [
                        recording["session_id"],
                        proof["run_id"],
                        proof["action"]["decision"]["decision_id"],
                    ]
                )
                if (
                    occurrence != row["occurrence"]
                    or sample.run_id != run_id
                    or sample.transition_id
                    != semantic_hash(
                        {
                            "schema": TRANSITION_SCHEMA_V2,
                            "bundle": source_sha,
                            "record": original["transition_id"],
                        }
                    )
                    or membership[occurrence]["run_id"] != run_id
                    or sample.split != "train"
                    or sample.surface not in SURFACES
                    or sample.state_text != public.state_text
                    or sample.action_texts != public.action_texts
                    or sample.action_keys != tuple(action.key for action in public.actions)
                    or sample.chosen_index != chosen
                    or chosen != row["chosen_index"]
                ):
                    _fail("cached_selected_original_choice_mismatch")
                origin = {
                    "occurrence_id": occurrence,
                    "transition_id": sample.transition_id,
                    "canonical_record_id": original["transition_id"],
                    "run_id": run_id,
                    "source_id": source_id,
                    "source_archive_sha256": source_sha,
                    "proof_ref": row["proof_ref"],
                    "H_ref": ref,
                    "chosen_index": chosen,
                }
                reads = [read for read in frame.get("reads", []) if read.get("kind") == "run_deck"]
                try:
                    if len(reads) != 1 or reads[0].get("status") != "materialized":
                        _fail("matching_materialized_run_deck_missing")
                    read = reads[0]
                    deck = deck_for_frame(frame, (raw / read["payload_ref"]).read_bytes())
                except BoundaryError as error:
                    exclusions.append({**origin, "reason": error.code, "excluded_arms": ["A", "B"]})
                    continue
                accepted.append(
                    {
                        **origin,
                        "A": sample.to_dict(),
                        "B": replace(
                            sample, state_text=append_deck(sample.state_text, deck)
                        ).to_dict(),
                        "deck": deck,
                        "Read_evidence": read,
                    }
                )
    accepted.sort(key=lambda row: row["occurrence_id"])
    return {
        "schema": SOURCE_SCHEMA,
        "source_spec": SOURCE_SPEC,
        "cohort": COHORT,
        "input_specs": INPUT_SPECS,
        "cached_view_id": view.artifact_id,
        "dataset_id": view.parent("dataset"),
        "allocation_id": allocation.artifact_id,
        "historical_binding": historical,
        "split_scope": "original_prior_TRAIN_assignment_no_new_global_allocation",
        "historical_external_exposure": "unknown",
        "selected_occurrence_ids": list(ids),
        "paired_config": paired_config.paired_schedule(),
        "paired_limits": paired_limits,
        "rows": accepted,
        "exclusions": exclusions,
        "counts": {
            "requested_unique_TRAIN_N": len(ids),
            "paired_unique_TRAIN_N": len(accepted),
            "excluded_from_both": len(exclusions),
        },
        "target_distribution": dict(
            Counter(row["A"]["action_texts"][row["chosen_index"]] for row in accepted)
        ),
        "cached_samples_bytes_verified": view.payload("samples").size,
        "full_corpus_reprojection": False,
        "heldout_or_Gold": False,
    }


@dataclass(frozen=True)
class VerifiedRewardM0Source:
    manifest: Manifest
    value: dict[str, Any]

    @property
    def runs(self) -> frozenset[str]:
        return frozenset(row["run_id"] for row in self.value["rows"])

    @property
    def source_ids(self) -> frozenset[str]:
        return frozenset(row["source_id"] for row in self.value["rows"])


def reward_m0_source_manifest(projected: dict[str, Any], producer: Producer) -> Manifest:
    raw = json_bytes(projected)
    if len(raw) > MAX_SOURCE_BYTES:
        _fail("paired_source_capacity")
    payload = Payload("source", _sha(raw), len(raw), "application/json")
    return Manifest(
        "dataset",
        producer,
        (
            Parent("model_view", projected["cached_view_id"]),
            Parent("dataset", projected["dataset_id"]),
        ),
        (payload,),
        FrozenObject.of(
            {
                "schema": SOURCE_SCHEMA,
                "source_spec": SOURCE_SPEC,
                "cohort": COHORT,
                "split": "train",
                "source_sha256": payload.sha256,
                "paired_unique_TRAIN_N": len(projected["rows"]),
            }
        ),
    )


def publish_reward_m0_source(
    store: ArtifactStore, projected: dict[str, Any], producer: Producer
) -> Manifest:
    if not projected["rows"]:
        _fail("no_paired_eligible_TRAIN_N")
    manifest = reward_m0_source_manifest(projected, producer)
    payload = store.put_payload("source", io.BytesIO(json_bytes(projected)), "application/json")
    if payload != manifest.payload("source"):
        _fail("paired_source_publication_mismatch")
    store.publish(manifest)
    return manifest


def verify_reward_m0_source(store: ArtifactStore, identity: str) -> VerifiedRewardM0Source:
    manifest = store.get_manifest(digest(identity, "reward_m0.source_id"))
    if manifest.kind != "dataset" or manifest.parameters.value().get("schema") != SOURCE_SCHEMA:
        _fail("typed_reward_m0_source_required")
    raw = _bytes(store, manifest.payload("source"), MAX_SOURCE_BYTES)
    source = decode_json(raw)
    config = RewardM0Config(**{**source["paired_config"], "arm": "A"})
    expected = project_reward_m0_pair(
        store,
        source["cached_view_id"],
        source["selected_occurrence_ids"],
        config,
        source["paired_limits"],
        source["historical_binding"],
    )
    if (
        raw != json_bytes(expected)
        or not expected["rows"]
        or manifest.parent("model_view") != expected["cached_view_id"]
        or manifest.parent("dataset") != expected["dataset_id"]
        or manifest.parameters.value()
        != {
            "schema": SOURCE_SCHEMA,
            "source_spec": SOURCE_SPEC,
            "cohort": COHORT,
            "split": "train",
            "source_sha256": _sha(raw),
            "paired_unique_TRAIN_N": len(expected["rows"]),
        }
    ):
        _fail("paired_original_replay_mismatch")
    return VerifiedRewardM0Source(manifest, expected)
