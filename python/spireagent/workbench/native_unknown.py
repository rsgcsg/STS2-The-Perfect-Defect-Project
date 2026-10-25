"""Explicit unknown release proofs; never Stop, recover, or rewrite a run."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import socket
import subprocess
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit

from spireagent.json_boundary import BoundaryError

SCHEMA = "spireagent/native-unknown-release-v1"


def reject(code: str) -> NoReturn:
    raise BoundaryError("native_episode", code)


def durable_readback(path: Path, expected_raw: bytes) -> None:
    with path.open("rb") as stream:
        if stream.read(len(expected_raw) + 1) != expected_raw:
            reject("native_unknown_release_unconfirmed")
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def no_handles(directory: Path) -> None:
    """A failed census is not an empty census. This operation is POSIX-only."""
    result = subprocess.run(
        ["lsof", "-nP", "-Fpn", "+D", str(directory)],
        capture_output=True, timeout=30, check=False,
    )
    if result.returncode != 1 or result.stdout or result.stderr:
        reject("native_process_occupancy_unproved")


def unoccupied(profile: dict[str, Any], state_root: Path) -> dict[str, Any]:
    """Fresh OS observation plus the exact owner receipt, not port absence alone.

    Reject even another STS2 process: never adopt, kill, or inspect its state.
    The caller separately verifies the exact original native close receipt.
    """
    # The exact installed Host owns platform executable names and fail-closed
    # enumeration (including macOS's spaced executable name).
    module = Path(profile["host_package_directory"]) / "src/workstation-api.mjs"
    script = ("import {pathToFileURL} from 'node:url';"
              "const {listGameProcesses}=await import(pathToFileURL(process.argv[1]));"
              "process.stdout.write(JSON.stringify("
              "listGameProcesses(process.platform,{failClosed:true})));" )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(module)],
        capture_output=True, text=True, timeout=15, check=False)
    if result.returncode or not result.stdout.strip() or result.stderr:
        reject("native_process_occupancy_unproved")
    processes = json.loads(result.stdout)
    if not isinstance(processes, list) or any(not isinstance(p, str) for p in processes):
        reject("native_process_occupancy_unproved")
    if processes:
        reject("native_game_still_present")
    no_handles(state_root)
    endpoint = urlsplit(profile["endpoint"])
    # Only a positive local refusal proves no endpoint listener. Timeout, denied
    # access, DNS failure, or an answering replacement all remain blocked.
    if endpoint.hostname != "127.0.0.1" or endpoint.port is None:
        reject("native_process_occupancy_unproved")
    with socket.socket() as connection:
        connection.settimeout(2)
        if connection.connect_ex(("127.0.0.1", endpoint.port)) != errno.ECONNREFUSED:
            reject("native_endpoint_still_present_or_unknown")
    return {"game_process_census": "absent", "host_state_handles": "absent",
            "endpoint": profile["endpoint"], "endpoint_observation": "connection_refused"}


def expected(record: dict[str, Any], raw: bytes, application_id: str | None) -> dict[str, Any]:
    actor = record.get("actor") or {}
    return {"expected_application_instance_id": application_id,
            "expected_environment_ref": record["environment_ref"],
            "expected_generation_id": record["generation_id"],
            "expected_original_application_instance_id": record["application_instance_id"],
            "expected_run_id": actor.get("run_id"),
            "expected_resource_sha256": hashlib.sha256(raw).hexdigest()}
