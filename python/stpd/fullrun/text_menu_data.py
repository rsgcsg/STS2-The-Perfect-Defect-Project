"""Versioned text-menu traces and an explicitly admitted engineering BC view.

These records describe a public U transition. Native input delivery is not a
canonical successor, and no record here establishes a Human observation label.
"""

from __future__ import annotations

import io
from collections import Counter
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, json_bytes
from spireagent.storage.store import ArtifactStore

from ..canonical import semantic_hash
from .features import ModelSample

SOURCE_SCHEMA = "stpd/text-menu-trace-v1"
VIEW_SCHEMA = "stpd/text-menu-bc-view-v1"
MAX_SOURCE_BYTES = 64 * 1024**2
MAX_ROWS = 100000


def _text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise BoundaryError("text_menu_data", code)
    return value


def _record(row: Any) -> dict:
    if not isinstance(row, dict) or set(row) != {
        "schema", "record_id", "run_id", "step_index", "origin", "source_ref",
        "snapshot", "request", "selected_action_id", "result",
    } or row["schema"] != SOURCE_SCHEMA:
        raise BoundaryError("text_menu_data", "invalid_record_schema")
    if row["origin"] == "human":
        raise BoundaryError("text_menu_data", "human_observation_mapping_unsupported")
    if row["origin"] not in {"synthetic", "agent"}:
        raise BoundaryError("text_menu_data", "unsupported_origin")
    for key in ("record_id", "run_id", "source_ref", "selected_action_id"):
        _text(row[key], "missing_record_identity")
    if type(row["step_index"]) is not int or row["step_index"] < 0:
        raise BoundaryError("text_menu_data", "invalid_step_index")
    snapshot, request, result = row["snapshot"], row["request"], row["result"]
    if not all(isinstance(value, dict) for value in (snapshot, request, result)):
        raise BoundaryError("text_menu_data", "missing_frame_or_result")
    from .text_menu_inputs import project_text_menu_snapshot

    public = project_text_menu_snapshot(snapshot)
    actions = snapshot["menu_actions"]["actions"]
    ids = [a["action_id"] for a in actions]
    if ids != list(public.action_ids) or ids.count(row["selected_action_id"]) != 1:
        raise BoundaryError("text_menu_data", "choice_not_in_exact_catalog")
    chosen = actions[ids.index(row["selected_action_id"])]
    if (request.get("expected_snapshot_id") != snapshot.get("snapshot_id")
            or request.get("bound_action_id") != row["selected_action_id"]
            or request.get("input_profile") != "text-menu-v1"
            or not isinstance(request.get("request_id"), str) or not request["request_id"]):
        raise BoundaryError("text_menu_data", "request_frame_binding_mismatch")
    if result.get("schema") != "sts2.player-environment/text-menu-action-result-1":
        raise BoundaryError("text_menu_data", "result_schema_mismatch")
    if (result.get("input_profile") != "text-menu-v1"
            or result.get("request_id") != request["request_id"]
            or result.get("status") not in {"applied", "not_applied", "unknown"}):
        raise BoundaryError("text_menu_data", "result_choice_mismatch")
    if result.get("status") == "applied":
        if result.get("action") != chosen:
            raise BoundaryError("text_menu_data", "result_choice_mismatch")
        domain = chosen["effect_domain"]
        delivery = result.get("native_delivery")
        if (result.get("effect_domain") != domain
                or (domain == "text_menu" and delivery is not None)
                or (domain == "native_input" and delivery != "delivered")
                or not isinstance(result.get("successor"), dict)):
            raise BoundaryError("text_menu_data", "applied_effect_mismatch")
        successor = result["successor"]
        project_text_menu_snapshot(successor)
        if successor.get("snapshot_id") == snapshot.get("snapshot_id"):
            raise BoundaryError("text_menu_data", "unchanged_successor_identity")
    else:
        if (result.get("action") not in (None, chosen)
                or result.get("successor") is not None
                or result.get("effect_domain") is not None
                or result.get("native_delivery") not in (None, "not_delivered", "unknown")):
            raise BoundaryError("text_menu_data", "unproved_successor")
    return row


def validate_records(rows: tuple[dict, ...], *, admit_agent: bool = False) -> tuple[dict, ...]:
    if not isinstance(rows, tuple) or not 1 <= len(rows) <= MAX_ROWS:
        raise BoundaryError("text_menu_data", "invalid_record_count")
    ids: set[str] = set()
    steps: set[tuple[str, int]] = set()
    previous: dict[str, dict] = {}
    for row in rows:
        _record(row)
        if row["origin"] == "agent" and not admit_agent:
            raise BoundaryError("text_menu_data", "agent_admission_opt_in_required")
        key = (row["run_id"], row["step_index"])
        if row["record_id"] in ids or key in steps:
            raise BoundaryError("text_menu_data", "duplicate_record_or_step")
        prior = previous.get(row["run_id"])
        if prior is not None:
            if row["step_index"] <= prior["step_index"]:
                raise BoundaryError("text_menu_data", "run_step_order_mismatch")
            if row["step_index"] == prior["step_index"] + 1:
                successor = prior["result"].get("successor")
                if (not isinstance(successor, dict)
                        or successor.get("snapshot_id") != row["snapshot"].get("snapshot_id")):
                    raise BoundaryError("text_menu_data", "adjacent_frame_binding_mismatch")
        ids.add(row["record_id"])
        steps.add(key)
        previous[row["run_id"]] = row
    return rows


def publish_text_menu_source(
    store: ArtifactStore, rows: tuple[dict, ...], producer: Producer, *,
    admit_agent: bool = False,
) -> Manifest:
    validate_records(rows, admit_agent=admit_agent)
    raw = b"".join(json_bytes(row) for row in rows)
    if len(raw) > MAX_SOURCE_BYTES:
        raise BoundaryError("text_menu_data", "source_size_limit")
    payload = store.put_payload("records", io.BytesIO(raw), "application/x-ndjson")
    manifest = Manifest("dataset", producer, (), (payload,), FrozenObject.of({
        "schema": SOURCE_SCHEMA, "purpose": "training", "scope": "engineering",
        "agent_admission": admit_agent, "rows": len(rows),
        "source_digest": semantic_hash(list(rows)),
        "origin_counts": dict(Counter(r["origin"] for r in rows)),
        "claim": "public_text_menu_trace_only",
    }))
    store.publish(manifest)
    return manifest


def load_text_menu_source(store: ArtifactStore, identity: str) -> tuple[Manifest, tuple[dict, ...]]:
    manifest = store.get_manifest(identity)
    info = manifest.parameters.value()
    if (manifest.kind != "dataset" or info.get("schema") != SOURCE_SCHEMA
            or manifest.parents or [p.role for p in manifest.payloads] != ["records"]
            or info.get("purpose") != "training" or info.get("scope") != "engineering"
            or type(info.get("agent_admission")) is not bool):
        raise BoundaryError("text_menu_data", "unsupported_source")
    payload = manifest.payload("records")
    if payload.size > MAX_SOURCE_BYTES:
        raise BoundaryError("text_menu_data", "source_size_limit")
    raw = b"".join(store.read_payload(payload))
    rows = tuple(decode_json(line) for line in raw.splitlines())
    validate_records(rows, admit_agent=info["agent_admission"])
    if (raw != b"".join(json_bytes(row) for row in rows)
            or info != {"schema": SOURCE_SCHEMA, "purpose": "training", "scope": "engineering",
                        "agent_admission": info["agent_admission"], "rows": len(rows),
                        "source_digest": semantic_hash(list(rows)),
                        "origin_counts": dict(Counter(r["origin"] for r in rows)),
                        "claim": "public_text_menu_trace_only"}):
        raise BoundaryError("text_menu_data", "source_identity_mismatch")
    return manifest, rows


def _project(rows: tuple[dict, ...]) -> tuple[tuple[ModelSample, ...], dict]:
    from .text_menu_inputs import IDENTITY, project_text_menu_snapshot

    samples: list[ModelSample] = []
    lineage: list[dict] = []
    runs = sorted({row["run_id"] for row in rows})
    if len(runs) < 2:
        raise BoundaryError("text_menu_data", "independent_runs_required")
    parents = {run: run for run in runs}

    def root(run: str) -> str:
        while parents[run] != run:
            parents[run] = parents[parents[run]]
            run = parents[run]
        return run

    fingerprints: dict[str, str] = {}
    for row in rows:
        public = project_text_menu_snapshot(row["snapshot"])
        fingerprint = semantic_hash([public.state_text, list(public.action_texts)])
        previous = fingerprints.setdefault(fingerprint, row["run_id"])
        a, b = root(previous), root(row["run_id"])
        parents[max(a, b)] = min(a, b)
    components = sorted({root(row["run_id"]) for row in rows
                         if row["result"]["status"] == "applied"})
    if len(components) < 2:
        raise BoundaryError("text_menu_data", "independent_components_required")
    dev = components[0]
    for ordinal, row in enumerate(rows):
        snapshot, result = row["snapshot"], row["result"]
        public = project_text_menu_snapshot(snapshot)
        split = "dev" if root(row["run_id"]) == dev else "train"
        included = result["status"] == "applied"
        lineage.append({
            "source_index": ordinal, "record_id": row["record_id"],
            "run_id": row["run_id"], "step_index": row["step_index"],
            "source_ref": row["source_ref"], "origin": row["origin"],
            "snapshot_id": snapshot["snapshot_id"],
            "native_snapshot_id": snapshot["menu"]["native_snapshot_id"],
            "menu_cursor": snapshot["menu"]["cursor"],
            "candidate_digest": public.candidate_digest,
            "candidate_action_ids": list(public.action_ids),
            "selected_action_id": row["selected_action_id"],
            "request_id": row["request"]["request_id"],
            "result_status": result["status"],
            "successor_snapshot_id": result["successor"]["snapshot_id"] if included else None,
            "effect_domain": result.get("effect_domain"),
            "native_delivery": result.get("native_delivery"),
            "split": split, "status": "included" if included else "excluded",
            "reason": None if included else result["status"],
        })
        if included:
            samples.append(ModelSample(
                row["record_id"], row["run_id"], split,
                snapshot["interaction"]["kind"], "text_menu",
                public.state_text, public.action_texts, tuple(public.action_ids),
                public.action_ids.index(row["selected_action_id"]),
            ))
    if {s.split for s in samples} != {"train", "dev"}:
        raise BoundaryError("text_menu_data", "nonempty_train_dev_required")
    return tuple(samples), {"schema": VIEW_SCHEMA, "serializer": IDENTITY,
                            "label_boundary": "synthetic_or_agent_public_action_choice",
                            "native_successor_supervision": False,
                            "human_origin_verified": False,
                            "split_basis": "whole_run_and_duplicate_current_input",
                            "rows": lineage}


def publish_text_menu_bc_view(store: ArtifactStore, source_id: str, producer: Producer) -> Manifest:
    source, rows = load_text_menu_source(store, source_id)
    samples, report = _project(rows)
    payloads = (
        store.put_payload("samples", io.BytesIO(b"".join(json_bytes(s.to_dict()) for s in samples)),
                          "application/x-ndjson"),
        store.put_payload("lineage", io.BytesIO(json_bytes(report)), "application/json"),
    )
    manifest = Manifest("model_view", producer, (Parent("dataset", source_id),), payloads,
                        FrozenObject.of({"schema": VIEW_SCHEMA, "serializer": report["serializer"],
                                         "scope": "engineering", "samples": len(samples),
                                         "source_digest": source.parameters.value()["source_digest"],
                                         "label_boundary": report["label_boundary"]}))
    store.publish(manifest)
    return manifest


def load_text_menu_bc_view(store: ArtifactStore, manifest: Manifest) -> tuple[Manifest, tuple[ModelSample, ...]]:
    if (manifest.kind != "model_view" or manifest.parameters.value().get("schema") != VIEW_SCHEMA
            or [p.role for p in manifest.parents] != ["dataset"]
            or sorted(p.role for p in manifest.payloads) != ["lineage", "samples"]):
        raise BoundaryError("text_menu_data", "unsupported_view")
    source, rows = load_text_menu_source(store, manifest.parent("dataset"))
    samples, report = _project(rows)
    expected = {"schema": VIEW_SCHEMA, "serializer": report["serializer"],
                "scope": "engineering", "samples": len(samples),
                "source_digest": source.parameters.value()["source_digest"],
                "label_boundary": report["label_boundary"]}
    if manifest.parameters.value() != expected:
        raise BoundaryError("text_menu_data", "view_identity_mismatch")
    if (b"".join(store.read_payload(manifest.payload("samples")))
            != b"".join(json_bytes(s.to_dict()) for s in samples)
            or b"".join(store.read_payload(manifest.payload("lineage"))) != json_bytes(report)):
        raise BoundaryError("text_menu_data", "view_projection_mismatch")
    return manifest, samples
