"""Real failure archive/Store readers, replacing only the failed game execution."""
from __future__ import annotations

import json

import pytest

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.storage.config import open_store
from spireagent.workbench.managed_evaluation import ManagedEpisodeExecutor
from spireagent.workbench.managed_pipeline import PipelinedManagedExecutor
from spireagent.workbench.reference_suite import _Artifacts


@pytest.mark.parametrize("startup_known", [True, False])
def test_failure_before_typed_seal_keeps_all_available_originals(
        tmp_path, monkeypatch, startup_known):
    executor = PipelinedManagedExecutor.__new__(PipelinedManagedExecutor)
    executor.producer = Producer("synthetic-pipeline", "a"*40, "b"*64)
    executor.runtime = tmp_path / "runtime"
    executor.runtime.mkdir()
    (executor.runtime/"cli.js").write_bytes(b"// synthetic runtime fixture\n")
    (tmp_path/"package.json").write_bytes(json_bytes({
        "name": "@rsgcsg/sts2-policy-runtime", "version": "fixture"}))
    from spireagent.workbench import managed_pipeline

    # Package admission is replaced with this synthetic compiled fixture. Archive,
    # Runtime publication, transfer and full peer readers below are production.
    monkeypatch.setattr(managed_pipeline, "validate_installed_package", lambda *_, **__: None)
    executor.store = open_store(str(tmp_path / "store"))
    executor.peer = open_store(str(tmp_path / "peer"))
    model = Manifest("model", executor.producer,
                     payloads=(executor.store.put_bytes("weights", b"fake-model"),))
    executor.store.publish(model)
    executor.configuration = {"models": {"m": {"artifact_id": model.artifact_id}},
        "peer": {"kind": "local", "directory": str(tmp_path / "peer")},
        "host_pin": {"fixture": True}, "runtime_pin": {"fixture": True},
        "initial_progress": {"progress_sha256": "c"*64},
        "retention": {"tier": "trajectory", "audit_every": 100}}
    executor.preparation = {"store": str(tmp_path / "store")}
    artifacts = _Artifacts(tmp_path / "attempt")
    (artifacts.root / "private").mkdir()
    (artifacts.root / "private/manager.json").write_text("private-token-must-not-be-published")
    row = {"selection_id": "m", "seed": "new", "manifest_sha256": "d"*64}
    report = {"episodes": [row], "status": "running"}
    calls = []

    def failed_original(self, row, manifest, artifacts, checkpoint):
        calls.append(1)
        row.update(status="unknown", reason="runtime_process_failed", run_id="failed-one",
                   world_mutation_offered=True, cleanup_accepted=True, elapsed_seconds=1.0)
        run = artifacts.root / "runs/failed-one"
        run.mkdir(parents=True)
        (run / "events.jsonl").write_bytes(b'{"incomplete":true}\n')
        row["finish"] = artifacts.put("finish.json", json_bytes({"phase": "closed"}))
        if not startup_known:
            row.pop("run_id")
        checkpoint()

    monkeypatch.setattr(ManagedEpisodeExecutor, "__call__", failed_original)
    executor(row, {"character": "Defect", "ascension": 0}, artifacts,
             lambda: artifacts.save(report))
    assert row["status"] == "save_pending"
    result = executor.preserve_report(artifacts.root / "report.json")
    final = result["episodes"][0]
    assert final["status"] == "unknown"
    assert final["reason"] == "runtime_process_failed"
    assert "stored_failure_id" in final and "stored_evidence_id" not in final
    receipt = json.loads((artifacts.root/final["failure_preservation"]["path"]).read_bytes())
    assert receipt["full_readback"] and receipt["typed_recording_qualified"] is False
    manifest = executor.peer.get_manifest(final["stored_failure_id"])
    transfer = json.loads(b"".join(executor.peer.read_payload(manifest.payload("transfer"))))
    assert [f["path"] for f in transfer["files"]] == (
        ["events.jsonl"] if startup_known else ["failed-one/events.jsonl"])
    assert (artifacts.root/"runs/failed-one/events.jsonl").read_bytes() == b'{"incomplete":true}\n'
    assert calls == [1]


def test_diagnostic_direct_remote_rejected_before_any_world_or_package():
    with pytest.raises(BoundaryError, match="diagnostic_requires_node_store"):
        PipelinedManagedExecutor({"store": "s3", "retention": {
            "tier": "diagnostic", "audit_every": 100}}, episode_seconds=10, cleanup_seconds=10)


@pytest.mark.parametrize("typed_pass", [True, False])
def test_deferred_typed_classification_waits_for_close_and_preserves_original(
        tmp_path, monkeypatch, typed_pass):
    """Synthetic typed/native seams; queue, archive, transfer/readback are real."""
    import shutil
    from pathlib import Path
    from types import SimpleNamespace

    import sts2_platform_evidence as evidence
    from test_global_evidence_store import _setup

    from spireagent.workbench import managed_evaluation, managed_pipeline
    from spireagent.workbench.evaluation_ledger import result_row

    store, producer, original, expected, model, _, calls = _setup(tmp_path, monkeypatch)
    run_id = "run-fixture"
    expected.update(run_id=run_id)
    artifacts = _Artifacts(tmp_path / "attempt")
    (artifacts.root / "private").mkdir()
    run = artifacts.root / "runs" / run_id
    run.parent.mkdir()
    shutil.move(original, run)
    executor = PipelinedManagedExecutor.__new__(PipelinedManagedExecutor)
    executor.producer, executor.store = producer, store
    executor.peer = open_store(str(tmp_path / "peer"))
    executor.runtime = Path(expected["global_runtime_directory"])
    executor.preparation = {"store": str(tmp_path / "store")}
    executor.read_concurrency = 2
    executor.configuration = {
        "models": {"m": {"artifact_id": model.artifact_id,
                         "package_directory": str(tmp_path / "model")}},
        "peer": {"kind": "local", "directory": str(tmp_path / "peer")},
        "host_pin": {"fixture": True}, "runtime_pin": {"fixture": True},
        "initial_progress": {"progress_sha256": "c" * 64},
        "retention": {"tier": "formal", "audit_every": 100}}
    row = {"selection_id": "m", "seed": "fresh", "manifest_sha256": "d" * 64,
           "run_id": run_id, "scenario": {"character": "Defect", "ascension": 0},
           "world_mutation_offered": True, "cleanup_accepted": False,
           "retention": managed_pipeline.retention_configuration(executor.configuration)}
    report = {"episodes": [row], "status": "running"}
    def checkpoint():
        artifacts.save(report)
    typed_calls = []

    def verify(*args):
        assert row["cleanup_accepted"] is True
        typed_calls.append(1)
        if not typed_pass:
            return SimpleNamespace(passed=False, value=None)
        result = evidence.verify_agent_session_run_evidence(*args)
        result.status = "pass"
        return result

    monkeypatch.setattr(managed_pipeline, "verify_agent_session_run_evidence", verify)
    monkeypatch.setattr(managed_pipeline, "validate_installed_package", lambda *_, **__: None)
    monkeypatch.setattr(managed_evaluation, "project", lambda *_, **__: {
        "terminal_observation": {"status": "observed", "result": "loss"},
        "operation_end": {"status": "task_ended", "tainted": False, "controller": "released"}})
    monkeypatch.setattr(managed_evaluation, "read_reference_metrics", lambda *_, **__: {
        "max_observed_floor": 2})
    executor._finish_original(row, artifacts, run, expected, tmp_path / "model", "runtime")
    assert row["status"] == "verification_pending" and not typed_calls
    assert "agent_task_outcome" not in row and "typed_verification" not in row
    executor._preserve(row, artifacts, checkpoint, run, expected, {}, artifacts.root / "private")
    row.update(execution_status=row["status"], execution_reason=row["reason"],
               status="save_pending")
    checkpoint()
    refused = executor.preserve_report(artifacts.root / "report.json")
    assert refused["episodes"][0]["save_error"] == "closed_pending_original_required"
    assert not typed_calls
    row.update(status="save_pending", cleanup_accepted=True)
    row["finish"] = artifacts.put("finish.json", json_bytes({"phase": "closed"}))
    checkpoint()
    saved = executor.preserve_report(artifacts.root / "report.json")
    final = saved["episodes"][0]
    assert len(typed_calls) == 1
    if typed_pass:
        assert final["status"] == final["execution_status"] == "finished"
        assert result_row(final, artifacts.root / "report.json")["natural_result"] == "loss"
        assert "stored_evidence_id" in final and len(calls) >= 2
    else:
        assert final["status"] == final["execution_status"] == "unknown"
        assert saved["status"] == "stopped"
        assert saved["stop_reason"] == "typed_original_verification_failed"
        assert "stored_failure_id" in final
        assert "stored_evidence_id" not in final and "trajectory" not in final
        receipt = json.loads((artifacts.root / final["failure_preservation"]["path"]).read_bytes())
        assert receipt["full_readback"] and receipt["typed_recording_qualified"] is False
    assert (run / "global-original.bin").read_bytes() == (
        b"full input, catalog, scores and Receipt\r\n")


def test_evaluation_trajectory_admission_requires_original_and_matching_shard_readback(
        tmp_path, monkeypatch):
    """Real Store/shard transfer; synthetic episode claims no native qualification."""
    import hashlib
    from pathlib import Path

    from test_global_evidence_store import _setup
    from test_p1_trajectory import episode

    from spireagent.workbench import managed_pipeline
    from spireagent.workbench.evaluation_ledger import result_row

    store, producer, _, expected, model, _, _ = _setup(tmp_path, monkeypatch)
    executor = PipelinedManagedExecutor.__new__(PipelinedManagedExecutor)
    executor.producer, executor.store = producer, store
    executor.peer = open_store(str(tmp_path / "peer"))
    executor.runtime = Path(expected["global_runtime_directory"])
    executor.preparation = {"store": str(tmp_path / "store")}
    executor.configuration = {"models": {"m": {"artifact_id": model.artifact_id}},
        "peer": {"kind": "local", "directory": str(tmp_path / "peer")},
        "retention": {"tier": "trajectory", "purpose": "evaluation", "audit_every": 50},
        "host_pin": {}, "runtime_pin": {},
        "initial_progress": {"progress_sha256": "d" * 64}}
    retention = managed_pipeline.retention_configuration(executor.configuration)
    value = episode(model.artifact_id)
    value["identity"].update(retention=retention, model_sha256=model.payload("weights").sha256,
                             producer=producer.to_dict())
    expected.update(run_id=value["run_id"], agent_artifact_sha256=model.payload("weights").sha256,
                    model_binding={"input_spec": value["identity"]["input_spec"]})
    value.update(score_eligible=True, original_content_id="c" * 64)
    value["outcome"].update(terminal={"status": "observed", "result": "loss"},
                            max_observed_floor=2)
    artifacts = _Artifacts(tmp_path / "attempt")
    (artifacts.root / "private").mkdir()
    row = {"selection_id": "m", "seed": "fresh", "manifest_sha256": "d" * 64,
        "scenario": {"character": "Defect", "ascension": 0}, "run_id": value["run_id"],
        "status": "trajectory_pending", "execution_status": "finished",
        "execution_reason": "natural_terminal", "retention": retention,
        "world_mutation_offered": True, "cleanup_accepted": True,
        "formal_audit_selected": False, "ledger_slot_id": "synthetic-slot",
        "game_metrics": {"max_observed_floor": 2},
        "agent_task_outcome": {"terminal_observation": value["outcome"]["terminal"]}}
    row["finish"] = artifacts.put("finish.json", json_bytes({"phase": "closed"}))
    row["typed_verification"] = artifacts.put("typed-verification.json", json_bytes({
        "status": "pass", "content_id": value["original_content_id"], "expected": expected}))
    row["trajectory"] = artifacts.put("trajectory.json", json_bytes(value))
    report = {"episodes": [row], "status": "running"}
    executor._write_job(row, artifacts, lambda: artifacts.save(report),
        expected, mode="sealed")
    path = artifacts.root / "report.json"
    with pytest.raises(BoundaryError, match="previous_offered_attempt_requires_reconciliation"):
        result_row({**row, "status": "finished"}, path)
    [(saved_path, saved)] = executor.finalize_trajectories([path])
    final = saved["episodes"][0]
    assert saved_path == path and final["status"] == "finished"
    assert result_row(final, path)["natural_result"] == "loss"
    receipt_path = artifacts.root / final["trajectory_preservation"]["path"]
    original = receipt_path.read_bytes()
    receipt = json.loads(original)
    receipt["episode_sha256"] = "0" * 64
    changed = json_bytes(receipt)
    receipt_path.write_bytes(changed)
    damaged = {**final, "trajectory_preservation": {**final["trajectory_preservation"],
        "bytes": len(changed), "sha256": hashlib.sha256(changed).hexdigest()}}
    with pytest.raises(BoundaryError, match="evaluation_trajectory_readback_required"):
        result_row(damaged, path)
    receipt_path.write_bytes(original)
    with pytest.raises(BoundaryError):
        result_row({**final, "formal_audit_selected": True}, path)
    with pytest.raises(BoundaryError, match="evaluation_trajectory_intent_required"):
        result_row({**final, "execution_status": "unknown"}, path)

    # Repack changed identities through the real Store and reader, so matching
    # shard hashes alone cannot qualify the wrong seed or model.
    import copy

    other_model = Manifest("model", Producer("other-model", "e" * 40, "f" * 64),
                           payloads=(model.payload("weights"),))
    store.publish(other_model)
    for field, replacement in (("seed", "foreign-seed"),
                               ("model_artifact_id", other_model.artifact_id)):
        changed_episode = copy.deepcopy(value)
        changed_episode["identity"][field] = replacement
        writer = _Artifacts.reopen(artifacts.root)
        next_row = {**final, "status": "trajectory_pending",
                    "trajectory": writer.put("trajectory.json", json_bytes(changed_episode))}
        writer.save({"episodes": [next_row], "status": "running"})
        [(_, wrong_report)] = executor.finalize_trajectories([path])
        with pytest.raises(BoundaryError, match="evaluation_trajectory_identity_changed"):
            result_row(wrong_report["episodes"][0], path)
