"""Actual immutable Store/source-verifier/fixed-weights synthetic CPU composition."""

from __future__ import annotations

import copy
import io
import runpy

import pytest
import test_structured_s0 as fixtures
import torch

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from stpd.fullrun import protocol_source as sources
from stpd.models.structured_training import evaluate_runs
from stpd.policy.structured_export import ROOT
from stpd.workers.structured_evaluation import (
    StructuredEvaluationRequest,
    prepare_structured_evaluation,
    run_structured_evaluation,
)

cpu_threads = fixtures.cpu_threads
exported = fixtures.exported

FIXTURE = runpy.run_path(str(ROOT.parent / "tools/test/test_baseline_s0_dataset.py"))
RAW_PRODUCER = Producer("fixture://original", "e" * 40, "a" * 64)
PRODUCER = Producer("fixture://projector", "b" * 40, "b" * 64)
MODEL_PRODUCER = Producer("fixture://model", "a" * 40, "c" * 64)
OPERATION = "a" * 32


def setup(tmp_path, exported, partition="dev"):
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    (tmp_path / "raw").mkdir()
    directory, _ = FIXTURE["create_run"](tmp_path / "raw", seed="2", suffix="eval")
    ref = sources.publish_protocol_run(
        store, directory, RAW_PRODUCER, PRODUCER, origin="synthetic_fixture")
    source = sources.publish_protocol_source_partition(store, (ref,), partition, PRODUCER)
    package = exported[0]
    model = Manifest("model", MODEL_PRODUCER, payloads=tuple(
        store.put_payload(role, io.BytesIO((package / path).read_bytes()), media)
        for role, path, media in (
            ("package_manifest", "model.json", "application/json"),
            ("weights", "weights.tensor-tree", "application/vnd.stpd.tensor-tree"),
        )), parameters=FrozenObject.of({"schema": "stpd/structured-m2-model-v1",
                                      "model_id": exported[1]["model_id"]}))
    store.publish(model)
    return store, source, model


@pytest.mark.parametrize("partition", ["dev", "test"])
def test_actual_partition_fixed_weights_replay_no_optimizer(tmp_path, exported, monkeypatch,
                                                           partition):
    store, source, model = setup(tmp_path, exported, partition)
    request = StructuredEvaluationRequest(source.manifest.artifact_id, model.artifact_id,
                                          OPERATION, partition)
    before = {key: value.clone() for key, value in exported[2].state_dict().items()}

    def forbidden(*args, **kwargs):
        raise AssertionError("fixed model evaluation must never construct optimizer")

    monkeypatch.setattr(torch.optim, "AdamW", forbidden)
    prepared = prepare_structured_evaluation(store, request, PRODUCER)
    result = run_structured_evaluation(store, prepared.artifact_id, PRODUCER)
    replay = run_structured_evaluation(store, prepared.artifact_id, PRODUCER)
    assert replay == result
    assert result.kind == "offline_evaluation"
    assert {parent.role for parent in result.parents} == {"evaluation_input", "model", "source"}
    assert not any(parent.role == "training_input" for parent in prepared.parents)
    report = decode_json(store.bytes(result.payload("report"), maximum=128 * 1024 * 1024))
    assert report["summary"] == evaluate_runs(exported[2], source.dataset.runs)
    assert report["known_label_denominator"] == 2
    assert report["source_producer"] != report["model_producer"]
    assert report["raw_source_ids"] == list(source.source_ids)
    assert report["optimizer_updates"] == 0
    assert report["claims"]["independent_generalization"] is False
    assert report["replay"] == "partition_start_fixed_weights_zero_memory_per_run"
    assert len(report["rows"]) == 2
    assert sum(row["label_index"] is not None for row in report["rows"]) == 2
    assert all(len(row["scores"]) == row["candidate_count"] for row in report["rows"])
    assert all(torch.equal(value, exported[2].state_dict()[key]) for key, value in before.items())


def test_train_or_wrong_partition_rejected_before_publication(tmp_path, exported):
    store, source, model = setup(tmp_path, exported, "train")
    before = store.manifest_ids()
    with pytest.raises(BoundaryError, match="single_partition"):
        prepare_structured_evaluation(store, StructuredEvaluationRequest(
            source.manifest.artifact_id, model.artifact_id, OPERATION, "test"), PRODUCER)
    assert store.manifest_ids() == before
    with pytest.raises(BoundaryError, match="unsupported_intent"):
        StructuredEvaluationRequest(source.manifest.artifact_id, model.artifact_id,
                                    OPERATION, "train")


def test_rehashed_prepared_input_cannot_change_weights_or_source(tmp_path, exported):
    store, source, model = setup(tmp_path, exported)
    prepared = prepare_structured_evaluation(store, StructuredEvaluationRequest(
        source.manifest.artifact_id, model.artifact_id, OPERATION, "dev"), PRODUCER)
    value = copy.deepcopy(prepared.parameters.value())
    value["weights_sha256"] = "0" * 64
    forged = Manifest("analysis", PRODUCER, prepared.parents, parameters=FrozenObject.of(value))
    store.publish(forged)
    with pytest.raises(BoundaryError, match="evaluation_input_identity_drift"):
        run_structured_evaluation(store, forged.artifact_id, PRODUCER)


@pytest.mark.parametrize("which", ["model", "source"])
def test_source_reprojection_and_model_weights_tamper_fail(tmp_path, exported, which):
    store, source, model = setup(tmp_path, exported)
    prepared = prepare_structured_evaluation(store, StructuredEvaluationRequest(
        source.manifest.artifact_id, model.artifact_id, OPERATION, "dev"), PRODUCER)
    payload = (model.payload("weights") if which == "model" else
               store.get_manifest(source.source_ids[0]).payload("archive"))
    # Locate a genuine chunk of this immutable weights payload.
    index = decode_json(store.blobs.get(f"payload-indexes/v1/{payload.sha256}.json"))
    key = "objects/sha256/" + index["chunks"][0]["sha256"]
    (store.blobs.root / key).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        run_structured_evaluation(store, prepared.artifact_id, PRODUCER)


def test_application_fence_revocation_prevents_domain_publication(tmp_path, exported):
    store, source, model = setup(tmp_path, exported)
    request = StructuredEvaluationRequest(source.manifest.artifact_id, model.artifact_id,
                                          OPERATION, "dev")

    class Fence:
        revoked = False

        def assert_current(self):
            if self.revoked:
                raise BoundaryError("app_attempt", "revoked")

    fence = Fence()
    prepared = prepare_structured_evaluation(store, request, PRODUCER, authority=fence)
    before = store.manifest_ids()
    fence.revoked = True
    with pytest.raises(BoundaryError, match="revoked"):
        run_structured_evaluation(store, prepared.artifact_id, PRODUCER, authority=fence)
    assert store.manifest_ids() == before


def test_unlabelled_prefix_and_score_only_rows_preserve_known_denominator(exported):
    from spireagent.json_boundary import json_bytes
    from stpd.fullrun.structured_sequences import parse_structured_dataset
    from stpd.workers.structured_evaluation import _rows

    value = fixtures.source([fixtures.sample(20), fixtures.sample(21),
                             fixtures.sample(22, hp=9)], split="dev")
    value["runs"][0]["steps"][0]["chosen_action_id"] = None
    dataset = parse_structured_dataset(json_bytes(value))
    rows = _rows(exported[2], dataset, None)
    metrics = evaluate_runs(exported[2], dataset.runs)
    assert rows[0]["label_index"] is None and rows[0]["loss"] is None
    assert [row["advance"] for row in rows] == [True, False, True]
    assert metrics["observations"] == 3 and metrics["labels"] == 2
    assert metrics["score_only_labels"] == 1
    assert metrics["mean_loss"] == sum(row["loss"] for row in rows[1:]) / 2


def test_model_loading_does_not_read_unavailable_private_training_payloads(tmp_path, exported):
    store, source, model = setup(tmp_path, exported)
    private = Manifest("training_input", MODEL_PRODUCER, payloads=(
        store.put_payload("private-training", io.BytesIO(b"private")),))
    store.publish(private)
    detached = Manifest("model", model.producer, (Parent("training_input", private.artifact_id),),
                        model.payloads, model.parameters)
    store.publish(detached)
    # A model-only authorized download can preserve ancestry IDs without raw private bytes.
    (store.blobs.root / f"manifests/{private.artifact_id}.json").unlink()
    request = StructuredEvaluationRequest(source.manifest.artifact_id, detached.artifact_id,
                                          OPERATION, "dev")
    prepared = prepare_structured_evaluation(store, request, PRODUCER)
    result = run_structured_evaluation(store, prepared.artifact_id, PRODUCER)
    assert result.parent("model") == detached.artifact_id
