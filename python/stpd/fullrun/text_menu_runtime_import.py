"""Import finalized, typed-verified Policy Runtime text-menu Agent evidence.

The immutable Agent Run remains the source. Only exact dispatched, correlated
Connector results become trace rows; a decision, dispatch attempt, missing
result, or later observation is never invented into native delivery or Z.
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from sts2_platform_evidence import verify_agent_run_evidence

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject
from spireagent.storage.store import ArtifactStore

from ..canonical import semantic_hash
from .text_menu_data import SOURCE_SCHEMA, publish_text_menu_source
from .text_menu_inputs import INPUT_PROFILE, SNAPSHOT_SCHEMA, project_text_menu_snapshot

EVIDENCE_SCHEMA = "stpd/verified-text-menu-agent-run-v1"
FILES = ("adapter-attestation.json", "checksums.sha256", "events.jsonl",
         "evidence-manifest.json", "manifest.json", "policy-manifest.json")
MAX_ARCHIVE_BYTES = 64 * 1024**2
OUTCOME_KINDS = frozenset({"menu_navigation", "text_native_delivery",
                           "text_native_unknown", "text_menu_not_applied"})


def _verified_files(directory: Path) -> tuple[dict[str, bytes], Any]:
    first = verify_agent_run_evidence(directory)
    if not first.passed or first.value is None:
        raise BoundaryError("text_menu_import", "agent_evidence_verification_failed")
    content = {}
    size = 0
    for name in FILES:
        file = directory / name
        if file.is_symlink() or not file.is_file():
            raise BoundaryError("text_menu_import", "agent_evidence_inventory_changed")
        raw = file.read_bytes()
        size += len(raw)
        if size > MAX_ARCHIVE_BYTES:
            raise BoundaryError("text_menu_import", "agent_evidence_size_limit")
        content[name] = raw
    # Verify exactly the copied bytes, closing the verification-to-read gap.
    with tempfile.TemporaryDirectory(prefix="verified-text-menu-") as name:
        copied = Path(name)
        for relative, raw in content.items():
            (copied / relative).write_bytes(raw)
        second = verify_agent_run_evidence(copied)
        if (not second.passed or second.value is None
                or second.value.content_id != first.value.content_id):
            raise BoundaryError("text_menu_import", "agent_evidence_bytes_changed")
    return content, first.value


def _archive(content: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with (gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0) as packed,
          tarfile.open(fileobj=packed, mode="w") as archive):
        for name in FILES:
            raw = content[name]
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mode = 0o600
            info.mtime = 0
            archive.addfile(info, io.BytesIO(raw))
    return buffer.getvalue()


def load_verified_agent_run_artifact(
    store: ArtifactStore, identity: str,
) -> tuple[Any, tuple[dict[str, Any], ...]]:
    """Reverify archived exact bytes before accepting an evidence parent."""
    manifest = store.get_manifest(identity)
    info = manifest.parameters.value()
    if (manifest.kind != "evidence" or info.get("schema") != EVIDENCE_SCHEMA
            or manifest.parents or [p.role for p in manifest.payloads] != ["archive"]
            or manifest.payload("archive").size > MAX_ARCHIVE_BYTES):
        raise BoundaryError("text_menu_import", "verified_evidence_parent_required")
    raw = b"".join(store.read_payload(manifest.payload("archive")))
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise BoundaryError("text_menu_import", "agent_evidence_size_limit")
    with tempfile.TemporaryDirectory(prefix="load-text-menu-evidence-") as name:
        directory = Path(name)
        try:
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
                members = archive.getmembers()
                if (tuple(member.name for member in members) != FILES
                        or any(not member.isfile() for member in members)):
                    raise BoundaryError("text_menu_import", "agent_evidence_inventory_changed")
                total = 0
                for member in members:
                    total += member.size
                    if total > MAX_ARCHIVE_BYTES:
                        raise BoundaryError("text_menu_import", "agent_evidence_size_limit")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise BoundaryError("text_menu_import", "agent_evidence_inventory_changed")
                    extracted = stream.read(member.size + 1)
                    if len(extracted) != member.size:
                        raise BoundaryError("text_menu_import", "agent_evidence_size_limit")
                    (directory / member.name).write_bytes(extracted)
        except (OSError, tarfile.TarError) as error:
            raise BoundaryError("text_menu_import", "agent_evidence_archive_invalid") from error
        result = verify_agent_run_evidence(directory)
        if (not result.passed or result.value is None
                or info != {"schema": EVIDENCE_SCHEMA, "content_id": result.value.content_id,
                            "run_id": result.value.run_id,
                            "event_count": result.value.event_count,
                            "origin": "agent", "verification": "typed_pass"}):
            raise BoundaryError("text_menu_import", "verified_evidence_parent_mismatch")
        policy = json.loads((directory / "policy-manifest.json").read_bytes())
        if policy.get("representation", {}).get("input_schema") != SNAPSHOT_SCHEMA:
            raise BoundaryError("text_menu_import", "text_profile_required")
        rows, _ = _trace_rows(_events((directory / "events.jsonl").read_bytes(),
                                      result.value.event_count),
                              result.value.run_id, result.value.content_id)
        return result.value, rows


def _events(raw: bytes, count: int) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in raw.splitlines()]
    if len(rows) != count:
        raise BoundaryError("text_menu_import", "verified_event_count_mismatch")
    return rows


def _trace_rows(events: list[dict[str, Any]], run_id: str, content_id: str) -> tuple[
    tuple[dict[str, Any], ...], dict[str, Any]
]:
    inputs: dict[str, tuple[dict[str, Any], int]] = {}
    decisions: dict[str, tuple[dict[str, Any], int]] = {}
    dispatches: dict[str, tuple[dict[str, Any], int]] = {}
    outcomes: dict[str, tuple[str, dict[str, Any], int]] = {}
    diagnostics: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for event in events:
        kind, payload, sequence = event["kind"], event["payload"], event["sequence"]
        if kind == "text_decision_input":
            identity = payload["decision_id"]
            if identity in inputs:
                raise BoundaryError("text_menu_import", "duplicate_decision_input")
            inputs[identity] = (payload["snapshot"], sequence)
        elif kind == "decision":
            decision = payload["decision"]
            identity = decision["decision_id"]
            if identity in decisions:
                raise BoundaryError("text_menu_import", "duplicate_decision")
            if identity in inputs and (previous is None or previous["kind"] != "text_decision_input"
                                       or previous["payload"]["decision_id"] != identity):
                raise BoundaryError("text_menu_import", "decision_input_not_adjacent")
            decisions[identity] = (payload, sequence)
        elif kind == "text_menu_dispatch_attempt":
            identity = payload["decision_id"]
            if identity in dispatches:
                raise BoundaryError("text_menu_import", "duplicate_dispatch")
            dispatches[identity] = (payload, sequence)
        elif kind in OUTCOME_KINDS:
            identity = payload["decision_id"]
            if identity in outcomes:
                raise BoundaryError("text_menu_import", "duplicate_outcome")
            outcomes[identity] = (kind, payload, sequence)
        elif kind == "text_menu_result_rejected":
            diagnostics.append({"sequence": sequence, "decision_id": payload["decision_id"],
                                "reason": "result_rejected"})
        previous = event
    rows = []
    for ordinal, (identity, (decision_payload, decision_seq)) in enumerate(decisions.items()):
        if identity not in inputs:
            raise BoundaryError("text_menu_import", "text_decision_input_required")
        snapshot, input_seq = inputs[identity]
        if (snapshot.get("schema") != SNAPSHOT_SCHEMA
                or snapshot.get("input_profile") != INPUT_PROFILE):
            raise BoundaryError("text_menu_import", "text_profile_required")
        public = project_text_menu_snapshot(snapshot)
        decision = decision_payload["decision"]
        selected_index = decision["selected_index"]
        resolved = decision_payload["resolved_bound_action_id"]
        if (decision["snapshot_id"] != snapshot["snapshot_id"]
                or decision["candidate_digest"] != public.candidate_digest
                or decision["candidate_count"] != len(public.action_ids)
                or (selected_index is None and resolved is not None)
                or (selected_index is not None
                    and (public.action_ids[selected_index] != resolved))):
            raise BoundaryError("text_menu_import", "decision_input_binding_mismatch")
        if selected_index is None or identity not in dispatches:
            diagnostics.append({"sequence": decision_seq, "decision_id": identity,
                                "reason": "abstained_or_not_dispatched"})
            continue
        dispatch, dispatch_seq = dispatches[identity]
        selected = public.action_ids[selected_index]
        chosen = snapshot["menu_actions"]["actions"][selected_index]
        if (dispatch_seq <= decision_seq or dispatch["action_id"] != selected
                or dispatch["effect_domain"] != chosen["effect_domain"]):
            raise BoundaryError("text_menu_import", "dispatch_binding_mismatch")
        if identity not in outcomes:
            diagnostics.append({"sequence": dispatch_seq, "decision_id": identity,
                                "reason": "dispatch_without_correlated_result"})
            continue
        kind, outcome, result_seq = outcomes[identity]
        result = outcome["result"]
        status_domain = {"menu_navigation": ("applied", "text_menu"),
                         "text_native_delivery": ("applied", "native_input"),
                         "text_native_unknown": ("unknown", "native_input"),
                         "text_menu_not_applied": ("not_applied", None)}
        expected_status, expected_domain = status_domain[kind]
        if (result_seq <= dispatch_seq or result["status"] != expected_status
                or expected_domain is not None and result["effect_domain"] != expected_domain
                or (kind == "menu_navigation" and outcome["action_id"] != selected)
                or result.get("action") != chosen):
            raise BoundaryError("text_menu_import", "outcome_binding_mismatch")
        request_id = f"request-{run_id}-{identity}"
        if result["request_id"] != request_id:
            raise BoundaryError("text_menu_import", "request_id_mismatch")
        rows.append({
            "schema": SOURCE_SCHEMA, "record_id": semantic_hash([content_id, identity]),
            "run_id": run_id, "step_index": ordinal, "origin": "agent",
            "source_ref": (f"agent-run://{content_id}/events.jsonl"
                           f"#input={input_seq}&outcome={result_seq}"),
            "snapshot": snapshot,
            "request": {"request_id": request_id,
                        "expected_snapshot_id": snapshot["snapshot_id"],
                        "bound_action_id": selected, "input_profile": INPUT_PROFILE},
            "selected_action_id": selected, "result": result,
        })
    return tuple(rows), {"schema": "stpd/text-menu-agent-import-report-v1",
                         "run_id": run_id, "content_id": content_id,
                         "event_count": len(events), "rows": len(rows),
                         "diagnostics": diagnostics,
                         "outcomes": dict(Counter(row["result"]["status"] for row in rows))}


def publish_verified_text_menu_run(
    store: ArtifactStore, directory: Path, producer: Producer, *, admit_agent: bool = False,
) -> tuple[Manifest, Manifest | None, dict[str, Any]]:
    """Return immutable evidence, optional trace source, and accounting."""
    if not admit_agent:
        raise BoundaryError("text_menu_import", "agent_admission_opt_in_required")
    content, verified = _verified_files(directory)
    policy = json.loads(content["policy-manifest.json"])
    if policy.get("representation", {}).get("input_schema") != SNAPSHOT_SCHEMA:
        raise BoundaryError("text_menu_import", "text_profile_required")
    rows, report = _trace_rows(_events(content["events.jsonl"], verified.event_count),
                               verified.run_id, verified.content_id)
    packed = _archive(content)
    if len(packed) > MAX_ARCHIVE_BYTES:
        raise BoundaryError("text_menu_import", "agent_evidence_size_limit")
    payload = store.put_payload("archive", io.BytesIO(packed), "application/gzip")
    evidence = Manifest("evidence", producer, (), (payload,), FrozenObject.of({
        "schema": EVIDENCE_SCHEMA, "content_id": verified.content_id,
        "run_id": verified.run_id, "event_count": verified.event_count,
        "origin": "agent", "verification": "typed_pass",
    }))
    store.publish(evidence)
    source = (publish_text_menu_source(store, rows, producer, admit_agent=True,
                                       verified_evidence_id=evidence.artifact_id)
              if rows else None)
    return evidence, source, report
