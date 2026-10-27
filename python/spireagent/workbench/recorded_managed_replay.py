"""Optional Q post-intake diagnostics over sealed originals, never a new intake chain.

The existing maintenance owner holds the attempt state. The configured immutable
execution checkout supplies replay code; recorded bytes cannot select executables.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sts2_platform_evidence import DirectoryTransferManifest

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from spireagent.source import source_identity
from spireagent.storage.store import ManifestArtifactStore, copy_artifact

from .developer import ROOT, ProjectConfig, atomic_json
from .p1_recording_storage import open_peer, require_long_task_space
from .p1_recording_store import _verify_archive, materialize_p1_original

if TYPE_CHECKING:
    from .p1_recording_maintenance import P1RecordingMaintenance

CONFIG = "managed-recording-replay.json"
PAGE = "managed-replay-summary.html"
SCHEMA = "spireagent/recorded-managed-replay-config-1"
DIAGNOSTIC = "spireagent/recorded-managed-replay-archive-1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _execution_identity(root: Path) -> dict[str, Any]:
    files = [
        "tools/p1-managed-recording-replay.mjs",
        "tools/p1-render-coverage.mjs",
        "components/host-runtime/package.json",
    ]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=root):
        raise BoundaryError("recorded_replay", "immutable_clean_execution_required")
    build_files = []
    for directory in (
        root / "components/policy-runtime/dist",
        root / "components/connector/sdk/typescript/dist",
    ):
        if not directory.is_dir():
            raise BoundaryError("recorded_replay", "built_execution_required")
        for item in sorted(directory.rglob("*")):
            if item.is_symlink():
                raise BoundaryError("recorded_replay", "immutable_execution_build_required")
            if item.is_file():
                build_files.append(item)
    actual_sdk = (root / "node_modules/@rsgcsg/sts2-connector-client").resolve()
    if actual_sdk != (root / "components/connector/sdk/typescript").resolve():
        raise BoundaryError("recorded_replay", "executing_sdk_binding_mismatch")
    return {
        "revision": head,
        "files": {name: _sha(root / name) for name in files},
        "build_files": {str(item.relative_to(root)): _sha(item) for item in build_files},
    }


def configure(
    config: ProjectConfig,
    *,
    source_root: Path,
    candidate: Path,
    language: str,
    closed_after: str,
    enabled: bool = True,
    budget_ms: int = 180000,
) -> dict[str, Any]:
    if (
        os.name != "posix"
        or language not in {"en", "zh"}
        or type(enabled) is not bool
        or type(budget_ms) is not int
        or not 1 <= budget_ms <= 1200000
        or datetime.fromisoformat(closed_after).tzinfo is None
    ):
        raise BoundaryError("recorded_replay", "invalid_configuration")
    source_root, candidate = source_root.resolve(strict=True), candidate.resolve(strict=True)
    value = {
        "schema": SCHEMA,
        "enabled": enabled,
        "source_root": str(source_root),
        "candidate": str(candidate),
        "language": language,
        "closed_after": closed_after,
        "budget_ms": budget_ms,
        "execution": _execution_identity(source_root),
    }
    atomic_json(config.state_dir / CONFIG, value)
    return value


def _load(config: ProjectConfig) -> dict[str, Any] | None:
    path = config.state_dir / CONFIG
    if not path.exists():
        return None
    value = json.loads(path.read_bytes())
    if (
        os.name != "posix"
        or value.get("schema") != SCHEMA
        or type(value.get("enabled")) is not bool
        or value.get("language") not in {"en", "zh"}
        or type(value.get("budget_ms")) is not int
        or not 1 <= value["budget_ms"] <= 1200000
        or datetime.fromisoformat(value["closed_after"]).tzinfo is None
    ):
        raise BoundaryError("recorded_replay", "invalid_configuration")
    return value if value["enabled"] else None


def _game_idle(source_root: Path) -> bool:
    # Reuse the Host's exact executable-name observer, without scanning recordings.
    code = (
        "import {listGameProcesses} from "
        + json.dumps((source_root / "components/host-runtime/src/game-processes.mjs").as_uri())
        + "; const idle=listGameProcesses(process.platform,{failClosed:true}).length===0;"
        + "console.log(JSON.stringify(idle));"
    )
    result = subprocess.run(
        [shutil.which("node") or "node", "--input-type=module", "-e", code],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    return json.loads(result.stdout) is True


def _group_alive(pgid: int) -> bool:
    observed = subprocess.run(
        ["ps", "-axo", "pgid=,stat="], capture_output=True, text=True, timeout=5, check=True
    )
    for row in observed.stdout.splitlines():
        values = row.split()
        if len(values) >= 2 and values[0] == str(pgid) and not values[1].startswith("Z"):
            return True
    return False


def _cleanup_group(process: subprocess.Popen[bytes]) -> None:
    # A crashed group leader is not proof that its native child has exited.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while _group_alive(process.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _group_alive(process.pid):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=5)
    if _group_alive(process.pid):
        raise BoundaryError("recorded_replay", "owned_group_cleanup_unconfirmed")


def _run(
    command: list[str],
    output: Path,
    source_root: Path,
    maintenance: P1RecordingMaintenance,
    budget_ms: int,
    on_started: Callable[[int], None] = lambda _pid: None,
) -> str:
    with (
        (output / "worker.stdout").open("wb") as stdout,
        (output / "worker.stderr").open("wb") as stderr,
    ):
        process = subprocess.Popen(
            command, cwd=source_root, stdout=stdout, stderr=stderr, start_new_session=True
        )
        deadline = time.monotonic() + budget_ms / 1000 + 150
        reason = "finished"
        try:
            on_started(process.pid)
            while process.poll() is None:
                if maintenance.stop.wait(0.5):
                    reason = "owner_stopped_unknown"
                    break
                try:
                    idle = _game_idle(source_root)
                except Exception:
                    reason = "game_observer_failed_unknown"
                    break
                if not idle:
                    reason = "recording_priority_interrupted_unknown"
                    break
                if time.monotonic() >= deadline:
                    reason = "worker_deadline_unknown"
                    break
        finally:
            _cleanup_group(process)
        if reason != "finished":
            return reason
        return "finished" if process.returncode == 0 else "worker_failed"


def publish_diagnostic(
    store: ManifestArtifactStore,
    directory: Path,
    source_id: str,
    producer: Producer,
    scratch: Path,
    *,
    before_publish: Callable[[str], None] | None = None,
) -> Manifest:
    from .local_recording_import import _archive_verified_bundle

    inventory = DirectoryTransferManifest.from_directory(
        directory,
        content_id=hashlib.sha256((directory / "job.json").read_bytes()).hexdigest(),
        artifact_type="managed-recording-replay-diagnostic",
    )
    with tempfile.TemporaryDirectory(prefix="managed-diagnostic-", dir=scratch) as name:
        archive = Path(name) / "diagnostic.tar.gz"
        _archive_verified_bundle(
            directory,
            archive,
            inventory,
            max_bytes=768 * 1024**2,
            max_files=50000,
            max_archive_bytes=768 * 1024**2,
        )
        _verify_archive(archive, inventory)
        with archive.open("rb") as handle:
            payload = store.put_payload("archive", handle, "application/gzip")
    transfer = store.put_bytes("transfer", json_bytes(inventory.to_dict()))
    manifest = Manifest(
        "analysis",
        producer,
        (Parent("source", source_id),),
        (payload, transfer),
        FrozenObject.of(
            {
                "schema": DIAGNOSTIC,
                "source_artifact_id": source_id,
                "diagnostic_only": True,
                "typed_game_evidence_qualified": False,
                "human_origin_verified": False,
                "research_admission": "not_evaluated",
            }
        ),
    )
    if before_publish is not None:
        before_publish(manifest.artifact_id)
    store.publish(manifest)
    return manifest


def verify_diagnostic(store: ManifestArtifactStore, identity: str, scratch: Path) -> dict[str, Any]:
    manifest = store.get_manifest(identity)
    if manifest.kind != "analysis" or manifest.parameters.value().get("schema") != DIAGNOSTIC:
        raise BoundaryError("recorded_replay", "diagnostic_manifest_required")
    with tempfile.TemporaryDirectory(prefix="managed-diagnostic-readback-", dir=scratch) as name:
        root = Path(name)
        transfer = DirectoryTransferManifest.read(
            store.materialize(manifest.payload("transfer"), root / "transfer.json")
        )
        archive = store.materialize(manifest.payload("archive"), root / "archive.tar.gz")
        _verify_archive(archive, transfer)
    return {
        "artifact_id": identity,
        "full_readback": True,
        "diagnostic_only": True,
        "verified_at": datetime.now(UTC).isoformat(),
    }


def _render(config: ProjectConfig, execution: dict[str, Any], jobs: dict[str, Any]) -> None:
    root = Path(execution["source_root"])
    snapshot = config.state_dir / "managed-replays-view.json"
    atomic_json(snapshot, {"schema": "spireagent/managed-replay-view-1", "jobs": jobs})
    subprocess.run(
        [
            shutil.which("node") or "node",
            str(root / "tools/p1-render-coverage.mjs"),
            "--directory",
            str(config.state_dir / "managed-replays"),
            "--ledger",
            str(snapshot),
            "--out",
            str(config.state_dir / PAGE),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def _preserve(
    store: ManifestArtifactStore,
    identity: str,
    peer: dict[str, Any],
    config: ProjectConfig,
    job: dict[str, Any],
    importer: Any,
) -> None:
    remote = open_peer(peer)
    with importer.lock:
        job["storage_status"] = "preservation_unconfirmed"
        importer._save()
    copy_artifact(store, remote, identity, scratch=config.state_dir, reuse_published_parents=True)
    receipt = verify_diagnostic(remote, identity, config.state_dir)
    with importer.lock:
        job.update(storage_receipt=receipt, storage_status="preserved")
        importer._save()


def _promote(
    store: ManifestArtifactStore,
    output: Path,
    source: str,
    job: dict[str, Any],
    importer: Any,
    root: Path,
) -> None:
    config = importer.config
    if not _game_idle(root):
        with importer.lock:
            job["storage_status"] = "pending_diagnostic_publication_game_exit"
            importer._save()
        return
    identity = job.get("diagnostic_artifact_id")
    if identity is None:

        def intent(identity: str) -> None:
            with importer.lock:
                job.update(
                    diagnostic_artifact_id=identity, storage_status="publication_unconfirmed"
                )
                importer._save()

        artifact = publish_diagnostic(
            store, output, source, source_identity(ROOT), config.state_dir, before_publish=intent
        )
        identity = artifact.artifact_id
    else:
        store.get_manifest(identity)  # Reconcile existing publication; never guess/re-publish it.
    peer = importer._maintenance["policy"].get("peer")
    if peer is not None and _game_idle(root):
        _preserve(store, identity, peer, config, job, importer)
    else:
        with importer.lock:
            job["storage_status"] = "pending_game_exit_or_peer"
            importer._save()


def _tick(maintenance: P1RecordingMaintenance) -> None:
    importer = maintenance.importer
    config = importer.config
    try:
        execution = _load(config)
        if execution is None:
            return
        root = Path(execution["source_root"])
        state = importer._maintenance
        if (
            not state["policy"]["enabled"]
            or state.get("status") not in {"completed", "up_to_date"}
            or state.get("active") is not None
            or importer.thread is not None
            and importer.thread.is_alive()
        ):
            return
        jobs = state.setdefault("managed_replays", {})
        eligible = [
            source
            for source, session in state.get("sessions", {}).items()
            if session["scope_facts"].get("closed_at")
            and datetime.fromisoformat(session["scope_facts"]["closed_at"])
            >= datetime.fromisoformat(execution["closed_after"])
            and session["scope_facts"].get("declared_origin") == "declared_human"
        ]
        pending = [
            source
            for source in eligible
            if source not in jobs
            or jobs[source].get("status") in {"running", "not_started", "interrupted_unknown"}
            or jobs[source].get("storage_status")
            in {"pending_game_exit_or_peer", "pending_diagnostic_publication_game_exit"}
        ]
        if not pending:
            return
        if execution["execution"] != _execution_identity(root):
            raise BoundaryError("recorded_replay", "execution_identity_changed")
        if not _game_idle(root):
            return
        if shutil.disk_usage(config.state_dir).free < 15 * 1024**3:
            raise BoundaryError("recorded_replay", "fifteen_GiB_free_required")
        require_long_task_space(config.state_dir, 1024**3)
        from .local_recording_import import _selected_store

        for job in jobs.values():
            if job.get("status") in {"running", "interrupted_unknown"}:
                pid = job.get("worker_pid")
                if not isinstance(pid, int) or _group_alive(pid):
                    with importer.lock:
                        job.update(
                            status="interrupted_unknown",
                            unknown_delivery=None,
                            attempt_unconfirmed=True,
                            storage_status="local_files_retained_reconciliation_required",
                        )
                        importer._save()
                    _render(config, execution, jobs)
                    return
        store, _registry = _selected_store(config, require_curation=False)
        jobs = state.setdefault("managed_replays", {})
        after = datetime.fromisoformat(execution["closed_after"])
        for source, session in state.get("sessions", {}).items():
            closed = session["scope_facts"].get("closed_at")
            if (
                not closed
                or datetime.fromisoformat(closed) < after
                or session["scope_facts"].get("declared_origin") != "declared_human"
            ):
                continue
            if source in jobs:
                previous = jobs[source]
                if previous.get("status") in {"running", "interrupted_unknown"}:
                    with importer.lock:
                        previous.update(
                            status="interrupted_unknown",
                            unknown_delivery=None,
                            attempt_unconfirmed=True,
                            error="previous_attempt_unconfirmed_no_retry",
                        )
                        importer._save()
                    pid = previous.get("worker_pid")
                    if not isinstance(pid, int) or _group_alive(pid):
                        previous["storage_status"] = "local_files_retained_reconciliation_required"
                        continue
                    output = config.state_dir / "managed-replays" / source
                    previous.update(status="failed", ended_at=datetime.now(UTC).isoformat())
                    atomic_json(output / "job.json", previous)
                    _promote(store, output, source, previous, importer, root)
                    break
                if previous.get("storage_status") == "pending_diagnostic_publication_game_exit":
                    output = config.state_dir / "managed-replays" / source
                    _promote(store, output, source, previous, importer, root)
                    break
                if previous.get("storage_status") == "pending_game_exit_or_peer":
                    identity = previous.get("diagnostic_artifact_id")
                    peer = state["policy"].get("peer")
                    if identity is not None and peer is not None:
                        _preserve(store, identity, peer, config, previous, importer)
                        _render(config, execution, jobs)
                        break
                    continue
                if previous.get("status") != "not_started":
                    continue
            original = store.get_manifest(source).parameters.value()
            # Old/empty recordings remain missing; never synthesize a launch fact.
            if not any(
                name.startswith("initial-progress/") and name.endswith(".json")
                for name in original["raw_file_sha256"]
            ):
                with importer.lock:
                    jobs[source] = {"status": "skipped", "error": "original_launch_witness_missing"}
                    importer._save()
                continue
            output = config.state_dir / "managed-replays" / source
            output.mkdir(parents=True, exist_ok=source in jobs)
            job = {
                "status": "running",
                "source_artifact_id": source,
                "session_id": session["session_id"],
                "started_at": datetime.now(UTC).isoformat(),
                "formal_evaluation": False,
                "execution": execution["execution"],
            }
            with importer.lock:
                jobs[source] = job
                importer._save()  # Attempt is durable before any Host/world offer.
            try:
                with materialize_p1_original(store, source, scratch=config.state_dir) as bundle:
                    if not _game_idle(root):
                        job.update(status="not_started", error="recording_started_before_dispatch")
                    else:
                        command = [
                            shutil.which("node") or "node",
                            str(root / "tools/p1-managed-recording-replay.mjs"),
                            "--bundle",
                            str(bundle),
                            "--candidate",
                            execution["candidate"],
                            "--out",
                            str(output / "replay"),
                            "--language",
                            execution["language"],
                            "--budget-ms",
                            str(execution["budget_ms"]),
                        ]

                        def started(pid: int, current_job: dict[str, Any] = job) -> None:
                            with importer.lock:
                                current_job["worker_pid"] = pid
                                importer._save()

                        disposition = _run(
                            command,
                            output,
                            root,
                            maintenance,
                            execution["budget_ms"],
                            on_started=started,
                        )
                        report_path = output / "replay/report.json"
                        report = (
                            json.loads(report_path.read_bytes()) if report_path.exists() else {}
                        )
                        job.update(
                            status="completed"
                            if disposition == "finished" and report.get("completed")
                            else "failed",
                            disposition=disposition,
                            completed=report.get("completed", False),
                            unknown_delivery=report.get("unknown_delivery"),
                            attempt_unconfirmed=not bool(report),
                        )
            except Exception as error:
                job.update(
                    status="failed",
                    error=getattr(error, "code", type(error).__name__),
                    unknown_delivery=None if "worker_pid" in job else False,
                    attempt_unconfirmed="worker_pid" in job,
                )
            finally:
                job["ended_at"] = datetime.now(UTC).isoformat()
                atomic_json(output / "job.json", job)
                with importer.lock:
                    importer._save()
            if job["status"] == "failed":
                _promote(store, output, source, job, importer, root)
            _render(config, execution, jobs)
            break  # At most one serial replay per idle maintenance tick.
        _render(config, execution, jobs)
    except Exception as error:
        # Optional diagnostics cannot disable Q or change source/cohort admission.
        with importer.lock:
            importer._maintenance["managed_replay_error"] = getattr(
                error, "code", type(error).__name__
            )
            importer._save()


def tick(maintenance: P1RecordingMaintenance) -> None:
    importer = maintenance.importer
    if not (importer.config.state_dir / CONFIG).exists():
        return

    def busy() -> None:
        raise BoundaryError("recorded_replay", "diagnostic_in_progress")

    with importer.lock:
        if importer.thread is not None and importer.thread.is_alive():
            return
        original_guard = importer.operation_admission_guard
        if original_guard is not None:
            try:
                original_guard()
            except BoundaryError:
                return
        importer.operation_admission_guard = busy
    try:
        _tick(maintenance)
    finally:
        with importer.lock:
            importer.operation_admission_guard = original_guard


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Configure optional post-intake Managed diagnostics; no game/installation"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--language", choices=("en", "zh"), required=True)
    parser.add_argument("--closed-after", required=True)
    parser.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    print(
        json.dumps(
            configure(
                ProjectConfig.load(args.config),
                source_root=args.source_root,
                candidate=args.candidate,
                language=args.language,
                closed_after=args.closed_after,
                enabled=args.enabled,
            )
        )
    )


if __name__ == "__main__":
    main()
