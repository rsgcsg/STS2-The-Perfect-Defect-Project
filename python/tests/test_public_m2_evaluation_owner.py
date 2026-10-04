"""Synthetic owner-boundary regressions for fixed-stage public M2 evaluation."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest
import torch
from test_public_m2_run import _fixture, _prepare

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from stpd.workers import public_m2_evaluation_owner as owner
from stpd.workers.public_m2_run import execute_public_m2_run


@pytest.fixture(autouse=True)
def one_torch_thread():
    old = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def test_caller_input_without_fresh_exact_full_dev_projection_is_rejected():
    value = _fixture_for_input()
    transitions, sample_hashes = owner._ids_and_hashes(value)
    membership = hashlib.sha256(json_bytes({
        "transition_ids": list(transitions),
        "public_sample_sha256": list(sample_hashes),
    })).hexdigest()
    forged = owner.FullDevProjection(value, transitions, sample_hashes, membership)
    with pytest.raises(BoundaryError, match="full_dev_membership_mismatch"):
        owner._admit_full_dev(
            forged, value, expected_dev_decisions=4217,
            expected_membership_sha256=membership,
        )
    with pytest.raises(BoundaryError, match="typed_full_dev_projection_required"):
        owner._admit_full_dev(
            True, value, expected_dev_decisions=2,
            expected_membership_sha256=membership,
        )  # type: ignore[arg-type]


def test_epoch_one_stage_is_admitted_while_parent_run_is_paused(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, run_producer = bundle[:3]
    run = _prepare(bundle)
    paused = execute_public_m2_run(
        store, reporter, run.artifact_id, run_producer, stop_after_windows=2,
    )
    assert paused.state == "paused"
    assert reporter.completed(run.artifact_id) is None
    stages = [store.get_manifest(identity) for identity in store.manifest_ids()
              if store.get_manifest(identity).kind == "analysis"
              and store.get_manifest(identity).parameters.value().get("schema")
              == "stpd/public-m2-epoch-stage-v1"]
    assert [item.parameters.value()["epoch"] for item in stages] == [1]
    closure = owner._load_stage(store, stages[0].artifact_id, run_producer)
    assert closure[-1]["epoch"] == 1


def test_owner_denial_precedes_training_input_or_weight_payload_reads(tmp_path, monkeypatch):
    bundle = _fixture(tmp_path)
    store, reporter, run_producer = bundle[:3]
    run = _prepare(bundle)
    execute_public_m2_run(
        store, reporter, run.artifact_id, run_producer, stop_after_windows=2,
    )
    stage = next(store.get_manifest(identity) for identity in store.manifest_ids()
                 if store.get_manifest(identity).kind == "analysis"
                 and store.get_manifest(identity).parameters.value().get("schema")
                 == "stpd/public-m2-epoch-stage-v1")
    reads = []

    def deny_payload_read(_payload):
        reads.append(True)
        raise AssertionError("payload read occurred before owner denial")

    monkeypatch.setattr(store, "read_payload", deny_payload_read)

    class DeniedOwner:
        def require_training_datasets(self, *_args):
            raise BoundaryError("curation", "training_not_admitted")

        def _allocation_dev_use(self, *_args, **_kwargs):
            raise AssertionError("allocation check must not follow denied training admission")

    with pytest.raises(BoundaryError, match="training_not_admitted"):
        owner.prepare_stage_evaluation(
            store, stage_id=stage.artifact_id, run_producer=run_producer,
            evaluation_producer=type(run_producer)(
                "synthetic/evaluator", "c" * 40, "d" * 64),
            operation_id="e" * 32, full_dev_input=bundle[5],
            expected_dev_decisions=2, expected_membership_sha256="a" * 64,
            projector=object(), evaluator=object(), owner=DeniedOwner(),
        )
    assert reads == []


def test_full_dev_owner_reservation_occurs_only_after_admission_and_validation(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, run_producer, source_view, allocation, source, _ = bundle
    run = _prepare(bundle)
    paused = execute_public_m2_run(
        store, reporter, run.artifact_id, run_producer, stop_after_windows=2,
    )
    assert paused.state == "paused"
    stage = next(store.get_manifest(identity) for identity in store.manifest_ids()
                 if store.get_manifest(identity).kind == "analysis"
                 and store.get_manifest(identity).parameters.value().get("schema")
                 == "stpd/public-m2-epoch-stage-v1")
    full_input = source
    transitions, sample_hashes = owner._ids_and_hashes(full_input)
    membership = hashlib.sha256(json_bytes({
        "transition_ids": list(transitions),
        "public_sample_sha256": list(sample_hashes),
    })).hexdigest()

    class Projector:
        def reproject_full_dev(self, _store, *, source_view_id, allocation_id):
            assert source_view_id == source_view.artifact_id
            assert allocation_id == allocation.artifact_id
            return owner.FullDevProjection(
                full_input, transitions, sample_hashes, membership,
            )

    class Evaluator:
        def build_selection(self, _input, *, training_input_digest):
            return SimpleNamespace(
                source_input_identity=full_input.identity,
                training_input_digest=training_input_digest,
                tokenizer_sha256=hashlib.sha256(source.state_tokenizer).hexdigest(),
                ordered_transition_ids=transitions,
                ordered_membership_digest=membership,
                row_count=len(transitions),
                chains=tuple(SimpleNamespace(chain_id=chain.chain_id)
                             for chain in full_input.chains if chain.split == "dev"),
                identity=hashlib.sha256(full_input.payload_bytes()).hexdigest(),
            )

    class CurationOwner:
        def __init__(self):
            self.calls = []

        def require_training_datasets(self, *_args):
            self.calls.append("require_training_datasets")
            return {}

        def _allocation_dev_use(self, _store, *, record_use, **_kwargs):
            self.calls.append("reserve" if record_use else "allocation_preflight")
            return {}

    curation = CurationOwner()
    before = store.manifest_ids()
    prepared = owner.prepare_stage_evaluation(
            store, stage_id=stage.artifact_id, run_producer=run_producer,
            evaluation_producer=type(run_producer)(
                "synthetic/evaluator", "c" * 40, "d" * 64),
                operation_id="e" * 32, full_dev_input=full_input,
            expected_dev_decisions=2, expected_membership_sha256=membership,
            projector=Projector(), evaluator=Evaluator(), owner=curation,
    )
    assert curation.calls == [
        "require_training_datasets", "allocation_preflight", "reserve",
    ]
    assert prepared.expected_dev_decisions == 2
    assert prepared.membership_sha256 == membership
    assert store.get_manifest(prepared.evaluation_input.artifact_id).kind == "analysis"
    assert set(store.manifest_ids()) == set(before) | {prepared.evaluation_input.artifact_id}
    prepared_ids = store.manifest_ids()
    with pytest.raises(BoundaryError, match="prepared_stage_changed"):
        owner.accept_stage_evaluation(
            store, replace(prepared, weights_digest="f" * 64), _summary(prepared),
            projector=Projector(),
            evaluator=Evaluator(), owner=curation,
        )
    fake_input = replace(
        prepared.evaluation_input,
        parameters=FrozenObject.of({
            **prepared.evaluation_input.parameters.value(), "operation_id": "f" * 32,
        }),
    )
    with pytest.raises(BoundaryError, match="prepared_evaluation_input_missing"):
        owner.accept_stage_evaluation(
            store, replace(prepared, evaluation_input=fake_input), _summary(prepared),
            projector=Projector(), evaluator=Evaluator(), owner=curation,
        )
    assert curation.calls[-4:] == [
        "require_training_datasets", "allocation_preflight",
        "require_training_datasets", "allocation_preflight",
    ]
    assert store.manifest_ids() == prepared_ids
    accepted = owner.accept_stage_evaluation(
        store, prepared, _summary(prepared), projector=Projector(),
        evaluator=Evaluator(), owner=curation,
    )
    assert accepted.offline_evaluation.kind == "offline_evaluation"
    after_accept = store.manifest_ids()
    changed_summary = _summary(prepared, cross_entropy_sum=4.0)
    with pytest.raises(BoundaryError, match="operation_id_collision"):
        owner.accept_stage_evaluation(
            store, prepared, changed_summary, projector=Projector(),
            evaluator=Evaluator(), owner=curation,
        )
    assert store.manifest_ids() == after_accept


def _summary(prepared, *, cross_entropy_sum=2.0):
    data = {
        "selection_identity": prepared.selection.identity,
        "source_input_identity": prepared.full_dev_input.identity,
        "training_input_digest": prepared.training_engine_input_digest,
        "weights_sha256": prepared.weights_sha256,
        "weights_digest": prepared.weights_digest,
        "completed_epochs": prepared.completed_epochs,
        "run_complete": prepared.completed_epochs == prepared.config.epochs,
        "implementation_sha256": prepared.implementation_sha256,
        "config_digest": prepared.config_digest,
        "runtime": {"torch": "synthetic"},
        "chain_count": len(prepared.selection.chains),
        "label_count": prepared.expected_dev_decisions,
        "cross_entropy_sum": cross_entropy_sum,
        "loss_mean": cross_entropy_sum / prepared.expected_dev_decisions,
        "correct_count": 1,
        "top1_accuracy": 1 / prepared.expected_dev_decisions,
        "ordered_chain_ids": tuple(chain.chain_id for chain in prepared.selection.chains),
        "ordered_transition_ids": prepared.transition_ids,
        "ordered_membership_digest": prepared.selection.ordered_membership_digest,
        "inference_device": "cpu",
    }
    return SimpleNamespace(**data, to_dict=lambda: data)


def _fixture_for_input():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as root:
        return _fixture(Path(root))[5]


def test_crosswired_epoch_model_is_rejected_before_any_evaluation_write(tmp_path):
    bundle = _fixture(tmp_path)
    store, reporter, run_producer = bundle[:3]
    run = _prepare(bundle)
    execute_public_m2_run(store, reporter, run.artifact_id, run_producer)
    stages = [store.get_manifest(identity) for identity in store.manifest_ids()
              if store.get_manifest(identity).kind == "analysis"
              and store.get_manifest(identity).parameters.value().get("schema")
              == "stpd/public-m2-epoch-stage-v1"]
    first = next(item for item in stages if item.parameters.value()["epoch"] == 1)
    third = next(item for item in stages if item.parameters.value()["epoch"] == 3)
    crosswired = Manifest(
        "analysis", run_producer,
        tuple(Parent(parent.role,
                     third.parent(parent.role) if parent.role == "model"
                     else parent.artifact_id)
              for parent in first.parents),
        first.payloads, FrozenObject.of(first.parameters.value()),
    )
    store.publish(crosswired)
    before = store.manifest_ids()
    with pytest.raises(BoundaryError, match="stage_parent_closure_mismatch"):
        owner._load_stage(store, crosswired.artifact_id, run_producer)
    assert store.manifest_ids() == before
