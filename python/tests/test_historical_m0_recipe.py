"""Configured-owner and tiny real-graph fixtures; no private historical data or game."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import uuid
from collections import Counter
from dataclasses import replace
from typing import Any

import pytest
from test_artifact_store_v1 import PRODUCER
from test_reward_m0_pair import pair_originals as pair_originals

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.workbench.historical_m0_data import (
    historical_dev_reference,
    publish_historical_source,
    record_historical_use,
    require_historical_source,
    reserve_historical_dev,
)
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.trusted_recipes import (
    describe_recipe,
    typed_recipe_run_schema,
)
from stpd.canonical import semantic_hash
from stpd.fullrun.historical_public_m0 import (
    compile_relation,
    load_historical_inputs,
    publish_historical_inputs,
    verify_relation,
    warm_start_metadata,
)
from stpd.fullrun.light_action_inputs import _state_codec, fit_state_bpe
from stpd.fullrun.light_action_row_storage import prepare_rows
from stpd.fullrun.public_bc import project_allocation, publish_public_bc_view
from stpd.fullrun.public_inputs import COMPACT_IDENTITY
from stpd.fullrun.token_format import input_texts
from stpd.historical_m0_spec import DIAGNOSTIC_SCOPE, ORDER_CONTRACT, RECIPE, HistoricalM0Config
from stpd.light_action_codec import SPEC, SPEC_BYTES, SPEC_SHA256, encode_action
from stpd.stage1a_recipes import LIGHT_ACTION_M0_GRAPH


def _original_encoded(fixture):
    """Actual Human-proof producer and frozen TRAIN codec; historical version vectors."""
    from tokenizers import Tokenizer

    store, owner, project, historical, _ = fixture
    allocation_id = historical["allocation_id"]
    producer = Producer(
        PRODUCER.repository, ORDER_CONTRACT["view_producer_revision"], PRODUCER.uv_lock_sha256
    )
    view = publish_public_bc_view(store, allocation_id, producer)
    _, _, samples, report = project_allocation(store, allocation_id)
    raw = fit_state_bpe(samples)
    tokenizer = Tokenizer.from_str(raw.decode())
    state_codec = _state_codec(raw, tokenizer, "s", 8192)
    encoded = [
        {
            "state": tokenizer.encode(
                input_texts(row.state_text, ())[0], add_special_tokens=False
            ).ids,
            "actions": [list(encode_action(action, max_bytes=8192)) for action in row.action_texts],
            "action_ids": list(row.action_keys),
        }
        for row in samples
    ]
    with prepare_rows(json_bytes(row) for row in encoded) as (stream, storage, media):
        rows_payload = store.put_payload("rows", stream, media)
    binding = {
        "schema": "stpd/light-action-m0-training-binding-v1",
        "dataset_ids": [historical["dataset_id"]],
        "allocation_id": allocation_id,
        "model_view_id": view.artifact_id,
        "training_operation_id": historical["training_operation_id"],
    }
    info = {
        "schema": "stpd/stage1a-light-action-m0-public-input-v1",
        "format": "stpd-token-light-action-m0-public-v1",
        "graph": LIGHT_ACTION_M0_GRAPH,
        "source_schema": report["schema"],
        "source_renderer": COMPACT_IDENTITY,
        "state_codec": state_codec,
        "action_codec": {**SPEC, "sha256": SPEC_SHA256, "source_renderer": COMPACT_IDENTITY},
        "max_state_tokens": 8192,
        "max_action_bytes": 8192,
        "samples": len(samples),
        "counts": dict(Counter(row.split for row in samples)),
        "training_binding": binding,
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
            rows_payload,
            store.put_payload("state_tokenizer", io.BytesIO(raw)),
            store.put_payload("action_codec", io.BytesIO(SPEC_BYTES)),
        ),
        FrozenObject.of(info),
    )
    store.publish(inputs)
    original = {
        "dataset_id": historical["dataset_id"],
        "allocation_id": allocation_id,
        "model_view_id": view.artifact_id,
        "training_input_id": inputs.artifact_id,
        "admission_operation_id": historical["training_operation_id"],
        "admission_receipt_id": historical["receipt_id"],
    }
    members = json.loads(
        b"".join(store.read_payload(store.get_manifest(allocation_id).payload("members")))
    )["members"]
    selected = [next(row for row in members if row["split"] == split) for split in ("train", "dev")]
    selected.sort(key=lambda row: row["occurrence"])
    return store, owner, project, original, selected, samples, encoded, inputs


def _one_update_original_model(store, samples, rows, inputs, producer):
    """One real synthetic update with the production factory/loss/safe tensor codec.

    This is fixture preparation, not evidence of the private model's old training.
    No checkpoint is fabricated as resumable under the new input.
    """
    import torch
    from safetensors.torch import save

    from stpd.models.losses import listwise_rank_loss
    from stpd.workers.checkpoint_codec import encode_checkpoint
    from stpd.workers.token_ranking import (
        LightActionM0Config,
        config_payload,
        construct_model,
        model_weights,
        seeded_step,
    )

    config = LightActionM0Config(steps=1, seed=19)
    source = inputs.parameters.value()
    model, backbone = construct_model(
        config, source["state_codec"]["vocab_size"], state_codec=source["state_codec"]
    )
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay
    )
    index = next(index for index, sample in enumerate(samples) if sample.split == "train")
    row = rows[index]
    with seeded_step(config.seed, "cpu"):
        scores = model(
            torch.tensor(row["state"], dtype=torch.long),
            tuple(torch.tensor(action, dtype=torch.long) for action in row["actions"]),
        )
        loss = listwise_rank_loss(scores, samples[index].chosen_index)
        assert bool(torch.isfinite(loss))
        loss.backward()
    torch.nn.utils.clip_grad_norm_(
        model.parameters(), config.gradient_clip, error_if_nonfinite=True
    )
    optimizer.step()
    assert optimizer.state
    values = model_weights(model, frozen=False)
    weights = store.put_payload("weights", io.BytesIO(save(values)), "application/vnd.safetensors")
    old_config = {**config_payload(config), "public_profile": "public_compact"}
    run = Manifest(
        "run",
        producer,
        (Parent("training_input", inputs.artifact_id),),
        parameters=FrozenObject.of(
            {
                "schema": "stpd/stage1a-run-v1",
                "config": old_config,
                "fixture": "one_real_synthetic_update",
            }
        ),
    )
    store.publish(run)
    checkpoint_bytes = encode_checkpoint(
        {
            "schema": "stpd/stage1a-light-action-m0-checkpoint-v1",
            "data_identity": semantic_hash([inputs.artifact_id, old_config, [index], backbone]),
            "config": old_config,
            "torch_version": str(torch.__version__),
            "step": 1,
            "model": values,
            "optimizer": optimizer.state_dict(),
            "rng_protocol": "seed_plus_completed_steps_v1",
            "cpu_threads": torch.get_num_threads(),
        }
    )
    checkpoint = Manifest(
        "checkpoint",
        producer,
        (Parent("run", run.artifact_id), Parent("training_input", inputs.artifact_id)),
        (store.put_payload("checkpoint", io.BytesIO(checkpoint_bytes)),),
        FrozenObject.of({"schema": "stpd/stage1a-light-action-m0-checkpoint-v1", "step": 1}),
    )
    store.publish(checkpoint)
    info = {
        "schema": "stpd/stage1a-light-action-m0-public-model-v1",
        "recipe": config.recipe,
        "config": old_config,
        "graph": LIGHT_ACTION_M0_GRAPH,
        "backbone": backbone,
        "state_codec": source["state_codec"],
        "action_codec": source["action_codec"],
        "state_tokenizer_sha256": inputs.payload("state_tokenizer").sha256,
        "action_codec_sha256": SPEC_SHA256,
        "weights_sha256": weights.sha256,
        "source_view_schema": source["source_schema"],
        "source_renderer": source["source_renderer"],
        "input_schema": source["schema"],
        "input_format": source["format"],
        "training_binding": source["training_binding"],
        "steps": 1,
        "qualification": "engineering_only",
        "dtype": "float32",
        "adapter_config": None,
        "adapter_tensor_names": [],
    }
    artifact = Manifest(
        "model",
        producer,
        (
            Parent("run", run.artifact_id),
            Parent("checkpoint", checkpoint.artifact_id),
            Parent("training_input", inputs.artifact_id),
            Parent("model_view", inputs.parent("model_view")),
        ),
        (weights, inputs.payload("state_tokenizer"), inputs.payload("action_codec")),
        FrozenObject.of(info),
    )
    store.publish(artifact)
    return artifact, checkpoint_bytes


def _ledger_exposure(owner):
    with owner.transaction() as db:
        return tuple(
            db.execute("SELECT run,kind,reference FROM curation_uses ORDER BY run,kind,reference")
        ), tuple(
            db.execute(
                "SELECT source,kind,reference FROM curation_source_uses "
                "ORDER BY source,kind,reference"
            )
        )


def test_missing_original_semantic_receipt_rejects_before_store_or_use_mutation(pair_originals):
    store, owner, _, original, selection, _, _, _ = _original_encoded(pair_originals)
    before = store.manifest_ids(), _ledger_exposure(owner)
    with pytest.raises(BoundaryError, match="source_verification_required"):
        publish_historical_source(
            owner,
            store,
            {**original, "admission_receipt_id": "c" * 64},
            selection,
            PRODUCER,
            "b" * 32,
        )
    assert (store.manifest_ids(), _ledger_exposure(owner)) == before


def test_frozen_input_subset_reserves_all_codec_exposure_and_rejects_bad_roster(
    pair_originals,
    monkeypatch,
):
    store, owner, _, original, selection, _, _, _ = _original_encoded(pair_originals)
    before = store.manifest_ids(), _ledger_exposure(owner)
    changed = copy.deepcopy(selection)
    changed[0]["source_archive_sha256"] = "c" * 64
    with pytest.raises(BoundaryError):
        publish_historical_source(owner, store, original, changed, PRODUCER, "b" * 32)
    assert (store.manifest_ids(), _ledger_exposure(owner)) == before
    source_id = publish_historical_source(owner, store, original, selection, PRODUCER, "b" * 32)
    original_row_sha = store.get_manifest(original["training_input_id"]).payload("rows").sha256
    read_payload = store.read_payload

    def selected_only(payload):
        assert payload.sha256 != original_row_sha, "ordinary consume rescanned original rows"
        yield from read_payload(payload)

    monkeypatch.setattr(store, "read_payload", selected_only)
    relation, summary = require_historical_source(owner, store, source_id)
    inherited = set(summary["datasets"][0]["qualified_run_ids"])
    assert owner.ledger.dataset(source_id) == ("training", inherited)
    assert relation.selected_runs <= inherited
    assert len(verify_relation(store, source_id).samples) == 2
    assert len(inherited) > len(relation.selected_runs)
    assert relation.value["scope"]["clean_held_out_claim"] is False


@pytest.mark.parametrize("change", ["sidecar_choice", "valid_state_token"])
def test_resealed_original_parent_cannot_borrow_declared_producer_and_canonical_receipt(
    pair_originals,
    change,
):
    store, owner, _, original, selection, _, _, inputs = _original_encoded(pair_originals)
    relation = compile_relation(store, original, selection)
    if change == "sidecar_choice":
        view = store.get_manifest(original["model_view_id"])
        sidecar = json.loads(b"".join(store.read_payload(view.payload("dispositions"))))
        chosen = next(
            row for row in sidecar["rows"] if row["occurrence"] == selection[0]["occurrence"]
        )
        chosen["chosen_index"] = (chosen["chosen_index"] + 1) % chosen["candidates"]
        changed = replace(
            view,
            payloads=(
                view.payload("samples"),
                store.put_payload("dispositions", io.BytesIO(json_bytes(sidecar))),
            ),
        )
        store.publish(changed)
        inputs = replace(
            inputs,
            parents=(Parent("model_view", changed.artifact_id),),
            parameters=FrozenObject.of(
                {
                    **inputs.parameters.value(),
                    "training_binding": {
                        **inputs.parameters.value()["training_binding"],
                        "model_view_id": changed.artifact_id,
                    },
                }
            ),
        )
        original = {**original, "model_view_id": changed.artifact_id}
    else:
        rows = [
            json.loads(line)
            for line in b"".join(store.read_payload(inputs.payload("rows"))).splitlines()
        ]
        ordinal = relation.value["mapping"][0]["ordinal"]
        row = rows[ordinal]
        row["state"][0] = (row["state"][0] + 1) % inputs.parameters.value()["state_codec"][
            "vocab_size"
        ]
        encoded = store.put_payload(
            "rows", io.BytesIO(b"".join(json_bytes(row) for row in rows)), "application/x-ndjson"
        )
        inputs = replace(
            inputs,
            payloads=(encoded, inputs.payload("state_tokenizer"), inputs.payload("action_codec")),
        )
    store.publish(inputs)
    original = {**original, "training_input_id": inputs.artifact_id}
    before = store.manifest_ids(), _ledger_exposure(owner)
    with owner.transaction() as db:
        receipts = tuple(
            db.execute("SELECT context,identity,receipt FROM local_training_projection_receipts")
        )
    with pytest.raises(BoundaryError, match="H_encoding_or_label_mismatch"):
        publish_historical_source(owner, store, original, selection, PRODUCER, "d" * 32)
    assert (store.manifest_ids(), _ledger_exposure(owner)) == before
    with owner.transaction() as db:
        assert (
            tuple(
                db.execute(
                    "SELECT context,identity,receipt FROM local_training_projection_receipts"
                )
            )
            == receipts
        )


def test_warm_start_has_fresh_optimizer_plan_and_cannot_resume_original_checkpoint(pair_originals):
    import torch
    from safetensors.torch import load, save

    from stpd.workers.token_ranking import LightActionM0Config, TokenRankingEngine

    store, owner, _, original, selection, samples, rows, old_inputs = _original_encoded(
        pair_originals
    )
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    try:
        model, old_checkpoint = _one_update_original_model(
            store, samples, rows, old_inputs, PRODUCER
        )
        source_id = publish_historical_source(owner, store, original, selection, PRODUCER, "b" * 32)
        relation, _ = require_historical_source(owner, store, source_id)
        relation = verify_relation(store, source_id)
        config = HistoricalM0Config(model.artifact_id, seed=97, steps=2)
        record_historical_use(owner, store, source_id, "c" * 32)
        training = publish_historical_inputs(store, source_id, relation, config, "c" * 32, PRODUCER)
        inputs = load_historical_inputs(store, training.artifact_id)
        engine = TokenRankingEngine(inputs, LightActionM0Config(**config.numerical_fields()))
        assert engine.step == 0 and not engine.optimizer.state
        assert (
            engine.plan
            == [
                next(
                    index for index, sample in enumerate(inputs.samples) if sample.split == "train"
                )
            ]
            * 2
        )
        with pytest.raises(BoundaryError, match="warm_start_weights_required"):
            engine.advance()
        # A historical input never inherits a legacy owner's implicit ready
        # state when its constructor omitted the new field. All output/update
        # entries must refuse before touching weights or creating artifacts.
        del engine.initialized
        for entry in (
            engine.advance, lambda: engine.scores(0), engine.model_bytes, engine.checkpoint
        ):
            with pytest.raises(BoundaryError, match="warm_start_weights_required"):
                entry()
        engine.initialized = False
        raw = b"".join(store.read_payload(model.payload("weights")))
        identity = warm_start_metadata(store, relation, config)
        engine.initialize_model_weights(raw, identity)
        assert engine.model_bytes() == raw
        assert engine.step == 0 and not engine.optimizer.state
        with pytest.raises(BoundaryError, match="resume_identity_mismatch"):
            engine.restore(old_checkpoint)
        with pytest.raises(BoundaryError, match="step_zero_identity"):
            engine.initialize_model_weights(raw, identity)
        bad_values = load(raw)
        first = next(iter(bad_values.values()))
        first.flatten()[0] = float("nan")
        bad_raw = save(bad_values)
        bad_init = {**identity, "weights_sha256": hashlib.sha256(bad_raw).hexdigest()}
        changed = replace(
            training,
            parameters=FrozenObject.of({**training.parameters.value(), "initialization": bad_init}),
        )
        bad_inputs = replace(inputs, manifest=changed)
        bad_engine = TokenRankingEngine(
            bad_inputs, LightActionM0Config(**config.numerical_fields())
        )
        with pytest.raises(BoundaryError, match="invalid_weights"):
            bad_engine.initialize_model_weights(bad_raw, bad_init)
        assert (
            bad_engine.step == 0 and not bad_engine.optimizer.state and not bad_engine.initialized
        )
    finally:
        torch.set_num_threads(previous)


def test_real_service_child_completes_and_existing_public_export_scores_exact_inputs(
    pair_originals, tmp_path, monkeypatch
):
    import torch
    from test_public_inputs import snapshot

    from spireagent.source import source_identity
    from spireagent.workbench.developer import ROOT
    from spireagent.workbench.local_model_export import LocalModelExport
    from spireagent.workbench.local_model_registration import LocalModelRegistration
    from spireagent.workbench.local_models import LocalModelService
    from spireagent.workbench.local_training import LocalTrainingService
    from stpd.fullrun.public_inputs import project_public_snapshot
    from stpd.policy.token_decision import (
        PUBLIC_LIGHT_ACTION_M0_EXPORT_SCHEMA,
        LightActionM0DecisionScorer,
    )
    from stpd.workers.token_ranking import LightActionM0Config, TokenRankingEngine

    store, owner, project, original, selection, samples, rows, old_inputs = _original_encoded(
        pair_originals
    )
    original_input_bytes = old_inputs.to_bytes()
    original_payloads_by_role = {payload.role: payload.to_dict() for payload in old_inputs.payloads}
    original_row_bytes = b"".join(store.read_payload(old_inputs.payload("rows")))
    actual_producer = source_identity(ROOT)
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    try:
        initial_model, _ = _one_update_original_model(
            store, samples, rows, old_inputs, actual_producer
        )
        source_id = publish_historical_source(
            owner, store, original, selection, actual_producer, "b" * 32
        )
        config = HistoricalM0Config(initial_model.artifact_id, steps=1, seed=101)
        request = TrainingRequest(
            uuid.uuid4().hex,
            RECIPE,
            source_id,
            config.to_dict(),
            limits={"wall_seconds": 120, "scratch_bytes": 512 * 1024**2},
        )
        description = describe_recipe(RECIPE)
        assert description["automatic_retry"] is False and description["remote_execution"] is False
        assert typed_recipe_run_schema(RECIPE) != "stpd/stage1a-run-v1"
        service = LocalTrainingService(project)
        admitted = service.start(request)
        assert service._thread is not None
        service._thread.join(timeout=120)
        assert not service._thread.is_alive()
        terminal = service.status(admitted["operation"]["operation_id"])
        operation = terminal["operation"]
        assert operation["status"] == "completed", terminal
        assert operation["worker_state"] == "terminal"
        assert operation["progress"]["completed"] == 1
        inherited = owner.ledger.dataset(source_id)[1]
        with owner.transaction() as db:
            uses = {
                row[0]
                for row in db.execute(
                    "SELECT run FROM curation_uses WHERE reference=? AND kind='training'",
                    (operation["operation_id"],),
                )
            }
            assert uses == inherited
        result = ObjectStoreRunReporter(store, store.blobs).completed(operation["run_id"])
        assert result is not None
        model_id = result.parent("model")
        model = store.get_manifest(model_id)
        assert model.parameters.value()["schema"] == initial_model.parameters.value()["schema"]
        assert model.parameters.value()["scope"] == DIAGNOSTIC_SCOPE
        metrics = store.get_manifest(result.parent("offline_evaluation"))
        assert metrics.parameters.value()["clean_held_out_claim"] is False
        # Exercise the real selected-scope owner entry on the actual child Model,
        # including idempotence and fail-before-mutation reference/policy fences.
        evaluation_id = historical_dev_reference(model_id, source_id, operation["operation_id"])
        relation, summary = require_historical_source(owner, store, source_id)
        dev = [member for member in relation.value["members"] if member["split"] == "dev"]
        selected_dev_runs = {member["run_id"] for member in dev}
        selected_dev_archives = {member["source_archive_sha256"] for member in dev}
        selected_dev_sources = {
            source["artifact_id"]
            for source in summary["datasets"][0]["sources"]
            if source["archive_sha256"] in selected_dev_archives
        }
        with owner.transaction() as db:
            expected_related = owner.ledger._groups(db, selected_dev_runs)
            expected_overlap = bool(
                expected_related
                & owner.ledger._groups(
                    db,
                    {m["run_id"] for m in relation.value["members"] if m["split"] == "train"},
                )
            )
            assert {
                row[0]
                for row in db.execute(
                    "SELECT run FROM curation_uses WHERE kind='evaluation' AND reference=?",
                    (evaluation_id,),
                )
            } == expected_related
            assert {
                row[0]
                for row in db.execute(
                    "SELECT source FROM curation_source_uses "
                    "WHERE kind='evaluation' AND reference=?",
                    (evaluation_id,),
                )
            } == selected_dev_sources
        exposed = _ledger_exposure(owner)
        original_payloads = {
            payload.sha256
            for identity in (original["allocation_id"], original["model_view_id"])
            for payload in store.get_manifest(identity).payloads
        }
        original_payloads.add(old_inputs.payload("rows").sha256)
        original_payloads.update(
            payload.sha256
            for source in summary["datasets"][0]["sources"]
            for payload in store.get_manifest(source["artifact_id"]).payloads
        )
        read_payload = store.read_payload

        def no_original_payload_reads(payload):
            assert payload.sha256 not in original_payloads, "DEV hydrated original payload"
            yield from read_payload(payload)

        monkeypatch.setattr(store, "read_payload", no_original_payload_reads)
        admitted_dev = reserve_historical_dev(
            owner, store, model_id, source_id, operation["operation_id"], evaluation_id
        )
        assert _ledger_exposure(owner) == exposed
        assert admitted_dev["semantic_overlap"] == expected_overlap
        assert admitted_dev["clean_held_out_claim"] is False
        assert admitted_dev["qualified_run_ids"] == sorted(selected_dev_runs)
        for wrong_model, wrong_source, wrong_reference in (
            (initial_model.artifact_id, source_id, evaluation_id),
            (model_id, original["dataset_id"], evaluation_id),
            (model_id, source_id, operation["operation_id"]),
        ):
            with pytest.raises(BoundaryError, match="reference_binding_mismatch"):
                reserve_historical_dev(
                    owner,
                    store,
                    wrong_model,
                    wrong_source,
                    operation["operation_id"],
                    wrong_reference,
                )
            assert _ledger_exposure(owner) == exposed
        # A structurally valid original public Model still cannot be substituted
        # even when its caller supplies a newly computed reference.
        with pytest.raises(BoundaryError, match="model_source_binding_mismatch"):
            reserve_historical_dev(
                owner,
                store,
                initial_model.artifact_id,
                source_id,
                operation["operation_id"],
                historical_dev_reference(
                    initial_model.artifact_id, source_id, operation["operation_id"]
                ),
            )
        assert _ledger_exposure(owner) == exposed
        with pytest.raises(BoundaryError):
            reserve_historical_dev(
                owner,
                store,
                model_id,
                original["dataset_id"],
                operation["operation_id"],
                historical_dev_reference(
                    model_id, original["dataset_id"], operation["operation_id"]
                ),
            )
        assert _ledger_exposure(owner) == exposed
        wrong_model = replace(
            model,
            parameters=FrozenObject.of(
                {
                    **model.parameters.value(),
                    "training_binding": {
                        **model.parameters.value()["training_binding"],
                        "allocation_id": "f" * 64,
                    },
                }
            ),
        )
        store.publish(wrong_model)
        with pytest.raises(BoundaryError, match="model_source_binding_mismatch"):
            reserve_historical_dev(
                owner,
                store,
                wrong_model.artifact_id,
                source_id,
                operation["operation_id"],
                historical_dev_reference(
                    wrong_model.artifact_id, source_id, operation["operation_id"]
                ),
            )
        assert _ledger_exposure(owner) == exposed
        for purpose in ("test", "gold"):
            # Synthetic imported conflicting ledger state: exercise the existing
            # current-owner policy fences rather than changing its public kind API.
            claim = "blocked-" + purpose
            with owner.transaction() as db:
                db.execute(
                    "INSERT INTO curation_claims VALUES(?,?,?,?)",
                    (claim, purpose, None, 0.0),
                )
                db.executemany(
                    "INSERT INTO curation_claim_runs VALUES(?,?)",
                    ((claim, run) for run in selected_dev_runs),
                )
            try:
                with pytest.raises(BoundaryError):
                    reserve_historical_dev(
                        owner, store, model_id, source_id, operation["operation_id"], evaluation_id
                    )
                assert _ledger_exposure(owner) == exposed
            finally:
                with owner.transaction() as db:
                    db.execute("DELETE FROM curation_claim_runs WHERE claim=?", (claim,))
                    db.execute("DELETE FROM curation_claims WHERE id=?", (claim,))
        monkeypatch.setattr(store, "read_payload", read_payload)
        exported = LocalModelExport(project)
        exported.start(model_id)
        assert exported.thread is not None
        exported.thread.join(timeout=120)
        assert not exported.thread.is_alive()
        export_result = exported.status()["operation"]
        assert export_result["status"] == "completed", export_result
        destination = exported.verified_public_m0_for_registration(model_id)
        assert (
            json.loads((destination / "model.json").read_bytes())["schema"]
            == PUBLIC_LIGHT_ACTION_M0_EXPORT_SCHEMA
        )
        scorer = LightActionM0DecisionScorer(destination)
        inputs = load_historical_inputs(store, model.parent("training_input"))
        engine = TokenRankingEngine(inputs, LightActionM0Config(**config.numerical_fields()))
        checkpoint = store.get_manifest(model.parent("checkpoint"))
        engine.restore(b"".join(store.read_payload(checkpoint.payload("checkpoint"))))
        for index, sample in enumerate(inputs.samples):
            assert scorer.score_texts(sample.state_text, sample.action_texts) == engine.scores(
                index
            )
        public = snapshot()
        projected = project_public_snapshot(public, compact=True)
        scored = scorer.score_snapshot(public)
        assert tuple(scored) == tuple(action.key for action in projected.actions)
        assert tuple(scored.values()) == scorer.score_texts(
            projected.state_text, projected.action_texts
        )
        old_rows = b"".join(store.read_payload(old_inputs.payload("rows")))
        assert old_rows and old_rows == original_row_bytes
        assert hashlib.sha256(old_rows).hexdigest() == old_inputs.payload("rows").sha256
        stored_original = store.get_manifest(original["training_input_id"])
        assert (
            stored_original.artifact_id == old_inputs.artifact_id == original["training_input_id"]
        )
        assert stored_original.to_bytes() == original_input_bytes
        assert {
            payload.role: payload.to_dict() for payload in stored_original.payloads
        } == original_payloads_by_role
        from test_public_m0_policy_port import capabilities

        models = LocalModelService(project)
        registration = LocalModelRegistration(project, exported, models)
        # Only physical installed Runtime/SDK discovery is synthetic here. The
        # real export receipt, metadata reader, binder/registry and model remain.
        monkeypatch.setattr(
            registration,
            "_native_runtime",
            lambda **kwargs: (project.state_dir, project.state_dir / "sdk.js"),
        )
        monkeypatch.setattr(registration, "_capabilities", lambda *args, **kwargs: capabilities())
        monkeypatch.setattr(models, "_public_manifest_contract", lambda *args, **kwargs: None)
        registered = registration.register(model_id, task=None)
        assert registered["status"] == "registered" and registered["loaded"] is False
        selected = models.selection(registered["selection_id"])
        from stpd.public_m0_policy_installation import validate

        _, manifest = validate(
            ROOT,
            models.entry_path(selected, "config"),
            models.entry_path(selected, "manifest"),
            binding_root=models.entry_root(selected),
        )
        assert manifest["representation"]["input_schema"] == "sts2.player-environment/snapshot-1"
    finally:
        torch.set_num_threads(previous)


def test_actual_historical_app_pause_resume_uses_checkpoint_without_second_warm_start(
    pair_originals, monkeypatch
):
    import torch

    from spireagent.source import source_identity
    from spireagent.workbench.developer import ROOT
    from spireagent.workbench.local_training import LocalTrainingService
    from spireagent.workbench.recipes import structured as adapter
    from stpd.workers.checkpoint_codec import decode_checkpoint

    store, owner, project, original, selection, samples, rows, old_inputs = _original_encoded(
        pair_originals
    )
    producer = source_identity(ROOT)
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    try:
        initial_model, _ = _one_update_original_model(store, samples, rows, old_inputs, producer)
        source_id = publish_historical_source(owner, store, original, selection, producer, "b" * 32)
        config = HistoricalM0Config(
            initial_model.artifact_id, steps=2, seed=107, checkpoint_every_boundaries=1
        )
        request = TrainingRequest(
            uuid.uuid4().hex,
            RECIPE,
            source_id,
            config.to_dict(),
            limits={"wall_seconds": 120, "scratch_bytes": 512 * 1024**2},
        )
        service = LocalTrainingService(project)
        original_child = adapter._private_child
        observed: dict[str, Any] = {"pause": None, "next_reservation": False, "exits": []}

        def real_child_with_parent_observer(command, *args, **kwargs):
            on_line = kwargs["on_stdout_line"]
            on_exit = kwargs["on_exited"]

            def line(raw):
                message = json.loads(raw)
                if message["kind"] == "reserve_artifact" and observed.get("awaiting_checkpoint"):
                    # The actual child is blocked in reserve_artifact until this
                    # callback ACKs. Pause intent was durably written at step 1.
                    assert message["details"]["role"] == "payload"
                    current = service.status(message["operation_id"])["operation"]
                    assert current["requested_action"] == "pause"
                    assert current["progress"]["completed"] == 1
                    observed.update(awaiting_checkpoint=False, next_reservation=True)
                on_line(raw)
                if message["kind"] == "event":
                    event = store.get_manifest(message["details"]["event_id"])
                    info = event.parameters.value()
                    if (
                        info["kind"] == "step"
                        and info["details"]["optimizer_updates"] == 1
                        and observed["pause"] is None
                    ):
                        observed["trigger"] = event
                        observed["pause"] = service.pause(
                            message["operation_id"], message["attempt_id"]
                        )
                        observed["awaiting_checkpoint"] = True

            def exited(code, forced, seconds):
                observed["exits"].append((code, forced, seconds))
                on_exit(code, forced, seconds)

            kwargs.update(on_stdout_line=line, on_exited=exited)
            # Preserve the actual production command, channel and computation.
            return original_child(command, *args, **kwargs)

        monkeypatch.setattr(adapter, "_private_child", real_child_with_parent_observer)
        service.start(request)
        assert service._thread is not None
        service._thread.join(timeout=120)
        assert not service._thread.is_alive()
        paused = service.status()["operation"]
        assert observed["next_reservation"] and observed["pause"] is not None
        assert paused["status"] == "paused" and paused["progress"]["completed"] == 1
        assert paused["worker_state"] == "terminal"
        assert paused["domain_completion_state"] == "not_completed"
        assert paused["requested_action"] == "pause"
        assert paused["child_exit"]["exit_code"] == 0 and paused["child_exit"]["forced"] is False
        run_id, input_id = paused["run_id"], paused["input_id"]
        first_attempt = paused["attempt_id"]
        inputs = store.get_manifest(input_id)
        checkpoint = store.get_manifest(paused["checkpoint_id"])
        checkpoint_info = checkpoint.parameters.value()
        state = decode_checkpoint(b"".join(store.read_payload(checkpoint.payload("checkpoint"))))
        assert checkpoint_info["step"] == state["step"] == 1
        assert checkpoint_info["attempt"] == first_attempt
        assert checkpoint_info["data_identity"] == state["data_identity"]
        assert checkpoint_info["initialization"] == inputs.parameters.value()["initialization"]
        assert state["optimizer"]["state"]
        assert all(float(slot["step"]) == 1 for slot in state["optimizer"]["state"].values())
        reporter = ObjectStoreRunReporter(store, store.blobs)
        assert reporter.completed(run_id) is None
        path = owner.path.parent / "local-training-operation.json"
        paused_journal = path.read_bytes()
        manifests_before_wrong_resume = store.manifest_ids()
        with pytest.raises(BoundaryError, match="checkpoint_run_initialization_mismatch"):
            service.resume(
                paused["operation_id"],
                first_attempt,
                initial_model.parent("checkpoint"),
                uuid.uuid4().hex,
                request.limits,
            )
        assert path.read_bytes() == paused_journal
        assert store.manifest_ids() == manifests_before_wrong_resume

        _, summary = require_historical_source(owner, store, source_id)
        unavailable_hashes = {
            initial_model.payload("weights").sha256,
            old_inputs.payload("rows").sha256,
        } | {source["archive_sha256"] for source in summary["datasets"][0]["sources"]}
        protected_hashes = {
            payload.sha256
            for manifest in (store.get_manifest(source_id), inputs, checkpoint)
            for payload in manifest.payloads
        }
        assert unavailable_hashes.isdisjoint(protected_hashes)
        held_indexes = []
        try:
            for sha in sorted(unavailable_hashes):
                index = store.blobs.root / "payload-indexes/v1" / (sha + ".json")
                hidden = index.with_suffix(".resume-proof-unavailable")
                assert index.is_file() and not index.is_symlink() and not hidden.exists()
                before = index.read_bytes()
                index.rename(hidden)
                held_indexes.append((index, hidden, before))
            # Original private synthetic body indexes are unavailable. No old
            # archives/whole rows/initial weights can be hydrated by this resume.
            resumed = service.resume(
                paused["operation_id"],
                first_attempt,
                checkpoint.artifact_id,
                uuid.uuid4().hex,
                request.limits,
            )["operation"]
            assert resumed["operation_id"] == paused["operation_id"]
            assert resumed["run_id"] == run_id and resumed["input_id"] == input_id
            assert resumed["attempt_id"] != first_attempt
            assert service._thread is not None
            service._thread.join(timeout=120)
            assert not service._thread.is_alive()
            completed = service.status()["operation"]
            assert completed["status"] == "completed", completed
            assert completed["progress"]["completed"] == 2
            assert completed["worker_state"] == "terminal"
            assert completed["run_id"] == run_id and completed["input_id"] == input_id
            assert completed["child_exit"]["exit_code"] == 0
            assert completed["child_exit"]["forced"] is False
            assert completed["limits"] == paused["limits"] == request.limits
            assert completed["elapsed_seconds"] >= (
                paused["elapsed_seconds"] + completed["child_exit"]["elapsed_seconds"]
            )
            assert completed["elapsed_seconds"] < request.limits["wall_seconds"]
            assert completed["artifact_reserved_bytes"] >= paused["artifact_reserved_bytes"]
            assert all(not index.exists() for index, _, _ in held_indexes)
            final_checkpoint = store.get_manifest(completed["checkpoint_id"])
            final_info = final_checkpoint.parameters.value()
            final_state = decode_checkpoint(
                b"".join(store.read_payload(final_checkpoint.payload("checkpoint")))
            )
            assert final_info["step"] == final_state["step"] == 2
            assert final_info["attempt"] == completed["attempt_id"]
            assert final_info["initialization"] == checkpoint_info["initialization"]
            assert (
                final_info["data_identity"]
                == final_state["data_identity"]
                == state["data_identity"]
            )
            assert final_state["optimizer"]["param_groups"] == state["optimizer"]["param_groups"]
            assert final_state["optimizer"]["state"].keys() == state["optimizer"]["state"].keys()
            assert all(
                float(slot["step"]) == 2 for slot in final_state["optimizer"]["state"].values()
            )
            events = [event.parameters.value() for event in reporter.events(run_id)]
            assert [event["step"] for event in events] == list(range(1, len(events) + 1))
            assert [
                event["details"]["optimizer_updates"] for event in events if event["kind"] == "step"
            ] == [1, 2]
            starts = [event for event in events if event["kind"] == "started"]
            resumes = [event for event in events if event["kind"] == "resumed"]
            assert len(starts) == len(resumes) == 1
            assert starts[0]["attempt"] == first_attempt
            assert resumes[0]["attempt"] == completed["attempt_id"]
            assert resumes[0]["details"]["optimizer_updates"] == 1
            loadings = [event for event in events if event["kind"] == "loading"]
            assert [event["details"]["resume"] for event in loadings] == [
                None,
                checkpoint.artifact_id,
            ]
            result = reporter.completed(run_id)
            assert result is not None
            assert result.parent("training_input") == input_id
            assert [
                manifest.artifact_id
                for identity in store.manifest_ids()
                if (manifest := store.get_manifest(identity)).kind == "run_result"
                and manifest.parent("run") == run_id
            ] == [result.artifact_id]
            durable = json.loads(path.read_bytes())
            assert durable["attempts"][0]["attempt_id"] == first_attempt
            assert durable["attempts"][0]["writer_terminal"] is True
            assert durable["writer_terminal"] is True
            assert len(observed["exits"]) == 2
            assert all(code == 0 and forced is False for code, forced, _ in observed["exits"])
        finally:
            recreated = [index for index, _, _ in held_indexes if index.exists()]
            for index, hidden, _ in held_indexes:
                hidden.replace(index)
            assert all(index.read_bytes() == before for index, _, before in held_indexes)
            assert not recreated, "Resume recreated a forbidden original body index"
    finally:
        torch.set_num_threads(previous)
