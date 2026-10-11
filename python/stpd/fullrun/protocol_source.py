"""Immutable, replay-verified sampled Agent sources and isolated partitions.

The S0 verifier owns raw byte/Runtime joins. This module binds that result to
ArtifactStore manifests and exact offer locations. Declared Agent origin is not
independent authenticity, native Human proof, causal settlement or full V1 coverage.
No publication grants a data-use permission. Synthetic fixtures retain that origin.
"""

from __future__ import annotations

import hashlib
import io
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, digest, json_bytes
from spireagent.storage.store import ArtifactStore
from stpd.canonical import semantic_hash
from stpd.fullrun import agent_source_verifier as verifier
from stpd.fullrun.structured_sequences import (
    MAX_RUNS,
    MAX_SOURCE_BYTES,
    S0_INPUT_SPEC,
    SOURCE_SCHEMA,
    StructuredDataset,
    parse_structured_dataset,
)

RAW_SCHEMA = "stpd/protocol-agent-raw-v1"
REPORT_SCHEMA = "stpd/protocol-agent-verification-v1"
PARTITION_SCHEMA = "stpd/protocol-source-partition-v1"
PROFILE = "s0-sampled-agent-offers-v1"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_FILES = 32768
MAX_REPORT_BYTES = 16 * 1024 * 1024
NON_CLAIMS = [
    "independent_authenticity",
    "human_origin",
    "causal_successor",
    "full_v1_coverage",
    "statistically_strong_independence",
    "training_use_permission",
]


@dataclass(frozen=True)
class ProtocolRunRef:
    raw_id: str
    report_id: str

    def __post_init__(self) -> None:
        digest(self.raw_id, "protocol_source.raw_id")
        digest(self.report_id, "protocol_source.report_id")


@dataclass(frozen=True)
class VerifiedProtocolSource:
    manifest: Manifest
    dataset: StructuredDataset
    split: str
    source_ids: tuple[str, ...]
    runs: frozenset[str]
    source_groups: frozenset[str]
    # Index facts are actual raw-run/offer/capsule locations, never Human transitions.
    index: tuple[dict[str, Any], ...]


def _fail(code: str) -> None:
    raise BoundaryError("protocol_source", code)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _verifier_identity() -> dict[str, str]:
    # A changed verifier requires a newly produced report, not silent report upgrade.
    return {
        "schema": REPORT_SCHEMA,
        "code_sha256": _sha(Path(verifier.__file__).read_bytes()),
        "qualification_code_sha256": _sha(Path(__file__).read_bytes()),
    }


def _bytes(store: ArtifactStore, manifest: Manifest, role: str, maximum: int) -> bytes:
    payload = manifest.payload(role)
    if not 0 < payload.size <= maximum:
        _fail("payload_size_limit")
    raw = b"".join(store.read_payload(payload))
    if len(raw) != payload.size or _sha(raw) != payload.sha256:
        _fail("payload_integrity_failure")
    return raw


def _provenance(run: dict[str, Any], producer: Producer) -> dict[str, Any]:
    start = run["identity"]["run_start"]
    revision = digest(start.get("source_revision"), "protocol_source.raw_revision", length=40)
    if revision != producer.source_revision:
        _fail("original_producer_revision_mismatch")
    fields = {
        key: digest(start.get(key), "protocol_source." + key)
        for key in (
            "source_diff_sha256",
            "runner_code_sha256",
            "records_code_sha256",
        )
    }
    # Old records do not state the Python lock or repository. Keep the explicit
    # admission Producer fields, but do not assert a nonexistent raw lock join.
    return {
        "source_revision": revision,
        **fields,
        "dirty_identity_basis": "recorded_git_diff_head_sha256",
        "producer_lock_binding": "not_recorded_in_s0_raw",
        "producer_repository_binding": "not_recorded_in_s0_raw",
    }


def _template_path(run: dict[str, Any]) -> Path:
    profile = run["identity"]["episode"]["profile"]
    return (
        Path(profile["profile_root"]).parent.parent
        / "profile-templates"
        / str(profile["template_id"])
    )


def _inventory(files: dict[str, bytes]) -> list[dict[str, Any]]:
    return [
        {"path": name, "sha256": _sha(raw), "bytes": len(raw)}
        for name, raw in sorted(files.items())
    ]


def _archive(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, raw in sorted(files.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            archive.writestr(info, raw)
    raw = output.getvalue()
    if len(files) > MAX_FILES or len(raw) > MAX_ARCHIVE_BYTES:
        _fail("archive_size_limit")
    return raw


def _extract(raw: bytes, root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if not 0 < len(entries) <= MAX_FILES:
                _fail("archive_file_count")
            total = 0
            for entry in entries:
                name = entry.filename
                parsed = PurePosixPath(name)
                if (
                    not name
                    or parsed.is_absolute()
                    or "\\" in name
                    or ":" in name
                    or any(part in {"", ".", ".."} for part in name.split("/"))
                    or name in files
                    or entry.is_dir()
                    or entry.flag_bits & 1
                    or entry.compress_type != zipfile.ZIP_STORED
                    or stat.S_IFMT(entry.external_attr >> 16) not in {0, stat.S_IFREG}
                    or not (
                        name.startswith("run/")
                        or name == "template/template.json"
                        or name.startswith("template/user-data/")
                    )
                ):
                    _fail("unsafe_archive_entry")
                total += entry.file_size
                if (
                    not 0 < entry.file_size <= verifier.MAX_LEDGER_BYTES
                    or total > MAX_ARCHIVE_BYTES
                ):
                    _fail("archive_size_limit")
                content = archive.read(entry)
                if len(content) != entry.file_size:
                    _fail("archive_size_mismatch")
                files[name] = content
                destination = root.joinpath(*parsed.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
    except BoundaryError:
        raise
    except (zipfile.BadZipFile, RuntimeError, ValueError) as error:
        raise BoundaryError("protocol_source", "archive_invalid") from error
    return files


def _expected_files(run: dict[str, Any], files: dict[str, bytes]) -> set[str]:
    template = decode_json(files["template/template.json"])
    return (
        {"run/" + item["path"] for item in run["identity"]["lineage"]}
        | {
            "template/template.json",
        }
        | {"template/user-data/" + item["path"] for item in template["files"]}
    )


def _row_index(run: dict[str, Any], raw_id: str) -> list[dict[str, Any]]:
    paths = {
        PurePosixPath(item["path"]).name.split("-", 1)[1].removesuffix(".json"): item["path"]
        for item in run["identity"]["lineage"]
        if item["path"].startswith("captures/")
    }
    return [
        {
            "raw_id": raw_id,
            "run_id": run["run_id"],
            "position": step["position"],
            "capture_id": step["capture_id"],
            "capsule_sha256": step["capsule_sha256"],
            "capsule_path": paths[step["capture_id"]],
            "occurrence_id": semantic_hash([raw_id, run["run_id"], step["position"]]),
        }
        for step in run["steps"]
    ]


def _report(run: dict[str, Any], teacher: dict[str, Any], raw: Manifest) -> dict[str, Any]:
    identity = run["identity"]
    return {
        "schema": REPORT_SCHEMA,
        "profile": PROFILE,
        "input_spec": S0_INPUT_SPEC,
        "status": "verified",
        "rejection": None,
        "raw_id": raw.artifact_id,
        "raw_archive_sha256": raw.payload("archive").sha256,
        "original_producer": raw.producer.to_dict(),
        "provenance": _provenance(run, raw.producer),
        "origin": raw.parameters.value()["origin"],
        "actor": teacher,
        "verifier": _verifier_identity(),
        "run_id": run["run_id"],
        "source_group": run["source_group"],
        "counts": identity["counts"],
        "coverage": {
            "sampling": "actual_admitted_nonempty_policy_offers",
            "history": "sampled_acquired_history_only",
            "I": False,
            "F": False,
            "native_exit": identity.get("native_exit"),
            "unoffered_captures": "retained_raw_not_model_consumed",
        },
        "inventory": raw.parameters.value()["inventory"],
        "rows": _row_index(run, raw.artifact_id),
        "non_claims": NON_CLAIMS,
    }


def _replay_raw(
    store: ArtifactStore, raw_id: str
) -> tuple[Manifest, dict[str, Any], dict[str, Any]]:
    manifest = store.get_manifest(digest(raw_id, "protocol_source.raw_id"))
    info = manifest.parameters.value()
    if (
        manifest.kind != "evidence"
        or manifest.parents
        or len(manifest.payloads) != 1
        or set(info) != {"schema", "profile", "origin", "run_id", "inventory", "provenance"}
        or info["schema"] != RAW_SCHEMA
        or info["profile"] != PROFILE
        or info["origin"] not in {"declared_agent", "synthetic_fixture"}
    ):
        _fail("typed_raw_evidence_required")
    raw = _bytes(store, manifest, "archive", MAX_ARCHIVE_BYTES)
    with tempfile.TemporaryDirectory(prefix="stpd-protocol-source-") as temporary:
        root = Path(temporary).resolve()
        files = _extract(raw, root)
        if _inventory(files) != info["inventory"]:
            _fail("raw_inventory_mismatch")
        try:
            run, teacher = verifier.verify_run(
                root / "run", "train", template_root=root / "template"
            )
            if set(files) != _expected_files(run, files):
                _fail("unrecorded_archive_files")
        except BoundaryError:
            raise
        except (OSError, KeyError, TypeError, ValueError, AttributeError) as error:
            raise BoundaryError("protocol_source", "raw_archive_incomplete_or_invalid") from error
    if info["run_id"] != run["run_id"] or info["provenance"] != _provenance(run, manifest.producer):
        _fail("raw_provenance_mismatch")
    return manifest, run, teacher


def publish_protocol_run(
    store: ArtifactStore,
    directory: Path,
    original_producer: Producer,
    producer: Producer,
    *,
    origin: str,
) -> ProtocolRunRef:
    """Publish one private raw evidence closure and its reverified analysis.

    `origin` is an explicit source declaration, never inferred from UI/API use.
    Only declared template inventory bytes are included; profiles and binaries are absent.
    `original_producer` binds the recorded revision; new report producer stays separate.
    """
    if origin not in {"declared_agent", "synthetic_fixture"}:
        _fail("explicit_source_origin_required")
    run, _ = verifier.verify_run(directory, "train")
    provenance = _provenance(run, original_producer)
    files = {
        "run/" + item["path"]: verifier.read_file(
            directory / item["path"], verifier.MAX_LEDGER_BYTES
        )
        for item in run["identity"]["lineage"]
    }
    template = _template_path(run)
    template_raw = verifier.read_file(template / "template.json", verifier.MAX_METADATA_BYTES)
    files["template/template.json"] = template_raw
    for item in decode_json(template_raw)["files"]:
        files["template/user-data/" + item["path"]] = verifier.read_file(
            verifier.relative_file(template / "user-data", item["path"]), verifier.MAX_LEDGER_BYTES
        )
    payload = store.put_payload("archive", io.BytesIO(_archive(files)), "application/zip")
    manifest = Manifest(
        "evidence",
        original_producer,
        payloads=(payload,),
        parameters=FrozenObject.of(
            {
                "schema": RAW_SCHEMA,
                "profile": PROFILE,
                "origin": origin,
                "run_id": run["run_id"],
                "inventory": _inventory(files),
                "provenance": provenance,
            }
        ),
    )
    store.publish(manifest)
    raw, replay, teacher = _replay_raw(store, manifest.artifact_id)
    if replay != run:
        _fail("raw_changed_during_publication")
    return publish_protocol_verification(store, raw.artifact_id, producer)


def publish_protocol_verification(
    store: ArtifactStore,
    raw_id: str,
    producer: Producer,
) -> ProtocolRunRef:
    """Reverify an existing original archive under this exact verifier implementation.

    A changed verifier produces a new report; historical report bytes are never edited.
    """
    raw, replay, teacher = _replay_raw(store, raw_id)
    report = _report(replay, teacher, raw)
    report_payload = store.put_payload("report", io.BytesIO(json_bytes(report)), "application/json")
    analysis = Manifest(
        "analysis",
        producer,
        (Parent("raw", raw.artifact_id),),
        (report_payload,),
        FrozenObject.of(
            {"schema": REPORT_SCHEMA, "profile": PROFILE, "verifier": _verifier_identity()}
        ),
    )
    store.publish(analysis)
    return ProtocolRunRef(raw.artifact_id, analysis.artifact_id)


def _verify_ref(store: ArtifactStore, ref: ProtocolRunRef) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(ref, ProtocolRunRef):
        _fail("typed_raw_reference_required")
    raw, run, teacher = _replay_raw(store, ref.raw_id)
    analysis = store.get_manifest(digest(ref.report_id, "protocol_source.report_id"))
    if (
        analysis.kind != "analysis"
        or analysis.parents != (Parent("raw", ref.raw_id),)
        or len(analysis.payloads) != 1
        or analysis.parameters.value()
        != {"schema": REPORT_SCHEMA, "profile": PROFILE, "verifier": _verifier_identity()}
    ):
        _fail("typed_verification_report_required")
    if _bytes(store, analysis, "report", MAX_REPORT_BYTES) != json_bytes(
        _report(run, teacher, raw)
    ):
        _fail("verification_report_binding")
    return run, teacher


def _partition(
    store: ArtifactStore, refs: tuple[ProtocolRunRef, ...], split: str
) -> tuple[bytes, tuple[dict[str, Any], ...]]:
    if (
        split not in {"train", "dev", "test"}
        or not isinstance(refs, tuple)
        or not 0 < len(refs) <= MAX_RUNS
    ):
        _fail("partition_selection_invalid")
    if len({ref.raw_id for ref in refs}) != len(refs):
        _fail("duplicate_raw_run")
    runs, teacher, origin = [], None, None
    runtime_ids: set[str] = set()
    generations: set[str] = set()
    common_template = None
    index = []
    for ref in refs:
        run, actor = _verify_ref(store, ref)
        raw_origin = store.get_manifest(ref.raw_id).parameters.value()["origin"]
        if teacher is not None and actor != teacher or origin is not None and origin != raw_origin:
            _fail("teacher_or_origin_changed")
        teacher, origin = actor, raw_origin
        episode = run["identity"]["episode"]
        runtime_id, generation = (
            episode["host"]["runtime_instance_id"],
            episode["profile"]["generation_id"],
        )
        if runtime_id in runtime_ids or generation in generations:
            _fail("fresh_native_process_and_profile_required")
        runtime_ids.add(runtime_id)
        generations.add(generation)
        template = run["identity"]["grouping_basis"] | {"canonical_seed": None}
        if common_template is not None and common_template != template:
            _fail("shared_template_or_game_changed")
        common_template = template
        run["split"] = split
        runs.append(run)
        index.extend(_row_index(run, ref.raw_id))
    source = {
        "schema": SOURCE_SCHEMA,
        "source_kind": "agent" if origin == "declared_agent" else "synthetic",
        "teacher": teacher,
        "runs": runs,
    }
    raw = json_bytes(source)
    parse_structured_dataset(raw)
    return raw, tuple(index)


def publish_protocol_source_partition(
    store: ArtifactStore,
    raw_refs: tuple[ProtocolRunRef, ...],
    split: str,
    producer: Producer,
) -> VerifiedProtocolSource:
    """Each derived source parents only selected raw/report closures, never a collection."""
    if (
        not isinstance(raw_refs, tuple)
        or not raw_refs
        or any(not isinstance(ref, ProtocolRunRef) for ref in raw_refs)
    ):
        _fail("typed_raw_reference_required")
    raw_refs = tuple(sorted(raw_refs, key=lambda ref: ref.raw_id))
    raw, _ = _partition(store, raw_refs, split)
    source = parse_structured_dataset(raw)
    payload = store.put_payload("source", io.BytesIO(raw), "application/json")
    manifest = Manifest(
        "dataset",
        producer,
        tuple(Parent(f"report-{index:04d}", ref.report_id) for index, ref in enumerate(raw_refs)),
        (payload,),
        FrozenObject.of(
            {
                "schema": SOURCE_SCHEMA,
                "partition_schema": PARTITION_SCHEMA,
                "profile": PROFILE,
                "input_spec": S0_INPUT_SPEC,
                "split": split,
                "source_kind": source.source_kind,
                "source_sha256": source.source_sha256,
                "qualification": "typed_protocol_joins_sampled_s0"
                if source.source_kind == "agent"
                else "synthetic_fixture",
                "raw_refs": [
                    {"raw_id": ref.raw_id, "report_id": ref.report_id} for ref in raw_refs
                ],
            }
        ),
    )
    store.publish(manifest)
    return verify_protocol_source_partition(store, manifest.artifact_id)


def verify_protocol_source_partition(
    store: ArtifactStore, source_id: str
) -> VerifiedProtocolSource:
    """Read-only qualification by re-executing exact archived source joins."""
    manifest = store.get_manifest(digest(source_id, "protocol_source.source_id"))
    info = manifest.parameters.value()
    if (
        manifest.kind != "dataset"
        or len(manifest.payloads) != 1
        or set(info)
        != {
            "schema",
            "partition_schema",
            "profile",
            "input_spec",
            "split",
            "source_kind",
            "source_sha256",
            "qualification",
            "raw_refs",
        }
        or info["schema"] != SOURCE_SCHEMA
        or info["partition_schema"] != PARTITION_SCHEMA
        or info["profile"] != PROFILE
        or info["input_spec"] != S0_INPUT_SPEC
        or not isinstance(info["raw_refs"], list)
        or not 0 < len(info["raw_refs"]) <= MAX_RUNS
    ):
        _fail("verified_protocol_partition_required")
    refs = []
    for value in info["raw_refs"]:
        if not isinstance(value, dict) or set(value) != {"raw_id", "report_id"}:
            _fail("raw_reference_invalid")
        refs.append(
            ProtocolRunRef(
                digest(value["raw_id"], "raw_id"), digest(value["report_id"], "report_id")
            )
        )
    if refs != sorted(refs, key=lambda ref: ref.raw_id) or manifest.parents != tuple(
        Parent(f"report-{index:04d}", ref.report_id) for index, ref in enumerate(refs)
    ):
        _fail("partition_parent_closure_mismatch")
    expected, index = _partition(store, tuple(refs), info["split"])
    raw = _bytes(store, manifest, "source", MAX_SOURCE_BYTES)
    if raw != expected:
        _fail("derived_source_raw_join_mismatch")
    dataset = parse_structured_dataset(raw)
    if (
        dataset.source_sha256 != info["source_sha256"]
        or dataset.source_kind != info["source_kind"]
        or info["qualification"]
        != (
            "typed_protocol_joins_sampled_s0"
            if dataset.source_kind == "agent"
            else "synthetic_fixture"
        )
    ):
        _fail("partition_identity_mismatch")
    return VerifiedProtocolSource(
        manifest,
        dataset,
        info["split"],
        tuple(ref.raw_id for ref in refs),
        frozenset(run.run_id for run in dataset.runs),
        frozenset(run.source_group for run in dataset.runs),
        index,
    )
