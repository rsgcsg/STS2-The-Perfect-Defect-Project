"""One bounded ordinary two-arm CPU fit on sealed synthetic N; no game or private corpus."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace

import pytest
from test_reward_m0_pair import limits
from test_reward_m0_pair import pair_originals as pair_originals
from test_reward_m0_recipe import publish_pair

from spireagent.workbench.local_training import LocalTrainingService
from spireagent.workbench.recipe_contracts import TrainingRequest
from spireagent.workbench.reward_m0_data import require_reward_m0_use
from stpd.reward_m0_spec import MODEL_SCHEMA, RECIPE, RUN_SCHEMA, RewardM0Config


def test_ordinary_two_arm_fit_has_shared_init_tokenizer_schedule_and_exact_artifacts(
    pair_originals,
    monkeypatch,
):
    pytest.importorskip("torch")
    store, owner, config, _, _ = pair_originals
    # The real artifact reservation ACK fences the first update before the
    # second. This makes the pause test observe an actual owned boundary.
    source_id, frozen = publish_pair(
        pair_originals,
        monkeypatch,
        RewardM0Config(steps=2, state_vocab_size=256, checkpoint_every_boundaries=1),
    )
    models, runs, inputs = [], [], []
    previous = None
    operations = []
    from spireagent.workbench.recipes import structured

    actual_child = structured._private_child
    for arm in ("A", "B"):
        selected = replace(frozen, arm=arm)
        service = LocalTrainingService(config)
        request = TrainingRequest(
            uuid.uuid4().hex,
            RECIPE,
            source_id,
            selected.to_dict(),
            limits=limits(),
            after_completed_operation_id=previous,
        )
        pause_sent = False

        def child(*args, _arm=arm, _service=service, **kwargs):
            callback = kwargs["on_stdout_line"]

            def observed(line):
                nonlocal pause_sent
                callback(line)  # Actual production validation/dispatch precedes control.
                if _arm == "A" and not pause_sent:
                    current = _service.status()["operation"]
                    if current["progress"]["completed"] == 1:
                        _service.pause(current["operation_id"], current["attempt_id"])
                        pause_sent = True

            kwargs["on_stdout_line"] = observed
            return actual_child(*args, **kwargs)

        monkeypatch.setattr(structured, "_private_child", child)
        service.start(request)
        assert service._thread is not None
        service._thread.join(timeout=140)
        assert not service._thread.is_alive(), "fixed fixture wall budget did not terminate"
        operation = service.status()["operation"]
        if arm == "A":
            assert pause_sent and operation["status"] == "paused", operation
            assert operation["progress"]["completed"] == 1
            before = operation["elapsed_seconds"]
            service.resume(
                operation["operation_id"],
                operation["attempt_id"],
                operation["checkpoint_id"],
                uuid.uuid4().hex,
                limits(),
            )
            service._thread.join(timeout=140)
            assert not service._thread.is_alive()
            operation = service.status()["operation"]
            assert operation["elapsed_seconds"] >= before
            assert operation["limits"] == limits()

        assert operation["status"] == "completed", operation
        assert operation["validation_state"] == "verified"
        assert operation["progress"]["completed"] == selected.steps == 2
        assert operation["worker_state"] == "terminal" and operation["child_exit"]["exit_code"] == 0
        require_reward_m0_use(owner, store, source_id, operation["operation_id"])
        run = store.get_manifest(operation["run_id"])
        model = store.get_manifest(operation["model_id"])
        training = store.get_manifest(operation["input_id"])
        checkpoint = store.get_manifest(operation["checkpoint_id"])
        result = store.get_manifest(operation["result_id"])
        assert run.parameters.value()["schema"] == RUN_SCHEMA
        assert model.parameters.value()["schema"] == MODEL_SCHEMA
        assert model.parameters.value()["arm"] == arm
        assert model.parent("source") == source_id
        assert model.parent("training_input") == training.artifact_id
        assert model.parent("checkpoint") == checkpoint.artifact_id
        assert checkpoint.parent("run") == run.artifact_id
        metrics = json.loads(b"".join(store.read_payload(result.payload("metrics"))))
        assert metrics["evaluation_scope"] == "TRAIN_internal_diagnostic_no_independent_heldout"
        assert metrics["eligible_N"] == 4 and len(metrics["rows"]) == 4
        assert metrics["mean_NLL"] >= 0
        assert len(metrics["update_losses"]) == (1 if arm == "A" else 2)
        assert model.parameters.value()["initial_weights_sha256"] != model.payload("weights").sha256
        assert (
            hashlib.sha256(b"".join(store.read_payload(model.payload("weights")))).hexdigest()
            == (model.parameters.value()["weights_sha256"])
        )
        models.append(model)
        runs.append(run)
        inputs.append(training)
        previous = operation["operation_id"]
        operations.append(operation["operation_id"])
    assert models[0].artifact_id != models[1].artifact_id
    assert runs[0].artifact_id != runs[1].artifact_id
    assert inputs[0].artifact_id != inputs[1].artifact_id
    assert (
        models[0].parameters.value()["initial_weights_sha256"]
        == (models[1].parameters.value()["initial_weights_sha256"])
    )
    assert (
        models[0].parameters.value()["paired_plan"] == models[1].parameters.value()["paired_plan"]
    )
    assert inputs[0].parent("paired_features") == inputs[1].parent("paired_features")
    assert inputs[0].payload("state_tokenizer") == inputs[1].payload("state_tokenizer")
    with owner.transaction() as db:
        for operation in operations:
            assert (
                db.execute(
                    "SELECT count(*) FROM curation_source_uses WHERE reference=?", (operation,)
                ).fetchone()[0]
                > 0
            )
