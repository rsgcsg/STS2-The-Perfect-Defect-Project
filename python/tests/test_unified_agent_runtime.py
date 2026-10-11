"""Actual common service/packaged CLI/control/Stop/seal; synthetic world and M2 math."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_local_models import public_native_preflight  # noqa: F401, F811
from test_public_m0_policy_port import capabilities as public_caps
from test_public_m2_runtime import metadata_export

from spireagent.json_boundary import json_bytes
from spireagent.workbench.local_model_registration import LocalModelRegistration
from stpd import public_m2_policy_installation

ROOT = Path(__file__).resolve().parents[2]


def settled(service):
    service.thread.join(timeout=30)
    assert not service.thread.is_alive()
    assert service.state["operation"]["status"] == "completed", service.state
    return service.status()


@pytest.mark.parametrize("family", ["teacher", "public_m2"])
def test_same_common_service_real_start_control_stop_and_original_request(
    public_native_preflight,  # noqa: F811
    tmp_path,
    monkeypatch,
    family,
):
    service, _, _ = public_native_preflight
    import sts2_platform_evidence

    assert "sts2.policy-runtime/decision-only-ndjson-4" in (
        sts2_platform_evidence.SUPPORTED_POLICY_DECISION_PROTOCOLS
    )
    profile = json.loads(
        (
            ROOT / "components/connector/contracts/native-logical-publication-profile-v1.json"
        ).read_bytes()
    )
    c = json.loads(
        (ROOT / "components/connector/contracts/fixtures/native-logical-v1.json").read_bytes()
    )["wire_samples"]["capabilities"]
    c["supported_methods"] = list(
        set(c["supported_methods"]) | {"capabilities", "submit", "result"}
    )
    c["capture_coverage"] = profile["required_seams"]
    c["host"]["runtime_instance_id"] = c["session"]["runtime_instance_id"]
    c["host"].update(
        host_kind="test",
        version="fixture-version",
        implementation={
            "source_revision": "fixture-source",
            "module_version_id": "fixture-module",
            "artifact_sha256": "f" * 64,
        },
    )
    c["game"]["modset"].update(status="exact", fingerprint="b" * 64, loaded_mod_ids=[])
    c["game"]["version"], c["game"]["commit"] = "test-game", "test-commit"
    observed = {"capabilities": c, "publication_profile": profile, "publication_profile_sha256": ""}
    from spireagent.workbench.native_agent_support import PUBLICATION_PROFILE_SHA256

    observed["publication_profile_sha256"] = PUBLICATION_PROFILE_SHA256
    exported = SimpleNamespace()
    if family == "public_m2":
        for name in public_m2_policy_installation.ADAPTER_SOURCE_CLOSURE:
            source, target = ROOT / "python" / name, service.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        export, model = metadata_export(tmp_path)
        destination = service.config.state_dir / "model-exports" / model.artifact_id
        shutil.copytree(export, destination)
        exported.status_for_model = lambda _: {
            "availability": "ready",
            "operation": {
                "model_id": model.artifact_id,
                "model_type": "public_m2",
                "status": "completed",
                "verified_receipt": {"fixture": True},
            },
        }
        exported.verified_public_m2_for_registration = lambda *_a, **_k: destination
        c = public_caps()
    registration = LocalModelRegistration(service.config, exported, service)
    monkeypatch.setattr(
        registration, "_capabilities", lambda *_a, **_k: observed if family == "teacher" else c
    )
    if family == "public_m2":
        registered = registration.register(model.artifact_id)
        # The actual stateful adapter/codec/W/NDJSON remains in the child. Only
        # original weights/tokenizer/backend construction is a counting fixture.
        monkeypatch.setattr(
            public_m2_policy_installation,
            "inspect",
            lambda *_a, **_k: {
                "policy_identity": {"status": "pass"},
                "backend": {"status": "pass"},
            },
        )
    else:
        registered = registration.register_teacher(variant="baseline")
        assert (
            registration.register_teacher(variant="baseline")["selection_id"]
            == registered["selection_id"]
        )
    identity = registered["selection_id"]
    request = service.agent_request(identity, "start")
    manifest_path = service.entry_path(service.selection(identity), "manifest")
    original_manifest = manifest_path.read_bytes()
    manifest = json.loads(original_manifest)
    original_artifact_path = str((manifest_path.parent / manifest["artifact"]["path"]).resolve())
    assert original_artifact_path not in json.dumps(request)
    assert request["composition"]["implementation"]["runtime_artifact"] == {
        "id": manifest["artifact"]["id"],
        "sha256": manifest["artifact"]["sha256"],
    }

    models = request["composition"]["semantic_definition"]["models"]
    assert models == [] if family == "teacher" else len(models) == 1
    world = tmp_path / "world.json"
    world.write_bytes(json_bytes({"capabilities": c, "profile": profile, "result": "loss"}))
    real_popen = subprocess.Popen
    commands = []
    trace = tmp_path / "model-trace.jsonl"

    def child_boundary(command, **kw):
        if command[0] != "node" or not command[1].endswith("dist/cli.js"):
            return real_popen(command, **kw)
        commands.append(copy.deepcopy(command))
        routed = [
            command[0],
            str(ROOT / "python/tests/unified_agent_runtime_world.mjs"),
            command[1],
            str(world),
            *command[2:],
        ]
        if family == "public_m2":
            manifest = str(service.entry_path(service.selection(identity), "manifest"))
            routed = [arg for arg in routed if not arg.startswith("--adapter-arg=")]
            routed += [
                "--adapter-arg=" + value
                for value in (
                    str(ROOT / "python/tests/public_m2_child.py"),
                    manifest,
                    str(trace),
                    "registered",
                )
            ]
        return real_popen(routed, **kw)

    from spireagent.workbench import local_models

    real_decode = local_models.decode_json

    def capture_startup(raw):
        value = real_decode(raw)
        if isinstance(value, dict) and value.get("schema", "").endswith("startup-1"):
            (tmp_path / "actual-startup.json").write_bytes(json_bytes(value))
        return value

    monkeypatch.setattr(local_models, "decode_json", capture_startup)
    monkeypatch.setattr(subprocess, "Popen", child_boundary)
    monkeypatch.setattr(service, "_require_model_admission", lambda: None)
    monkeypatch.setattr(
        service.native_tasks,
        "prepare_model",
        lambda *_a, **_k: {"runtime_instance_id": c["host"]["runtime_instance_id"]},
    )
    monkeypatch.setattr(
        service.native_tasks, "connector_instance", lambda _: c["host"]["runtime_instance_id"]
    )
    try:
        service.run_agent(request)
        loaded = settled(service)
        assert loaded["loaded"] is True and loaded["runtime"]["mode"] == "human"
        original_ref = service.state["agent_run_request"]
        raw = (service.directory / original_ref["path"]).read_bytes()
        if family == "teacher":
            # A Human load also exercises the ordinary explicit Stop owner.
            # Terminal Close below already invokes Runtime's finishStop; sending
            # another POST after its CLI exit would be an invalid extra command.
            service.command("stop")
            first_stopped = settled(service)
            assert first_stopped["evaluation"]["evidence_verification"] == "pass"
            assert (
                first_stopped["evaluation"]["agent_task_outcome"]["task_completion"]["status"]
                != "completed"
            )
            prior_ref, prior_raw = original_ref, raw
            service.run_agent(request)
            loaded = settled(service)
            original_ref = service.state["agent_run_request"]
            raw = (service.directory / original_ref["path"]).read_bytes()
            assert original_ref["operation_id"] != prior_ref["operation_id"]
            assert (service.directory / prior_ref["path"]).read_bytes() == prior_raw
        process = service.process
        context = service.control_context()
        context.pop("schema")  # Command owns the existing three-field precondition.
        service.command("one_step", expected_context=context)
        observed = settled(service)
        if family == "teacher":
            assert observed["runtime"]["lifecycle"] == "stopped"
            # No manual observer/handoff call and no follow-up Runtime request:
            # the command owner must finalize this exact stopped response now.
            assert observed["loaded"] is False and observed["status"] == "stopped"
            assert service.client is None
            assert process.poll() is not None
            stopped = observed
        else:
            service.command("stop")
            stopped = settled(service)
        report = stopped["evaluation"]
        assert report["evidence_verification"] == "pass", report
        assert report["agent_execution"]["status"] == "bound"
        assert original_artifact_path not in json.dumps(report)
        assert manifest_path.read_bytes() == original_manifest

        assert report["agent_run_request"] == original_ref
        assert (service.directory / original_ref["path"]).read_bytes() == raw
        assert report["agent_execution"]["run_id"] == loaded["startup"]["run_id"]
        events = [
            json.loads(line)
            for line in (
                service.directory / "agent-runs" / loaded["startup"]["run_id"] / "events.jsonl"
            )
            .read_bytes()
            .splitlines()
        ]
        assert events[-1]["kind"] == "stopped"
        assert events[-1]["payload"]["controller"] == "released"
        if family == "teacher":
            assert report["agent_task_outcome"]["task_completion"]["status"] == "completed", report
            assert report["agent_task_outcome"]["terminal_observation"]["result"] == "loss"
            assert report["agent_task_outcome"]["agent_composition"]["models"] == []
            assert not any(event["kind"] == "native_submission_requested" for event in events)
        else:
            assert report["delivery_counts"]["delivered"] == 1
            rows = [json.loads(line) for line in trace.read_bytes().splitlines()]
            assert rows[0]["control"]["observation_ordinal"] == 1 and rows[0]["advanced"] is True
        assert len(commands) == (2 if family == "teacher" else 1)
        assert "--mode" in commands[0] and commands[0][commands[0].index("--mode") + 1] == "human"
    finally:
        if service.state["status"] != "stopped":
            service.close()
