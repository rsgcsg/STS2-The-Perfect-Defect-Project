"""Existing recipe owner accepts exact synthetic event facts without a child fit."""

import time
from copy import deepcopy
from dataclasses import asdict
from threading import RLock
from types import SimpleNamespace

import pytest

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.recipes import structured as module
from spireagent.workbench.trusted_recipes import STRUCTURED_SCOPED_RECIPE
from stpd.structured_workload_contracts import StructuredTrainingConfig

PRODUCER = Producer("https://example.test/fixture", "1" * 40, "2" * 64)


class Service:
    def __init__(self, operation):
        self.operation = operation
        self.snapshots = []
        self._lock = RLock()
        self._active_attempt = operation["attempt_id"]
        self._attempt_started_monotonic = time.monotonic()
        self._cancel_requested_monotonic = None

    def _read(self, path, owner):
        return deepcopy(self.operation)

    def _advance(self, path, identity, **changes):
        self.operation.update(changes)
        self.snapshots.append(deepcopy(self.operation))

    def _finish_attempt(self, path, owner, identity, **changes):
        self.operation.update(
            {key: value for key, value in changes.items() if key != "expected_attempt_id"}
        )
        self.operation["writer_terminal"] = True


def setup(tmp_path, monkeypatch, *, regressed=False, changed_plan=False):
    config = StructuredTrainingConfig(epochs=1, max_updates=100)
    source = Manifest(
        "dataset",
        PRODUCER,
        parameters=FrozenObject.of(
            {"schema": "stpd/structured-sequence-source-v1", "source_sha256": "f" * 64}
        ),
    )
    request = TrainingRequest(
        "3" * 32,
        STRUCTURED_SCOPED_RECIPE,
        source.artifact_id,
        {**asdict(config), "checkpoint_every_boundaries": 1},
        limits={"wall_seconds": 60, "scratch_bytes": 16 * 1024**2},
    )
    dataset = SimpleNamespace(
        source_sha256="f" * 64,
        runs=(
            SimpleNamespace(
                run_id="train",
                split="train",
                steps=tuple(
                    SimpleNamespace(
                        position=i,
                        chosen_action_id="choice" if i in {4, 5, 8} else None,
                        advance=True,
                        reset_before=i in {0, 8},
                    )
                    for i in range(9)
                ),
            ),
        ),
    )
    adapter = module.StructuredRecipeAdapter(STRUCTURED_SCOPED_RECIPE)
    prepared = module._preflight_result(dataset, None, True)
    plan = adapter.training_plan(None, None, request, prepared_dataset=prepared)
    training = Manifest("training_input", PRODUCER, (Parent("source", source.artifact_id),))
    run = Manifest(
        "run",
        PRODUCER,
        (Parent("training_input", training.artifact_id),),
        parameters=FrozenObject.of(
            {
                "schema": "stpd/structured-m2-run-v3",
                "operation_id": "4" * 32,
                "source_sha256": "f" * 64,
                "config": asdict(config),
            }
        ),
    )
    operation = {
        "operation_id": "4" * 32,
        "attempt_id": "5" * 32,
        "recipe": STRUCTURED_SCOPED_RECIPE,
        "request": request.to_dict(),
        "mode": "start",
        "dataset_id": source.artifact_id,
        "attempt_producer": PRODUCER.to_dict(),
        "status": "pending",
        "writer_terminal": False,
        "requested_action": "continue",
        "elapsed_seconds": 0.0,
        "attempts": [],
        "child_lock_name": "fixture-child.lock",
        "scratch_name": "fixture-scratch",
        "training_plan": {**plan, "source_sha256": "e" * 64} if changed_plan else plan,
    }
    service = Service(operation)
    artifacts = {m.artifact_id: m for m in (source, training, run)}
    store = SimpleNamespace(get_manifest=lambda identity: artifacts[identity])
    owner = SimpleNamespace(
        identity=("workspace", "ledger", "store", str(tmp_path)), store_dir=tmp_path
    )
    monkeypatch.setattr(adapter, "preflight", lambda *args: dataset)
    monkeypatch.setattr(adapter, "record_training_use", lambda *args: None)
    # The sixth read seals the child exit; it is not another progress event.
    ticks = iter((10.0, 20.0, 30.0, 40.0, 50.0, 60.0))
    monkeypatch.setattr(module.time, "time", lambda: next(ticks))

    def private_child(
        command, log, environment, *, on_started, on_stdout_line, on_exited, **kwargs
    ):
        sequence = event_step = 0
        on_started()

        def emit(kind, **details):
            nonlocal sequence
            sequence += 1
            on_stdout_line(
                json_bytes(
                    {
                        "schema": "spireagent/structured-child-event-v1",
                        "operation_id": operation["operation_id"],
                        "attempt_id": operation["attempt_id"],
                        "sequence": sequence,
                        "kind": kind,
                        "details": details,
                    }
                )
            )

        def event(kind, details):
            nonlocal event_step
            event_step += 1
            artifact = Manifest(
                "run_event",
                PRODUCER,
                (Parent("run", run.artifact_id),),
                parameters=FrozenObject.of(
                    {
                        "schema": "stpd/run-event-v1",
                        "step": event_step,
                        "attempt": operation["attempt_id"],
                        "kind": kind,
                        "details": {**details, "attempt_producer": PRODUCER.to_dict()},
                    }
                ),
            )
            artifacts[artifact.artifact_id] = artifact
            emit("event", event_id=artifact.artifact_id)

        def checkpoint(counters):
            cursor = {
                "epoch": counters["epoch"],
                "chunk": counters["boundaries"],
                "run_index": 0,
                "next_step": 4 * counters["boundaries"],
            }
            cp = Manifest(
                "checkpoint",
                PRODUCER,
                (Parent("run", run.artifact_id), Parent("training_input", training.artifact_id)),
                parameters=FrozenObject.of(
                    {
                        "schema": "stpd/structured-m2-training-checkpoint-v3",
                        "attempt": operation["attempt_id"],
                        "attempt_producer": PRODUCER.to_dict(),
                        "optimizer_updates": counters["optimizer_updates"],
                        "boundary": counters["boundaries"],
                        "cursor": cursor,
                    }
                ),
            )
            artifacts[cp.artifact_id] = cp
            event(
                "checkpoint",
                {
                    "checkpoint_id": cp.artifact_id,
                    "optimizer_updates": counters["optimizer_updates"],
                    "boundary": counters["boundaries"],
                    "cursor": cursor,
                    "phase": "train",
                    "accepted_counters": counters,
                },
            )
            return cp.artifact_id

        emit("started", attempt_producer=PRODUCER.to_dict())
        emit("prepared", run_id=run.artifact_id, input_id=training.artifact_id)
        checkpoint(
            {
                "optimizer_updates": 0,
                "label_uses": 0,
                "observations": 0,
                "boundaries": 0,
                "epoch": 0,
                "whole_cohort_passes": 0,
            }
        )
        first = {
            "optimizer_updates": 0,
            "label_uses": 0,
            "observations": 4,
            "boundaries": 1,
            "epoch": 0,
            "whole_cohort_passes": 0,
        }
        event(
            "progress",
            {
                "optimizer_updates": 0,
                "boundary": 1,
                "cursor": {"epoch": 0, "chunk": 1, "run_index": 0, "next_step": 4},
                "label_uses": 0,
                "observations": 4,
                "advances": 4,
                "chunk_loss": None,
                "training_complete": False,
                "accepted_counters": first,
            },
        )
        checkpoint(first)
        second = {
            "optimizer_updates": 1,
            "label_uses": 2,
            "observations": 8,
            "boundaries": 0 if regressed else 2,
            "epoch": 0,
            "whole_cohort_passes": 0,
        }
        event(
            "progress",
            {
                "optimizer_updates": 1,
                "boundary": second["boundaries"],
                "cursor": {"epoch": 0, "chunk": 2, "run_index": 0, "next_step": 8},
                "label_uses": 2,
                "observations": 8,
                "advances": 8,
                "chunk_loss": 1.0,
                "training_complete": False,
                "accepted_counters": second,
            },
        )
        cp = checkpoint(second)
        emit(
            "terminal",
            state="paused",
            run_id=run.artifact_id,
            attempt_id=operation["attempt_id"],
            checkpoint_id=cp,
            result_id=None,
            model_id=None,
            event_step=event_step,
            optimizer_updates=1,
            attempt_seconds=0.01,
        )
        on_exited(0, False, 0.01)
        return 0, 0.01

    monkeypatch.setattr(module, "_private_child", private_child)
    return adapter, service, owner, store


def test_existing_recipe_projects_actual_counters_and_keeps_checkpoint_timestamp_separate(
    tmp_path, monkeypatch
):
    adapter, service, owner, store = setup(tmp_path, monkeypatch)
    adapter.execute(service, tmp_path / "operation.json", "4" * 32, owner, store, lambda: None)
    facts = [row for row in service.snapshots if row.get("accepted_counters")]
    first_progress = next(row for row in facts if row.get("last_progress_at") == 20.0)
    first_checkpoint = next(row for row in facts if row.get("last_checkpoint_at") == 30.0)
    assert first_progress["accepted_counters"]["boundaries"] == 1
    assert first_checkpoint["last_progress_at"] == 20.0
    assert first_checkpoint["checkpoint_progress"]["optimizer_updates"] == 0
    assert service.operation["accepted_counters"] == {
        "optimizer_updates": 1,
        "label_uses": 2,
        "observations": 8,
        "boundaries": 2,
        "epoch": 0,
        "whole_cohort_passes": 0,
    }
    assert service.operation["progress"] == {"unit": "optimizer_update", "completed": 1, "total": 2}
    assert service.operation["training_plan"]["update_cap"] == 100
    assert service.operation["last_progress_at"] == 40.0
    assert service.operation["last_checkpoint_at"] == 50.0
    assert service.operation["status"] == "paused"


def test_original_recipe_rejects_regressed_accepted_counters(tmp_path, monkeypatch):
    adapter, service, owner, store = setup(tmp_path, monkeypatch, regressed=True)
    with pytest.raises(BoundaryError, match="child_progress_regressed"):
        adapter.execute(service, tmp_path / "operation.json", "4" * 32, owner, store, lambda: None)
    assert service.operation["accepted_counters"]["boundaries"] == 1


def test_prepared_Run_must_bind_the_original_saved_plan_source(tmp_path, monkeypatch):
    adapter, service, owner, store = setup(tmp_path, monkeypatch, changed_plan=True)
    with pytest.raises(BoundaryError, match="frozen_training_plan_binding_mismatch"):
        adapter.execute(service, tmp_path / "operation.json", "4" * 32, owner, store, lambda: None)
    assert "run_id" not in service.operation
