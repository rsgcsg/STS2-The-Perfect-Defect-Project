"""Subprocess campaign CLI coverage using frozen-source, verified synthetic PublicM2."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PYTHON_ROOT.parent
CAMPAIGN_SCHEMA = "stpd/m2-campaign-status-v1"


_OFFLINE_CHILD = r'''
# Test-only bootstrap: install restrictions in this actual child before APIs load.
import hashlib, json, os, runpy, sys
from pathlib import Path
mode, scope_arg, worker_arg, target = sys.argv[1:5]
scope, worker = Path(scope_arg).resolve(), Path(worker_arg).resolve()
temporary = Path(os.environ["TMPDIR"]).resolve()
if mode not in {"setup", "execute", "check", "recover"} or not scope.is_relative_to(temporary):
    raise RuntimeError("non_synthetic_child_scope")
if not worker.is_relative_to(temporary):
    raise RuntimeError("non_synthetic_worker")
original = ([sys.executable, "-I", "-c", target] + sys.argv[5:] if mode == "setup"
            else [sys.executable, "-I", target] + sys.argv[5:])
log_path = os.environ.get("M2_CLI_CHILD_CALL_LOG")
def record(kind, argv=None, cwd=None):
    if log_path:
        with open(log_path, "a") as stream:
            row = {"kind": kind, "argv": argv, "cwd": cwd}
            stream.write(json.dumps(row, sort_keys=True) + "\n")
record("test_child_bootstrap", original, str(Path.cwd()))
git_reads = {("git", "rev-parse", "HEAD"), ("git", "status", "--porcelain"),
             ("git", "rev-parse", "--show-prefix"), ("git", "show", "HEAD:uv.lock")}
def audit(event, args):
    if event.startswith("socket.") and event != "socket.__new__":
        record("network_blocked")
        raise RuntimeError("offline_child_network_denied")
    if event == "import":
        name = args[0].split(".", 1)[0]
        forbidden = {"modal"} if mode != "recover" else {"modal", "torch", "stpd", "spireagent"}
        if name in forbidden:
            record("import_blocked:" + name)
            raise RuntimeError("offline_child_import_denied:" + name)
    if event == "subprocess.Popen":
        command, cwd = args[1], args[2]
        argv = tuple(os.fsdecode(x) for x in command) if isinstance(command, (list, tuple)) else ()
        if (mode != "recover" and argv in git_reads
                and cwd is not None and Path(cwd).resolve() == worker):
            record("source_identity_local_git", list(argv), str(worker))
            return
        if mode in {"setup", "execute"} and argv == ("uname", "-p") and cwd is None:
            record("local_platform_processor", list(argv))
            return
        if (mode in {"setup", "execute"} and len(argv) == 3 and argv[:2] == ("file", "-b")
                and cwd is None and Path(argv[2]).resolve() == Path(sys.executable).resolve()):
            record("local_platform_interpreter_architecture", list(argv))
            return
        # Python 3.11 ctypes.util.find_library reads the Linux linker cache.
        if (sys.platform == "linux" and mode in {"setup", "execute"}
                and argv == ("/sbin/ldconfig", "-p") and cwd is None):
            record("local_linux_linker_cache", list(argv))
            return
        record("subprocess_blocked", list(argv), None if cwd is None else str(cwd))
        raise RuntimeError("offline_child_subprocess_denied")
sys.addaudithook(audit)
if mode == "setup":
    if (len(sys.argv) != 7 or Path(sys.argv[5]).resolve() != worker
            or not Path(sys.argv[6]).resolve().is_relative_to(scope)):
        raise RuntimeError("synthetic_setup_paths_required")
    sys.argv = ["-c"] + sys.argv[5:]
    exec(compile(target, "<synthetic-fixture-setup>", "exec"), {"__name__": "__main__"})
else:
    arguments = sys.argv[5:]
    if arguments[:1] != ["--config"] or arguments[2] != mode:
        raise RuntimeError("exact_cli_arguments_required")
    config = Path(arguments[1]).resolve()
    if not config.is_relative_to(scope):
        raise RuntimeError("synthetic_config_required")
    value = json.loads(config.read_bytes())
    if Path(value["worker_python_root"]).resolve() != worker:
        raise RuntimeError("synthetic_worker_binding_required")
    for key in ("project_config", "store_root", "store_lock", "journal_root", "approval_dir"):
        if not Path(value[key]).resolve().is_relative_to(scope):
            raise RuntimeError("synthetic_owner_store_paths_required")
    if not Path(value["provider_adapter"]["path"]).resolve().is_relative_to(scope):
        raise RuntimeError("synthetic_provider_path_required")
    script = Path(target).resolve()
    if script.name != "public_m2_campaign.py" or script.parent.name != "tools":
        raise RuntimeError("actual_cli_entrypoint_required")
    sys.argv = [str(script)] + arguments
    runpy.run_path(str(script), run_name="__main__")
'''


@pytest.mark.parametrize(("platform", "mode", "argv", "cwd", "allowed"), [
    ("linux", "setup", ("/sbin/ldconfig", "-p"), None, True),
    ("linux", "execute", ("/sbin/ldconfig", "-p"), None, True),
    ("linux", "recover", ("/sbin/ldconfig", "-p"), None, False),
    ("linux", "check", ("/sbin/ldconfig", "-p"), None, False),
    ("darwin", "execute", ("/sbin/ldconfig", "-p"), None, False),
    ("win32", "setup", ("/sbin/ldconfig", "-p"), None, False),
    ("linux", "execute", ("ldconfig", "-p"), None, False),
    ("linux", "execute", ("/sbin/ldconfig",), None, False),
    ("linux", "execute", ("/sbin/ldconfig", "-p", "extra"), None, False),
    ("linux", "execute", ("/sbin/ldconfig", "-v"), None, False),
    ("linux", "execute", ("/sbin/ldconfig", "-p"), "/tmp", False),
])
def test_offline_child_linux_linker_cache_guard(
    platform: str, mode: str, argv: tuple[str, ...], cwd: str | None, allowed: bool,
) -> None:
    # Exercise the exact child audit function without launching a subprocess,
    # registering a process-wide hook, or importing numerical/provider APIs.
    audit_node = next(node for node in ast.parse(_OFFLINE_CHILD).body
                      if isinstance(node, ast.FunctionDef) and node.name == "audit")
    records: list[tuple[str, object, object]] = []

    def record(kind: str, argv: object = None, cwd: object = None) -> None:
        records.append((kind, argv, cwd))

    namespace: dict[str, Any] = {
        "os": os, "Path": Path, "mode": mode, "git_reads": set(),
        "sys": SimpleNamespace(platform=platform, executable=sys.executable),
        "record": record,
    }
    exec(compile(ast.Module(body=[audit_node], type_ignores=[]),
                 "<offline-child-audit-guard>", "exec"), namespace)
    audit = namespace["audit"]
    if allowed:
        audit("subprocess.Popen", (argv[0], list(argv), cwd, None))
        assert records == [("local_linux_linker_cache", list(argv), None)]
    else:
        with pytest.raises(RuntimeError, match="offline_child_subprocess_denied"):
            audit("subprocess.Popen", (argv[0], list(argv), cwd, None))
        assert records == [("subprocess_blocked", list(argv), cwd)]


def _offline_argv(
    scope: Path, worker: Path, mode: str, target: str, arguments: list[str],
) -> list[str]:
    return [sys.executable, "-I", "-c", _OFFLINE_CHILD,
            mode, str(scope), str(worker), target, *arguments]


def _offline_env(scope: Path) -> dict[str, str]:
    environment = _subprocess_env()
    # Both the synthetic worker and workspace are children of this case root.
    # Set the child's environment explicitly on Linux/Windows as well as macOS.
    for name in ("TMPDIR", "TMP", "TEMP"):
        environment[name] = str(scope.parent.resolve())
    return environment


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def _frozen_python(root: Path) -> Path:
    worker = root / "worker-python"
    worker.mkdir()
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", "HEAD:python"], cwd=REPO_ROOT,
    )
    with tarfile.open(fileobj=BytesIO(archive), mode="r:") as source:
        source.extractall(worker, filter="data")
    listed = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
    ).split(b"\0")
    for raw_name in listed:
        if not raw_name:
            continue
        relative = Path(os.fsdecode(raw_name))
        if not relative.parts or relative.parts[0] != "python":
            continue
        source_path = REPO_ROOT / relative
        target = worker.joinpath(*relative.parts[1:])
        if source_path.is_symlink():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
            continue
        if not source_path.exists():
            target.unlink(missing_ok=True)
        elif source_path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target)
    for directory, subdirectories, files in os.walk(worker, topdown=True, followlinks=False):
        base = Path(directory)
        for name in tuple(subdirectories):
            item = base / name
            if item.is_symlink():
                item.unlink()
                subdirectories.remove(name)
        for name in files:
            item = base / name
            if item.is_symlink():
                item.unlink()
    env = _subprocess_env()
    env.update({
        "GIT_AUTHOR_NAME": "Synthetic Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Synthetic Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    })
    subprocess.run(["git", "init", "-q"], cwd=worker, env=env, check=True)
    subprocess.run(["git", "add", "-A"], cwd=worker, env=env, check=True)
    subprocess.run(["git", "commit", "-qm", "frozen synthetic worker source"],
                   cwd=worker, env=env, check=True)
    return worker


_FIXTURE_SETUP = r'''
import hashlib, json, sys
from dataclasses import asdict
from pathlib import Path
worker = Path(sys.argv[1])
sys.path.insert(0, str(worker))
sys.path.insert(0, str(worker / "tests"))
from _pytest.monkeypatch import MonkeyPatch
from spireagent.source import source_identity
from spireagent.json_boundary import json_bytes
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import ManifestArtifactStore
from m2_campaign_fixture import prepared_m2_campaign
producer = source_identity(worker)
fixture = prepared_m2_campaign(Path(sys.argv[2]), MonkeyPatch(), producer=producer)
run_config = fixture.config
config_bytes = json_bytes(asdict(run_config))
binding = {
    "run_id": fixture.run.artifact_id,
    "max_requests": 20,
    "dataset_id": fixture.dataset_id,
    "allocation_id": fixture.allocation.artifact_id,
    "source_view_id": fixture.view.artifact_id,
    "operation_id": fixture.training_admission["operation_id"],
    "evaluation_operation_id": fixture.dev_admission["evaluation_operation_id"],
    "input_identity": fixture.training_input.identity,
    "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
    "training_admission_sha256": hashlib.sha256(json_bytes(fixture.training_admission)).hexdigest(),
    "dev_admission_sha256": hashlib.sha256(json_bytes(fixture.dev_admission)).hexdigest(),
    "train_decisions": sum(
        len(c.steps) for c in fixture.training_input.chains if c.split == "train"
    ),
    "dev_decisions": sum(len(c.steps) for c in fixture.training_input.chains if c.split == "dev"),
}
run_manifest = fixture.store.get_manifest(fixture.run.artifact_id)
training_manifest = fixture.store.get_manifest(run_manifest.parent("training_input"))
training_payload = training_manifest.payload("training_input")
tokenizer_payload = training_manifest.payload("state_tokenizer")
train_labels = binding["train_decisions"]
dev_labels = binding["dev_decisions"]
evidence_root = Path(fixture.project_config).parent / "campaign-evidence"
evidence_root.mkdir(parents=True, exist_ok=True)
intake = {
    "schema": "stpd/public-m2-intake-evidence-v1",
    "status": "prepared_not_trained",
    "producer": producer.to_dict(),
    "dataset_id": fixture.dataset_id,
    "allocation_id": fixture.allocation.artifact_id,
    "source_view_id": fixture.view.artifact_id,
    "tokenizer_sha256": tokenizer_payload.sha256,
    "codec_fit_calls": 1,
    "codec_fit_labels": train_labels,
    "official_source_projection_calls": 1,
    "products": [{
        "input_identity": fixture.training_input.identity,
        "sha256": training_payload.sha256,
        "bytes": training_payload.size,
        "train_labels": train_labels,
        "dev_labels": dev_labels,
    }],
}
intake_raw = json_bytes(intake)
intake_path = evidence_root / "intake.json"
intake_path.write_bytes(intake_raw)
prepared = {
    "schema": "stpd/m2-prepared-run-evidence-v1",
    "producer": producer.to_dict(),
    "intake_receipt_sha256": hashlib.sha256(intake_raw).hexdigest(),
    "runs": {fixture.run.artifact_id: {
        "run_id": fixture.run.artifact_id,
        "dataset_id": fixture.dataset_id,
        "allocation_id": fixture.allocation.artifact_id,
        "source_view_id": fixture.view.artifact_id,
        "operation_id": binding["operation_id"],
        "evaluation_operation_id": binding["evaluation_operation_id"],
        "input_identity": fixture.training_input.identity,
        "config": asdict(run_config),
        "training_input_payload_sha256": training_payload.sha256,
        "state_tokenizer_sha256": tokenizer_payload.sha256,
        "receipt_sha256": hashlib.sha256(intake_raw).hexdigest(),
        "intake_producer": producer.to_dict(),
    }},
}
prepared_raw = json_bytes(prepared)
prepared_path = evidence_root / "prepared.json"
prepared_path.write_bytes(prepared_raw)
binding["preparation_sha256"] = hashlib.sha256(prepared_raw).hexdigest()
print(json.dumps({
    "project_config": str(fixture.project_config),
    "store_root": str(fixture.store.blobs.root),
    "producer": producer.to_dict(),
    "runtime": fixture.runtime,
    "binding": binding,
    "preparation_evidence": [
        {"path": str(prepared_path), "sha256": hashlib.sha256(prepared_raw).hexdigest()},
        {"path": str(intake_path), "sha256": hashlib.sha256(intake_raw).hexdigest()},
    ],
    "run_config": asdict(run_config),
    "training_windows_per_epoch": sum(
        (len(c.steps) + run_config.window_steps - 1) // run_config.window_steps
        for c in fixture.training_input.chains if c.split == "train"
    ),
}, sort_keys=True))
'''


_FAKE_PROVIDER = r'''"""Local transport shim that invokes the actual CPU remote worker."""
import hashlib
import json
from pathlib import Path

from stpd.workers.public_m2_remote import execute_public_m2_remote_request


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _write_calls(path, action, attempt):
    path.mkdir(parents=True, exist_ok=True)
    calls_path = path / "calls.json"
    calls = json.loads(calls_path.read_text()) if calls_path.exists() else []
    calls.append({"action": action, "attempt_id": attempt.attempt_id})
    calls_path.write_text(json.dumps(calls, sort_keys=True, separators=(",", ":")))


def create_provider(settings):
    class LocalRemoteTransport:
        def __init__(self):
            self.root = Path(__file__).with_name("fake-transport-state")

        def prepare(self, attempt, request, approval):
            _write_calls(self.root, "prepare", attempt)
            return json.dumps({"attempt_id": attempt.attempt_id,
                               "request_sha256": _sha(request)}, sort_keys=True).encode()

        def submit(self, attempt, target, request):
            _write_calls(self.root, "submit", attempt)
            expected = json.loads(target)
            if expected != {"attempt_id": attempt.attempt_id, "request_sha256": _sha(request)}:
                raise RuntimeError("target_request_mismatch")
            (self.root / (attempt.attempt_id + ".request")).write_bytes(request)
            return attempt.attempt_id.encode("ascii")

        def poll(self, attempt, target, handle):
            _write_calls(self.root, "poll", attempt)
            if handle != attempt.attempt_id.encode("ascii"):
                raise RuntimeError("handle_mismatch")
            request = (self.root / (attempt.attempt_id + ".request")).read_bytes()
            return execute_public_m2_remote_request(request, request_sha256=_sha(request))

        def stop_and_confirm(self, attempt, target, handle):
            _write_calls(self.root, "stop", attempt)
            return json.dumps({"attempt_id": attempt.attempt_id,
                               "target_sha256": _sha(target),
                               "handle_sha256": None if handle is None else _sha(handle),
                               "stopped": True}, sort_keys=True).encode()

    return LocalRemoteTransport()
'''


def _receipt(root: Path, name: str, content: dict[str, Any]) -> dict[str, str]:
    raw = _canonical(content)
    path = root / name
    if path.exists() and path.read_bytes() != raw:
        raise AssertionError("synthetic observation receipt changed")
    path.write_bytes(raw)
    return {"path": str(path), "sha256": _sha(raw)}


def _operator_once(
    approval_dir: Path, provider_sha: str, *, raw_ceiling: str = "0.01", runtime: dict,
) -> None:
    for request_path in approval_dir.glob("*.request.json"):
        attempt_id = request_path.name.removesuffix(".request.json")
        grant_path = approval_dir / f"{attempt_id}.grant.json"
        pin_path = approval_dir / f"{attempt_id}.approved-sha256"
        if grant_path.exists() or pin_path.exists():
            continue
        request = json.loads(request_path.read_bytes())
        now = datetime.now(UTC)
        runtime_reference = _receipt(approval_dir, f"{attempt_id}.runtime.json", runtime)
        grant = {
            "schema": "stpd/m2-campaign-attempt-grant-v1",
            "config_sha256": request["config_sha256"],
            "attempt": request["attempt"],
            "request_sha256": request["request_sha256"],
            "producer": request["producer"],
            "provider_adapter_sha256": provider_sha,
            "raw_ceiling_usd": raw_ceiling,
            "expires_at_utc": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "provider": {"adapter": "test-local-cpu-remote", "external_network": False,
                         "runtime_receipt": runtime_reference},
        }
        grant_raw = _canonical(grant)
        grant_path.write_bytes(grant_raw)
        pin_path.write_text(_sha(grant_raw) + "\n")
        account = _receipt(approval_dir, f"{attempt_id}.account.json",
                           {"schema": "synthetic-account-observation-v1", "active_gpu": 0})
        billing = _receipt(
            approval_dir, f"{attempt_id}.billing.json",
            {"schema": "synthetic-billing-observation-v1", "raw_exposure_usd": "0.00"},
        )
        fresh = {
            "schema": "stpd/m2-campaign-fresh-observation-v1",
            "config_sha256": request["config_sha256"],
            "attempt_id": attempt_id,
            "request_sha256": request["request_sha256"],
            "grant_sha256": _sha(grant_raw),
            "observed_at_utc": now.isoformat().replace("+00:00", "Z"),
            "external_active_gpu": 0,
            "workspace_raw_exposure_excluding_this_attempt_usd": "0.00",
            "account_receipt": account,
            "billing_receipt": billing,
        }
        (approval_dir / f"{attempt_id}.fresh.json").write_bytes(_canonical(fresh))


def _run_cli(
    config_path: Path, command: str, *, pause: int | None = None,
    operator: bool = False, timeout: int = 900, offline: bool = False,
    raw_ceiling: str = "0.01",
) -> tuple[subprocess.CompletedProcess[str], list[dict]]:
    script = PYTHON_ROOT / "tools" / "public_m2_campaign.py"
    args = [sys.executable, "-I", str(script), "--config", str(config_path), command]
    if pause is not None:
        args.extend(["--pause-after-accepted", str(pause)])
    if offline:
        value = json.loads(config_path.read_bytes())
        args = _offline_argv(config_path.parent, Path(value["worker_python_root"]), command,
                             str(script), args[3:])
    env = _offline_env(config_path.parent) if offline else _subprocess_env()
    process = subprocess.Popen(args, cwd=PYTHON_ROOT, env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=1)
    errors: list[str] = []
    settings = json.loads(config_path.read_bytes())

    def operate() -> None:
        deadline = time.monotonic() + timeout
        while process.poll() is None and time.monotonic() < deadline:
            if operator:
                try:
                    _operator_once(Path(settings["approval_dir"]),
                                   settings["provider_adapter"]["sha256"], raw_ceiling=raw_ceiling,
                                   runtime=settings["runtime"])
                except Exception as error:  # surfaced after process exit for useful test output
                    errors.append(repr(error))
                    return
            time.sleep(0.02)

    worker_thread = threading.Thread(target=operate, daemon=True)
    worker_thread.start()
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        process.kill()
        stdout, stderr = process.communicate()
        raise AssertionError(
            f"campaign CLI timed out\nstdout={stdout}\nstderr={stderr}"
        ) from error
    worker_thread.join(timeout=2)
    if errors:
        raise AssertionError("synthetic root operator failed: " + errors[0])
    status = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if value.get("schema") == CAMPAIGN_SCHEMA:
            status.append(value)
    completed = subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    return completed, status


def _campaign_files(tmp_path: Path, *, offline: bool = False) -> tuple[Path, dict[str, Any], Path]:
    worker = _frozen_python(tmp_path)
    workspace = tmp_path / "campaign-workspace"
    workspace.mkdir()
    env = _offline_env(workspace) if offline else _subprocess_env()
    setup_args = [sys.executable, "-I", "-c", _FIXTURE_SETUP, str(worker), str(workspace / "data")]
    if offline:
        setup_args = _offline_argv(workspace, worker, "setup", _FIXTURE_SETUP,
                                   [str(worker), str(workspace / "data")])
    setup = subprocess.run(
        setup_args,
        cwd=worker, env=env, text=True, capture_output=True, check=False, timeout=240,
    )
    assert setup.returncode == 0, f"fixture setup failed\n{setup.stdout}\n{setup.stderr}"
    artifacts = json.loads(setup.stdout.splitlines()[-1])
    adapter = workspace / "fake_provider.py"
    adapter.write_text(_FAKE_PROVIDER, encoding="utf-8")
    provider_sha = _sha(adapter.read_bytes())
    approval = workspace / "approvals"
    journal = workspace / "journal"
    approval.mkdir()
    settings = {
        "schema": "stpd/m2-campaign-config-v1",
        "worker_python_root": str(worker),
        "producer": artifacts["producer"],
        "project_config": artifacts["project_config"],
        "store_root": artifacts["store_root"],
        "store_lock": str(workspace / "store.lock"),
        "journal_root": str(journal),
        "approval_dir": str(approval),
        "provider_adapter": {"path": str(adapter), "sha256": provider_sha},
        "runtime": artifacts["runtime"],
        "runs": [artifacts["binding"]],
        "preparation_evidence": artifacts["preparation_evidence"],
        "budget_scope_id": hashlib.sha256(
            str(workspace.resolve()).encode("utf-8")
        ).hexdigest()[:32],
        "max_windows": 2,
        "goal_epochs": 5,
        "limits": {
            "command_seconds": 900,
            "approval_wait_seconds": 300,
            "poll_seconds": 1,
            "max_rss_bytes": 4_000_000_000,
            "minimum_free_bytes": 100_000_000,
            "batch_raw_usd": "5.00",
            "workspace_cap_usd": "10.00",
            "external_exposure_usd": "0.00",
        },
    }
    config_path = workspace / "campaign.json"
    config_path.write_bytes(_canonical(settings))
    return worker, artifacts, config_path


def _subprocess_env() -> dict[str, str]:
    sensitive = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "AWS_", "MODAL", "OPENAI", "HF_")
    environment = {
        key: value for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in sensitive)
    }
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX"):
        environment.pop(key, None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment.setdefault("TMPDIR", tempfile.gettempdir())
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        environment[name] = "1"
    return environment


def test_public_m2_campaign_cli_frozen_source_owner_and_cpu_remote_restart(tmp_path: Path) -> None:
    _, artifacts, config_path = _campaign_files(tmp_path)
    approval_dir = Path(json.loads(config_path.read_bytes())["approval_dir"])
    checked, statuses = _run_cli(config_path, "check")
    assert checked.returncode == 0, f"{checked.stdout}\n{checked.stderr}"
    assert statuses and statuses[-1]["status"] == "checked"
    assert artifacts["training_windows_per_epoch"] >= 4

    first, statuses = _run_cli(config_path, "execute", pause=1, operator=True)
    assert first.returncode == 0, f"{first.stdout}\n{first.stderr}"
    assert statuses[-1]["status"] == "paused"
    accepted = next(row["accepted"] for row in reversed(statuses)
                    if row.get("accepted") is not None)
    assert accepted["completed_epochs"] == 0
    assert (Path(json.loads(config_path.read_bytes())["journal_root"]) / "state.json").is_file()
    first_state = json.loads((Path(json.loads(config_path.read_bytes())["journal_root"])
                              / "state.json").read_bytes())
    assert len(first_state["attempts"]) == 1
    first_attempt = first_state["attempts"][0]
    assert first_attempt["slice"]["max_windows"] == 2
    assert first_attempt["slice"]["completes_epoch"] is False
    assert list(approval_dir.glob("*.grant.json"))
    assert list(approval_dir.glob("*.fresh.json"))

    # A budget scope is immutable across process restarts, including its journal.
    original_config = config_path.read_bytes()
    settings = json.loads(original_config)
    calls_path = config_path.parent / "fake-transport-state" / "calls.json"
    calls_before = calls_path.read_bytes()
    settings["journal_root"] = str(config_path.parent / "alternate-journal")
    config_path.write_bytes(_canonical(settings))
    rebound, _ = _run_cli(config_path, "execute", operator=False)
    assert rebound.returncode != 0
    assert "budget_scope_already_bound_to_other_campaign" in rebound.stderr
    assert calls_path.read_bytes() == calls_before
    config_path.write_bytes(original_config)

    resumed, statuses = _run_cli(config_path, "execute", operator=True)
    assert resumed.returncode == 0, f"{resumed.stdout}\n{resumed.stderr}"
    assert statuses[-1]["status"] == "completed"
    summary = json.loads(Path(statuses[-1]["summary_path"]).read_bytes())
    assert len(summary["runs"]) == 1
    run = summary["runs"][0]
    assert run["run_id"] == artifacts["binding"]["run_id"]
    assert [stage["epoch"] for stage in run["stages"]] == [1, 3, 5]
    final_state = json.loads((Path(json.loads(config_path.read_bytes())["journal_root"])
                              / "state.json").read_bytes())
    run_attempts = [row for row in final_state["attempts"]
                    if row["run_id"] == artifacts["binding"]["run_id"]]
    assert run_attempts[0]["attempt_id"] == first_attempt["attempt_id"]
    assert run_attempts[1]["slice"]["resume_id"] == accepted["checkpoint_id"]
    assert len(run_attempts) == 10
    calls_path = config_path.parent / "fake-transport-state" / "calls.json"
    calls = json.loads(calls_path.read_bytes())
    assert len([row for row in calls if row["action"] == "submit"]) == 10
    assert len([row for row in calls if row["action"] == "stop"]) == 10


def test_public_m2_campaign_cli_adapter_recovery_same_owner_store_and_run(tmp_path: Path) -> None:
    """Actual numerical APIs; only transport and external operator receipts are fake."""
    from m2_recovery_fixture import packet

    from tools.m2_campaign.config import Settings

    _, artifacts, config_path = _campaign_files(tmp_path, offline=True)
    value = json.loads(config_path.read_bytes())
    value["runs"][0]["max_requests"] = 10  # Exactly the nominal 5-epoch slice count.
    adapter = Path(value["provider_adapter"]["path"])
    broken = _FAKE_PROVIDER.replace(
        'return json.dumps({"attempt_id": attempt.attempt_id,',
        'raise FileNotFoundError("synthetic_missing_absolute_cli")\n'
        '            return json.dumps({"attempt_id": attempt.attempt_id,',
        1,
    )
    adapter.write_text(broken)
    value["provider_adapter"]["sha256"] = _sha(adapter.read_bytes())
    config_path.write_bytes(_canonical(value))
    original_config = config_path.read_bytes()
    failed, statuses = _run_cli(
        config_path, "execute", operator=True, offline=True, raw_ceiling="0.50",
    )
    assert failed.returncode == 2, f"{failed.stdout}\n{failed.stderr}"
    assert any(row.get("reason") == "prepare_unknown:FileNotFoundError" for row in statuses)
    journal = Path(value["journal_root"])
    original_state = (journal / "state.json").read_bytes()
    old_row = json.loads(original_state)["attempts"][0]
    assert old_row["phase"] == "deploy_intent"
    assert all(old_row[key] is None for key in ("target", "handle", "result", "stop"))
    scope_path = (Path(value["store_root"]).parent / ".public-m2-budget-scopes"
                  / (value["budget_scope_id"] + ".json"))
    scope_raw = scope_path.read_bytes()
    old_history = {item.name: item.read_bytes() for item in (journal / "history").iterdir()}

    new_adapter = adapter.with_name("fixed_provider.py")
    new_adapter.write_text(_FAKE_PROVIDER)
    value["provider_adapter"] = {"path": str(new_adapter), "sha256": _sha(new_adapter.read_bytes())}
    after = Settings.decode(_canonical(value))
    new_config = config_path.with_name("repaired-config.json")
    new_config.write_bytes(after.raw)
    receipt_path, _ = packet(after, config_path.parent / "repair")
    args = _offline_argv(config_path.parent, Path(value["worker_python_root"]), "recover",
                         str(PYTHON_ROOT / "tools" / "public_m2_campaign.py"),
                         ["--config", str(new_config), "recover", "--receipt", str(receipt_path),
                          "--receipt-sha256", _sha(receipt_path.read_bytes())])
    for _ in range(2):  # Replay is metadata-only and does not mint another permit.
        repaired = subprocess.run(args, cwd=PYTHON_ROOT, env=_offline_env(config_path.parent),
                                  text=True, capture_output=True, timeout=30)
        assert repaired.returncode == 0, f"{repaired.stdout}\n{repaired.stderr}"
    assert len(list((journal / "recoveries").iterdir())) == 1
    assert (journal / "state.json").read_bytes() == original_state
    assert (journal / "settings.json").read_bytes() == original_config
    assert scope_path.read_bytes() == scope_raw
    for name, raw in old_history.items():
        assert (journal / "history" / name).read_bytes() == raw
    calls_path = config_path.parent / "fake-transport-state" / "calls.json"
    old_calls = calls_path.read_bytes()
    rejected, _ = _run_cli(config_path, "execute", offline=True)
    assert rejected.returncode == 1 and "execution_settings_not_current" in rejected.stderr
    assert calls_path.read_bytes() == old_calls

    first, statuses = _run_cli(new_config, "execute", pause=1, operator=True, offline=True)
    assert first.returncode == 0, f"{first.stdout}\n{first.stderr}"
    accepted = next(row["accepted"] for row in reversed(statuses) if row.get("accepted"))
    assert accepted["completed_epochs"] == 0
    intermediate = json.loads((journal / "state.json").read_bytes())["attempts"]
    assert intermediate[0] == old_row
    assert intermediate[1]["replacement_for"] == old_row["attempt_id"]
    assert intermediate[1]["attempt_id"] != old_row["attempt_id"]
    assert intermediate[1]["ordinal"] == 2
    assert intermediate[1]["slice"] == old_row["slice"]
    final, statuses = _run_cli(new_config, "execute", operator=True, offline=True)
    assert final.returncode == 0, f"{final.stdout}\n{final.stderr}"
    assert statuses[-1]["status"] == "completed"
    summary = json.loads(Path(statuses[-1]["summary_path"]).read_bytes())
    assert summary["origin_settings_sha256"] == _sha(original_config)
    assert summary["config_sha256"] == after.identity
    assert summary["reserved_raw_usd"] == "0.60"  # Failed .50 hold + 10 numerical requests at .01.
    assert [row["epoch"] for row in summary["runs"][0]["stages"]] == [1, 3, 5]
    rows = json.loads((journal / "state.json").read_bytes())["attempts"]
    assert len(rows) == 11 and [row["ordinal"] for row in rows] == list(range(1, 12))
    assert rows[0] == old_row and rows[2]["slice"]["resume_id"] == accepted["checkpoint_id"]
    assert all(row["run_id"] == artifacts["binding"]["run_id"] for row in rows)
    assert sum("replacement_for" in row for row in rows) == 1
    assert scope_path.read_bytes() == scope_raw
    calls = json.loads(calls_path.read_bytes())
    old_actions = [row["action"] for row in calls if row["attempt_id"] == old_row["attempt_id"]]
    assert old_actions == ["prepare"]
    submitted = [row["attempt_id"] for row in calls if row["action"] == "submit"]
    assert len(submitted) == len(set(submitted)) == 10


@pytest.mark.parametrize(
    ("tamper", "expected_error"),
    [
        ("intake_link", "prepared_run_intake_binding_mismatch"),
        ("prepared_schema", "prepared_run_inventory_required"),
        ("request_limit", "request_limit_cannot_complete_configured_run"),
    ],
)
def test_public_m2_campaign_cli_rejects_tampered_binding_before_provider(
    tmp_path: Path, tamper: str, expected_error: str,
) -> None:
    _, _, config_path = _campaign_files(tmp_path)
    settings = json.loads(config_path.read_bytes())
    if tamper == "request_limit":
        settings["runs"][0]["max_requests"] = 1
    else:
        prepared_reference = settings["preparation_evidence"][0]
        prepared_path = Path(prepared_reference["path"])
        prepared = json.loads(prepared_path.read_bytes())
        if tamper == "intake_link":
            prepared["intake_receipt_sha256"] = "0" * 64
        else:
            prepared["schema"] = "unsupported/prepared-evidence-v1"
        prepared_raw = _canonical(prepared)
        prepared_path.write_bytes(prepared_raw)
        prepared_sha = _sha(prepared_raw)
        settings["preparation_evidence"][0] = {
            "path": str(prepared_path), "sha256": prepared_sha,
        }
        settings["runs"][0]["preparation_sha256"] = prepared_sha
    config_path.write_bytes(_canonical(settings))
    completed, statuses = _run_cli(config_path, "execute", operator=False)
    assert completed.returncode != 0
    assert not any(row["status"] == "checked" for row in statuses)
    assert expected_error in completed.stderr
    # Check and failed admission must not trigger deployment or submission.
    adapter_state = config_path.parent / "fake-transport-state" / "calls.json"
    assert not adapter_state.exists()
