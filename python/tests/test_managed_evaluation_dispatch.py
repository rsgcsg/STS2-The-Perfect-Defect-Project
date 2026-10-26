"""Exercise the shared dispatcher, durable slots and isolated worker artifact writes.

The fake executor replaces only Host/Runtime/evidence IO; no game equivalence or
model speed is claimed by these queue tests. Real P1 runs cover that boundary.
"""

from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_reference_suite import suite_manifest

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.workbench.evaluation_ledger import EvaluationLedger
from spireagent.workbench.reference_suite import SuiteLimits, load_manifest


@pytest.fixture
def ledger(tmp_path):
    path, _ = suite_manifest(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["host"] = "Managed"
    path.write_bytes(json_bytes(manifest))
    execution = tmp_path / "execution.json"
    execution.write_bytes(
        json_bytes(
            {
                "schema": "spireagent/managed-evaluation-execution-v1",
                "models": {"same-weight": {}},
                "peer": {"kind": "local", "directory": str(tmp_path)},
                "minimum_free_bytes": 0,
            }
        )
    )
    return EvaluationLedger.create(
        tmp_path / "ledger",
        path,
        hashlib.sha256(path.read_bytes()).hexdigest(),
        selections={"438": "same-weight"},
        execution=execution,
    )


def complete(row, artifacts, checkpoint):
    row.update(
        status="finished",
        reason="natural_terminal",
        run_id=row["intent_id"],
        stored_evidence_id="f" * 64,
        world_mutation_offered=True,
        actor_started=True,
        cleanup_accepted=True,
        agent_task_outcome={"terminal_observation": {"status": "observed", "result": "loss"}},
    )
    row["finish"] = artifacts.put("finish.json", json_bytes({"phase": "closed"}))
    row["preservation"] = artifacts.put(
        "preservation.json",
        json_bytes(
            {
                "status": "completed",
                "run_id": row["run_id"],
                "artifact_id": "f" * 64,
                "peer_readback": {"full_readback": True, "typed_verification": "pass"},
            }
        ),
    )
    checkpoint()


def test_parallel_reservations_precede_executor_and_restart_never_reoffers(ledger):
    barrier = threading.Barrier(3)
    seen = []
    mutex = threading.Lock()

    def executor(row, manifest, artifacts, checkpoint):
        current = EvaluationLedger(ledger.directory).slots()
        slot = next(item for item in current if item["seed"] == row["seed"])
        assert slot["status"] == "reserved"
        with mutex:
            seen.append(row["seed"])
        barrier.wait(timeout=10)
        complete(row, artifacts, checkpoint)

    result = ledger.run_managed(
        workers=3, max_episodes=3, executor=executor, limits=SuiteLimits(10, 10, 10, 60)
    )
    assert result["new_finished"] == 3
    reopened = EvaluationLedger(ledger.directory)
    assert reopened.report()["loss"] == 3
    assert reopened.report()["not_started"] == 17
    second = []

    def rest(row, manifest, artifacts, checkpoint):
        second.append(row["seed"])
        complete(row, artifacts, checkpoint)

    reopened.run_managed(workers=2, max_episodes=2, executor=rest)
    assert not set(seen) & set(second)
    assert reopened.report()["loss"] == 5
    assert (
        len(
            {
                event["slot_id"]
                for key in reopened.store.keys("events/")
                if (event := json.loads(reopened.store.get(key)))["kind"] == "reserved"
            }
        )
        == 5
    )


def test_unknown_is_one_slot_neighbors_continue_and_restart_never_reoffers_it(ledger):
    barrier = threading.Barrier(2)
    release_neighbor = threading.Event()

    def executor(row, manifest, artifacts, checkpoint):
        if row["seed_ordinal"] <= 2:
            barrier.wait(timeout=10)
        if row["seed_ordinal"] == 1:
            row.update(
                world_mutation_offered=True,
                cleanup_accepted=True,
                status="unknown",
                reason="lost_step_reply",
            )
            checkpoint()
        elif row["seed_ordinal"] == 2:
            assert release_neighbor.wait(timeout=10)
            complete(row, artifacts, checkpoint)
        else:
            complete(row, artifacts, checkpoint)

    with ThreadPoolExecutor(1) as coordinator:
        future = coordinator.submit(
            ledger.run_managed, workers=2, max_episodes=5, executor=executor
        )
        # Wait on the durable unknown event, not on a guessed scheduler delay.
        deadline = threading.Event()
        for _ in range(1000):
            if ledger.report()["unknown"] == 1:
                break
            deadline.wait(0.005)
        assert ledger.report()["unknown"] == 1
        release_neighbor.set()
        result = future.result(timeout=10)
    assert result["status"] == "completed"
    assert result["dispatched"] == 5
    assert ledger.report()["unknown"] == 1
    assert ledger.report()["loss"] == 4
    assert ledger.report()["not_started"] == 15
    assert (
        sum(
            json.loads(ledger.store.get(key))["kind"] == "reserved"
            for key in ledger.store.keys("events/")
        )
        == 5
    )
    later = []

    def next_slot(row, manifest, artifacts, checkpoint):
        later.append(row["seed_ordinal"])
        complete(row, artifacts, checkpoint)

    ledger.run_managed(workers=1, max_episodes=1, executor=next_slot)
    assert later == [6]
    assert ledger.report()["unknown"] == 1


def test_unconfirmed_process_cleanup_stops_admission_without_retrying_slot(ledger):
    seen = []

    def executor(row, manifest, artifacts, checkpoint):
        seen.append(row["seed_ordinal"])
        row.update(world_mutation_offered=True, status="unknown",
                   reason="host_cleanup_unconfirmed", cleanup_accepted=False)
        checkpoint()

    result = ledger.run_managed(workers=1, max_episodes=5, executor=executor)
    assert result["stop_reason"] == "unknown_cleanup_requires_owner_read"
    assert seen == [1]
    assert ledger.report()["unknown"] == 1
    assert ledger.report()["not_started"] == 19


def test_crashed_reservation_is_unknown_and_blocks_new_admission(ledger):
    slot = ledger.slots()[0]
    ledger.event(slot["slot_id"], "reserved", report_path=str(ledger.directory / "missing.json"))
    result = ledger.run_managed(executor=lambda *_: pytest.fail("must not dispatch"))
    assert result["stop_reason"] == "unknown_cleanup_requires_owner_read"
    assert ledger.report()["unknown"] == 1 and ledger.report()["not_started"] == 19


def test_frozen_execution_and_host_cannot_change(ledger):
    execution = ledger.plan["execution"]
    with open(execution["path"], "ab") as stream:
        stream.write(b" ")
    with pytest.raises(BoundaryError, match="original_reference_changed"):
        EvaluationLedger(ledger.directory)
    path = ledger.plan["slots"][0]["manifest"]["path"]
    _, manifest = load_manifest(Path(path), ledger.plan["slots"][0]["manifest"]["sha256"])
    assert manifest["host"] == "Managed"


def test_stale_producer_cannot_label_new_evidence_or_start_host(monkeypatch):
    from spireagent.workbench import managed_evaluation

    actual = Producer("rsgcsg/STS2-The-Perfect-Defect-Project", "b" * 40, "c" * 64)
    monkeypatch.setattr(managed_evaluation, "source_identity", lambda root: actual)
    expected = {**actual.to_dict(), "source_revision": "a" * 40}
    # Deliberately no Host configuration: rejection must precede even auditing
    # a candidate, creating a Store or launching a process.
    with pytest.raises(BoundaryError, match="frozen_execution_source_changed"):
        managed_evaluation.ManagedEpisodeExecutor(
            {"producer": expected}, episode_seconds=10, cleanup_seconds=10
        )


@pytest.mark.parametrize("handoff", [
    {"mode": "human", "tainted": False},
    {"mode": "auto", "tainted": True},
])
def test_live_http_runtime_is_stopped_once_after_handoff_not_waited_to_deadline(
    tmp_path, monkeypatch, handoff
):
    # Scheduling fixture only. The existing real CLI test covers transport,
    # exact-run headers, Stop sealing and actual process exit.
    from spireagent.workbench import managed_evaluation
    from spireagent.workbench.reference_suite import _Artifacts

    artifacts = _Artifacts(tmp_path / "attempt")
    stdout = tmp_path / "stdout"
    startup = {"schema": "sts2.policy-runtime/agent-session-startup-2",
               "run_id": "owned-run", "address": "http://127.0.0.1:12345"}
    stdout.write_bytes(json_bytes(startup))
    calls = []

    class Process:
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            assert calls == [("/status", None), ("/stop", {})]
            assert timeout == 3
            self.returncode = 0

    process = Process()

    class Client:
        def request(self, route, body=None):
            calls.append((route, body))
            state = {"lifecycle": "running", "autonomy_budget": {"state": "inactive"},
                     **handoff}
            return {"status": state}

    monkeypatch.setattr(managed_evaluation, "RuntimeClient", lambda address, original: Client())
    row = {}
    observed = managed_evaluation._finish_runtime(
        process, stdout, row, artifacts, managed_evaluation.time.monotonic() + 1800, 3
    )
    assert observed == startup
    assert row["run_id"] == "owned-run"
    assert "runtime_stop" in row and "wall_limit_reached" not in row


@pytest.mark.parametrize("failed_route", ["/status", "/stop"])
def test_original_runtime_exit_racing_http_is_read_without_reposting_stop(
    tmp_path, monkeypatch, failed_route
):
    from spireagent.workbench import managed_evaluation
    from spireagent.workbench.reference_suite import _Artifacts

    artifacts = _Artifacts(tmp_path / "attempt")
    stdout = tmp_path / "stdout"
    startup = {"schema": "sts2.policy-runtime/agent-session-startup-2",
               "run_id": "owned-run", "address": "http://127.0.0.1:12345"}
    stdout.write_bytes(json_bytes(startup))
    calls = []

    class Process:
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            assert self.returncode == 0
            return self.returncode

    process = Process()

    class Client:
        def request(self, route, body=None):
            calls.append(route)
            if route == failed_route:
                process.returncode = 0
                raise BoundaryError("local_model", "runtime_command_unknown")
            return {"status": {"lifecycle": "running", "mode": "human", "tainted": False,
                               "autonomy_budget": {"state": "inactive"}}}

    monkeypatch.setattr(managed_evaluation, "RuntimeClient", lambda address, original: Client())
    row = {}
    assert managed_evaluation._finish_runtime(
        process, stdout, row, artifacts, managed_evaluation.time.monotonic() + 1800, 3
    ) == startup
    assert calls == (["/status"] if failed_route == "/status" else ["/status", "/stop"])
    assert "runtime_http_error" in row
