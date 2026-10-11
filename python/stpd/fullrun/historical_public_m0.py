"""Bounded original member/disposition/encoded-row relation, without source hydration.

This verifies a reviewed historical producer's ordered derivative relationship.
It does not authenticate an actor or replace owning original semantic admission.
"""

from __future__ import annotations

import hashlib
import io
import tempfile
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, NoReturn

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import (
    BoundaryError,
    FrozenObject,
    decode_json,
    digest,
    json_bytes,
    object_fields,
    text,
)
from spireagent.storage.store import ArtifactStore

from ..canonical import semantic_hash
from ..historical_m0_spec import (
    DIAGNOSTIC_SCOPE,
    INITIALIZATION_SCHEMA,
    INPUT_FORMAT,
    INPUT_SCHEMA,
    MAX_MEMBERS_BYTES,
    MAX_ORIGINAL_ROWS,
    MAX_RELATION_BYTES,
    MAX_ROW_BYTES,
    MAX_SELECTED_ARCHIVE_BYTES,
    MAX_SELECTED_BYTES,
    MAX_SELECTED_ROWS,
    MEMBER_FIELDS,
    ORDER_CONTRACT,
    RELATION_SCHEMA,
    HistoricalM0Config,
)
from ..light_action_codec import SPEC, SPEC_BYTES, SPEC_SHA256, decode_action, encode_action
from ..stage1a_recipes import LIGHT_ACTION_M0_GRAPH
from .features import ModelSample
from .light_action_row_storage import decoded_rows, storage_identity
from .public_inputs import COMPACT_IDENTITY

ORIGINAL_INPUT_SCHEMA = "stpd/stage1a-light-action-m0-public-input-v1"
ORIGINAL_INPUT_FORMAT = "stpd-token-light-action-m0-public-v1"
ORIGINAL_VIEW_SCHEMA = "stpd/public-observation-bc-view-v2"

if TYPE_CHECKING:
    from .light_action_inputs import LoadedLightActionInputs


def _fail(code: str) -> NoReturn:
    raise BoundaryError("historical_public_m0", code)


def originals(value: object) -> dict[str, Any]:
    obj = object_fields(
        value,
        {
            "dataset_id",
            "allocation_id",
            "model_view_id",
            "training_input_id",
            "admission_operation_id",
            "admission_receipt_id",
        },
        "historical_m0.originals",
    )
    for key, identity in obj.items():
        digest(
            identity, "historical_m0." + key, length=32 if key == "admission_operation_id" else 64
        )
    return obj


def member(value: object) -> dict[str, Any]:
    obj = object_fields(value, set(MEMBER_FIELDS), "historical_m0.member")
    for key in ("occurrence", "source_archive_sha256"):
        digest(obj[key], "historical_m0." + key)
    text(obj["transition_id"], "historical_m0.transition_id", maximum=128)
    text(obj["run_id"], "historical_m0.run_id", maximum=512)
    if obj["split"] not in {"train", "dev"}:
        _fail("original_train_dev_only")
    return obj


def selected_members(value: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_SELECTED_ROWS:
        _fail("bounded_explicit_members_required")
    result = tuple(member(row) for row in value)
    if len({row["occurrence"] for row in result}) != len(result):
        _fail("duplicate_selected_occurrence")
    if result != tuple(sorted(result, key=lambda row: row["occurrence"])):
        _fail("canonical_member_order_required")
    splits = {row["split"] for row in result}
    train_runs = {row["run_id"] for row in result if row["split"] == "train"}
    dev_runs = {row["run_id"] for row in result if row["split"] == "dev"}
    if splits != {"train", "dev"} or train_runs & dev_runs:
        _fail("original_group_partition_required")
    return result


def _read(store: ArtifactStore, manifest: Manifest, role: str, maximum: int) -> bytes:
    payload = manifest.payload(role)
    if not 0 < payload.size <= maximum:
        _fail("bounded_parent_payload_required")
    return b"".join(store.read_payload(payload))


def _lines(chunks: Iterable[bytes]) -> Iterator[bytes]:
    pending = bytearray()
    for chunk in chunks:
        offset = 0
        while offset < len(chunk):
            end = chunk.find(b"\n", offset)
            cut = len(chunk) if end < 0 else end + 1
            if len(pending) + cut - offset > MAX_ROW_BYTES:
                _fail("original_row_bytes_limit")
            pending.extend(chunk[offset:cut])
            offset = cut
            if end >= 0:
                yield bytes(pending)
                pending.clear()
    if pending:
        _fail("original_rows_terminal_newline_required")


def _parent_manifests(store: ArtifactStore, binding: dict[str, Any]) -> tuple[Manifest, ...]:
    dataset, allocation, view, inputs = (
        store.get_manifest(binding[key])
        for key in ("dataset_id", "allocation_id", "model_view_id", "training_input_id")
    )
    if (
        dataset.kind != "dataset"
        or dataset.parameters.value().get("purpose") != "training"
        or allocation.kind != "protocol"
        or allocation.parameters.value().get("schema") != "stpd/decision-allocation-v1"
        or [p.role for p in allocation.parents] != ["dataset"]
        or allocation.parent("dataset") != dataset.artifact_id
        or [p.role for p in allocation.payloads] != ["members"]
        or view.kind != "model_view"
        or view.parameters.value().get("schema") != ORIGINAL_VIEW_SCHEMA
        or view.parameters.value().get("serializer") != COMPACT_IDENTITY
        or view.parameters.value().get("scope") != "platform_verified"
        or view.parameters.value().get("label_boundary") != "exact_human_observation_bound_action"
        or view.parameters.value().get("successor_supervision") is not False
        or sorted(p.role for p in view.parents) != ["allocation", "dataset"]
        or view.parent("allocation") != allocation.artifact_id
        or view.parent("dataset") != dataset.artifact_id
        or sorted(p.role for p in view.payloads) != ["dispositions", "samples"]
        or inputs.kind != "training_input"
        or [p.role for p in inputs.parents] != ["model_view"]
        or inputs.parent("model_view") != view.artifact_id
        or sorted(p.role for p in inputs.payloads) != ["action_codec", "rows", "state_tokenizer"]
        or view.producer.source_revision != ORDER_CONTRACT["view_producer_revision"]
        or inputs.producer.source_revision != ORDER_CONTRACT["input_producer_revision"]
    ):
        _fail("original_parent_or_order_contract_mismatch")
    info = inputs.parameters.value()
    state, action = info.get("state_codec"), info.get("action_codec")
    if (
        info.get("schema") != ORIGINAL_INPUT_SCHEMA
        or info.get("format") != ORIGINAL_INPUT_FORMAT
        or info.get("graph") != LIGHT_ACTION_M0_GRAPH
        or info.get("source_schema") != ORIGINAL_VIEW_SCHEMA
        or info.get("source_renderer") != COMPACT_IDENTITY
        or not isinstance(state, dict)
        or state.get("schema") != "stpd/state-byte-bpe-v1"
        or state.get("family") != "train-only-byte-bpe"
        or state.get("sha256") != inputs.payload("state_tokenizer").sha256
        or type(state.get("vocab_size")) is not int
        or not 256 <= state["vocab_size"] <= 8192
        or state.get("add_special_tokens") is not False
        or action != {**SPEC, "sha256": SPEC_SHA256, "source_renderer": COMPACT_IDENTITY}
        or inputs.payload("action_codec").sha256 != SPEC_SHA256
    ):
        _fail("original_codec_or_input_contract_mismatch")
    for field in ("max_state_tokens", "max_action_bytes"):
        if type(info.get(field)) is not int or not 1 <= info[field] <= 8192:
            _fail("historical_input_limit_unsupported")
    if state.get("max_tokens") != info["max_state_tokens"]:
        _fail("original_codec_or_input_contract_mismatch")
    if info.get("training_binding") != {
        "schema": "stpd/light-action-m0-training-binding-v1",
        "dataset_ids": [dataset.artifact_id],
        "training_operation_id": binding["admission_operation_id"],
        "allocation_id": allocation.artifact_id,
        "model_view_id": view.artifact_id,
    }:
        _fail("original_training_binding_mismatch")
    return dataset, allocation, view, inputs


@dataclass(frozen=True)
class VerifiedHistoricalRelation:
    value: dict[str, Any]
    original_input: Manifest
    samples: tuple[ModelSample, ...]
    encoded_rows: tuple[dict[str, Any], ...]
    selected_bytes: bytes
    tokenizer_bytes: bytes

    @property
    def selected_runs(self) -> set[str]:
        return {row["run_id"] for row in self.value["members"]}


def compile_relation(
    store: ArtifactStore, binding_value: object, selection_value: object
) -> VerifiedHistoricalRelation:
    """Pure relationship validation. Reads only original manifests/codecs/sidecar/rows.

    The producer contract supplies order, not an independent Human/source proof.
    Application admission consumes its separate owner-held semantic receipt.
    """
    binding, selection = originals(binding_value), selected_members(selection_value)
    dataset, allocation, view, inputs = _parent_manifests(store, binding)
    allocation_value = decode_json(_read(store, allocation, "members", MAX_MEMBERS_BYTES))
    if (
        not isinstance(allocation_value, dict)
        or allocation_value.get("schema") != "stpd/decision-allocation-v1"
    ):
        _fail("original_allocation_contract_mismatch")
    rows = allocation_value.get("members")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_ORIGINAL_ROWS:
        _fail("original_member_count_limit")
    members = {row["occurrence"]: row for row in (member(raw) for raw in rows)}
    if (
        len(members) != len(rows)
        or allocation_value.get("counts") != dict(Counter(row["split"] for row in rows))
        or allocation.parameters.value().get("counts") != allocation_value["counts"]
    ):
        _fail("original_member_identity_or_counts_mismatch")
    selected = {row["occurrence"]: row for row in selection}
    if any(members.get(key) != row for key, row in selected.items()):
        _fail("selected_original_tuple_mismatch")
    sidecar = decode_json(_read(store, view, "dispositions", MAX_MEMBERS_BYTES))
    if (
        not isinstance(sidecar, dict)
        or sidecar.get("schema") != ORIGINAL_VIEW_SCHEMA
        or sidecar.get("allocation_id") != allocation.artifact_id
        or sidecar.get("serializer") != COMPACT_IDENTITY
        or sidecar.get("label_boundary") != "exact_human_observation_bound_action"
        or sidecar.get("successor_supervision") is not False
        or not isinstance(sidecar.get("rows"), list)
        or len(sidecar["rows"]) != len(members)
    ):
        _fail("original_dispositions_contract_mismatch")
    seen: set[str] = set()
    included = []
    for raw in sidecar["rows"]:
        if (
            not isinstance(raw, dict)
            or raw.get("occurrence") not in members
            or raw["occurrence"] in seen
        ):
            _fail("disposition_original_occurrence_mismatch")
        original = members[raw["occurrence"]]
        if any(
            raw.get(key) != original[key]
            for key in (
                "transition_id",
                "source_archive_sha256",
                "split",
            )
        ):
            _fail("disposition_original_tuple_mismatch")
        seen.add(raw["occurrence"])
        digest(raw.get("proof_ref"), "historical_m0.proof_ref")
        text(raw.get("surface"), "historical_m0.surface", maximum=128)
        if raw.get("status") == "included":
            digest(raw.get("human_frame_sha256"), "historical_m0.human_frame_sha256")
            if (
                type(raw.get("candidates")) is not int
                or not 1 <= raw["candidates"] <= 65536
                or type(raw.get("chosen_index")) is not int
                or not 0 <= raw["chosen_index"] < raw["candidates"]
            ):
                _fail("disposition_label_or_catalog_mismatch")
            included.append(raw)
        elif raw.get("status") == "excluded":
            text(raw.get("reason"), "historical_m0.exclusion", maximum=256)
            if raw["occurrence"] in selected:
                _fail("selected_occurrence_excluded")
        else:
            _fail("unknown_disposition")
    counts = dict(Counter(row["status"] for row in sidecar["rows"]))
    if (
        sidecar.get("counts") != counts
        or view.parameters.value().get("counts") != counts
        or view.parameters.value().get("samples") != len(included)
        or inputs.parameters.value().get("samples") != len(included)
    ):
        _fail("original_projection_coverage_mismatch")
    tokenizer = _read(store, inputs, "state_tokenizer", 16 * 1024**2)
    if _read(store, inputs, "action_codec", len(SPEC_BYTES)) != SPEC_BYTES:
        _fail("original_action_codec_bytes_mismatch")
    info = inputs.parameters.value()
    storage = info.get("rows_storage")
    if storage is not None:
        storage_identity(storage)
    payload = inputs.payload("rows")
    decoded = decoded_rows(
        store.read_payload(payload),
        stored_size=payload.size,
        media_type=payload.media_type,
        identity=storage,
    )
    whole, total, number = hashlib.sha256(), 0, 0
    selected_data = bytearray()
    mapping, samples, encoded = [], [], []
    for line in _lines(decoded):
        whole.update(line)
        total += len(line)
        if number >= len(included):
            _fail("original_encoded_extra_row")
        row = object_fields(
            decode_json(line), {"state", "actions", "action_ids"}, "historical_m0.row"
        )
        disposition = included[number]
        state, actions, keys = row["state"], row["actions"], row["action_ids"]
        if (
            not isinstance(state, list)
            or not 1 <= len(state) <= info["max_state_tokens"]
            or any(
                type(value) is not int or not 0 <= value < info["state_codec"]["vocab_size"]
                for value in state
            )
            or not isinstance(actions, list)
            or not isinstance(keys, list)
            or len(actions) != len(keys)
            or len(keys) != disposition["candidates"]
            or any(not isinstance(key, str) or not key or len(key) > 1024 for key in keys)
            or len(set(keys)) != len(keys)
            or line != json_bytes(row)
        ):
            _fail("original_encoded_catalog_or_tokens_mismatch")
        texts = []
        for action in actions:
            if not isinstance(action, list) or not 3 <= len(action) <= info["max_action_bytes"] + 2:
                _fail("original_action_bytes_limit")
            texts.append(decode_action(action))
        key = disposition["occurrence"]
        if key in selected:
            if len(selected_data) + len(line) > MAX_SELECTED_BYTES:
                _fail("selected_logical_bytes_limit")
            selected_data.extend(line)
            original = selected[key]
            mapping.append(
                {
                    **original,
                    "ordinal": number,
                    "encoded_line_sha256": hashlib.sha256(line).hexdigest(),
                    "action_ids_sha256": semantic_hash(keys),
                    "chosen_index": disposition["chosen_index"],
                    "candidate_count": disposition["candidates"],
                }
            )
            samples.append(
                ModelSample(
                    original["transition_id"],
                    original["run_id"],
                    original["split"],
                    disposition["surface"],
                    "historical_unspecified",
                    "",
                    tuple(texts),
                    tuple(keys),
                    disposition["chosen_index"],
                )
            )
            encoded.append(row)
        number += 1
    if number != len(included) or len(mapping) != len(selection):
        _fail("original_encoded_member_coverage_mismatch")
    value = {
        "schema": RELATION_SCHEMA,
        "originals": binding,
        "order_contract": ORDER_CONTRACT,
        "members": list(selection),
        "mapping": mapping,
        "mapping_sha256": semantic_hash(mapping),
        "parent_payloads": {
            "members": allocation.payload("members").to_dict(),
            "dispositions": view.payload("dispositions").to_dict(),
            "samples": view.payload("samples").to_dict(),
            "encoded_rows": payload.to_dict(),
            "state_tokenizer": inputs.payload("state_tokenizer").to_dict(),
            "action_codec": inputs.payload("action_codec").to_dict(),
        },
        "original_rows": number,
        "selected_dispositions": [row for row in included if row["occurrence"] in selected],
        "original_input_metadata": info,
        "logical_bytes": total,
        "logical_sha256": whole.hexdigest(),
        "counts": dict(Counter(row["split"] for row in mapping)),
        "scope": DIAGNOSTIC_SCOPE,
        "source_proof_scope": (
            "owner_held_original_canonical_projection_separate_from_public_input_order"
        ),
    }
    return VerifiedHistoricalRelation(
        value, inputs, tuple(samples), tuple(encoded), bytes(selected_data), tokenizer
    )


def verify_selected_human_encoding(
    store: ArtifactStore,
    relation: VerifiedHistoricalRelation,
    sources: tuple[Manifest, ...],
) -> VerifiedHistoricalRelation:
    """Explicit selected-only H/codec proof. Never runs during ordinary consumption.

    The installed original bundle verifier and public_bc.bound_choice own the
    witness/catalog relation. Frozen tokenization must reproduce each selected
    original row exactly; declaring an old producer revision supplies no proof.
    """
    from sts2_platform_evidence.human_session_bundle_v3 import HumanSessionBundleV3Verifier
    from tokenizers import Tokenizer

    from .contracts import TRANSITION_SCHEMA_V2
    from .platform_bundle3 import _extract, _lines, _load
    from .public_bc import bound_choice
    from .token_format import input_texts

    tokenizer = Tokenizer.from_str(relation.tokenizer_bytes.decode("utf-8"))
    if (
        tokenizer.truncation is not None
        or tokenizer.padding is not None
        or tokenizer.get_vocab_size()
        != relation.original_input.parameters.value()["state_codec"]["vocab_size"]
    ):
        _fail("original_state_tokenizer_mismatch")
    source_by_archive = {item.payload("archive").sha256: item for item in sources}
    if len(source_by_archive) != len(sources):
        _fail("original_source_alias")
    selection = {row["occurrence"]: row for row in relation.value["mapping"]}
    dispositions = {row["occurrence"]: row for row in relation.value["selected_dispositions"]}
    required = {row["source_archive_sha256"] for row in selection.values()}
    if not required <= set(source_by_archive):
        _fail("selected_original_source_missing")
    total = sum(source_by_archive[sha].payload("archive").size for sha in required)
    if total > MAX_SELECTED_ARCHIVE_BYTES:
        _fail("selected_original_archive_bytes_limit")
    qualified: dict[str, ModelSample] = {}
    proofs = []
    for sha in sorted(required):
        source = source_by_archive[sha]
        with tempfile.TemporaryDirectory(prefix="historical-m0-selected-H-") as name:
            from pathlib import Path

            directory = Path(name)
            _extract(_read(store, source, "archive", MAX_SELECTED_ARCHIVE_BYTES), directory)
            verified = HumanSessionBundleV3Verifier().verify(directory)
            if verified.status != "pass" or verified.value is None:
                _fail("selected_original_bundle_verification_failed")
            raw = directory / "raw"
            events = {
                semantic_hash(row): row
                for row in _lines(raw / "semantic-boundary-trace.jsonl")
                if row["kind"] == "transition_proved"
            }
            session = verified.value.manifest["session_id"]
            for canonical in _lines(directory / "export/canonical-transitions.jsonl"):
                occurrence = semantic_hash(
                    [session, canonical["run_id"], canonical["decision"]["decision_id"]]
                )
                if occurrence not in selection:
                    continue
                selected = selection[occurrence]
                disposition = dispositions[occurrence]
                transition = semantic_hash(
                    {
                        "schema": TRANSITION_SCHEMA_V2,
                        "bundle": sha,
                        "record": canonical["transition_id"],
                    }
                )
                if (
                    selected["source_archive_sha256"] != sha
                    or selected["run_id"] != session + "/" + canonical["run_id"]
                    or selected["transition_id"] != transition
                    or disposition["surface"] != canonical["decision"]["surface"]
                    or occurrence in qualified
                ):
                    _fail("selected_original_canonical_tuple_mismatch")
                proof = events.get(disposition["proof_ref"])
                if (
                    proof is None
                    or proof["action"]["action_witness_id"] != canonical["action_witness_id"]
                    or not isinstance(proof.get("human_observation_ref"), dict)
                ):
                    _fail("selected_original_H_proof_missing")
                reference = proof["human_observation_ref"]
                if reference["content_sha256"] != disposition["human_frame_sha256"]:
                    _fail("selected_original_H_reference_mismatch")
                frame = _load(raw / reference["object_ref"])
                public, chosen = bound_choice(frame, reference, proof["action"], compact=True)
                numerical = {
                    "state": tokenizer.encode(
                        input_texts(public.state_text, ())[0], add_special_tokens=False
                    ).ids,
                    "actions": [
                        list(
                            encode_action(
                                text,
                                max_bytes=relation.original_input.parameters.value()[
                                    "max_action_bytes"
                                ],
                            )
                        )
                        for text in public.action_texts
                    ],
                    "action_ids": [action.key for action in public.actions],
                }
                encoded = json_bytes(numerical)
                if (
                    chosen != selected["chosen_index"]
                    or len(public.actions) != selected["candidate_count"]
                    or semantic_hash(numerical["action_ids"]) != selected["action_ids_sha256"]
                    or hashlib.sha256(encoded).hexdigest() != selected["encoded_line_sha256"]
                ):
                    _fail("selected_original_H_encoding_or_label_mismatch")
                qualified[occurrence] = ModelSample(
                    transition,
                    selected["run_id"],
                    selected["split"],
                    frame["snapshot"]["interaction"]["kind"],
                    canonical["decision"]["family"],
                    public.state_text,
                    public.action_texts,
                    tuple(numerical["action_ids"]),
                    chosen,
                )
                proofs.append(
                    {
                        "occurrence": occurrence,
                        "archive_sha256": sha,
                        "source_id": source.artifact_id,
                        "bundle_content_id": verified.value.bundle_content_id,
                        "canonical_ref": semantic_hash(canonical),
                        "proof_ref": disposition["proof_ref"],
                        "human_observation_ref": reference,
                        "encoded_line_sha256": selected["encoded_line_sha256"],
                    }
                )
    if set(qualified) != set(selection):
        _fail("selected_original_H_coverage_mismatch")
    samples = tuple(qualified[row["occurrence"]] for row in relation.value["mapping"])
    sample_bytes = b"".join(json_bytes(row.to_dict()) for row in samples)
    value = {
        **relation.value,
        "selected_H_proof": proofs,
        "selected_rows_sha256": hashlib.sha256(relation.selected_bytes).hexdigest(),
        "selected_samples_sha256": hashlib.sha256(sample_bytes).hexdigest(),
    }
    return replace(relation, value=value, samples=samples)


def read_relation(store: ArtifactStore, identity: str) -> tuple[Manifest, dict[str, Any]]:
    """Cheap immutable-parent and frozen-selector check; no corpus/archive reads."""
    source = store.get_manifest(identity)
    if (
        source.kind != "dataset"
        or source.parameters.value().get("schema") != RELATION_SCHEMA
        or sorted(p.role for p in source.payloads) != ["relation", "rows", "samples"]
    ):
        _fail("historical_relation_source_required")
    value = decode_json(_read(store, source, "relation", MAX_RELATION_BYTES))
    if not isinstance(value, dict) or value.get("schema") != RELATION_SCHEMA:
        _fail("historical_relation_source_required")
    binding = originals(value.get("originals"))
    dataset, allocation, view, inputs = _parent_manifests(store, binding)
    expected_parents = {
        "dataset": dataset.artifact_id,
        "allocation": allocation.artifact_id,
        "model_view": view.artifact_id,
        "original_input": inputs.artifact_id,
    }
    expected_payloads = {
        "members": allocation.payload("members").to_dict(),
        "dispositions": view.payload("dispositions").to_dict(),
        "samples": view.payload("samples").to_dict(),
        "encoded_rows": inputs.payload("rows").to_dict(),
        "state_tokenizer": inputs.payload("state_tokenizer").to_dict(),
        "action_codec": inputs.payload("action_codec").to_dict(),
    }
    if (
        {p.role: p.artifact_id for p in source.parents} != expected_parents
        or value.get("parent_payloads") != expected_payloads
        or value.get("original_input_metadata") != inputs.parameters.value()
        or value.get("order_contract") != ORDER_CONTRACT
        or value.get("scope") != DIAGNOSTIC_SCOPE
    ):
        _fail("historical_relation_parent_mismatch")
    selected = selected_members(value.get("members"))
    mapping = value.get("mapping")
    if (
        not isinstance(mapping, list)
        or len(mapping) != len(selected)
        or value.get("mapping_sha256") != semantic_hash(mapping)
        or type(value.get("original_rows")) is not int
        or not 1 <= value["original_rows"] <= MAX_ORIGINAL_ROWS
    ):
        _fail("historical_relation_projection_mismatch")
    indexed = {row["occurrence"]: row for row in selected}
    seen: set[str] = set()
    previous = -1
    for row in mapping:
        if not isinstance(row, dict) or set(row) != set(MEMBER_FIELDS) | {
            "ordinal",
            "encoded_line_sha256",
            "action_ids_sha256",
            "chosen_index",
            "candidate_count",
        }:
            _fail("historical_relation_projection_mismatch")
        original = member({key: row[key] for key in MEMBER_FIELDS})
        if (
            indexed.get(row["occurrence"]) != original
            or row["occurrence"] in seen
            or type(row["ordinal"]) is not int
            or not previous < row["ordinal"] < value["original_rows"]
            or type(row["candidate_count"]) is not int
            or not 1 <= row["candidate_count"] <= 65536
            or type(row["chosen_index"]) is not int
            or not 0 <= row["chosen_index"] < row["candidate_count"]
        ):
            _fail("historical_relation_projection_mismatch")
        digest(row["encoded_line_sha256"], "historical_m0.encoded_line")
        digest(row["action_ids_sha256"], "historical_m0.action_ids")
        previous = row["ordinal"]
        seen.add(row["occurrence"])
    proof_id = digest(
        source.parameters.value().get("historical_public_receipt_id"),
        "historical_m0.selected_H_receipt",
    )
    if (
        value.get("counts") != dict(Counter(row["split"] for row in selected))
        or value.get("selected_rows_sha256") != source.payload("rows").sha256
        or value.get("selected_samples_sha256") != source.payload("samples").sha256
        or source.parameters.value() != relation_parameters(value, proof_id)
    ):
        _fail("historical_relation_projection_mismatch")
    return source, value


def relation_reference(store: ArtifactStore, identity: str) -> VerifiedHistoricalRelation:
    source, value = read_relation(store, identity)
    return VerifiedHistoricalRelation(
        value, store.get_manifest(source.parent("original_input")), (), (), b"", b""
    )


def verify_relation(store: ArtifactStore, identity: str) -> VerifiedHistoricalRelation:
    """Verify only the prepared selected bytes against their frozen relation."""
    source, value = read_relation(store, identity)
    original = store.get_manifest(source.parent("original_input"))
    info = original.parameters.value()
    selected_raw = _read(store, source, "rows", MAX_SELECTED_BYTES)
    sample_raw = _read(store, source, "samples", MAX_SELECTED_BYTES)
    lines = list(_lines([selected_raw]))
    sample_lines = list(_lines([sample_raw]))
    if len(lines) != len(value["mapping"]) or len(sample_lines) != len(lines):
        _fail("frozen_selected_coverage_mismatch")
    encoded, samples = [], []
    for mapping, line, sample_line in zip(value["mapping"], lines, sample_lines, strict=True):
        row = object_fields(
            decode_json(line), {"state", "actions", "action_ids"}, "historical_m0.row"
        )
        sample = object_fields(
            decode_json(sample_line), set(ModelSample.__dataclass_fields__), "historical_m0.sample"
        )
        state, actions, keys = row["state"], row["actions"], row["action_ids"]
        if (
            line != json_bytes(row)
            or hashlib.sha256(line).hexdigest() != mapping["encoded_line_sha256"]
            or not isinstance(state, list)
            or not 1 <= len(state) <= info["max_state_tokens"]
            or any(
                type(token) is not int or not 0 <= token < info["state_codec"]["vocab_size"]
                for token in state
            )
            or not isinstance(actions, list)
            or not isinstance(keys, list)
            or len(actions) != len(keys)
            or len(keys) != mapping["candidate_count"]
            or any(not isinstance(key, str) or not key for key in keys)
            or len(set(keys)) != len(keys)
            or semantic_hash(keys) != mapping["action_ids_sha256"]
            or any(
                sample.get(key) != mapping[key]
                for key in ("run_id", "transition_id", "split", "chosen_index")
            )
            or sample["action_keys"] != keys
            or not isinstance(sample["action_texts"], list)
            or len(sample["action_texts"]) != len(keys)
        ):
            _fail("frozen_selected_row_or_sample_mismatch")
        for action, action_text in zip(actions, sample["action_texts"], strict=True):
            if (
                not isinstance(action, list)
                or len(action) > info["max_action_bytes"] + 2
                or decode_action(action) != action_text
            ):
                _fail("frozen_selected_action_encoding_mismatch")
        for name in ("state_text", "surface", "family"):
            text(sample[name], "historical_m0." + name, maximum=MAX_ROW_BYTES)
        sample["action_texts"] = tuple(sample["action_texts"])
        sample["action_keys"] = tuple(keys)
        samples.append(ModelSample(**sample))
        encoded.append(row)
    return VerifiedHistoricalRelation(
        value,
        original,
        tuple(samples),
        tuple(encoded),
        selected_raw,
        _read(store, original, "state_tokenizer", 16 * 1024**2),
    )


def relation_parameters(value: dict[str, Any], proof_id: str) -> dict[str, Any]:
    return {
        "schema": RELATION_SCHEMA,
        "purpose": "training",
        "counts": value["counts"],
        "mapping_sha256": value["mapping_sha256"],
        "scope": DIAGNOSTIC_SCOPE,
        "original_admission_receipt": value["originals"]["admission_receipt_id"],
        "historical_public_receipt_id": proof_id,
    }


def relation_manifest(
    store: ArtifactStore, relation: VerifiedHistoricalRelation, producer: Producer, proof_id: str
) -> Manifest:
    """Owned explicit preparation after selected H/codec proof and use reservation."""
    digest(proof_id, "historical_m0.selected_H_receipt")
    raw = json_bytes(relation.value)
    if len(raw) > MAX_RELATION_BYTES or "selected_H_proof" not in relation.value:
        _fail("selected_H_receipt_required")
    samples = b"".join(json_bytes(sample.to_dict()) for sample in relation.samples)
    if (
        hashlib.sha256(samples).hexdigest() != relation.value["selected_samples_sha256"]
        or hashlib.sha256(relation.selected_bytes).hexdigest()
        != relation.value["selected_rows_sha256"]
    ):
        _fail("selected_H_payload_binding_mismatch")
    payloads = (
        store.put_payload("relation", io.BytesIO(raw), "application/json"),
        store.put_payload("rows", io.BytesIO(relation.selected_bytes), "application/x-ndjson"),
        store.put_payload("samples", io.BytesIO(samples), "application/x-ndjson"),
    )
    binding = relation.value["originals"]
    result = Manifest(
        "dataset",
        producer,
        (
            Parent("dataset", binding["dataset_id"]),
            Parent("allocation", binding["allocation_id"]),
            Parent("model_view", binding["model_view_id"]),
            Parent("original_input", binding["training_input_id"]),
        ),
        payloads,
        FrozenObject.of(relation_parameters(relation.value, proof_id)),
    )
    store.publish(result)
    return result


def warm_start_metadata(
    store: Any, relation: VerifiedHistoricalRelation, config: HistoricalM0Config
) -> dict[str, Any]:
    """Bind original Model metadata/codecs/ancestry without reading model tensors.

    The owning safe loader repeats metadata checks and verifies the full tensor
    inventory/dtype/finite values before the first numerical update.
    """
    from dataclasses import asdict

    from ..models.token_core import ScratchShape
    from ..policy.token_decision import check_light_action_m0_model

    model = store.get_manifest(config.warm_start_model_id)
    _, info = check_light_action_m0_model(model)
    original = relation.original_input
    source, binding = original.parameters.value(), relation.value["originals"]
    shape = asdict(
        ScratchShape(source["state_codec"]["vocab_size"], max_tokens=config.max_state_tokens)
    )
    backbone = {"kind": "scratch", "shape": shape, "state_codec": source["state_codec"]}
    backbone["core_fingerprint"] = semantic_hash(
        {
            "core": backbone,
            "graph": LIGHT_ACTION_M0_GRAPH,
            "vocabulary_size": source["state_codec"]["vocab_size"],
            "max_state_tokens": config.max_state_tokens,
        }
    )
    if (
        info["schema"] != "stpd/stage1a-light-action-m0-public-model-v1"
        or info["recipe"] != "stage1a.dsimple.light-action.m0.s.v1"
        or info["backbone"] != backbone
        or info["config"].get("public_profile") != "public_compact"
        or model.parent("training_input") != original.artifact_id
        or model.parent("model_view") != binding["model_view_id"]
        or model.payload("state_tokenizer") != original.payload("state_tokenizer")
        or model.payload("action_codec") != original.payload("action_codec")
        or any(
            info[key] != source[key]
            for key in (
                "state_codec",
                "action_codec",
                "source_renderer",
            )
        )
        or info["config"]["max_state_tokens"] != config.max_state_tokens
        or info["config"]["max_action_bytes"] != config.max_action_bytes
        or info["training_binding"]
        != {
            "schema": "stpd/light-action-m0-training-binding-v1",
            "dataset_ids": [binding["dataset_id"]],
            "training_operation_id": binding["admission_operation_id"],
            "allocation_id": binding["allocation_id"],
            "model_view_id": binding["model_view_id"],
        }
    ):
        _fail("warm_start_original_model_or_codec_mismatch")
    return {
        "schema": INITIALIZATION_SCHEMA,
        "mode": "model_weights_new_run",
        "model_id": model.artifact_id,
        "weights_sha256": model.payload("weights").sha256,
        "original_training_input_id": original.artifact_id,
        "original_admission_receipt_id": binding["admission_receipt_id"],
        "graph": info["graph"],
        "dtype": info["dtype"],
        "state_codec": info["state_codec"],
        "action_codec": info["action_codec"],
        "source_renderer": info["source_renderer"],
        "backbone": info.get("backbone"),
        "inherited_exposure": "all_original_codec_and_model_training_ancestry",
        "optimizer": "fresh",
        "rng_cursor_plan": "new_run",
        "memory": "stateless_M0",
    }


def historical_input_parameters(
    relation: VerifiedHistoricalRelation,
    source_id: str,
    operation_id: str,
    initialization: dict[str, Any],
    public_receipt_id: str,
) -> dict[str, Any]:
    digest(source_id, "historical_m0.source_id")
    digest(operation_id, "historical_m0.operation_id", length=32)
    digest(public_receipt_id, "historical_m0.selected_H_receipt")
    original = relation.original_input.parameters.value()
    binding = relation.value["originals"]
    state_lengths = [len(row["state"]) for row in relation.encoded_rows]
    action_lengths = [len(action) - 2 for row in relation.encoded_rows for action in row["actions"]]

    def lengths(values: list[int]) -> dict[str, int]:
        return {
            "min": min(values),
            "max": max(values),
            "p50": sorted(values)[(len(values) - 1) // 2],
        }

    return {
        "schema": INPUT_SCHEMA,
        "format": INPUT_FORMAT,
        "graph": LIGHT_ACTION_M0_GRAPH,
        "source_schema": ORIGINAL_VIEW_SCHEMA,
        "source_renderer": COMPACT_IDENTITY,
        "state_codec": original["state_codec"],
        "action_codec": original["action_codec"],
        "max_state_tokens": original["max_state_tokens"],
        "max_action_bytes": original["max_action_bytes"],
        "samples": len(relation.samples),
        "counts": relation.value["counts"],
        "state_lengths": lengths(state_lengths),
        "action_lengths": lengths(action_lengths),
        "training_binding": {
            "schema": "stpd/light-action-m0-training-binding-v1",
            "dataset_ids": sorted([binding["dataset_id"], source_id]),
            "training_operation_id": operation_id,
            "allocation_id": binding["allocation_id"],
            "model_view_id": binding["model_view_id"],
        },
        "historical_relation": {
            "source_id": source_id,
            "original_input_id": relation.original_input.artifact_id,
            "mapping_sha256": relation.value["mapping_sha256"],
            "original_admission_receipt_id": binding["admission_receipt_id"],
            "historical_public_receipt_id": public_receipt_id,
        },
        "initialization": initialization,
        "scope": DIAGNOSTIC_SCOPE,
    }


def publish_historical_inputs(
    store: ArtifactStore,
    source_id: str,
    relation: VerifiedHistoricalRelation,
    config: HistoricalM0Config,
    operation_id: str,
    producer: Producer,
) -> Manifest:
    """Owned recipe preparation after the configured Curation use reservation."""
    source_manifest, source_value = read_relation(store, source_id)
    if relation.value != source_value:
        _fail("historical_input_relation_mismatch")
    initialization = warm_start_metadata(store, relation, config)
    original = relation.original_input.parameters.value()
    if (
        config.max_state_tokens != original["max_state_tokens"]
        or config.max_action_bytes != original["max_action_bytes"]
    ):
        _fail("historical_input_frozen_limits_mismatch")
    payload = store.put_payload("rows", io.BytesIO(relation.selected_bytes), "application/x-ndjson")
    item = Manifest(
        "training_input",
        producer,
        (
            Parent("source", source_id),
            Parent("original_input", relation.original_input.artifact_id),
            Parent("model_view", source_value["originals"]["model_view_id"]),
        ),
        (
            payload,
            relation.original_input.payload("state_tokenizer"),
            relation.original_input.payload("action_codec"),
        ),
        FrozenObject.of(
            historical_input_parameters(
                relation,
                source_id,
                operation_id,
                initialization,
                source_manifest.parameters.value()["historical_public_receipt_id"],
            )
        ),
    )
    store.publish(item)
    return item


def load_historical_inputs(
    store: ArtifactStore,
    identity: str,
    *,
    verified_relation: VerifiedHistoricalRelation | None = None,
) -> LoadedLightActionInputs:
    """Recheck the exact subset bytes and reconstruct lossless text for diagnostics."""
    from dataclasses import replace

    from tokenizers import Tokenizer

    from .light_action_inputs import LightActionTokenRow, LoadedLightActionInputs
    from .token_format import input_texts

    manifest = store.get_manifest(identity)
    info = manifest.parameters.value()
    if (
        manifest.kind != "training_input"
        or info.get("schema") != INPUT_SCHEMA
        or sorted(p.role for p in manifest.parents) != ["model_view", "original_input", "source"]
        or sorted(p.role for p in manifest.payloads) != ["action_codec", "rows", "state_tokenizer"]
    ):
        _fail("historical_training_input_required")
    relation = verified_relation or verify_relation(store, manifest.parent("source"))
    source_manifest, source_value = read_relation(store, manifest.parent("source"))
    if relation.value != source_value:
        _fail("historical_input_relation_mismatch")
    binding = info.get("training_binding")
    initialization = info.get("initialization")
    if not isinstance(binding, dict) or not isinstance(initialization, dict):
        _fail("historical_training_binding_required")
    config = HistoricalM0Config(
        warm_start_model_id=digest(initialization.get("model_id"), "historical_m0.initial_model"),
        max_state_tokens=relation.original_input.parameters.value()["max_state_tokens"],
        max_action_bytes=relation.original_input.parameters.value()["max_action_bytes"],
    )
    expected_init = warm_start_metadata(store, relation, config)
    if (
        initialization != expected_init
        or info
        != historical_input_parameters(
            relation,
            manifest.parent("source"),
            digest(binding.get("training_operation_id"), "historical_m0.operation", length=32),
            expected_init,
            source_manifest.parameters.value()["historical_public_receipt_id"],
        )
        or manifest.parent("original_input") != relation.original_input.artifact_id
        or manifest.parent("model_view") != relation.value["originals"]["model_view_id"]
        or manifest.payload("state_tokenizer") != relation.original_input.payload("state_tokenizer")
        or manifest.payload("action_codec") != relation.original_input.payload("action_codec")
        or _read(store, manifest, "rows", MAX_SELECTED_BYTES) != relation.selected_bytes
    ):
        _fail("historical_training_input_projection_mismatch")
    try:
        tokenizer = Tokenizer.from_str(relation.tokenizer_bytes.decode("utf-8"))
    except Exception as error:
        raise BoundaryError("historical_public_m0", "invalid_original_state_tokenizer") from error
    if (
        tokenizer.truncation is not None
        or tokenizer.padding is not None
        or tokenizer.get_vocab_size() != info["state_codec"]["vocab_size"]
    ):
        _fail("original_state_tokenizer_mismatch")
    samples, rows = [], []
    for sample, encoded in zip(relation.samples, relation.encoded_rows, strict=True):
        state = tokenizer.decode(encoded["state"], skip_special_tokens=False)
        if (
            not state.startswith("OBS\n")
            or not state.endswith("\n")
            or tokenizer.encode(state, add_special_tokens=False).ids != encoded["state"]
        ):
            _fail("original_state_encoding_not_lossless")
        raw_state = state[4:-1]
        if input_texts(raw_state, ())[0] != state or sample.state_text != raw_state:
            _fail("original_state_wrapper_mismatch")
        samples.append(replace(sample, state_text=raw_state))
        rows.append(
            LightActionTokenRow(
                tuple(encoded["state"]),
                tuple(tuple(action) for action in encoded["actions"]),
                tuple(encoded["action_ids"]),
            )
        )
    return LoadedLightActionInputs(manifest, tuple(samples), tuple(rows), tokenizer)
