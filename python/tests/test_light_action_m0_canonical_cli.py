"""Synthetic canonical M0 CLI admission and resume path; no private data or game runtime."""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.storage.local import LocalBlobStore
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
            "--stop-after", "1",
        )
        assert paused["state"] == "paused" and paused["checkpoint_id"]
        completed = _cli(
            monkeypatch, *common, "run-light-action-m0",
            "--project-config", str(config_path), "--run", paused["run_id"],
            "--operation", operation, "--resume", paused["checkpoint_id"],
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


def test_canonical_m0_cli_rejects_a_foreign_store_before_opening_it(tmp_path: Path) -> None:
    pytest.importorskip("torch")
    from spireagent.research_cli import _configured_m0_owner

    config_path, store_dir, _, _ = _synthetic_workspace(tmp_path)
    with pytest.raises(BoundaryError, match="store_identity_mismatch"):
        _configured_m0_owner(config_path, str(tmp_path / "foreign-store"))
    assert not (tmp_path / "foreign-store").exists()
    assert store_dir.is_dir()
