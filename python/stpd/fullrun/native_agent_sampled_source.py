"""Typed native AgentRun originals to known-prefix student sample reexpression.

The public Evidence verifier owns integrity and protocol grammar. This adapter
indexes its original records for one closed research relation and sparse N;
it never creates native operands, a Runtime ledger or research use permission.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

import sts2_platform_evidence as evidence_owner

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import (
    BoundaryError,
    FrozenObject,
    decode_json,
    digest,
    json_bytes,
    object_fields,
)
from spireagent.storage.archives import _extract, archive_bundle
from spireagent.storage.blobs import MAX_BLOB_BYTES
from spireagent.storage.store import ArtifactStore

from ..canonical import semantic_hash
from ..native_agent_sampled_source_spec import (
    ADMISSION_SCHEMA,
    FIXTURE_COHORT,
    MAX_EVENTS,
    MAX_FILE_BYTES,
    MAX_KNOWN_SAMPLES,
    MAX_ORIGINAL_BYTES,
    MAX_ORIGINAL_FILES,
    MAX_RAW_REFERENCES,
    MAX_SOURCE_BYTES,
    PARTITION_SCHEMA,
    PROFILE,
    RAW_SCHEMA,
    RELATION_SPEC,
    RUN_IDENTITY_SCHEMA,
    SOURCE_KIND,
    SOURCE_SCHEMA,
    VALIDATION_SCHEMA,
    checked_relation,
    checked_specs,
    qualification,
    relation_body,
    relation_specs,
)
from ..native_sampled_carry_spec import (
    HISTORY_MODE,
    INPUT_SPEC,
    sample_eligible,
)
from ..policy.native_task import observe_ready_summary
from .native_structured_sequences import NativeUnit, native_advance, qualify_native
from .structured_sequences import StructuredDataset, StructuredRun, StructuredStep

EVIDENCE_TYPE = "policy-runtime-agent-session-run"
EVIDENCE_FILES = (
    "__init__.py",
    "core.py",
    "agent_run_evidence.py",
    "agent_session_run_evidence.py",
    "source_session_bundle.py",
)
PROJECTION_ROOT = Path(__file__).resolve().parents[2]
PROJECTION_FILES = (
    "spireagent/__init__.py",
    "spireagent/artifact_contracts.py",
    "spireagent/encoding.py",
    "spireagent/json_boundary.py",
    "spireagent/storage/__init__.py",
    "spireagent/storage/archives.py",
    "spireagent/storage/blobs.py",
    "spireagent/storage/store.py",
    "stpd/__init__.py",
    "stpd/canonical.py",
    "stpd/contracts.py",
    "stpd/linear_q.py",
    "stpd/representation.py",
    "stpd/native_agent_sampled_source_spec.py",
    "stpd/native_sampled_carry_spec.py",
    "stpd/native_graph_spec.py",
    "stpd/fullrun/__init__.py",
    "stpd/fullrun/native_agent_sampled_source.py",
    "stpd/fullrun/native_structured_inputs.py",
    "stpd/fullrun/native_structured_sequences.py",
    "stpd/fullrun/contracts.py",
    "stpd/fullrun/representation.py",
    "stpd/fullrun/semantic_projection.py",
    "stpd/fullrun/structured_inputs.py",
    "stpd/fullrun/structured_sequences.py",
    "stpd/fullrun/structured_tree.py",
    "stpd/fullrun/text_menu_inputs.py",
    "stpd/policy/__init__.py",
    "stpd/policy/native_task.py",
)
CONTEXT = ("session_id", "recovery_epoch")


def _fail(code: str) -> NoReturn:
    raise BoundaryError("native_agent_sampled_source", code)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file(directory: Path, name: str, maximum: int = MAX_FILE_BYTES) -> bytes:
    if not isinstance(name, str):
        _fail("unsafe_original_path")
    path = PurePosixPath(name)
    if (
        not isinstance(name, str)
        or path.is_absolute()
        or str(path) != name
        or len(path.parts) != 1
        or name in {"", ".", ".."}
        or "\\" in name
        or ":" in name
    ):
        _fail("unsafe_original_path")
    target = directory / name
    info = target.lstat()
    if target.is_symlink() or not stat.S_ISREG(info.st_mode) or not 0 <= info.st_size <= maximum:
        _fail("original_file_capacity_or_path")
    with target.open("rb") as handle:
        raw = handle.read(maximum + 1)
    if len(raw) != info.st_size or len(raw) > maximum:
        _fail("original_file_changed_or_capacity")
    return raw


def _inventory(directory: Path) -> list[dict[str, Any]]:
    if directory.is_symlink() or not directory.is_dir():
        _fail("original_directory_required")
    total = 0
    paths: list[Path] = []
    for path in directory.iterdir():
        if len(paths) >= MAX_ORIGINAL_FILES:
            _fail("original_inventory_capacity")
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode):
            _fail("original_flat_regular_inventory_required")
        total += info.st_size
        if total > MAX_ORIGINAL_BYTES or info.st_size > MAX_FILE_BYTES:
            _fail("original_inventory_capacity")
        paths.append(path)
    return [
        {"path": p.name, "bytes": p.stat().st_size, "sha256": _sha(_file(directory, p.name))}
        for p in sorted(paths)
    ]


def verifier_identity(root: Path = PROJECTION_ROOT) -> dict[str, Any]:
    owner = Path(evidence_owner.__file__).parent
    rows = [{"path": name, "sha256": _sha(_file(owner, name))} for name in EVIDENCE_FILES]
    root = root.resolve()
    projection = []
    for name in PROJECTION_FILES:
        path = root / name
        if path.is_symlink() or not path.is_file():
            _fail("projection_closure_missing")
        projection.append({"path": name, "sha256": _sha(path.read_bytes())})
    return {
        "schema": "stpd/native-agent-sampled-verifier-identity-v1",
        "type_id": EVIDENCE_TYPE,
        "package": "rsgcsg-sts2-platform-evidence",
        "version": importlib.metadata.version("rsgcsg-sts2-platform-evidence"),
        "evidence_code_sha256": semantic_hash(rows),
        "projection_code_sha256": semantic_hash(projection),
    }


def _verified(directory: Path) -> Any:
    _inventory(directory)  # Bound paths/bytes before the public verifier materializes its trace.
    if _file(directory, "events.jsonl").count(b"\n") > MAX_EVENTS:
        _fail("original_event_capacity")
    if any(
        getattr(evidence_owner, name, None) is None
        for name in (
            "AgentSessionRunEvidence",
            "verify_agent_session_run_evidence",
        )
    ):
        _fail("typed_native_agent_evidence_api_required")
    result = evidence_owner.verify_agent_session_run_evidence(directory)
    if (
        not result.passed
        or not isinstance(result.value, evidence_owner.AgentSessionRunEvidence)
        or result.descriptor.type_id != EVIDENCE_TYPE
        or result.descriptor.version != 1
    ):
        _fail("native_agent_bundle_verification_failed")
    return result.value


def _events(directory: Path) -> list[dict[str, Any]]:
    raw = _file(directory, "events.jsonl")
    if raw.count(b"\n") > MAX_EVENTS:
        _fail("original_event_capacity")
    return [decode_json(line) for line in raw.splitlines()]


def _producer(bundle: Any, cohort: str, relation: object) -> dict[str, Any]:
    checked_relation(relation, cohort)
    body = relation_body(relation, cohort)
    agent = _plain(bundle.agent_manifest)
    input_ = agent["input"]
    if (
        input_["profile"] != "native-logical-v1"
        or input_["history_mode"] != HISTORY_MODE
        or json_bytes(input_["input_spec"]) != json_bytes(body["producer_input_spec"])
        or input_["projection"] != body["producer_wire_projection"]
        or input_["consumption_mode"] != "once_per_occurrence"
        or input_["state_format_version"] != body["producer_state_format"]
        or input_["state_recovery"]["mode"] != "none"
    ):
        _fail("producer_input_spec_relation")
    definition = body.get("producer_definition")
    if definition is not None and (
        not _same(agent["adapter"], definition["adapter"])
        or agent["artifact"]["id"] != definition["artifact_id"]
        or agent["artifact"]["sha256"] != definition["artifact_sha256"]
        or agent["agent"]
        != {
            "id": definition["agent_spec"]["id"],
            "version": definition["agent_spec"]["version"],
            "provider": "stpd",
            "architecture": "explicit_native_public_program_teacher",
        }
    ):
        _fail("fixed_teacher_producer_identity")
    return {
        "agent_manifest": agent,
        "agent_manifest_sha256": bundle.manifest["agent_manifest_sha256"],
        "adapter_attestation": decode_json(
            _file(Path(bundle.directory), "adapter-attestation.json")
        ),
        "original_run_manifest": _plain(bundle.manifest),
        "source_kind": SOURCE_KIND,
        "cohort": cohort,
        "producer_student_relation": relation,
        "producer_relation_body": body,
        "original_git_revision": None,
        "origin_claim": (
            "explicit_synthetic_conformance_not_production_teacher_or_real_native"
            if cohort == FIXTURE_COHORT
            else "declared_native_machine_teacher_origin_not_established_by_this_verifier"
        ),
    }


def _bytes(store: ArtifactStore, manifest: Manifest, role: str, maximum: int) -> bytes:
    payload = manifest.payload(role)
    if not 0 < payload.size <= maximum:
        _fail("artifact_payload_capacity")
    result = bytearray()
    checksum = hashlib.sha256()
    for chunk in store.read_payload(payload):
        if len(result) + len(chunk) > payload.size or len(result) + len(chunk) > maximum:
            _fail("artifact_payload_integrity")
        checksum.update(chunk)
        result.extend(chunk)
    if len(result) != payload.size or checksum.hexdigest() != payload.sha256:
        _fail("artifact_payload_integrity")
    return bytes(result)


def _context(payload: dict[str, Any]) -> tuple[str, int]:
    return payload["session_id"], payload["recovery_epoch"]


def _same(a: object, b: object) -> bool:
    return json_bytes(a) == json_bytes(b)


def _unique(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return rows[0] if len(rows) == 1 else None


def _join_index(
    events: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[int, dict[str, Any]]]:
    """Index the verified original duplex, without reimplementing its grammar."""
    kinds: dict[str, list[dict[str, Any]]] = {}
    parents: dict[int, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    for event in events:
        kind = event["kind"]
        kinds.setdefault(kind, []).append(event)
        if kind == "agent_sample_next_requested":
            current = {"requested": event, "completed": None, "directive": None}
        elif kind == "agent_sample_query_offered" and current is not None:
            parents[event["sequence"]] = current
        elif kind == "agent_sample_next_completed" and current is not None:
            current["completed"] = event
        elif kind == "agent_directive" and current is not None:
            current["directive"] = event
            current = None
    return kinds, parents


def _known_join(
    offer: dict[str, Any], kinds: dict[str, list[dict[str, Any]]], parent: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, str]:
    acquisition = offer["payload"]["acquisition_id"]
    stored = _unique(
        [
            r
            for r in kinds.get("agent_sample_input_stored", [])
            if r["payload"]["metadata"]["acquisition_id"] == acquisition
            and r["payload"]["disposition"] == "consume_proposed"
        ]
    )
    if stored is None:
        discarded = any(
            r["payload"]["acquisition_id"] == acquisition
            and r["payload"]["reason"] == "readiness_check"
            for r in kinds.get("agent_sample_query_discarded", [])
        )
        if discarded and parent is not None and parent["completed"] and parent["directive"]:
            return None, "discarded_readiness_no_sample"
        return None, "offered_without_known_consumption_tail"
    proposal = stored["payload"]["proposal"]
    report = proposal["report"]
    consumed = _unique(
        [
            r
            for r in kinds.get("agent_consumed", [])
            if r["payload"]["report"]["acquisition_id"] == acquisition
        ]
    )
    if consumed is None or not _same(consumed["payload"]["report"], report):
        return None, "consume_proposed_without_ACK_tail"
    ack = consumed["payload"]["acknowledgement"]
    offered_ack = _unique(
        [
            r
            for r in kinds.get("agent_sample_consume_ack_offered", [])
            if r["payload"]["request_id"] == proposal["request_id"]
            and _same(r["payload"]["acknowledgement"], ack)
        ]
    )
    if offered_ack is None:
        return None, "ACK_without_original_write_offer_tail"
    if parent is None or parent["completed"] is None or parent["directive"] is None:
        return None, "ACK_without_known_completedNext_directive_tail"
    completed, directive, requested = (
        parent["completed"],
        parent["directive"],
        parent["requested"],
    )
    context = _context(offer["payload"])
    if (
        any(
            _context(r["payload"]) != context
            for r in (stored, consumed, offered_ack, requested, completed, directive)
        )
        or _context(proposal) != context
        or completed["payload"]["request_id"] != requested["payload"]["request_id"]
        or not _same(completed["payload"]["output"], directive["payload"]["output"])
    ):
        return None, "original_completedNext_context_mismatch_tail"
    output = directive["payload"]["output"]
    if (
        any(
            output[key] != report[key]
            for key in ("continuity_token", "consumption_id", "state_version")
        )
        or stored["payload"]["metadata"]["continuity_token"] != report["continuity_token"]
        or not _same(
            consumed["payload"]["witness"]["capture"], stored["payload"]["metadata"]["capture"]
        )
    ):
        return None, "original_sample_watermark_mismatch_tail"
    return {
        "offer": offer,
        "stored": stored,
        "consumed": consumed,
        "ack_offered": offered_ack,
        "requested": requested,
        "completed": completed,
        "directive": directive,
    }, "known_sample"


def _target(
    join: dict[str, Any],
    actions: list[dict[str, Any]],
    kinds: dict[str, list[dict[str, Any]]],
    terminal: bool,
) -> tuple[str | None, str, dict[str, Any], int | None]:
    directive_event = join["directive"]
    directive = directive_event["payload"]["output"]["directive"]
    acquisition = join["offer"]["payload"]["acquisition_id"]
    evidence: dict[str, Any] = {
        "directive_sequence": directive_event["sequence"],
        "original_directive": directive,
    }
    if terminal:
        return None, "ready_summary_no_N", evidence, None
    if directive["type"] != "act" or directive["selection"]["kind"] != "handle":
        return None, "no_original_handle_Act_target", evidence, None
    selected = directive["selection"]["action_id"]
    members = [a for a in actions if a["action_id"] == selected]
    attempts = [
        e
        for e in kinds.get("native_submission_requested", [])
        if e["payload"]["basis_acquisition_id"] == acquisition
        and _context(e["payload"]) == _context(directive_event["payload"])
    ]
    submission = _unique(attempts)
    if len(members) != 1 or submission is None:
        return (
            None,
            "handle_without_unique_original_submission",
            evidence,
            directive_event["sequence"],
        )
    attempt = submission["payload"]
    witness = join["consumed"]["payload"]["witness"]
    if (
        directive["basis_acquisition_id"] != acquisition
        or attempt["action_id"] != selected
        or attempt["snapshot_id"] != witness["snapshot_id"]
        or attempt["catalog_digest"] != witness["catalog_digest"]
        or attempt["runtime_instance_id"] != witness["capture"]["session"]["runtime_instance_id"]
    ):
        _fail("original_handle_submission_binding")
    outcomes = [
        e
        for e in kinds.get("native_result", [])
        if e["payload"]["result"]["request_id"] == attempt["request_id"]
    ]
    outcomes.extend(
        e
        for e in kinds.get("native_request_reconciled", [])
        if e["payload"]["result"] is not None
        and e["payload"]["original"]["request_id"] == attempt["request_id"]
    )
    outcome = _unique(outcomes)
    evidence.update(
        submission_sequence=submission["sequence"],
        original_submission=attempt,
        result_sequence=None if outcome is None else outcome["sequence"],
        original_result=None if outcome is None else outcome["payload"]["result"],
    )
    if outcome is None:
        not_started = any(
            e["payload"]["request_id"] == attempt["request_id"]
            for e in kinds.get("native_submission_not_started", [])
        )
        return (
            None,
            "original_submission_not_started"
            if not_started
            else "original_delivery_outstanding_tail",
            evidence,
            None if not_started else submission["sequence"],
        )
    result = outcome["payload"]["result"]
    if result["action"] is not None and not _same(result["action"], members[0]):
        _fail("original_result_full_member_binding")
    if result["delivery"] in {"unknown", "partially_delivered"}:
        return None, "original_delivery_uncertain_tail", evidence, outcome["sequence"]
    if (
        result["delivery"] != "delivered"
        or result["execution"] == "native_rejected"
        or result["action"] is None
    ):
        return None, "original_delivery_or_execution_not_N", evidence, None
    return selected, "eligible_original_delivered_handle_N", evidence, None


def _projection(
    bundle: Any, raw_id: str, cohort: str, relation: object = RELATION_SPEC
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(bundle, evidence_owner.AgentSessionRunEvidence):
        _fail("typed_verified_native_agent_bundle_required")
    producer = _producer(bundle, cohort, relation)
    projection_spec, target_spec = relation_specs(relation, cohort)
    directory = Path(bundle.directory)
    events = _events(directory)
    kinds, parents = _join_index(events)
    offers = kinds.get("agent_sample_query_offered", [])
    registrations = {
        e["payload"]["witness"]["acquisition_id"]: e
        for e in kinds.get("native_acquisition_registered", [])
    }
    runtime_ids = {
        registrations[o["payload"]["acquisition_id"]]["payload"]["witness"]["capture"]["session"][
            "runtime_instance_id"
        ]
        for o in offers
    }
    runtime_ids.update(
        e["payload"]["environment"]["runtime_instance_id"]
        for e in kinds.get("native_session_attached", [])
    )
    if len(runtime_ids) != 1:
        _fail("one_original_runtime_required")
    runtime = next(iter(runtime_ids))
    group = "protocol-runtime:" + runtime
    related = [
        group,
        "native-agent-content:" + bundle.content_id,
        "native-agent-run:" + bundle.run_id,
    ]
    run_id = "native-agent:" + bundle.content_id + ":" + bundle.run_id
    steps: list[dict[str, Any]] = []
    index: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    previous: NativeUnit | None = None
    first_context: tuple[str, int] | None = None
    token: str | None = None
    environment: object = None
    censored: dict[str, Any] | None = None
    cutoff: int | None = None
    accepted_end: int | None = None

    for offer in offers:
        acquisition = offer["payload"]["acquisition_id"]
        witness = registrations[acquisition]["payload"]["witness"]
        context = _context(offer["payload"])
        join, reason = _known_join(offer, kinds, parents.get(offer["sequence"]))
        row = {
            "raw_id": raw_id,
            "run_id": run_id,
            "source_group": group,
            "related_keys": related,
            "capture_id": witness["capture"]["capture_id"],
            "record_ref": "events.jsonl:" + str(offer["sequence"]),
            "occurrence_id": semantic_hash([bundle.content_id, offer["sequence"], acquisition]),
            "admitted": False,
            "N_eligible": False,
            "evidence": {
                "bundle_content_id": bundle.content_id,
                "agent_run_id": bundle.run_id,
                "runtime_instance_id": runtime,
                "acquisition_id": acquisition,
                "session_id": context[0],
                "recovery_epoch": context[1],
                "offered_sequence": offer["sequence"],
                "witness": witness,
                "source_kind": SOURCE_KIND,
                "cohort": cohort,
            },
        }
        if cutoff is not None and offer["sequence"] > cutoff:
            reason = "censored_after_" + str(
                censored["reason"] if censored else "uncertain_boundary"
            )
            join = None
        if steps and accepted_end is not None and cutoff is None:
            barriers = [
                e
                for e in events
                if accepted_end < e["sequence"] < offer["sequence"]
                and (
                    e["kind"]
                    in {
                        "agent_sample_segment_ended",
                        "handoff_to_human",
                        "fail_closed",
                        "runtime_tainted",
                        "stopped",
                    }
                    or e["kind"] == "mode_changed"
                    and e["payload"]["mode"] == "human"
                )
            ]
            if barriers:
                censored = {"sequence": barriers[0]["sequence"], "reason": "original_segment_ended"}
                cutoff = barriers[0]["sequence"]
                reason, join = "censored_after_original_segment_ended", None
        if join is not None:
            metadata = join["stored"]["payload"]["metadata"]
            report = join["consumed"]["payload"]["report"]
            row["evidence"].update(
                metadata=metadata,
                proposal=join["stored"]["payload"]["proposal"],
                consumed_sequence=join["consumed"]["sequence"],
                ack_offered_sequence=join["ack_offered"]["sequence"],
                next_requested_sequence=join["requested"]["sequence"],
                next_completed_sequence=join["completed"]["sequence"],
                directive_sequence=join["directive"]["sequence"],
            )
            if not steps and (
                report["state_version"] != 1
                or report["previous_consumption_id"] is not None
                or context[1] != 0
                or not any(
                    e["payload"]["acquisition_id"] == acquisition
                    and e["payload"]["continuity_token"] == report["continuity_token"]
                    for e in kinds.get("agent_sample_segment_started", [])
                )
            ):
                reason, join = "missing_original_first_known_prefix", None
            elif steps and (context != first_context or report["continuity_token"] != token):
                reason, join = "original_context_discontinuity_tail", None
            if join is not None:
                observation_raw = _file(directory, metadata["observation"]["path"])
                catalog_raw = _file(directory, metadata["catalog"]["path"])
                if (
                    _sha(observation_raw) != metadata["observation"]["sha256"]
                    or _sha(catalog_raw) != metadata["catalog"]["sha256"]
                ):
                    _fail("original_sample_changed")
                observation, actions = decode_json(observation_raw), decode_json(catalog_raw)
                frame, unit = qualify_native(observation, actions)
                current_environment = observation["session"]
                if steps and not _same(current_environment, environment):
                    reason, join = "original_environment_discontinuity_tail", None
                if join is not None:
                    advance = native_advance(previous, unit)
                    if join is not None:
                        if not sample_eligible(observation, actions):
                            reason, join = "ineligible_original_consumed_sample_tail", None
                        elif not advance:
                            reason, join = "unchanged_original_unit_no_student_sample", None
                        elif (
                            report["advanced"] is not True
                            or report["state_version"] != len(steps) + 1
                        ):
                            reason, join = "original_sample_advance_watermark_tail", None
                        else:
                            terminal = observe_ready_summary(observation).agent_task_complete
                            label, target_reason, target_evidence, target_cut = _target(
                                join, actions, kinds, terminal
                            )
                            evidence = {
                                **row["evidence"],
                                "report": report,
                                "target": {"reason": target_reason, **target_evidence},
                            }
                            steps.append(
                                {
                                    "observation": observation,
                                    "catalog": actions,
                                    "chosen_action_id": label,
                                    "reset_before": not steps,
                                    "reset_reason": "sample_segment_start" if not steps else None,
                                    "evidence": evidence,
                                }
                            )
                            row.update(
                                admitted=True,
                                N_eligible=label is not None,
                                target_eligibility=target_reason,
                            )
                            previous, first_context, token, environment = (
                                unit,
                                context,
                                report["continuity_token"],
                                current_environment,
                            )
                            accepted_end = join["directive"]["sequence"]
                            if target_cut is not None:
                                cutoff = target_cut
                                censored = {"sequence": target_cut, "reason": target_reason}
                            if len(steps) > MAX_KNOWN_SAMPLES:
                                _fail("known_sample_capacity")
        if not row["admitted"]:
            row["target_eligibility"] = reason
            exclusions.append(
                {
                    "record_ref": row["record_ref"],
                    "reason": reason,
                    "original_offer": offer,
                    "original_witness": witness,
                }
            )
            if (
                reason
                not in {
                    "discarded_readiness_no_sample",
                    "unchanged_original_unit_no_student_sample",
                }
                and cutoff is None
            ):
                cutoff = offer["sequence"]
                censored = {"sequence": cutoff, "reason": reason}
        index.append(row)
    runs = []
    if steps:
        runs.append(
            {
                "run_id": run_id,
                "source_group": group,
                "split": "train",
                "identity": {
                    "schema": RUN_IDENTITY_SCHEMA,
                    "raw_id": raw_id,
                    "bundle_content_id": bundle.content_id,
                    "agent_run_id": bundle.run_id,
                    "runtime_instance_id": runtime,
                    "session_id": first_context[0] if first_context else None,
                    "recovery_epoch": first_context[1] if first_context else None,
                    "continuity_token": token,
                    "native_game_continuity_id": None,
                    "related_keys": related,
                    "producer": producer,
                    "cohort": cohort,
                    "source_profile": PROFILE,
                    "known_prefix_start": steps[0]["evidence"]["offered_sequence"],
                    "known_prefix_end": accepted_end,
                    "censored_tail": censored,
                    "history_claim": (
                        "declared_student_reexpression_of_original_known_machine_sample_prefix"
                    ),
                },
                "steps": steps,
            }
        )
    report = {
        "schema": ADMISSION_SCHEMA,
        "raw_id": raw_id,
        "bundle_content_id": bundle.content_id,
        "agent_run_id": bundle.run_id,
        "runtime_instance_id": runtime,
        "source_kind": SOURCE_KIND,
        "cohort": cohort,
        "source_profile": PROFILE,
        "producer_student_relation": relation,
        "producer": producer,
        "input_spec": INPUT_SPEC,
        "projection_spec": projection_spec,
        "target_spec": target_spec,
        "qualification": qualification(relation, cohort),
        "validation": {
            "schema": VALIDATION_SCHEMA,
            "method": "typed_original_samples_reverified_then_known_prefix_reexpressed",
            "producer_student_relation": relation,
            "student_input_spec": INPUT_SPEC,
        },
        "index": index,
        "exclusions": exclusions,
        "censored_tail": censored,
        "counts": {
            "original_offers": len(offers),
            "known_context_samples": len(steps),
            "eligible_unique_N": sum(s["chosen_action_id"] is not None for s in steps),
            "readiness_exclusions": sum(
                r["target_eligibility"]
                in {"discarded_readiness_no_sample", "unchanged_original_unit_no_student_sample"}
                for r in index
            ),
            "excluded_offers": sum(not r["admitted"] for r in index),
            "known_ready_summary_samples": sum(
                s["evidence"]["target"]["reason"] == "ready_summary_no_N" for s in steps
            ),
            "real_native_samples": 0 if cohort == FIXTURE_COHORT else None,
        },
        "native_origin_status": (
            "synthetic_conformance"
            if cohort == FIXTURE_COHORT
            else "not_established_by_this_verifier"
        ),
    }
    return runs, report


@dataclass(frozen=True)
class NativeAgentSampledRef:
    raw_id: str
    admission_id: str


@dataclass(frozen=True)
class VerifiedNativeAgentSampledSource:
    manifest: Manifest
    dataset: StructuredDataset
    split: str
    source_ids: tuple[str, ...]
    runs: frozenset[str]
    source_groups: frozenset[str]
    index: tuple[dict[str, Any], ...]


def publish_native_agent_sampled_raw(
    store: ArtifactStore,
    directory: Path,
    producer: Producer,
    *,
    cohort: str = FIXTURE_COHORT,
    relation: object = RELATION_SPEC,
) -> Manifest:
    if type(producer) is not Producer:
        _fail("typed_import_producer_required")
    bundle = _verified(directory)
    original = _producer(bundle, cohort, relation)
    inventory = _inventory(directory)
    raw = archive_bundle(directory, max_bytes=MAX_ORIGINAL_BYTES, max_files=MAX_ORIGINAL_FILES)
    if len(raw) > MAX_BLOB_BYTES:
        _fail("archive_transport_capacity")
    with tempfile.TemporaryDirectory() as name:
        check = Path(name)
        _extract(raw, check, max_bytes=MAX_ORIGINAL_BYTES, max_files=MAX_ORIGINAL_FILES)
        replay = _verified(check)
        if (
            replay.content_id != bundle.content_id
            or inventory != _inventory(check)
            or not _same(original, _producer(replay, cohort, relation))
        ):
            _fail("original_changed_during_archive")
    payload = store.put_payload("archive", io.BytesIO(raw), "application/gzip")
    manifest = Manifest(
        "evidence",
        producer,
        payloads=(payload,),
        parameters=FrozenObject.of(
            {
                "schema": RAW_SCHEMA,
                "source_profile": PROFILE,
                "source_kind": SOURCE_KIND,
                "cohort": cohort,
                "producer_student_relation": relation,
                "original": original,
                "bundle_content_id": bundle.content_id,
                "inventory": inventory,
            }
        ),
    )
    store.publish(manifest)
    return manifest


def _replay_raw(store: ArtifactStore, raw_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = store.get_manifest(digest(raw_id, "native_agent_sampled.raw_id"))
    info = object_fields(
        raw.parameters.value(),
        {
            "schema",
            "source_profile",
            "source_kind",
            "cohort",
            "producer_student_relation",
            "original",
            "bundle_content_id",
            "inventory",
        },
        "native_agent_sampled.raw",
    )
    if (
        raw.kind != "evidence"
        or raw.parents
        or len(raw.payloads) != 1
        or raw.payload("archive").media_type != "application/gzip"
        or info["schema"] != RAW_SCHEMA
        or info["source_profile"] != PROFILE
        or info["source_kind"] != SOURCE_KIND
    ):
        _fail("typed_native_agent_raw_required")
    checked_relation(info["producer_student_relation"], info["cohort"])
    archive = _bytes(store, raw, "archive", MAX_BLOB_BYTES)
    with tempfile.TemporaryDirectory() as name:
        directory = Path(name)
        _extract(archive, directory, max_bytes=MAX_ORIGINAL_BYTES, max_files=MAX_ORIGINAL_FILES)
        bundle = _verified(directory)
        if (
            info["inventory"] != _inventory(directory)
            or info["bundle_content_id"] != bundle.content_id
            or not _same(
                info["original"],
                _producer(bundle, info["cohort"], info["producer_student_relation"]),
            )
        ):
            _fail("original_archive_binding")
        runs, report = _projection(
            bundle, raw_id, info["cohort"], info["producer_student_relation"]
        )
    report.update(
        archive_sha256=_sha(archive),
        import_producer=raw.producer.to_dict(),
        verifier=verifier_identity(),
    )
    return runs, report


def publish_native_agent_sampled_admission(
    store: ArtifactStore,
    raw_id: str,
    producer: Producer,
) -> NativeAgentSampledRef:
    _, report = _replay_raw(store, raw_id)
    payload = store.put_payload("report", io.BytesIO(json_bytes(report)), "application/json")
    manifest = Manifest(
        "analysis",
        producer,
        (Parent("raw", raw_id),),
        (payload,),
        FrozenObject.of({"schema": ADMISSION_SCHEMA, "verifier": verifier_identity()}),
    )
    store.publish(manifest)
    return NativeAgentSampledRef(raw_id, manifest.artifact_id)


def _verify_ref(
    store: ArtifactStore, ref: NativeAgentSampledRef
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if type(ref) is not NativeAgentSampledRef:
        _fail("typed_native_agent_reference_required")
    manifest = store.get_manifest(digest(ref.admission_id, "native_agent_sampled.admission_id"))
    runs, report = _replay_raw(store, ref.raw_id)
    if (
        manifest.kind != "analysis"
        or manifest.parents != (Parent("raw", ref.raw_id),)
        or len(manifest.payloads) != 1
        or manifest.payload("report").media_type != "application/json"
        or manifest.parameters.value()
        != {"schema": ADMISSION_SCHEMA, "verifier": verifier_identity()}
        or _bytes(store, manifest, "report", MAX_BLOB_BYTES) != json_bytes(report)
    ):
        _fail("native_agent_admission_binding")
    return runs, report


def _teacher(relation: object, cohort: str) -> dict[str, Any]:
    return {
        "id": "native-agent-known-sample-student-reexpression",
        "version": "1.0.0",
        "parameters": {
            "producer_student_relation": checked_relation(relation, cohort),
            "cohort": cohort,
            "source_kind": SOURCE_KIND,
            "private_teacher_state_observed": False,
        },
    }


def parse_native_agent_sampled_dataset(raw: bytes) -> StructuredDataset:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SOURCE_BYTES:
        _fail("projected_source_capacity")
    source = object_fields(
        decode_json(raw),
        {
            "schema",
            "source_profile",
            "source_kind",
            "cohort",
            "producer_student_relation",
            "input_spec",
            "projection_spec",
            "target_spec",
            "teacher",
            "raw_refs",
            "runs",
        },
        "native_agent_sampled.source",
    )
    checked_relation(source["producer_student_relation"], source["cohort"])
    checked_specs(
        source["projection_spec"],
        source["target_spec"],
        source["producer_student_relation"],
        source["cohort"],
    )
    if (
        source["schema"] != SOURCE_SCHEMA
        or source["source_profile"] != PROFILE
        or source["source_kind"] != SOURCE_KIND
        or not _same(source["input_spec"], INPUT_SPEC)
        or not _same(
            source["teacher"], _teacher(source["producer_student_relation"], source["cohort"])
        )
        or not isinstance(source["raw_refs"], list)
        or not 0 < len(source["raw_refs"]) <= MAX_RAW_REFERENCES
        or not isinstance(source["runs"], list)
        or not 0 < len(source["runs"]) <= MAX_RAW_REFERENCES
    ):
        _fail("projected_source_identity")
    refs = [
        object_fields(r, {"raw_id", "admission_id"}, "native_agent_sampled.reference")
        for r in source["raw_refs"]
    ]
    raw_ids = [digest(r["raw_id"], "native_agent_sampled.raw_id") for r in refs]
    for ref in refs:
        digest(ref["admission_id"], "native_agent_sampled.admission_id")
    if len(set(raw_ids)) != len(raw_ids) or raw_ids != sorted(raw_ids):
        _fail("projected_raw_reference_order")
    result = []
    names = set()
    runtime_ids = set()
    for value in source["runs"]:
        run = object_fields(
            value,
            {"run_id", "source_group", "split", "identity", "steps"},
            "native_agent_sampled.run",
        )
        identity = run["identity"]
        if (
            not isinstance(identity, dict)
            or identity.get("schema") != RUN_IDENTITY_SCHEMA
            or identity.get("raw_id") not in raw_ids
            or run["split"] != "train"
            or not isinstance(run["run_id"], str)
            or run["run_id"] in names
            or identity.get("native_game_continuity_id") is not None
            or identity.get("cohort") != source["cohort"]
            or identity.get("source_profile") != PROFILE
            or not isinstance(run["steps"], list)
            or not 0 < len(run["steps"]) <= MAX_KNOWN_SAMPLES
        ):
            _fail("projected_run_identity_or_train_only")
        runtime = identity["runtime_instance_id"]
        if (
            run["source_group"] != "protocol-runtime:" + runtime
            or run["source_group"] not in identity["related_keys"]
        ):
            _fail("original_runtime_group_binding")
        steps = []
        previous = None
        for position, value in enumerate(run["steps"]):
            step = object_fields(
                value,
                {
                    "observation",
                    "catalog",
                    "chosen_action_id",
                    "reset_before",
                    "reset_reason",
                    "evidence",
                },
                "native_agent_sampled.step",
            )
            e = step["evidence"]
            if (
                not isinstance(e, dict)
                or e.get("bundle_content_id") != identity["bundle_content_id"]
                or e.get("agent_run_id") != identity["agent_run_id"]
                or e.get("runtime_instance_id") != runtime
                or e.get("session_id") != identity["session_id"]
                or e.get("recovery_epoch") != identity["recovery_epoch"]
                or type(step["reset_before"]) is not bool
                or step["reset_before"] != (position == 0)
                or step["reset_reason"] != ("sample_segment_start" if position == 0 else None)
            ):
                _fail("projected_original_context_reset_binding")
            frame, unit = qualify_native(step["observation"], step["catalog"])
            if (
                not native_advance(previous, unit)
                or not sample_eligible(step["observation"], step["catalog"])
                or step["observation"]["session"]["runtime_instance_id"] != runtime
                or e["report"]["state_version"] != position + 1
                or e["report"]["advanced"] is not True
                or e["report"]["continuity_token"] != identity["continuity_token"]
            ):
                _fail("projected_sample_advance_binding")
            label = step["chosen_action_id"]
            target = e["target"]
            if label is not None and (
                label not in frame.action_ids
                or target["reason"] != "eligible_original_delivered_handle_N"
                or observe_ready_summary(step["observation"]).agent_task_complete
            ):
                _fail("projected_N_binding")
            if label is not None:
                directive = target["original_directive"]
                submission, outcome = target["original_submission"], target["original_result"]
                if (
                    directive["type"] != "act"
                    or directive["selection"] != {"kind": "handle", "action_id": label}
                    or directive["basis_acquisition_id"] != e["acquisition_id"]
                    or submission["basis_acquisition_id"] != e["acquisition_id"]
                    or submission["action_id"] != label
                    or submission["request_id"] != outcome["request_id"]
                    or outcome["delivery"] != "delivered"
                    or outcome["execution"] == "native_rejected"
                    or sum(_same(outcome["action"], a) for a in step["catalog"]) != 1
                ):
                    _fail("projected_original_handle_N_binding")
            metadata = e["metadata"]
            if (
                _sha(json_bytes(step["catalog"])[:-1]) != metadata["catalog"]["sha256"]
                or metadata["capture"]["session"]["runtime_instance_id"] != runtime
            ):
                _fail("projected_original_catalog_binding")
            steps.append(
                StructuredStep(
                    position,
                    frame,
                    label,
                    position == 0,
                    True,
                    metadata["observation"]["sha256"],
                    metadata["capture"]["capture_id"],
                    False,
                    step["reset_reason"],
                )
            )
            previous = unit
        names.add(run["run_id"])
        runtime_ids.add(runtime)
        result.append(
            StructuredRun(
                run["run_id"], run["source_group"], "train", FrozenObject.of(identity), tuple(steps)
            )
        )
    if len(runtime_ids) != 1:
        _fail("one_original_runtime_required")
    return StructuredDataset(
        SOURCE_KIND,
        FrozenObject.of(source["teacher"]),
        tuple(result),
        _sha(raw),
        raw,
        FrozenObject.of(INPUT_SPEC),
    )


def _partition(
    store: ArtifactStore, refs: tuple[NativeAgentSampledRef, ...], split: str
) -> tuple[bytes, tuple[dict[str, Any], ...]]:
    if split != "train" or not 0 < len(refs) <= MAX_RAW_REFERENCES:
        _fail("train_only_partition_required")
    if len({r.raw_id for r in refs}) != len(refs):
        _fail("duplicate_raw_source")
    runs, index, contents = [], [], set()
    original_runtimes: set[str] = set()
    cohort = relation = None
    for ref in refs:
        original_runs, report = _verify_ref(store, ref)
        original_runtimes.add(report["runtime_instance_id"])
        if len(original_runtimes) != 1:
            _fail("one_original_runtime_required")
        if report["bundle_content_id"] in contents:
            _fail("duplicate_original_evidence_content")
        if cohort is not None and (
            cohort != report["cohort"] or not _same(relation, report["producer_student_relation"])
        ):
            _fail("same_producer_student_relation_required")
        cohort, relation = report["cohort"], report["producer_student_relation"]
        contents.add(report["bundle_content_id"])
        runs.extend(original_runs)
        index.extend(report["index"])
    if not runs or cohort is None or relation is None:
        _fail("no_known_original_prefix")
    projection_spec, target_spec = relation_specs(relation, cohort)
    source = {
        "schema": SOURCE_SCHEMA,
        "source_profile": PROFILE,
        "source_kind": SOURCE_KIND,
        "cohort": cohort,
        "producer_student_relation": relation,
        "input_spec": INPUT_SPEC,
        "projection_spec": projection_spec,
        "target_spec": target_spec,
        "teacher": _teacher(relation, cohort),
        "raw_refs": [ref.__dict__ for ref in refs],
        "runs": runs,
    }
    encoded = json_bytes(source)
    parse_native_agent_sampled_dataset(encoded)
    return encoded, tuple(index)


def publish_native_agent_sampled_partition(
    store: ArtifactStore,
    refs: tuple[NativeAgentSampledRef, ...],
    split: str,
    producer: Producer,
) -> VerifiedNativeAgentSampledSource:
    if (
        not isinstance(refs, tuple)
        or not refs
        or any(type(r) is not NativeAgentSampledRef for r in refs)
    ):
        _fail("typed_native_agent_reference_required")
    selected = tuple(sorted(refs, key=lambda r: r.raw_id))
    raw, _ = _partition(store, selected, split)
    dataset = parse_native_agent_sampled_dataset(raw)
    value = decode_json(raw)
    payload = store.put_payload("source", io.BytesIO(raw), "application/json")
    manifest = Manifest(
        "dataset",
        producer,
        tuple(Parent(f"admission-{i:04d}", r.admission_id) for i, r in enumerate(selected)),
        (payload,),
        FrozenObject.of(
            {
                "schema": SOURCE_SCHEMA,
                "partition_schema": PARTITION_SCHEMA,
                "split": split,
                "source_profile": PROFILE,
                "source_kind": SOURCE_KIND,
                "cohort": value["cohort"],
                "source_sha256": dataset.source_sha256,
                "input_spec": INPUT_SPEC,
                "producer_student_relation": value["producer_student_relation"],
                "projection_spec": value["projection_spec"],
                "target_spec": value["target_spec"],
                "qualification": qualification(value["producer_student_relation"], value["cohort"]),
                "raw_refs": [r.__dict__ for r in selected],
            }
        ),
    )
    store.publish(manifest)
    return verify_native_agent_sampled_partition(store, manifest.artifact_id)


def verify_native_agent_sampled_partition(
    store: ArtifactStore, source_id: str
) -> VerifiedNativeAgentSampledSource:
    manifest = store.get_manifest(digest(source_id, "native_agent_sampled.partition_id"))
    info = object_fields(
        manifest.parameters.value(),
        {
            "schema",
            "partition_schema",
            "split",
            "source_profile",
            "source_kind",
            "cohort",
            "source_sha256",
            "input_spec",
            "producer_student_relation",
            "projection_spec",
            "target_spec",
            "qualification",
            "raw_refs",
        },
        "native_agent_sampled.partition",
    )
    checked_relation(info["producer_student_relation"], info["cohort"])
    checked_specs(
        info["projection_spec"],
        info["target_spec"],
        info["producer_student_relation"],
        info["cohort"],
    )
    if (
        manifest.kind != "dataset"
        or len(manifest.payloads) != 1
        or manifest.payload("source").media_type != "application/json"
        or info["schema"] != SOURCE_SCHEMA
        or info["partition_schema"] != PARTITION_SCHEMA
        or info["source_profile"] != PROFILE
        or info["source_kind"] != SOURCE_KIND
        or info["split"] != "train"
        or not _same(info["input_spec"], INPUT_SPEC)
        or info["qualification"] != qualification(info["producer_student_relation"], info["cohort"])
        or not isinstance(info["raw_refs"], list)
        or not 0 < len(info["raw_refs"]) <= MAX_RAW_REFERENCES
    ):
        _fail("typed_native_agent_partition_required")
    refs = tuple(
        NativeAgentSampledRef(
            **object_fields(r, {"raw_id", "admission_id"}, "native_agent_sampled.reference")
        )
        for r in info["raw_refs"]
    )
    if refs != tuple(sorted(refs, key=lambda r: r.raw_id)) or manifest.parents != tuple(
        Parent(f"admission-{i:04d}", r.admission_id) for i, r in enumerate(refs)
    ):
        _fail("partition_admission_parent_binding")
    expected, index = _partition(store, refs, info["split"])
    raw = _bytes(store, manifest, "source", MAX_SOURCE_BYTES)
    if raw != expected or _sha(raw) != info["source_sha256"]:
        _fail("projected_original_join_mismatch")
    dataset = parse_native_agent_sampled_dataset(raw)
    return VerifiedNativeAgentSampledSource(
        manifest,
        dataset,
        "train",
        tuple(r.raw_id for r in refs),
        frozenset(r["run_id"] for r in index),
        frozenset(r["source_group"] for r in index),
        index,
    )
