"""Cheap, faithful algorithm -> immutable package -> real Agent-port checks."""

from __future__ import annotations

import copy
import io
import json
import random
from dataclasses import replace
from pathlib import Path

import pytest
import torch
from test_artifact_store_v1 import PRODUCER
from test_p1_snapshot import explicit_snapshot, projection_fixture, store_at
from test_p1_t0 import observation

from spireagent.json_boundary import BoundaryError
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import copy_artifact
from stpd.p1.agent_driver import SCHEMA
from stpd.p1.baseline_agent import installation, serve
from stpd.p1.baseline_export import export_model, load_model, register
from stpd.p1.baseline_features import NUMERICS, candidate_features, fit_categories, vectorize
from stpd.p1.baseline_spec import BaselineConfig
from stpd.p1.baseline_worker import (
    execute_baseline_run,
    materialize_baseline_model,
    verify_baseline_checkpoint,
    verify_baseline_result,
)
from stpd.p1.baseline_workload import prepare_baseline_run, verify_baseline_run
from stpd.p1.baselines import BaselinePopulation, BaselineTrainer
from stpd.p1.training import maximum_difference
from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint


def populations():
    return {
        split: BaselinePopulation((observation(),), (1,), (split + "-point",))
        for split in ("train", "dev", "test")
    }


def test_hand_features_follow_actual_connector_producer_fields_and_public_refs():
    path = Path(__file__).resolve().parents[2] / (
        "components/connector/sdk/typescript/test/fixtures/p1-global/synthetic-complete-combat.json"
    )
    fixture = json.loads(path.read_bytes())["snapshot"]
    combat = copy.deepcopy(fixture["interaction"]["content"]["context"])
    public_player = fixture["persistent"]["content"]["player"]
    obs = observation()
    obs["scene"]["family"] = "combat"
    obs["combat"] = {"availability": "available", "content": combat}
    obs["player"] = {"availability": "available", "content": public_player}
    card, enemy = combat["player"]["hand"][1], combat["enemies"][0]
    obs["catalog"][0].update(subject=card["entity_id"], target=enemy["entity_id"], kind="card.play")
    row = candidate_features(obs)[0]
    assert row["card_cost"] == float(card["cost"])
    assert row["card_type"] == card["type"]
    assert row["description_number_1"] == 6
    assert row["description_number_2"] is None
    assert row["target_hp_ratio"] == enemy["hp"] / enemy["max_hp"]
    assert row["target_is_attack"] == 1
    assert row["player_energy"] == combat["player"]["energy"]
    assert row["player_hp_ratio"] == public_player["hp"] / public_player["max_hp"]
    assert row["hand_size"] == len(combat["player"]["hand"])
    # The opaque reference names may be changed bijectively without affecting features.
    renamed = copy.deepcopy(obs)
    renamed["combat"]["content"]["player"]["hand"][1]["entity_id"] = "renamed-card"
    renamed["combat"]["content"]["enemies"][0]["entity_id"] = "renamed-enemy"
    renamed["catalog"][0].update(subject="renamed-card", target="renamed-enemy")
    assert candidate_features(renamed) == candidate_features(obs)


def test_missing_optional_numeric_and_unknown_category_stay_explicit_no_catalog_filter():
    train = observation()
    categories = fit_categories([train])
    heldout = copy.deepcopy(train)
    heldout["scene"]["family"] = "unseen-heldout-family"
    heldout["catalog"][0]["kind"] = "heldout-only-action"
    heldout["scene"]["content"]["entities"][0].update(cost="X", type=None)
    heldout["player"] = {"availability": "available", "content": {"hp": None, "max_hp": 0}}
    vectors = vectorize(heldout, categories)
    assert len(vectors) == len(heldout["catalog"])
    assert "heldout-only-action" not in categories["action"]
    offset = sum(len(v) for v in categories.values())
    assert vectors[0][offset + 2 * NUMERICS.index("card_cost") :][:2] == [0, 1]
    assert vectors[0][0] == 1  # unknown scene
    assert all(torch.isfinite(torch.tensor(vectors)).flatten())
    aliased = copy.deepcopy(heldout)
    aliased["scene"]["content"]["entities"].append(
        {"entity_id": "e1", "cost": "2", "description": "Conflict 90"}
    )
    assert candidate_features(aliased)[0]["description_number_1"] is None


def test_frequency_is_train_scene_chosen_action_kind_proportion_and_no_optimizer():
    data = populations()
    trainer = BaselineTrainer(data, "snapshot", BaselineConfig(algorithm="frequency"))
    trainer.advance()
    trainer.finish()
    assert trainer.step == 0 and trainer.optimizer is None
    assert trainer.model.scores(observation()) == [0, 1]
    unseen = observation()
    unseen["scene"]["family"] = "heldout-unseen"
    assert trainer.model.scores(unseen) == [0, 0]
    assert trainer.model.counts == {"event": {"event.leave": 1}}


def test_linear_logistic_reduces_train_loss_and_exact_resume_keeps_dev_best():
    config = BaselineConfig(algorithm="linear", max_epochs=8)
    trainer = BaselineTrainer(populations(), "snapshot", config)
    before = trainer.evaluate(trainer.populations["train"])["mean_score_softmax_N_loss"]
    trainer.advance()
    saved = trainer.checkpoint()
    replacement = BaselineTrainer(populations(), "snapshot", config)
    replacement.restore(saved)
    while trainer.phase == "training":
        trainer.advance()
        replacement.advance()
    trainer.finish()
    replacement.finish()
    assert (
        maximum_difference(
            decode_checkpoint(trainer.checkpoint()), decode_checkpoint(replacement.checkpoint())
        )
        == 0
    )
    assert trainer.evaluate(trainer.populations["train"])["mean_score_softmax_N_loss"] < before
    assert trainer.model.scores(observation())[1] > trainer.model.scores(observation())[0]
    assert trainer.best_step and trainer.best_model and trainer.test_result["N"] == 1
    with pytest.raises(ValueError, match="identity"):
        BaselineTrainer(populations(), "different", config).restore(saved)


def _registered(tmp_path, algorithm):
    config = BaselineConfig(algorithm=algorithm, max_epochs=2)
    trainer = BaselineTrainer(populations(), "snapshot", config)
    while trainer.phase == "training":
        trainer.advance()
    trainer.finish()
    checkpoint = tmp_path / "last"
    checkpoint.write_bytes(trainer.checkpoint())
    package = tmp_path / "package"
    descriptor = export_model(trainer.selected_model(), checkpoint, package, trainer.identity)
    model, payload = load_model(package / "model.tensor-tree", descriptor["package_sha256"])
    assert model.scores(observation()) == trainer.selected_model().scores(observation())
    assert payload["training_identity"] == trainer.identity
    root = tmp_path / "private"
    register(
        package,
        root,
        {
            "schema": "sts2.policy-runtime/agent-manifest-1",
            "input": {"profile": "p1-global-r1", "global": {"revision": "r1.5"}},
        },
    )
    config, manifest, _, _ = installation(root / "config.json", root / "manifest.json", root)
    return root, config, manifest


@pytest.mark.parametrize(
    "algorithm,tied",
    [("random", False), ("frequency", False), ("frequency", True), ("linear", False)],
)
def test_serialized_package_actual_agent_ack_rng_repeat_and_full_catalog(tmp_path, algorithm, tied):
    root, config, manifest = _registered(tmp_path, algorithm)
    output = io.StringIO()
    context = {"schema": SCHEMA, "session_id": "fixture", "recovery_epoch": 0}
    choices = []
    current = observation()
    if tied:
        current["scene"]["family"] = "unseen-frequency-scene"

    def client():
        consumption = None
        for number, unit in enumerate(("one", "one", "two", "three", "four")):
            value = {
                "acquisition_id": "acq-" + str(number),
                "continuity_token": "continuity",
                "input_spec": manifest["input"]["input_spec"],
                "previous_consumption_id": consumption,
                "observation": current,
                "catalog": current["catalog"],
                "consumption_unit": {"id": unit},
            }
            request = "consume-" + str(number)
            yield json.dumps(
                {**context, "message_type": "consume", "request_id": request, "input": value}
            )
            completion = json.loads(output.getvalue().splitlines()[-1])["completion"]
            consumption = completion["consumption_id"]
            assert completion["advanced"] == (number != 1)
            yield json.dumps(
                {
                    **context,
                    "message_type": "consume_ack",
                    "request_id": request,
                    "completion": completion,
                }
            )
            for repeat in range(2):
                yield json.dumps(
                    {
                        **context,
                        "message_type": "next",
                        "request_id": "next",
                        "input": {"continuity_token": "continuity"},
                    }
                )
                directive = json.loads(output.getvalue().splitlines()[-1])["output"]["directive"]
                assert len(directive["scores"]["values"]) == 2
                if repeat == 0:
                    choices.append(directive["selection"]["action_id"])
                else:
                    assert choices[-1] == directive["selection"]["action_id"]

    serve(root / "config.json", root / "manifest.json", root, client(), output)
    assert choices[0] == choices[1]
    if algorithm == "random" or tied:
        rng = random.Random(config["seed"])
        expected = [rng.choice(["c7", "c9"]) for _ in range(4)]
        assert choices == [expected[0], *expected]
        assert set(choices) == {"c7", "c9"}  # zero scores did not become first-item argmax.
    else:
        assert choices == ["c9"] * 5


def test_agent_refuses_next_before_ack_and_tampered_installation(tmp_path):
    root, _, manifest = _registered(tmp_path, "random")
    output = io.StringIO()
    context = {"schema": SCHEMA, "session_id": "fixture", "recovery_epoch": 0}
    value = {
        "acquisition_id": "acq",
        "continuity_token": "continuity",
        "input_spec": manifest["input"]["input_spec"],
        "previous_consumption_id": None,
        "observation": observation(),
        "catalog": observation()["catalog"],
        "consumption_unit": {"id": "one"},
    }
    messages = [
        json.dumps({**context, "message_type": "consume", "request_id": "consume", "input": value}),
        json.dumps(
            {
                **context,
                "message_type": "next",
                "request_id": "next",
                "input": {"continuity_token": "continuity"},
            }
        ),
    ]
    with pytest.raises(ValueError, match="acknowledged"):
        serve(root / "config.json", root / "manifest.json", root, messages, output)
    assert all(
        json.loads(row)["message_type"] != "directive" for row in output.getvalue().splitlines()
    )
    config = json.loads((root / "config.json").read_bytes())
    config["seed"] += 1
    (root / "config.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="binding"):
        installation(root / "config.json", root / "manifest.json", root)


@pytest.mark.parametrize("algorithm", ["random", "frequency", "linear"])
def test_same_store_run_worker_result_materialize_and_transfer(tmp_path, algorithm):
    store = store_at(tmp_path / "store")
    projections = [projection_fixture(store, "source-" + str(i))[0] for i in range(3)]
    snapshot = explicit_snapshot(store, projections)
    run = prepare_baseline_run(
        store,
        snapshot.artifact_id,
        PRODUCER,
        BaselineConfig(algorithm=algorithm, max_epochs=2),
        "a" * 32,
    )
    workload = verify_baseline_run(store, run.artifact_id, PRODUCER)
    reporter = ObjectStoreRunReporter(store, store.blobs)
    result = execute_baseline_run(store, reporter, run.artifact_id, PRODUCER, attempt_id="full")
    checked, model, last = verify_baseline_result(store, result.result_id, workload)
    assert last.state["step"] == (2 if algorithm == "linear" else 0)
    assert checked.parameters.value()["scientific_verdict"] == "not_claimed"
    descriptor = materialize_baseline_model(store, model.artifact_id, tmp_path / "export")
    loaded, _ = load_model(tmp_path / "export/model.tensor-tree", descriptor["package_sha256"])
    assert len(loaded.scores(observation())) == 2
    readback = store_at(tmp_path / "readback")
    copy_artifact(store, readback, checked.artifact_id)
    verify_baseline_result(readback, checked.artifact_id, workload)
    # Reconcile reads the selected result; it does not fit another model or draw RNG.
    reconciled = execute_baseline_run(
        store, reporter, run.artifact_id, PRODUCER, attempt_id="query", mode="reconcile"
    )
    assert reconciled.result_id == result.result_id
    verify_baseline_checkpoint(store, result.checkpoint_id, workload)
    with pytest.raises(BoundaryError, match="source_or_schema"):
        verify_baseline_run(store, run.artifact_id, replace(PRODUCER, source_revision="f" * 40))


def test_linear_selects_dev_best_before_test_and_exports_selected_weights(tmp_path):
    data = populations()
    data["dev"] = BaselinePopulation((observation(),), (0,), ("dev-opposing",))
    trainer = BaselineTrainer(data, "snapshot", BaselineConfig(algorithm="linear", max_epochs=4))
    while trainer.phase == "training":
        trainer.advance()
        assert trainer.test_result is None
    assert trainer.best_step == 1 and trainer.step == 4
    trainer.finish()
    assert not torch.equal(trainer.selected_model().weights, trainer.model.weights)
    checkpoint = tmp_path / "last"
    checkpoint.write_bytes(trainer.checkpoint())
    descriptor = export_model(
        trainer.selected_model(), checkpoint, tmp_path / "package", trainer.identity
    )
    loaded, _ = load_model(tmp_path / "package/model.tensor-tree", descriptor["package_sha256"])
    assert torch.equal(loaded.weights, trainer.selected_model().weights)


@pytest.mark.parametrize("algorithm", ["random", "frequency", "linear"])
def test_worker_pause_resume_uses_same_run_checkpoint_and_exact_result(tmp_path, algorithm):
    store = store_at(tmp_path / "store")
    projections = [projection_fixture(store, "source-" + str(i))[0] for i in range(6)]
    snapshot = explicit_snapshot(store, projections)
    run = prepare_baseline_run(
        store,
        snapshot.artifact_id,
        PRODUCER,
        BaselineConfig(algorithm=algorithm, max_epochs=2, batch_size=1),
        "a" * 32,
    )
    reporter = ObjectStoreRunReporter(store, store.blobs)

    class Pause:
        calls = 0

        def requested_action(self):
            self.calls += 1
            return "continue" if self.calls == 1 else "pause"

    paused = execute_baseline_run(
        store, reporter, run.artifact_id, PRODUCER, attempt_id="first", control=Pause()
    )
    assert paused.state == "paused"
    assert paused.optimizer_updates == (1 if algorithm == "linear" else 0)
    resumed = execute_baseline_run(
        store,
        reporter,
        run.artifact_id,
        PRODUCER,
        attempt_id="replacement",
        resume=paused.checkpoint_id,
        mode="resume",
    )
    readback = store_at(tmp_path / "full")
    copy_artifact(store, readback, run.artifact_id)
    full = execute_baseline_run(
        readback,
        ObjectStoreRunReporter(readback, readback.blobs),
        run.artifact_id,
        PRODUCER,
        attempt_id="uninterrupted",
    )
    workload = verify_baseline_run(store, run.artifact_id, PRODUCER)
    assert (
        maximum_difference(
            verify_baseline_checkpoint(store, resumed.checkpoint_id, workload).state,
            verify_baseline_checkpoint(readback, full.checkpoint_id, workload).state,
        )
        == 0
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "cursor",
        "best_step",
        "early_test",
        "optimizer_lr",
        "optimizer_empty",
        "optimizer_steps",
        "optimizer_shape",
    ],
)
def test_resume_rejects_inconsistent_progress_selected_model_and_optimizer(corruption):
    config = BaselineConfig(algorithm="linear", max_epochs=2)
    trainer = BaselineTrainer(populations(), "snapshot", config)
    trainer.advance()
    state = decode_checkpoint(trainer.checkpoint())
    if corruption == "cursor":
        state["cursor"] = -1
    elif corruption == "best_step":
        state["best_step"] = 100
    elif corruption == "early_test":
        state["test_result"] = {"N": 1}
    elif corruption == "optimizer_lr":
        state["optimizer"]["param_groups"][0]["lr"] = 1.0
    elif corruption == "optimizer_empty":
        state["optimizer"]["state"] = {}
    elif corruption == "optimizer_steps":
        state["optimizer"]["state"][0]["step"] += 1
    else:
        state["optimizer"]["state"][0]["exp_avg"] = torch.zeros(1)
    replacement = BaselineTrainer(populations(), "snapshot", config)
    with pytest.raises(ValueError):
        replacement.restore(encode_checkpoint(state))
