"""Real dispatcher/artifact/store seam; scripted World transport and R2 receipt."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
from types import SimpleNamespace

import pytest
from test_reference_suite import Clock, OwnerClient

from spireagent.instance_lock import instance_lock
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.workbench import evaluation_ledger as owner
from spireagent.workbench import reference_suite
from spireagent.workbench.native_episode_binding import checked_context
from stpd.evaluation.suites import checkpoint_reference_manifest

PEER = {"kind": "local", "path": "/fixture-peer"}


class CheckpointClient(OwnerClient):
    planned = 10

    def prepare(self, body, timeout):
        super().prepare(body, timeout)
        self.context["binding"].update(
            scenario={"character": body["character"], "ascension": body["ascension"]},
            initialization="fresh_seeded_reference_scenario")
        self.context = checked_context(self.context)
        self.resource["admission_context"] = self.context
        self.resource["preparation_outcome"] = "completed"
        return copy.deepcopy(self.resource)

    def preserve(self, run_id, peer, timeout):
        return {"status": "completed", "operation_id": "preserve-" + run_id,
                "run_id": run_id, "peer": peer, "artifact_id": "f" * 64,
                "peer_readback": {"full_readback": True, "typed_verification": "pass"}}

    def finish(self, ref, timeout):
        result = super().finish(ref, timeout)
        self.resource = copy.deepcopy(result)
        return result

    def metrics(self, evaluation, context):
        return {"max_observed_floor": 12}


@pytest.fixture
def imported(tmp_path):
    manifest = checkpoint_reference_manifest("v0.111.0", "Defect", 0)
    path = tmp_path / "manifest.json"
    path.write_bytes(json_bytes(manifest))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    attempt = tmp_path / "original"

    class OriginalClient(CheckpointClient):
        def finish(self, ref, timeout):
            self.fault = "cleanup_unknown" if self.number == 2 else None
            return super().finish(ref, timeout)

    client, clock = OriginalClient(attempt), Clock()
    report = reference_suite.run_suite(
        path, sha, client=client, run_dir=attempt, selection="",
        selections={"1460": "existing-agent"}, run_profile="short",
        limits=reference_suite.SuiteLimits(2, 2, 2, 20, .01), max_episodes=2,
        evidence_peer=PEER, clock=clock, sleep=clock.sleep)
    assert [r["status"] for r in report["episodes"][:2]] == ["finished", "unknown"]
    matrix = tmp_path / "matrix.json"
    matrix.write_bytes(json_bytes({"planned": 10, "groups": [{
        "character": "Defect", "ascension": 0, "manifest_path": str(path),
        "manifest_sha256": sha, "selections": {"1460": "existing-agent"}}]}))
    episodes = tmp_path / "episodes.csv"
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=["character", "ascension", "seed", "step",
        "seed_ordinal", "selection_id", "status", "run_id", "natural_result",
        "stored_evidence_id", "original_report", "reason"])
    writer.writeheader()
    for row in report["episodes"]:
        writer.writerow({"character": "Defect", "ascension": 0, "step": "1460",
            "seed": row["seed"], "seed_ordinal": row["seed_ordinal"],
            "selection_id": row["selection_id"], "status": row["status"],
            "run_id": row.get("run_id", ""), "natural_result": "loss"
                if row["status"] == "finished" else "",
            "stored_evidence_id": row.get("stored_evidence_id", ""),
            "original_report": str(attempt / "report.json"), "reason": row.get("reason")})
    episodes.write_text(stream.getvalue())
    config = SimpleNamespace(state_dir=tmp_path / "same-original-state")
    ledger = owner.EvaluationLedger.import_matrix(tmp_path / "ledger", matrix, episodes, config)
    return ledger, config, manifest, matrix, episodes


def release(ledger, config, monkeypatch):
    row = next(r for r in ledger.slots() if r["status"] == "unknown")
    expected = {"expected_run_id": row["run_id"],
                "expected_environment_ref": row["environment_ref"]}
    monkeypatch.setattr(owner, "native_episode_command", lambda *_a, **_k: {
        "result": "unknown", "released": True, "expected": expected})
    ledger.seal_unknown(config, expected)


def transport(ledger, monkeypatch):
    client = CheckpointClient(None)
    client.number = 100
    client.planned = 1
    monkeypatch.setattr(owner, "WorkbenchReferenceClient", lambda _config: client)
    actual = reference_suite.run_suite

    def dispatch(*args, **kwargs):
        client.run_dir = kwargs["run_dir"]
        clock = Clock()
        return actual(*args, **kwargs, clock=clock, sleep=clock.sleep)

    monkeypatch.setattr(owner, "run_suite", dispatch)
    return client


def test_import_then_restart_never_reoffers_completed_or_sealed_unknown(imported, monkeypatch):
    ledger, config, manifest, matrix, episodes = imported
    originals = matrix.read_bytes(), episodes.read_bytes()
    assert ledger.run(config)["stop_reason"] == "unknown_requires_explicit_seal"
    release(ledger, config, monkeypatch)
    client = transport(ledger, monkeypatch)
    limits = reference_suite.SuiteLimits(2, 2, 2, 20, .01)
    ledger.run(config, max_episodes=1, limits=limits, cool=False)
    reopened = owner.EvaluationLedger(ledger.directory)
    assert reopened.report()["loss"] == 2
    assert reopened.report()["unknown"] == reopened.report()["unknown_sealed"] == 1
    assert reopened.run(config, limits=limits, cool=False)["status"] == "completed"
    final = reopened.report()
    assert final["planned"] == 10 and final["loss"] == 9 and final["unknown"] == 1
    assert final["not_started"] == 0 and client.calls.count("submit") == 8
    offered = [json.loads((p / "report.json").read_bytes())["episodes"][0]["seed"]
               for p in (ledger.directory / "attempts").iterdir()]
    assert set(offered) == set(manifest["seeds"][2:])
    assert (matrix.read_bytes(), episodes.read_bytes()) == originals


def test_low_space_stays_not_started_and_never_calls_prepare(imported, monkeypatch):
    ledger, config, *_ = imported
    release(ledger, config, monkeypatch)
    client = transport(ledger, monkeypatch)

    def no_space(*_args):
        raise BoundaryError("p1_storage", "workspace_capacity_insufficient")

    monkeypatch.setattr(reference_suite, "require_long_task_space", no_space)
    result = ledger.run(config, cool=False)
    assert result["stop_reason"] == "workspace_capacity_insufficient"
    assert ledger.report()["not_started"] == 8 and ledger.report()["unknown"] == 1
    assert "prepare" not in client.calls


def test_new_unknown_stops_and_restart_does_not_offer_again(imported, monkeypatch):
    ledger, config, *_ = imported
    release(ledger, config, monkeypatch)
    client = transport(ledger, monkeypatch)
    client.fault = "cleanup_unknown"
    ledger.run(config, cool=False)
    assert client.calls.count("prepare") == 1
    assert ledger.report()["unknown"] == 2 and ledger.report()["not_started"] == 7
    assert owner.EvaluationLedger(ledger.directory).run(config)["stop_reason"] == (
        "unknown_requires_explicit_seal")
    assert client.calls.count("prepare") == 1


def test_abandoned_reservation_cannot_become_a_fresh_offer(imported, monkeypatch):
    ledger, config, *_ = imported
    release(ledger, config, monkeypatch)
    row = next(r for r in ledger.slots() if r["status"] == "not_started")
    ledger.event(row["slot_id"], "reserved", report_path=str(ledger.directory / "missing.json"))
    assert owner.EvaluationLedger(ledger.directory).run(config)["stop_reason"] == (
        "unknown_requires_explicit_seal")
    assert ledger.report()["unknown"] == 2


def test_changed_original_bytes_reject_without_any_game(imported):
    ledger, _config, _manifest, _matrix, episodes = imported
    episodes.write_text(episodes.read_text() + "\n")
    with pytest.raises(BoundaryError, match="original_reference_changed"):
        owner.EvaluationLedger(ledger.directory)


def test_crash_between_finish_and_R2_receipt_becomes_unknown_not_permanently_reserved(
    imported, monkeypatch,
):
    ledger, config, *_ = imported
    release(ledger, config, monkeypatch)
    row = next(r for r in ledger.slots() if r["status"] == "not_started")
    path = ledger.directory / "abandoned-report.json"
    path.write_bytes(json_bytes({"status": "running", "episodes": [{
        "status": "finished", "run_id": "run-original-publish-gap",
        "environment_ref": "b" * 32, "world_mutation_offered": True}]}))
    ledger.event(row["slot_id"], "reserved", report_path=str(path))
    assert ledger.run(config)["stop_reason"] == "unknown_requires_explicit_seal"
    report = ledger.report()
    assert report["reserved"] == 0 and report["unknown"] == 2
    assert any(r.get("run_id") == "run-original-publish-gap" and r["status"] == "unknown"
               for r in report["slots"])


def test_status_is_readable_while_the_long_running_writer_holds_its_lock(imported, capsys):
    ledger, *_ = imported
    before = ledger.store.keys("events/")
    with instance_lock(ledger.directory.parent / (ledger.directory.name + ".lock")):
        assert owner.main(["status", "--ledger", str(ledger.directory)]) == 0
        assert owner.main(["report", "--ledger", str(ledger.directory)]) == 0
    assert ledger.store.keys("events/") == before
    assert '"planned": 10' in capsys.readouterr().out


def test_fresh_unstarted_matrix_uses_explicit_conditions_without_a_fake_run(tmp_path):
    manifest = checkpoint_reference_manifest("v0.111.0", "Defect", 10)
    path = tmp_path / "manifest.json"
    path.write_bytes(json_bytes(manifest))
    matrix = tmp_path / "matrix.json"
    conditions = {"run_profile": "extended", "display_mode": "native-ui",
                  "speed": "fast", "evidence_peer": PEER}
    frozen = {"planned": 2, "conditions": conditions, "groups": [{
        "character": "Defect", "ascension": 10, "manifest_path": str(path),
        "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "selections": {"new-checkpoint": "registered-candidate"}}]}
    matrix.write_bytes(json_bytes(frozen))
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=["character", "ascension", "seed", "step",
        "seed_ordinal", "selection_id", "status", "run_id", "stored_evidence_id",
        "original_report"])
    writer.writeheader()
    for ordinal, seed in enumerate(manifest["seeds"], 1):
        writer.writerow({"character": "Defect", "ascension": 10, "seed": seed,
            "step": "new-checkpoint", "seed_ordinal": ordinal,
            "selection_id": "registered-candidate", "status": "not_started"})
    episodes = tmp_path / "episodes.csv"
    episodes.write_text(stream.getvalue())
    config = SimpleNamespace(state_dir=tmp_path / "normal-state")
    before = matrix.read_bytes(), episodes.read_bytes()
    ledger = owner.EvaluationLedger.import_matrix(tmp_path / "ledger", matrix, episodes, config)
    assert ledger.plan["conditions"] == conditions
    assert ledger.report()["not_started"] == 2
    assert all(not row.get("run_id") for row in ledger.slots())
    assert (matrix.read_bytes(), episodes.read_bytes()) == before
    assert owner.EvaluationLedger(ledger.directory).report()["not_started"] == 2
    # No invented previous run, no missing-condition fallback and no invalid
    # operating mode is admitted by the new preparation path.
    frozen.pop("conditions")
    matrix.write_bytes(json_bytes(frozen))
    with pytest.raises(BoundaryError, match="original_conditions_required"):
        owner.EvaluationLedger.import_matrix(tmp_path / "no-conditions", matrix, episodes, config)
    frozen["conditions"] = {**conditions, "display_mode": "guessed-display"}
    matrix.write_bytes(json_bytes(frozen))
    with pytest.raises(BoundaryError, match="invalid_declared_conditions"):
        owner.EvaluationLedger.import_matrix(tmp_path / "bad-conditions", matrix, episodes, config)


def test_new_conditions_cannot_override_historical_runs(imported):
    _, config, _, matrix, episodes = imported
    frozen = json.loads(matrix.read_bytes())
    frozen["conditions"] = {"run_profile": "extended", "display_mode": "native-ui",
                            "speed": "fast", "evidence_peer": PEER}
    matrix.write_bytes(json_bytes(frozen))
    with pytest.raises(BoundaryError, match="declared_conditions_differ_from_originals"):
        owner.EvaluationLedger.import_matrix(matrix.parent / "changed-conditions",
                                            matrix, episodes, config)
