"""Read-only actual InputSpec projection over closed diagnostic logs; no model.

Raw views, feature row layout, and V2 permutation semantics are distinct.
P1 graph equality supplies the public bijection; V2 uses symmetric roles and no
scene row positional encoding. This never claims bitwise policy equality.
"""
from __future__ import annotations

import argparse
import dataclasses
import gzip
import hashlib
import json
from pathlib import Path

from stpd.p1.input import INPUT_SPEC, V2_INPUT_SPEC, project


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def features(frame):
    # Binding spellings in frame.choices are not learned. The model consumes
    # action kinds and pooled roles; catalog labels/options also live in entities.
    return {"entities": [dataclasses.asdict(node) for node in frame.entities],
        "roles": frame.roles, "action_kinds": [choice["kind"] for choice in frame.choices]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = {"schema": "m2e-recorded-inputspec-effects-1", "input_specs": [INPUT_SPEC, V2_INPUT_SPEC],
        "policy_output_effect": "not_tested", "v2_feature_graph_equivalence": "expected_by_declared_permutation_contract; per-record exact feature layout measured; representative explicit node permutations tested separately", "sources": [], "no_model_or_game_started": True}
    for report_path in sorted(args.batch.glob("*/report.json")):
        report = json.loads(report_path.read_text())
        if report.get("mode") != "collect_all_differences":
            continue
        path = report_path.parent / "replay.jsonl.gz"
        summary = {"source": report_path.parent.name, "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "record_sha256": report["record"]["sha256"], "pending_expected": report.get("model_frames_pending", 0),
            "pending_read": 0, "legacy_feature_layout_changes": 0, "v2_feature_layout_changes": 0,
            "v2_public_graph_equal_cases": 0, "projection_errors": [], "examples": []}
        if hashlib.sha256(path.read_bytes()).hexdigest() != summary["record_sha256"]:
            raise ValueError("closed diagnostic record digest mismatch")
        with gzip.open(path, "rt", encoding="utf-8") as source:
            for line in source:
                row = json.loads(line)
                if row["kind"] != "diagnostic_model_frame_pending":
                    continue
                summary["pending_read"] += 1
                detail = {key: row.get(key) for key in ["sequence", "event_id", "phase", "surface"]}
                try:
                    for label, spec in [("legacy", INPUT_SPEC), ("v2", V2_INPUT_SPEC)]:
                        left = features(project(row["reference_observation"], input_projection=spec))
                        right = features(project(row["actual_observation"], input_projection=spec))
                        detail[label + "_feature_layout_changed"] = digest(left) != digest(right)
                        detail[label + "_left_sha256"] = digest(left)
                        detail[label + "_right_sha256"] = digest(right)
                    summary["legacy_feature_layout_changes"] += int(detail["legacy_feature_layout_changed"])
                    summary["v2_feature_layout_changes"] += int(detail["v2_feature_layout_changed"])
                    if row.get("strict_graph_verdict") != "equal":
                        raise ValueError("pending projection lacks successful public graph witness")
                    summary["v2_public_graph_equal_cases"] += 1
                except (ValueError, TypeError, KeyError) as error:
                    detail["error"] = str(error)
                    summary["projection_errors"].append(detail)
                if len(summary["examples"]) < 3:
                    summary["examples"].append(detail)
        summary["complete"] = summary["pending_read"] == summary["pending_expected"] and not summary["projection_errors"]
        result["sources"].append(summary)
    result["complete"] = all(row["complete"] for row in result["sources"])
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
