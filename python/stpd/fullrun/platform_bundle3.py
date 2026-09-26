"""Version-pinned Platform bundle3 verification and research-only projection.

The transport tar.gz is not a Platform schema. Its members preserve the verified
bundle bytes. Catalogs/lineage/Commit/successors are read, never reconstructed.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
import tempfile
import zlib
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sts2_platform_evidence.human_session_bundle_v3 import HumanSessionBundleV3Verifier

from spireagent.json_boundary import BoundaryError, FrozenObject, decode_json

from ..canonical import canonical_json, semantic_hash
from .contracts import (
    AdmittedRead,
    EvidenceLink,
    ResearchTransitionV2,
    SemanticAction,
    SemanticState,
    SourceProjection,
)

# Immutable adapter contract baseline, not the installed verifier or recorded runtime.
# The historical source_evidence.platform_revision wire field retains this meaning/ID.
# Actual verifier dependency is pinned by the producing STPD source and uv.lock;
# actual game/Mod provenance remains in recording_identity. Do not rewrite old artifacts.
SUPPORTED_PLATFORM_CONTRACT_REVISION = "6fb6afc9c7abb8a4d34d18d16de4f19f48bd608d"
ADAPTER_ID = "stpd-platform-bundle3-adapter-v1@" + SUPPORTED_PLATFORM_CONTRACT_REVISION
MAX_BYTES = 256 * 1024 * 1024
MAX_FILES = 20000
_TERMINATIONS = frozenset(
    {
        "transition_proved",
        "transition_unknown",
        "action_cancelled_before_start",
        "action_cancelled_after_start",
        "action_aborted_before_commit",
    }
)
# These are capture/identity metadata, not player semantics. The originals remain
# in the immutable evidence. Unknown hidden/native fields are rejected downstream.
_METADATA = frozenset(
    {
        "entity_id",
        "referent_id",
        "interaction_id",
        "combat_id",
        "action_keys",
        "evidence",
        "non_claims",
        "sources",
        "observation_basis",
        "action_support",
        "blocked_reason",
        "native_decision_status",
        "completeness",
        "slot_entity_id",
        "inventory_index",
        "focused",
        "hovered",
    }
)


def _load(path: Path) -> dict[str, Any]:
    value = decode_json(path.read_bytes())
    if not isinstance(value, dict):
        raise BoundaryError("platform_bundle3", "expected_object")
    return value


def _lines(path: Path) -> list[dict[str, Any]]:
    return [decode_json(line) for line in path.read_bytes().splitlines() if line.strip()]


def archive_bundle(directory: Path) -> bytes:
    """Deterministic transport of an existing bundle; does not edit or attest it."""
    paths = sorted(directory.rglob("*"))
    if any(p.is_symlink() for p in paths):
        raise BoundaryError("source_archive", "symlink_forbidden")
    files = [p for p in paths if p.is_file()]
    if len(files) > MAX_FILES or sum(p.stat().st_size for p in files) > MAX_BYTES:
        raise BoundaryError("source_archive", "size_limit")
    target = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=target, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        for path in files:
            content = path.read_bytes()
            info = tarfile.TarInfo(path.relative_to(directory).as_posix())
            info.size = len(content)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(content))
    return target.getvalue()


def _extract(raw: bytes, directory: Path) -> None:
    if len(raw) > MAX_BYTES:
        raise BoundaryError("source_archive", "size_limit")
    try:
        # Bound the entire decompressed stream, including PAX/longname headers
        # that tarfile consumes before yielding a member for our inventory checks.
        with tempfile.TemporaryFile() as expanded:
            total_expanded = 0
            with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as compressed:
                while chunk := compressed.read(1024 * 1024):
                    total_expanded += len(chunk)
                    if total_expanded > MAX_BYTES:
                        raise BoundaryError("source_archive", "expanded_size_limit")
                    expanded.write(chunk)
            expanded.seek(0)
            _extract_tar(expanded, directory)
    except (tarfile.TarError, gzip.BadGzipFile, EOFError, zlib.error) as error:
        raise BoundaryError("source_archive", "invalid_archive") from error


def _extract_tar(expanded: Any, directory: Path) -> None:
    # Streaming mode prevents a malicious metadata size from issuing an enormous
    # direct file read, even though the expanded archive is already disk-bounded.
    with tarfile.open(fileobj=expanded, mode="r|") as archive:
        names: set[str] = set()
        total = 0
        for info in archive:
            total += info.size
            if len(names) >= MAX_FILES or total > MAX_BYTES:
                raise BoundaryError("source_archive", "size_limit")
            path = PurePosixPath(info.name)
            folded = info.name.casefold()
            if (
                not info.name
                or "\\" in info.name
                or path.is_absolute()
                or any(p in {".", ".."} for p in info.name.split("/"))
                or ":" in info.name
                or str(path) != info.name
                or not info.isfile()
                or folded in names
                or info.size < 0
            ):
                raise BoundaryError("source_archive", "unsafe_or_duplicate_member")
            names.add(folded)
            member = archive.extractfile(info)
            if member is None:
                raise BoundaryError("source_archive", "missing_member")
            content = member.read(info.size + 1)
            if len(content) != info.size:
                raise BoundaryError("source_archive", "member_size_mismatch")
            target = directory / info.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


class _SemanticProjection:
    """Project public values; resolve opaque references only from captured entities."""

    def __init__(self, values: list[Any]) -> None:
        self.entities: dict[str, dict[str, Any]] = {}
        for value in values:
            self._collect(value)

    def _collect(self, value: Any) -> None:
        if isinstance(value, dict):
            identifier = value.get("entity_id")
            if isinstance(value.get("interaction_id"), str) and "kind" in value:
                self.entities[value["interaction_id"]] = {
                    "kind": value["kind"],
                    **value.get("content", {}),
                }
            if isinstance(value.get("referent_id"), str):
                # Connector explicitly serializes absent public properties as
                # null. This adds no inferred facts; the visible referent still
                # keeps its role and state.
                properties = value.get("properties")
                self.entities[value["referent_id"]] = {
                    "role": value.get("role"),
                    **({} if properties is None else properties),
                    "state": value.get("state", {}),
                }
            if isinstance(value.get("slot_entity_id"), str):
                self.entities[value["slot_entity_id"]] = value
            if isinstance(identifier, str):
                # Prefer richer normal inspectable descriptions over compact fields.
                previous = self.entities.get(identifier, {})
                self.entities[identifier] = {**previous, **value}
            for item in value.values():
                self._collect(item)
        elif isinstance(value, list):
            for item in value:
                self._collect(item)

    def clean(self, value: Any, *, resolving: bool = False) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                if key in _METADATA:
                    continue
                if key.endswith("_entity_ids") or key.endswith("_referent_ids"):
                    result[key.removesuffix("_ids") + "_values"] = [
                        self.reference(x, resolving) for x in item
                    ]
                elif key.endswith("_entity_id") or key.endswith("_referent_id"):
                    result[key.removesuffix("_id") + "_value"] = (
                        self.reference(item, resolving) if item is not None else None
                    )
                else:
                    result["visible_label" if key == "label" else key] = self.clean(
                        item, resolving=resolving
                    )
            if (
                value.get("ordering_semantics") == "unordered_multiset"
                or value.get("kind") == "run_deck"
            ) and isinstance(result.get("cards"), list):
                result["cards"] = sorted(result["cards"], key=canonical_json)
            return result
        if isinstance(value, list):
            return [self.clean(item, resolving=resolving) for item in value]
        if isinstance(value, str) and value in self.entities and not resolving:
            return self.entity(value)
        return value

    def reference(self, identifier: str, resolving: bool) -> dict[str, Any]:
        if not resolving:
            return self.entity(identifier)
        if identifier not in self.entities:
            raise BoundaryError("platform_projection", "missing_referent_semantics")

        # An entity can refer to itself/its owner. Keep a semantic reference rather
        # than recursively expand a cycle or leak the runtime handle into features.
        def leaf(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: leaf(item)
                    for key, item in value.items()
                    if key not in _METADATA
                    and not key.endswith(
                        ("_entity_id", "_entity_ids", "_referent_id", "_referent_ids")
                    )
                }
            if isinstance(value, list):
                return [leaf(item) for item in value]
            return value

        return {"semantic_ref": semantic_hash(leaf(self.entities[identifier]))}

    def entity(self, identifier: str | None) -> dict[str, Any]:
        if identifier is None:
            return {}
        if identifier not in self.entities:
            raise BoundaryError("platform_projection", "missing_referent_semantics")
        result: dict[str, Any] = self.clean(self.entities[identifier], resolving=True)
        return result


def _frame(raw: Path, ref: dict[str, Any]) -> dict[str, Any]:
    return _load(raw / ref["object_ref"])


def _state_and_actions(
    raw: Path,
    frame: dict[str, Any],
    catalog: dict[str, Any] | None = None,
) -> tuple[SemanticState, _SemanticProjection]:
    snapshot = frame["snapshot"]
    if (
        snapshot.get("schema") != "sts2.player-environment/snapshot-1"
        or snapshot.get("information_policy", {}).get("includes_hidden_information") is not False
        or snapshot.get("completeness", {}).get("status") != "complete"
    ):
        raise BoundaryError("platform_projection", "unqualified_state_envelope")
    persistent = snapshot.get("persistent", {}).get("content", {})
    interaction = snapshot.get("interaction", {})
    reads = [
        (read, _load(raw / read["payload_ref"]))
        for read in frame.get("reads", [])
        if read.get("status") == "materialized"
    ]
    execution = {} if catalog is None else catalog["semantic_state"]
    projector = _SemanticProjection(
        [
            persistent,
            interaction.get("content", {}),
            snapshot.get("referents", []),
            interaction,
            *[value for _, value in reads],
            execution,
        ]
    )
    public_run = persistent.get("run", {})
    public_player = persistent.get("player", {})
    run = projector.clean({**public_run, **public_player})
    if "character_definition_id" in run:
        run["character"] = run.pop("character_definition_id")
    decision = {
        "surface": interaction.get("kind", "none"),
        "content": projector.clean(interaction.get("content", {})),
    }
    if execution:
        decision["execution"] = projector.clean(execution)
    entities = tuple(
        FrozenObject.of(
            projector.clean(
                {
                    "role": ref.get("role"),
                    "properties": ref.get("properties", {}),
                    "state": ref.get("state", {}),
                }
            )
        )
        for ref in snapshot.get("referents", [])
    )
    entities = tuple(sorted(entities, key=lambda entity: entity.encoded))
    admitted_reads = tuple(
        AdmittedRead(read["kind"], FrozenObject.of(projector.clean(value)), read["payload_sha256"])
        for read, value in reads
    )
    result = SemanticState(
        FrozenObject.of(run), FrozenObject.of(decision), entities, admitted_reads
    )
    from .representation import FullRunSerializer

    FullRunSerializer("full").state_content(result)
    return result, projector


def _actions(
    row: dict[str, Any],
    pre: dict[str, Any],
    catalog: dict[str, Any] | None,
    projector: _SemanticProjection,
) -> tuple[tuple[SemanticAction, ...], str]:
    if catalog is not None:
        if (
            catalog.get("status") != "captured"
            or catalog.get("observed_membership") != "exact_once"
            or catalog.get("observed_match_count") != 1
        ):
            raise BoundaryError("platform_projection", "incomplete_native_catalog")
        values = catalog["actions"]
        chosen = catalog["observed_action_key"]
    else:
        bound = pre["snapshot"]["bound_actions"]
        if bound.get("status") != "complete" or bound.get("total_count") != len(bound["actions"]):
            raise BoundaryError("platform_projection", "incomplete_public_execution_catalog")
        values = bound["actions"]
        chosen = row["action"]["bound_action_id"]
    result = []
    for action in values:
        arguments = action.get("arguments", {})
        if isinstance(arguments, list):
            arguments = {item["role"]: item["referent_id"] for item in arguments}
        result.append(
            SemanticAction(
                action.get("key", action.get("bound_action_id")),
                action["verb"],
                FrozenObject.of(projector.entity(action.get("subject_referent_id"))),
                FrozenObject.of(
                    {role: projector.entity(target) for role, target in arguments.items()}
                ),
            )
        )
    return tuple(result), chosen


def _commit_evidence(event: dict[str, Any], action_events: list[dict[str, Any]]) -> dict[str, Any]:
    action = event["action"]
    for field in ("native_completion", "native_continuation"):
        witness = event.get(field)
        if witness is not None:
            if (
                not isinstance(witness, dict)
                or witness.get("succeeded") is not True
                or witness.get("action_witness_id") != action["action_witness_id"]
            ):
                raise BoundaryError("platform_projection", "invalid_commit_or_continuation_witness")
            return {"kind": field, "evidence": witness, "source_event_ref": semantic_hash(event)}
    finished = [item for item in action_events if item["kind"] == "action_finished"]
    if action.get("native_mechanism") != "direct_ui_commit" or len(finished) != 1:
        raise BoundaryError("platform_projection", "missing_direct_input_commit_evidence")
    return {
        "kind": "direct_ui_action_finished",
        "evidence": finished[0],
        "source_event_ref": semantic_hash(finished[0]),
    }


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class PlatformBundle3SourceAdapter:
    adapter_id = ADAPTER_ID

    def project(self, source: bytes) -> SourceProjection:
        with tempfile.TemporaryDirectory(prefix="stpd-verified-bundle3-") as name:
            directory = Path(name)
            _extract(source, directory)
            verification = HumanSessionBundleV3Verifier().verify(directory)
            if verification.status != "pass" or verification.value is None:
                raise BoundaryError("platform_verifier", "bundle3_verification_failed")
            bundle = verification.value
            return self._project(
                directory,
                source,
                bundle.bundle_content_id,
                dict(bundle.capture_profile),
                dict(bundle.manifest),
            )

    def _project(
        self,
        directory: Path,
        source: bytes,
        content_id: str,
        profile: dict[str, Any],
        manifest: dict[str, Any],
    ) -> SourceProjection:
        raw = directory / "raw"
        canonical = _lines(directory / "export/canonical-transitions.jsonl")
        trace = _lines(raw / "semantic-boundary-trace.jsonl")
        journal = _lines(raw / "run-journal.jsonl")
        invalidations = _lines(raw / "invalidations.jsonl")
        accepted = {
            e["action"]["action_witness_id"]: e for e in trace if e["kind"] == "action_accepted"
        }
        ends = {e["action"]["action_witness_id"]: e for e in trace if e["kind"] in _TERMINATIONS}
        by_witness = {r["action_witness_id"]: r for r in canonical}
        action_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in trace:
            action_events[item["action"]["action_witness_id"]].append(item)
        ledger = [
            {
                "action": e["action"],
                "accepted_ref": semantic_hash(e),
                "disposition": ends[witness]["kind"],
                "disposition_ref": semantic_hash(ends[witness]),
                "canonical_record_id": by_witness.get(witness, {}).get("transition_id"),
            }
            for witness, e in accepted.items()
        ]
        accounting = FrozenObject.of(
            {
                "schema": "stpd/platform-bundle3-accounting-v1",
                "bundle_content_id": content_id,
                "occurrences": ledger,
                "invalidations": invalidations,
                "journal": journal,
                "counts": {
                    "accepted": len(accepted),
                    "canonical": len(canonical),
                    **dict(Counter(e["kind"] for e in ends.values())),
                },
                "human_origin_attestation": manifest["human_origin_attestation"],
            }
        )
        source_hash = hashlib.sha256(source).hexdigest()
        runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in canonical:
            runs[row["run_id"]].append(row)
        records = []
        proofs = {}
        session = manifest["session_id"]
        for native_run, rows in sorted(runs.items()):
            rows.sort(key=lambda row: row["action_sequence"])
            run_id = session + "/" + native_run
            events = [e for e in journal if e.get("run_id") == native_run]
            starts = [e for e in events if e["kind"] == "run_started_native"]
            terminals = [e for e in events if e["kind"] == "run_ended_native"]
            gaps = [
                e
                for e in events
                if e["kind"]
                in {
                    "recording_interrupted",
                    "run_resumed_native",
                    "run_abandoned",
                    "recording_paused",
                    "recording_resumed",
                    "run_ended_unproved",
                    "run_reloaded_native",
                }
            ]
            complete_run = len(starts) == len(terminals) == 1 and not gaps
            if complete_run:
                start, terminal = starts[0], terminals[0]
                accepted_times = [
                    _timestamp(accepted[r["action_witness_id"]]["observed_at"]) for r in rows
                ]
                complete_run = (
                    _timestamp(start["recorded_at"])
                    <= min(accepted_times)
                    <= max(accepted_times)
                    <= _timestamp(terminal["recorded_at"])
                )
            for index, row in enumerate(rows):
                if (
                    row.get("schema_version") != 3
                    or row.get("decision", {}).get("schema_version") != 2
                ):
                    raise BoundaryError("platform_projection", "current_occurrence_schema_required")
                pre = _frame(raw, row["pre_state_ref"])
                post = _frame(raw, row["successor_ref"])
                ref = row.get("execution_semantic_action_space_ref")
                catalog = _frame(raw, ref) if ref is not None else None
                state, projector = _state_and_actions(raw, pre, catalog)
                successor, _ = _state_and_actions(raw, post)
                actions, chosen = _actions(row, pre, catalog, projector)
                event = ends[row["action_witness_id"]]
                commit = _commit_evidence(event, action_events[row["action_witness_id"]])
                terminal_decision = (
                    complete_run
                    and index == len(rows) - 1
                    and post["snapshot"].get("interaction", {}).get("kind") == "game_over"
                )
                decision = row["decision"]
                source_evidence = FrozenObject.of(
                    {
                        "bundle_content_id": content_id,
                        "platform_revision": SUPPORTED_PLATFORM_CONTRACT_REVISION,
                        "canonical_ref": semantic_hash(row),
                        "commit": commit,
                        "proof_ref": semantic_hash(event),
                        "action_sequence": row["action_sequence"],
                        "pre_frame_sha256": row["pre_state_ref"]["content_sha256"],
                        "successor_frame_sha256": row["successor_ref"]["content_sha256"],
                        "human_observation_ref": event.get("human_observation_ref"),
                        "catalog_ref": ref,
                        "native_mechanism": row["native_mechanism"],
                        "profile_sha256": manifest["capture_profile_sha256"],
                        "native_run_id": native_run,
                        "session_id": session,
                        "recording_identity": _load(raw / "recording-manifest.json"),
                        "terminal_event_ref": semantic_hash(terminals[0])
                        if terminal_decision
                        else None,
                    }
                )
                # Commit may be a native completion, PlayerChoice continuation, or
                # direct-input proof. Preserve that exact proof rather than synthesize
                # a GameAction completion for a selector.
                provenance = EvidenceLink(
                    source_hash,
                    row["transition_id"],
                    pre["snapshot"]["session"]["environment_fingerprint"],
                    "platform_verified",
                    semantic_hash({"adapter": self.adapter_id, "content_id": content_id}),
                    decision["causal_root_id"],
                    ref["content_sha256"] if ref else row["pre_state_ref"]["content_sha256"],
                    semantic_hash(commit["evidence"]),
                    row["successor_ref"]["content_sha256"],
                )
                records.append(
                    ResearchTransitionV2(
                        run_id,
                        run_id,
                        index,
                        "gameplay",
                        decision["family"],
                        decision["surface"],
                        state,
                        actions,
                        True,
                        len(actions),
                        chosen,
                        successor,
                        bool(terminal_decision),
                        "committed",
                        provenance,
                        FrozenObject.of(decision),
                        source_evidence,
                        row["action_space_authority"],
                    )
                )
            if complete_run and records[-1].terminal:
                proofs[run_id] = {
                    "started_ref": semantic_hash(starts[0]),
                    "terminal_ref": semantic_hash(terminals[0]),
                    "record_count": len(rows),
                }
        return SourceProjection(
            self.adapter_id,
            source_hash,
            "platform_verified",
            FrozenObject.of(proofs),
            tuple(records),
            source,
            accounting,
        )
