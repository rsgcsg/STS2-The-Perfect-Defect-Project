"""Synthetic ordered producer bytes; these fixtures do not attest Human admission."""

from __future__ import annotations

import copy
import hashlib
import io
from collections import Counter
from dataclasses import replace
from typing import Any

import pytest
from test_artifact_store_v1 import PRODUCER, store

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from stpd.canonical import semantic_hash
from stpd.fullrun.features import ModelSample
from stpd.fullrun.historical_public_m0 import (
    compile_relation,
    relation_manifest,
    verify_relation,
)
from stpd.fullrun.light_action_inputs import _state_codec, fit_state_bpe
from stpd.fullrun.light_action_row_storage import decoded_rows, prepare_rows
from stpd.fullrun.public_inputs import COMPACT_IDENTITY
from stpd.fullrun.token_format import input_texts
from stpd.historical_m0_spec import ORDER_CONTRACT
from stpd.light_action_codec import SPEC, SPEC_BYTES, SPEC_SHA256, encode_action
from stpd.stage1a_recipes import LIGHT_ACTION_M0_GRAPH


def synthetic_originals(tmp_path, monkeypatch=None, compressed=False):
    """Exercise the production canonical wrapper, byte codec, row storage and store.

    The source/producer declarations are synthetic ordering-contract fixtures;
    actual canonical admission is separately tested through a configured owner.
    """
    from tokenizers import Tokenizer

    archive = store(tmp_path)
    dataset = Manifest(
        "dataset",
        PRODUCER,
        parameters=FrozenObject.of(
            {
                "purpose": "training",
                "logical_id": semantic_hash("fixture-dataset"),
            }
        ),
    )
    archive.publish(dataset)
    members = [
        {
            "occurrence": semantic_hash(["occurrence", index]),
            "transition_id": semantic_hash(["transition", index]),
            "source_archive_sha256": semantic_hash(["archive", index // 2]),
            "run_id": "run-" + ("train" if index < 2 else "dev"),
            "split": "train" if index < 2 else "dev",
        }
        for index in range(4)
    ]
    member_value = {
        "schema": "stpd/decision-allocation-v1",
        "members": members,
        "counts": dict(Counter(row["split"] for row in members)),
    }
    member_payload = archive.put_payload("members", io.BytesIO(json_bytes(member_value)))
    allocation = Manifest(
        "protocol",
        PRODUCER,
        (Parent("dataset", dataset.artifact_id),),
        (member_payload,),
        FrozenObject.of(
            {
                "schema": member_value["schema"],
                "counts": member_value["counts"],
            }
        ),
    )
    archive.publish(allocation)
    samples, dispositions = [], []
    for index, row in enumerate(members):
        disposition: dict[str, Any] = {
            key: row[key]
            for key in (
                "occurrence",
                "transition_id",
                "source_archive_sha256",
                "split",
            )
        }
        disposition.update(proof_ref=semantic_hash(["proof", index]), surface="combat_turn")
        if index == 3:
            disposition.update(status="excluded", reason="human_observation_missing")
        else:
            # Two identical-looking encoded occurrences are still two members.
            sample = ModelSample(
                row["transition_id"],
                row["run_id"],
                row["split"],
                "combat_turn",
                "fixture",
                "same public\nstate\n含义",
                ("first", "second"),
                ("first-key", "second-key"),
                index % 2,
            )
            samples.append(sample)
            disposition.update(
                status="included",
                human_frame_sha256=semantic_hash(["H", index]),
                candidates=2,
                chosen_index=sample.chosen_index,
            )
        dispositions.append(disposition)
    sidecar = {
        "schema": "stpd/public-observation-bc-view-v2",
        "allocation_id": allocation.artifact_id,
        "serializer": COMPACT_IDENTITY,
        "label_boundary": "exact_human_observation_bound_action",
        "successor_supervision": False,
        "rows": dispositions,
        "counts": {"included": 3, "excluded": 1},
    }
    view = Manifest(
        "model_view",
        Producer(
            PRODUCER.repository, ORDER_CONTRACT["view_producer_revision"], PRODUCER.uv_lock_sha256
        ),
        (Parent("allocation", allocation.artifact_id), Parent("dataset", dataset.artifact_id)),
        (
            archive.put_payload(
                "samples", io.BytesIO(b"".join(json_bytes(row.to_dict()) for row in samples))
            ),
            archive.put_payload("dispositions", io.BytesIO(json_bytes(sidecar))),
        ),
        FrozenObject.of(
            {
                "schema": sidecar["schema"],
                "serializer": COMPACT_IDENTITY,
                "scope": "platform_verified",
                "label_boundary": sidecar["label_boundary"],
                "successor_supervision": False,
                "samples": 3,
                "counts": sidecar["counts"],
            }
        ),
    )
    archive.publish(view)
    tokenizer_bytes = fit_state_bpe(tuple(samples), vocab_size=256)
    tokenizer = Tokenizer.from_str(tokenizer_bytes.decode())
    state_codec = _state_codec(tokenizer_bytes, tokenizer, "s", 8192)
    rows = [
        {
            "state": tokenizer.encode(
                input_texts(sample.state_text, ())[0], add_special_tokens=False
            ).ids,
            "actions": [list(encode_action(text, max_bytes=8192)) for text in sample.action_texts],
            "action_ids": list(sample.action_keys),
        }
        for sample in samples
    ]
    logical = b"".join(json_bytes(row) for row in rows)
    if compressed:
        assert monkeypatch is not None
        monkeypatch.setattr(
            "stpd.fullrun.light_action_row_storage.MAX_STORED_BYTES", len(logical) - 1
        )
    with prepare_rows([logical]) as (encoded, storage, media):
        payload = archive.put_payload("rows", encoded, media)
    info = {
        "schema": "stpd/stage1a-light-action-m0-public-input-v1",
        "format": "stpd-token-light-action-m0-public-v1",
        "graph": LIGHT_ACTION_M0_GRAPH,
        "source_schema": sidecar["schema"],
        "source_renderer": COMPACT_IDENTITY,
        "state_codec": state_codec,
        "action_codec": {**SPEC, "sha256": SPEC_SHA256, "source_renderer": COMPACT_IDENTITY},
        "max_state_tokens": 8192,
        "max_action_bytes": 8192,
        "samples": len(samples),
        "training_binding": {
            "schema": "stpd/light-action-m0-training-binding-v1",
            "dataset_ids": [dataset.artifact_id],
            "training_operation_id": "a" * 32,
            "allocation_id": allocation.artifact_id,
            "model_view_id": view.artifact_id,
        },
    }
    if storage is not None:
        info["rows_storage"] = storage
    inputs = Manifest(
        "training_input",
        Producer(
            PRODUCER.repository, ORDER_CONTRACT["input_producer_revision"], PRODUCER.uv_lock_sha256
        ),
        (Parent("model_view", view.artifact_id),),
        (
            payload,
            archive.put_payload("state_tokenizer", io.BytesIO(tokenizer_bytes)),
            archive.put_payload("action_codec", io.BytesIO(SPEC_BYTES)),
        ),
        FrozenObject.of(info),
    )
    archive.publish(inputs)
    binding = {
        "dataset_id": dataset.artifact_id,
        "allocation_id": allocation.artifact_id,
        "model_view_id": view.artifact_id,
        "training_input_id": inputs.artifact_id,
        "admission_operation_id": "a" * 32,
        "admission_receipt_id": "b" * 64,
    }
    selection = sorted(members[:3], key=lambda row: row["occurrence"])
    return archive, binding, selection, logical, members


def frozen_byte_fixture(archive, relation):
    """Unadmitted storage fixture; no configured owner holds this placeholder proof."""
    samples = tuple(
        replace(sample, state_text="same public\nstate\n含义") for sample in relation.samples
    )
    sample_bytes = b"".join(json_bytes(sample.to_dict()) for sample in samples)
    value = {
        **relation.value,
        "selected_H_proof": [],
        "selected_samples_sha256": hashlib.sha256(sample_bytes).hexdigest(),
        "selected_rows_sha256": hashlib.sha256(relation.selected_bytes).hexdigest(),
    }
    frozen = replace(relation, value=value, samples=samples)
    return frozen, relation_manifest(archive, frozen, PRODUCER, "b" * 64)


@pytest.mark.parametrize("compressed", [False, True])
def test_exact_occurrences_keep_order_full_catalog_label_and_original_bytes(
    tmp_path, monkeypatch, compressed
):
    archive, binding, selection, logical, _ = synthetic_originals(tmp_path, monkeypatch, compressed)
    before = archive.manifest_ids()
    relation = compile_relation(archive, binding, selection)
    assert archive.manifest_ids() == before
    assert relation.selected_bytes == logical
    assert relation.value["counts"] == {"train": 2, "dev": 1}
    assert [row["ordinal"] for row in relation.value["mapping"]] == [0, 1, 2]
    assert [row["chosen_index"] for row in relation.value["mapping"]] == [0, 1, 0]
    assert relation.encoded_rows[0] == relation.encoded_rows[1]
    assert relation.value["mapping"][0]["occurrence"] != relation.value["mapping"][1]["occurrence"]
    assert (
        relation.value["mapping"][0]["encoded_line_sha256"]
        == relation.value["mapping"][1]["encoded_line_sha256"]
    )
    assert all(row["candidate_count"] == 2 for row in relation.value["mapping"])
    assert relation.value["mapping_sha256"] == semantic_hash(relation.value["mapping"])
    frozen, item = frozen_byte_fixture(archive, relation)
    assert verify_relation(archive, item.artifact_id).value == frozen.value
    assert b"".join(archive.read_payload(relation.original_input.payload("rows")))


@pytest.mark.parametrize(
    "change", ["duplicate", "run", "split", "transition", "archive", "excluded", "order", "missing"]
)
def test_changed_original_members_do_not_become_a_new_selection(tmp_path, change):
    archive, binding, selection, _, members = synthetic_originals(tmp_path)
    selected = copy.deepcopy(selection)
    if change == "duplicate":
        selected.append(copy.deepcopy(selected[0]))
    elif change == "excluded":
        selected.append(members[3])
    elif change == "order":
        selected.reverse()
    else:
        field = {
            "run": "run_id",
            "split": "split",
            "transition": "transition_id",
            "archive": "source_archive_sha256",
            "missing": "occurrence",
        }[change]
        selected[0][field] = (
            ("dev" if selected[0][field] == "train" else "train") if field == "split" else "c" * 64
        )
    if change != "order":
        selected.sort(key=lambda row: row["occurrence"])
    before = archive.manifest_ids()
    with pytest.raises(BoundaryError):
        compile_relation(archive, binding, selected)
    assert archive.manifest_ids() == before


@pytest.mark.parametrize(
    "change", ["ordinal", "row", "label", "catalog", "parent", "mapping_order"]
)
def test_rehashed_relation_cannot_reassign_an_original_row(tmp_path, change):
    archive, binding, selection, _, _ = synthetic_originals(tmp_path)
    relation = compile_relation(archive, binding, selection)
    frozen, original = frozen_byte_fixture(archive, relation)
    value = copy.deepcopy(frozen.value)
    if change == "mapping_order":
        value["mapping"].reverse()
    elif change == "parent":
        value["parent_payloads"]["encoded_rows"]["sha256"] = "c" * 64
    else:
        field = {
            "ordinal": "ordinal",
            "row": "encoded_line_sha256",
            "label": "chosen_index",
            "catalog": "action_ids_sha256",
        }[change]
        value["mapping"][0][field] = 1 if change in {"ordinal", "label"} else "c" * 64
    value["mapping_sha256"] = semantic_hash(value["mapping"])
    altered = replace(
        original,
        payloads=(
            archive.put_payload("relation", io.BytesIO(json_bytes(value))),
            original.payload("rows"),
            original.payload("samples"),
        ),
    )
    archive.publish(altered)
    with pytest.raises(BoundaryError, match="relation_(projection|parent)_mismatch"):
        verify_relation(archive, altered.artifact_id)


def test_codec_and_parent_changes_reject_before_row_consumption(tmp_path):
    archive, binding, selection, _, _ = synthetic_originals(tmp_path)
    inputs = archive.get_manifest(binding["training_input_id"])
    changed = replace(
        inputs,
        parameters=FrozenObject.of(
            {
                **inputs.parameters.value(),
                "state_codec": {**inputs.parameters.value()["state_codec"], "sha256": "c" * 64},
            }
        ),
    )
    archive.publish(changed)
    with pytest.raises(BoundaryError, match="codec_or_input"):
        compile_relation(archive, {**binding, "training_input_id": changed.artifact_id}, selection)
    wrong_parent = replace(inputs, parents=(Parent("model_view", binding["dataset_id"]),))
    archive.publish(wrong_parent)
    with pytest.raises(BoundaryError, match="parent_or_order"):
        compile_relation(
            archive, {**binding, "training_input_id": wrong_parent.artifact_id}, selection
        )


def test_compressed_original_checks_split_header_truncation_and_trailing_data(
    tmp_path, monkeypatch
):
    archive, binding, selection, logical, _ = synthetic_originals(tmp_path, monkeypatch, True)
    inputs = archive.get_manifest(binding["training_input_id"])
    payload = inputs.payload("rows")
    raw = b"".join(archive.read_payload(payload))
    storage = inputs.parameters.value()["rows_storage"]
    kwargs = {"stored_size": len(raw), "media_type": payload.media_type, "identity": storage}
    assert b"".join(decoded_rows((raw[i : i + 1] for i in range(len(raw))), **kwargs)) == logical
    for corrupted in (raw[:-1], raw + b"trailing", raw[:30] + b"x" + raw[31:]):
        with pytest.raises(BoundaryError):
            b"".join(decoded_rows([corrupted], **{**kwargs, "stored_size": len(corrupted)}))
    assert (
        compile_relation(archive, binding, selection).value["logical_sha256"]
        == hashlib.sha256(logical).hexdigest()
    )
