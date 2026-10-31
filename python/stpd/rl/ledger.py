"""Append-only decision accounting linked to original trajectory run_id:step.index.

The owner calls trained only after optimizer.step succeeds. Cross-host times are
reported with an explicit offset bound; absent clock calibration stays unknown.
"""
from __future__ import annotations

import collections
import hashlib
import math
import time
from pathlib import Path
from typing import Any

from spireagent.json_boundary import json_bytes

from .measurement import UNIT


class DecisionLedger:
    def __init__(self, path: Path, *, enabled: bool = True,
                 clock_offset_bound_seconds: float | None = None) -> None:
        self.enabled = enabled
        self.path = path
        self.offset = clock_offset_bound_seconds
        self.counts: collections.Counter[str] = collections.Counter()
        self.live: dict[str, dict[str, Any]] = {}
        self.started_at = time.time()
        self.began = time.monotonic()
        self.prior_wall_seconds = 0.0
        self.writer = path.open("ab") if enabled else None
        self.write_seconds = 0.0
        self.published_offset = 0
        self.segments: list[dict[str, Any]] = []

    def write(self, kind: str, **fields: Any) -> None:
        if self.writer is None:
            return
        began = time.monotonic()
        self.writer.write(json_bytes({"schema": "stpd/rl-decision-ledger-1", "unit": UNIT,
            "kind": kind, "at": time.time(), **fields}))
        self.writer.flush()
        self.write_seconds += time.monotonic() - began

    def episode(self, disposition: str, episode: dict[str, Any], record: dict[str, Any],
                **fields: Any) -> None:
        if not self.enabled:
            return
        now = time.time()
        game = next((g for g in record.get("games", ())
                     if g.get("run_id") == episode["run_id"]), {})
        rows = []
        for step in episode["steps"]:
            observation = episode["observations"][step["observation_index"]]["observation"]
            count = len(observation["catalog"])
            row = {"id": f"{episode['run_id']}:{step['index']}", "catalog_size": count,
                "decision": count >= 2, "generated_at": step.get("generated_at"),
                "model_version": record["model_version"], "uses": 0,
                "episode_started_at": game.get("started_at"),
                "admitted_at": now if disposition == "admitted" else None,
                "behavior_entropy": -sum(p * math.log(p)
                    for p in step["behavior"].get("probabilities", ()) if p > 0)}
            rows.append(row)
            for stage in ("generated", "uploaded", disposition):
                self.counts[stage + "_rows"] += 1
                self.counts[stage + "_decisions"] += int(row["decision"])
            if disposition == "admitted":
                self.live[row["id"]] = row
        self.write("episode", disposition=disposition, run_id=episode["run_id"],
            original_content_id=episode.get("original_content_id"),
            shard_artifact_id=record.get("shard_artifact_id"),
            uploaded_at=record.get("finished_at"), rows=rows, **fields)

    def trained(self, ids: list[str], update: int, version: int) -> None:
        if not self.enabled:
            return
        now = time.time()
        first = []
        uses = []
        for key in ids:
            row = self.live[key]
            if row["uses"] == 0:
                self.counts["first_used_rows"] += 1
                self.counts["first_used_decisions"] += int(row["decision"])
                generated = row["generated_at"]
                delay = now - generated if generated is not None else None
                first.append({"id": key, "decision": row["decision"],
                    "generated_to_first_seconds": delay,
                    "clock_offset_bound_seconds": self.offset,
                    "model_lag": version - row["model_version"],
                    "episode_to_first_seconds": (now - row["episode_started_at"]
                        if row.get("episode_started_at") is not None else None),
                    "latency_interval_seconds": ([delay - self.offset, delay + self.offset]
                        if delay is not None and self.offset is not None else None)})
            row["uses"] += 1
            self.counts["sample_uses"] += 1
            self.counts["decision_uses"] += int(row["decision"])
            uses.append(row["uses"])
        self.write("trained", ids=ids, uses=uses, first=first, update=update, version=version)

    def retire(self, key: str, uses: int, reason: str, version: int) -> None:
        if not self.enabled:
            return
        row = self.live.pop(key)
        if row["uses"] != uses:
            raise ValueError("decision_ledger_reuse_mismatch")
        self.counts[f"retired_{reason}_uses{uses}_rows"] += 1
        self.counts[f"retired_{reason}_uses{uses}_decisions"] += int(row["decision"])
        self.write("retired", id=key, uses=uses, decision=row["decision"],
                   reason=reason, version=version)

    def summary(self) -> dict[str, Any]:
        wall = self.prior_wall_seconds + time.monotonic() - self.began
        admitted = self.counts["admitted_decisions"]
        first = self.counts["first_used_decisions"]
        return {"enabled": self.enabled, "unit": UNIT, "counts": dict(self.counts),
            "started_at": self.started_at, "wall_seconds": wall,
            "effective_new_decisions_per_hour": first * 3600 / wall if wall else None,
            "first_use_rate": first / admitted if admitted else None,
            "live_rows": len(self.live),
            "live_unused_decisions": sum(row["decision"] and row["uses"] == 0
                                         for row in self.live.values()),
            "write_seconds": self.write_seconds,
            "clock_offset_bound_seconds": self.offset}

    def freeze_segments(self) -> list[tuple[dict[str, Any], bytes]]:
        """Freeze complete JSONL records; the owner never mutates returned bytes."""
        if self.writer is None:
            return []
        self.writer.flush()
        result = []
        with self.path.open("rb") as stream:
            stream.seek(self.published_offset)
            while True:
                start = stream.tell()
                lines: list[bytes] = []
                size = 0
                while size < 4 * 1024**2:
                    line = stream.readline(16 * 1024**2 + 1)
                    if not line:
                        break
                    if len(line) > 16 * 1024**2 or not line.endswith(b"\n"):
                        raise ValueError("decision_ledger_record_exceeds_blob_bound")
                    if lines and size + len(line) > 16 * 1024**2:
                        stream.seek(-len(line), 1)
                        break
                    lines.append(line)
                    size += len(line)
                if not lines:
                    break
                raw = b"".join(lines)
                identity = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
                    "start_offset": start, "end_offset": stream.tell()}
                result.append((identity, raw))
                self.segments.append(identity)
                self.published_offset = stream.tell()
        return result

    def state(self) -> dict[str, Any]:
        return {"counts": dict(self.counts), "live": self.live, "started_at": self.started_at,
                "wall_seconds": self.prior_wall_seconds + time.monotonic() - self.began}

    def restore(self, state: dict[str, Any]) -> None:
        self.counts.update(state.get("counts", {}))
        self.live = state.get("live", {})
        self.started_at = state.get("started_at", time.time())
        self.prior_wall_seconds = state.get("wall_seconds", 0.0)

    def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            self.writer = None
