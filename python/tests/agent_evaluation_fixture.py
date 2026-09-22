"""Synthetic finalized Runtime bytes; never native or Human qualification."""

from __future__ import annotations

import hashlib
from pathlib import Path

from spireagent.encoding import canonical_json
from spireagent.live_evaluation import EXPECTED, FILES


def evidence(
    root: Path, *, tainted: bool = False, terminal_event: str = "stopped"
) -> tuple[Path, dict]:
    run_id = "run-00000000-0000-0000-0000-000000000001"
    directory = root / run_id
    directory.mkdir(parents=True)
    adapter = {
        "id": "synthetic",
        "version": "1.0.0",
        "code_sha256": "d" * 64,
        "protocol": "sts2.policy-runtime/decision-only-ndjson-1",
    }
    policy = {
        "schema": "sts2.policy-runtime/policy-manifest-1",
        "manifest_id": "fixture",
        "policy": {"id": "fixture", "version": "1.0.0"},
        "adapter": adapter,
        "artifact": {"sha256": "b" * 64},
    }
    manifest = {
        "schema": "sts2.policy-runtime/agent-run-1",
        "run_id": run_id,
        "manifest_id": "fixture",
        "policy_manifest_sha256": sha(canonical_json(policy).encode()),
        "policy_id": "fixture",
        "policy_version": "1.0.0",
        "policy_artifact_sha256": "b" * 64,
        "runtime_version": "0.1.0-rc.1",
        "runtime_code_sha256": "c" * 64,
        "started_at": "2026-09-16T00:00:00.000Z",
        "ended_at": "2026-09-16T00:01:00.000Z",
        "status": "tainted" if tainted else "stopped",
        "mode": "human",
        "tainted": tainted,
        "append_only": True,
    }
    attestation = {
        "schema": "sts2.policy-runtime/adapter-attestation-1",
        "run_id": run_id,
        "manifest_id": "fixture",
        "policy_manifest_sha256": manifest["policy_manifest_sha256"],
        "status": "attested",
        "expected": adapter,
        "actual": adapter,
        "attested_at": "2026-09-16T00:00:00.500Z",
    }
    for name, value in (
        ("manifest.json", manifest),
        ("policy-manifest.json", policy),
        ("adapter-attestation.json", attestation),
    ):
        (directory / name).write_text(canonical_json(value) + "\n")
    event = {
        "schema": "sts2.policy-runtime/agent-run-event-1",
        "sequence": 1,
        "recorded_at": "2026-09-16T00:01:00.000Z",
        "kind": terminal_event,
        "payload": {"mode": "human"} if terminal_event == "mode_changed" else {},
    }
    (directory / "events.jsonl").write_text(canonical_json(event) + "\n")
    files = [
        {
            "path": name,
            "bytes": len((directory / name).read_bytes()),
            "sha256": sha((directory / name).read_bytes()),
        }
        for name in FILES
        if name not in {"checksums.sha256", "evidence-manifest.json"}
    ]
    immutable = {
        "schema": "sts2.policy-runtime/immutable-evidence-manifest-1",
        "run_id": run_id,
        "complete": True,
        "append_only": True,
        "files": files,
        "manifest_sha256": sha(canonical_json({"run_id": run_id, "files": files}).encode()),
    }
    (directory / "evidence-manifest.json").write_text(canonical_json(immutable) + "\n")
    (directory / "checksums.sha256").write_text(
        "".join(
            sha((directory / name).read_bytes()) + "  " + name + "\n"
            for name in FILES
            if name != "checksums.sha256"
        )
    )
    return directory, {name: manifest[name] for name in EXPECTED}


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
