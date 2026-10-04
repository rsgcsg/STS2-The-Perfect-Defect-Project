"""Synthetic root recovery documents; never an operator approval for real compute."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tools.m2_campaign.config import Settings, canonical, sha


def reference(root: Path, name: str, value: object) -> dict[str, str]:
    raw = canonical(value)
    location = root / name
    location.write_bytes(raw)
    return {"path": str(location), "sha256": sha(raw)}


def synthetic_settings(root: Path) -> Settings:
    run = {key: "d" * 64 for key in (
        "run_id", "dataset_id", "allocation_id", "source_view_id", "input_identity",
        "preparation_sha256", "config_sha256", "training_admission_sha256", "dev_admission_sha256",
    )}
    run.update(operation_id="1" * 32, evaluation_operation_id="2" * 32,
               max_requests=6, train_decisions=10, dev_decisions=1)
    adapter = root / "old-provider.py"
    adapter.write_text("# synthetic original transport\n")
    return Settings.decode(canonical({
        "schema": "stpd/m2-campaign-config-v1", "worker_python_root": str(root / "worker"),
        "producer": {"repository": "synthetic/test", "source_revision": "a" * 40,
                     "uv_lock_sha256": "b" * 64},
        "project_config": str(root / "project.toml"), "store_root": str(root / "store"),
        "store_lock": str(root / "store.lock"), "journal_root": str(root / "journal"),
        "approval_dir": str(root / "approvals"),
        "provider_adapter": {"path": str(adapter), "sha256": sha(adapter.read_bytes())},
        "runtime": {"torch": "synthetic", "python": "synthetic", "platform": "synthetic",
                    "default_dtype": "torch.float32", "cpu_threads": 1,
                    "implementation_sha256": "c" * 64},
        "runs": [run], "max_windows": 512, "goal_epochs": 5,
        "preparation_evidence": [{"path": str(root / "prepared.json"), "sha256": "d" * 64}],
        "budget_scope_id": "e" * 32,
        "limits": {"command_seconds": 60, "approval_wait_seconds": 10, "poll_seconds": 1,
                   "max_rss_bytes": 100_000_000, "minimum_free_bytes": 1,
                   "batch_raw_usd": "20.00", "workspace_cap_usd": "24.00",
                   "external_exposure_usd": "2.20"},
    }))


def replacement_settings(before: Settings) -> Settings:
    value = json.loads(before.raw)
    adapter = Path(value["provider_adapter"]["path"]).with_name("replacement-provider.py")
    adapter.write_text("# synthetic replacement transport\n")
    value["provider_adapter"] = {"path": str(adapter), "sha256": sha(adapter.read_bytes())}
    return Settings.decode(canonical(value))


def packet(after: Settings, output: Path) -> tuple[Path, dict[str, Any]]:
    """Build a test-only receipt from synthetic journal metadata and source bytes."""
    output.mkdir(parents=True, exist_ok=True)
    journal = Path(after.value["journal_root"])
    before_raw = (journal / "settings.json").read_bytes()
    before = Settings.decode(before_raw)
    state_raw = (journal / "state.json").read_bytes()
    state = json.loads(state_raw)
    failed = state["attempts"][-1]
    grant_raw = (journal / "objects" / failed["approval"]["sha256"]).read_bytes()
    grant = json.loads(grant_raw)
    now = datetime.now(UTC)
    config = output / "new-settings.json"
    config.write_bytes(after.raw)
    attempt = {key: failed[key] for key in ("attempt_id", "run_id", "ordinal", "slice")}
    observation = {
        "schema": "stpd/m2-campaign-unsubmitted-prepare-observation-v1",
        "config_sha256": before.identity, "attempt_id": failed["attempt_id"],
        "request_sha256": failed["request"]["sha256"], "grant_sha256": sha(grant_raw),
        "provider": grant["provider"], "deployment_name": "stpd-public-m2-" + failed["attempt_id"],
        "observed_at_utc": now.isoformat(), "complete": True, "exact_name_absent": True,
        "active_apps": 0, "active_gpu": 0, "active_tasks": 0,
        "active_containers": 0, "active_sandboxes": 0,
        "historical_outcome": "unknown", "historical_cost": "unknown",
    }
    directory = journal / "provider" / failed["attempt_id"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "grant.json").write_bytes(grant_raw)
    runtime = grant["provider"].get("runtime_receipt")
    runtime_raw = (canonical(before.value["runtime"]) if runtime is None
                   else Path(runtime["path"]).read_bytes())
    (directory / "runtime.json").write_bytes(
        runtime_raw,
    )
    local = {
        "schema": "stpd/m2-campaign-local-prepare-evidence-v1",
        "attempt_id": failed["attempt_id"], "request_sha256": failed["request"]["sha256"],
        "grant_sha256": sha(grant_raw), "directory": str(directory), "no_submit_evidence": True,
        "files": [{"name": item.name, "sha256": sha(item.read_bytes()), "size": item.stat().st_size}
                  for item in sorted(directory.iterdir())],
    }
    package = Path(__file__).resolve().parents[1] / "tools" / "m2_campaign"
    sources = [package / name for name in (
        "__init__.py", "cli.py", "config.py", "core.py", "journal.py", "authority.py",
        "backend.py", "recovery.py",
    )] + [package.parent / "public_m2_campaign.py", Path(after.value["provider_adapter"]["path"])]
    closure = {"schema": "stpd/m2-campaign-implementation-closure-v1",
               "files": [{"path": str(item), "sha256": sha(item.read_bytes()),
                          "size": item.stat().st_size} for item in sources]}
    reserved = []
    for row in state["attempts"]:
        if row["approval"] is not None:
            data = json.loads((journal / "objects" / row["approval"]["sha256"]).read_bytes())
            reserved.append({"attempt_id": row["attempt_id"],
                             "grant_sha256": row["approval"]["sha256"],
                             "raw_ceiling_usd": data["raw_ceiling_usd"]})
    receipt = {
        "schema": "stpd/m2-campaign-recovery-receipt-v1",
        "action": "abandon_unsubmitted_prepare_and_transition",
        "origin_settings_sha256": before.identity, "from_settings_sha256": before.identity,
        "to_settings_sha256": after.identity,
        "to_settings": {"path": str(config), "sha256": after.identity},
        "journal_root": str(journal), "budget_scope_id": before.value["budget_scope_id"],
        "before_state_sha256": sha(state_raw),
        "before_history": {"name": sorted((journal / "history").iterdir())[-1].name,
                           "sha256": sha(state_raw)},
        "attempt": attempt, "request_sha256": failed["request"]["sha256"],
        "grant_sha256": sha(grant_raw),
        "provider_adapter_before": before.value["provider_adapter"],
        "provider_adapter_after": after.value["provider_adapter"],
        "implementation_closure": reference(output, "closure.json", closure),
        "provider_observation": reference(output, "observation.json", observation),
        "local_prepare_evidence": reference(output, "local.json", local),
        "replacement_limit": 1, "reservations": reserved,
        "retained_raw_ceiling_usd": grant["raw_ceiling_usd"],
        "issued_at_utc": now.isoformat(),
        "expires_at_utc": (now + timedelta(minutes=5)).isoformat(),
    }
    location = output / "recovery.json"
    location.write_bytes(canonical(receipt))
    return location, receipt
