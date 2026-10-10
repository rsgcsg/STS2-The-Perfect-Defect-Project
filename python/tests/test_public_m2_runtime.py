"""Actual public Runtime/SDK/wire/typed Evidence readers with a synthetic Model seam."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_public_m0_policy_port import capabilities
from test_public_m2_port import setup
from test_public_m2_restoration import stage_graph

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_model_registration import LocalModelRegistration
from spireagent.workbench.local_models import (
    PUBLIC_M2_ADAPTER,
    PUBLIC_M2_PROFILE,
    LocalModelService,
)
from stpd.policy.public_m2_export import EXPORT_SCHEMA, FILES, model_lineage, validate_package

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parent


def advertise_public_reader(monkeypatch):
    import sts2_platform_evidence

    monkeypatch.setattr(
        sts2_platform_evidence,
        "SUPPORTED_POLICY_DECISION_PROTOCOLS",
        ("sts2.policy-runtime/decision-only-ndjson-4",),
        raising=False,
    )


def typed_verify(directory: Path):
    # This source candidate's real verifier in an isolated process. The locked
    # external Corpus dependency remains unchanged; this is not installed proof.
    script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from sts2_platform_evidence.agent_run_evidence import AgentRunEvidenceVerifier
result = AgentRunEvidenceVerifier().verify(Path(sys.argv[2]))
print(json.dumps({'verdict': result.status,
                  'findings': [finding.code for finding in result.findings]}))
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            script,
            str(REPOSITORY / "components/evidence"),
            str(directory),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return json.loads(result.stdout)


def reseal(directory: Path, events):
    """Reseal deliberately damaged fixture bytes to reach typed association checks."""
    (directory / "events.jsonl").write_bytes(b"".join(json_bytes(event) for event in events))
    manifest_path = directory / "evidence-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    for entry in manifest["files"]:
        raw = (directory / entry["path"]).read_bytes()
        entry.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    manifest["manifest_sha256"] = hashlib.sha256(
        json_bytes({"run_id": manifest["run_id"], "files": manifest["files"]}).rstrip(b"\n")
    ).hexdigest()
    manifest_path.write_bytes(json_bytes(manifest))
    names = [
        "adapter-attestation.json",
        "events.jsonl",
        "evidence-manifest.json",
        "manifest.json",
        "policy-manifest.json",
    ]
    (directory / "checksums.sha256").write_text(
        "".join(
            hashlib.sha256((directory / name).read_bytes()).hexdigest() + "  " + name + "\n"
            for name in names
        ),
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def actual_public_runtime(tmp_path_factory):
    # Real SDK decoding, common controller/submission owner, actual Public M2
    # projection/codec/cache, NDJSON and immutable writer. World, weight loading
    # and numerical backend are mocked; no native, original-weight or quality claim.
    directory = tmp_path_factory.mktemp("public-m2-runtime")
    adapter, _ = setup()
    profile = directory / "profile.json"
    profile.write_bytes(json_bytes(adapter.manifest["adapter_config"]))
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}
    }
    results = {}
    for scenario in ("normal", "unknown", "oversize"):
        result = subprocess.run(
            [
                "node",
                str(REPOSITORY / "components/policy-runtime/test/fixtures/public-m2-interop.mjs"),
                str(REPOSITORY / "components/policy-runtime/dist/index.js"),
                str(REPOSITORY / "components/connector/sdk/typescript/dist/index.js"),
                sys.executable,
                str(ROOT / "tests/public_m2_child.py"),
                str(directory / scenario),
                str(profile),
                scenario,
            ],
            cwd=REPOSITORY,
            env=environment,
            text=True,
            capture_output=True,
            timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        results[scenario] = json.loads(result.stdout)
    return results


@pytest.mark.parametrize("scenario", ["normal", "unknown", "oversize"])
def test_actual_public_adapter_and_runtime_producer_pass_typed_evidence(
    actual_public_runtime, scenario
):
    observed = actual_public_runtime[scenario]
    assert typed_verify(Path(observed["evidence"])) == {"verdict": "pass", "findings": []}
    assert observed["generic_snapshot_only"] is True
    assert observed["stopped"]["controller"] == "released"
    assert observed["stopped"]["lifecycle"] == "stopped"
    assert observed["submitted"] == {"normal": 2, "unknown": 1, "oversize": 0}[scenario]
    assert observed["stopped"]["tainted"] is (scenario == "unknown")


@pytest.mark.parametrize(
    "change, expected",
    [
        (
            lambda inputs: inputs[1]["payload"]["control"].update(observation_ordinal=1),
            "public_completion_binding",
        ),
        (
            lambda inputs: inputs[1]["payload"]["completion"].update(sequence=True),
            "public_completion_binding",
        ),
        (
            lambda inputs: inputs[1]["payload"]["snapshot"]["bound_actions"]["actions"].reverse(),
            "public_decision_binding",
        ),
        (
            lambda inputs: inputs[3]["payload"]["control"]["previous_action"].update(
                request_id="unproved"
            ),
            "public_history_binding",
        ),
        (
            lambda inputs: inputs[3]["payload"]["control"].update(
                episode_scope="single_game_episode"
            ),
            "schema_literal",
        ),
    ],
)
def test_public_typed_reader_rejects_resealed_control_or_full_catalog_drift(
    actual_public_runtime,
    tmp_path,
    change,
    expected,
):
    import shutil

    source = Path(actual_public_runtime["normal"]["evidence"])
    directory = tmp_path / "damaged"
    shutil.copytree(source, directory)
    events = [
        json.loads(line)
        for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    inputs = [event for event in events if event["kind"] == "public_decision_input"]
    change(inputs)
    reseal(directory, events)
    result = typed_verify(directory)
    assert result["verdict"] == "fail" and result["findings"] == [expected]


def metadata_export(tmp_path):
    """The real closed package family/lineage with tiny bytes, no inference claim."""
    archive, model, stage = stage_graph(tmp_path)
    export = tmp_path / "export"
    export.mkdir()
    lineage = model_lineage(archive, model, stage_id=stage.artifact_id)
    (export / "model.json").write_bytes(
        json_bytes(
            {
                "schema": EXPORT_SCHEMA,
                "model_id": model.artifact_id,
                "model": json.loads(model.to_bytes()),
                "lineage": lineage,
            }
        )
    )
    for role, name in FILES.items():
        (export / name).write_bytes(b"".join(archive.read_payload(model.payload(role))))
    assert validate_package(export)[0].artifact_id == model.artifact_id
    return export, model


def test_shared_public_m2_registration_registry_and_stop_owner_routes(tmp_path, monkeypatch):
    advertise_public_reader(monkeypatch)
    export, model = metadata_export(tmp_path)
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    service = LocalModelService(config)
    proof_calls = []
    operation = {
        "availability": "ready",
        "operation": {
            "model_id": model.artifact_id,
            "model_type": "public_m2",
            "status": "completed",
            "verified_receipt": {"fixture": True},
        },
    }
    exported = SimpleNamespace(
        status_for_model=lambda _: copy.deepcopy(operation),
        verified_public_m2_for_registration=lambda identity, **kwargs: (
            proof_calls.append(identity) or export
        ),
    )
    registration = LocalModelRegistration(config, exported, service)
    monkeypatch.setattr(
        registration, "_public_m2_runtime", lambda **kwargs: (tmp_path, tmp_path / "sdk")
    )
    monkeypatch.setattr(registration, "_capabilities", lambda *args, **kwargs: capabilities())
    monkeypatch.setattr(service, "_public_manifest_contract", lambda *args, **kwargs: None)
    # Journal points to the usual owned export directory; the tiny fixture moves
    # only through a real package copy to that private location.
    import shutil

    destination = config.state_dir / "model-exports" / model.artifact_id
    shutil.copytree(export, destination)
    exported.verified_public_m2_for_registration = lambda identity, **kwargs: (
        proof_calls.append(identity) or destination
    )
    registered = registration.register(model.artifact_id)
    assert registered["status"] == "registered" and registered["loaded"] is False
    assert registered["runtime_profile"] == PUBLIC_M2_PROFILE
    assert registration.register(model.artifact_id) == registered
    assert registration.status(model.artifact_id) == registered
    assert proof_calls == [model.artifact_id, model.artifact_id]
    entry = service.selection(registered["selection_id"])
    assert entry["adapter"] == PUBLIC_M2_ADAPTER
    assert service.adapter_arguments(entry) == [
        "-m",
        "stpd.policy.public_m2_cli",
        "serve",
        "--config",
        str(service.entry_path(entry, "config")),
        "--manifest",
        str(service.entry_path(entry, "manifest")),
        "--binding-root",
        str(service.private_root),
    ]
    assert service._run_profile(entry["id"], "short") is True
    assert service._run_profile(entry["id"], "extended") is True
    assert service.runtime_profile(entry["id"])[0] == service.directory
    assert json.loads(service.entry_path(entry, "manifest").read_bytes())["adapter"][
        "protocol"
    ] == ("sts2.policy-runtime/decision-only-ndjson-4")
    assert (
        model.parameters.value()["epoch"] == 1 and model.parameters.value()["run_complete"] is False
    )


def test_public_m2_register_cannot_bypass_missing_runtime_contract(tmp_path, monkeypatch):
    advertise_public_reader(monkeypatch)
    export, model = metadata_export(tmp_path)
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    service = LocalModelService(config)
    exported = SimpleNamespace(
        status_for_model=lambda _: {
            "operation": {
                "model_id": model.artifact_id,
                "model_type": "public_m2",
                "status": "completed",
            }
        },
        verified_public_m2_for_registration=lambda *args, **kwargs: export,
    )
    registration = LocalModelRegistration(config, exported, service)

    def unavailable(**kwargs):
        raise BoundaryError("local_model_registration", "public_m2_runtime_contract_unavailable")

    monkeypatch.setattr(registration, "_public_m2_runtime", unavailable)
    with pytest.raises(BoundaryError, match="public_m2_runtime_contract_unavailable"):
        registration.register(model.artifact_id)
    assert not service.private_root.exists()


@pytest.mark.parametrize(
    "protocols",
    [
        None,
        (),
        ("sts2.policy-runtime/decision-only-ndjson-3",),
        ["sts2.policy-runtime/decision-only-ndjson-4"],
    ],
)
def test_installed_evidence_capability_blocks_public_registration_and_model_inspection(
    tmp_path,
    monkeypatch,
    protocols,
):
    import sts2_platform_evidence

    import stpd.public_m2_policy_installation as installation

    monkeypatch.setattr(
        sts2_platform_evidence, "SUPPORTED_POLICY_DECISION_PROTOCOLS", protocols, raising=False
    )
    identity = "a" * 64
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    service = LocalModelService(config)
    export_calls, runtime_calls, weights_calls = [], [], []
    exported = SimpleNamespace(
        status_for_model=lambda _: {
            "availability": "ready",
            "operation": {
                "model_id": identity,
                "model_type": "public_m2",
                "status": "completed",
                "verified_receipt": {"fixture": True},
            },
        },
        verified_public_m2_for_registration=lambda *args, **kwargs: export_calls.append(args),
    )
    registration = LocalModelRegistration(config, exported, service)
    monkeypatch.setattr(
        registration, "_native_runtime", lambda **kwargs: runtime_calls.append(kwargs)
    )
    monkeypatch.setattr(
        installation, "validate_package", lambda *args, **kwargs: weights_calls.append(args)
    )
    with pytest.raises(BoundaryError, match="public_m2_evidence_contract_unavailable"):
        registration.register(identity)
    assert registration.status(identity)["reason_code"] == "public_m2_evidence_contract_unavailable"
    inspected = installation.inspect(ROOT, {"config": "missing", "manifest": "missing"}, {}, {})
    assert inspected == {
        "policy_identity": {"status": "blocked", "code": "public_m2_evidence_contract_unavailable"}
    }
    assert export_calls == runtime_calls == weights_calls == []
    assert not service.private_root.exists()


def test_source_evidence_explicit_capability_matches_implemented_port4_reader():
    script = """
import json, sys
sys.path.insert(0, sys.argv[1])
import sts2_platform_evidence as sdk
from sts2_platform_evidence.agent_run_evidence import SUPPORTED_POLICY_DECISION_PROTOCOLS
assert type(sdk.SUPPORTED_POLICY_DECISION_PROTOCOLS) is tuple
assert sdk.SUPPORTED_POLICY_DECISION_PROTOCOLS == SUPPORTED_POLICY_DECISION_PROTOCOLS
print(json.dumps(list(sdk.SUPPORTED_POLICY_DECISION_PROTOCOLS)))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(REPOSITORY / "components/evidence")],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == [
        f"sts2.policy-runtime/decision-only-ndjson-{version}" for version in (1, 2, 3, 4)
    ]


def test_absent_evidence_package_is_an_explicit_public_m2_capability_failure(monkeypatch):
    from stpd.public_m2_policy_installation import require_evidence

    monkeypatch.setitem(sys.modules, "sts2_platform_evidence", None)
    with pytest.raises(BoundaryError, match="public_m2_evidence_contract_unavailable"):
        require_evidence()


def test_common_public_manifest_load_gate_rejects_an_uninstalled_port4_reader(
    tmp_path, monkeypatch
):
    import sts2_platform_evidence

    monkeypatch.delattr(
        sts2_platform_evidence, "SUPPORTED_POLICY_DECISION_PROTOCOLS", raising=False
    )
    service = LocalModelService(ProjectConfig(tmp_path / "state", "", "", None, combination()))
    manifest = tmp_path / "candidate.json"
    manifest.write_bytes(
        json_bytes(
            {
                "schema": "sts2.policy-runtime/policy-manifest-1",
                "adapter": {"protocol": "sts2.policy-runtime/decision-only-ndjson-4"},
            }
        )
    )
    node_calls = []
    monkeypatch.setattr(service, "_runtime_package", lambda *args: {})
    monkeypatch.setattr(
        "spireagent.workbench.local_models.subprocess.run",
        lambda *args, **kwargs: node_calls.append(args),
    )
    with pytest.raises(BoundaryError, match="public_m2_evidence_contract_unavailable"):
        service._public_manifest_contract(manifest)
    assert node_calls == []
