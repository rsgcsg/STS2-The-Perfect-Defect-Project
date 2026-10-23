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
