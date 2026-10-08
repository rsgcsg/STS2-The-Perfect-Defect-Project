"""Source3 originals to ordered native inputs and sparse N supervision.

Evidence owns wire verification. This adapter owns a versioned research view and
replays original references at their original cuts. It never fills a missing
basis, changes a revision, promotes declared Human origin, or grants data use.
"""

from __future__ import annotations

import hashlib
import io
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
from spireagent.storage.archives import MAX_BYTES, _extract, archive_bundle
from spireagent.storage.store import ArtifactStore

from ..canonical import semantic_hash
from ..ordered_source_spec import (
    ADMISSION_SCHEMA,
    COHORTS,
    DEFAULT_VIEW,
    MAX_STEPS_PER_RUN,
    PARTITION_SCHEMA,
    PRETRAIN_VIEW,
    RAW_SCHEMA,
    SOURCE_SCHEMA,
    checked_view,
    view_qualification,
    view_specs,
)
from ..policy.native_task import observe_ready_summary
from .native_structured_inputs import INPUT_SPEC
from .native_structured_sequences import NativeUnit, native_advance, qualify_native
from .structured_sequences import (
    MAX_RUNS,
    MAX_SOURCE_BYTES,
    StructuredDataset,
    StructuredRun,
    StructuredStep,
    _text,
)

MAX_REPORT_BYTES = 64 * 1024 * 1024
SOURCE_PROFILE = "native-logical-source-v3"
EVIDENCE_FILES = (
    "__init__.py",
    "core.py",
    "source_session_bundle.py",
    "source_session_bundle_v2.py",
    "source_session_bundle_v3.py",
    "source_session_order.py",
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
    "stpd/ordered_source_spec.py",
    "stpd/native_graph_spec.py",
    "stpd/fullrun/__init__.py",
    "stpd/fullrun/native_structured_inputs.py",
    "stpd/fullrun/native_structured_sequences.py",
    "stpd/fullrun/ordered_source.py",
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


def _fail(code: str) -> NoReturn:
    raise BoundaryError("source3_ordered", code)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _api() -> Any:
    # Fixed owner API only. An installed Source2 reader cannot silently validate
    # Source3, and a fixture dictionary cannot act as a verification receipt.
    if any(
        getattr(evidence_owner, name, None) is None
        for name in (
            "SourceSessionBundleV3",
            "SourceSessionBundleV3Verifier",
            "verify_source_session_bundle_v3",
        )
    ):
        _fail("source3_evidence_api_required")
    return evidence_owner


def verifier_identity(root: Path = PROJECTION_ROOT) -> dict[str, Any]:
    owner = Path(_api().__file__).parent
    rows = []
    for name in EVIDENCE_FILES:
        path = owner / name
        if path.is_symlink() or not path.is_file():
            _fail("source3_verifier_closure_missing")
        rows.append({"path": name, "sha256": _sha(path.read_bytes())})
    projection = []
    root = root.resolve()
    for name in PROJECTION_FILES:
        path = root / name
        if path.is_symlink() or not path.is_file() or any(
            parent.is_symlink() for parent in path.parents if parent != root
        ):
            _fail("source3_projection_closure_missing")
        projection.append({"path": name, "sha256": _sha(path.read_bytes())})
    return {
        "schema": "stpd/source3-verifier-identity-v1",
        "type_id": "source-session-bundle-v3",
        "source_sha256": semantic_hash(rows),
        "projection_code_sha256": semantic_hash(projection),
        "supported_view_specs": [{"projection_spec": view_specs(view)[0],
                                  "target_spec": view_specs(view)[1]}
                                 for view in (DEFAULT_VIEW, PRETRAIN_VIEW)],
    }


def _verified(directory: Path) -> Any:
    api = _api()
    result = api.verify_source_session_bundle_v3(directory)
    if not result.passed or not isinstance(result.value, api.SourceSessionBundleV3):
        _fail("source3_bundle_verification_failed")
    if result.descriptor.type_id != "source-session-bundle-v3" or result.descriptor.version != 3:
        _fail("source3_typed_verifier_required")
    return result.value


def _bytes(store: ArtifactStore, manifest: Manifest, role: str, maximum: int) -> bytes:
    payload = manifest.payload(role)
    if not 0 < payload.size <= maximum:
        _fail("payload_capacity")
    parts, size, checksum = [], 0, hashlib.sha256()
    for chunk in store.read_payload(payload):
        size += len(chunk)
        if size > payload.size or size > maximum:
            _fail("payload_integrity")
        checksum.update(chunk)
        parts.append(chunk)
    if size != payload.size or checksum.hexdigest() != payload.sha256:
        _fail("payload_integrity")
    return b"".join(parts)


def _blob(bundle: Any, reference: Mapping[str, Any], hash_key: str) -> bytes:
    name = reference["payload_ref"]
    path = PurePosixPath(name)
    if (
        not isinstance(name, str)
        or path.is_absolute()
        or str(path) != name
        or any(part in {"", ".", ".."} for part in name.split("/"))
    ):
        _fail("original_blob_reference_invalid")
    target = Path(bundle.directory).resolve() / "raw" / name
    if target.is_symlink() or any(part.is_symlink() for part in target.parents):
        _fail("original_blob_reference_invalid")
    raw = target.read_bytes()
    if len(raw) != reference["byte_count"] or _sha(raw) != reference[hash_key]:
        _fail("original_blob_changed")
    return raw


def _run_identity(bundle: Any, epoch: Mapping[str, Any]) -> tuple[str, str, list[str]]:
    runtime = epoch["context"]["environment"]["runtime_instance_id"]
    continuity = epoch["context"]["game_continuity_id"]
    # A setup epoch has no original game run. Keep its explicit epoch identity;
    # never invent a launch or count it in whole-game evaluation.
    original = "game:" + continuity if continuity is not None else "epoch:" + epoch["epoch_id"]
    group = "source3:" + runtime + ":" + original
    # Each original attachment epoch has its own replay/reset boundary. Separate
    # view run locators must still share the original game's exposure group.
    name = ("source3:" + bundle.manifest["timeline_id"] + ":" + original
            + ":epoch:" + epoch["epoch_id"])
    return name, group, [group, "source3-timeline:" + bundle.manifest["timeline_id"],
                         "protocol-runtime:" + runtime]


def _project_epoch(
    bundle: Any,
    epoch: Mapping[str, Any],
    raw_id: str,
    original_publications: list[Mapping[str, Any]],
    original_inputs: list[Mapping[str, Any]],
    original_boundaries: list[Mapping[str, Any]],
    cohort: str,
    view: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]], int]:
    index: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    eligible = 0
    result = None
    epoch_id = epoch["epoch_id"]
    run_id, group, related = _run_identity(bundle, epoch)
    declarations = {row["segment_id"]: row["declaration"] for row in bundle.segments}
    publications = {int(row["position"]["publication_index"]): row for row in original_publications}
    inputs: dict[int, list[Mapping[str, Any]]] = {}
    for row in original_inputs:
        inputs.setdefault(int(row["pre_position"]["publication_index"]), []).append(row)
    for rows in inputs.values():
        rows.sort(key=lambda row: int(row["input_prefix_ordinal"]))
    boundaries: dict[int, list[Mapping[str, Any]]] = {}
    for row in original_boundaries:
        boundaries.setdefault(int(row["position"]["publication_index"]), []).append(row)
    source_previous: NativeUnit | None = None
    publication_previous: NativeUnit | None = None
    publication_actions: list[dict[str, Any]] | None = None
    steps: list[dict[str, Any]] = []
    prefix_failure: str | None = None

    def boundary(row: Mapping[str, Any]) -> None:
        nonlocal prefix_failure
        paused_gap = any(
            interval["epoch_id"] == epoch_id for interval in row.get("paused_intervals", ())
        )
        if (row["kind"] == "pause" or paused_gap) and prefix_failure is None:
            prefix_failure = "recording_pause_excludes_remaining_epoch"
            exclusions.append(
                {
                    "run_id": run_id,
                    "stream": "source-boundaries.jsonl",
                    "sequence": row["sequence"],
                    "reason": prefix_failure,
                    "original": _plain(row),
                }
            )

    def exposure(row: Mapping[str, Any], is_input: bool) -> None:
        nonlocal \
            source_previous, \
            publication_previous, \
            publication_actions, \
            prefix_failure, \
            eligible
        stream = "native-input-witnesses.jsonl" if is_input else "public-observations.jsonl"
        capture = row["pre_capture"] if is_input else row["capture"]
        catalog = row["catalog"]
        point = row["pre_position"] if is_input else row["position"]
        evidence = {
            "raw_id": raw_id,
            "bundle_content_id": bundle.content_id,
            "original_run_id": run_id,
            "epoch_id": epoch_id,
            "segment_id": row["segment_id"],
            "source_declaration": _plain(declarations[row["segment_id"]]),
            "publication_index": point["publication_index"],
            "input_prefix_ordinal": row["input_prefix_ordinal"] if is_input else None,
            "stream": stream,
            "stream_sequence": row["sequence"],
            "capture": _plain(capture),
            "catalog": _plain(catalog),
            "input_id": row["input_id"] if is_input else None,
        }
        label = None
        reason = prefix_failure
        model_exposed = False
        target_eligibility = "publication_has_no_N_target"
        if (
            reason is None
            and is_input
            and row["basis_order"] != {"status": "native_prefix_frozen", "reason_code": None}
        ):
            reason = "input_basis_order_unproven"
        if reason is None and (
            capture is None or catalog is None or not is_input and row["completeness"] != "complete"
        ):
            reason = "original_complete_basis_missing"
        if reason is None:
            observation_raw = _blob(bundle, capture, "sha256")
            catalog_raw = _blob(bundle, catalog, "payload_sha256")
            observation, actions = decode_json(observation_raw), decode_json(catalog_raw)
            frame, unit = qualify_native(observation, actions)
            source_advance = native_advance(source_previous, unit)
            source_previous = unit
            model_exposed = (
                not is_input
                or view != DEFAULT_VIEW
                or unit == publication_previous
                and actions == publication_actions
            )
            advance = (
                native_advance(publication_previous, unit)
                if view == DEFAULT_VIEW
                else source_advance
            )
            if is_input:
                outcome = row["outcome"]
                task_complete = observe_ready_summary(observation).agent_task_complete
                target_eligibility = (
                    "deployment_publication_basis_mismatch"
                    if not model_exposed
                    else "ready_summary_task_complete_no_N"
                    if task_complete
                    else "other_original_declared_cohort"
                    if declarations[row["segment_id"]]["source_kind"] != cohort
                    else "mapping_or_delivery_not_exact_delivered"
                )
                if (
                    outcome["mapping_status"] == "exact"
                    and outcome["delivery"] == "delivered"
                    and declarations[row["segment_id"]]["source_kind"] == cohort
                    and model_exposed
                    and not task_complete
                ):
                    selected = _plain(outcome["selected_action"])
                    # Equality of the entire original action is essential; ID-only
                    # matches cannot supply a target or an abbreviated catalog.
                    if outcome["match_count"] != 1 or sum(a == selected for a in actions) != 1:
                        _fail("N_full_original_action_binding")
                    label = selected["action_id"]
                    eligible += 1
                    target_eligibility = "eligible_exact_delivered_N"
            if not is_input:
                publication_previous, publication_actions = unit, actions
            if model_exposed:
                steps.append(
                    {
                        "observation": observation,
                        "catalog": actions,
                        "chosen_action_id": label,
                        "reset_before": not steps,
                        "reset_reason": "original_attachment_epoch_start" if not steps else None,
                        "evidence": evidence,
                    }
                )
            # Validation is performed now and again when serialized inputs load.
            assert type(advance) is bool and frame.action_ids == tuple(
                a["action_id"] for a in actions
            )
        else:
            target_eligibility = reason if is_input else "publication_has_no_N_target"
            if prefix_failure is None:
                prefix_failure = reason
            exclusions.append(
                {
                    "run_id": run_id,
                    "stream": stream,
                    "sequence": row["sequence"],
                    "reason": reason,
                    "original": _plain(row),
                }
            )
        index.append(
            {
                "raw_id": raw_id,
                "run_id": run_id,
                "source_group": group,
                "related_keys": related,
                "capture_id": capture["capture_id"]
                if capture is not None
                else row.get("input_id", "missing-publication"),
                "record_ref": stream + ":" + str(row["sequence"]),
                "occurrence_id": semantic_hash([bundle.content_id, stream, row["sequence"]]),
                "admitted": reason is None,
                "model_exposed": model_exposed,
                "N_eligible": label is not None,
                "target_eligibility": target_eligibility,
                "original_N_candidate": is_input
                and declarations[row["segment_id"]]["source_kind"] == cohort
                and row["outcome"]["mapping_status"] == "exact"
                and row["outcome"]["delivery"] == "delivered",
                "evidence": evidence,
            }
        )

    for cut in sorted(set(publications) | set(inputs) | set(boundaries)):
        if cut in publications:
            exposure(publications[cut], False)
        fences = sorted(boundaries.get(cut, ()), key=lambda row: row["sequence"])
        offset = 0
        for row in inputs.get(cut, ()):
            ordinal = int(row["input_prefix_ordinal"])
            while offset < len(fences) and int(fences[offset]["after_input_ordinal"]) < ordinal:
                boundary(fences[offset])
                offset += 1
            exposure(row, True)
        for fence in fences[offset:]:
            boundary(fence)
    if len(steps) > MAX_STEPS_PER_RUN:
        _fail("projected_epoch_capacity")
    if steps:
        epoch_boundaries = original_boundaries
        starts = [row for row in epoch_boundaries if row["kind"] == "launch"]
        ends = [row for row in epoch_boundaries if row["kind"] == "terminal"]
        whole = (
            epoch["context"]["game_continuity_id"] is not None
            and len(starts) == len(ends) == 1
            and prefix_failure is None
            and starts[0]["transition"]["start_provenance"] == "new"
            and starts[0]["sequence"] < ends[0]["sequence"]
        )
        result = {
            "run_id": run_id,
            "source_group": group,
            "split": "train",
            "identity": {
                "schema": "stpd/source3-original-run-view-v1",
                "raw_id": raw_id,
                "bundle_content_id": bundle.content_id,
                "epoch": _plain(epoch),
                "related_keys": related,
                "source_declarations": [_plain(row) for row in bundle.segments],
                "boundaries": _plain(epoch_boundaries),
                "capture_history": "recorded_complete_epoch_prefix",
                "whole_game_recorded_capture_eligible": whole,
                "prefix_exclusion": prefix_failure,
            },
            "steps": steps,
        }
    return result, index, exclusions, eligible


def _projection(
    bundle: Any, raw_id: str, cohort: str, view: str = DEFAULT_VIEW
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One recorded exposure per P+I; persistence sequence never orders inputs.

    Ordering indexes are mechanical, not trainable state. Admission retains a
    complete prefix per epoch; only another original epoch supplies a new reset.
    """
    if cohort not in COHORTS:
        _fail("unsupported_declared_cohort")
    projection_spec, target_spec = view_specs(view)
    if bundle.human_origin_verified:
        _fail("source_declaration_is_not_human_proof")
    publications: dict[str, list[Mapping[str, Any]]] = {}
    inputs: dict[str, list[Mapping[str, Any]]] = {}
    boundaries: dict[str, list[Mapping[str, Any]]] = {}
    for row in bundle.observations:
        publications.setdefault(row["epoch_id"], []).append(row)
    for row in bundle.inputs:
        inputs.setdefault(row["epoch_id"], []).append(row)
    for row in bundle.boundaries:
        boundaries.setdefault(row["position"]["epoch_id"], []).append(row)
    all_runs, index, exclusions, eligible = [], [], [], 0
    for epoch in bundle.epochs:
        epoch_id = epoch["epoch_id"]
        run, rows, omitted, labels = _project_epoch(
            bundle,
            epoch,
            raw_id,
            publications.get(epoch_id, []),
            inputs.get(epoch_id, []),
            boundaries.get(epoch_id, []),
            cohort,
            view,
        )
        if run is not None:
            all_runs.append(run)
        index.extend(rows)
        exclusions.extend(omitted)
        eligible += labels
    report = {
        "schema": ADMISSION_SCHEMA,
        "raw_id": raw_id,
        "bundle_content_id": bundle.content_id,
        "cohort": cohort,
        "input_spec": INPUT_SPEC,
        "projection_spec": projection_spec,
        "target_spec": target_spec,
        "qualification": view_qualification(view),
        "human_origin_verified": False,
        "final_input_prefix_ordinal": bundle.final_input_prefix_ordinal,
        "original_recording": _plain(bundle.recording),
        "index": index,
        "exclusions": exclusions,
        "counts": {
            "original_publications": len(bundle.observations),
            "original_inputs": len(bundle.inputs),
            "admitted_frames": sum(len(r["steps"]) for r in all_runs),
            "eligible_unique_N": eligible,
            "original_exact_delivered_cohort_choices": sum(
                row["original_N_candidate"] for row in index
            ),
            "publication_basis_mismatched_N": sum(
                row["original_N_candidate"]
                and row["target_eligibility"] == "deployment_publication_basis_mismatch"
                for row in index
            ),
            "ready_summary_task_masked_N": sum(
                row["original_N_candidate"]
                and row["target_eligibility"] == "ready_summary_task_complete_no_N"
                for row in index
            ),
            "model_unexposed_frames": sum(not row["model_exposed"] for row in index),
            "excluded_frames": sum(not r["admitted"] for r in index),
            "original_game_runs": sum(
                e["context"]["game_continuity_id"] is not None for e in bundle.epochs
            ),
            "whole_game_recorded_capture_runs": sum(
                r["identity"]["whole_game_recorded_capture_eligible"] for r in all_runs
            ),
        },
    }
    denominator = report["counts"]["original_exact_delivered_cohort_choices"]
    report["N_coverage"] = {
        "cohort": cohort,
        "eligible": eligible,
        "denominator": denominator,
        "fraction": eligible / denominator if denominator else None,
    }
    return all_runs, report


def parse_ordered_training_dataset(raw: bytes) -> StructuredDataset:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_SOURCE_BYTES:
        _fail("projected_source_capacity")
    source = object_fields(
        decode_json(raw),
        {
            "schema",
            "source_kind",
            "input_spec",
            "projection_spec",
            "target_spec",
            "teacher",
            "raw_refs",
            "runs",
        },
        "source3_projected",
    )
    if (
        source["schema"] != SOURCE_SCHEMA
        or source["source_kind"] not in COHORTS
        or source["input_spec"] != INPUT_SPEC
        or not isinstance(source["runs"], list)
        or not 0 < len(source["runs"]) <= MAX_RUNS
    ):
        _fail("projected_source_contract")
    view = checked_view(source["projection_spec"], source["target_spec"])
    refs = source["raw_refs"]
    if (
        not isinstance(refs, list)
        or not refs
        or any(not isinstance(r, dict) or set(r) != {"raw_id", "admission_id"} for r in refs)
    ):
        _fail("projected_original_refs_required")
    ref_ids = {digest(r["raw_id"], "source3.raw_id") for r in refs}
    if len(ref_ids) != len(refs):
        _fail("duplicate_raw_source")
    for ref in refs:
        digest(ref["admission_id"], "source3.admission_id")
    teacher = object_fields(source["teacher"], {"id", "version", "parameters"}, "source3.teacher")
    if teacher != _teacher(source["source_kind"]):
        _fail("source3_declaration_teacher_contract")
    runs: list[StructuredRun] = []
    seen: set[str] = set()
    groups: dict[str, str] = {}
    for raw_run in source["runs"]:
        run = object_fields(
            raw_run, {"run_id", "source_group", "split", "identity", "steps"}, "source3.run"
        )
        name, group = _text(run["run_id"], "run_id"), _text(run["source_group"], "source_group")
        if (
            name in seen
            or run["split"] not in {"train", "dev", "test"}
            or group in groups
            and groups[group] != run["split"]
            or not isinstance(run["identity"], dict)
            or run["identity"].get("raw_id") not in ref_ids
            or not isinstance(run["steps"], list)
            or not 0 < len(run["steps"]) <= MAX_STEPS_PER_RUN
        ):
            _fail("projected_run_contract_or_split")
        steps, previous = [], None
        for position, raw_step in enumerate(run["steps"]):
            step = object_fields(
                raw_step,
                {
                    "observation",
                    "catalog",
                    "chosen_action_id",
                    "reset_before",
                    "reset_reason",
                    "evidence",
                },
                "source3.step",
            )
            evidence = object_fields(
                step["evidence"],
                {
                    "raw_id",
                    "bundle_content_id",
                    "original_run_id",
                    "epoch_id",
                    "segment_id",
                    "source_declaration",
                    "publication_index",
                    "input_prefix_ordinal",
                    "stream",
                    "stream_sequence",
                    "capture",
                    "catalog",
                    "input_id",
                },
                "source3.evidence",
            )
            if (
                type(step["reset_before"]) is not bool
                or step["reset_before"] != (position == 0)
                or step["reset_reason"]
                != ("original_attachment_epoch_start" if position == 0 else None)
                or evidence["raw_id"] != run["identity"]["raw_id"]
                or evidence["original_run_id"] != name
            ):
                _fail("projected_reset_or_original_run_binding")
            frame, unit = qualify_native(step["observation"], step["catalog"])
            advance = native_advance(previous, unit)
            if (
                view == DEFAULT_VIEW
                and evidence["stream"] == "native-input-witnesses.jsonl"
                and advance
            ):
                _fail("publication_memory_input_basis_must_not_advance")
            label = step["chosen_action_id"]
            if label is not None and observe_ready_summary(step["observation"]).agent_task_complete:
                _fail("N_ready_summary_task_complete_must_be_unlabelled")
            if label is not None and (
                evidence["stream"] != "native-input-witnesses.jsonl"
                or evidence["source_declaration"]["source_kind"] != source["source_kind"]
                or not isinstance(label, str)
                or label not in frame.action_ids
            ):
                _fail("N_label_catalog_binding")
            steps.append(
                StructuredStep(
                    position,
                    frame,
                    label,
                    step["reset_before"],
                    advance,
                    evidence["capture"]["sha256"],
                    evidence["capture"]["capture_id"],
                    # This parser verifies projected bytes/model semantics.
                    # Only verify_ordered_source_partition proves their raw
                    # archive join; a SHA or a caller-created dictionary does
                    # not become an independently verified capsule.
                    False,
                    step["reset_reason"],
                )
            )
            previous = unit
        seen.add(name)
        groups[group] = run["split"]
        runs.append(
            StructuredRun(name, group, run["split"], FrozenObject.of(run["identity"]), tuple(steps))
        )
    return StructuredDataset(
        source["source_kind"],
        FrozenObject.of(teacher),
        tuple(runs),
        _sha(raw),
        raw,
        FrozenObject.of(INPUT_SPEC),
    )


def _teacher(cohort: str) -> dict[str, Any]:
    return {
        "id": "source3-declared-" + cohort,
        "version": "1.0.0",
        "parameters": {
            "origin_basis": "original_SourceDeclaration",
            "human_origin_verified": False,
        },
    }


@dataclass(frozen=True)
class OrderedSourceRef:
    raw_id: str
    admission_id: str

    def __post_init__(self) -> None:
        digest(self.raw_id, "source3.raw_id")
        digest(self.admission_id, "source3.admission_id")


@dataclass(frozen=True)
class VerifiedOrderedSource:
    manifest: Manifest
    dataset: StructuredDataset
    split: str
    source_ids: tuple[str, ...]
    runs: frozenset[str]
    source_groups: frozenset[str]
    index: tuple[dict[str, Any], ...]


def _raw_info(bundle: Any, archive: bytes) -> dict[str, Any]:
    return {
        "schema": RAW_SCHEMA,
        "source_profile": SOURCE_PROFILE,
        "source_kinds": sorted({row["declaration"]["source_kind"] for row in bundle.segments}),
        "bundle_content_id": bundle.content_id,
        "archive_sha256": _sha(archive),
        "original_recording": _plain(bundle.recording),
        "human_origin_verified": False,
    }


def publish_ordered_source_raw(
    store: ArtifactStore,
    directory: Path,
    original_producer: Producer,
) -> Manifest:
    """Import unchanged originals. Caller owns consent/access; no use reservation is made."""
    bundle = _verified(directory)
    if bundle.recording["recorder_source_revision"] != original_producer.source_revision:
        _fail("original_recorder_producer_mismatch")
    archive = archive_bundle(directory, max_bytes=MAX_BYTES)
    with tempfile.TemporaryDirectory(prefix="source3-import-check-") as name:
        root = Path(name)
        _extract(archive, root, max_bytes=MAX_BYTES)
        replay = _verified(root)
        if _raw_info(replay, archive) != _raw_info(bundle, archive):
            _fail("raw_changed_during_archive")
    payload = store.put_payload("archive", io.BytesIO(archive), "application/gzip")
    raw = Manifest(
        "evidence",
        original_producer,
        payloads=(payload,),
        parameters=FrozenObject.of(_raw_info(bundle, archive)),
    )
    store.publish(raw)
    return raw


def _replay_raw(
    store: ArtifactStore, raw_id: str, cohort: str, view: str
) -> tuple[Manifest, list[dict[str, Any]], dict[str, Any]]:
    raw = store.get_manifest(digest(raw_id, "source3.raw_id"))
    info = raw.parameters.value()
    if (
        raw.kind != "evidence"
        or raw.parents
        or len(raw.payloads) != 1
        or set(info)
        != {
            "schema",
            "source_profile",
            "source_kinds",
            "bundle_content_id",
            "archive_sha256",
            "original_recording",
            "human_origin_verified",
        }
        or info["schema"] != RAW_SCHEMA
        or info["source_profile"] != SOURCE_PROFILE
        or raw.payload("archive").media_type != "application/gzip"
    ):
        _fail("typed_original_source3_required")
    archive = _bytes(store, raw, "archive", MAX_BYTES)
    with tempfile.TemporaryDirectory(prefix="source3-verified-replay-") as name:
        root = Path(name)
        _extract(archive, root, max_bytes=MAX_BYTES)
        bundle = _verified(root)
        if (
            info != _raw_info(bundle, archive)
            or bundle.recording["recorder_source_revision"] != raw.producer.source_revision
        ):
            _fail("original_raw_identity_changed")
        runs, report = _projection(bundle, raw_id, cohort, view)
    report.update(
        original_producer=raw.producer.to_dict(),
        archive_sha256=_sha(archive),
        verifier=verifier_identity(),
    )
    return raw, runs, report


def publish_ordered_source_admission(
    store: ArtifactStore,
    raw_id: str,
    producer: Producer,
    *,
    cohort: str = "declared_human",
    view: str = DEFAULT_VIEW,
) -> OrderedSourceRef:
    """Produce a new immutable report under this exact verifier and ProjectionSpec."""
    raw, _, report = _replay_raw(store, raw_id, cohort, view)
    payload = store.put_payload("report", io.BytesIO(json_bytes(report)), "application/json")
    admission = Manifest(
        "analysis",
        producer,
        (Parent("raw", raw.artifact_id),),
        (payload,),
        FrozenObject.of(
            {
                "schema": ADMISSION_SCHEMA,
                "cohort": cohort,
                "view": view,
                "verifier": verifier_identity(),
            }
        ),
    )
    store.publish(admission)
    return OrderedSourceRef(raw.artifact_id, admission.artifact_id)


def _verify_ref(
    store: ArtifactStore, ref: OrderedSourceRef
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if type(ref) is not OrderedSourceRef:
        _fail("typed_source3_reference_required")
    admission = store.get_manifest(ref.admission_id)
    info = object_fields(
        admission.parameters.value(), {"schema", "cohort", "view", "verifier"}, "source3.admission"
    )
    _, runs, report = _replay_raw(store, ref.raw_id, info["cohort"], info["view"])
    if (
        admission.kind != "analysis"
        or admission.parents != (Parent("raw", ref.raw_id),)
        or len(admission.payloads) != 1
        or admission.payload("report").media_type != "application/json"
        or admission.parameters.value()
        != {
            "schema": ADMISSION_SCHEMA,
            "cohort": report["cohort"],
            "view": info["view"],
            "verifier": verifier_identity(),
        }
        or _bytes(store, admission, "report", MAX_REPORT_BYTES) != json_bytes(report)
    ):
        _fail("source3_admission_report_binding")
    return runs, report


def _partition(
    store: ArtifactStore, refs: tuple[OrderedSourceRef, ...], split: str
) -> tuple[bytes, tuple[dict[str, Any], ...]]:
    if split not in {"train", "dev", "test"} or not 0 < len(refs) <= MAX_RUNS:
        _fail("partition_selection")
    if len({ref.raw_id for ref in refs}) != len(refs):
        _fail("duplicate_raw_source")
    runs: list[dict[str, Any]] = []
    index: list[dict[str, Any]] = []
    cohort: str | None = None
    selected_view: str | None = None
    contents: set[str] = set()
    for ref in refs:
        original_runs, report = _verify_ref(store, ref)
        if report["bundle_content_id"] in contents:
            _fail("duplicate_original_bundle_content")
        contents.add(report["bundle_content_id"])
        if cohort is not None and cohort != report["cohort"]:
            _fail("separate_declared_source_cohorts_required")
        cohort = report["cohort"]
        view = checked_view(report["projection_spec"], report["target_spec"])
        if selected_view is not None and selected_view != view:
            _fail("same_projection_target_view_required")
        selected_view = view
        runs.extend({**run, "split": split} for run in original_runs)
        index.extend(report["index"])
    if cohort is None or selected_view is None:
        _fail("partition_selection")
    projection_spec, target_spec = view_specs(selected_view)
    source = {
        "schema": SOURCE_SCHEMA,
        "source_kind": cohort,
        "input_spec": INPUT_SPEC,
        "projection_spec": projection_spec,
        "target_spec": target_spec,
        "teacher": _teacher(cohort),
        "raw_refs": [ref.__dict__ for ref in refs],
        "runs": runs,
    }
    raw = json_bytes(source)
    parse_ordered_training_dataset(raw)
    return raw, tuple(index)


def publish_ordered_source_partition(
    store: ArtifactStore, raw_refs: tuple[OrderedSourceRef, ...], split: str, producer: Producer
) -> VerifiedOrderedSource:
    if (
        not isinstance(raw_refs, tuple)
        or not raw_refs
        or any(type(ref) is not OrderedSourceRef for ref in raw_refs)
    ):
        _fail("typed_source3_reference_required")
    refs = tuple(sorted(raw_refs, key=lambda ref: ref.raw_id))
    raw, _ = _partition(store, refs, split)
    dataset = parse_ordered_training_dataset(raw)
    projected = decode_json(raw)
    view = checked_view(projected["projection_spec"], projected["target_spec"])
    payload = store.put_payload("source", io.BytesIO(raw), "application/json")
    manifest = Manifest(
        "dataset",
        producer,
        tuple(Parent(f"admission-{i:04d}", ref.admission_id) for i, ref in enumerate(refs)),
        (payload,),
        FrozenObject.of(
            {
                "schema": SOURCE_SCHEMA,
                "partition_schema": PARTITION_SCHEMA,
                "split": split,
                "source_kind": dataset.source_kind,
                "source_sha256": dataset.source_sha256,
                "input_spec": INPUT_SPEC,
                "projection_spec": projected["projection_spec"],
                "target_spec": projected["target_spec"],
                "qualification": view_qualification(view),
                "raw_refs": [ref.__dict__ for ref in refs],
            }
        ),
    )
    store.publish(manifest)
    return verify_ordered_source_partition(store, manifest.artifact_id)


def verify_ordered_source_partition(store: ArtifactStore, source_id: str) -> VerifiedOrderedSource:
    manifest = store.get_manifest(digest(source_id, "source3.partition_id"))
    info = manifest.parameters.value()
    if (
        manifest.kind != "dataset"
        or len(manifest.payloads) != 1
        or manifest.payload("source").media_type != "application/json"
        or set(info)
        != {
            "schema",
            "partition_schema",
            "split",
            "source_kind",
            "source_sha256",
            "input_spec",
            "projection_spec",
            "target_spec",
            "qualification",
            "raw_refs",
        }
        or info["schema"] != SOURCE_SCHEMA
        or info["partition_schema"] != PARTITION_SCHEMA
        or info["input_spec"] != INPUT_SPEC
        or not isinstance(info["raw_refs"], list)
        or not info["raw_refs"]
    ):
        _fail("typed_source3_partition_required")
    view = checked_view(info["projection_spec"], info["target_spec"])
    if info["qualification"] != view_qualification(view):
        _fail("partition_qualification_identity")
    refs = tuple(
        OrderedSourceRef(**object_fields(value, {"raw_id", "admission_id"}, "source3.reference"))
        for value in info["raw_refs"]
    )
    if refs != tuple(sorted(refs, key=lambda ref: ref.raw_id)) or manifest.parents != tuple(
        Parent(f"admission-{i:04d}", ref.admission_id) for i, ref in enumerate(refs)
    ):
        _fail("partition_parent_closure")
    expected, index = _partition(store, refs, info["split"])
    raw = _bytes(store, manifest, "source", MAX_SOURCE_BYTES)
    if raw != expected:
        _fail("projected_original_join_mismatch")
    dataset = parse_ordered_training_dataset(raw)
    source = decode_json(raw)
    if checked_view(source["projection_spec"], source["target_spec"]) != view:
        _fail("partition_view_binding")
    if dataset.source_sha256 != info["source_sha256"] or dataset.source_kind != info["source_kind"]:
        _fail("partition_identity")
    return VerifiedOrderedSource(
        manifest,
        dataset,
        info["split"],
        tuple(ref.raw_id for ref in refs),
        frozenset(row["run_id"] for row in index),
        frozenset(row["source_group"] for row in index),
        index,
    )
