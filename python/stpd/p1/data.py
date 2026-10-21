"""Read current optional segments without inventing trajectories or holdouts."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stpd.data.splits import assign_episode_splits


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


@dataclass
class Example:
    segment_id: str
    point_index: int
    observation: dict[str, Any]
    chosen: str | None
    mask: dict[str, bool]
    next_index: int | None
    end_target: dict[str, Any] | None
    provenance: dict[str, Any]


@dataclass
class SegmentInput:
    source: dict[str, Any]
    examples: list[Example]
    receipt: dict[str, Any]


def read_segments(path: Path, expected_sha256: str) -> SegmentInput:
    raw = path.read_bytes()
    if digest(raw) != expected_sha256:
        raise ValueError("P1 source checksum mismatch")
    value = json.loads(raw)
    if (value.get("schema"), value.get("profile"), value.get("revision")) != (
        "sts2.p1/native-segments-1",
        "p1-global-r1",
        "r1.4",
    ):
        raise ValueError(
            "T0 supports current r1.4 segments only; historical input is regression-only"
        )
    branch = value["branch"]
    examples: list[Example] = []
    excluded: Counter[str] = Counter()
    terminal = [
        p
        for s in value["segments"]
        for p in s["points"]
        if p["observation"]["status"] == "terminal"
    ]
    for segment in value["segments"]:
        if segment["branch"] != branch:
            raise ValueError("segment branch differs from source")
        boundary = segment["start_boundary"]
        if (
            not boundary["complete_basis"]
            or boundary["unfinished_reward_commitment"]
            or boundary["pending_native_decisions"]
            or boundary["selector_continuation"]
        ):
            raise ValueError("segment begins at an incomplete protocol boundary")
        points, end = segment["points"], segment["end_target"]
        for i, point in enumerate(points):
            obs, mask, chosen = point["observation"], point["mask"], point["chosen"]
            if obs.get("revision") != "r1.4" or obs.get("profile") != "p1-global-r1":
                raise ValueError("point profile/revision drift")
            if set(mask) != {"N", "next", "end"} or any(type(x) is not bool for x in mask.values()):
                raise ValueError("invalid supervision mask")
            catalog = obs["catalog"]
            ids = [c["choice_id"] for c in catalog]
            if len(set(ids)) != len(ids):
                raise ValueError("duplicate catalog choice ID")
            if mask["N"]:
                if not obs["envelope"]["required_information_complete"] or ids.count(chosen) != 1:
                    raise ValueError(
                        "N requires complete input and exactly one chosen in this catalog"
                    )
            else:
                excluded["N_false:" + obs["status"]] += 1
            if mask["next"] and i + 1 >= len(points):
                raise ValueError("next cannot cross a segment boundary")
            if mask["end"] and (
                not end
                or end["branch"] != branch
                or end["result"] not in {"loss", "win"}
                or not any(
                    p["source_event_id"] == end["source_event_id"]
                    and p["observation"]["terminal"]["outcome"] == end["result"]
                    for p in terminal
                )
            ):
                raise ValueError("end target needs an observed terminal on this branch")
            examples.append(
                Example(
                    segment["segment_id"],
                    i,
                    obs,
                    chosen,
                    mask,
                    i + 1 if mask["next"] else None,
                    end if mask["end"] else None,
                    {k: v for k, v in point.items() if k not in {"observation", "chosen", "mask"}},
                )
            )
    root = "/".join(branch[k] for k in ("session_id", "timeline_id", "run_id"))
    splits = assign_episode_splits(
        [{"episode_id": s["segment_id"], "lineage": root} for s in value["segments"]],
        salt="p1-t0-engineering-only-1",
        train_ratio=10000,
        dev_ratio=0,
        test_ratio=0,
        root_field="lineage",
    )
    receipt = {
        "schema": "stpd/p1-t0-consumption-1",
        "source_sha256": expected_sha256,
        "source_content_id": value["source_content_id"],
        "branch": branch,
        "profile": value["profile"],
        "revision": value["revision"],
        "segments": len(value["segments"]),
        "observations": len(examples),
        "N": sum(e.mask["N"] for e in examples),
        "multi_N": sum(e.mask["N"] and len(e.observation["catalog"]) > 1 for e in examples),
        "next": sum(e.mask["next"] for e in examples),
        "end": sum(e.mask["end"] for e in examples),
        "excluded_N": dict(excluded),
        "gaps": len(value["gaps"]),
        "discarded_choices": value["discarded_choices"],
        "split_use": "train-only engineering snapshot; one original run; no holdout",
        "splits": {key: vars(item) for key, item in splits.items()},
        "model_state": "stateless; reset for every observation including each segment start",
        "protocol_state": "producer start_protocol_state retained without modification",
        "target_spec": {
            "id": "p1-N-next-end-1",
            "N": "exact catalog chosen",
            "next": "next_index within same segment only",
            "end": "observed terminal on same branch; metadata only for M-std",
        },
        "training_admission": "authorized T0 engineering only",
        "historical_training_points": 0,
    }
    return SegmentInput(value, examples, receipt)
