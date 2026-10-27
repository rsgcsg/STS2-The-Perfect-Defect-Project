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
