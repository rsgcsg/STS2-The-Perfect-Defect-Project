"""Managed execution backend for the shared suite and slot ledger.

Only lifecycle, registration and evidence transfer live here. The shipped P1
Runtime and research Agent own the complete observation/decision/Commit loop.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NoReturn

from sts2_platform_evidence import verify_agent_session_run_evidence

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.package_identity import validate_installed_package
from spireagent.source import source_identity
from spireagent.storage.cache import cache_usage
from spireagent.storage.config import open_peer, open_store
from spireagent.storage.local import LocalBlobStore
from spireagent.storage.store import copy_artifact
from stpd.evaluation.reference_metrics import read_reference_metrics
from stpd.p1 import export

from .agent_task_outcome import project
from .developer import ROOT
from .global_evidence_store import (
    publish_global_evidence,
    publish_global_runtime,
    verify_global_artifact,
)
from .local_environment import _audit, _candidate_audit, _verify_host
from .local_model_registration import global_environment_manifest
from .local_models import JSON_LIMIT, RuntimeClient
from .managed_model_target import runtime_arguments
from .p1_recording_storage import storage_budget, workspace_reserve_bytes


def storage_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    """Resolve once at ledger creation; explicit historical choices remain exact."""
    concurrency = configuration.get("read_concurrency", 8)
    if type(concurrency) is not int or not 1 <= concurrency <= 8:
        reject("invalid_read_concurrency")
    location = configuration.get("store", str(
        Path.home() / ".local/share/spireagent/managed-evaluation/store"))
    if location != "s3" and (not isinstance(location, str)
            or not Path(location).is_absolute() or Path(location).resolve() != Path(location)):
        reject("absolute_working_store_required")
    return {"store": location, "read_concurrency": concurrency}


def reject(code: str) -> NoReturn:
    raise BoundaryError("managed_evaluation", code)


def _load_host_api(path: Path) -> Any:
    """Execute the already-pinned source without reading/writing package pycache."""
    spec = importlib.util.spec_from_file_location("managed_evaluation_host_client", path)
    if spec is None or spec.loader is None:
        reject("host_client_unavailable")
    import sys

    api = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = api  # dataclasses resolves annotations through this owner.
    exec(compile(path.read_bytes(), str(path), "exec"), api.__dict__)
    return api


@contextmanager
def _phase(row: dict[str, Any], name: str, *, detail: bool = False) -> Iterator[None]:
    """Monotonic, disjoint phase cost; partial failures retain elapsed time."""
    began = time.monotonic()
    try:
        yield
    finally:
        phases = row.setdefault("detail_seconds" if detail else "phase_seconds", {})
        phases[name] = phases.get(name, 0.0) + time.monotonic() - began


def _finish_runtime(process: subprocess.Popen, stdout: Path, row: dict[str, Any],
                    artifacts: Any, deadline: float, cleanup_seconds: float) -> dict[str, Any]:
    """Observe the existing Runtime owner and send one exact-run Stop on handoff.

    Handoff/taint can leave the HTTP service alive; terminal Close can exit it.
    Process exit is cleanup evidence, never the game's terminal classifier.
    """
    startup_deadline = min(deadline, time.monotonic() + 120)
    startup = None
    while time.monotonic() < startup_deadline:
        if stdout.stat().st_size > JSON_LIMIT:
            reject("runtime_startup_too_large")
        raw = stdout.read_bytes()
        if b"\n" in raw:
            startup = json.loads(raw.split(b"\n", 1)[0])
            break
        if process.poll() is not None:
            reject("runtime_exited_before_startup")
        time.sleep(0.05)
    if not isinstance(startup, dict) or startup.get("schema") != (
        "sts2.policy-runtime/agent-session-startup-2"
    ):
        reject("runtime_startup_missing")
    if "runtime_process_started_monotonic" in row:
        row.setdefault("detail_seconds", {})["runtime_startup"] = (
            time.monotonic() - row.pop("runtime_process_started_monotonic"))
    client = RuntimeClient(startup["address"], startup)
    row["run_id"] = startup["run_id"]
    row["startup"] = artifacts.put("runtime-startup.json", json_bytes(startup))
    operation = "status"
    try:
        while process.poll() is None:
            state = client.request("/status")["status"]
            expired = time.monotonic() >= deadline
            if any("managed_settling_stalled" in reason for reason in (
                    state.get("taint_reason") or "", *state.get("errors", []))):
                row["settling_stalled"] = True
            if (state["lifecycle"] == "stopped" or state["mode"] == "human"
                    or state["tainted"] or state["autonomy_budget"]["state"] == "exhausted"
                    or expired):
                row["runtime_final_status"] = artifacts.put(
                    "runtime-final-status.json", json_bytes(state))
                if expired:
                    row["wall_limit_reached"] = True
                if state["lifecycle"] != "stopped":
                    operation = "stop"
                    receipt = client.request("/stop", {})
                    row["runtime_stop"] = artifacts.put("runtime-stop.json", json_bytes(receipt))
                break
            time.sleep(0.2)
    except (OSError, ValueError) as error:
        row["runtime_http_error"] = artifacts.put("runtime-http-error.json", json_bytes({
            "operation": operation,
            "error": error.code if isinstance(error, BoundaryError) else type(error).__name__,
            "request_retried": False,
        }))
        # The original process may close HTTP between poll/status, or after
        # accepting Stop but before its reply arrives. Read its own exit handle;
        # only the later returncode and typed original can establish the outcome.
        try:
            process.wait(timeout=min(2.0, cleanup_seconds))
        except subprocess.TimeoutExpired:
            raise error from None
    process.wait(timeout=cleanup_seconds)
    return startup


def _host_performance(manager: Any, target: dict[str, Any]) -> dict[str, Any]:
    # Optional observation must not fence an otherwise preservable original.
    try:
        result: dict[str, Any] = manager.request({"command": "performance",
            "expected_runtime_instance_id": target["runtime_instance_id"],
            "expected_game_continuity_id": target["game_continuity_id"]})["result"]
        return result
    except Exception as error:
        return {"type": "performance_unavailable", "status": "unavailable",
                "error": error.code if isinstance(error, BoundaryError) else type(error).__name__}


def _outcome_status(outcome: Mapping[str, Any], *, settling_stalled: bool) -> dict[str, str]:
    recorded_reason = outcome["operation_end"].get("recorded_reason") or ""
    if settling_stalled or "managed_settling_stalled" in recorded_reason:
        return {"status": "unknown", "reason": "managed_settling_stalled"}
    terminal, end = outcome["terminal_observation"], outcome["operation_end"]
    clean = (end.get("status") in {"task_ended", "censored"}
             and end.get("controller") == "released" and end.get("tainted") is False)
    return {
        "status": "finished" if clean and terminal.get("status") == "observed"
        and terminal.get("result") in {"win", "loss"}
        else "censored" if clean else "unknown",
        "reason": "natural_terminal" if clean and terminal.get("status") == "observed"
        else "runtime_censored" if clean else "runtime_execution_or_release_unknown",
    }


class ManagedEpisodeExecutor:
    """One independent Host/Runtime/Agent per call; safe for separate worker threads."""

    def __init__(
        self, configuration: dict[str, Any], *, episode_seconds: float, cleanup_seconds: float
    ) -> None:
        self.configuration = configuration
        preparation_started = time.monotonic()
        storage = storage_configuration(configuration)
        self.read_concurrency = storage["read_concurrency"]
        mode = configuration.get("inference", {"mode": "per_actor"})
        if not isinstance(mode, dict) or mode.get("mode") not in {"per_actor", "central_batch"}:
            reject("unsupported_inference_implementation")
        self.settling_timeout_ms = configuration.get("settling_timeout_ms", 120000)
        if self.settling_timeout_ms is not None and (
                type(self.settling_timeout_ms) is not int
                or not 1 <= self.settling_timeout_ms <= 2**53 - 1):
            reject("invalid_settling_timeout")
        self.measure_host_performance = configuration.get("measure_host_performance", False)
        if type(self.measure_host_performance) is not bool:
            reject("invalid_host_performance_setting")
        self.max_auto_submissions = configuration.get("max_auto_submissions", 10000)
        if (type(self.max_auto_submissions) is not int
                or not 1 <= self.max_auto_submissions <= 10000):
            reject("invalid_runtime_submission_budget")
        # The frozen producer is an expected pin, never a caller-supplied claim
        # about whatever code happens to execute this invocation.
        self.producer = source_identity(ROOT)
        if self.producer != Producer.decode(configuration["producer"]):
            reject("frozen_execution_source_changed")
        self.episode_seconds = episode_seconds
        self.cleanup_seconds = cleanup_seconds
        self.host = Path(configuration["host_directory"]).resolve()
        self.candidate = Path(configuration["candidate_directory"]).resolve()
        self.runtime = Path(configuration["runtime_directory"]).resolve()
        _verify_host(self.host, configuration["host_pin"])
        validate_installed_package(self.runtime.parent, configuration["runtime_pin"],
            required_paths=("dist/cli.js", "dist/global-replay.js", "dist/managed.js"))
        runtime_version = json.loads(
            (self.runtime.parent / "package.json").read_bytes()).get("version")
        if (mode["mode"] == "central_batch"
                and runtime_version not in {"0.1.0-rc.50", "0.1.0-rc.51", "0.1.0-rc.52"}):
            reject("runtime_explicit_adapter_environment_required")
        if self.settling_timeout_ms is not None and runtime_version != "0.1.0-rc.52":
            reject("runtime_settling_watchdog_required")
        if configuration["agent_adapter"] != export.adapter_identity():
            reject("frozen_agent_implementation_changed")
        if self.measure_host_performance and json.loads(
                (self.host / "package.json").read_bytes()).get("version") != "1.1.0-rc.46":
            reject("host_performance_diagnostics_required")
        self.build = _candidate_audit(_audit(self.host, self.candidate))
        self.api = _load_host_api(self.host / "consumers/python/sts2_headless/managed_service.py")
        self.peer = open_peer(configuration["peer"])
        # Remote execution writes to this already-frozen peer binding. Credentials
        # remain environment-only; bucket/endpoint/prefix cannot follow env drift.
        location = storage["store"]
        self.store = self.peer if location == "s3" else open_store(location)
        local_root = (self.store.blobs.root
                      if isinstance(self.store.blobs, LocalBlobStore) else None)
        self.preparation = {"store": location, "read_concurrency": self.read_concurrency,
                            "store_bytes_before": cache_usage(self.store) if local_root else None}
        model_started = time.monotonic()
        for model in configuration["models"].values():
            if local_root is not None:
                storage_budget(self.peer, model["artifact_id"], scratch=local_root,
                    destination=local_root, destination_store=self.store,
                    reserve_bytes=max(configuration.get("minimum_free_bytes", 0),
                                      workspace_reserve_bytes()))
            copy_artifact(self.peer, self.store, model["artifact_id"],
                          scratch=local_root, reuse_published_parents=True)
            original = self.store.get_manifest(model["artifact_id"])
            descriptor = json.loads((Path(model["package_directory"]) / "model.json").read_bytes())
            if (
                original.kind != "model"
                or original.payload("weights").sha256 != descriptor["package_sha256"]
                or b"".join(self.store.read_payload(original.payload("package_manifest")))
                != (Path(model["package_directory"]) / "model.json").read_bytes()
            ):
                reject("original_model_binding_changed")

        self.preparation.update(
            model_seconds=time.monotonic() - model_started,
            store_bytes_after=cache_usage(self.store) if local_root else None,
            free_bytes_after=shutil.disk_usage(local_root).free if local_root else None,
            elapsed_seconds=time.monotonic() - preparation_started,
        )
        self.inference_pool = None
        if mode["mode"] == "central_batch":
            from stpd.p1.local_inference import LocalInferencePool

            packages = {}
            for model in configuration["models"].values():
                package = Path(model["package_directory"])
                descriptor = json.loads((package / "model.json").read_bytes())
                packages[descriptor["package_sha256"]] = package / "model.tensor-tree"
            self.inference_pool = LocalInferencePool(packages,
                max_batch=mode["max_batch"], window_ms=mode["window_ms"])

    def close(self) -> dict[str, Any] | None:
        return self.inference_pool.close() if self.inference_pool else None

    def __call__(
        self, row: dict[str, Any], manifest: dict[str, Any], artifacts: Any, checkpoint: Any
    ) -> None:
        if manifest["ascension"] != 0 or manifest["host"] != "Managed":
            reject("unsupported_scenario")
        model = self.configuration["models"][row["selection_id"]]
        private = artifacts.root / "private"
        private.mkdir(mode=0o700)
        client_path, manager_path = private / "client.json", private / "manager.json"
        launch = manager = runtime_process = None
        started = time.monotonic()
        row.update(host_kind="managed", status="preparing", reason=None,
                   preservation_store=self.preparation["store"],
                   read_concurrency=self.read_concurrency,
                   settling_timeout_ms=self.settling_timeout_ms)
        checkpoint()
        try:
            with _phase(row, "prepare"):
                with _phase(row, "host_launch", detail=True):
                    launch = self.api.launch_managed_host_service(
                        host_package_dir=self.host,
                        candidate_dir=self.candidate,
                        client_attachment=client_path,
                        manager_attachment=manager_path,
                        log_file=private / "host.log",
                        diagnostics_file=private / "native-diagnostics.json",
                        character=manifest["character"],
                        language=self.configuration.get("language", "en"),
                        timeout_ms=60000,
                        startup_timeout=min(120, self.episode_seconds),
                    )
                client = self.api.ManagedHostServiceClient.from_attachment(client_path)
                manager = self.api.ManagedHostServiceManager.from_attachment(manager_path)
                ready = client.ready()
                row["host_service_instance_id"] = ready["service_instance_id"]
                row["host_service_pid"] = launch.pid
                row["world_mutation_offered"] = True
                checkpoint()  # Durable offer BEFORE reset, never reoffer on a lost reply.
                with _phase(row, "host_reset", detail=True):
                    reset = manager.reset(
                        row["seed"],
                        expected_runtime_instance_id=ready["adapter_runtime_instance_id"],
                        expected_game_continuity_id=ready["episode"].get("game_continuity_id"),
                        initial_progress=self.configuration["initial_progress"],
                    )
                row["reset"] = artifacts.put("managed-reset.json", json_bytes(reset))
                ready = client.ready()
                identity = manager.request({"command": "episode_identity"})["result"]["identity"]
                provenance = identity["episode_provenance"]
                if (
                    provenance["verdict"] != "provenance_pass"
                    or provenance["actual_seed"] != row["seed"]
                    or identity["initial_progress"]["run_unlock_state_matches"] is not True
                    or identity["initial_progress"]["progress_sha256"]
                    != self.configuration["initial_progress"]["progress_sha256"]
                ):
                    reject("prepared_seed_or_progress_changed")
                row["episode_identity"] = artifacts.put(
                    "episode-identity.json", json_bytes(identity))
                target = {
                    "input_profile": "public-basis-1",
                    "profile_sha256": hashlib.sha256(
                        json_bytes(
                            {
                                "host_pin": self.configuration["host_pin"],
                                "candidate_build": self.build,
                                "initial_progress": self.configuration["initial_progress"],
                            }
                        )
                    ).hexdigest(),
                    "host_package_pin": self.configuration["host_pin"],
                    "candidate_build": self.build,
                    "client_attachment": client_path,
                    "service_instance_id": ready["service_instance_id"],
                    "runtime_instance_id": ready["adapter_runtime_instance_id"],
                    "game_continuity_id": ready["episode"]["game_continuity_id"],
                }
                if self.measure_host_performance:
                    with _phase(row, "host_performance", detail=True):
                        row["host_performance_before"] = artifacts.put(
                            "host-performance-before.json",
                            json_bytes(_host_performance(manager, target)))
                host_args = runtime_arguments(target, private)
                options = {
                    host_args[i].removeprefix("--"): host_args[i + 1]
                    for i in range(0, len(host_args), 2)
                }
                package = Path(model["package_directory"])
                envelope = global_environment_manifest(
                    self.runtime,
                    options,
                    package / "model.tensor-tree",
                    export.adapter_identity(),
                    row["intent_id"],
                    started + self.episode_seconds,
                )
                registration = private / "registration"
                export.register(package, registration, envelope, entry_id=row["selection_id"])
                remaining = self.episode_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    reject("preparation_wall_limit")
                args = [
                    "node",
                    str(self.runtime / "cli.js"),
                    "--manifest",
                    str(registration / "manifest.json"),
                    "--adapter-command",
                    self.configuration["python_executable"],
                    "--adapter-arg=-m",
                    "--adapter-arg=stpd.p1.agent",
                    "--adapter-arg=--config",
                    "--adapter-arg=" + str(registration / "config.json"),
                    "--adapter-arg=--manifest",
                    "--adapter-arg=" + str(registration / "manifest.json"),
                    "--adapter-arg=--binding-root",
                    "--adapter-arg=" + str(registration),
                    "--listen-port",
                    "0",
                    "--mode",
                    "auto",
                    "--evidence-root",
                    str(artifacts.root / "runs"),
                    "--max-auto-submissions",
                    str(self.max_auto_submissions),
                    "--max-policy-calls",
                    "20000",
                    "--auto-deadline-ms",
                    str(int(remaining * 1000)),
                    *host_args,
                ]
                if self.settling_timeout_ms is not None:
                    args.extend(["--managed-settling-timeout-ms", str(self.settling_timeout_ms)])
                environment = os.environ.copy()
                row["inference_mode"] = "central_batch" if self.inference_pool else "per_actor"
                if self.inference_pool is not None:
                    attachment = registration / "local-inference.json"
                    binding = json_bytes(self.inference_pool.attachment())
                    attachment.write_bytes(binding)
                    row["inference_binding"] = artifacts.put(
                        "local-inference-binding.json", binding)
                    args.extend(["--adapter-arg=--local-inference",
                        "--adapter-arg=" + str(attachment)])
                    credentials = self.inference_pool.environment()
                    environment.update(credentials)
                    for name in credentials:
                        args.extend(["--adapter-env-key", name])
            row.update(status="running", actor_started=True)
            checkpoint()
            with (
                _phase(row, "execute"), (private / "runtime.stdout").open("xb") as stdout,
                (private / "runtime.stderr").open("xb") as stderr,
            ):
                runtime_started = time.monotonic()
                runtime_process = subprocess.Popen(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    env=environment,
                )
                row["runtime_process_started_monotonic"] = runtime_started
                startup = _finish_runtime(
                    runtime_process, private / "runtime.stdout", row, artifacts,
                    started + self.episode_seconds, self.cleanup_seconds)
                row["runtime_window_seconds"] = time.monotonic() - runtime_started
            if self.measure_host_performance:
                with _phase(row, "host_performance", detail=True):
                    row["host_performance"] = artifacts.put(
                        "host-performance.json", json_bytes(_host_performance(manager, target)))
            with _phase(row, "finish"):
                if runtime_process.returncode != 0:
                    reject("runtime_process_failed")
                run = artifacts.root / "runs" / startup["run_id"]
                installed = json.loads((registration / "config.json").read_bytes())
                expected = {
                    key: startup[key]
                    for key in (
                        "run_id",
                        "runtime_version",
                        "runtime_code_sha256",
                        "agent_manifest_sha256",
                        "agent_artifact_sha256",
                    )
                }
                expected.update(
                    global_runtime_directory=str(self.runtime),
                    model_binding={
                        key: installed[key]
                        for key in ("package_input_spec", "input_spec", "input_projection")
                    },
                )
                result = verify_agent_session_run_evidence(run, expected)
                if not result.passed or result.value is None:
                    reject("typed_original_verification_failed")
                outcome = project(
                    result, native=True, native_package=(package / "model.json").read_bytes()
                )
                row["agent_task_outcome"] = outcome
                row["typed_verification"] = artifacts.put(
                    "typed-verification.json",
                    json_bytes(
                        {
                            "status": result.status,
                            "content_id": result.value.content_id,
                            "expected": expected,
                        }
                    ),
                )
                metrics = read_reference_metrics(
                    run,
                    {
                        "evidence_verification": "pass",
                        "agent_task_outcome": outcome,
                        "run_id": startup["run_id"],
                        "evidence_content_id": result.value.content_id,
                    },
                    {"binding": {"runtime_instance_id": target["runtime_instance_id"]}},
                )
                row["game_metrics"] = metrics
            self._preserve(row, artifacts, checkpoint, run, expected, model, private)
            row.update(_outcome_status(
                outcome, settling_stalled=row.get("settling_stalled") is True))
        except Exception as error:
            row.update(
                status="unknown" if row["world_mutation_offered"] else "not_started",
                reason=(error.code if isinstance(error, BoundaryError)
                        else type(error).__name__),
            )
        finally:
            close_started = time.monotonic()
            close = None
            try:
                if runtime_process is not None and runtime_process.poll() is None:
                    runtime_process.terminate()
                    runtime_process.wait(timeout=self.cleanup_seconds)
                if manager is not None:
                    close = manager.close_host()["result"]
                exit_code = (
                    launch.process.wait(timeout=self.cleanup_seconds)
                    if launch is not None
                    else None
                )
                accepted = (
                    close is not None and close.get("exit", {}).get("code") == 0 and exit_code == 0
                )
                row["finish"] = artifacts.put(
                    "managed-finish.json",
                    json_bytes(
                        {
                            "phase": "closed" if accepted else "unknown",
                            "host_kind": "managed",
                            "service_instance_id": row.get("host_service_instance_id"),
                            "close": close,
                            "service_exit_code": exit_code,
                        }
                    ),
                )
                row["cleanup_accepted"] = accepted
                if not accepted and row["world_mutation_offered"]:
                    row.update(status="unknown", reason="host_cleanup_unconfirmed")
            except Exception as error:
                row.update(
                    status="unknown",
                    reason="host_cleanup_unconfirmed",
                    cleanup_error=type(error).__name__,
                    cleanup_accepted=False,
                )
            row.setdefault("phase_seconds", {})["close"] = time.monotonic() - close_started
            row["elapsed_seconds"] = time.monotonic() - started
            row["phase_timing_schema"] = "spireagent/managed-phase-seconds-v1"
            row["detail_timing_schema"] = "spireagent/managed-detail-seconds-v1"
            checkpoint()

    def _preserve(self, row: dict[str, Any], artifacts: Any, checkpoint: Any,
                  run: Path, expected: dict[str, Any], model: dict[str, Any],
                  private: Path) -> None:
        """Seal -> local publish -> exact peer copy/readback; no queued work."""
        with _phase(row, "preserve"):
            with _phase(row, "publish_runtime", detail=True):
                runtime = publish_global_runtime(self.store, self.producer, expected)
            with _phase(row, "publish_evidence", detail=True):
                stored = publish_global_evidence(
                    self.store,
                    run,
                    self.producer,
                    expected=expected,
                    model_artifact_id=model["artifact_id"],
                    runtime_artifact_id=runtime.artifact_id,
                    scratch=private,
                )
            row["preservation_artifact_id"] = stored.artifact_id
            row["preservation_pending"] = artifacts.put(
                "preservation-pending.json", json_bytes({
                    "artifact_id": stored.artifact_id, "store": self.preparation["store"],
                    "peer": self.configuration["peer"], "expected": expected,
                    "status": "pending_peer_readback",
                }))
            checkpoint()
            with _phase(row, "copy_peer", detail=True):
                copy_artifact(self.store, self.peer, stored.artifact_id,
                    reuse_published_parents=True)
            with _phase(row, "peer_readback", detail=True):
                readback = verify_global_artifact(
                    self.peer, stored.artifact_id, expected=expected, scratch=private,
                    read_concurrency=self.read_concurrency,
                )
            row["stored_evidence_id"] = stored.artifact_id
            row["preservation"] = artifacts.put(
                "preservation.json",
                json_bytes(
                    {
                        "status": "completed",
                        "run_id": expected["run_id"],
                        "artifact_id": stored.artifact_id,
                        "peer": self.configuration["peer"],
                        "peer_readback": readback,
                    }
                ),
            )
