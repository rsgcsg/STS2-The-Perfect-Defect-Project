"""Focused consumer boundaries; no cloud, game or installed-runtime claim."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_artifact_store_v1 import PRODUCER
from test_global_evidence_store import _setup
from test_p1_snapshot import store_at

from spireagent.artifact_contracts import Manifest, Parent
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.task_outcome import _empty
from spireagent.workbench.agent_task_outcome import _global_model
from spireagent.workbench.developer import ProjectConfig, RemoteComputeConfig, combination
from spireagent.workbench.global_evidence_store import _parents
from spireagent.workbench.local_models import LocalModelService
from stpd.evaluation.suites import (
    FOUR_AGENT_SUITE_ID,
    four_agent_reference_manifest,
    require_non_evaluation_seed,
)
from stpd.p1.comparison import compare_test_results


def test_store_download_own_bytes_without_private_parent_or_hub_fallback(tmp_path):
    store = store_at(tmp_path / "remote")
    payload = store.put_bytes("weights", b"original bytes")
    private = Manifest("dataset", PRODUCER, payloads=(store.put_bytes("raw", b"private parent"),))
    store.publish(private)
    model = Manifest("model", PRODUCER, (Parent("private_source", private.artifact_id),),
                     (payload,), FrozenObject.of({"schema": "test-only"}))
    store.publish(model)
    config = ProjectConfig(tmp_path / "state", "", "", None, combination(), remote_compute=
        RemoteComputeConfig(tmp_path / "operations", tmp_path / "target", 1, 1,
                            {"kind": "local", "directory": str(store.blobs.root)}))
    models = LocalModelService(config)
    try:
        class Hub:
            def download(self, *_):
                pytest.fail("explicit Store selection must not use Hub")
        models.hub = Hub()
        models.prepare(model.artifact_id, source="store")
        models.thread.join(timeout=5)
        receipt = models.status()["last_download"]
        target = config.state_dir / "downloads" / model.artifact_id
        assert receipt["parents_downloaded"] is False
        assert {p.name for p in target.iterdir()} == {
            "manifest.json", "download.json", payload.sha256 + ".bin"}
        assert (target / (payload.sha256 + ".bin")).read_bytes() == b"original bytes"
        (target / (payload.sha256 + ".bin")).write_bytes(b"corrupt")
        models.prepare(model.artifact_id, source="store")
        models.thread.join(timeout=5)
        assert models.status()["error_code"] == "existing_payload_mismatch"
        assert models.process is None
    finally:
        models.close()


@pytest.mark.parametrize("baseline,schema", [(False, "stpd/p1-mstd-model-1"),
    (False, "stpd/p1-mstd-model-2"), (True, "stpd/p1-baseline-model-1")])
def test_model_outcome_keeps_model_binding_without_claiming_task_success(
    baseline, schema, tmp_path,
):
    spec = {"id": "p1-frequency" if baseline else "p1-mstd", "version": "1",
            "task_spec": {"id": "current-public-semantic-choice", "version": "1"}}
    input_spec = {"id": "input", "version": "1", "sha256": "a" * 64}
    manifest = {"agent": {"id": spec["id"], "version": "1"},
                "adapter": {"id": "stpd-p1-baseline-agent" if baseline else "stpd-p1-mstd-agent"},
                "artifact": {"sha256": "b" * 64},
                "input": {"profile": "p1-global-r1", "input_spec": input_spec,
                          "state_recovery": {"model_bindings": []}}}
    descriptor = {"schema": schema,
                  "package_sha256": "b" * 64, "agent_spec": spec, "input_spec": input_spec}
    outcome = _empty("pass")
    outcome["agent_composition"]["model"] = {"runtime_artifact_sha256": "b" * 64}
    _global_model(outcome, None, manifest, [], json_bytes(descriptor))
    assert outcome["agent_composition"]["models"] == [outcome["agent_composition"]["model"]]
    assert outcome["agent_composition"]["binding"] == "original_p1_model_artifact"
    assert outcome["task_completion"]["status"] == "unproved"
    assert outcome["terminal_observation"]["status"] == "not_observed"
    with pytest.raises(BoundaryError, match="global_original_model_unavailable"):
        _global_model(outcome, None, manifest, [],
                      json_bytes({**descriptor, "package_sha256": "c" * 64}))

    from spireagent.workbench.agent_task_outcome import project

    basis = {"capture_status": "complete", "snapshot": {
        "schema": "sts2.player-environment/snapshot-1", "interaction": {
            "kind": "game_over", "content_schema": "sts2.player-environment/surface/game_over-1",
            "content": {"surface": {"kind": "game_over", "stage": "intro"},
                        "context": {"kind": "game_over", "result": "win"}}}}}
    record = json_bytes({"serialized_observation": json.dumps(basis)})
    digest = hashlib.sha256(record).hexdigest()
    original = {"path": f"global-{digest}.bin", "sha256": digest,
                "bytes": len(record)}
    events = [
        {"kind": "global_source", "sequence": 1,
         "payload": {"ordinal": 1, "artifact": original}},
        {"kind": "global_observation", "sequence": 2,
         "payload": {"source_ordinal": 1, "complete": True, "status": "terminal"}},
        {"kind": "stopped", "sequence": 3,
         "payload": {"controller": "released", "reason": "agent_closed"}},
    ]
    raw = b"".join(json_bytes(e) for e in events)
    (tmp_path / original["path"]).write_bytes(record)
    (tmp_path / "events.jsonl").write_bytes(raw)
    files = [original, {"path": "events.jsonl", "sha256": hashlib.sha256(raw).hexdigest(),
                        "bytes": len(raw)}]
    value = SimpleNamespace(content_id="d" * 64, directory=tmp_path,
        evidence_manifest={"files": files}, agent_manifest=manifest,
        manifest={"tainted": False, "agent_manifest_sha256": "a" * 64})
    projected = project(SimpleNamespace(status="pass", value=value), native=True,
                        native_package=json_bytes(descriptor))
    assert projected["agent_composition"]["binding"] == "original_p1_model_artifact"
    assert projected["terminal_observation"]["result"] == "win"
    assert projected["task_completion"]["status"] == "unproved"
    assert projected["operation_end"]["controller"] == "released"


def test_baseline_evidence_parent_uses_baseline_projection(tmp_path, monkeypatch):
    from stpd.p1.baseline_export import INPUT_PROJECTION
    from stpd.p1.input import INPUT_SPEC

    store, _, directory, expected, model, runtime, _ = _setup(tmp_path, monkeypatch)
    descriptor = json.loads(b"".join(store.read_payload(model.payload("package_manifest"))))
    descriptor["schema"] = "stpd/p1-baseline-model-1"
    payload = store.put_bytes("package_manifest", json_bytes(descriptor), "application/json")
    baseline = replace(model, payloads=(model.payload("weights"), payload))
    store.publish(baseline)
    original = descriptor["input_spec"]
    expected["model_binding"] = {"package_input_spec": original, "input_spec": original,
                                 "input_projection": INPUT_PROJECTION}
    agent = {"adapter": {"id": "stpd-p1-baseline-agent"}, "input": {"input_spec": original}}
    value = SimpleNamespace(manifest=json.loads((directory / "manifest.json").read_bytes()),
                            agent_manifest=agent)
    parents = _parents(store, baseline.artifact_id, runtime.artifact_id, value, expected)
    assert parents[0].artifact_id == baseline.artifact_id
    expected["model_binding"]["input_projection"] = INPUT_SPEC
    with pytest.raises(BoundaryError, match="trusted_model_binding_mismatch"):
        _parents(store, baseline.artifact_id, runtime.artifact_id, value, expected)


def test_packed_original_package_is_accepted_as_global_evidence_parent(tmp_path, monkeypatch):
    store, _, directory, expected, model, runtime, _ = _setup(tmp_path, monkeypatch)
    descriptor = json.loads(b"".join(store.read_payload(model.payload("package_manifest"))))
    descriptor["schema"] = "stpd/p1-mstd-model-2"
    payload = store.put_bytes("package_manifest", json_bytes(descriptor), "application/json")
    packed = replace(model, payloads=(model.payload("weights"), payload))
    store.publish(packed)
    value = SimpleNamespace(manifest=json.loads((directory / "manifest.json").read_bytes()),
                            agent_manifest={"adapter": {"id": "stpd-p1-mstd-agent"},
                                            "input": {"input_spec": descriptor["input_spec"]}})
    parents = _parents(store, packed.artifact_id, runtime.artifact_id, value, expected)
    assert parents[0].artifact_id == packed.artifact_id
    descriptor["package_sha256"] = "0" * 64
    wrong = replace(packed, payloads=(packed.payload("weights"), store.put_bytes(
        "package_manifest", json_bytes(descriptor), "application/json")))
    store.publish(wrong)
    with pytest.raises(BoundaryError, match="original_model_descriptor_mismatch"):
        _parents(store, wrong.artifact_id, runtime.artifact_id, value, expected)


@pytest.mark.parametrize("invalid_projection", [None, "v1", "tampered_v2"])
def test_v2_evidence_binding_uses_original_model_projection(
    tmp_path, monkeypatch, invalid_projection,
):
    from stpd.p1.input import INPUT_SPEC, V2_INPUT_SPEC

    store, _, directory, expected, model, runtime, _ = _setup(tmp_path, monkeypatch)
    descriptor = json.loads(b"".join(store.read_payload(model.payload("package_manifest"))))
    compact = {"id": V2_INPUT_SPEC["id"], "version": V2_INPUT_SPEC["version"],
               "sha256": "c" * 64}
    descriptor["input_spec"] = compact
    parameters = model.parameters.value()
    parameters["input_spec"] = compact
    original = replace(model, parameters=FrozenObject.of(parameters), payloads=(
        model.payload("weights"), store.put_bytes(
            "package_manifest", json_bytes(descriptor), "application/json")))
    store.publish(original)
    value = SimpleNamespace(manifest=json.loads((directory / "manifest.json").read_bytes()),
                            agent_manifest={"adapter": {"id": "stpd-p1-mstd-agent"},
                                            "input": {"input_spec": compact}})
    expected["model_binding"] = {"package_input_spec": compact, "input_spec": compact,
                                 "input_projection": V2_INPUT_SPEC}
    assert _parents(store, original.artifact_id, runtime.artifact_id, value, expected)[0] == (
        Parent("model", original.artifact_id))
    wrong = {"v1": INPUT_SPEC, "tampered_v2": {**V2_INPUT_SPEC, "truncate": True}}
    expected["model_binding"]["input_projection"] = wrong.get(invalid_projection)
    with pytest.raises(BoundaryError, match="trusted_model_binding_mismatch"):
        _parents(store, original.artifact_id, runtime.artifact_id, value, expected)


def test_new_reference_manifest_has_new_reserved_seeds_and_explicit_version(tmp_path):
    from spireagent.workbench.reference_suite import load_manifest

    manifest = four_agent_reference_manifest("v0.999.1")
    assert manifest["suite_id"] == FOUR_AGENT_SUITE_ID
    assert len(set(manifest["seeds"])) == 10
    for seed in manifest["seeds"]:
        with pytest.raises(ValueError, match="reserved"):
            require_non_evaluation_seed(seed)
    path = tmp_path / "manifest.json"
    raw = json_bytes(manifest)
    path.write_bytes(raw)
    assert load_manifest(path, hashlib.sha256(raw).hexdigest())[1] == manifest
    manifest["seeds"][0] = manifest["seeds"][1]
    raw = json_bytes(manifest)
    path.write_bytes(raw)
    with pytest.raises(BoundaryError, match="manifest_configuration_changed"):
        load_manifest(path, hashlib.sha256(raw).hexdigest())


def test_test_opening_is_required_before_any_store_access():
    class Unreadable:
        def get_manifest(self, *_):
            pytest.fail("sealed test must not be touched")
    with pytest.raises(BoundaryError, match="explicit_test_opening_required"):
        compare_test_results(Unreadable(), {})


@pytest.mark.parametrize("preserve_fault", [False, True, "cleanup_unknown"])
def test_four_agent_reference_40_denominator_and_preservation_before_next_actor(
    tmp_path, preserve_fault,
):
    from test_reference_suite import Clock, OwnerClient

    from spireagent.encoding import canonical_json
    from spireagent.workbench.reference_suite import SuiteLimits, run_suite

    class FourOwner(OwnerClient):
        planned = 40

        def __init__(self, root):
            super().__init__(root,
                             "cleanup_unknown" if preserve_fault == "cleanup_unknown" else None)
            self.compositions = {}
            self.preserved = 0

        def readiness(self, selection, timeout):
            agent = selection.removeprefix("existing-")
            composition = {**self.composition, "selection_id": selection,
                "semantic_definition": {**self.composition["semantic_definition"],
                    "agent": {"id": "p1-" + agent}, "input": {"profile": "p1-global-r1"}},
                "implementation": {**self.composition["implementation"],
                    "adapter": {"id": "stpd-p1-mstd-agent" if agent == "mstd"
                                      else "stpd-p1-baseline-agent"}}}
            composition.pop("composition_id")
            composition["composition_id"] = hashlib.sha256(
                canonical_json(composition).encode()).hexdigest()
            self.compositions[selection] = composition
            return {"status": "ready_to_load", "selection_id": selection,
                    "agent_composition": composition}

        def prepare(self, body, timeout):
            assert self.preserved == self.number  # Durable readback before next world offer.
            return super().prepare(body, timeout)

        def request(self, selection, ref, profile, timeout):
            self.composition = self.compositions[selection]
            return super().request(selection, ref, profile, timeout)

        def preserve(self, run_id, peer, timeout):
            assert self.running is False
            self.calls.append("preserve")
            if preserve_fault is True:
                raise TimeoutError("lost publication reply; never offer again")
            self.preserved += 1
            return {"operation_id": str(self.number), "status": "completed", "run_id": run_id,
                    "peer": peer, "artifact_id": "f" * 64, "peer_readback": {"verified": True}}

    value = four_agent_reference_manifest("v0.111.0")  # Explicit synthetic test tuple.
    path = tmp_path / "new-suite.json"
    raw = json_bytes(value)
    path.write_bytes(raw)
    directory = tmp_path / "attempt"
    client, clock = FourOwner(directory), Clock()
    report = run_suite(path, hashlib.sha256(raw).hexdigest(), client=client, run_dir=directory,
        selection="", selections={a: "existing-" + a
                                  for a in ("random", "frequency", "linear", "mstd")},
        evidence_peer={"kind": "local", "directory": str(tmp_path / "peer")},
        run_profile="short", max_episodes=40, limits=SuiteLimits(2, 2, 2, 200, 0.01),
        clock=clock, sleep=clock.sleep)
    assert report["planned"] == len(report["episodes"]) == 40
    assert {row["agent"] for row in report["episodes"]} == {"random", "frequency", "linear", "mstd"}
    assert len({row["seed"] for row in report["episodes"]}) == 10
    if preserve_fault == "cleanup_unknown":
        assert report["status"] == "stopped"
        assert report["summary"]["not_started"] == 39
        assert report["summary"]["cleanup_unknown"] == 1
        assert report["summary"]["preserved_evidence"] == client.preserved == 1
        assert client.calls.count("submit") == 1
    elif preserve_fault is True:
        assert report["status"] == "stopped"
        assert report["summary"]["not_started"] == 39
        assert client.calls.count("preserve") == client.calls.count("submit") == 1
        assert report["stop_reason"] == "preservation_unknown_or_failed"
    else:
        assert report["status"] == "completed", report
        assert client.preserved == client.calls.count("submit") == 40
        assert report["summary"]["preserved_evidence"] == 40
