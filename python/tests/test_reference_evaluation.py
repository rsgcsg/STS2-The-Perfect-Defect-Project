"""Small group contract checks; synthetic transport, no native game qualification."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest
from test_reference_suite import Clock, OwnerClient

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.workbench.reference_suite import SuiteLimits, load_manifest, run_suite
from stpd.evaluation.suites import diagnostic_reference_manifest, seed_membership


class GroupOwner(OwnerClient):
    planned = 2

    def prepare(self, body, timeout):
        value = super().prepare(body, timeout)
        self.context["binding"]["presentation"].update(
            requested=body["display_mode"],
            observed_host_kind="headless" if body["display_mode"] == "headless" else "live_ui")
        self.context["binding"]["speed"].update(requested=body["speed"], effective=body["speed"])
        value["admission_context"] = copy.deepcopy(self.context)
        self.resource = copy.deepcopy(value)
        return value

    def preserve(self, run_id, peer, timeout):
        self.calls.append(("preserve", run_id))
        return {"status": "completed", "operation_id": "f" * 32, "run_id": run_id,
                "peer": peer, "artifact_id": "e" * 64, "peer_readback": {"verified": True}}


def test_diagnostic_plan_is_reproducible_versioned_and_excludes_reserved_seeds(tmp_path):
    manifest = diagnostic_reference_manifest("v0.111.0", "a" * 32, 2)
    assert manifest["purpose"] == "diagnostic_reference"
    assert manifest["version"] == "1.0.0"
    assert not any(seed_membership(seed) for seed in manifest["seeds"])
    raw = json_bytes(manifest)
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)
    assert load_manifest(path, hashlib.sha256(raw).hexdigest())[1] == manifest
    manifest["seeds"].reverse()
    path.write_bytes(json_bytes(manifest))
    with pytest.raises(BoundaryError, match="manifest_configuration_changed"):
        load_manifest(path, hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.parametrize("speed,display", [("normal", "native-ui"), ("fast", "headless"),
                                          ("instant", "headless")])
def test_two_seed_group_checks_config_and_preserves_before_boundary(tmp_path, speed, display):
    raw = json_bytes(diagnostic_reference_manifest("v0.111.0", "b" * 32, 2))
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)
    directory = tmp_path / "attempt"
    client = GroupOwner(directory)
    clock = Clock()
    boundary = []

    def before(report):
        boundary.append(copy.deepcopy(report))
        if len(boundary) == 2:
            assert client.calls[-1] == ("preserve", "run-1")
        return True

    report = run_suite(path, hashlib.sha256(raw).hexdigest(), client=client, run_dir=directory,
                       selection="existing-agent", run_profile="short", max_episodes=2,
                       speed=speed, display_mode=display, evidence_peer={"kind": "fixture"},
                       limits=SuiteLimits(10, 10, 10, 60), clock=clock, sleep=clock.sleep,
                       before_episode=before)
    assert report["status"] == "completed", report
    assert report["planned"] == report["actor_started"] == 2
    assert report["summary"]["preserved_evidence"] == 2
    assert report["speed"] == speed and report["display_mode"] == display
    assert boundary[0]["episodes"][0]["status"] == "not_started"
    assert boundary[1]["episodes"][0]["cleanup_accepted"] is True
    saved = json.loads((directory / "report.json").read_bytes())
    assert saved == report


@pytest.mark.parametrize("display,fault", [("native-ui", None), ("headless", None),
                                           ("native-ui", "lost_submit")])
def test_group_preserves_selected_model_and_actual_bound_execution(tmp_path, display, fault):
    from spireagent.encoding import canonical_json
    from spireagent.workbench.agent_run_request import create

    class BoundModelOwner(GroupOwner):
        def __init__(self, directory):
            super().__init__(directory, fault=fault)
            self.composition["semantic_definition"].update(
                agent={"id": "p1-mstd", "version": "1"},
                models=[{"model_id": "immutable-package", "weights_sha256": "a" * 64}],
                input={"profile": "p1-global-r1"},
                environment={"profile": "p1-global-r1", "requirements": {
                    "environment": {"host_kind": "reference", "producer_sha256": "b" * 64}}})
            self.composition["implementation"].update(
                adapter={"id": "stpd-p1-mstd-agent", "code_sha256": "c" * 64},
                runtime_artifact={"id": "immutable-package", "sha256": "d" * 64})
            self.composition.pop("composition_id")
            self.composition["composition_id"] = hashlib.sha256(
                canonical_json(self.composition).encode()).hexdigest()

        def request(self, selection, ref, profile, timeout):
            self.calls.append("request")
            bound = copy.deepcopy(self.composition)
            bound["selection_id"] = "reference-binding-" + ref
            bound["semantic_definition"]["environment"]["requirements"]["environment"][
                "producer_sha256"] = "e" * 64
            bound["implementation"]["original_manifest_id"] = bound["selection_id"]
            bound.pop("composition_id")
            bound["composition_id"] = hashlib.sha256(canonical_json(bound).encode()).hexdigest()
            return create(bound, "prepare-and-takeover", profile,
                          {"max_submissions": 16, "max_policy_calls": 32, "deadline_ms": 60000},
                          "native", native_episode_context=self.context)

    raw = json_bytes(diagnostic_reference_manifest("v0.111.0", "c" * 32, 2))
    path = tmp_path / "manifest.json"
    path.write_bytes(raw)
    directory = tmp_path / "attempt"
    client, clock = BoundModelOwner(directory), Clock()
    report = run_suite(path, hashlib.sha256(raw).hexdigest(), client=client, run_dir=directory,
        selection="existing-agent", run_profile="short", max_episodes=2, display_mode=display,
        speed="normal", evidence_peer={"kind": "fixture"}, limits=SuiteLimits(10, 10, 10, 60),
        clock=clock, sleep=clock.sleep)
    row = report["episodes"][0]
    assert row["selection_id"] == report["selection_id"] == "existing-agent"
    assert row["execution_selection_id"] == "reference-binding-" + row["environment_ref"]
    request_raw = (directory / row["original_request"]["path"]).read_bytes()
    admitted = json.loads(request_raw)
    assert admitted["selection_id"] == row["execution_selection_id"]
    assert admitted["composition_id"] == row["execution_composition_id"]
    assert admitted["composition"]["semantic_definition"]["models"] == [
        {"model_id": "immutable-package", "weights_sha256": "a" * 64}]
    if fault is None:
        assert report["status"] == "completed" and report["summary"]["preserved_evidence"] == 2
    else:
        assert client.calls.count("submit") == client.calls.count("prepare") == 1
        assert report["episodes"][1]["status"] == "not_started"
