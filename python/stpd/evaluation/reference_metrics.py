"""Research metrics from the exact sealed public originals, with no live control."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from spireagent.json_boundary import BoundaryError
from spireagent.task_outcome import public_terminal
from sts2_platform_evidence import read_global_artifact


def read_reference_metrics(directory: Path, evaluation: dict[str, Any],
                           context: dict[str, Any]) -> dict[str, Any]:
    if evaluation.get("evidence_verification") != "pass":
        raise BoundaryError("reference_metrics", "verified_original_required")
    events_ref = evaluation["agent_task_outcome"]["evidence"]["events"]

    def read(name: str, sha: str, size: int, limit: int) -> bytes:
        if Path(name).name != name or type(size) is not int or not 0 <= size <= limit:
            raise BoundaryError("reference_metrics", "original_capacity_or_path")
        path = directory / name
        if path.is_symlink():
            raise BoundaryError("reference_metrics", "original_path")
        if name.endswith(".bin.gz"):
            try:
                return read_global_artifact(
                    directory, {"path": name, "sha256": sha, "bytes": size}, maximum=limit,
                )
            except (ValueError, OSError) as error:
                raise BoundaryError("reference_metrics", "original_changed") from error
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha:
            raise BoundaryError("reference_metrics", "original_changed")
        return raw

    raw = read(events_ref["path"], events_ref["sha256"], events_ref["bytes"], 64 * 1024**2)
    events = [json.loads(line) for line in raw.splitlines()]
    runtime = context["binding"]["runtime_instance_id"]
    floors: list[int] = []
    observations = []
    results = []
    sources = 0
    kinds: Counter[str] = Counter()
    for event in events:
        kinds[event["kind"]] += 1
        if event["kind"] != "global_source":
            continue
        ref = event["payload"]["artifact"]
        record = json.loads(read(ref["path"], ref["sha256"], ref["bytes"], 16 * 1024**2))
        basis = json.loads(record["serialized_observation"])
        snapshot = basis["snapshot"]
        if (record["capture"]["session"]["runtime_instance_id"] != runtime
                or snapshot["session"]["runtime_instance_id"] != runtime):
            raise BoundaryError("reference_metrics", "foreign_runtime")
        sources += 1
        content = (snapshot.get("persistent") or {}).get("content", {})
        run, player = content.get("run", {}), content.get("player", {})
        floor = run.get("floor")
        if type(floor) is int:
            floors.append(floor)
        row = {"event_sequence": event["sequence"], "snapshot_id": snapshot["snapshot_id"],
               "kind": snapshot["interaction"]["kind"], "status": basis["capture_status"],
               "floor": floor, "act": run.get("act"), "hp": player.get("hp"),
               "max_hp": player.get("max_hp"), "gold": player.get("gold"),
               "character": player.get("character_definition_id"),
               "ascension": run.get("ascension"), "source_sha256": ref["sha256"]}
        observations.append(row)
        result = public_terminal(snapshot) if basis["capture_status"] == "complete" else None
        if result is not None:
            results.append({**row, "result": result,
                            "context": snapshot["interaction"]["content"]["context"]})
    unique = {row["result"] for row in results}
    return {"schema": "stpd/reference-game-metrics-v1", "run_id": evaluation["run_id"],
            "evidence_content_id": evaluation["evidence_content_id"],
            "events_sha256": events_ref["sha256"], "public_sources": sources,
            "max_observed_floor": max(floors) if floors else None,
            "terminal_status": ("observed" if len(unique) == 1
                                else "ambiguous" if unique else "not_observed"),
            "terminal_result": next(iter(unique)) if len(unique) == 1 else None,
            "terminal_witnesses": results, "observations": observations,
            "event_counts": dict(kinds), "strategy_quality": "research_comparison_only",
            "non_claims": ["No HP/process-exit inference of win or loss",
                           "Not Human origin or research admission"]}
