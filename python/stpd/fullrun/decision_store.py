"""Immutable decision dataset artifacts; loading reproduces selection from source bytes."""

from __future__ import annotations

import io
import tempfile
from collections.abc import Callable, Iterator
from itertools import zip_longest
from typing import TYPE_CHECKING

from spireagent.artifact_contracts import Manifest, Parent, Payload, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json, json_bytes
from spireagent.local_verified_bundle import EVIDENCE_SCHEMA, verified_local_bundle
from spireagent.storage.store import ArtifactStore

from ..canonical import canonical_json
from .contracts import SourceProjection
from .decision_dataset import (
    SCHEMA,
    DecisionDataset,
    SelectionRules,
    _versions,
    _versions_directory,
    select_verified_sources,
)
from .decision_index import resolve_payload
from .decision_preview import PreviewCache
from .decision_union import UNION_SCHEMA, union_decisions
from .platform_bundle3 import MAX_BYTES, PlatformBundle3SourceAdapter

if TYPE_CHECKING:
    from .decision_cache import VerifiedSourceCache

Progress = Callable[[str, int, int], None]


def _sources(
    sources: tuple[Manifest, ...], progress: Progress | None = None,
) -> Iterator[tuple[Manifest, Payload]]:
    if not 1 <= len(sources) <= 100:
        raise BoundaryError("decision_dataset", "source_selection_limit")
    total = 0
    for index, source in enumerate(sorted(sources, key=lambda s: s.payload("archive").sha256)):
        if progress:
            progress("reading_sources", index, len(sources))
        info = source.parameters.value()
        received = (info.get("schema") == "stpd/received-bundle-v1"
                    and info.get("disposition") == "verified")
        local = info.get("schema") == EVIDENCE_SCHEMA
        if source.kind != "evidence" or not (received or local):
            raise BoundaryError("decision_dataset", "source_not_verified_receipt")
        payload = source.payload("archive")
        total += payload.size
        if payload.size > MAX_BYTES or total > MAX_BYTES:
            raise BoundaryError("decision_dataset", "source_size_limit")
        yield source, payload


def _recheck_local_sources(store: ArtifactStore, sources: tuple[Manifest, ...]) -> None:
    """A preview-cache hit never substitutes for current local evidence bytes."""
    for source, _ in _sources(sources):
        if source.parameters.value().get("schema") == EVIDENCE_SCHEMA:
            with verified_local_bundle(store, source):
                pass


def _lineage_has_local_source(store: ArtifactStore, parents: tuple[Manifest, ...]) -> bool:
    """A bounded cache-routing check; the normal union loader enforces graph rules."""
    pending = [store.get_manifest(parent.artifact_id) for parent in parents]
    seen: set[str] = set()
    while pending:
        item = pending.pop()
        if item.artifact_id in seen:
            continue
        seen.add(item.artifact_id)
        if len(seen) > 256:
            return True
        if item.parameters.value().get("schema") == EVIDENCE_SCHEMA:
            return True
        if item.kind == "dataset":
            pending.extend(store.get_manifest(parent.artifact_id) for parent in item.parents)
    return False


def _selection(sources: tuple[Manifest, ...], rules: SelectionRules, schema: str) -> dict:
    return {"schema": schema, "sources": sorted(source.artifact_id for source in sources),
            "rules": rules.to_dict()}


def preview(
    store: ArtifactStore, sources: tuple[Manifest, ...], rules: SelectionRules,
    *, cache: VerifiedSourceCache | None = None, progress: Progress | None = None,
    on_projection: Callable[[SourceProjection], None] | None = None,
) -> DecisionDataset:
    def projections() -> Iterator[tuple[SourceProjection, dict]]:
        for index, (source, payload) in enumerate(_sources(sources, progress)):
            if progress:
                progress("verifying_sources", index, len(sources))
            if source.parameters.value().get("schema") == EVIDENCE_SCHEMA:
                with verified_local_bundle(store, source) as verified:
                    if cache is None:
                        projection = PlatformBundle3SourceAdapter()._project_verified_local(
                            verified
                        )
                        yield projection, _versions_directory(verified.directory)
                    else:
                        yield resolve_payload(cache, store, payload, verified_local=verified)
                continue
            if cache is None:
                raw = b"".join(store.read_payload(payload))
                yield PlatformBundle3SourceAdapter().project(raw), _versions(raw)
                del raw
            else:
                yield resolve_payload(cache, store, payload)

    dataset = select_verified_sources(projections(), rules, on_projection)
    if progress:
        progress("verifying_sources", len(sources), len(sources))
    contracts = dataset.report.value()["source_contracts"]
    for source in sources:
        if (
            contracts[source.payload("archive").sha256]["bundle_content_id"]
            != source.parameters.value()["content_id"]
        ):
            raise BoundaryError("decision_dataset", "received_content_identity_mismatch")
    if cache is not None:
        PreviewCache(cache).put(_selection(sources, rules, SCHEMA), dataset)
    return dataset


def publish(
    store: ArtifactStore,
    sources: tuple[Manifest, ...],
    rules: SelectionRules,
    producer: Producer,
    expected_preview: str,
    *, cache: VerifiedSourceCache | None = None, progress: Progress | None = None,
) -> Manifest:
    dataset = (PreviewCache(cache).get(_selection(sources, rules, SCHEMA), expected_preview)
               if cache is not None else None)
    if dataset is None:
        dataset = preview(store, sources, rules, cache=cache, progress=progress)
    else:
        _recheck_local_sources(store, sources)
    return _publish(store, sources, rules, producer, expected_preview, dataset, SCHEMA, progress)


def preview_union(
    store: ArtifactStore, parents: tuple[Manifest, ...], rules: SelectionRules,
    *, cache: VerifiedSourceCache | None = None, progress: Progress | None = None,
) -> DecisionDataset:
    if not 1 <= len(parents) <= 100 or len({p.artifact_id for p in parents}) != len(parents):
        raise BoundaryError("decision_union", "invalid_parents")
    memo: dict[str, tuple[Manifest, DecisionDataset]] = {}
    source_budget: dict[str, int] = {}
    loaded: list[tuple[str, DecisionDataset]] = []
    for parent in sorted(parents, key=lambda p: p.artifact_id):
        if progress:
            progress("loading_selected_datasets", len(loaded), len(parents))
        # Reserve the new union node/depth so a successful preview cannot publish an
        # artifact that its own loader would reject solely because of graph bounds.
        result = _load(store, parent.artifact_id, cache, memo, set(), 1, source_budget, 1)
        loaded.append((result[0].artifact_id, result[1]))
    if progress:
        progress("union_selected_decisions", len(loaded), len(parents))
    dataset = union_decisions(tuple(loaded), rules)
    if cache is not None:
        PreviewCache(cache).put(_selection(parents, rules, UNION_SCHEMA), dataset)
    return dataset


def publish_union(
    store: ArtifactStore, parents: tuple[Manifest, ...], rules: SelectionRules,
    producer: Producer, expected_preview: str,
    *, cache: VerifiedSourceCache | None = None, progress: Progress | None = None,
) -> Manifest:
    dataset = (PreviewCache(cache).get(_selection(parents, rules, UNION_SCHEMA), expected_preview)
               if cache is not None else None)
    if dataset is None or _lineage_has_local_source(store, parents):
        dataset = preview_union(store, parents, rules, cache=cache, progress=progress)
    return _publish(store, parents, rules, producer, expected_preview,
                    dataset, UNION_SCHEMA, progress)


def materialize_records(store: ArtifactStore, dataset: DecisionDataset) -> Payload:
    import pyarrow as pa
    import pyarrow.parquet as pq

    splits = dataset.report.value()["splits"]
    schema = pa.schema(
        [
            ("transition_id", pa.string()),
            ("run_id", pa.string()),
            ("original_sequence", pa.int64()),
            ("split", pa.string()),
            ("record_json", pa.string()),
        ]
    )
    # Keep decoded rows/Arrow buffers bounded, and spool compressed bytes to the
    # verifier-owned scratch directory. Row groups are physical artifact layout;
    # ordered row content and the semantic dataset identity remain unchanged.
    with tempfile.TemporaryFile() as output:
        with pq.ParquetWriter(output, schema, compression="zstd") as writer:
            rows: list[dict[str, str | int]] = []
            row_bytes = 0
            for record in dataset.records:
                encoded = canonical_json(record.to_dict())
                size = len(encoded.encode("utf-8"))
                if rows and (len(rows) >= 128 or row_bytes + size > 8 * 1024**2):
                    writer.write_table(pa.Table.from_pylist(rows, schema=schema))
                    rows.clear()
                    row_bytes = 0
                rows.append({
                    "transition_id": record.transition_id,
                    "run_id": record.run_id,
                    "original_sequence": record.source_evidence.value()["action_sequence"],
                    "split": splits[record.run_id],
                    "record_json": encoded,
                })
                row_bytes += size
            if rows:
                writer.write_table(pa.Table.from_pylist(rows, schema=schema))
        output.seek(0)
        return store.put_payload("records", output, "application/vnd.apache.parquet")


def _publish(
    store: ArtifactStore, sources: tuple[Manifest, ...], rules: SelectionRules,
    producer: Producer, expected_preview: str, dataset: DecisionDataset,
    schema_id: str, progress: Progress | None,
) -> Manifest:
    logical_id = dataset.logical_id
    if logical_id != expected_preview:
        raise BoundaryError("decision_dataset", "preview_changed")
    if not dataset.records:
        raise BoundaryError("decision_dataset", "empty_selection")
    if progress:
        progress("publishing_dataset", 0, 1)
    records = materialize_records(store, dataset)
    report = store.put_payload(
        "selection", io.BytesIO(json_bytes(dataset.report.value())), "application/json"
    )
    unique = {s.artifact_id: s for s in sources}
    manifest = Manifest(
        "dataset",
        producer,
        parents=tuple(Parent(("dataset_" if schema_id == UNION_SCHEMA else "source_") + key, key)
                      for key in sorted(unique)),
        payloads=(records, report),
        parameters=FrozenObject.of(
            {
                "schema": schema_id,
                "logical_id": logical_id,
                "rules": rules.to_dict(),
                "records": len(dataset.records),
                "scope": "platform_verified",
                "split_status": dataset.report.value()["split_status"],
            }
        ),
    )
    store.publish(manifest)
    return manifest


def load(
    store: ArtifactStore, artifact_id: str, *, cache: VerifiedSourceCache | None = None,
) -> tuple[Manifest, DecisionDataset]:
    from .curated_dataset import SCHEMA as CURATED_SCHEMA
    from .curated_dataset import load_selection

    manifest = store.get_manifest(artifact_id)
    if manifest.parameters.value().get("schema") == CURATED_SCHEMA:
        return manifest, load_selection(store, manifest, cache=cache)
    return _load(store, artifact_id, cache, {}, set(), 0, {}, 0)


def _load(
    store: ArtifactStore, artifact_id: str, cache: VerifiedSourceCache | None,
    memo: dict[str, tuple[Manifest, DecisionDataset]], visiting: set[str], depth: int,
    source_budget: dict[str, int],
    reserved_nodes: int,
) -> tuple[Manifest, DecisionDataset]:
    import pyarrow.parquet as pq

    if artifact_id in memo:
        return memo[artifact_id]
    if artifact_id in visiting or depth > 8 or len(memo) + len(visiting) + reserved_nodes >= 256:
        raise BoundaryError("decision_dataset", "dataset_lineage_limit")
    visiting.add(artifact_id)
    manifest = store.get_manifest(artifact_id)
    info = manifest.parameters.value()
    if manifest.kind != "dataset" or info.get("schema") not in {SCHEMA, UNION_SCHEMA}:
        raise BoundaryError("decision_dataset", "unsupported_dataset_schema")
    if info["schema"] == UNION_SCHEMA:
        prefix = "dataset_"
        parents = tuple(
            (parent.artifact_id, _load(store, parent.artifact_id, cache, memo,
                                      visiting, depth + 1, source_budget, reserved_nodes)[1])
            for parent in manifest.parents
        )
        dataset = union_decisions(parents, SelectionRules.decode(info["rules"]))
    else:
        prefix = "source_"
        sources = tuple(store.get_manifest(parent.artifact_id) for parent in manifest.parents)
        for source in sources:
            payload = source.payload("archive")
            source_budget[payload.sha256] = payload.size
        if len(source_budget) > 100 or sum(source_budget.values()) > MAX_BYTES:
            raise BoundaryError("decision_dataset", "union_source_size_limit")
        dataset = preview(store, sources, SelectionRules.decode(info["rules"]), cache=cache)
    if any(p.role != prefix + p.artifact_id for p in manifest.parents):
        raise BoundaryError("decision_dataset", "parent_inventory_mismatch")
    if info.get("logical_id") != dataset.logical_id or info.get("records") != len(dataset.records):
        raise BoundaryError("decision_dataset", "dataset_reprojection_mismatch")
    if sum(p.size for p in manifest.payloads) > MAX_BYTES:
        raise BoundaryError("decision_dataset", "payload_size_limit")
    report = decode_json(b"".join(store.read_payload(manifest.payload("selection"))))
    if report != dataset.report.value():
        raise BoundaryError("decision_dataset", "selection_reprojection_mismatch")
    with tempfile.TemporaryFile() as source_file:
        for chunk in store.read_payload(manifest.payload("records")):
            source_file.write(chunk)
        source_file.seek(0)
        parquet = pq.ParquetFile(source_file)
        actual = (row for batch in parquet.iter_batches(batch_size=64)
                  for row in batch.to_pylist())
        for row, record in zip_longest(actual, dataset.records):
            expected = None if record is None else {
                "transition_id": record.transition_id,
                "run_id": record.run_id,
                "original_sequence": record.source_evidence.value()["action_sequence"],
                "split": report["splits"][record.run_id],
                "record_json": canonical_json(record.to_dict()),
            }
            if row != expected:
                raise BoundaryError("decision_dataset", "records_reprojection_mismatch")
    visiting.remove(artifact_id)
    memo[artifact_id] = (manifest, dataset)
    return manifest, dataset
