"""Verify generic native-logical source recordings without Human or causal promotion."""
from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

from .core import VerificationFinding, VerificationResult, VerifierDescriptor

BUNDLE_SCHEMA = "sts2.annotator/source-session-bundle-1"
MANIFEST_SCHEMA = "sts2.annotator/source-session-manifest-1"
PROFILE_SCHEMA = "sts2.annotator/source-capture-profile-1"
PROFILE_ID = "native-logical-source-v1"
TYPE_ID = "source-session-bundle-v1"
STREAMS = {
    "source-segments.jsonl": "sts2.annotator/source-segment-1",
    "source-boundaries.jsonl": "sts2.annotator/source-boundary-1",
    "public-observations.jsonl": "sts2.annotator/public-observation-1",
    "native-input-witnesses.jsonl": "sts2.annotator/native-input-witness-1",
}
SOURCE_KINDS = {"declared_human", "agent_native_ui", "agent_protocol", "unknown"}
NON_CLAIMS = (
    "not_machine_proof_of_human_origin", "not_native_coverage_qualified",
    "not_causal_transition_proof", "not_research_admission", "not_g2_v1_approved",
)
_SHA = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_INDEX = re.compile(r"^(?:0|[1-9][0-9]*)$")
_ACTION_FIELDS = {"action_id", "kind", "verb", "label", "subject_referent_id", "arguments", "effect_domain"}


class SourceSessionError(ValueError):
    def __init__(self, code: str, detail: str = "", path: str | None = None):
        super().__init__(detail or code)
        self.code, self.path = code, path


def _require(condition: bool, code: str, detail: str = "") -> None:
    if not condition:
        raise SourceSessionError(code, detail)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict), "source_object_required")
    return value


def _identifier(value: Any) -> str:
    _require(isinstance(value, str) and value not in {".", ".."}
             and _IDENTIFIER.fullmatch(value) is not None, "source_identifier_invalid")
    return value


def _clock(value: Any) -> tuple[str, int]:
    value = _object(value)
    _require(set(value) == {"stream_generation", "publication_index"}, "source_clock_fields_invalid")
    generation = _identifier(value["stream_generation"])
    index = value["publication_index"]
    _require(isinstance(index, str) and _INDEX.fullmatch(index) is not None, "source_clock_invalid")
    number = int(index)
    _require(number <= (1 << 64) - 1, "source_clock_overflow")
    return generation, number


def _json(data: bytes) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            _require(key not in result, "source_json_duplicate_key")
            result[key] = value
        return result

    def constant(_: str) -> Any:
        raise SourceSessionError("source_json_nonfinite")

    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)


def _inventory(directory: Path) -> dict[str, str]:
    _require(directory.is_dir() and not directory.is_symlink(), "source_directory_missing_or_link")
    files: dict[str, str] = {}
    for item in directory.rglob("*"):
        _require(not item.is_symlink(), "source_symlink_forbidden")
        if item.is_file():
            relative = item.relative_to(directory).as_posix()
            _require(relative not in files, "source_inventory_duplicate")
            files[relative] = _sha(item.read_bytes())
    return files


def _catalog_digest(actions: Any) -> str:
    _require(isinstance(actions, list) and len(actions) <= 65536, "source_catalog_array_invalid")
    digest = hashlib.sha256()
    digest.update(b"sts2.native-logical.catalog.v1\0")
    digest.update(struct.pack(">I", len(actions)))
    ids: set[str] = set()

    def text(value: Any) -> None:
        _require(isinstance(value, str), "source_catalog_string_required")
        try:
            encoded = value.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise SourceSessionError("source_catalog_unicode_invalid") from error
        _require(len(encoded) <= 0xFFFFFFFF, "source_catalog_length_overflow")
        digest.update(struct.pack(">I", len(encoded)))
        digest.update(encoded)

    for item in actions:
        item = _object(item)
        _require(set(item) == _ACTION_FIELDS, "source_catalog_fields_invalid")
        action_id = item["action_id"]
        _require(isinstance(action_id, str) and action_id not in ids
                 and item["kind"] == "native_input",
                 "source_catalog_identity_invalid")
        ids.add(action_id)
        for field in ("action_id", "kind", "verb", "label"):
            text(item[field])
        subject = item["subject_referent_id"]
        digest.update(b"\0" if subject is None else b"\1")
        if subject is not None:
            text(subject)
        arguments = item["arguments"]
        _require(isinstance(arguments, list), "source_catalog_arguments_invalid")
        digest.update(struct.pack(">I", len(arguments)))
        roles: set[str] = set()
        for argument in arguments:
            argument = _object(argument)
            _require(set(argument) == {"role", "referent_id"}
                     and isinstance(argument["role"], str) and argument["role"] not in roles,
                     "source_catalog_argument_invalid")
            roles.add(argument["role"])
            text(argument["role"])
            text(argument["referent_id"])
        text(item["effect_domain"])
    return digest.hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _capture_shape(body: dict[str, Any], capture: Mapping[str, Any], require_full: bool = False,
                   actions: list[dict[str, Any]] | None = None) -> bool:
    """Verify the captured public projection; this does not evaluate native legality."""
    domains = ["persistent", "interaction", "referents", "catalog"]

    def field(value: Any, name: str) -> Any:
        _require(isinstance(value, dict) and name in value, "source_capture_shape_invalid")
        return value[name]

    def text(value: Any, name: str) -> str:
        result = field(value, name)
        _require(isinstance(result, str), "source_capture_shape_invalid")
        return result

    def array(value: Any, name: str) -> list[Any]:
        result = field(value, name)
        _require(isinstance(result, list), "source_capture_shape_invalid")
        return result

    def strings(value: Any, name: str) -> list[str]:
        result = array(value, name)
        _require(all(isinstance(item, str) for item in result), "source_capture_shape_invalid")
        return result

    def boolean(value: Any, name: str) -> bool:
        result = field(value, name)
        _require(type(result) is bool, "source_capture_shape_invalid")
        return result

    def nullable(value: Any, name: str, kind: type) -> None:
        _require(isinstance(value, dict) and (name not in value or value[name] is None or type(value[name]) is kind),
                 "source_capture_shape_invalid")

    completeness = _object(field(body, "completeness"))
    included, missing = strings(completeness, "included"), strings(completeness, "missing")
    _require(included == [item for item in domains if item in included]
             and missing == [item for item in domains if item not in included], "source_capture_scope_completeness_invalid")
    full = boolean(completeness, "full_reference_complete")
    _require(text(completeness, "status") == ("partial" if missing else "complete")
             and full == (not missing), "source_capture_scope_completeness_invalid")
    persistent = field(body, "persistent")
    if "persistent" not in included:
        _require(persistent is None, "source_capture_shape_invalid")
    elif persistent is not None:
        text(persistent, "content_schema")
        _require(field(persistent, "content") is not None, "source_capture_shape_invalid")
    interaction = field(body, "interaction")
    if "interaction" not in included:
        _require(interaction is None, "source_capture_shape_invalid")
    else:
        for name in ("interaction_id", "kind", "stage", "content_schema"):
            text(interaction, name)
        nullable(interaction, "prompt", str)
        content = field(interaction, "content")
        text(field(content, "surface"), "kind")
        text(field(content, "context"), "kind")
        for capability in array(interaction, "capabilities"):
            text(capability, "verb")
            text(capability, "availability_basis")
            nullable(capability, "subject_role", str)
            for argument in array(capability, "arguments"):
                text(argument, "role")
                boolean(argument, "required")
    referents: set[str] = set()
    references = array(body, "referents")
    _require("referents" in included or not references, "source_capture_shape_invalid")
    for reference in references:
        referent_id = text(reference, "referent_id")
        _require(referent_id not in referents, "source_capture_referent_identity_invalid")
        referents.add(referent_id)
        text(reference, "role")
        text(reference, "kind")
        nullable(reference, "label", str)
        nullable(reference, "properties_schema", str)
        state = field(reference, "state")
        boolean(state, "visible")
        text(state, "observation_basis")
        for name in ("enabled", "selected", "focused"):
            nullable(state, name, bool)
    owner = field(body, "owner_occurrence")
    for name in ("owner_id", "occurrence_id", "binding_revision"):
        text(owner, name)
    nullable(owner, "focus_referent_id", str)
    nullable(owner, "focus_occurrence", str)
    descriptor = field(body, "catalog")
    _require(all(text(descriptor, key) == capture[key] for key in ("snapshot_id", "scope_id", "stream_generation")),
             "source_capture_scope_mismatch")
    text(descriptor, "catalog_ref")
    text(descriptor, "ordering_semantics")
    methods = strings(descriptor, "access_methods")
    if "catalog" in included:
        _require(text(descriptor, "status") == "complete" and type(field(descriptor, "total_count")) is int
                 and 0 <= descriptor["total_count"] <= 65536 and _SHA.fullmatch(text(descriptor, "digest")) is not None,
                 "source_capture_catalog_shape_invalid")
    else:
        _require(text(descriptor, "status") == "not_captured" and field(descriptor, "total_count") is None
                 and field(descriptor, "digest") is None and not methods, "source_capture_catalog_shape_invalid")
    _require(not require_full or full, "source_capture_full_reference_incomplete")
    if full and actions is not None:
        _require(all((action["subject_referent_id"] is None or action["subject_referent_id"] in referents)
                     and all(argument["referent_id"] in referents for argument in action["arguments"])
                     for action in actions), "source_catalog_operand_not_in_capture")
    return full


@dataclass(frozen=True)
class SourceSessionBundle:
    directory: Path
    manifest: Mapping[str, Any]
    recording: Mapping[str, Any]
    observations: tuple[Mapping[str, Any], ...]
    inputs: tuple[Mapping[str, Any], ...]
    segments: tuple[Mapping[str, Any], ...]
    content_id: str
    gap_count: int

    @property
    def human_origin_verified(self) -> bool:
        return False


DESCRIPTOR = VerifierDescriptor(TYPE_ID, BUNDLE_SCHEMA, 1, SourceSessionBundle)


class SourceSessionBundleVerifier:
    descriptor = DESCRIPTOR

    def verify(self, source: str | Path, expected: Mapping[str, object] | None = None) -> VerificationResult[SourceSessionBundle]:
        directory = Path(source).absolute()
        try:
            value = self._verify(directory, expected)
            return VerificationResult(DESCRIPTOR, "pass", directory, value)
        except SourceSessionError as error:
            return VerificationResult(DESCRIPTOR, "fail", directory,
                findings=(VerificationFinding(error.code, str(error), error.path),))
        except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
            return VerificationResult(DESCRIPTOR, "fail", directory,
                findings=(VerificationFinding("source_bundle_invalid", type(error).__name__),))

    def _verify(self, directory: Path, expected: Mapping[str, object] | None) -> SourceSessionBundle:
        inventory = _inventory(directory)
        checksum_path = directory / "checksums.sha256"
        declared: dict[str, str] = {}
        for line in checksum_path.read_text(encoding="utf-8").splitlines():
            _require(len(line) >= 67 and line[64:66] == "  " and _SHA.fullmatch(line[:64]) is not None,
                     "source_checksum_invalid")
            relative = line[66:]
            path = PurePosixPath(relative)
            _require(not path.is_absolute() and "\\" not in relative
                     and all(part not in {"", ".", ".."} for part in relative.split("/"))
                     and relative not in declared and relative != "checksums.sha256", "source_checksum_path_invalid")
            declared[relative] = line[:64]
        _require(declared == {key: value for key, value in inventory.items() if key != "checksums.sha256"},
                 "source_checksum_inventory_mismatch")
        bundle = _object(_json((directory / "source-session-bundle-manifest.json").read_bytes()))
        identity_bytes = (directory / "content-identity.json").read_bytes()
        identity = _object(_json(identity_bytes))
        _require(set(bundle) == {"schema_version", "schema", "bundle_content_id", "session_id", "timeline_id",
                 "capture_profile_id", "capture_profile_sha256", "worker_id", "campaign_id", "source_kinds",
                 "human_origin_attested", "audit_status", "observation_count", "input_count", "gap_count",
                 "content_identity", "non_claims"}
                 and set(identity) == {"schema", "session_id", "timeline_id", "capture_profile_id", "worker_id",
                 "campaign_id", "packer_source_revision", "raw_file_sha256", "export_file_sha256",
                 "audit_sha256", "source_kinds", "human_origin_attested", "non_claims"}, "source_bundle_fields_invalid")
        _require(bundle.get("schema") == BUNDLE_SCHEMA and bundle.get("schema_version") == 1
                 and bundle.get("bundle_content_id") == _sha(identity_bytes)
                 and bundle.get("content_identity") == identity, "source_bundle_identity_invalid")
        _require(bundle.get("human_origin_attested") is False and identity.get("human_origin_attested") is False,
                 "source_human_promotion_forbidden")
        _require(bundle.get("audit_status") == "pass"
                 and bundle.get("non_claims") == list(NON_CLAIMS)
                 and identity.get("non_claims") == list(NON_CLAIMS), "source_bundle_nonclaims_invalid")
        for key in ("session_id", "timeline_id", "worker_id", "campaign_id"):
            _identifier(bundle.get(key))
            _require(bundle[key] == identity.get(key), "source_identity_metadata_mismatch")
        _require(identity.get("schema") == BUNDLE_SCHEMA and identity.get("capture_profile_id") == PROFILE_ID
                 and re.fullmatch(r"[0-9a-fA-F]{40}", str(identity.get("packer_source_revision"))) is not None,
                 "source_packer_identity_invalid")
        raw = directory / "raw"
        export = directory / "export"
        raw_inventory = _inventory(raw)
        export_inventory = _inventory(export)
        _require(raw_inventory == identity.get("raw_file_sha256")
                 and export_inventory == identity.get("export_file_sha256"), "source_raw_export_inventory_mismatch")
        expected_export = {key: value for key, value in raw_inventory.items()
                           if key in STREAMS or key.startswith(("public-captures/", "public-catalogs/"))}
        _require(expected_export == export_inventory, "source_raw_export_equivalence_invalid")
        audit_bytes = (directory / "audit/source-audit.json").read_bytes()
        audit = _object(_json(audit_bytes))
        _require(identity.get("audit_sha256") == _sha(audit_bytes)
                 and audit.get("schema") == "sts2.annotator/source-session-audit-1"
                 and audit.get("status") == "pass" and audit.get("errors") == []
                 and audit.get("non_claims") == list(NON_CLAIMS), "source_producer_audit_invalid")
        recording, observations, inputs, segments, gaps, kinds = _verify_raw(raw, bundle)
        _require(bundle.get("source_kinds") == kinds and identity.get("source_kinds") == kinds
                 and audit.get("source_kinds") == kinds and audit.get("gap_count") == gaps
                 and audit.get("observation_count") == len(observations)
                 and audit.get("input_count") == len(inputs) and audit.get("session_id") == recording["session_id"],
                 "source_audit_summary_mismatch")
        allowed = {"source-session-bundle-manifest.json", "content-identity.json", "checksums.sha256",
                   "audit/source-audit.json"}
        _require(set(inventory) == allowed | {"raw/" + key for key in raw_inventory}
                 | {"export/" + key for key in export_inventory}, "source_bundle_extra_file")
        if expected is not None:
            for key, value in expected.items():
                _require(key in bundle and bundle[key] == value, "source_expected_identity_mismatch", key)
        return SourceSessionBundle(directory, _freeze(bundle), _freeze(recording),
            tuple(_freeze(row) for row in observations), tuple(_freeze(row) for row in inputs),
            tuple(_freeze(row) for row in segments), bundle["bundle_content_id"], gaps)


def _verify_raw(raw: Path, bundle: Mapping[str, Any]) -> tuple[
        dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int, list[str]]:
    _require(not (raw / "source-accounting-failure.json").exists(), "source_accounting_failed")
    recording = _object(_json((raw / "recording-manifest.json").read_bytes()))
    profile_bytes = (raw / "capture-profile.json").read_bytes()
    profile = _object(_json(profile_bytes))
    _require(recording.get("schema") == MANIFEST_SCHEMA and recording.get("schema_version") == 1
             and recording.get("source_schema_version") == 1
             and recording.get("capture_profile_id") == PROFILE_ID
             and recording.get("capture_profile_sha256") == _sha(profile_bytes)
             and recording.get("decision_schema_version") is None
             and recording.get("text_input_schema_version") is None, "source_recording_manifest_invalid")
    _require(recording["session_id"] == bundle["session_id"]
             and recording["timeline_id"] == bundle["timeline_id"]
             and bundle.get("capture_profile_id") == PROFILE_ID
             and bundle.get("capture_profile_sha256") == _sha(profile_bytes), "source_manifest_bundle_mismatch")
    _identifier(recording["session_id"])
    _identifier(recording["timeline_id"])
    _require(profile.get("schema") == PROFILE_SCHEMA and profile.get("profile_id") == PROFILE_ID
             and profile.get("input_profile") == "native-logical-v1"
             and profile.get("eager_scope") == ["persistent", "interaction", "referents", "catalog"],
             "source_profile_invalid")
    scope = _identifier(profile.get("scope_id"))
    coverage = _object(profile.get("seam_coverage"))
    _require(len(coverage) <= 256, "source_coverage_limit")
    for seam, value in coverage.items():
        _identifier(seam)
        value = _object(value)
        _identifier(value.get("version"))
        _require(set(value) == {"version", "coverage"}
                 and value["coverage"] in {"complete_at_seam", "sampled", "unsupported"}, "source_coverage_invalid")
    limits = _object(profile.get("limits"))
    upper = {"max_capture_bytes": 64 * 1024 * 1024, "max_payload_bytes": 512 * 1024 * 1024,
             "max_row_bytes": 1024 * 1024, "max_rows_per_stream": 65536,
             "max_bytes_per_stream": 64 * 1024 * 1024, "max_segments": 256, "max_pending_inputs": 128}
    _require(set(limits) == set(upper)
             and all(type(limits[key]) is int and 1 <= limits[key] <= value for key, value in upper.items())
             and limits["max_row_bytes"] >= 512
             and limits["max_payload_bytes"] >= limits["max_capture_bytes"]
             and limits["max_bytes_per_stream"] >= limits["max_row_bytes"], "source_limits_invalid")
    environment = _object(recording.get("source_environment"))
    _identifier(environment.get("runtime_instance_id"))
    _identifier(environment.get("environment_fingerprint"))
    for component in ("connector", "annotator"):
        artifact = _object(environment.get(component))
        _require(_SHA.fullmatch(str(artifact.get("sha256"))) is not None
                 and _SHA.fullmatch(str(artifact.get("source_digest_sha256"))) is not None
                 and re.fullmatch(r"[0-9a-fA-F]{40}", str(artifact.get("source_revision"))) is not None,
                 "source_environment_artifact_invalid")
    game = _object(environment.get("game"))
    _require(_SHA.fullmatch(str(game.get("main_assembly_sha256"))) is not None, "source_environment_game_identity_invalid")
    receipt = _object(_json((raw / "source-close-receipt.json").read_bytes()))
    _require(receipt.get("schema") == "sts2.annotator/source-session-close-1"
             and receipt.get("status") == "closed" and receipt.get("accounting_complete") is True
             and receipt.get("session_id") == recording["session_id"]
             and receipt.get("timeline_id") == recording["timeline_id"], "source_close_receipt_invalid")
    generation, close_index = _clock(receipt.get("boundary_clock"))
    rows: dict[str, list[dict[str, Any]]] = {}
    _require(set(_object(receipt.get("counts"))) == set(STREAMS)
             and set(_object(receipt.get("stream_sha256"))) == set(STREAMS), "source_close_stream_inventory_invalid")
    for file, schema in STREAMS.items():
        data = (raw / file).read_bytes()
        _require(len(data) <= limits["max_bytes_per_stream"]
                 and (not data or data.endswith(b"\n")), "source_stream_bytes_invalid")
        decoded: list[dict[str, Any]] = []
        for sequence, line in enumerate(data.splitlines(), 1):
            _require(line.strip() and len(line) + 1 <= limits["max_row_bytes"], "source_stream_row_invalid")
            row = _object(_json(line))
            _require(row.get("schema") == schema and type(row.get("sequence")) is int
                     and row["sequence"] == sequence
                     and row.get("session_id") == recording["session_id"]
                     and row.get("timeline_id") == recording["timeline_id"], "source_stream_identity_or_sequence_invalid")
            decoded.append(row)
        _require(len(decoded) <= limits["max_rows_per_stream"]
                 and receipt["counts"][file] == len(decoded)
                 and receipt["stream_sha256"][file] == _sha(data), "source_stream_close_hash_mismatch")
        rows[file] = decoded
    segments = rows["source-segments.jsonl"]
    _require(0 < len(segments) <= limits["max_segments"], "source_segments_invalid")
    segment_map: dict[str, dict[str, Any]] = {}
    previous: dict[str, Any] | None = None
    kinds: set[str] = set()
    for segment in segments:
        segment_id = _identifier(segment.get("segment_id"))
        clock_generation, index = _clock(segment.get("boundary_clock"))
        declaration = _object(segment.get("declaration"))
        _require(set(declaration) == {"source_kind", "actor_id", "declaration_id", "machine_verifiable"}
                 and declaration.get("source_kind") in SOURCE_KINDS
                 and declaration.get("machine_verifiable") is False, "source_declaration_invalid")
        _identifier(declaration.get("actor_id"))
        _identifier(declaration.get("declaration_id"))
        _require(segment_id not in segment_map and clock_generation == generation and index <= close_index
                 and segment.get("previous_segment_id") == (previous["segment_id"] if previous else None)
                 and (previous is None or index >= _clock(previous["boundary_clock"])[1]), "source_segment_binding_invalid")
        segment_map[segment_id] = segment
        kinds.add(declaration["source_kind"])
        previous = segment
    boundaries = rows["source-boundaries.jsonl"]
    _require(boundaries and boundaries[-1].get("kind") == "close"
             and sum(row.get("kind") == "close" for row in boundaries) == 1, "source_close_boundary_missing")
    gaps, last_boundary = 0, _clock(segments[0]["boundary_clock"])[1]
    paused: dict[str, Any] | None = None
    intervals: list[tuple[int, int]] = []
    for boundary in boundaries:
        clock_generation, index = _clock(boundary.get("clock"))
        _require(boundary.get("segment_id") in segment_map and clock_generation == generation
                 and last_boundary <= index <= close_index, "source_boundary_clock_invalid")
        kind = boundary.get("kind")
        if kind == "pause" and paused is None and boundary.get("gap_after") is None and boundary.get("gap_reason") is None:
            paused = boundary["clock"]
        elif kind == "resume" and paused is not None and boundary.get("gap_after") == paused and boundary.get("gap_reason") == "recording_paused":
            begin = _clock(paused)[1]
            intervals.append((begin, index))
            gaps += int(index > begin)
            paused = None
        elif kind == "close" and boundary["clock"] == receipt["boundary_clock"]:
            if paused is not None:
                begin = _clock(paused)[1]
                intervals.append((begin, index))
                gaps += int(index > begin)
        else:
            raise SourceSessionError("source_boundary_transition_invalid")
        last_boundary = index
    for segment in segments[1:]:
        index = _clock(segment["boundary_clock"])[1]
        candidates = [row for row in boundaries if row["kind"] == "pause" and _clock(row["clock"])[1] <= index]
        resumes = [row for row in boundaries if row["kind"] == "resume" and _clock(row["clock"])[1] < index]
        _require(bool(candidates) and (not resumes or resumes[-1]["sequence"] < candidates[-1]["sequence"]),
                 "source_change_not_paused")

    capture_ids: dict[str, dict[str, Any]] = {}
    catalog_ids: dict[str, dict[str, Any]] = {}
    catalog_actions: dict[str, list[dict[str, Any]]] = {}
    observed_payloads: set[str] = set()

    def blob(reference: dict[str, Any], family: str, hash_key: str) -> bytes:
        digest = reference.get(hash_key)
        _require(isinstance(digest, str) and _SHA.fullmatch(digest) is not None, "source_blob_hash_invalid")
        relative = f"{family}/sha256/{digest[:2]}/{digest}.bin"
        _require(reference.get("payload_ref") == relative and type(reference.get("byte_count")) is int
                 and 1 <= reference["byte_count"] <= limits["max_capture_bytes"], "source_blob_reference_invalid")
        payload = (raw / relative).read_bytes()
        _require(len(payload) == reference["byte_count"] and _sha(payload) == digest, "source_blob_missing_or_changed")
        observed_payloads.add(relative)
        return payload

    def join(capture: Any, catalog: Any, require_full: bool = False) -> None:
        body: dict[str, Any] | None = None
        if capture is not None:
            capture = _object(capture)
            _identifier(capture.get("capture_id"))
            _identifier(capture.get("snapshot_id"))
            previous_capture = capture_ids.get(capture["capture_id"])
            _require(previous_capture is None or previous_capture == capture, "source_capture_identity_conflict")
            capture_ids[capture["capture_id"]] = capture
            body = _object(_json(blob(capture, "public-captures", "sha256")))
            policy = _object(body.get("information_policy"))
            session = _object(body.get("session"))
            _require(body.get("schema") == "sts2.player-environment/native-logical-observation-1"
                     and body.get("input_profile") == "native-logical-v1"
                     and body.get("snapshot_id") == capture["snapshot_id"]
                     and body.get("protocol_version") == environment.get("player_environment_protocol")
                     and session.get("runtime_instance_id") == environment["runtime_instance_id"]
                     and session.get("environment_fingerprint") == environment["environment_fingerprint"]
                     and policy.get("includes_hidden_information") is False
                     and capture.get("scope_id") == scope and capture.get("stream_generation") == generation,
                     "source_capture_identity_invalid")
            _capture_shape(body, capture, require_full)
        if catalog is None:
            return
        catalog = _object(catalog)
        _identifier(catalog.get("catalog_ref"))
        _require(capture is not None and all(capture.get(key) == catalog.get(key)
                 for key in ("snapshot_id", "scope_id", "stream_generation")), "source_capture_catalog_join_invalid")
        previous_catalog = catalog_ids.get(catalog["catalog_ref"])
        _require(previous_catalog is None or previous_catalog == catalog, "source_catalog_identity_conflict")
        catalog_ids[catalog["catalog_ref"]] = catalog
        actions = _json(blob(catalog, "public-catalogs", "payload_sha256"))
        structural = _catalog_digest(actions)
        _require(type(catalog.get("total_count")) is int and catalog["total_count"] == len(actions)
                 and catalog.get("structural_digest") == structural, "source_catalog_digest_mismatch")
        descriptor = _object(body.get("catalog") if body is not None else None)
        _require(descriptor.get("status") == "complete"
                 and all(descriptor.get(key) == catalog.get(key) for key in
                         ("catalog_ref", "snapshot_id", "scope_id", "stream_generation", "total_count"))
                 and descriptor.get("digest") == structural, "source_capture_catalog_descriptor_mismatch")
        _capture_shape(body, capture, require_full, actions)
        catalog_actions[catalog["catalog_ref"]] = actions

    observations = rows["public-observations.jsonl"]
    last_observation = _clock(segments[0]["boundary_clock"])[1]
    for observation in observations:
        clock_generation, index = _clock(observation.get("clock"))
        _require(clock_generation == generation and last_observation < index <= close_index
                 and observation.get("scope_id") == scope
                 and observation.get("segment_id") in segment_map
                 and not any(begin < index <= end for begin, end in intervals), "source_observation_clock_invalid")
        segment = [value for value in segments if _clock(value["boundary_clock"])[1] < index][-1]
        _require(segment["segment_id"] == observation["segment_id"], "source_observation_relabelled")
        _identifier(observation.get("source_seam"))
        _identifier(observation.get("phase"))
        _clock({"stream_generation": generation, "publication_index": observation.get("source_index")})
        join(observation.get("capture"), observation.get("catalog"), observation.get("completeness") == "complete")
        capture = observation.get("capture")
        completeness = observation.get("completeness")
        _require(completeness in {"complete", "partial", "capacity_exceeded", "failed"}
                 and (capture is None or capture["snapshot_id"] == observation.get("snapshot_id")),
                 "source_observation_completeness_invalid")
        if completeness == "complete":
            _require(capture is not None and observation.get("catalog") is not None
                     and observation.get("missing_reason") is None, "source_full_reference_incomplete")
        else:
            _require(isinstance(observation.get("missing_reason"), str) and bool(observation["missing_reason"]),
                     "source_missing_reason_required")
        gap_after = observation.get("gap_after_index")
        if gap_after is not None:
            _require(_clock({"stream_generation": generation, "publication_index": gap_after})[1] < index,
                     "source_gap_interval_invalid")
        gaps += int(observation.get("missing_reason") is not None or gap_after is not None)
        last_observation = index
    inputs = rows["native-input-witnesses.jsonl"]
    input_ids: set[str] = set()
    for item in inputs:
        input_id = _identifier(item.get("input_id"))
        clock_generation, index = _clock(item.get("pre_clock"))
        _require(input_id not in input_ids and item.get("segment_id") in segment_map
                 and clock_generation == generation and index <= close_index
                 and not any(begin < index <= end for begin, end in intervals), "source_input_identity_invalid")
        input_ids.add(input_id)
        segment = segment_map[item["segment_id"]]
        segment_index = segments.index(segment)
        _require(index >= _clock(segment["boundary_clock"])[1]
                 and (segment_index + 1 == len(segments)
                      or index <= _clock(segments[segment_index + 1]["boundary_clock"])[1]), "source_input_relabelled")
        join(item.get("pre_capture"), item.get("catalog"))
        outcome = _object(item.get("outcome"))
        _identifier(outcome.get("native_mechanism"))
        mapping = outcome.get("mapping_status")
        count = outcome.get("match_count")
        _require(mapping in {"exact", "unmapped", "ambiguous", "capture_missing"}
                 and type(count) is int and 0 <= count <= 65536
                 and outcome.get("delivery") in {"rejected_before_input", "delivered", "partially_delivered", "unknown"},
                 "source_input_outcome_invalid")
        selected = outcome.get("selected_action")
        if mapping == "exact":
            _require(count == 1 and isinstance(selected, dict) and item.get("pre_capture") is not None
                     and item.get("catalog") is not None, "source_exact_input_basis_missing")
            join(item.get("pre_capture"), item.get("catalog"), require_full=True)
            actions = catalog_actions[item["catalog"]["catalog_ref"]]
            _require(sum(action == selected for action in actions) == 1, "source_selected_action_not_in_original_catalog")
        else:
            _require(selected is None and (mapping != "ambiguous" or count >= 2)
                     and (mapping not in {"unmapped", "capture_missing"} or count == 0), "source_input_mapping_invalid")
        _require(item.get("pre_capture") is not None or mapping == "capture_missing", "source_input_capture_missing")
    for file in ("semantic-boundary-trace.jsonl", "canonical-transitions.jsonl", "native-semantic-discriminator.jsonl",
                 "invalidations.jsonl"):
        _require((raw / file).read_bytes() == b"", "source_session_contains_human_semantics")
    raw_inventory = _inventory(raw)
    allowed_raw = set(STREAMS) | {
        "recording-manifest.json", "capture-profile.json", "source-close-receipt.json",
        "source-coverage.json", "source-accounting-failure.json", "performance-profile.json",
        "recording-owner.lock", "run-journal.jsonl", "invalidations.jsonl",
        "semantic-boundary-trace.jsonl", "canonical-transitions.jsonl", "native-semantic-discriminator.jsonl",
    }
    _require(all(key in allowed_raw or re.fullmatch(
        r"(public-captures|public-catalogs)/sha256/[0-9a-f]{2}/[0-9a-f]{64}\.bin", key) is not None
        for key in raw_inventory), "source_unexpected_raw_file")
    payload_bytes = sum((raw / key).stat().st_size for key in raw_inventory
                        if key.startswith(("public-captures/", "public-catalogs/")))
    _require(payload_bytes <= limits["max_payload_bytes"], "source_payload_total_limit")
    _require(receipt.get("gap_count") == gaps and receipt.get("source_kinds") == sorted(kinds)
             and bundle.get("gap_count") == gaps and bundle.get("observation_count") == len(observations)
             and bundle.get("input_count") == len(inputs), "source_close_summary_mismatch")
    return recording, observations, inputs, segments, gaps, sorted(kinds)


def verify_source_session_bundle(source: str | Path,
                                expected: Mapping[str, object] | None = None) -> VerificationResult[SourceSessionBundle]:
    return SourceSessionBundleVerifier().verify(source, expected)
