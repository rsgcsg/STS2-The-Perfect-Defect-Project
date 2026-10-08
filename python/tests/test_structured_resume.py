"""Synthetic CPU boundary recovery and trusted workload fence contract tests."""

from __future__ import annotations

from dataclasses import replace

import pytest
import torch
from test_structured_s0 import PRODUCER, sample, source

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore
from stpd.fullrun.structured_sequences import parse_structured_dataset
from stpd.models.structured_engine import StructuredTrainingEngine
from stpd.models.structured_training import StructuredTrainingConfig, train_structured_model
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint
from stpd.workers.structured_control import StructuredWorkloadRequest
from stpd.workers.structured_execution import (
    execute_structured_workload,
    prepare_structured_workload,
)


@pytest.fixture(autouse=True)
def two_threads():
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    yield
    torch.set_num_threads(previous)


def dataset(*, unlabelled=False):
    value = source(
        [
            sample(20),
            sample(21),
            sample(22, hp=11),
            sample(23, hp=10),
            sample(24, hp=9),
            sample(25, hp=9),
            sample(26, hp=8),
        ]
    )
    if unlabelled:
        for step in value["runs"][0]["steps"][:5]:
            step["chosen_action_id"] = None
    return parse_structured_dataset(json_bytes(value))


def equal_tree(left, right):
    if isinstance(left, torch.Tensor):
        assert isinstance(right, torch.Tensor) and torch.equal(left, right)
    elif isinstance(left, dict):
        assert set(left) == set(right)
        for key in left:
            equal_tree(left[key], right[key])
    elif isinstance(left, (list, tuple)):
        assert type(left) is type(right) and len(left) == len(right)
        for a, b in zip(left, right, strict=True):
            equal_tree(a, b)
    else:
        assert left == right


def finish(engine):
    while not engine.training_complete:
        engine.advance_chunk()
    return engine.evaluate()


@pytest.mark.parametrize("pause_boundary", [0, 1, 2, 3])
def test_uninterrupted_and_restored_states_are_exact(pause_boundary):
    data = dataset()
    config = StructuredTrainingConfig(epochs=2)
    original = StructuredTrainingEngine(data, config)
    for _ in range(pause_boundary):
        original.advance_chunk()
    checkpoint = original.checkpoint()
    resumed = StructuredTrainingEngine(data, config)
    resumed.restore(checkpoint)
    equal_tree(original.model.state_dict(), resumed.model.state_dict())
    equal_tree(original.optimizer.state_dict(), resumed.optimizer.state_dict())
    equal_tree(original.memory, resumed.memory)
    first = finish(original)
    second = finish(resumed)
    assert first == second
    equal_tree(original.model.state_dict(), resumed.model.state_dict())
    equal_tree(original.optimizer.state_dict(), resumed.optimizer.state_dict())
    assert original.cursor == resumed.cursor


def test_engine_preserves_legacy_graph_chunk_numerics():
    data = dataset()
    config = StructuredTrainingConfig(epochs=2)
    legacy = train_structured_model(data, config)
    engine = StructuredTrainingEngine(data, config)
    metrics = finish(engine)
    equal_tree(engine.model.state_dict(), legacy.model.state_dict())
    equal_tree(engine.optimizer.state_dict(), legacy.optimizer_state)
    assert metrics == legacy.metrics


def test_unlabelled_chunks_and_score_only_rows_have_correct_checkpoint_cursor():
    engine = StructuredTrainingEngine(dataset(unlabelled=True), StructuredTrainingConfig())
    progress = engine.advance_chunk()
    assert progress.optimizer_updates == 1 and progress.cursor.next_step == 6
    assert engine.unlabelled_chunks == 0  # Score-only row5 has a real N target in this chunk.


def test_cumulative_update_budget_does_not_restart_on_resume():
    data = dataset()
    config = StructuredTrainingConfig(epochs=3, max_updates=2)
    engine = StructuredTrainingEngine(data, config)
    engine.advance_chunk()
    restored = StructuredTrainingEngine(data, config)
    restored.restore(engine.checkpoint())
    restored.advance_chunk()
    assert restored.training_complete and restored.updates == 2
    with pytest.raises(BoundaryError, match="exhausted"):
        restored.advance_chunk()


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda value: value["identity"].update(code_sha256="0" * 64), "identity_mismatch"),
        (lambda value: value["cursor"].update(next_step=3), "cursor_plan_mismatch"),
        (lambda value: value["counters"].update(updates=9), "counter_mismatch"),
        (lambda value: value.update(memory_run=100), "memory_cursor"),
        (
            lambda value: value["optimizer"]["param_groups"][0].update(lr=0.1),
            "configuration_mismatch",
        ),
        (lambda value: value.update(phase="publication"), "phase_plan_mismatch"),
    ],
)
def test_checkpoint_rejects_identity_cursor_optimizer_and_phase_tampering(change, code):
    data = dataset()
    engine = StructuredTrainingEngine(data, StructuredTrainingConfig())
    engine.advance_chunk()
    altered = decode_checkpoint(engine.checkpoint())
    change(altered)
    target = StructuredTrainingEngine(data, StructuredTrainingConfig())
    prior = {name: value.clone() for name, value in target.model.state_dict().items()}
    with pytest.raises(BoundaryError, match=code):
        target.restore(encode_checkpoint(altered))
    equal_tree(target.model.state_dict(), prior)


class Authority:
    def __init__(self):
        self.active = True
        self.resume_checks = []

    def assert_current(self, run_id, operation_id, attempt_id):
        if not self.active:
            raise BoundaryError("test_authority", "stale_attempt")

    def authorize_resume(self, run_id, attempt_id, checkpoint_id):
        self.resume_checks.append((run_id, attempt_id, checkpoint_id))


class PauseControl:
    def __init__(self, after=1, *, cancel=False):
        self.calls = 0
        self.after = after
        self.cancel = cancel

    def requested_action(self):
        self.calls += 1
        return ("cancel" if self.cancel else "pause") if self.calls > self.after else "continue"


def setup(tmp_path, *, config=None):
    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    reporter = ObjectStoreRunReporter(store, store.blobs)
    run = prepare_structured_workload(
        store,
        dataset(),
        PRODUCER,
        config or StructuredTrainingConfig(epochs=2),
        operation_id="1" * 32,
    )
    request = StructuredWorkloadRequest(
        run.artifact_id, run.parent("training_input"), "1" * 32, "2" * 32
    )
    return store, reporter, run, request


def test_explicit_pause_resume_complete_and_idempotent_reconcile(tmp_path):
    store, reporter, run, request = setup(tmp_path)
    authority = Authority()
    paused = execute_structured_workload(
        store, reporter, request, PRODUCER, authority=authority, control=PauseControl()
    )
    assert paused.state == "paused" and paused.optimizer_updates == 1
    checkpoint = store.get_manifest(paused.checkpoint_id)
    assert checkpoint.parent("run") == run.artifact_id
    resumed_request = replace(
        request, attempt_id="3" * 32, mode="resume", resume_checkpoint_id=paused.checkpoint_id
    )
    completed = execute_structured_workload(
        store, reporter, resumed_request, PRODUCER, authority=authority
    )
    assert completed.state == "completed" and completed.optimizer_updates == 4
    assert authority.resume_checks == [(run.artifact_id, "3" * 32, paused.checkpoint_id)]
    replay = execute_structured_workload(
        store, reporter, replace(request, mode="reconcile"), PRODUCER, authority=authority
    )
    assert replay.result_id == completed.result_id and replay.optimizer_updates == 4
    events = reporter.events(run.artifact_id)
    assert [event.parameters.value()["step"] for event in events] == list(range(1, len(events) + 1))


def test_stale_writer_cannot_publish_even_failure_event(tmp_path):
    store, reporter, run, request = setup(tmp_path)
    authority = Authority()
    authority.active = False
    prior = store.manifest_ids()
    with pytest.raises(BoundaryError, match="stale_attempt"):
        execute_structured_workload(store, reporter, request, PRODUCER, authority=authority)
    assert store.manifest_ids() == prior and not reporter.events(run.artifact_id)


def test_cancel_has_checkpoint_but_no_model_or_completion(tmp_path):
    store, reporter, run, request = setup(tmp_path)
    result = execute_structured_workload(
        store, reporter, request, PRODUCER, authority=Authority(), control=PauseControl(cancel=True)
    )
    assert result.state == "cancelled" and result.checkpoint_id
    assert result.model_id is None and reporter.completed(run.artifact_id) is None
    with pytest.raises(BoundaryError, match="explicit_resume_required"):
        execute_structured_workload(store, reporter, request, PRODUCER, authority=Authority())


def test_legacy_v1_run_cannot_be_implicitly_resumed(tmp_path):
    from stpd.workers.structured_run import prepare_structured_run

    store = ManifestArtifactStore(LocalBlobStore(tmp_path / "store"))
    run = prepare_structured_run(store, dataset(), PRODUCER, StructuredTrainingConfig())
    reporter = ObjectStoreRunReporter(store, store.blobs)
    request = StructuredWorkloadRequest(
        run.artifact_id, run.parent("training_input"), "1" * 32, "2" * 32
    )
    with pytest.raises(BoundaryError, match="v1_not_resumable"):
        execute_structured_workload(store, reporter, request, PRODUCER, authority=Authority())


def test_request_closed_fields_and_explicit_checkpoint():
    value = StructuredWorkloadRequest("a" * 64, "b" * 64, "1" * 32, "2" * 32).to_dict()
    assert StructuredWorkloadRequest.from_dict(value).to_dict() == value
    value["mode"] = "resume"
    with pytest.raises(BoundaryError):
        StructuredWorkloadRequest.from_dict(value)
    value["mode"] = "start"
    value["auto_latest"] = True
    with pytest.raises(BoundaryError, match="unknown_fields"):
        StructuredWorkloadRequest.from_dict(value)


def test_abrupt_exit_never_selects_latest_or_restarts(tmp_path, monkeypatch):
    store, reporter, run, request = setup(tmp_path)
    original = StructuredTrainingEngine.advance_chunk

    def interrupted(engine):
        original(engine)
        raise KeyboardInterrupt("simulated process loss before durable progress")

    monkeypatch.setattr(StructuredTrainingEngine, "advance_chunk", interrupted)
    with pytest.raises(KeyboardInterrupt):
        execute_structured_workload(store, reporter, request, PRODUCER, authority=Authority())
    checkpoints = [
        event.parameters.value()["details"]["checkpoint_id"]
        for event in reporter.events(run.artifact_id)
        if event.parameters.value()["kind"] == "checkpoint"
    ]
    assert len(checkpoints) == 1
    assert not any(
        event.parameters.value()["kind"] == "failed" for event in reporter.events(run.artifact_id)
    )
    with pytest.raises(BoundaryError, match="explicit_resume_required"):
        execute_structured_workload(store, reporter, request, PRODUCER, authority=Authority())
    monkeypatch.setattr(StructuredTrainingEngine, "advance_chunk", original)
    resumed = execute_structured_workload(
        store,
        reporter,
        replace(request, mode="resume", attempt_id="3" * 32, resume_checkpoint_id=checkpoints[0]),
        PRODUCER,
        authority=Authority(),
    )
    assert resumed.optimizer_updates == 4


def test_fixed_weight_evaluation_replay_never_repeats_optimizer_work(tmp_path, monkeypatch):
    store, reporter, run, request = setup(tmp_path)
    original = StructuredTrainingEngine.evaluate

    def interrupted(engine):
        assert engine.training_complete and engine.updates == 4
        raise KeyboardInterrupt("simulated evaluation interruption")

    monkeypatch.setattr(StructuredTrainingEngine, "evaluate", interrupted)
    with pytest.raises(KeyboardInterrupt):
        execute_structured_workload(store, reporter, request, PRODUCER, authority=Authority())
    checkpoints = [
        event.parameters.value()["details"]["checkpoint_id"]
        for event in reporter.events(run.artifact_id)
        if event.parameters.value()["kind"] == "checkpoint"
    ]
    checkpoint = checkpoints[-1]
    assert store.get_manifest(checkpoint).parameters.value()["phase"] == "evaluation"
    monkeypatch.setattr(StructuredTrainingEngine, "evaluate", original)

    def forbidden(_engine):
        pytest.fail("resume of training-complete checkpoint attempted optimizer work")

    monkeypatch.setattr(StructuredTrainingEngine, "advance_chunk", forbidden)
    completed = execute_structured_workload(
        store,
        reporter,
        replace(request, mode="resume", attempt_id="3" * 32, resume_checkpoint_id=checkpoint),
        PRODUCER,
        authority=Authority(),
    )
    assert completed.optimizer_updates == 4
    assert any(
        event.parameters.value()["details"].get("replay") is True
        for event in reporter.events(run.artifact_id)
        if event.parameters.value()["kind"] == "evaluation"
    )


def test_resume_authority_denial_publishes_nothing(tmp_path):
    store, reporter, run, request = setup(tmp_path)
    paused = execute_structured_workload(
        store, reporter, request, PRODUCER, authority=Authority(), control=PauseControl()
    )

    class Denied(Authority):
        def authorize_resume(self, run_id, attempt_id, checkpoint_id):
            raise BoundaryError("app_authority", "prior_writer_not_terminal")

    before = store.manifest_ids()
    with pytest.raises(BoundaryError, match="prior_writer_not_terminal"):
        execute_structured_workload(
            store,
            reporter,
            replace(
                request,
                mode="resume",
                attempt_id="3" * 32,
                resume_checkpoint_id=paused.checkpoint_id,
            ),
            PRODUCER,
            authority=Denied(),
        )
    assert store.manifest_ids() == before


def test_pure_capability_contract_does_not_import_tensor_runtime():
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import stpd.structured_workload_contracts; "
            'assert "torch" not in sys.modules',
        ],
        check=True,
    )


def test_checkpoint_restores_all_rng_and_clears_autograd_state():
    import random

    import numpy as np

    data = dataset()
    engine = StructuredTrainingEngine(data, StructuredTrainingConfig())
    engine.advance_chunk()
    random.seed(73)
    np.random.seed(74)
    torch.manual_seed(75)
    checkpoint = engine.checkpoint()
    expected = (random.random(), np.random.random(), torch.rand(4))
    restored = StructuredTrainingEngine(data, StructuredTrainingConfig())
    restored.restore(checkpoint)
    assert random.random() == expected[0]
    assert np.random.random() == expected[1]
    assert torch.equal(torch.rand(4), expected[2])
    assert restored.memory.grad_fn is None
    assert all(parameter.grad is None for parameter in restored.model.parameters())
    next(engine.model.parameters()).grad = torch.ones_like(next(engine.model.parameters()))
    with pytest.raises(BoundaryError, match="unsafe_boundary"):
        engine.checkpoint()


def test_chunk_failure_retains_last_checkpoint_and_requires_explicit_resume(tmp_path, monkeypatch):
    store, reporter, run, request = setup(tmp_path)
    original = StructuredTrainingEngine.advance_chunk

    def fail(engine):
        original(engine)
        raise RuntimeError("injected failure after computation")

    monkeypatch.setattr(StructuredTrainingEngine, "advance_chunk", fail)
    with pytest.raises(RuntimeError, match="injected failure"):
        execute_structured_workload(store, reporter, request, PRODUCER, authority=Authority())
    failed = reporter.events(run.artifact_id)[-1].parameters.value()
    assert failed["kind"] == "failed"
    checkpoint = failed["details"]["checkpoint_id"]
    assert store.get_manifest(checkpoint).parameters.value()["optimizer_updates"] == 0
    assert reporter.completed(run.artifact_id) is None
    with pytest.raises(BoundaryError, match="explicit_resume_required"):
        execute_structured_workload(store, reporter, request, PRODUCER, authority=Authority())
    monkeypatch.setattr(StructuredTrainingEngine, "advance_chunk", original)
    complete = execute_structured_workload(
        store,
        reporter,
        replace(request, mode="resume", attempt_id="3" * 32, resume_checkpoint_id=checkpoint),
        PRODUCER,
        authority=Authority(),
    )
    assert complete.optimizer_updates == 4


def test_revocation_at_boundary_prevents_next_checkpoint_and_failure_annotation(tmp_path):
    store, reporter, run, request = setup(tmp_path)
    authority = Authority()

    class Revoke:
        def requested_action(self):
            authority.active = False
            return "pause"

    with pytest.raises(BoundaryError, match="stale_attempt"):
        execute_structured_workload(
            store, reporter, request, PRODUCER, authority=authority, control=Revoke()
        )
    assert [event.parameters.value()["kind"] for event in reporter.events(run.artifact_id)] == [
        "started",
        "checkpoint",
    ]
    assert reporter.completed(run.artifact_id) is None


def test_partial_event_publication_never_creates_duplicate_failure_step(tmp_path, monkeypatch):
    store, reporter, run, request = setup(tmp_path)
    original = reporter.emit

    def emit_then_fail(event):
        result = original(event)
        if event.parameters.value()["kind"] == "progress":
            raise OSError("lost response after durable publication")
        return result

    monkeypatch.setattr(reporter, "emit", emit_then_fail)
    with pytest.raises(OSError):
        execute_structured_workload(store, reporter, request, PRODUCER, authority=Authority())
    history = reporter.events(run.artifact_id)
    assert [event.parameters.value()["step"] for event in history] == [1, 2, 3]
    assert history[-1].parameters.value()["kind"] == "progress"
    checkpoint = history[1].parameters.value()["details"]["checkpoint_id"]
    monkeypatch.setattr(reporter, "emit", original)
    result = execute_structured_workload(
        store,
        reporter,
        replace(request, mode="resume", attempt_id="3" * 32, resume_checkpoint_id=checkpoint),
        PRODUCER,
        authority=Authority(),
    )
    assert result.state == "completed" and result.optimizer_updates == 4


def test_uncertain_completion_reconciles_without_false_failure_or_retraining(tmp_path, monkeypatch):
    store, reporter, run, request = setup(tmp_path)
    original = reporter.complete

    def complete_then_fail(result):
        original(result)
        raise OSError("completion response lost")

    monkeypatch.setattr(reporter, "complete", complete_then_fail)
    with pytest.raises(OSError):
        execute_structured_workload(store, reporter, request, PRODUCER, authority=Authority())
    assert reporter.completed(run.artifact_id) is not None
    assert not any(
        event.parameters.value()["kind"] == "failed" for event in reporter.events(run.artifact_id)
    )
    result = execute_structured_workload(
        store, reporter, replace(request, mode="reconcile"), PRODUCER, authority=Authority()
    )
    assert result.state == "completed" and result.optimizer_updates == 4


def test_restore_rejects_missing_entire_adamw_entry_without_mutating_model():
    data = dataset()
    engine = StructuredTrainingEngine(data, StructuredTrainingConfig())
    engine.advance_chunk()
    altered = decode_checkpoint(engine.checkpoint())
    assert len(altered["optimizer"]["state"]) > 1
    removed = next(iter(altered["optimizer"]["state"]))
    assert altered["optimizer_participation"][removed] == 1
    del altered["optimizer"]["state"][removed]
    target = StructuredTrainingEngine(data, StructuredTrainingConfig())
    before = {name: tensor.clone() for name, tensor in target.model.state_dict().items()}
    with pytest.raises(BoundaryError, match="optimizer_state_inventory_mismatch"):
        target.restore(encode_checkpoint(altered))
    equal_tree(target.model.state_dict(), before)
    assert target.optimizer.state_dict()["state"] == {}


def test_unused_parameters_keep_zero_participation_and_resume_exactly():
    data = dataset()
    engine = StructuredTrainingEngine(data, StructuredTrainingConfig())
    engine.advance_chunk()
    checkpoint = decode_checkpoint(engine.checkpoint())
    unused = {
        index for index, count in enumerate(checkpoint["optimizer_participation"]) if count == 0
    }
    assert unused  # This synthetic frame does not activate every typed relation path.
    assert unused.isdisjoint(checkpoint["optimizer"]["state"])
    for index, state in checkpoint["optimizer"]["state"].items():
        assert float(state["step"]) == checkpoint["optimizer_participation"][index]
    resumed = StructuredTrainingEngine(data, StructuredTrainingConfig())
    resumed.restore(encode_checkpoint(checkpoint))
    assert resumed.optimizer_participation == engine.optimizer_participation
    assert finish(resumed) == finish(engine)
    equal_tree(resumed.model.state_dict(), engine.model.state_dict())
    equal_tree(resumed.optimizer.state_dict(), engine.optimizer.state_dict())
    assert resumed.optimizer_participation == engine.optimizer_participation


def test_adamw_step_is_bound_to_its_actual_parameter_participation():
    data = dataset()
    engine = StructuredTrainingEngine(data, StructuredTrainingConfig())
    engine.advance_chunk()
    checkpoint = decode_checkpoint(engine.checkpoint())
    used = next(iter(checkpoint["optimizer"]["state"]))
    checkpoint["optimizer"]["state"][used]["step"] = torch.tensor(0.0)
    with pytest.raises(BoundaryError, match="optimizer_step_mismatch"):
        StructuredTrainingEngine(data, StructuredTrainingConfig()).restore(
            encode_checkpoint(checkpoint)
        )


@pytest.mark.parametrize("inventory", ["absent", "wrong_length", "non_integer", "too_many"])
def test_invalid_optimizer_participation_inventory_is_rejected(inventory):
    data = dataset()
    engine = StructuredTrainingEngine(data, StructuredTrainingConfig())
    engine.advance_chunk()
    checkpoint = decode_checkpoint(engine.checkpoint())
    if inventory == "absent":
        del checkpoint["optimizer_participation"]
    elif inventory == "wrong_length":
        checkpoint["optimizer_participation"].pop()
    elif inventory == "non_integer":
        checkpoint["optimizer_participation"][0] = True
    else:
        checkpoint["optimizer_participation"][0] = 2
    with pytest.raises(BoundaryError):
        StructuredTrainingEngine(data, StructuredTrainingConfig()).restore(
            encode_checkpoint(checkpoint)
        )


def alternate_completion(tmp_path, store, result):
    """Publish a complete immutable candidate through the real reporter into fresh slots."""
    reporter = ObjectStoreRunReporter(store, LocalBlobStore(tmp_path / "other-report-slots"))
    reporter.complete(result)
    return reporter


def test_completed_reconcile_rejects_model_training_input_parent_of_wrong_kind(tmp_path):
    from spireagent.artifact_contracts import Parent

    store, reporter, run, request = setup(tmp_path)
    completed = execute_structured_workload(
        store, reporter, request, PRODUCER, authority=Authority()
    )
    model = store.get_manifest(completed.model_id)
    wrong = replace(
        model,
        parents=tuple(
            Parent(parent.role, run.parent("experiment"))
            if parent.role == "training_input"
            else parent
            for parent in model.parents
        ),
    )
    store.publish(wrong)
    result = store.get_manifest(completed.result_id)
    forged = replace(
        result,
        parents=tuple(
            Parent(parent.role, wrong.artifact_id) if parent.role == "model" else parent
            for parent in result.parents
        ),
    )
    alternate = alternate_completion(tmp_path, store, forged)
    with pytest.raises(BoundaryError, match="completed_model_identity_mismatch"):
        execute_structured_workload(
            store, alternate, replace(request, mode="reconcile"), PRODUCER, authority=Authority()
        )


def corrupt_payload(store, payload):
    from spireagent.json_boundary import decode_json

    index = decode_json(store.blobs.get(f"payload-indexes/v1/{payload.sha256}.json"))
    chunk = store.blobs.root / "objects" / "sha256" / index["chunks"][0]["sha256"]
    raw = chunk.read_bytes()
    chunk.write_bytes(b"X" + raw[1:])


def test_completed_reconcile_reads_and_verifies_actual_report_blob(tmp_path):
    store, reporter, run, request = setup(tmp_path)
    completed = execute_structured_workload(
        store, reporter, request, PRODUCER, authority=Authority()
    )
    result = store.get_manifest(completed.result_id)
    corrupt_payload(store, result.payload("report"))
    with pytest.raises(BoundaryError, match="payload_chunk_integrity_failure"):
        execute_structured_workload(
            store, reporter, replace(request, mode="reconcile"), PRODUCER, authority=Authority()
        )


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "training_input_id",
        "checkpoint_id",
        "model_artifact_id",
        "operation_id",
        "producer",
        "source_sha256",
        "config",
        "metrics",
        "execution_identity",
        "attempt",
        "schema",
    ],
)
def test_completed_report_content_is_bound_to_exact_result_closure(tmp_path, field):
    import io

    from spireagent.json_boundary import decode_json

    store, reporter, run, request = setup(tmp_path)
    completed = execute_structured_workload(
        store, reporter, request, PRODUCER, authority=Authority()
    )
    result = store.get_manifest(completed.result_id)
    report = decode_json(b"".join(store.read_payload(result.payload("report"))))
    report[field] = {} if isinstance(report[field], dict) else "0" * len(report[field])
    changed = store.put_payload("report", io.BytesIO(json_bytes(report)), "application/json")
    forged = replace(result, payloads=(changed,))
    alternate = alternate_completion(tmp_path, store, forged)
    with pytest.raises(BoundaryError, match="completed_report_binding_mismatch"):
        execute_structured_workload(
            store, alternate, replace(request, mode="reconcile"), PRODUCER, authority=Authority()
        )


@pytest.mark.parametrize("field", ["boundary", "cursor", "phase", "optimizer_updates"])
def test_checkpoint_manifest_metadata_must_match_verified_state_payload(tmp_path, field):
    from spireagent.json_boundary import FrozenObject

    store, reporter, run, request = setup(tmp_path)
    paused = execute_structured_workload(
        store, reporter, request, PRODUCER, authority=Authority(), control=PauseControl()
    )
    checkpoint = store.get_manifest(paused.checkpoint_id)
    info = checkpoint.parameters.value()
    if field == "cursor":
        info[field]["next_step"] += 1
    elif field == "phase":
        info[field] = "publication"
    else:
        info[field] += 1
    changed = replace(checkpoint, parameters=FrozenObject.of(info))
    store.publish(changed)
    with pytest.raises(BoundaryError, match="checkpoint_metadata_payload_mismatch"):
        execute_structured_workload(
            store,
            reporter,
            replace(
                request,
                mode="resume",
                attempt_id="3" * 32,
                resume_checkpoint_id=changed.artifact_id,
            ),
            PRODUCER,
            authority=Authority(),
        )


@pytest.mark.parametrize("marker", ["run-events/", "run-completions/"])
def test_reporter_marker_write_failure_after_manifest_never_resubmits_or_invents_terminal(
    tmp_path, monkeypatch, marker
):
    store, reporter, run, request = setup(tmp_path)
    original = store.blobs.put_if_absent
    failed = False

    def failing_marker(key, data):
        nonlocal failed
        if key.startswith(marker):
            failed = True
            raise OSError("injected lower marker write failure")
        return original(key, data)

    monkeypatch.setattr(store.blobs, "put_if_absent", failing_marker)
    with pytest.raises(OSError, match="lower marker"):
        execute_structured_workload(store, reporter, request, PRODUCER, authority=Authority())
    assert failed
    assert not store.blobs.keys(marker)
    events = reporter.events(run.artifact_id)
    assert not any(event.parameters.value()["kind"] == "failed" for event in events)
    assert [event.parameters.value()["step"] for event in events] == list(range(1, len(events) + 1))
    assert reporter.completed(run.artifact_id) is None
    monkeypatch.setattr(store.blobs, "put_if_absent", original)
    before = store.manifest_ids()
    with pytest.raises(BoundaryError, match="explicit_resume_required"):
        execute_structured_workload(store, reporter, request, PRODUCER, authority=Authority())
    with pytest.raises(BoundaryError, match="no_completed_result_reconcile_required"):
        execute_structured_workload(
            store, reporter, replace(request, mode="reconcile"), PRODUCER, authority=Authority()
        )
    assert store.manifest_ids() == before
