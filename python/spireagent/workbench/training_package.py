"""A portable receipt for an existing prepared Run, never a second scheduler."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError, json_bytes, object_fields

from .developer import ProjectConfig
from .training_cli import PACKAGE_SCHEMA, training_command


def package_start(value: object) -> dict[str, Any]:
    obj = object_fields(value, {"schema", "prepared", "start", "sha256"}, "training_package")
    content = {k: v for k, v in obj.items() if k != "sha256"}
    if (obj["schema"] != PACKAGE_SCHEMA
            or hashlib.sha256(json_bytes(content)).hexdigest() != obj["sha256"]):
        raise BoundaryError("remote_training", "prepared_package_digest_mismatch")
    prepared, start = obj["prepared"], obj["start"]
    if (not isinstance(prepared, dict) or not isinstance(start, dict)
            or prepared.get("state") != "prepared" or prepared.get("enqueued") is not False
            or prepared.get("provider_submitted") is not False
            or prepared.get("run_id") != start.get("run_id")):
        raise BoundaryError("remote_training", "prepared_package_run_mismatch")
    return object_fields(start, {"run_id", "intent_id", "max_seconds", "stop_after"},
                         "training_package.start")


def prepare_package(config_path: Path, body: object, output: Path | None, execution: str,
                    *, wait_seconds: int | None = None, cohort_plan: object = None,
                    configuration_id: str | None = None) -> dict[str, Any]:
    if execution != "remote" or output is None or output.exists():
        raise BoundaryError("remote_training", "new_remote_package_output_required")
    if not isinstance(body, dict):
        raise BoundaryError("remote_training", "exact_prepare_request_required")
    config = ProjectConfig.load(config_path)
    if cohort_plan is not None:
        from .training_cohort import cohort_weighting, freeze_cohort

        if configuration_id is None or "loss_weighting" in body:
            raise BoundaryError("training_cohort", "explicit_single_weighting_source_required")
        if "source_id" not in body:
            body = {**body, "source_id": freeze_cohort(config, cohort_plan, configuration_id)}
        body = {**body, "loss_weighting": cohort_weighting(
            config, cohort_plan, configuration_id, body["source_id"])}
    elif configuration_id is not None:
        raise BoundaryError("training_cohort", "cohort_plan_required")
    prepared = training_command(config, "prepare", body=body,
                                execution="remote", wait_seconds=wait_seconds)
    start = {"run_id": prepared.get("run_id"), "intent_id": body["intent_id"],
             "max_seconds": body["max_seconds"], "stop_after": body["stop_after"]}
    content = {"schema": PACKAGE_SCHEMA, "prepared": prepared, "start": start}
    result = {**content, "sha256": hashlib.sha256(json_bytes(content)).hexdigest()}
    package_start(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as handle:
        json.dump(result, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return {"state": "prepared", "run_id": prepared["run_id"],
            "package": str(output.resolve()), "provider_submitted": False}
