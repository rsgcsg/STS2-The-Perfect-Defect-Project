"""Exercise the shared dispatcher, durable slots and isolated worker artifact writes.

The fake executor replaces only Host/Runtime/evidence IO; no game equivalence or
model speed is claimed by these queue tests. Real P1 runs cover that boundary.
"""

from __future__ import annotations

import hashlib
import json
import shutil
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
def ledger(tmp_path, request):
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
                "minimum_free_bytes": getattr(request, "param", 0),
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


@pytest.mark.parametrize("ledger", [10 * 1024**3], indirect=True)
def test_parallel_capacity_charges_inflight_peaks_and_drains_before_readmission(
    ledger, monkeypatch
):
    gib = 1024**3
    # Frozen floor 10 GiB, free 14 GiB: exactly two existing 2 GiB peaks.
    monkeypatch.setenv("SPIREAGENT_MIN_FREE_BYTES", str(2 * gib))
    monkeypatch.setattr(shutil, "disk_usage", lambda _: type("Space", (), {"free": 14 * gib})())
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = peak = 0

    def executor(row, manifest, artifacts, checkpoint):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=5)
        complete(row, artifacts, checkpoint)
        with lock:
            active -= 1

    result = ledger.run_managed(workers=4, max_episodes=4, executor=executor)
    assert result["status"] == "completed" and result["dispatched"] == 4
    assert peak == 2
    assert ledger.report()["loss"] == 4 and ledger.report()["not_started"] == 16


def test_capacity_stop_preserves_next_slot_and_restart_uses_only_not_started(ledger, monkeypatch):
    gib = 1024**3
    free = 4 * gib
    monkeypatch.setenv("SPIREAGENT_MIN_FREE_BYTES", str(2 * gib))
    monkeypatch.setattr(shutil, "disk_usage", lambda _: type("Space", (), {"free": free})())
    seen = []
    def executor(row, manifest, artifacts, checkpoint):
        nonlocal free
        seen.append(row["seed_ordinal"])
        complete(row, artifacts, checkpoint)
        free = 3 * gib

    result = ledger.run_managed(workers=4, max_episodes=4, executor=executor)
    assert result["dispatched"] == 1 and result["stop_reason"] == "workspace_capacity_insufficient"
    assert ledger.report()["not_started"] == 19 and ledger.report()["unknown"] == 0
    free = 4 * gib
    EvaluationLedger(ledger.directory).run_managed(workers=4, max_episodes=4, executor=executor)
    assert seen == [1, 2]
    # A higher existing workstation reserve still applies to a zero-floor plan.
    monkeypatch.setenv("SPIREAGENT_MIN_FREE_BYTES", str(3 * gib))
    free = 4 * gib
    result = ledger.run_managed(executor=executor)
    assert result["stop_reason"] == "workspace_capacity_insufficient" and seen == [1, 2]


def test_finished_bounded_batch_does_not_require_capacity_for_an_unrequested_next_slot(
    ledger, monkeypatch
):
    gib = 1024**3
    free = 4 * gib
    monkeypatch.setenv("SPIREAGENT_MIN_FREE_BYTES", str(2 * gib))
    monkeypatch.setattr(shutil, "disk_usage", lambda _: type("Space", (), {"free": free})())

    def executor(row, manifest, artifacts, checkpoint):
        nonlocal free
        complete(row, artifacts, checkpoint)
        free = 3 * gib

    result = ledger.run_managed(workers=4, max_episodes=1, executor=executor)
    assert result["status"] == "completed" and result["stop_reason"] is None
    assert result["dispatched"] == 1 and ledger.report()["not_started"] == 19


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


def test_repeated_pinned_host_client_loads_do_not_mutate_package_hash(tmp_path):
    from spireagent.package_identity import directory_sha256
    from spireagent.workbench.managed_evaluation import _load_host_api

    # The actual shipped consumer, including its dataclasses and standard-library
    # imports. Package pins must survive both the first and subsequent load.
    original = Path(__file__).resolve().parents[2] / (
        "components/host-runtime/consumers/python/sts2_headless/managed_service.py"
    )
    source = tmp_path / "managed_service.py"
    source.write_bytes(original.read_bytes())
    expected = directory_sha256(tmp_path)
    for _ in range(2):
        api = _load_host_api(source)
        assert api.ManagedHostServiceClient and api.ManagedHostServiceManager
        assert directory_sha256(tmp_path) == expected
    assert not (tmp_path / "__pycache__").exists()


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


@pytest.mark.parametrize("limit", [None, True, 0, -1, 10001, "32"])
def test_invalid_frozen_runtime_budget_rejects_before_a_host_or_model_is_read(limit):
    from spireagent.workbench.managed_evaluation import ManagedEpisodeExecutor

    with pytest.raises(BoundaryError, match="invalid_runtime_submission_budget"):
        ManagedEpisodeExecutor({"max_auto_submissions": limit},
                               episode_seconds=10, cleanup_seconds=10)


@pytest.mark.parametrize("version, admitted", [
    ("0.1.0-rc.47", False), ("0.1.0-rc.50", True),
    ("0.1.0-rc.51", True), ("0.1.0-rc.52", True),
])
def test_central_inference_requires_cli_environment_before_world_audit(
    tmp_path, monkeypatch, version, admitted
):
    from spireagent.workbench import managed_evaluation as backend

    actual = Producer("rsgcsg/STS2-The-Perfect-Defect-Project", "b" * 40, "c" * 64)
    monkeypatch.setattr(backend, "source_identity", lambda _: actual)
    monkeypatch.setattr(backend, "_verify_host", lambda *_: None)
    monkeypatch.setattr(backend, "validate_installed_package", lambda *_, **__: None)
    def audit(*_):
        if not admitted:
            pytest.fail("must reject before world audit")
        raise RuntimeError("admitted_runtime_before_world_audit")

    monkeypatch.setattr(backend, "_audit", audit)
    (tmp_path / "package.json").write_bytes(json_bytes({"version": version}))
    configuration = {"producer": actual.to_dict(), "settling_timeout_ms": None,
        "inference": {"mode": "central_batch"},
        "host_directory": str(tmp_path), "candidate_directory": str(tmp_path),
        "runtime_directory": str(tmp_path / "dist"), "host_pin": {}, "runtime_pin": {},
        "agent_adapter": backend.export.adapter_identity()}
    error = RuntimeError if admitted else BoundaryError
    reason = ("admitted_runtime_before_world_audit" if admitted
              else "runtime_explicit_adapter_environment_required")
    with pytest.raises(error, match=reason):
        backend.ManagedEpisodeExecutor(configuration, episode_seconds=10, cleanup_seconds=10)

def test_phase_cost_keeps_failed_elapsed_and_disjoint_nodes(monkeypatch):
    """A preservation failure retains its cost without inventing unvisited nodes."""
    from spireagent.workbench import managed_evaluation as backend

    ticks = iter([0.0, 3.0, 3.0, 8.0, 8.0, 10.0])
    monkeypatch.setattr(backend.time, "monotonic", lambda: next(ticks))
    row = {}
    with backend._phase(row, "execute"):
        pass
    with pytest.raises(ValueError, match="original failure"), backend._phase(row, "preserve"):
        raise ValueError("original failure")
    with backend._phase(row, "close"):
        pass
    assert row["phase_seconds"] == {"execute": 3.0, "preserve": 5.0, "close": 2.0}
    assert sum(row["phase_seconds"].values()) == 10.0
    assert "finish" not in row["phase_seconds"]


@pytest.mark.parametrize("limit", [True, 0, -1, 1.5, "120000", 2**53])
def test_settling_budget_is_validated_before_reading_packages(limit):
    from spireagent.workbench.managed_evaluation import ManagedEpisodeExecutor

    with pytest.raises(BoundaryError, match="invalid_settling_timeout"):
        ManagedEpisodeExecutor({"settling_timeout_ms": limit},
                               episode_seconds=10, cleanup_seconds=10)


def test_clean_technical_settling_stop_remains_unknown_even_with_typed_preservation():
    from spireagent.workbench.managed_evaluation import _outcome_status

    outcome = {"terminal_observation": {"status": "not_observed"},
               "operation_end": {"status": "censored", "controller": "released", "tainted": False}}
    assert _outcome_status(outcome, settling_stalled=False)["status"] == "censored"
    assert _outcome_status(outcome, settling_stalled=True) == {
        "status": "unknown", "reason": "managed_settling_stalled"}
    outcome["terminal_observation"] = {"status": "observed", "result": "loss"}
    outcome["operation_end"]["status"] = "task_ended"
    assert _outcome_status(outcome, settling_stalled=False) == {
        "status": "finished", "reason": "natural_terminal"}


@pytest.mark.parametrize("setting", [None, 0, 1, "true"])
def test_host_diagnostics_setting_is_boolean_before_a_world_is_offered(setting):
    from spireagent.workbench.managed_evaluation import ManagedEpisodeExecutor

    with pytest.raises(BoundaryError, match="invalid_host_performance_setting"):
        ManagedEpisodeExecutor({"measure_host_performance": setting},
                               episode_seconds=10, cleanup_seconds=10)


@pytest.mark.parametrize("failure", [TimeoutError("synthetic"),
    BoundaryError("managed", "performance_unavailable")])
def test_optional_host_measurement_failure_does_not_abort_preservation(failure):
    from spireagent.workbench.managed_evaluation import _host_performance

    class Manager:
        def request(self, command):
            assert command["command"] == "performance"
            raise failure

    result = _host_performance(Manager(), {"runtime_instance_id": "owned-runtime",
                                          "game_continuity_id": "owned-episode"})
    assert result == {"type": "performance_unavailable", "status": "unavailable", "error":
                      failure.code if isinstance(failure, BoundaryError) else "TimeoutError"}


def test_original_settling_reason_survives_a_missing_final_http_status():
    from spireagent.workbench.managed_evaluation import _outcome_status

    outcome = {"terminal_observation": {"status": "not_observed"},
               "operation_end": {"status": "failed", "controller": "released", "tainted": True,
                   "recorded_reason": "global_post_delivery_capture:managed_settling_stalled"}}
    assert _outcome_status(outcome, settling_stalled=False) == {
        "status": "unknown", "reason": "managed_settling_stalled"}


def test_new_ledger_freezes_node_store_and_read_concurrency(ledger, monkeypatch, tmp_path):
    from spireagent.workbench import managed_evaluation as backend

    assert Path(ledger.plan["storage"]["store"]).is_absolute()
    assert ledger.plan["storage"]["read_concurrency"] == 8
    assert backend.storage_configuration({"store": "s3", "read_concurrency": 1}) == {
        "store": "s3", "read_concurrency": 1}
    observed = []

    class Executor:
        preparation = {"elapsed_seconds": 2, "model_seconds": 1}

        def __init__(self, configuration, **kwargs):
            observed.append(configuration)

        def __call__(self, row, manifest, artifacts, checkpoint):
            complete(row, artifacts, checkpoint)

        def close(self):
            return None

    from spireagent.workbench import managed_pipeline

    monkeypatch.setattr(managed_pipeline, "PipelinedManagedExecutor", Executor)
    # The frozen node path survives a changed home/environment at dispatch.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "other-home")
    ledger.run_managed(max_episodes=1)
    assert observed[0]["store"] == ledger.plan["storage"]["store"]
    assert json.loads((ledger.directory / "managed-preparations/000001.json").read_bytes()) == (
        Executor.preparation)


@pytest.mark.parametrize("concurrency", [True, 0, -1, 9, 1.5, None])
def test_bad_managed_read_concurrency_fails_before_packages_or_world(concurrency):
    from spireagent.workbench.managed_evaluation import ManagedEpisodeExecutor

    with pytest.raises(BoundaryError, match="invalid_read_concurrency"):
        ManagedEpisodeExecutor({"read_concurrency": concurrency},
                               episode_seconds=10, cleanup_seconds=10)



def test_slot_selection_is_sparse_in_plan_order_and_keeps_full_denominator(ledger):
    original = ledger.store.get("plan.json")
    slots = ledger.slots()
    selected = tuple(slots[i]["slot_id"] for i in (6, 2, 0))
    seen = []

    def executor(row, manifest, artifacts, checkpoint):
        seen.append(row["seed"])
        complete(row, artifacts, checkpoint)

    result = ledger.run_managed(slot_ids=selected, max_episodes=2, executor=executor)
    assert result["dispatched"] == 2
    assert seen == [slots[0]["seed"], slots[2]["seed"]]
    assert ledger.store.get("plan.json") == original
    assert ledger.report()["planned"] == 20 and ledger.report()["not_started"] == 18
    again = ledger.run_managed(slot_ids=selected, executor=executor)
    assert again["dispatched"] == 1
    assert seen[-1] == slots[6]["seed"]
    assert ledger.run_managed(slot_ids=selected, executor=executor)["dispatched"] == 0


@pytest.mark.parametrize("selection", [(), ("f" * 64,), ("duplicate", "duplicate"),
                                       (None,), "not-a-tuple"])
def test_bad_slot_selection_rejects_before_reservation(ledger, selection):
    with pytest.raises(BoundaryError, match="invalid_managed_slot_selection"):
        ledger.run_managed(slot_ids=selection, executor=lambda *_: pytest.fail("no offer"))
    assert not ledger.store.keys("events/")


def test_selected_unknown_is_not_reoffered_and_unselected_cleanup_still_fences(ledger):
    slots = ledger.slots()
    first = slots[0]["slot_id"]
    seen = []

    def unknown(row, manifest, artifacts, checkpoint):
        seen.append(row["seed"])
        row.update(status="unknown", reason="lost_reply", world_mutation_offered=True,
                   cleanup_accepted=True)
        checkpoint()

    ledger.run_managed(slot_ids=(first,), executor=unknown)
    assert ledger.run_managed(slot_ids=(first,), executor=unknown)["dispatched"] == 0
    assert seen == [slots[0]["seed"]] and ledger.report()["unknown"] == 1
    # This global fence cannot be bypassed by selecting some different slot.
    def unconfirmed(row, manifest, artifacts, checkpoint):
        row.update(status="unknown", reason="close_lost", world_mutation_offered=True,
                   cleanup_accepted=False)
        checkpoint()

    ledger.run_managed(slot_ids=(slots[1]["slot_id"],), executor=unconfirmed)
    result = ledger.run_managed(slot_ids=(slots[2]["slot_id"],),
                               executor=lambda *_: pytest.fail("cleanup fence"))
    assert result["stop_reason"] == "unknown_cleanup_requires_owner_read"


def test_cli_forwards_slot_ids_and_rejects_non_managed_actions(ledger, monkeypatch, capsys):
    from spireagent.workbench import evaluation_ledger as module

    selected = ledger.slots()[2]["slot_id"]
    calls = []

    def run(self, **kwargs):
        calls.append(kwargs)
        return {"status": "completed"}

    monkeypatch.setattr(module.EvaluationLedger, "run_managed", run)
    assert module.main(["run", "--host", "managed", "--ledger", str(ledger.directory),
                        "--slot-id", selected]) == 0
    assert calls[0]["slot_ids"] == (selected,)
    assert module.main(["run", "--ledger", str(ledger.directory),
                        "--slot-id", selected]) == 1
    assert "slot_selection_requires_managed_run" in capsys.readouterr().out
    assert module.main(["status", "--host", "managed", "--ledger", str(ledger.directory),
                        "--slot-id", selected]) == 1
    assert len(calls) == 1


class QueuedSaveExecutor:
    """Replace Host/Runtime IO only; real suite/report/ledger coordination stays live."""
    def __init__(self, *, fail_once=False):
        self.offers = 0
        self.saves = 0
        self.fail_once = fail_once

    def __call__(self, row, manifest, artifacts, checkpoint):
        self.offers += 1
        complete(row, artifacts, checkpoint)
        row.update(execution_status="finished", execution_reason="natural_terminal")
        row["save_job"] = artifacts.put("save-job.json", json_bytes({
            "schema": "spireagent/managed-save-job-1", "selection_id": row["selection_id"],
            "retention": row["retention"]}))
        row.update(status="save_pending", reason="waiting_for_save")
        checkpoint()

    def preserve_report(self, path):
        from spireagent.workbench.reference_suite import _Artifacts

        report = json.loads(path.read_bytes())
        row = report["episodes"][0]
        assert row["cleanup_accepted"] is True
        self.saves += 1
        if self.fail_once:
            self.fail_once = False
            row.update(status="save_failed", reason="injected_save_failure")
        else:
            row.update(status="finished", reason="natural_terminal")
        report["status"] = "completed"
        _Artifacts.reopen(path.parent).save(report)
        return report


def test_closed_execution_releases_worker_but_queue_is_bounded_until_save(ledger, monkeypatch):
    from spireagent.workbench import evaluation_ledger as module

    # Capacity is an independent admission dimension, mocked generously here.
    real_usage = shutil.disk_usage(ledger.directory)
    monkeypatch.setattr(module.shutil, "disk_usage",
                        lambda _: real_usage._replace(free=100*1024**3))
    admitted = threading.Event()
    release = threading.Event()
    mutex = threading.Lock()
    executor = QueuedSaveExecutor()
    base_call = executor.__class__.__call__

    class Blocked(QueuedSaveExecutor):
        def __call__(self, row, manifest, artifacts, checkpoint):
            with mutex:
                if self.offers >= 8:
                    assert release.is_set()  # The ninth offer requires drained capacity.
                base_call(self, row, manifest, artifacts, checkpoint)
                if self.offers == 8:
                    admitted.set()

        def preserve_report(self, path):
            assert release.wait(20)
            return super().preserve_report(path)

    executor = Blocked()
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(ledger.run_managed, workers=1, max_episodes=10, executor=executor)
        assert admitted.wait(20)
        snapshot = EvaluationLedger(ledger.directory).report()
        assert snapshot["loss"] == 0 and snapshot["save_pending"] in {7, 8}
        assert not task.done()  # Save tail belongs to the same batch.
        release.set()
        result = task.result(timeout=30)
    assert result["new_finished"] == 10
    assert executor.offers == executor.saves == 10


def test_restart_after_mutable_save_checkpoint_recovers_save_without_an_offer(ledger):
    from spireagent.workbench.reference_suite import _Artifacts

    executor = QueuedSaveExecutor(fail_once=True)
    first = ledger.run_managed(workers=1, max_episodes=1, executor=executor)
    assert first["stop_reason"] == "save_failed_resume_save_only"
    slot = next(s for s in ledger.slots() if s["status"] == "save_failed")
    path = Path(slot["attempt_report"])
    report = json.loads(path.read_bytes())
    report["checkpoint_after_ledger_event"] = True
    report["episodes"][0]["status"] = "save_pending"
    _Artifacts.reopen(path.parent).save(report)
    # This deliberately invalidates the old mutable report SHA.
    assert reference_digest(path) != slot["report"]["sha256"]
    second = EvaluationLedger(ledger.directory).run_managed(
        workers=1, max_episodes=5, executor=executor)
    assert second["dispatched"] == 0
    assert executor.offers == 1 and executor.saves == 2
    assert EvaluationLedger(ledger.directory).report()["loss"] == 1
    slot = next(r for r in EvaluationLedger(ledger.directory).slots()
                if r["status"] == "finished")
    assert slot["reason"] == "natural_terminal"
    assert slot["execution_status"] == "finished"


def reference_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_restart_between_base_close_and_wrapper_checkpoint_keeps_save_job(ledger):
    from spireagent.workbench.reference_suite import _Artifacts

    executor = QueuedSaveExecutor(fail_once=True)
    ledger.run_managed(workers=1, max_episodes=1, executor=executor)
    slot = next(s for s in ledger.slots() if s["status"] == "save_failed")
    path = Path(slot["attempt_report"])
    report = json.loads(path.read_bytes())
    row = report["episodes"][0]
    report["status"] = "running"
    row.update(status="finished", reason="natural_terminal")
    row.pop("execution_status")
    row.pop("execution_reason")
    _Artifacts.reopen(path.parent).save(report)
    result = EvaluationLedger(ledger.directory).run_managed(
        workers=1, max_episodes=1, executor=executor)
    assert result["dispatched"] == 0
    assert executor.offers == 1 and executor.saves == 2
    assert EvaluationLedger(ledger.directory).report()["loss"] == 1


def test_save_only_recovery_does_not_reserve_another_game_peak(ledger, monkeypatch):
    from spireagent.workbench import evaluation_ledger as module

    executor = QueuedSaveExecutor(fail_once=True)
    ledger.run_managed(workers=1, max_episodes=1, executor=executor)
    usage = shutil.disk_usage(ledger.directory)
    monkeypatch.setattr(module, "workspace_reserve_bytes", lambda: 15*1024**3)
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _: usage._replace(free=16*1024**3))
    result = EvaluationLedger(ledger.directory).run_managed(
        workers=1, max_episodes=1, executor=executor)
    assert result["dispatched"] == 0 and result["new_finished"] == 1
    assert executor.offers == 1
    # The exact same free space still rejects a NEW world offer.
    fresh = EvaluationLedger(ledger.directory).run_managed(
        workers=1, max_episodes=1, executor=executor)
    assert fresh["stop_reason"] == "workspace_capacity_insufficient"
    assert executor.offers == 1
