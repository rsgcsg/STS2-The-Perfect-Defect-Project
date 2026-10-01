"""Stateless generic-Snapshot decision port for trusted public light-action M0."""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
from pathlib import Path
from typing import Any

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError, object_fields

from ..public_m0_policy_installation import SNAPSHOT_SCHEMA, validate
from .token_decision import LightActionM0DecisionScorer, check_light_action_m0_model
from .token_port import serve

ROOT = Path(__file__).resolve().parents[2]


class PublicM0PolicyAdapter:
    """Score the complete current generic Connector catalog in its existing order."""

    def __init__(self, config_path: Path, manifest_path: Path,
                 binding_root: Path | None = None) -> None:
        self.config, self.manifest = validate(
            ROOT, config_path, manifest_path, binding_root=binding_root,
        )
        snapshot = self.config["qwen_snapshot"]
        self.scorer = LightActionM0DecisionScorer(
            Path(self.config["export_path"]),
            snapshot=Path(snapshot) if snapshot is not None else None,
        )
        _, info = check_light_action_m0_model(self.scorer.artifact)
        if (
            self.scorer.artifact.artifact_id != self.config["model_id"]
            or info["schema"] != "stpd/stage1a-light-action-m0-public-model-v1"
            or info["source_renderer"] != self.config["renderer"]
            or self.manifest.get("representation") != {
                "id": info["source_renderer"]["profile"],
                "version": info["source_renderer"]["version"],
                "input_schema": SNAPSHOT_SCHEMA,
            }
            or self.manifest.get("adapter_config", {}).get("public_m0", {}).get("renderer")
            != info["source_renderer"]
        ):
            raise BoundaryError("public_m0_policy", "public_model_identity_mismatch")
        self.closed = False

    def decide(self, value: object) -> dict[str, Any]:
        if self.closed:
            raise BoundaryError("public_m0_policy", "adapter_closed")
        request = object_fields(
            value,
            {"run_id", "manifest", "bundle", "candidate_digest", "candidate_count"},
            "public_m0.request",
        )
        if (
            not isinstance(request["run_id"], str) or not request["run_id"]
            or request["manifest"] != self.manifest
        ):
            raise BoundaryError("public_m0_policy", "request_identity_mismatch")
        bundle = object_fields(request["bundle"], {"observation", "reads"},
                               "public_m0.bundle")
        if bundle["reads"] != []:
            raise BoundaryError("public_m0_policy", "unexpected_reads")
        snapshot = bundle["observation"]
        if not isinstance(snapshot, dict) or snapshot.get("schema") != SNAPSHOT_SCHEMA:
            raise BoundaryError("public_m0_policy", "generic_snapshot_required")
        if self.manifest["representation"].get("input_schema") != SNAPSHOT_SCHEMA:
            raise BoundaryError("public_m0_policy", "generic_snapshot_required")
        try:
            actions = snapshot["bound_actions"]["actions"]
            keys = [action["bound_action_id"] for action in actions]
            interaction_kind = snapshot["interaction"]["kind"]
            verbs = [action["verb"] for action in actions]
        except (KeyError, TypeError) as error:
            raise BoundaryError("public_m0_policy", "complete_catalog_required") from error
        candidate_digest = hashlib.sha256(canonical_json(keys).encode()).hexdigest()
        if (
            type(request["candidate_count"]) is not int
            or request["candidate_count"] != len(keys)
            or request["candidate_digest"] != candidate_digest
        ):
            raise BoundaryError("public_m0_policy", "candidate_binding_mismatch")
        support = self.manifest["support"]
        if (interaction_kind not in support["interaction_kinds"]
                or any(verb not in support["action_verbs"] for verb in verbs)):
            raise BoundaryError("public_m0_policy", "unsupported_whole_decision")
        scores = self.scorer.score_snapshot(snapshot)
        if (
            not isinstance(scores, dict) or list(scores) != keys or not scores
            or any(not isinstance(score, (int, float)) or not math.isfinite(score)
                   for score in scores.values())
        ):
            raise BoundaryError("public_m0_policy", "score_binding_mismatch")
        values = list(scores.values())
        return {
            "candidate_digest": candidate_digest,
            "scores": values,
            "selected_index": max(range(len(values)), key=values.__getitem__),
        }

    def close(self) -> None:
        self.closed = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--binding-root", type=Path)
    args = parser.parse_args()
    import torch

    torch.set_num_threads(2)
    adapter = PublicM0PolicyAdapter(
        args.config.resolve(), args.manifest.resolve(), args.binding_root,
    )
    return serve(adapter, sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
