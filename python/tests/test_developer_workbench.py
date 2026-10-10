from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import tomllib
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import ModuleType
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from jsonschema import Draft202012Validator
from test_artifact_store_v1 import PRODUCER

from spireagent.artifact_contracts import Manifest, Parent, Payload
from spireagent.json_boundary import BoundaryError
from spireagent.workbench.__main__ import main
from spireagent.workbench.developer import (
    ROOT,
    LocalResearchWorkspaceConfig,
    ProjectConfig,
    combination,
    doctor,
    endpoint,
    evidence_identity,
    setup,
)
from spireagent.workbench.developer_cli import inspect_policy
from spireagent.workbench.developer_server import (
    Application,
    configuration_id,
    create_server,
    instance_lock,
    running,
    status_project,
    stop_project,
)
from spireagent.workbench.hub_client import HubClient


@pytest.fixture
def project(tmp_path):
    path = tmp_path / "project.json"
    setup(path, state_dir=tmp_path / "state", install=False)
    return path, ProjectConfig.load(path)


def test_shipped_combination_pins_the_locked_evidence_reader() -> None:
    """A green package test must not ship a profile rejected by the real doctor."""
    from spireagent.source import REPOSITORY_URLS

    pin = combination()["evidence_source_revision"]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    assert any(f"{url}@{pin}#" in dep for url in REPOSITORY_URLS
               for dep in project["project"]["dependencies"])
    installed = [p for p in lock["package"] if p["name"] == "rsgcsg-sts2-platform-evidence"]
    assert len(installed) == 1
    assert installed[0]["source"]["git"].endswith("#" + pin)


def test_setup_combination_schema_and_idempotency(project, tmp_path):
    path, config = project
    setup(path, state_dir=config.state_dir, install=False)
    for name, value in (
        ("developer-project-v1", config.to_dict()),
        ("developer-combination-v1", combination()),
    ):
        schema = json.loads((ROOT / f"schemas/{name}.schema.json").read_bytes())
        Draft202012Validator(schema).validate(value)
    with pytest.raises(BoundaryError, match="existing_config_differs"):
        setup(path, state_dir=config.state_dir, hub_url="https://hub.example", install=False)
    value = json.loads(path.read_bytes())
    value["combination"]["platform_source_revision"] = "f" * 40
    path.write_text(json.dumps(value))
    with pytest.raises(BoundaryError, match="combination_changed"):
        ProjectConfig.load(path)


def test_project_config_optional_local_research_workspace_preserves_legacy_files(project, tmp_path):
    path, config = project
    assert config.research_workspace is None
    value = json.loads(path.read_bytes())
    value["research_workspace"] = {
        "store_dir": str(tmp_path / "existing-store"),
        "registry_path": str(tmp_path / "existing-registry.sqlite"),
    }
    path.write_text(json.dumps(value))
    loaded = ProjectConfig.load(path)
    assert loaded.research_workspace is not None
    assert loaded.research_workspace.store_dir == tmp_path / "existing-store"
    assert loaded.research_workspace.registry_path == tmp_path / "existing-registry.sqlite"
    schema = json.loads((ROOT / "schemas/developer-project-v1.schema.json").read_bytes())
    Draft202012Validator(schema).validate(loaded.to_dict())
    setup(path, state_dir=config.state_dir, install=False)
    assert ProjectConfig.load(path).research_workspace == loaded.research_workspace


@pytest.fixture
def research_project(project, tmp_path):
    path, config = project
    workspace = LocalResearchWorkspaceConfig(
        tmp_path / "existing-store", tmp_path / "existing-registry.sqlite")
    workspace.store_dir.mkdir()
    (workspace.store_dir / "retained-artifact").write_bytes(b"immutable artifact")
    workspace.registry_path.write_bytes(b"existing research index")
    (workspace.store_dir / "curation.sqlite").write_bytes(b"existing use ledger")
    selected = replace(config, research_workspace=workspace)
    path.write_text(json.dumps(selected.to_dict()))
    return path, selected


@pytest.mark.parametrize("relocate", [False, True])
def test_explicit_setup_upgrade_preserves_selected_research_workspace(
    research_project, tmp_path, relocate,
):
    path, selected = research_project
    before = json.loads(path.read_bytes())
    before["combination"]["evidence_source_revision"] = "f" * 40
    path.write_text(json.dumps(before))
    workspace = selected.research_workspace
    assert workspace is not None
    retained = {item: item.read_bytes() for item in (
        workspace.store_dir / "retained-artifact", workspace.registry_path,
        workspace.store_dir / "curation.sqlite")}
    state = tmp_path / "new-state" if relocate else selected.state_dir

    result = setup(path, state_dir=state, install=False, replace_config=True)

    assert result["status"] == "configured"
    assert ProjectConfig.load(path) == replace(selected, state_dir=state)
    assert all(item.read_bytes() == contents for item, contents in retained.items())
    assert set(workspace.store_dir.iterdir()) == {
        workspace.store_dir / "retained-artifact", workspace.store_dir / "curation.sqlite"}
    assert not (state / "curation.sqlite").exists()
    assert not (state / "existing-store").exists()


def test_setup_upgrade_requires_explicit_replacement(research_project):
    path, selected = research_project
    previous = selected.to_dict()
    previous["combination"] = {**previous["combination"], "evidence_source_revision": "f" * 40}
    path.write_text(json.dumps(previous))
    before = path.read_bytes()

    with pytest.raises(BoundaryError, match="combination_changed_rerun_setup"):
        setup(path, state_dir=selected.state_dir, install=False)

    assert path.read_bytes() == before


def test_new_setup_does_not_adopt_another_configs_research_workspace(research_project, tmp_path):
    original, selected = research_project
    before = original.read_bytes()
    fresh = tmp_path / "fresh-project.json"

    setup(fresh, state_dir=selected.state_dir, install=False, replace_config=True)

    assert ProjectConfig.load(fresh).research_workspace is None
    assert "research_workspace" not in json.loads(fresh.read_bytes())
    assert original.read_bytes() == before


@pytest.mark.parametrize("locked_state", ["current", "new"])
def test_setup_replacement_respects_both_state_locks(research_project, tmp_path, locked_state):
    path, selected = research_project
    before = path.read_bytes()
    destination = tmp_path / "new-state"
    lock = selected.state_dir if locked_state == "current" else destination

    with (
        instance_lock(lock / "instance.lock"),
        pytest.raises(BoundaryError, match="already_running"),
    ):
        setup(path, state_dir=destination, install=False, replace_config=True)

    assert path.read_bytes() == before
    assert not (destination / "logs").exists()


def test_project_config_rejects_explicit_null_research_workspace(project):
    path, _ = project
    value = json.loads(path.read_bytes())
    value["research_workspace"] = None
    path.write_text(json.dumps(value))
    with pytest.raises(BoundaryError, match="missing_or_unknown_fields"):
        ProjectConfig.load(path)


@pytest.mark.parametrize(
    "url",
    [
        "http://public.example",
        "https://secret@hub.example",
        "https://hub.example?token=secret",
        "https://hub.example/#secret",
        "https://hub.example/path",
        "https://hub.example:invalid",
    ],
)
def test_configuration_rejects_credentials_and_unsafe_endpoints(url):
    with pytest.raises(BoundaryError):
        endpoint(url)
    assert endpoint("http://127.0.0.1:8765/") == "http://127.0.0.1:8765"


def test_public_dependency_identity_rejects_sibling_install(monkeypatch):
    class Distribution:
        version = "0.1.0"
        files = []

        def read_text(self, name):
            return json.dumps({"url": "file:///private/sibling", "dir_info": {"editable": True}})

    monkeypatch.setattr("importlib.metadata.distribution", lambda name: Distribution())
    assert evidence_identity("1" * 40)["status"] == "PIN_MISMATCH"


@pytest.fixture(params=["Pefect", "Perfect"])
def installed_evidence(tmp_path, monkeypatch, request):
    """A complete wheel-style identity without executing its package initializer."""
    site = tmp_path / "site-packages"
    package = site / "sts2_platform_evidence"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("raise AssertionError('must not import during doctor')\n")
    (package / "delivery_cli.py").write_text("raise AssertionError('must not run during doctor')\n")
    metadata = site / "rsgcsg_sts2_platform_evidence-0.1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: rsgcsg-sts2-platform-evidence\nVersion: 0.1.0\n")
    (metadata / "direct_url.json").write_text(
        json.dumps(
            {
                "url": f"https://github.com/rsgcsg/STS2-The-{request.param}-Defect-Project.git",
                "vcs_info": {"commit_id": "1" * 40},
                "subdirectory": "components/evidence",
            }
        )
    )
    rows = []
    for path in sorted(site.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            sha = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            # Installed wheel RECORD paths use forward slashes on every platform.
            rows.append(f"{path.relative_to(site).as_posix()},sha256={sha},{len(data)}\n")
    (metadata / "RECORD").write_text("".join(rows))
    distribution = importlib.metadata.Distribution.at(metadata)
    monkeypatch.setattr("importlib.metadata.distribution", lambda name: distribution)
    for name in list(sys.modules):
        if name == "sts2_platform_evidence" or name.startswith("sts2_platform_evidence."):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(site))
    return package, metadata


def test_evidence_record_binds_package_and_delivery_without_import(installed_evidence):
    identity = evidence_identity("1" * 40)
    assert identity["status"] == "PASS"
    assert identity["delivery_entrypoint_verified"] is True
    assert "sts2_platform_evidence" not in sys.modules


@pytest.mark.parametrize("already_loaded", [False, True])
def test_doctor_rejects_import_shadow_even_with_verified_record(
    installed_evidence, project, tmp_path, monkeypatch, already_loaded
):
    package, _ = installed_evidence
    if already_loaded:
        spec = importlib.util.spec_from_file_location(
            "sts2_platform_evidence", package / "__init__.py"
        )
        monkeypatch.setitem(
            sys.modules, "sts2_platform_evidence", importlib.util.module_from_spec(spec)
        )
    shadow = tmp_path / "shadow" / "sts2_platform_evidence"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("raise AssertionError('shadow must not run')\n")
    monkeypatch.syspath_prepend(str(shadow.parent))
    assert evidence_identity("1" * 40)["status"] == "IMPORT_ORIGIN_MISMATCH"
    _, initial = project
    delivery_file = tmp_path / "delivery.json"
    delivery_file.write_text('{"hub_url":"https://hub.example"}')
    config = ProjectConfig(
        initial.state_dir, "https://hub.example", "", delivery_file, initial.combination
    )
    monkeypatch.setattr(
        "spireagent.workbench.developer.dependency_checks",
        lambda _: {"evidence": evidence_identity("1" * 40)},
    )
    report = doctor(config)
    assert report["status"] == "BLOCKED"
    assert report["checks"]["delivery_tool"]["status"] == "NOT_VERIFIED"


def test_evidence_rejects_entrypoint_missing_from_record(installed_evidence):
    _, metadata = installed_evidence
    record = metadata / "RECORD"
    rows = record.read_text().splitlines(keepends=True)
    retained = [
        row for row in rows if not row.startswith("sts2_platform_evidence/delivery_cli.py,")
    ]
    assert len(rows) - len(retained) == 1, "the fixture must remove the verified entrypoint"
    record.write_text("".join(retained))
    assert evidence_identity("1" * 40)["status"] == "IMPORT_ORIGIN_MISMATCH"


@pytest.mark.parametrize("owner_status", ["PASS", "BLOCKED"])
def test_doctor_delegates_delivery_readiness_to_isolated_platform_owner(
    project, tmp_path, monkeypatch, owner_status
):
    _, initial = project
    delivery = tmp_path / "delivery.json"
    delivery.write_text("{}")  # The consumer must not maintain a second config parser.
    config = ProjectConfig(
        initial.state_dir, "https://hub.example", "", delivery, initial.combination
    )
    monkeypatch.setattr(
        "spireagent.workbench.developer.dependency_checks",
        lambda _: {"evidence": {"status": "PASS", "delivery_entrypoint_verified": True}},
    )
    monkeypatch.setattr("spireagent.workbench.developer.tool_identity", lambda: {})
    monkeypatch.setenv("STPD_HUB_ADMIN_TOKEN", "must-not-forward")
    monkeypatch.setenv("PYTHONPATH", "must-not-use")

    def owner(command, **kwargs):
        assert command[:5] == [
            sys.executable,
            "-I",
            "-m",
            "sts2_platform_evidence.delivery_cli",
            "doctor",
        ]
        assert command[-1] == str(delivery)
        assert "STPD_HUB_ADMIN_TOKEN" not in kwargs["env"]
        assert "PYTHONPATH" not in kwargs["env"]
        return subprocess.CompletedProcess(
            command,
            0 if owner_status == "PASS" else 1,
            json.dumps(
                {
                    "schema": "sts2.evidence/delivery-doctor-1",
                    "status": owner_status,
                    "hub_url": "https://hub.example",
                    "discovered_sessions": 7,
                    "checks": {"collection_release": {"status": owner_status}},
                }
            ).encode(),
            b"",
        )

    monkeypatch.setattr("spireagent.workbench.developer.subprocess.run", owner)
    report = doctor(config)
    assert report["status"] == owner_status
    assert report["checks"]["delivery_preflight"]["discovered_sessions"] == 7


@pytest.mark.parametrize("response", [b"not-json", b'{"status":"PASS"}'])
def test_doctor_does_not_accept_missing_or_old_platform_preflight(
    project, tmp_path, monkeypatch, response
):
    _, initial = project
    delivery = tmp_path / "delivery.json"
    delivery.write_text("{}")
    config = ProjectConfig(
        initial.state_dir, "https://hub.example", "", delivery, initial.combination
    )
    monkeypatch.setattr(
        "spireagent.workbench.developer.dependency_checks",
        lambda _: {"evidence": {"status": "PASS", "delivery_entrypoint_verified": True}},
    )
    monkeypatch.setattr("spireagent.workbench.developer.tool_identity", lambda: {})
    monkeypatch.setattr(
        "spireagent.workbench.developer.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(
            [], 0, response, b"private-diagnostic-must-not-return"
        ),
    )
    report = doctor(config)
    assert report["status"] == "BLOCKED"
    assert report["checks"]["delivery_preflight"]["status"] == "UNAVAILABLE"
    assert "private-diagnostic" not in json.dumps(report)


def test_evidence_rejects_loaded_entrypoint_mismatch(installed_evidence, tmp_path, monkeypatch):
    name = "sts2_platform_evidence.delivery_cli"
    module = ModuleType(name)
    module.__file__ = str(tmp_path / "unverified.py")
    module.__spec__ = importlib.util.spec_from_file_location(name, module.__file__)
    monkeypatch.setitem(sys.modules, name, module)
    assert evidence_identity("1" * 40)["status"] == "IMPORT_ORIGIN_MISMATCH"


def test_isolated_delivery_uses_installed_tool_from_hostile_cwd_and_pythonpath(tmp_path):
    shadow = tmp_path / "sts2_platform_evidence"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("raise AssertionError('shadow must not run')\n")
    environment = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-I", "-m", "sts2_platform_evidence.delivery_cli", "--help"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    # Isolating the Platform child does not replace or disable editable STPD.
    editable = subprocess.run(
        [sys.executable, "-I", "-c", "import stpd; print(stpd.__file__)"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert editable.returncode == 0, editable.stderr
    assert Path(editable.stdout.strip()).resolve() == (ROOT / "stpd/__init__.py").resolve()


def test_project_cli_redacts_config_errors(project, capsys):
    path, _ = project
    assert (
        main(
            [
                "project",
                "setup",
                "--config",
                str(path),
                "--skip-install",
                "--hub-url",
                "https://secret@hub.example",
            ]
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "invalid_endpoint" in output and "secret" not in output
    assert main(["project", "policy"]) == 1


def test_instance_lock_does_not_delete_another_live_owner(tmp_path):
    path = tmp_path / "lock"
    with (
        instance_lock(path),
        pytest.raises(BoundaryError, match="already_running"),
        instance_lock(path),
    ):
        pass
    with instance_lock(path):
        assert path.exists()


def test_local_server_auth_identity_and_safe_render(project):
    _, config = project
    app = Application(config)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(url + "/health", timeout=2) as response:
            assert json.load(response)["instance_id"] == app.instance_id
        with pytest.raises(HTTPError) as error:
            urlopen(Request(url + "/api/status", headers={"Host": "attacker.example"}), timeout=2)
        assert error.value.code == 403
        with pytest.raises(HTTPError) as error:
            urlopen(Request(url + "/stop", data=b""), timeout=2)
        assert error.value.code == 403
        (config.state_dir / "runtime.json").write_text(
            json.dumps(
                {
                    "port": server.server_port,
                    "instance_id": app.instance_id,
                    "configuration_id": configuration_id(config),
                    "control_token": app.control_token,
                }
            )
        )
        assert running(config)["instance_id"] == app.instance_id
        assert stop_project(config)["status"] == "stopping"
        thread.join(timeout=3)
        assert not thread.is_alive()
    finally:
        server.shutdown()
        server.server_close()
        app.close()


@pytest.mark.parametrize("malformed", [False, True])
def test_project_status_cli_reads_composed_response_and_rejects_malformed(
    project, monkeypatch, malformed
):
    path, config = project
    app = Application(config)
    snapshot = app.snapshot
    requests = []

    def observed_snapshot():
        requests.append("snapshot")
        if malformed:
            return []
        # A normal composed observation can exceed the short local health timeout.
        time.sleep(2.25)
        return snapshot()

    monkeypatch.setattr(app, "snapshot", observed_snapshot)
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (config.state_dir / "runtime.json").write_text(
        json.dumps(
            {
                "port": server.server_port,
                "instance_id": app.instance_id,
                "configuration_id": configuration_id(config),
                "control_token": app.control_token,
            }
        )
    )
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "spireagent.workbench",
                "project",
                "status",
                "--config",
                str(path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        value = json.loads(result.stdout)
        if malformed:
            assert result.returncode == 1
            assert value == {"status": "FAIL", "code": "invalid_local_response"}
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            assert value["schema"] == "stpd/developer-status-v1"
            assert value["instance_id"] == app.instance_id
            assert value["delivery"] == {"status": "not_configured"}
        assert requests == ["snapshot"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        app.close()


def test_status_timeout_does_not_extend_health_stop_or_hide_errors(project, monkeypatch, capsys):
    path, config = project
    (config.state_dir / "runtime.json").write_text(
        json.dumps(
            {
                "port": 12345,
                "instance_id": "test-instance",
                "configuration_id": configuration_id(config),
                "control_token": "test-control",
            }
        )
    )
    requests = []

    class Opener:
        def open(self, request, *, timeout):
            requests.append((request.full_url, timeout))
            if request.full_url.endswith("/api/status"):
                raise TimeoutError("private diagnostic must not escape")
            return BytesIO(b'{"instance_id":"test-instance","status":"stopping"}')

    monkeypatch.setattr("spireagent.workbench.developer_server.build_opener", lambda *_: Opener())
    assert main(["project", "status", "--config", str(path)]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "FAIL", "code": "TimeoutError"}
    assert stop_project(config)["status"] == "stopping"
    assert requests == [
        ("http://127.0.0.1:12345/health", 2),
        ("http://127.0.0.1:12345/api/status", 15),
        ("http://127.0.0.1:12345/health", 2),
        ("http://127.0.0.1:12345/stop", 2),
    ]
    (config.state_dir / "runtime.json").unlink()
    assert status_project(config) == {"status": "not_running"}
    assert len(requests) == 4


def test_delivery_is_one_owned_public_tool_child(project, tmp_path, monkeypatch):
    _, initial = project
    config_file = tmp_path / "delivery.json"
    config_file.write_text("{}")
    config = ProjectConfig(initial.state_dir, "", "", config_file, initial.combination)
    commands = []
    options = []

    class Child:
        def __init__(self):
            self.finished = False

        def poll(self):
            return 0 if self.finished else None

        def terminate(self):
            self.finished = True

        def wait(self, timeout):
            return 0

    child = Child()

    def launch(command, **kwargs):
        commands.append(command)
        options.append(kwargs)
        return child

    app = Application(config)
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "shadow"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "shadow-home"))
    monkeypatch.setenv("STPD_HUB_ADMIN_TOKEN", "test-admin")
    monkeypatch.setenv("STPD_HUB_TOKEN", "test-device")
    monkeypatch.setattr("spireagent.workbench.developer_server.subprocess.Popen", launch)
    app.start_delivery()
    assert len(commands) == 1
    assert commands[0][1:] == [
        "-I",
        "-m",
        "sts2_platform_evidence.delivery_cli",
        "run",
        "--config",
        str(config_file),
    ]
    assert options[0]["cwd"] == config.state_dir
    environment = options[0]["env"]
    assert not {"PYTHONPATH", "PYTHONHOME", "STPD_HUB_ADMIN_TOKEN"} & environment.keys()
    assert environment["STPD_HUB_TOKEN"] == "test-device"

    def status(command, **kwargs):
        assert command[1:4] == ["-I", "-m", "sts2_platform_evidence.delivery_cli"]
        assert kwargs["cwd"] == config.state_dir and kwargs["env"] == environment
        return subprocess.CompletedProcess(command, 0, b'{"pending":1}')

    monkeypatch.setattr("spireagent.workbench.developer_server.subprocess.run", status)
    assert app.delivery_status()["outbox"] == {"pending": 1}
    app.close()
    assert child.finished


@pytest.fixture
def hub(monkeypatch):
    payload = b"verified-model-weights"
    manifest = Manifest(
        "model",
        PRODUCER,
        parents=(Parent("run", "a" * 64),),
        payloads=(Payload("weights", hashlib.sha256(payload).hexdigest(), len(payload)),),
    )
    requests = []
    state = {"corrupt": False, "redirect": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            if self.headers.get("Authorization") != "Bearer test-hub-secret":
                self.send_error(401)
                return
            if state["redirect"]:
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/credential-sink")
                self.end_headers()
                return
            if self.path == f"/v1/artifacts/{manifest.artifact_id}":
                value = manifest.to_bytes()
            elif self.path == f"/v1/artifacts/{manifest.artifact_id}/payloads/weights":
                value = b"corrupt" if state["corrupt"] else payload
            elif self.path in {"/v1/status", "/v1/uploads", "/v1/jobs", "/v1/incidents"}:
                value = b'{"items":[]}'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(value)))
            self.end_headers()
            self.wfile.write(value)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("STPD_HUB_TOKEN", "test-hub-secret")
    try:
        yield HubClient(f"http://127.0.0.1:{server.server_port}"), manifest, requests, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_exact_result_download_preserves_manifest_without_parent_payloads(hub, tmp_path):
    client, manifest, requests, _ = hub
    result = client.download(manifest.artifact_id, tmp_path)
    assert result["parents_downloaded"] is False
    assert result["model_load_validated"] is False
    assert (tmp_path / manifest.artifact_id / "manifest.json").read_bytes() == manifest.to_bytes()
    assert "a" * 64 not in str(requests)
    first = len(requests)
    client.download(manifest.artifact_id, tmp_path)
    assert (
        len(requests) == first + 1
    )  # Verify manifest and cached payload; do not redownload bytes.
    file = tmp_path / manifest.artifact_id / result["payloads"][0]["file"]
    file.write_bytes(b"tampered")
    with pytest.raises(BoundaryError, match="existing_payload_mismatch"):
        client.download(manifest.artifact_id, tmp_path)


def test_hub_status_and_background_download_use_separate_timeouts(tmp_path):
    payload = b"verified-model-weights"
    manifest = Manifest(
        "model",
        PRODUCER,
        parents=(Parent("run", "a" * 64),),
        payloads=(Payload("weights", hashlib.sha256(payload).hexdigest(), len(payload)),),
    )
    requests = []

    class Opener:
        def open(self, request, *, timeout):
            requests.append((request.full_url, timeout))
            route = request.full_url.removeprefix("https://hub.example")
            if route == f"/v1/artifacts/{manifest.artifact_id}":
                body = manifest.to_bytes()
            elif route == f"/v1/artifacts/{manifest.artifact_id}/payloads/weights":
                body = payload
            elif route in {"/v1/status", "/v1/uploads", "/v1/jobs", "/v1/incidents"}:
                body = b'{"items":[]}'
            else:
                raise AssertionError(f"unexpected Hub route: {route}")
            return BytesIO(body)

    client = HubClient("https://hub.example", timeout=2, token=lambda: "test-token")
    client.opener = Opener()
    assert client.snapshot()["status"]["status"] == "available"
    client.download(manifest.artifact_id, tmp_path)

    assert [timeout for _, timeout in requests] == [2, 2, 2, 2, 30, 30]


def test_corrupt_download_never_publishes_success_receipt(hub, tmp_path):
    client, manifest, _, state = hub
    state["corrupt"] = True
    with pytest.raises(BoundaryError, match="payload_integrity_failure"):
        client.download(manifest.artifact_id, tmp_path)
    assert not (tmp_path / manifest.artifact_id / "download.json").exists()
    assert not list(tmp_path.rglob(".pending-*"))


def test_hub_redirection_is_fail_closed_and_token_is_not_exposed(hub):
    client, _, requests, state = hub
    state["redirect"] = True
    with pytest.raises(BoundaryError, match="http_302") as error:
        client.get("/v1/status")
    assert "test-hub-secret" not in str(error.value)
    assert requests == ["/v1/status"]
    assert client.snapshot()["jobs"]["status"] == "unavailable"


def test_policy_inspection_never_loads_or_activates_weights():
    report = inspect_policy(ROOT / "policy-manifests/s1-policy-adapter-v2.json")
    assert report["status"] == "manifest_inspected"
    assert report["loaded"] is False and report["activated"] is False


def test_setup_bootstraps_without_research_dependencies(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "project.json"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "spireagent.workbench",
            "project",
            "setup",
            "--skip-install",
            "--config",
            str(path),
            "--state-dir",
            str(tmp_path / "state"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "configured"
    assert ProjectConfig.load(path).state_dir == (tmp_path / "state").resolve()


def test_projection_public_exports_remain_available():
    from spireagent.workbench import analyze, project, render_html
    from spireagent.workbench.analysis import analyze as analysis
    from spireagent.workbench.dashboard import project as projection
    from spireagent.workbench.dashboard import render_html as renderer

    assert analyze is analysis and project is projection and render_html is renderer


def test_invalid_owner_config_does_not_invent_endpoint_mismatch(project, tmp_path, monkeypatch):
    _, initial = project
    delivery = tmp_path / "delivery.json"
    delivery.write_text("{}")
    config = ProjectConfig(
        initial.state_dir, "https://hub.example", "", delivery, initial.combination
    )
    monkeypatch.setattr(
        "spireagent.workbench.developer.dependency_checks",
        lambda _: {"evidence": {"status": "PASS", "delivery_entrypoint_verified": True}},
    )
    monkeypatch.setattr("spireagent.workbench.developer.tool_identity", lambda: {})
    result = {
        "schema": "sts2.evidence/delivery-doctor-1",
        "status": "BLOCKED",
        "checks": {"configuration": {"status": "INVALID"}},
    }
    monkeypatch.setattr(
        "spireagent.workbench.developer.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess([], 1, json.dumps(result).encode(), b""),
    )
    report = doctor(config)
    assert report["status"] == "BLOCKED"
    assert report["checks"]["delivery_hub"]["status"] == "NOT_CHECKED"


@pytest.mark.parametrize("spelling", ["Pefect", "Perfect"])
def test_repository_rename_preserves_combination_identity(tmp_path, spelling):
    value = combination()
    value["platform_repository"] = (
        f"https://github.com/rsgcsg/STS2-The-{spelling}-Defect-Project.git"
    )
    path = tmp_path / "configs/developer/combination-v1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(value))
    assert combination(tmp_path) == value
    schema = json.loads((ROOT / "schemas/developer-combination-v1.schema.json").read_bytes())
    Draft202012Validator(schema).validate(value)


def test_repository_rename_does_not_accept_unrelated_repository(tmp_path):
    value = combination()
    value["platform_repository"] = "https://github.com/other/STS2-The-Perfect-Defect-Project.git"
    path = tmp_path / "configs/developer/combination-v1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(value))
    with pytest.raises(BoundaryError):
        combination(tmp_path)


@pytest.mark.parametrize("platform", [
    "", "http://127.0.0.1:15526", "http://localhost:15526",
    "http://127.0.0.1:25526", "https://other-host.example.invalid",
])
@pytest.mark.parametrize("interrupted", [False, True])
def test_native_workbench_registration_follows_server_lifecycle(
    project, monkeypatch, platform, interrupted
):
    from dataclasses import replace

    from spireagent.workbench import developer_server

    path, original = project
    config = replace(original, platform_url=platform)
    native = platform in {"http://127.0.0.1:15526", "http://localhost:15526"}
    events = []
    real_create = developer_server.create_server

    class Registration:
        def close(self):
            events.append("unregister")

    def register(url, instance_id, *, access):
        runtime = json.loads((config.state_dir / "runtime.json").read_bytes())
        assert url == f"http://127.0.0.1:{runtime['port']}/"
        assert instance_id == runtime["instance_id"]
        assert access.app.instance_id == instance_id
        assert access.app.config == config and access.app.config_path == path
        events.append("register")
        return Registration()

    def create(app):
        server = real_create(app)

        def run(*, poll_interval):
            assert events == (["register"] if native else [])
            events.append("serve")
            if interrupted:
                raise RuntimeError("synthetic_server_exit")

        server.serve_forever = run
        return server

    monkeypatch.setattr(developer_server, "doctor", lambda _: {"status": "PASS"})
    monkeypatch.setattr(developer_server, "start_workbench_registration", register)
    monkeypatch.setattr(developer_server, "create_server", create)
    if interrupted:
        with pytest.raises(RuntimeError, match="synthetic_server_exit"):
            developer_server.serve(config, config_path=path)
    else:
        assert developer_server.serve(config, config_path=path) == {"status": "stopped"}
    assert events == (["register", "serve", "unregister"] if native else ["serve"])
    assert not (config.state_dir / "runtime.json").exists()
