"""Synthetic canonical M0 CLI admission and resume path; no private data or game runtime."""

from __future__ import annotations

import contextlib
import io
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from spireagent.artifact_contracts import Parent
from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.run_reporter import ObjectStoreRunReporter
from spireagent.storage.store import ManifestArtifactStore, copy_artifact
from spireagent.workbench.developer import (
    LocalResearchWorkspaceConfig,
    ProjectConfig,
    combination,
)


def _synthetic_workspace(tmp_path: Path) -> tuple[Path, Path, str, object]:
    pytest.importorskip("torch")
    pytest.importorskip("tokenizers")
    pytest.importorskip("jwt")
    from test_dataset_curation import build
    from test_decision_store import setup
    from test_local_curation import create

    from stpd.fullrun.decision_dataset import SelectionRules
    from stpd.fullrun.decision_spool import SpoolSelection
    from stpd.fullrun.decision_store import preview

    hub, upload, source, jobs = setup(tmp_path / "hub")
    dataset_id = build(jobs, upload)["result"]["artifact_id"]
    local_root = tmp_path / "local"
    local_root.mkdir()
    state, directory, owner = create(local_root)
    store = ManifestArtifactStore(LocalBlobStore(owner.store_dir, create=False))
    copy_artifact(hub.store, store, dataset_id)
    projected = preview(
        store, (store.get_manifest(source.artifact_id),), SelectionRules(),
        on_projection=lambda projection: owner.ledger.index_source(
            source.artifact_id, projection),
    )
    try:
        owner.ledger.claim(dataset_id, "training", projected.run_ids)
        owner.ledger.bind(dataset_id, dataset_id)
    finally:
        if isinstance(projected.records, SpoolSelection):
            projected.records.owner.close()
    config = ProjectConfig(
        state, "", "", None, combination(),
        LocalResearchWorkspaceConfig(owner.store_dir, directory / "registry.sqlite"),
    )
    config_path = tmp_path / "project.json"
    config_path.write_text(json.dumps(config.to_dict()), encoding="utf-8")
    return config_path, owner.store_dir, dataset_id, owner


def _cli(monkeypatch, *arguments: str) -> dict:
    from test_artifact_store_v1 import PRODUCER

    from spireagent.research_cli import main

    monkeypatch.setattr("spireagent.research_cli.source_identity", lambda _root: PRODUCER)
    monkeypatch.setattr(sys, "argv", ["research-cli", *arguments])
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = main()
    assert status == 0
    lines = output.getvalue().strip().splitlines()
    assert lines, "research CLI did not emit its result receipt"
    return json.loads(lines[-1])


def _tiny_pinned_snapshot(path: Path, monkeypatch: pytest.MonkeyPatch):
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers

    from spireagent.package_identity import file_sha256

    path.mkdir()
    tokenizer = Tokenizer(models.BPE())
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.decoder = decoders.ByteLevel()
    tokenizer.train_from_iterator(
        ("OBS\nstate\n", "ACT\nfirst\n", "ACT\nsecond\n"),
        trainers.BpeTrainer(
            vocab_size=512, min_frequency=1, show_progress=False,
            initial_alphabet=pre_tokenizers.ByteLevel.alphabet(), special_tokens=[],
        ),
    )
    tokenizer_path = path / "tokenizer.json"
    tokenizer_path.write_text(tokenizer.to_str(), encoding="utf-8")
    sha = file_sha256(tokenizer_path)
    pin = SimpleNamespace(
        file_by_name={"tokenizer.json": SimpleNamespace(
            sha256=sha, size_bytes=tokenizer_path.stat().st_size)},
        hard_limit=8192,
        model_id="Qwen/Qwen3-0.6B-Base", repo_revision="d" * 40,
        tokenizer_bundle_sha256="e" * 64,
        special_tokens=(
            SimpleNamespace(token_id=1, roles=("bos_token",)),
            SimpleNamespace(token_id=2, roles=("eos_token",)),
            SimpleNamespace(token_id=3, roles=("pad_token",)),
        ),
    )
    monkeypatch.setattr("stpd.fullrun.light_action_inputs.load_pin", lambda: pin)
    monkeypatch.setattr("stpd.qwen.readout_backend.validate_engineering_identity",
                        lambda _identity: None)

    def backend_factory(snapshot, *, device):
        assert snapshot == path
        assert device == "cpu"
        from test_light_action_m0 import _tiny_backend

        return _tiny_backend(tokenizer.get_vocab_size())

    monkeypatch.setattr("stpd.workers.token_ranking.PortableQwenBackend", backend_factory)
    return tokenizer_path


def test_canonical_m0_cli_reserves_before_input_and_binds_resume_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    torch = pytest.importorskip("torch")
    pytest.importorskip("tokenizers")
    pytest.importorskip("jwt")
    from stpd.policy.token_decision import LightActionM0DecisionScorer

    config_path, store_dir, dataset_id, owner = _synthetic_workspace(tmp_path)
    operation = "a" * 32
    common = ("--store", str(store_dir))
    prepared = _cli(
        monkeypatch, *common, "prepare-light-action-m0",
        "--project-config", str(config_path), "--dataset", dataset_id,
        "--operation", operation, "--backbone", "s", "--train-limit", "8",
        "--dev-limit", "4",
    )
    input_id = prepared["training_input_id"]
    assert prepared["input_schema"] == "stpd/stage1a-light-action-m0-canonical-input-v1"
    assert prepared["admission"]["operation_id"] == operation

    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.store import ManifestArtifactStore

    store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
    before_wrong_operation = store.manifest_ids()
    with pytest.raises(BoundaryError, match="training_binding_mismatch"):
        _cli(
            monkeypatch, *common, "train-light-action-m0",
            "--project-config", str(config_path), "--inputs", input_id,
            "--operation", "b" * 32,
            "--recipe", "stage1a.dsimple.light-action.m0.s.v1", "--steps", "2",
        )
    assert store.manifest_ids() == before_wrong_operation

    previous_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        paused = _cli(
            monkeypatch, *common, "train-light-action-m0",
            "--project-config", str(config_path), "--inputs", input_id,
            "--operation", operation,
            "--recipe", "stage1a.dsimple.light-action.m0.s.v1", "--steps", "2",
            "--stop-after", "1", "--checkpoint-interval", "3",
        )
        assert paused["state"] == "paused" and paused["checkpoint_id"]
        completed = _cli(
            monkeypatch, *common, "run-light-action-m0",
            "--project-config", str(config_path), "--run", paused["run_id"],
            "--operation", operation, "--resume", paused["checkpoint_id"],
            "--checkpoint-interval", "3",
        )
        assert completed["state"] == "completed" and completed["result_id"]
    finally:
        torch.set_num_threads(previous_threads)

    model_id = store.get_manifest(completed["result_id"]).parent("model")
    exported = tmp_path / "model-export"
    receipt = _cli(
        monkeypatch, *common, "export-light-action-m0",
        "--project-config", str(config_path), "--operation", operation,
        "--model", model_id, "--destination", str(exported),
    )
    scorer = LightActionM0DecisionScorer(exported)
    assert receipt["model_id"] == model_id
    assert scorer.canonical_input is True
    with pytest.raises(BoundaryError, match="canonical_semantic_input_required"):
        scorer.score_snapshot({"schema": "stpd/text-menu-snapshot-v1"})
    from stpd.fullrun.decision_spool import SpoolSelection
    from stpd.fullrun.decision_store import load as load_decisions

    _, dataset = load_decisions(store, dataset_id)
    try:
        sample = dataset.records[0]
        original = scorer.score_semantic(sample.state, sample.actions, profile="standard")
        reordered = scorer.score_semantic(sample.state, sample.actions[::-1], profile="standard")
        assert reordered == pytest.approx(original, rel=0, abs=1e-7)
        with pytest.raises(BoundaryError, match="canonical_renderer_profile_mismatch"):
            scorer.score_semantic(sample.state, sample.actions, profile="full")
    finally:
        if isinstance(dataset.records, SpoolSelection):
            dataset.records.owner.close()
    assert owner.require_training_datasets(store, (dataset_id,), operation)[
        "historical_external_exposure"] == "unknown"
    run_events = [item.parameters.value()
                  for item in ObjectStoreRunReporter(store, store.blobs).events(paused["run_id"])]
    assert [item["details"]["checkpoint_interval"]
            for item in run_events if item["kind"] in {"started", "resumed"}] == [3, 3]
    report = store.get_manifest(
        store.get_manifest(completed["result_id"]).parent("offline_evaluation"))
    assert report.parameters.value()["evaluation_scope"] == \
        "within_training_purpose_allocation"
    assert report.parameters.value()["physical_game_independence"] == "unresolved"
    metrics = json.loads(b"".join(store.read_payload(report.payload("metrics"))))
    assert metrics["admission"]["clean_held_out_claim"] is False
    summaries = [metrics["summary"]["bootstrap"]]
    summaries.extend(item["bootstrap"] for item in metrics["baselines"].values())
    assert all(item["status"] == "unknown"
               and item["unit"] == "session_scoped_run_group" for item in summaries)
    with owner.transaction() as db:
        evaluations = db.execute(
            "SELECT kind,reference FROM curation_uses WHERE kind='evaluation'"
        ).fetchall()
        eval_sources = db.execute(
            "SELECT source,kind,reference FROM curation_source_uses WHERE kind='evaluation'"
        ).fetchall()
    assert evaluations and eval_sources
    assert {kind for kind, _ in evaluations} == {"evaluation"}
    assert len({reference for _, reference in evaluations}) == 1
    assert {(kind, reference) for _, kind, reference in eval_sources} == {
        ("evaluation", evaluations[0][1])}


def test_canonical_m0_cli_publishes_no_dev_result_when_owner_denies_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("torch")
    pytest.importorskip("tokenizers")
    pytest.importorskip("jwt")
    from spireagent.storage.local import LocalBlobStore
    from spireagent.workbench.local_curation import LocalCurationOwner

    config_path, store_dir, dataset_id, owner = _synthetic_workspace(tmp_path)
    operation = "c" * 32
    common = ("--store", str(store_dir))
    prepared = _cli(
        monkeypatch, *common, "prepare-light-action-m0",
        "--project-config", str(config_path), "--dataset", dataset_id,
        "--operation", operation, "--backbone", "s", "--train-limit", "8",
        "--dev-limit", "4",
    )

    from spireagent.storage.store import ManifestArtifactStore

    store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
    before = set(store.manifest_ids())
    def deny(self, *args, **kwargs):
        raise BoundaryError("local_curation", "allocation_dev_reservation_denied")

    monkeypatch.setattr(LocalCurationOwner, "reserve_allocation_dev", deny)
    with pytest.raises(BoundaryError, match="allocation_dev_reservation_denied"):
        _cli(
            monkeypatch, *common, "train-light-action-m0",
            "--project-config", str(config_path),
            "--inputs", prepared["training_input_id"], "--operation", operation,
            "--recipe", "stage1a.dsimple.light-action.m0.s.v1", "--steps", "1",
        )
    after = set(store.manifest_ids()) - before
    kinds = {store.get_manifest(identity).kind for identity in after}
    assert "offline_evaluation" not in kinds
    assert "run_result" not in kinds
    with owner.transaction() as db:
        assert db.execute(
            "SELECT count(*) FROM curation_uses WHERE kind='evaluation'"
        ).fetchone()[0] == 0


def test_run_tokens_rejects_forged_input_family_before_any_payload_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    torch = pytest.importorskip("torch")
    pytest.importorskip("tokenizers")
    pytest.importorskip("jwt")
    from test_artifact_store_v1 import PRODUCER

    from spireagent.artifact_contracts import FrozenObject
    from spireagent.storage.local import LocalBlobStore
    from stpd.fullrun.light_action_inputs import SCHEMA as LEGACY_M0_INPUT_SCHEMA
    from stpd.fullrun.light_action_inputs import load_light_action_inputs
    from stpd.workers.token_ranking import LightActionM0Config
    from stpd.workers.token_worker import prepare_token_run

    config_path, store_dir, dataset_id, _owner = _synthetic_workspace(tmp_path)
    operation = "d" * 32
    common = ("--store", str(store_dir))
    prepared = _cli(
        monkeypatch, *common, "prepare-light-action-m0",
        "--project-config", str(config_path), "--dataset", dataset_id,
        "--operation", operation, "--backbone", "s", "--train-limit", "8",
        "--dev-limit", "4",
    )
    store = ManifestArtifactStore(LocalBlobStore(store_dir, create=False))
    inputs = load_light_action_inputs(store, prepared["training_input_id"])
    torch.set_num_threads(2)
    run = prepare_token_run(
        store, inputs, LightActionM0Config(steps=1),
        PRODUCER,
    )
    forged_info = dict(inputs.manifest.parameters.value())
    forged_info["schema"] = LEGACY_M0_INPUT_SCHEMA
    forged_input = replace(inputs.manifest, parameters=FrozenObject.of(forged_info))
    store.publish(forged_input)
    experiment = store.get_manifest(run.parent("experiment"))
    forged_experiment = replace(
        experiment, parents=(Parent("training_input", forged_input.artifact_id),))
    store.publish(forged_experiment)
    forged_run = replace(run, parents=(
        Parent("training_input", forged_input.artifact_id),
        Parent("experiment", forged_experiment.artifact_id),
    ))
    store.publish(forged_run)

    reads: list[str] = []
    original_read_payload = ManifestArtifactStore.read_payload

    def record_read(self, payload):
        reads.append(payload.role)
        yield from original_read_payload(self, payload)

    monkeypatch.setattr(ManifestArtifactStore, "read_payload", record_read)
    from spireagent.research_cli import main

    monkeypatch.setattr("spireagent.research_cli.source_identity", lambda _root: run.producer)
    monkeypatch.setattr(sys, "argv", ["research-cli", *common, "run-tokens",
                                       "--run", forged_run.artifact_id])
    with pytest.raises(BoundaryError, match="input_family_source_mismatch"):
        main()
    assert reads == []


def test_canonical_m0_cli_rejects_a_foreign_store_before_opening_it(tmp_path: Path) -> None:
    pytest.importorskip("torch")
    from spireagent.research_cli import _configured_m0_owner

    config_path, store_dir, _, _ = _synthetic_workspace(tmp_path)
    with pytest.raises(BoundaryError, match="store_identity_mismatch"):
        _configured_m0_owner(config_path, str(tmp_path / "foreign-store"))
    assert not (tmp_path / "foreign-store").exists()
    assert store_dir.is_dir()


@pytest.mark.parametrize("backbone", ("pf", "pl"))
def test_public_m0_cli_forwards_pinned_snapshot_across_pause_and_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backbone: str,
) -> None:
    pytest.importorskip("torch")
    pytest.importorskip("tokenizers")
    pytest.importorskip("transformers")
    pytest.importorskip("peft")
    pytest.importorskip("jwt")

    config_path, store_dir, dataset_id, _owner = _synthetic_workspace(tmp_path)
    snapshot = tmp_path / "tiny-pinned-like"
    tokenizer_path = _tiny_pinned_snapshot(snapshot, monkeypatch)
    recipe = f"stage1a.dsimple.light-action.m0.{backbone}.v1"
    operation = ("f" if backbone == "pf" else "9") * 32
    common = ("--store", str(store_dir))
    prepared = _cli(
        monkeypatch, *common, "prepare-light-action-m0",
        "--project-config", str(config_path), "--dataset", dataset_id,
        "--operation", operation, "--backbone", backbone, "--snapshot", str(snapshot),
        "--train-limit", "8", "--dev-limit", "4",
    )
    assert prepared["input_schema"] == "stpd/stage1a-light-action-m0-canonical-input-v1"
    assert tokenizer_path.is_file()

    torch = pytest.importorskip("torch")
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(2)
    try:
        paused = _cli(
            monkeypatch, *common, "train-light-action-m0",
            "--project-config", str(config_path), "--inputs", prepared["training_input_id"],
            "--operation", operation, "--recipe", recipe, "--steps", "2",
            "--snapshot", str(snapshot), "--stop-after", "1",
        )
        assert paused["state"] == "paused" and paused["checkpoint_id"]
        resume_command = "run-tokens" if backbone == "pf" else "run-light-action-m0"
        completed = _cli(
            monkeypatch, *common, resume_command,
            "--project-config", str(config_path), "--run", paused["run_id"],
            "--operation", operation, "--resume", paused["checkpoint_id"],
            "--snapshot", str(snapshot),
        )
    finally:
        torch.set_num_threads(previous_threads)
    assert completed["state"] == "completed" and completed["result_id"]
