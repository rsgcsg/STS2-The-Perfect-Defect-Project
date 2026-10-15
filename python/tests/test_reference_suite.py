"""Serial suite dispatch over the existing product owner; no native execution."""
from __future__ import annotations

import copy
import hashlib
import json
from io import BytesIO
from types import SimpleNamespace

import pytest

from spireagent.encoding import canonical_json
from spireagent.workbench import local_model_cli


def test_original_request_cli_posts_preserved_request_once_without_regeneration(
    tmp_path, monkeypatch,
):
    config = SimpleNamespace(state_dir=tmp_path)
    monkeypatch.setattr(local_model_cli, "running", lambda _config: {
        "instance_id": "a" * 32, "port": 12345, "control_token": "private-token"})
    request = {"schema": "spireagent/agent-run-request-v1", "selection_id": "existing-agent",
               "admission_context": {"native_episode": {"environment_ref": "b" * 32}},
               "typed_value": True, "non_ascii": "原请求"}
    original = copy.deepcopy(request)
    calls = []

    class Transport:
        def open(self, value, *, timeout):
            calls.append((value.full_url, value.data, timeout))
            return BytesIO(b'{"status":"pending"}')

    monkeypatch.setattr(local_model_cli, "build_opener", lambda *_args: Transport())
    local_model_cli.native_episode_command(
        config, "run-agent", original_request=request, environment_ref="b" * 32,
        expected_instance_id="a" * 32, timeout_seconds=17,
    )
    assert len(calls) == 1
    assert calls[0][:2] == ("http://127.0.0.1:12345/api/local-models/run-agent",
                           canonical_json(original).encode())
    assert 0 < calls[0][2] <= 17
    assert request == original


def suite_manifest(tmp_path):
    from spireagent.json_boundary import json_bytes
    from stpd.game_seed import derive_game_seed

    value = {"schema": "stpd/evaluation-suite-v1", "suite_id": "ref-20", "version": "1.0.0",
             "game_version": "v0.111.0", "character": "Defect", "ascension": 0,
             "host": "Reference", "purpose": "paired_reference", "episode_count": 20,
             "seed_derivation": "derive_game_seed(suite_id, ordinal_1_based)",
             "seeds": [derive_game_seed("ref-20", index) for index in range(1, 21)]}
    raw = json_bytes(value)
    path = tmp_path / "ref-20.json"
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, value):
        self.now += value


class OwnerClient:
    """Scripted transport only; requests use the real owner serializer.

    Full production composition validation, HTTP and process evidence are in
    test_reference_suite_application. This mock does not prove those layers.
    """
    identity = {"application_instance_id": "a" * 32, "configuration_id": "b" * 64}

    def __init__(self, run_dir, fault=None):
        self.run_dir = run_dir
        self.fault = fault
        self.calls = []
        self.number = 0
        self.running = False
        self.resource = {"phase": "idle"}
        self.composition = {"selection_id": "existing-agent",
                            "semantic_definition": {"task_binding": {"id": "task"},
                                                    "program": {"id": "program"}},
                            "implementation": {"manifest_sha256": "c" * 64,
                                               "original_manifest_id": "manifest"}}
        self.composition["composition_id"] = hashlib.sha256(
            canonical_json(self.composition).encode()).hexdigest()

    def readiness(self, selection, timeout):
        self.calls.append("readiness")
        return {"status": "ready_to_load", "selection_id": selection,
                "agent_composition": self.composition}

    def prepare(self, body, timeout):
        from spireagent.workbench.native_episode_binding import checked_context

        self.calls.append("prepare")
        self.number += 1
        ref, generation = f"{self.number:032x}", f"{self.number + 20:032x}"
        binding = {"schema": "spireagent/native-episode-binding-v1", "ownership": "owned",
                   **self.identity, "generation_id": generation,
                   "runtime_instance_id": "native-" + str(self.number),
                   "endpoint_binding_sha256": "c" * 64,
                   "initialization": "fresh_seeded_defect_a0",
                   "installed_tuple": {"game_version": "v0.111.0", "game_commit": "commit",
                       "host_artifact_sha256": "d" * 64, "host_module_version_id": "module",
                       "host_source_revision": "e" * 40, "modset_fingerprint": "f" * 64,
                       "game_assembly_sha256": "1" * 64},
                   "host_package_pin": {"schema": "stpd/platform-host-runtime-pin-v1",
                       "package": "@rsgcsg/sts2-host-runtime", "version": "1.1.0-rc.27",
                       "source_revision": "a" * 40, "component_tree_revision": "b" * 40,
                       "release_asset_sha256": "c" * 64, "package_content_sha256": "d" * 64},
                   "profile": {"template_id": "defect-a0-s0", "template_payload_sha256": "e" * 64,
                               "generation_id": generation, "isolation": "isolated_local_profile"},
                   "presentation": {"requested": "headless", "observed_host_kind": "headless"},
                   "speed": {"requested": "instant", "effective": "instant", "before": "normal",
                             "basis": "native_typed_readback", "configured_default": None},
                   "handoff": {"schema": "sts2.host-runtime/reference-controller-handoff-1",
                               "runtime_instance_id": "native-" + str(self.number),
                               "observed_at": "2026-10-15T00:00:00Z", "controller": None,
                               "basis": "fresh_control_observation_after_close"}}
        self.context = checked_context({"schema": "spireagent/native-episode-admission-v1",
                                        "environment_ref": ref, "binding": binding})
        self.resource = {"schema": "spireagent/native-episode-resource-v1", **self.identity,
                         "environment_ref": ref, "generation_id": generation,
                         "intent_id": body["intent_id"], "preparation": body,
                         "admission_context": self.context, "phase": "ready",
                         "world_mutation_offered": True, "preparation_outcome": "ready"}
        if self.fault == "lost_prepare":
            raise TimeoutError("response lost after original owner preparation")
        return copy.deepcopy(self.resource)

    def native_status(self, timeout):
        self.calls.append("native-status")
        if self.fault == "resource_replaced" and self.running:
            return {**self.resource, "environment_ref": "9" * 32}
        return copy.deepcopy(self.resource)

    def request(self, selection, ref, profile, timeout):
        from spireagent.workbench.agent_run_request import create

        self.calls.append("request")
        return create(self.composition, "prepare-and-takeover", profile,
                      {"max_submissions": 16, "max_policy_calls": 32, "deadline_ms": 60000},
                      "native", native_episode_context=self.context)

    def submit(self, request, ref, timeout):
        from spireagent.json_boundary import json_bytes

        self.calls.append("submit")
        saved = sorted(self.run_dir.glob("*generated-request.json"))[-1].read_bytes()
        assert saved == canonical_json(request).encode()  # Exact HTTP body before offer.
        assert len(json.loads((self.run_dir / "report.json").read_bytes())["episodes"]) == 20
        self.generated = copy.deepcopy(request)
        self.run_id = "run-" + str(self.number)
        self.operation_id = f"{self.number + 40:032x}"
        self.request_raw = json_bytes({**request, "operation_id": self.operation_id})
        self.request_ref = {"path": "requests/original.json", "operation_id": self.operation_id,
                            "sha256": hashlib.sha256(self.request_raw).hexdigest(),
                            "composition_id": request["composition_id"]}
        self.running = True
        self.resource.update(phase="actor-associated", actor={"run_id": self.run_id})
        if self.fault == "lost_submit":
            raise TimeoutError("accepted original POST response lost")
        return self.status(timeout)

    def status(self, timeout):
        self.calls.append("status")
        if not hasattr(self, "run_id"):
            return {"schema": "stpd/local-models-v1", "status": "idle"}
        return {"schema": "stpd/local-models-v1", "status": "loaded",
                "operation": {"id": self.operation_id, "status": "completed"},
                "startup": {"run_id": self.run_id},
                "startup_native_episode": {"run_id": self.run_id,
                                           "admission_context": self.context},
                "startup_agent_run_request": {"run_id": self.run_id, "request": self.request_ref},
                "runtime": {"run_id": self.run_id,
                            "lifecycle": "running", "mode": "human", "tainted": False,
                            "pending_request": None, "autonomy_budget": {"state": "active"}},
                **({"evaluation": self.evaluation} if hasattr(self, "evaluation") else {})}

    def finish(self, ref, timeout):
        from spireagent.json_boundary import json_bytes

        self.calls.append(("finish", ref))
        assert ref == self.resource["environment_ref"]
        self.running = False
        if not hasattr(self, "run_id"):
            return {**self.resource, "phase": "closed", "finish_complete": True}
        evaluation = self.final_evaluation()
        cleanup = {"native_exit_disposition": "graceful",
                   "native_shutdown_ack": "runtime_bound_acknowledged", "driver_exit_code": 0,
                   "forced_fallback": False, "cleanup_error": None}
        if self.fault == "cleanup_unknown":
            cleanup["native_exit_disposition"] = "unknown"
        actor = {"run_id": self.run_id, "status": "stopped", "seal": "available",
                 "evaluation_reference": {"run_id": self.run_id,
                     "sha256": hashlib.sha256(json_bytes(evaluation)).hexdigest()}}
        result = {**self.resource, "phase": "closed", "finish_complete": True,
                  "actor_finish": actor, "cleanup": cleanup}
        if self.fault == "raw_hash_as_reference":
            actor["evaluation_reference"]["sha256"] = hashlib.sha256(
                canonical_json(evaluation).encode()).hexdigest()
        return result

    def final_evaluation(self):
        evaluation = {"schema": "stpd/local-native-agent-operation-handoff-v1",
                      "run_id": self.run_id, "evidence_verification": "pass",
                      "evidence_content_id": "e" * 64,
                      "agent_execution": {"status": "bound", "run_id": self.run_id,
                          "request": self.request_ref,
                          "native_episode": {"status": "bound", "admission_context": self.context}},
                      "tainted": False, "pending_request": None, "controller_at_stop": "released",
                      "request_outcome_counts": {"delivered": 1},
                      "agent_task_outcome": {"task_completion": {"status": "unknown"},
                          "operation_end": {"status": "censored", "controller": "released",
                                            "tainted": False},
                          "terminal_observation": {"status": "observed", "result": "loss"}}}
        evaluation["evaluation_id"] = hashlib.sha256(
            canonical_json(evaluation).encode()).hexdigest()
        self.evaluation = evaluation
        return evaluation

    def originals(self, evaluation, request, startup):
        return self.request_raw, canonical_json(evaluation).encode()


def execute(tmp_path, fault=None, *, count=1, stop=lambda: False):
    from spireagent.workbench.reference_suite import SuiteLimits, run_suite

    manifest, sha = suite_manifest(tmp_path)
    directory = tmp_path / "attempt"
    client, clock = OwnerClient(directory, fault), Clock()
    report = run_suite(manifest, sha, client=client, run_dir=directory,
                       selection="existing-agent", run_profile="short",
                       limits=SuiteLimits(2, 2, 2, 20, 0.01), max_episodes=count,
                       clock=clock, sleep=clock.sleep, should_stop=stop)
    return report, client, directory


def test_canary_preserves_complete_denominator_and_original_hash_dialects(tmp_path):
    report, client, directory = execute(tmp_path)
    assert report["status"] == "completed", report
    assert report["planned"] == len(report["episodes"]) == 20
    assert report["actor_started"] == report["world_offers"] == 1
    first = report["episodes"][0]
    assert first["status"] == "finished"
    assert first["agent_task_outcome"]["task_completion"]["status"] == "unknown"
    assert first["agent_task_outcome"]["terminal_observation"]["result"] == "loss"
    raw = (directory / first["evaluation"]["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == first["evaluation"]["sha256"]
    assert hashlib.sha256(raw + b"\n").hexdigest() == first["evaluation_reference"]["sha256"]
    assert all(row["status"] == "not_started" for row in report["episodes"][1:])
    assert client.calls.count("submit") == 1


def test_two_episodes_are_serial_and_finish_a_precedes_prepare_b(tmp_path):
    report, client, _ = execute(tmp_path, count=2)
    assert report["status"] == "completed", report
    calls = client.calls
    assert calls.index(("finish", f"{1:032x}")) < calls.index("prepare", calls.index("prepare") + 1)
    assert len({row["generation_id"] for row in report["episodes"][:2]}) == 2
    assert len({row["seed"] for row in report["episodes"][:2]}) == 2


class PublishingOwner(OwnerClient):
    """The original owner publishes while an independent HTTP read is in flight.

    LocalModelService associates the native resource before exposing its startup
    association. A native-status reply can still contain an earlier detached copy.
    This models that legal ordering, not an atomic pair of the two GET endpoints.
    """
    publication_pending = False

    def submit(self, request, ref, timeout):
        super().submit(request, ref, timeout)
        self.resource.pop("actor")
        self.resource["phase"] = "ready"
        self.publication_pending = True
        return {"operation": {"id": self.operation_id, "status": "pending"}}

    def publish(self):
        self.resource.update(phase="actor-associated", actor={"run_id": self.run_id})
        self.publication_pending = False

    def status(self, timeout):
        if self.publication_pending:
            self.publish()
        return super().status(timeout)

    def native_status(self, timeout):
        value = super().native_status(timeout)
        if self.publication_pending:
            # Return the already captured old resource even though publication
            # completes before the next endpoint is read.
            self.publish()
        elif self.running and self.fault == "actor_replaced":
            value["actor"] = {"run_id": "different-run"}
        elif self.running and self.fault == "actor_missing":
            value.pop("actor", None)
        return value


@pytest.mark.parametrize("fault", [None, "actor_replaced", "actor_missing"])
def test_startup_publication_between_gets_preserves_exact_resource_binding(tmp_path, fault):
    from spireagent.workbench.reference_suite import SuiteLimits, run_suite

    manifest, sha = suite_manifest(tmp_path)
    directory = tmp_path / "publication-attempt"
    client, clock = PublishingOwner(directory, fault), Clock()
    report = run_suite(manifest, sha, client=client, run_dir=directory,
                       selection="existing-agent", run_profile="short", max_episodes=2,
                       limits=SuiteLimits(2, 2, 2, 20, 0.01), clock=clock, sleep=clock.sleep)
    assert len(report["episodes"]) == 20
    if fault is None:
        assert report["status"] == "completed", report
        assert report["actor_started"] == client.calls.count("submit") == 2
        assert all(row["cleanup_accepted"] for row in report["episodes"][:2])
    else:
        assert report["status"] == "stopped"
        assert report["stop_reason"] == "resource_actor_changed"
        assert client.calls.count("submit") == client.calls.count("prepare") == 1
        assert all(row["status"] == "not_started" for row in report["episodes"][1:])
        evidence = report["episodes"][0]["actor_binding_observations"]
        raw = (directory / evidence["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == evidence["sha256"]
        pair = json.loads(raw)
        assert pair["application_status"]["startup"]["run_id"] == client.run_id
        assert pair["native_resource"].get("actor") == (
            {"run_id": "different-run"} if fault == "actor_replaced" else None)


def test_full_twenty_keeps_literal_denominator_and_twenty_exact_original_runs(tmp_path):
    report, client, _ = execute(tmp_path, count=20)
    assert report["status"] == "completed", report
    assert report["actor_started"] == report["world_offers"] == report["planned"] == 20
    assert report["summary"]["not_started"] == report["summary"]["cleanup_unknown"] == 0
    assert len({row["run_id"] for row in report["episodes"]}) == 20
    assert len({row["generation_id"] for row in report["episodes"]}) == 20
    assert client.calls.count("submit") == client.calls.count("prepare") == 20


@pytest.mark.parametrize("fault", ["lost_prepare", "lost_submit", "resource_replaced",
                                  "cleanup_unknown", "raw_hash_as_reference"])
def test_uncertain_or_changed_owner_stops_without_repost_or_losing_denominator(tmp_path, fault):
    report, client, _ = execute(tmp_path, fault, count=2)
    assert report["status"] == "stopped", report
    assert len(report["episodes"]) == 20
    assert client.calls.count("prepare") == 1
    assert client.calls.count("submit") == (0 if fault == "lost_prepare" else 1)
    assert [call for call in client.calls if isinstance(call, tuple)] == [("finish", f"{1:032x}")]
    assert all(row["status"] == "not_started" for row in report["episodes"][1:])


def test_stop_before_dispatch_preserves_twenty_not_started_rows(tmp_path):
    report, client, _ = execute(tmp_path, stop=lambda: True)
    assert report["stop_reason"] == "stop_requested"
    assert "prepare" not in client.calls
    assert all(row["status"] == "not_started" for row in report["episodes"])


def test_manifest_or_existing_directory_failure_cannot_offer_world_work(tmp_path):
    from spireagent.workbench.reference_suite import SuiteLimits, load_manifest, run_suite

    path, sha = suite_manifest(tmp_path)
    with pytest.raises(ValueError, match="manifest_hash_mismatch"):
        load_manifest(path, "f" * 64)
    directory = tmp_path / "existing"
    directory.mkdir()
    client = OwnerClient(directory)
    with pytest.raises(FileExistsError):
        run_suite(path, sha, client=client, run_dir=directory, selection="existing-agent",
                  run_profile="short", limits=SuiteLimits(2, 2, 2, 20))
    assert client.calls == []


def test_request_persistence_failure_never_submits_or_starts_next_episode(tmp_path, monkeypatch):
    from spireagent.workbench import reference_suite

    actual = reference_suite._Artifacts.put

    def failed(artifacts, label, raw):
        if label == "generated-request.json":
            raise OSError("synthetic disk failure")
        return actual(artifacts, label, raw)

    monkeypatch.setattr(reference_suite._Artifacts, "put", failed)
    report, client, _ = execute(tmp_path, count=2)
    assert report["status"] == "stopped"
    assert client.calls.count("prepare") == 1 and "submit" not in client.calls
    assert ("finish", f"{1:032x}") in client.calls


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, 181])
def test_native_response_budget_rejects_before_any_instance_or_offer(
    tmp_path, monkeypatch, timeout,
):
    monkeypatch.setattr(local_model_cli, "running", lambda _config: pytest.fail("no owner lookup"))
    with pytest.raises(ValueError, match="bounded_response_timeout_required"):
        local_model_cli.native_episode_command(
            SimpleNamespace(state_dir=tmp_path), "native-status", timeout_seconds=timeout)


def test_phase_watchdog_finishes_once_and_stops_remaining_dispatch(tmp_path, monkeypatch):
    original = OwnerClient.status

    def active(client, timeout):
        value = original(client, timeout)
        if "runtime" in value:
            value["runtime"]["mode"] = "auto"
        return value

    monkeypatch.setattr(OwnerClient, "status", active)
    report, client, _ = execute(tmp_path, count=2)
    assert report["status"] == "stopped"
    assert report["stop_reason"] == "suite_or_phase_wall_limit"
    assert client.calls.count("submit") == client.calls.count("prepare") == 1
    assert len([call for call in client.calls if isinstance(call, tuple)]) == 1


def test_runtime_unknown_and_replacement_do_not_adopt_or_reattempt_actor(tmp_path, monkeypatch):
    original = OwnerClient.status

    def uncertain(client, timeout):
        value = original(client, timeout)
        if "runtime" in value:
            value["runtime"]["tainted"] = True
            value["runtime"]["pending_request"] = {"request_id": "original-unknown"}
        return value

    monkeypatch.setattr(OwnerClient, "status", uncertain)
    report, client, _ = execute(tmp_path, count=2)
    assert report["stop_reason"] == "delivery_or_runtime_unknown"
    assert client.calls.count("submit") == 1
    assert all(row["status"] == "not_started" for row in report["episodes"][1:])


def test_final_report_write_failure_stops_before_the_next_world_offer(tmp_path, monkeypatch):
    from spireagent.workbench import reference_suite

    original = reference_suite._Artifacts.save
    failed = False

    def save(artifacts, report):
        nonlocal failed
        if not failed and report["episodes"][0]["status"] == "finished":
            failed = True
            raise OSError("synthetic final report write failure")
        return original(artifacts, report)

    monkeypatch.setattr(reference_suite._Artifacts, "save", save)
    report, client, _ = execute(tmp_path, count=2)
    assert failed and report["stop_reason"] == "report_persistence_failed"
    assert client.calls.count("prepare") == 1
    assert all(row["status"] == "not_started" for row in report["episodes"][1:])


class FinalizingOwner(OwnerClient):
    """Original observer publication can lag Runtime exit; no replacement Actor."""
    def __init__(self, run_dir, fault=None):
        super().__init__(run_dir, fault)
        self.polls = 0

    def status(self, timeout):
        value = super().status(timeout)
        if not self.running:
            return value
        self.polls += 1
        if self.fault == "transport_gap" and self.polls == 2:
            from spireagent.json_boundary import BoundaryError

            raise BoundaryError("local_model", "workbench_request_failed_check_status_before_retry")
        if self.polls > 1:
            value.update(status="runtime_exited", runtime=None,
                         observation_error="runtime_status_unavailable_or_identity_drift")
            if self.fault == "close_transition":
                value.update(status="loaded", observation_error=None, runtime={
                    "run_id": self.run_id, "lifecycle": "running", "mode": "human",
                    "autonomy_budget": {"ended_reason": "stopped"},
                    "last_directive": {"type": "close"}})
        if self.polls > 4 and self.fault != "never_finalizes":
            evaluation = self.final_evaluation()
            if self.fault in {"wrong_run", "old_evaluation"}:
                evaluation["run_id"] = "previous-run"
            value.update(status="stopped", evaluation=evaluation)
        return value

    def finish(self, ref, timeout):
        result = super().finish(ref, timeout)
        result["actor_finish"].update(status="owned_process_contained", process_exit_code=0)
        if self.fault == "nonzero_exit":
            result["actor_finish"]["process_exit_code"] = 1
        if self.fault in {"missing_seal", "never_finalizes"}:
            result["actor_finish"].update(seal="unknown", evaluation_reference=None)
        if self.fault in {"wrong_run", "old_evaluation"}:
            self.evaluation["run_id"] = "previous-run"
        return result


def observer_execute(tmp_path, fault=None):
    from spireagent.workbench.reference_suite import SuiteLimits, run_suite

    manifest, sha = suite_manifest(tmp_path)
    directory = tmp_path / "observer-attempt"
    client, clock = FinalizingOwner(directory, fault), Clock()
    report = run_suite(manifest, sha, client=client, run_dir=directory,
                       selection="existing-agent", run_profile="short", max_episodes=2,
                       limits=SuiteLimits(2, 2, 2, 20, 0.01), clock=clock, sleep=clock.sleep)
    return report, client, clock


@pytest.mark.parametrize("fault", [None, "transport_gap", "close_transition"])
def test_same_run_owner_finalization_after_exit_uses_existing_budget(tmp_path, fault):
    report, client, clock = observer_execute(tmp_path, fault)
    assert report["status"] == "completed", report
    assert report["actor_started"] == 2
    assert clock.now >= 0.03
    assert client.calls.count("submit") == 2
    assert all(row["reason"] == "owner_finalized" for row in report["episodes"][:2])
    assert all(row["cleanup_accepted"] for row in report["episodes"][:2])


@pytest.mark.parametrize("fault", ["never_finalizes", "nonzero_exit", "wrong_run",
                                  "missing_seal", "old_evaluation"])
def test_exit_or_stale_publication_cannot_replace_original_seal(tmp_path, fault):
    report, client, clock = observer_execute(tmp_path, fault)
    assert report["status"] == "stopped", report
    assert client.calls.count("prepare") == client.calls.count("submit") == 1
    assert [call for call in client.calls if isinstance(call, tuple)] == [("finish", f"{1:032x}")]
    assert all(row["status"] == "not_started" for row in report["episodes"][1:])
    if fault in {"never_finalizes", "wrong_run", "old_evaluation"}:
        assert 2 <= clock.now < 2.1
        assert report["stop_reason"] == "suite_or_phase_wall_limit"
    assert report["episodes"][0]["cleanup_accepted"] is False


@pytest.mark.parametrize("helper", ["native", "model"])
def test_explicit_cli_allowance_expired_by_health_never_offers_request(
    tmp_path, monkeypatch, helper,
):
    from spireagent.json_boundary import BoundaryError

    clock, offered = Clock(), []
    request = {"schema": "spireagent/agent-run-request-v1", "admission_context": {
        "native_episode": {"environment_ref": "b" * 32}}}
    original = copy.deepcopy(request)

    def health(_config):
        clock.sleep(0.03)
        return {"instance_id": "a" * 32, "port": 12345, "control_token": "synthetic"}

    class Transport:
        def open(self, value, *, timeout):
            offered.append((value.full_url, value.data, timeout))
            return BytesIO(b'{"schema":"stpd/local-models-v1"}')

    monkeypatch.setattr(local_model_cli, "monotonic", clock, raising=False)
    monkeypatch.setattr(local_model_cli, "running", health)
    monkeypatch.setattr(local_model_cli, "build_opener", lambda *_args: Transport())
    with pytest.raises(BoundaryError, match="response_allowance_exhausted"):
        if helper == "native":
            local_model_cli.native_episode_command(
                SimpleNamespace(state_dir=tmp_path), "run-agent", original_request=request,
                environment_ref="b" * 32, timeout_seconds=0.01)
        else:
            local_model_cli.model_command(
                SimpleNamespace(state_dir=tmp_path), "auto", timeout_seconds=0.01)
    assert offered == []
    assert request == original


@pytest.mark.parametrize("request_seconds", [0.3, 1.0])
def test_explicit_native_allowance_is_shared_by_health_and_both_offers(
    tmp_path, monkeypatch, request_seconds,
):
    from spireagent.json_boundary import BoundaryError

    clock, offered = Clock(), []

    def health(_config):
        clock.sleep(0.2)
        return {"instance_id": "a" * 32, "port": 12345, "control_token": "synthetic"}

    class Transport:
        def open(self, value, *, timeout):
            offered.append((value.full_url, timeout))
            if value.full_url.endswith("/request"):
                clock.sleep(request_seconds)
            return BytesIO(b'{"schema":"stpd/local-models-v1"}')

    monkeypatch.setattr(local_model_cli, "monotonic", clock, raising=False)
    monkeypatch.setattr(local_model_cli, "running", health)
    monkeypatch.setattr(local_model_cli, "build_opener", lambda *_args: Transport())

    def command():
        return local_model_cli.native_episode_command(
            SimpleNamespace(state_dir=tmp_path), "run-agent", selection="existing-agent",
            environment_ref="b" * 32, timeout_seconds=1.0)

    if request_seconds == 1.0:
        with pytest.raises(BoundaryError, match="response_allowance_exhausted"):
            command()
        assert len(offered) == 1
    else:
        command()
        assert [item[1] for item in offered] == pytest.approx([0.8, 0.5])
    assert offered[0][0].endswith("/request")
    assert offered[0][1] == pytest.approx(0.8)
