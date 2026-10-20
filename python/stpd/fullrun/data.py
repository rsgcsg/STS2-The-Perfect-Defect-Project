"""Full-Run admission, semantic-component/whole-run splits and canonical datasets."""

from __future__ import annotations

import hashlib
import io
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from spireagent.artifact_contracts import Manifest, Parent, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, json_bytes, unsigned
from spireagent.storage.store import ArtifactStore

from ..canonical import canonical_json, semantic_hash
from .contracts import ResearchTransitionV1, ResearchTransitionV2, SourceAdapter, SourceProjection
from .representation import decision_fingerprint

DATASET_SCHEMA = "stpd/fullrun-dataset-v1"


@dataclass(frozen=True)
class AdmittedDataset:
    records: Sequence[ResearchTransitionV1]
    splits: FrozenObject
    seed: int
    scope: str
    exact_duplicates: int = 0

    @property
    def logical_id(self) -> str:
        return semantic_hash(
            {
                "schema": DATASET_SCHEMA,
                "scope": self.scope,
                "seed": self.seed,
                "records": [r.to_dict() for r in self.records],
                "splits": self.splits.value(),
            }
        )


def split_whole_runs(records: Sequence[ResearchTransitionV1], seed: int) -> FrozenObject:
    """Keep entire runs and repeated semantic decision components in a single partition."""
    return split_run_fingerprints(
        ((record.run_id, decision_fingerprint(record)) for record in records), seed,
    )


def split_run_fingerprints(rows: Iterable[tuple[str, str]], seed: int) -> FrozenObject:
    """The same whole-run algorithm over the trusted selector's compact row index."""
    unsigned(seed, "split.seed")
    components = sorted(
        group_run_fingerprints(rows), key=lambda group: semantic_hash([seed, group])
    )
    if len(components) < 3:
        raise BoundaryError(
            "split",
            "insufficient_independent_run_components",
            "collect at least three independent run components",
        )
    test_count = max(1, len(components) // 10)
    dev_count = max(1, len(components) // 10)
    assignments: dict[str, str] = {}
    for index, component in enumerate(components):
        partition = (
            "test" if index < test_count else ("dev" if index < test_count + dev_count else "train")
        )
        assignments.update({run_id: partition for run_id in component})
    return FrozenObject.of(assignments)


def group_run_fingerprints(rows: Iterable[tuple[str, str]]) -> tuple[tuple[str, ...], ...]:
    """Existing run/component grouping, reusable before choosing any partition."""
    parent: dict[str, str] = {}

    def root(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    first: dict[str, str] = {}
    for run_id, fingerprint in rows:
        parent.setdefault(run_id, run_id)
        previous = first.setdefault(fingerprint, run_id)
        left, right = root(previous), root(run_id)
        if left != right:
            parent[max(left, right)] = min(left, right)
    groups: dict[str, list[str]] = defaultdict(list)
    for run_id in sorted(parent):
        groups[root(run_id)].append(run_id)
    return tuple(tuple(group) for group in sorted(groups.values()))


def _adapter(adapter_id: str) -> SourceAdapter:
    from .fixtures import SyntheticSourceAdapter
    from .platform_bundle3 import ADAPTER_ID, PlatformBundle3SourceAdapter

    if adapter_id == SyntheticSourceAdapter.adapter_id:
        return SyntheticSourceAdapter()
    if adapter_id == ADAPTER_ID:
        return PlatformBundle3SourceAdapter()
    raise BoundaryError("source", "uninstalled_source_adapter")


def _verify_platform_projection(
    projection: SourceProjection, *, require_admissible: bool = True
) -> None:
    if projection.scope != "platform_verified" or projection.source_bytes is None:
        raise BoundaryError("admission", "unverified_platform_projection")
    verified = _adapter(projection.adapter_id).project(projection.source_bytes)
    if verified != projection:
        raise BoundaryError("admission", "source_transition_projection_mismatch")
    if not require_admissible:
        return
    accounting = projection.accounting.value()
    occurrences = accounting.get("occurrences", [])
    if any(
        item["disposition"] == "transition_unknown"
        or (item["disposition"] == "transition_proved" and item["canonical_record_id"] is None)
        for item in occurrences
    ):
        raise BoundaryError("admission", "unresolved_or_unpersisted_occurrence")
    if any(
        item.get("disposition") == "failed_closed" for item in accounting.get("invalidations", [])
    ):
        raise BoundaryError("admission", "failed_closed_human_decision")
    # Cancellation/abort and diagnostic dispositions stay in the source ledger;
    # they are not fabricated committed training rows and are not real losses.
    decisions = {
        r.occurrence.value()["decision_id"]: r
        for r in projection.transitions
        if isinstance(r, ResearchTransitionV2)
    }
    for record in decisions.values():
        parent_id = record.occurrence.value().get("parent_decision_id")
        if parent_id is not None:
            parent = decisions.get(parent_id)
            if (
                parent is None
                or parent.run_id != record.run_id
                or parent.step_index >= record.step_index
                or parent.provenance.native_root_ref != record.provenance.native_root_ref
            ):
                raise BoundaryError("admission", "missing_or_inconsistent_canonical_parent")


def admit(projections: tuple[SourceProjection, ...], *, seed: int = 0) -> AdmittedDataset:
    if not projections or any(not isinstance(p, SourceProjection) for p in projections):
        raise BoundaryError("admission", "no_verified_source_projection")
    scopes = {projection.scope for projection in projections}
    if len(scopes) != 1:
        raise BoundaryError("admission", "mixed_engineering_and_qualified_sources")
    for projection in projections:
        if projection.scope != "engineering":
            _verify_platform_projection(projection)
    unique: dict[str, ResearchTransitionV1] = {}
    positions: dict[tuple[str, int], str] = {}
    duplicates = 0
    for projection in projections:
        proof_map = projection.run_proofs.value()
        projected_runs = {record.run_id for record in projection.transitions}
        if set(proof_map) != projected_runs:
            raise BoundaryError("admission", "run_proof_inventory_mismatch")
        for record in projection.transitions:
            if not record.rank_eligible:
                raise BoundaryError("admission", "incomplete_or_uncommitted_decision")
            decision_fingerprint(record)
            previous = unique.get(record.transition_id)
            if previous is not None:
                if previous.to_dict() != record.to_dict():
                    raise BoundaryError("admission", "transition_identity_collision")
                duplicates += 1
                continue
            position = (record.run_id, record.step_index)
            if position in positions:
                raise BoundaryError("admission", "run_step_collision")
            positions[position] = record.transition_id
            unique[record.transition_id] = record
    records = tuple(
        sorted(unique.values(), key=lambda r: (r.run_id, r.step_index, r.transition_id))
    )
    if not records:
        raise BoundaryError("admission", "empty_dataset")
    for run_id in {record.run_id for record in records}:
        run = [record for record in records if record.run_id == run_id]
        if [record.step_index for record in run] != list(range(len(run))):
            raise BoundaryError("admission", "missing_run_step")
        if not run[-1].terminal:
            raise BoundaryError("admission", "missing_terminal_disposition")
        if any(record.terminal for record in run[:-1]):
            raise BoundaryError("admission", "decision_after_terminal")
        for projection in projections:
            proof = projection.run_proofs.value().get(run_id)
            if proof is not None and proof.get("record_count") != len(run):
                raise BoundaryError("admission", "source_disposition_count_mismatch")
    return AdmittedDataset(records, split_whole_runs(records, seed), seed, scopes.pop(), duplicates)


def publish_source(
    store: ArtifactStore,
    raw: bytes,
    adapter: SourceAdapter,
    producer: Producer,
    *,
    parents: tuple[Parent, ...] = (),
) -> tuple[Manifest, SourceProjection]:
    projection = adapter.project(raw)
    if hashlib.sha256(raw).hexdigest() != projection.source_sha256:
        raise BoundaryError("source", "adapter_source_hash_mismatch")
    if projection.scope != "engineering":
        _verify_platform_projection(projection, require_admissible=False)
    payload = store.put_payload(
        "source",
        io.BytesIO(raw),
        "application/gzip" if projection.scope == "platform_verified" else "application/json",
    )
    manifest = Manifest(
        "evidence",
        producer,
        parents=parents,
        payloads=(payload,),
        parameters=FrozenObject.of(
            {
                "schema": "stpd/source-projection-v1",
                "adapter": projection.adapter_id,
                "scope": projection.scope,
                "source_sha256": projection.source_sha256,
                "run_proofs": projection.run_proofs.value(),
                **(
                    {"accounting": projection.accounting.value()}
                    if projection.scope != "engineering"
                    else {}
                ),
            }
        ),
    )
    store.publish(manifest)
    return manifest, projection


def publish_received_source(
    store: ArtifactStore, received_id: str, producer: Producer
) -> tuple[Manifest, SourceProjection]:
    """Promote received bytes to a verified research source, not Dataset admission.

    Preserve the Hub receipt/transport lineage as a parent. Independently rerun the
    pinned source verifier rather than trusting the Hub's disposition or metadata.
    """
    from .platform_bundle3 import MAX_BYTES, PlatformBundle3SourceAdapter

    received = store.get_manifest(received_id)
    if (
        received.kind != "evidence"
        or received.parameters.value().get("schema") != "stpd/received-bundle-v1"
    ):
        raise BoundaryError("received_source", "unsupported_received_bundle")
    payload = received.payload("archive")
    if payload.size > MAX_BYTES:
        raise BoundaryError("received_source", "source_bundle_size_limit")
    raw = b"".join(store.read_payload(payload))
    return publish_source(
        store,
        raw,
        PlatformBundle3SourceAdapter(),
        producer,
        parents=(Parent("received", received_id),),
    )


def _projections_from_manifests(
    store: ArtifactStore, records: Sequence[ResearchTransitionV1], sources: tuple[Manifest, ...]
) -> tuple[SourceProjection, ...]:
    by_source = {source.payload("source").sha256: source for source in sources}
    if not sources or len(by_source) != len(sources):
        raise BoundaryError("dataset", "source_inventory_mismatch")
    if set(by_source) != {r.provenance.bundle_sha256 for r in records}:
        raise BoundaryError("dataset", "source_inventory_mismatch")
    projections = []
    for source_hash, source in by_source.items():
        info = source.parameters.value()
        if (
            source.kind != "evidence"
            or info.get("schema") != "stpd/source-projection-v1"
            or info.get("source_sha256") != source_hash
        ):
            raise BoundaryError("dataset", "invalid_source_manifest")
        adapter = _adapter(info.get("adapter", ""))
        payload = source.payload("source")
        if payload.size > 256 * 1024 * 1024:
            raise BoundaryError("dataset", "source_bundle_size_limit")
        projected = adapter.project(b"".join(store.read_payload(payload)))
        if (
            projected.source_sha256 != source_hash
            or projected.scope != info.get("scope")
            or projected.run_proofs.value() != info.get("run_proofs")
            or (
                projected.scope != "engineering"
                and projected.accounting.value() != info.get("accounting")
            )
        ):
            raise BoundaryError("dataset", "source_attestation_mismatch")
        expected = {record.transition_id: record.to_dict() for record in projected.transitions}
        actual = {
            record.transition_id: record.to_dict()
            for record in records
            if record.provenance.bundle_sha256 == source_hash
        }
        if actual != expected:
            raise BoundaryError("dataset", "source_transition_projection_mismatch")
        projections.append(projected)
    return tuple(projections)


def publish_dataset(
    store: ArtifactStore,
    dataset: AdmittedDataset,
    sources: tuple[Manifest, ...],
    producer: Producer,
) -> Manifest:
    import pyarrow as pa
    import pyarrow.parquet as pq

    verified = admit(
        _projections_from_manifests(store, dataset.records, sources), seed=dataset.seed
    )
    if dataset.logical_id != verified.logical_id or dataset.scope != verified.scope:
        raise BoundaryError("dataset", "unadmitted_publication")
    rows = [
        {
            "transition_id": record.transition_id,
            "run_id": record.run_id,
            "step_index": record.step_index,
            "surface": record.surface,
            "family": record.family,
            "split": dataset.splits.value()[record.run_id],
            "record_json": canonical_json(record.to_dict()),
        }
        for record in dataset.records
    ]
    schema = pa.schema(
        [
            ("transition_id", pa.string()),
            ("run_id", pa.string()),
            ("step_index", pa.int64()),
            ("surface", pa.string()),
            ("family", pa.string()),
            ("split", pa.string()),
            ("record_json", pa.string()),
        ]
    )
    with tempfile.TemporaryFile("w+b") as handle:
        pq.write_table(
            pa.Table.from_pylist(rows, schema=schema),
            handle,
            compression="zstd",
            version="2.6",
            use_dictionary=True,
        )
        handle.seek(0)
        records_payload = store.put_payload("records", handle, "application/vnd.apache.parquet")
    splits_payload = store.put_payload(
        "splits", io.BytesIO(json_bytes(dataset.splits.value())), "application/json"
    )
    evidence_roles = (
        ("evidence",)
        if len(sources) == 1
        else tuple(f"evidence_{source.payload('source').sha256}" for source in sources)
    )
    manifest = Manifest(
        "dataset",
        producer,
        parents=tuple(
            Parent(role, source.artifact_id)
            for role, source in zip(evidence_roles, sources, strict=True)
        ),
        payloads=(records_payload, splits_payload),
        parameters=FrozenObject.of(
            {
                "schema": DATASET_SCHEMA,
                "logical_id": dataset.logical_id,
                "scope": dataset.scope,
                "seed": dataset.seed,
                "records": len(dataset.records),
                "runs": len(dataset.splits.value()),
                "exact_duplicates": dataset.exact_duplicates,
                "whole_run_and_semantic_component_isolation": True,
            }
        ),
    )
    store.publish(manifest)
    return manifest


def load_dataset(store: ArtifactStore, artifact_id: str) -> tuple[Manifest, AdmittedDataset]:
    import pyarrow.parquet as pq

    manifest = store.get_manifest(artifact_id)
    parameters = manifest.parameters.value()
    from .curated_dataset import SCHEMA as CURATED_SCHEMA
    from .curated_dataset import load_selection

    if manifest.kind == "dataset" and parameters.get("schema") == CURATED_SCHEMA:
        selected = load_selection(store, manifest, cache=None)
        report = selected.report.value()
        if "unassigned" in report["splits"].values():
            raise BoundaryError("dataset", "insufficient_independent_run_components")
        return manifest, AdmittedDataset(
            selected.records, FrozenObject.of(report["splits"]), parameters["rules"]["seed"],
            "platform_verified", report["exact_duplicate_decisions"],
        )
    if manifest.kind != "dataset" or parameters.get("schema") != DATASET_SCHEMA:
        raise BoundaryError("dataset", "unsupported_dataset_contract")
    if {p.role for p in manifest.payloads} != {"records", "splits"}:
        raise BoundaryError("dataset", "payload_inventory_mismatch")
    if manifest.payload("splits").size > 16 * 1024 * 1024:
        raise BoundaryError("dataset", "split_manifest_too_large")
    splits_raw = b"".join(store.read_payload(manifest.payload("splits")))
    splits = decode_json(splits_raw)
    if not isinstance(splits, dict) or splits_raw != json_bytes(splits):
        raise BoundaryError("dataset", "invalid_split_manifest")
    records: list[ResearchTransitionV1] = []
    with tempfile.TemporaryFile("w+b") as handle:
        for chunk in store.read_payload(manifest.payload("records")):
            handle.write(chunk)
        handle.seek(0)
        parquet = pq.ParquetFile(handle)
        if set(parquet.schema_arrow.names) != {
            "transition_id",
            "run_id",
            "step_index",
            "surface",
            "family",
            "split",
            "record_json",
        }:
            raise BoundaryError("dataset", "parquet_columns_mismatch")
        for batch in parquet.iter_batches(batch_size=1024):
            for row in batch.to_pylist():
                record = ResearchTransitionV1.decode(decode_json(row["record_json"]))
                expected = {
                    "transition_id": record.transition_id,
                    "run_id": record.run_id,
                    "step_index": record.step_index,
                    "surface": record.surface,
                    "family": record.family,
                    "split": splits.get(record.run_id),
                    "record_json": canonical_json(record.to_dict()),
                }
                if row != expected:
                    raise BoundaryError("dataset", "column_record_alignment_mismatch")
                records.append(record)
    sources = tuple(store.get_manifest(parent.artifact_id) for parent in manifest.parents)
    if any(p.role != "evidence" and not p.role.startswith("evidence_") for p in manifest.parents):
        raise BoundaryError("dataset", "source_inventory_mismatch")
    admitted = admit(
        _projections_from_manifests(store, tuple(records), sources),
        seed=unsigned(parameters.get("seed"), "dataset.seed"),
    )
    if (
        len(admitted.records) != len(records)
        or admitted.splits.value() != splits
        or admitted.logical_id != parameters.get("logical_id")
        or len(records) != parameters.get("records")
        or len(splits) != parameters.get("runs")
        or admitted.scope != parameters.get("scope")
    ):
        raise BoundaryError("dataset", "admission_or_identity_mismatch")
    return manifest, admitted
