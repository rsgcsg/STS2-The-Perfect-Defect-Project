"""Command-scoped owner verification, approval wait, transport and acceptance."""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import os
import shutil
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .config import Settings, bounded_file, canonical, path, sha


def _publish(location: Path, raw: bytes, *, immutable: bool = True) -> None:
    # Metadata only; the campaign journal separately owns all large byte objects.
    if location.is_symlink():
        raise ValueError("metadata_symlink")
    location.parent.mkdir(parents=True, exist_ok=True)
    if immutable:
        try:
            with location.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if bounded_file(location, 1024 * 1024) != raw:
                raise ValueError("immutable_metadata_changed") from None
    else:
        temporary = location.with_suffix(location.suffix + ".pending")
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, location)


def _bootstrap(settings: Settings) -> None:
    settings.preparations()
    root = path(settings.value["worker_python_root"])
    if not all((root / package / "__init__.py").is_file()
               for package in ("stpd", "spireagent")):
        raise ValueError("worker_packages_missing")
    sys.path.insert(0, str(root))
    for name, module in tuple(sys.modules.items()):
        if name.split(".", 1)[0] in {"spireagent", "stpd"}:
            origin = getattr(module, "__file__", None)
            if origin is None or not Path(origin).resolve().is_relative_to(root):
                raise ValueError("foreign_worker_module_loaded")
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[key] = "1"
    import torch
    torch.set_num_threads(1)
    from spireagent.artifact_contracts import Producer
    from spireagent.source import source_identity
    if source_identity(root) != Producer.decode(settings.value["producer"]):
        raise ValueError("worker_source_pin_mismatch")


def _provider(settings: Settings) -> Any:
    item = settings.value["provider_adapter"]
    location = path(item["path"])
    raw = bounded_file(location, 1024 * 1024)
    if sha(raw) != item["sha256"]:
        raise ValueError("provider_adapter_pin_mismatch")
    spec = importlib.util.spec_from_file_location("m2_campaign_provider", location)
    if spec is None or spec.loader is None:
        raise ValueError("provider_adapter_unloadable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    # Execute the exact bytes just verified, avoiding a stale .pyc or read race.
    exec(compile(raw, str(location), "exec"), module.__dict__)
    result = module.create_provider(settings)
    if any(not callable(getattr(result, name, None))
           for name in ("prepare", "submit", "poll", "stop_and_confirm")):
        raise ValueError("provider_contract_missing")
    return result


def _peak_rss_bytes() -> int:
    if sys.platform != "win32":
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak if sys.platform == "darwin" else peak * 1024)
    import ctypes
    from ctypes import wintypes

    class MemoryCounters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE,
                                          ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    if not psapi.GetProcessMemoryInfo(
        kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb,
    ):
        raise OSError("process_memory_observation_failed")
    return int(counters.PeakWorkingSetSize)


def _resources(settings: Settings, started: float) -> None:
    limits = settings.value["limits"]
    if time.monotonic() - started > limits["command_seconds"]:
        raise TimeoutError("campaign_command_deadline")
    if _peak_rss_bytes() > limits["max_rss_bytes"]:
        raise MemoryError("campaign_peak_rss_limit")
    if shutil.disk_usage(settings.value["store_root"]).free < limits["minimum_free_bytes"]:
        raise OSError("campaign_disk_reserve")


def _emit(settings: Settings, status: str, **values: Any) -> None:
    print(canonical({"schema": "stpd/m2-campaign-status-v1", "status": status,
                     "config_sha256": settings.identity, **values}).decode().strip(), flush=True)


def execute(settings: Settings, pause: int | None) -> int:
    from .authority import FileAuthority
    from .backend import PublicM2Backend
    from .core import Campaign, CampaignConfig, RunSpec
    from .journal import CampaignJournal

    started = time.monotonic()
    root = path(settings.value["journal_root"])
    path(settings.value["approval_dir"]).mkdir(parents=True, exist_ok=True)
    _publish(root / "settings.json", settings.raw)
    journal = CampaignJournal(root)
    backend = PublicM2Backend(settings)
    provider = _provider(settings)
    authority = FileAuthority(settings, journal)
    campaign = Campaign(CampaignConfig(
        tuple(RunSpec(row.run_id, row.max_requests) for row in settings.runs),
        settings.value["max_windows"], settings.value["goal_epochs"],
    ), journal, backend, provider, authority)
    waiting_since: float | None = None
    previous_status: tuple[Any, ...] | None = None
    with campaign.session():
        try:
            while True:
                _resources(settings, started)
                outcome = campaign.tick(pause)
                _resources(settings, started)
                attempt = None if outcome.attempt is None else asdict(outcome.attempt)
                accepted = None if outcome.accepted is None else asdict(outcome.accepted)
                status_key = (outcome.status, None if attempt is None else attempt["attempt_id"],
                              outcome.reason)
                if outcome.status == "waiting_approval":
                    assert outcome.attempt is not None
                    record = next(row for row in journal.records()
                                  if row["attempt_id"] == outcome.attempt.attempt_id)
                    notice = authority.request_notice(outcome.attempt, record["request"]["sha256"])
                    _publish(Path(settings.value["approval_dir"])
                             / f"{outcome.attempt.attempt_id}.request.json", notice)
                    waiting_since = waiting_since or time.monotonic()
                    if (time.monotonic() - waiting_since
                            > settings.value["limits"]["approval_wait_seconds"]):
                        raise TimeoutError("operator_approval_wait_expired")
                else:
                    waiting_since = None
                if status_key != previous_status:
                    _emit(settings, outcome.status, attempt=attempt, accepted=accepted,
                          reason=outcome.reason)
                    previous_status = status_key
                if outcome.status in {"completed", "paused"}:
                    summary = {
                        "schema": "stpd/m2-campaign-summary-v1", "status": outcome.status,
                        "config_sha256": settings.identity,
                        "observed_at_utc": dt.datetime.now(dt.UTC).isoformat(),
                        "producer": settings.value["producer"],
                        "qualification": "engineering_only",
                        "runs": [{"run_id": row.run_id,
                                  "stages": backend.stage_summaries(row.run_id)}
                                 for row in settings.runs],
                        "reserved_raw_usd": str(sum(authority._all_reserved().values())),
                        "billing_status": "reservations_are_not_final_charges",
                    }
                    target = root / f"summary-{time.time_ns()}.json"
                    _publish(target, canonical(summary))
                    _emit(settings, outcome.status, summary_path=str(target))
                    return 0
                if outcome.status == "blocked":
                    return 2
                if outcome.status in {"running", "waiting_approval"}:
                    time.sleep(min(settings.value["limits"]["poll_seconds"], 30))
        finally:
            cleanup = campaign.stop_active()
            if cleanup.status == "blocked":
                _emit(settings, "cleanup", attempt=(None if cleanup.attempt is None
                      else asdict(cleanup.attempt)), reason=cleanup.reason)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Validate source/config without reading run payloads")
    run = commands.add_parser("execute", help="Verify, wait for exact approval, execute and accept")
    run.add_argument("--pause-after-accepted", type=int)
    args = parser.parse_args(argv)
    try:
        settings = Settings.load(args.config)
        _bootstrap(settings)
        if args.command == "check":
            item = settings.value["provider_adapter"]
            if sha(bounded_file(Path(item["path"]), 1024 * 1024)) != item["sha256"]:
                raise ValueError("provider_adapter_pin_mismatch")
            _emit(settings, "checked")
            return 0
        if args.pause_after_accepted is not None and args.pause_after_accepted < 1:
            raise ValueError("positive_pause_count_required")
        def interrupted(signum: int, frame: Any) -> None:
            raise KeyboardInterrupt(f"signal_{signum}")
        signal.signal(signal.SIGTERM, interrupted)
        return execute(settings, args.pause_after_accepted)
    except (Exception, KeyboardInterrupt) as error:
        print(canonical({"schema": "stpd/m2-campaign-error-v1",
                         "kind": type(error).__name__, "message": str(error)}).decode().strip(),
              file=sys.stderr, flush=True)
        return 1
