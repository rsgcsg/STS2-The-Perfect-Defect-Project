"""Immutable decision selection, independent of strict complete-run admission.

Only installed owner-verified bundle projections enter this path. Missing decisions
stay exclusions; retaining neighbouring decisions does not repair sequence continuity.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from spireagent.json_boundary import BoundaryError, FrozenObject, object_fields, unsigned

from ..canonical import canonical_json, semantic_hash
from .contracts import ResearchTransitionV2, SourceProjection
from .data import split_run_fingerprints
from .platform_bundle3 import PlatformBundle3SourceAdapter, _extract

if TYPE_CHECKING:
    from .decision_cache import VerifiedSourceCache

SCHEMA = "stpd/decision-dataset-v1"
RULE_SCHEMA = "stpd/decision-selection-v1"
FILTERS = {
    "character",
    "difficulty",
    "environment_identity",
    "game_version",
    "connector_version",
    "annotator_version",
    "family",
    "surface",
    "decision_kind",
}


@dataclass(frozen=True)
class SelectionRules:
    complete_only: bool = False
    wins_only: bool = False
    filters: FrozenObject = FrozenObject()
    seed: int = 0
    no_failures_only: bool = False

    def __post_init__(self) -> None:
        if any(
            type(v) is not bool for v in (self.complete_only, self.wins_only, self.no_failures_only)
        ):
            raise BoundaryError("decision_selection", "invalid_boolean")
        unsigned(self.seed, "decision_selection.seed")
        if not isinstance(self.filters, FrozenObject):
            raise BoundaryError("decision_selection", "mutable_filters")
        for name, values in self.filters.value().items():
            if name not in FILTERS or not isinstance(values, list) or not 1 <= len(values) <= 50:
                raise BoundaryError("decision_selection", "invalid_filter")
            for value in values:
                if name == "difficulty":
                    unsigned(value, "decision_selection.difficulty")
                elif not isinstance(value, str) or not 1 <= len(value) <= 256:
                    raise BoundaryError("decision_selection", "invalid_filter_value")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RULE_SCHEMA,
            "complete_only": self.complete_only,
            "wins_only": self.wins_only,
            "no_failures_only": self.no_failures_only,
            "filters": self.filters.value(),
            "seed": self.seed,
        }

    @classmethod
    def decode(cls, value: object) -> SelectionRules:
        obj = object_fields(
            value,
            {"schema", "complete_only", "wins_only", "no_failures_only", "filters", "seed"},
            "decision_selection",
        )
        if obj["schema"] != RULE_SCHEMA or not isinstance(obj["filters"], dict):
            raise BoundaryError("decision_selection", "unsupported_schema")
        return cls(
            obj["complete_only"],
            obj["wins_only"],
            FrozenObject.of(obj["filters"]),
            obj["seed"],
            obj["no_failures_only"],
        )


@dataclass(frozen=True)
class DecisionDataset:
    records: Sequence[ResearchTransitionV2]
    report: FrozenObject

    @property
    def run_ids(self) -> set[str]:
        from .decision_spool import SpoolSelection

        if isinstance(self.records, SpoolSelection):
            return {row["run_id"] for row in self.records.summaries()}
        return {record.run_id for record in self.records}

    def fingerprints(self) -> Iterator[str]:
        from .decision_spool import SpoolSelection
        from .representation import decision_fingerprint

        if isinstance(self.records, SpoolSelection):
            yield from (row["fingerprint"] for row in self.records.summaries())
        else:
            yield from (decision_fingerprint(record) for record in self.records)

    @property
    def logical_id(self) -> str:
        # Emit the exact sorted-key canonical object one record at a time. Building
        # every decoded record and another canonical tree exhausts bounded workers.
        from .decision_spool import SpoolSelection

        rows = (self.records.canonical_rows() if isinstance(self.records, SpoolSelection)
                else (canonical_json(r.to_dict()).encode("utf-8") for r in self.records))
        digest = hashlib.sha256(b'{"records":[')
        for index, row in enumerate(rows):
            if index:
                digest.update(b",")
            digest.update(row)
        digest.update(b'],"report":')
        digest.update(self.report.encoded.encode("utf-8"))
        digest.update(b',"schema":')
        digest.update(canonical_json(SCHEMA).encode("utf-8"))
        digest.update(b"}")
        return digest.hexdigest()


def _identity(record: ResearchTransitionV2) -> str:
    evidence = record.source_evidence.value()
    return semantic_hash(
        [
            evidence["session_id"],
            evidence["native_run_id"],
            record.occurrence.value()["decision_id"],
        ]
    )


def _fact(record: ResearchTransitionV2) -> str:
    """Exclude packaging aliases only; never deduplicate merely similar states."""
    value = record.to_dict()
    for key in ("transition_id", "provenance", "source_evidence"):
        value.pop(key)
    # Projection terminal/step depend on the exported window, not the native decision.
    value.pop("terminal", None)
    value.pop("step_index", None)
    source = record.source_evidence.value()
    value["native_evidence"] = {
        key: source[key]
        for key in (
            "action_sequence",
            "pre_frame_sha256",
            "successor_frame_sha256",
            "commit",
            "proof_ref",
            "profile_sha256",
        )
    }
    value["environment"] = record.provenance.environment_identity
    return semantic_hash(value)


def _runs(projection: SourceProjection) -> list[dict[str, Any]]:
    accounting = projection.accounting.value()
    events = accounting["journal"]
    session_ids = {
        r.source_evidence.value()["session_id"]
        for r in projection.transitions
        if isinstance(r, ResearchTransitionV2)
    }
    session_ids.update(e["session_id"] for e in events if e.get("session_id"))
    if len(session_ids) != 1:
        raise BoundaryError("decision_dataset", "ambiguous_session")
    session = next(iter(session_ids))
    native_ids = {e["run_id"] for e in events if e.get("run_id")}
    native_ids.update(item["action"]["run_id"] for item in accounting["occurrences"])
    result = []
    for native_id in sorted(native_ids):
        journal = [e for e in events if e.get("run_id") == native_id]
        ledger = [o for o in accounting["occurrences"] if o["action"]["run_id"] == native_id]
        # Session-only sentinels are not a game.
        if not ledger and not any(
            e["kind"] in {"run_started_native", "run_ended_native", "run_observed_in_progress"}
            for e in journal
        ):
            continue
        ends = [e for e in journal if e["kind"] == "run_ended_native"]
        outcome = "unknown"
        if len(ends) == 1:
            outcome = {
                "RunManager.OnEnded(isVictory=true)": "win",
                "RunManager.OnEnded(isVictory=false)": "loss",
            }.get(ends[0].get("detail"), "unknown")
            if outcome == "loss" and any(
                e["kind"] == "run_abandoned_native"
                and e.get("detail") == "RunManager.OnEnded observed IsAbandoned=true."
                for e in journal
            ):
                outcome = "abandoned"
        failures = {
            o["action"]["action_witness_id"]
            for o in ledger
            if o["disposition"] == "transition_unknown"
        }
        invalidations = [i for i in accounting["invalidations"] if i.get("run_id") == native_id]
        failures.update(
            i["decision_failure"]["decision_witness_id"]
            for i in invalidations
            if isinstance(i.get("decision_failure"), dict)
        )
        identities = [
            r.source_evidence.value()["recording_identity"]
            for r in projection.transitions
            if isinstance(r, ResearchTransitionV2)
            and r.source_evidence.value()["native_run_id"] == native_id
        ]
        disposition_known = (
            bool(identities)
            and all(identity.get("disposition_schema_version") == 1 for identity in identities)
            and all(i.get("disposition") is not None for i in invalidations)
        )
        run_id = session + "/" + native_id
        result.append(
            {
                "run_id": run_id,
                "native_run_id": native_id,
                "session_id": session,
                "complete": run_id in projection.run_proofs.value(),
                "real_failures": len(failures) if disposition_known else None,
                "invalidations": len(invalidations),
                "outcome": outcome,
                "native_starts": sum(e["kind"] == "run_started_native" for e in journal),
                "native_ends": len(ends),
                "accepted": len(ledger),
                "canonical": sum(o["canonical_record_id"] is not None for o in ledger),
                "dispositions": dict(Counter(o["disposition"] for o in ledger)),
                "journal_refs": [semantic_hash(e) for e in journal],
            }
        )
    return result


def _merge_environment(
    environments: dict[str, Any], fingerprint: str, value: dict[str, Any],
    *, boundary: str = "decision_dataset",
) -> None:
    """Join process provenance without weakening exact environment identity.

    Connector's fingerprint excludes RuntimeInstanceId (SnapshotBuilder's
    ToSessionReference). Recorder retains it as provenance. Preserve old single-runtime
    reports verbatim for immutable re-projection; only new multi-runtime selections
    use a sorted set. Every other field, including unknown future identity fields,
    must agree exactly. Inputs and original source bytes are never mutated.
    """
    if fingerprint not in environments:
        environments[fingerprint] = value
        return
    previous = environments[fingerprint]
    if previous == value:
        return
    runtime_fields = {"runtime_instance_id", "runtime_instance_ids"}
    stable = {k: v for k, v in previous.items() if k not in runtime_fields}
    if stable != {k: v for k, v in value.items() if k not in runtime_fields}:
        raise BoundaryError(boundary, "environment_identity_conflict")

    def runtimes(environment: dict[str, Any]) -> list[str]:
        if "runtime_instance_id" in environment and "runtime_instance_ids" not in environment:
            values = [environment["runtime_instance_id"]]
        elif "runtime_instance_ids" in environment and "runtime_instance_id" not in environment:
            values = environment["runtime_instance_ids"]
        else:
            raise BoundaryError(boundary, "environment_identity_conflict")
        if not isinstance(values, list) or not values or any(
            not isinstance(runtime, str) or not runtime for runtime in values
        ):
            raise BoundaryError(boundary, "environment_identity_conflict")
        return values

    identities = sorted(set(runtimes(previous)) | set(runtimes(value)))
    environments[fingerprint] = {**stable, "runtime_instance_ids": identities}


def _versions(source: bytes) -> dict[str, Any]:
    # Called only after the installed verifier accepted the archive. Join identity
    # metadata by the exact environment fingerprint, never by a current runtime.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _extract(source, root)
        return _versions_directory(root)


def _versions_directory(root: Path) -> dict[str, Any]:
    """Read environment identity from a directory already typed-verified by its owner."""
    result: dict[str, Any] = {}
    for path in sorted((root / "raw").glob("run-*.jsonl")):
        if path.name == "run-journal.jsonl":
            continue
        for line in path.read_bytes().splitlines():
            row = json.loads(line)
            if row.get("schema") != "sts2.human-annotator/decision-record-2":
                continue
            environment = row.get("environment")
            if not isinstance(environment, dict):
                continue
            fingerprint = environment.get("environment_fingerprint")
            if not isinstance(fingerprint, str):
                continue
            _merge_environment(result, fingerprint, environment)
    return result


def select_decisions(
    sources: tuple[bytes, ...], rules: SelectionRules | None = None,
    *, cache: VerifiedSourceCache | None = None,
    on_source: Callable[[int, int], None] | None = None,
    on_projection: Callable[[SourceProjection], None] | None = None,
) -> DecisionDataset:
    """Verify original bytes or reuse a private owner-bound verification of identical bytes."""
    if not 1 <= len(sources) <= 100:
        raise BoundaryError("decision_dataset", "source_selection_limit")
    def projections() -> Iterator[tuple[SourceProjection, dict[str, Any]]]:
        # Sort small source references before projecting; never retain every decoded source.
        ordered = sorted(sources, key=lambda raw: hashlib.sha256(raw).hexdigest())
        for index, source in enumerate(ordered):
            if on_source:
                on_source(index, len(sources))
            if cache is None:
                yield PlatformBundle3SourceAdapter().project(source), _versions(source)
            else:
                yield cache.resolve(source)
        if on_source:
            on_source(len(sources), len(sources))
    return select_verified_sources(projections(), rules or SelectionRules(), on_projection)


def select_verified_sources(
    sources: Iterable[tuple[SourceProjection, dict[str, Any]]], rules: SelectionRules,
    on_projection: Callable[[SourceProjection], None] | None = None,
) -> DecisionDataset:
    """Internal streaming entry: installed owner projections, in archive digest order."""
    versions: dict[str, Any] = {}

    def projections() -> Iterator[SourceProjection]:
        previous = ""
        count = 0
        for projection, environments in sources:
            count += 1
            if count > 100 or projection.source_sha256 < previous:
                raise BoundaryError("decision_dataset", "source_selection_order_or_limit")
            previous = projection.source_sha256
            if on_projection:
                on_projection(projection)
            for key, value in environments.items():
                _merge_environment(versions, key, value)
            yield projection
        if not count:
            raise BoundaryError("decision_dataset", "source_selection_limit")

    return _select(projections(), rules, versions)


def _metadata(record: ResearchTransitionV2, versions: dict[str, Any]) -> dict[str, Any]:
    environment = versions.get(record.provenance.environment_identity, {})
    return {
        "game_version": environment.get("game", {}).get("version"),
        "connector_version": environment.get("connector", {}).get("version"),
        "annotator_version": environment.get("annotator", {}).get("version"),
        "character": record.state.run.value().get("character"),
        "difficulty": record.state.run.value().get("ascension"),
        "environment_identity": record.provenance.environment_identity,
        "family": record.family,
        "surface": record.surface,
        "decision_kind": record.occurrence.value()["decision_kind"],
    }


def _facets(records: Sequence[ResearchTransitionV2], versions: dict[str, Any]) -> dict[str, Any]:
    from .decision_spool import SpoolSelection

    counters: dict[str, Counter[Any]] = {key: Counter() for key in sorted(FILTERS)}
    metadata_rows = (
        (_summary_metadata(row, versions) for row in records.summaries())
        if isinstance(records, SpoolSelection)
        else (_metadata(record, versions) for record in records)
    )
    for metadata in metadata_rows:
        for key, counts in counters.items():
            value = metadata[key]
            if (key == "difficulty" and type(value) is int and value >= 0) or (
                key != "difficulty" and isinstance(value, str) and 0 < len(value) <= 256
            ):
                counts[value] += 1
    return {
        key: {"known": sum(counts.values()), "unknown": len(records) - sum(counts.values()),
              "items": [{"value": value, "count": count}
                        for value, count in sorted(counts.items(), key=lambda x: str(x[0]))]}
        for key, counts in counters.items()
    }


def _summary_metadata(row: dict[str, Any], versions: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(row["metadata"])
    environment = versions.get(metadata["environment_identity"], {})
    for component in ("game", "connector", "annotator"):
        metadata[component + "_version"] = environment.get(component, {}).get("version")
    return metadata


def _select(
    projections: Iterable[SourceProjection], rules: SelectionRules, versions: dict[str, Any],
) -> DecisionDataset:
    from .decision_spool import DecisionSpool

    records = DecisionSpool()
    source_contracts: dict[str, Any] = {}
    source_invalidations: dict[str, Any] = {}
    facts: dict[str, str] = {}
    aliases: dict[str, list[dict[str, str]]] = defaultdict(list)
    excluded: list[dict[str, Any]] = []
    runs: dict[str, dict[str, Any]] = {}
    context: dict[str, dict[str, Any]] = {}
    seen_sources: set[str] = set()
    accepted_ids: dict[str, set[str]] = defaultdict(set)
    for projection in projections:
        if projection.source_sha256 in seen_sources:
            continue
        seen_sources.add(projection.source_sha256)
        accounting = projection.accounting.value()
        source_contracts[projection.source_sha256] = {
            "adapter": projection.adapter_id, "bundle_content_id": accounting["bundle_content_id"],
        }
        source_invalidations[projection.source_sha256] = accounting["invalidations"]
        projection_runs = _runs(projection)
        for run in projection_runs:
            previous = runs.get(run["run_id"])
            if previous is not None and previous != run:
                known = {r["outcome"] for r in (previous, run) if r["outcome"] != "unknown"}
                if len(known) > 1:
                    raise BoundaryError("decision_dataset", "run_outcome_conflict")
                variants = previous.get("coverage_variants", [dict(previous)])
                if run not in variants:
                    variants.append(run)
                # A complete claim must already exist in one verified source. Combining
                # the beginning of one fragment with the end of another cannot create it.
                previous["complete"] = any(v["complete"] for v in variants)
                previous["outcome"] = next(iter(known), "unknown")
                previous["coverage_variants"] = variants
                previous["coverage_status"] = "overlapping_exports_differ"
                previous["real_failures"] = None
            elif previous is None:
                runs[run["run_id"]] = run
        ledger = projection.accounting.value()["occurrences"]
        accepted = {
            (o["action"]["run_id"], o["action"].get("decision", {}).get("decision_id")): o
            for o in ledger
        }
        for item in ledger:
            session = next(iter(projection_runs), {}).get("session_id")
            if session:
                accepted_ids[session + "/" + item["action"]["run_id"]].add(
                    item["action"]["action_witness_id"]
                )
            if item["canonical_record_id"] is None:
                excluded.append(
                    {
                        "source_sha256": projection.source_sha256,
                        "action": item["action"],
                        "reason": item["disposition"],
                    }
                )
        for record in projection.transitions:
            if not isinstance(record, ResearchTransitionV2):
                raise BoundaryError("decision_dataset", "unsupported_transition_contract")
            decision = record.occurrence.value()
            evidence = record.source_evidence.value()
            native_run = evidence["native_run_id"]
            owner = accepted.get((native_run, decision["decision_id"]))
            if owner is None or owner["disposition"] != "transition_proved":
                raise BoundaryError("decision_dataset", "missing_proved_owner")
            identity = _identity(record)
            lineage = []
            cursor = owner
            seen: set[str] = set()
            while cursor["action"].get("decision", {}).get("parent_decision_id") is not None:
                child = cursor["action"]
                parent_id = child["decision"]["parent_decision_id"]
                parent = accepted.get((native_run, parent_id))
                if (
                    parent_id in seen
                    or parent is None
                    or parent["action"]["action_sequence"] >= child["action_sequence"]
                    or parent["action"]["decision"]["causal_root_id"] != decision["causal_root_id"]
                ):
                    raise BoundaryError("decision_dataset", "invalid_parent_lineage")
                seen.add(parent_id)
                lineage.append(parent)
                cursor = parent
            context[identity] = {
                "parents": lineage,
                "original_sequence": evidence["action_sequence"],
            }
            fact = _fact(record)
            if identity in facts and facts[identity] != fact:
                raise BoundaryError("decision_dataset", "decision_identity_conflict")
            facts[identity] = fact
            aliases[identity].append(
                {"source_sha256": projection.source_sha256, "transition_id": record.transition_id}
            )
            if identity not in records:
                records[identity] = record
    if seen_sources:
        del projection  # Do not retain the last expanded archive while selecting disk rows.
    canonical_counts = Counter(row["run_id"] for row in records.summaries())
    for run_id, run in runs.items():
        run["accepted"] = len(accepted_ids[run_id])
        run["canonical"] = canonical_counts[run_id]
    for row in records.summaries():
        identity = row["id"]
        run = runs[row["run_id"]]
        metadata = _summary_metadata(row, versions)
        reason = None
        if rules.complete_only and not run["complete"]:
            reason = "incomplete_run"
        elif rules.no_failures_only and run["real_failures"] != 0:
            reason = "recording_failures_or_unknown"
        elif rules.wins_only and run["outcome"] != "win":
            reason = "outcome_not_win"
        else:
            for key, values in sorted(rules.filters.value().items()):
                if metadata[key] not in values:
                    reason = "filter_" + key
                    break
        if reason:
            excluded.append({"decision_id": identity, "reason": reason})
        else:
            records.select(identity)
    chosen = records.selected()
    try:
        splits = split_run_fingerprints(
            ((r["run_id"], r["fingerprint"]) for r in chosen.summaries()), rules.seed,
        ).value()
        split_status = "assigned"
    except BoundaryError as error:
        if error.code != "insufficient_independent_run_components":
            raise
        splits = {r["run_id"]: "unassigned" for r in chosen.summaries()}
        split_status = error.code
    report = {
        "schema": SCHEMA,
        "environments": versions,
        "rules": rules.to_dict(),
        "sources": sorted(seen_sources),
        "source_contracts": source_contracts,
        "runs": list(runs.values()),
        "selected": len(chosen),
        "selected_facets": _facets(chosen, versions),
        "excluded": excluded,
        "exclusion_counts": dict(Counter(e["reason"] for e in excluded)),
        "aliases": dict(aliases),
        "invalidations": [{"source_sha256": key, "items": value}
                          for key, value in sorted(source_invalidations.items())],
        "context": context,
        "splits": splits,
        "split_status": split_status,
        "exact_duplicate_decisions": sum(len(v) - 1 for v in aliases.values()),
        "non_claims": [
            "complete Full Run qualification",
            "unobserved outcomes",
            "continuity across excluded decisions",
            "model game ability",
        ],
    }
    return DecisionDataset(chosen, FrozenObject.of(report))
