"""Independent verification of original native Source V2 epochs and durable ranges."""
from __future__ import annotations
import re
from datetime import datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Mapping
from .core import VerificationFinding, VerificationResult, VerifierDescriptor
from .source_session_bundle import (SourceSessionError, SOURCE_KINDS, _require, _sha, _object, _identifier,
    _json, _inventory, _catalog_digest, _freeze, _capture_shape, _SHA, _INDEX)
BUNDLE_SCHEMA = "sts2.annotator/source-session-bundle-2"
MANIFEST_SCHEMA = "sts2.annotator/source-session-manifest-2"
PROFILE_SCHEMA = "sts2.annotator/source-capture-profile-2"
PROFILE_ID = "native-logical-source-v2"
PUBLICATION_PROFILE_ID = "native-logical-publication-profile-v1"
PUBLICATION_PROFILE_SHA = "c060cfd354c6702e10711e6329848836ec133f2117841750e317b6ab27b244cf"
TYPE_ID = "source-session-bundle-v2"
STREAMS = {
    "source-attachment-epochs.jsonl": "sts2.annotator/source-attachment-epoch-2",
    "source-segments.jsonl": "sts2.annotator/source-segment-2",
    "source-boundaries.jsonl": "sts2.annotator/source-boundary-2",
    "public-observations.jsonl": "sts2.annotator/public-observation-2",
    "native-input-witnesses.jsonl": "sts2.annotator/native-input-witness-2",
}
NON_CLAIMS = ("not_machine_proof_of_human_origin", "not_native_coverage_qualified", "not_causal_transition_proof",
    "not_research_admission", "not_non_interference_qualified", "not_g2_v1_approved")
DELIVERIES = {"rejected_before_input", "delivered", "partially_delivered", "unknown"}

def _integer(value: Any, maximum: int = (1 << 63) - 1) -> int:
    _require(type(value) is int and 0 <= value <= maximum, "source_integer_invalid")
    return value

_TIMESTAMP = re.compile(r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"(?:T(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})(?::(?P<second>[0-9]{2})(?:\.[0-9]{1,16})?)?"
    r"(?P<zone>Z|[+-][0-9]{2}:[0-9]{2})?)?")

def _timestamp(value: Any) -> str:
    _require(isinstance(value, str), "source_timestamp_invalid")
    match = _TIMESTAMP.fullmatch(value)
    _require(match is not None, "source_timestamp_invalid")
    try:
        parsed = datetime(int(match["year"]), int(match["month"]), int(match["day"]),
                 int(match["hour"] or 0), int(match["minute"] or 0), int(match["second"] or 0))
        zone = match["zone"]
        if zone is not None and zone != "Z":
            hours, minutes = int(zone[1:3]), int(zone[4:6])
            _require(hours <= 14 and minutes < 60 and (hours != 14 or minutes == 0), "source_timestamp_invalid")
            offset = timedelta(hours=hours, minutes=minutes)
            parsed - offset if zone[0] == "+" else parsed + offset
    except (ValueError, OverflowError):
        _require(False, "source_timestamp_invalid")
    return value

def _index(value: Any) -> int:
    _require(isinstance(value, str) and _INDEX.fullmatch(value) is not None and int(value) <= (1 << 64) - 1,
             "source_clock_invalid")
    return int(value)

def _text(value: Any, maximum: int = 128) -> str:
    _require(isinstance(value, str), "source_text_required")
    _require(len(value.encode("utf-8", errors="strict")) <= maximum, "source_text_capacity")
    return value

def _transition(value: Any) -> dict[str, Any]:
    value = _object(value)
    _require(set(value) == {"witness_id", "kind", "mechanism", "previous_game_continuity_id", "game_continuity_id",
                            "start_provenance", "graceful", "victory"}, "source_native_transition_fields_invalid")
    _identifier(value["witness_id"]); _text(value["mechanism"])
    previous, current, kind = value["previous_game_continuity_id"], value["game_continuity_id"], value["kind"]
    for item in (previous, current):
        if item is not None: _identifier(item)
    _require(kind in {"setup_handoff", "launch", "terminal", "cleanup"}, "source_native_transition_kind_invalid")
    if kind in {"setup_handoff", "launch"}:
        _require(value["start_provenance"] in {"new", "saved", "unknown"} and current is not None
                 and value["graceful"] is None and value["victory"] is None
                 and ((previous != current) if kind == "setup_handoff" else (previous == current)), "source_native_setup_or_launch_invalid")
    else:
        _require(value["start_provenance"] is None, "source_native_terminal_or_cleanup_invalid")
        if kind == "terminal":
            _require(current is not None and previous == current and type(value["victory"]) is bool
                     and value["graceful"] is None, "source_native_terminal_or_cleanup_invalid")
        else:
            _require(type(value["graceful"]) is bool and value["victory"] is None, "source_native_terminal_or_cleanup_invalid")
    return value

def _environment(value: Any, *, manifest: bool = False) -> dict[str, Any]:
    value = _object(value)
    _require(set(value) == {"game", "connector", "annotator", "player_environment_protocol", "runtime_instance_id",
             "environment_fingerprint", "modset_status", "modset_fingerprint"}, "source_environment_fields_invalid")
    for key in ("player_environment_protocol", "runtime_instance_id", "environment_fingerprint", "modset_status", "modset_fingerprint"):
        _require(isinstance(value[key], str), "source_environment_fields_invalid")
    _identifier(value["runtime_instance_id"]); _identifier(value["environment_fingerprint"])
    for component in ("connector", "annotator"):
        artifact = _object(value[component])
        _require(set(artifact) == {"product", "version", "source_revision", "source_digest_sha256", "sha256", "module_version_id"}
                 and all(isinstance(item, str) for item in artifact.values()), "source_environment_artifact_fields_invalid")
        _require(_SHA.fullmatch(artifact["sha256"]) is not None and _SHA.fullmatch(artifact["source_digest_sha256"]) is not None
                 and re.fullmatch(r"[a-fA-F0-9]{40}", artifact["source_revision"]) is not None, "source_environment_artifact_invalid")
    game = _object(value["game"])
    required = {"main_assembly_sha256", "main_assembly_module_version_id"}
    nullable = {"version", "commit"}
    _require(required <= set(game) <= required | nullable and (manifest or set(game) == required | nullable)
             and all(isinstance(game[key], str) for key in required)
             and all(game.get(key) is None or isinstance(game[key], str) for key in nullable), "source_environment_game_fields_invalid")
    _require(_SHA.fullmatch(game["main_assembly_sha256"]) is not None, "source_environment_game_identity_invalid")
    # Shared manifest serialization may omit these nullable game facts; stream
    # context serialization writes null explicitly. Compare their typed values.
    return {**value, "game": {**game, "version": game.get("version"), "commit": game.get("commit")}}



@dataclass(frozen=True)
class _SourceFormat:
    version: int = 2

    def schema(self, family: str) -> str:
        return f"sts2.annotator/{family}-{self.version}"

    @property
    def profile_id(self) -> str:
        return f"native-logical-source-v{self.version}"

    @property
    def streams(self) -> dict[str, str]:
        return {key: value[:-1] + str(self.version) for key, value in STREAMS.items()}

    @property
    def fence_fields(self) -> set[str]:
        return {"after_input_ordinal"} if self.version == 3 else set()


@dataclass(frozen=True)
class SourceSessionBundleV2:
    directory: Path
    manifest: Mapping[str, Any]
    recording: Mapping[str, Any]
    observations: tuple[Mapping[str, Any], ...]
    inputs: tuple[Mapping[str, Any], ...]
    segments: tuple[Mapping[str, Any], ...]
    content_id: str
    gap_count: int
    epochs: tuple[Mapping[str, Any], ...]
    final_drains: tuple[Mapping[str, Any], ...]

    @property
    def human_origin_verified(self) -> bool:
        return False


DESCRIPTOR = VerifierDescriptor(TYPE_ID, BUNDLE_SCHEMA, 2, SourceSessionBundleV2)


class SourceSessionBundleV2Verifier:
    descriptor = DESCRIPTOR
    format = _SourceFormat(2)
    value_type = SourceSessionBundleV2

    def verify(self, source: str | Path, expected: Mapping[str, object] | None = None) -> VerificationResult[SourceSessionBundleV2]:
        directory = Path(source).absolute()
        try:
            value = self._verify(directory, expected)
            return VerificationResult(self.descriptor, "pass", directory, value)
        except SourceSessionError as error:
            return VerificationResult(self.descriptor, "fail", directory,
                findings=(VerificationFinding(error.code, str(error), error.path),))
        except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
            return VerificationResult(self.descriptor, "fail", directory,
                findings=(VerificationFinding("source_bundle_invalid", type(error).__name__),))

    def _verify(self, directory: Path, expected: Mapping[str, object] | None) -> SourceSessionBundleV2:
        fmt = self.format
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
        _integer(bundle.get("schema_version"), (1 << 31) - 1)
        for key in ("observation_count", "input_count", "gap_count"): _integer(bundle.get(key))
        _require(bundle.get("schema") == fmt.schema("source-session-bundle") and bundle.get("schema_version") == fmt.version
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
        _require(identity.get("schema") == fmt.schema("source-session-bundle") and identity.get("capture_profile_id") == fmt.profile_id
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
                 and set(audit) == {"schema", "status", "session_id", "observation_count", "input_count", "gap_count", "source_kinds", "errors", "non_claims"}
                 and audit.get("schema") == fmt.schema("source-session-audit")
                 and audit.get("status") == "pass" and audit.get("errors") == []
                 and audit.get("non_claims") == list(NON_CLAIMS), "source_producer_audit_invalid")
        for key in ("observation_count", "input_count", "gap_count"): _integer(audit.get(key))
        recording, observations, inputs, segments, gaps, kinds, epochs, drains, boundaries, final_ordinal = _verify_raw(raw, bundle, fmt)
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
        additions = {"boundaries": tuple(_freeze(row) for row in boundaries),
                     "final_input_prefix_ordinal": final_ordinal} if fmt.version == 3 else {}
        return self.value_type(directory, _freeze(bundle), _freeze(recording),
            tuple(_freeze(row) for row in observations), tuple(_freeze(row) for row in inputs),
            tuple(_freeze(row) for row in segments), bundle["bundle_content_id"], gaps,
            tuple(_freeze(row) for row in epochs), tuple(_freeze(row) for row in drains), **additions)


def _verify_raw(raw: Path, bundle: Mapping[str, Any], fmt: _SourceFormat = _SourceFormat(2)) -> tuple[Any, ...]:
    _require(not (raw / "source-accounting-failure.json").exists(), "source_accounting_failed")
    recording = _object(_json((raw / "recording-manifest.json").read_bytes()))
    required_manifest = {"schema_version", "schema", "session_id", "timeline_id", "created_at", "recorder_version",
        "recorder_source_revision", "platform", "capture_profile_id", "capture_profile_sha256", "supported_families",
        "non_claims", "source_schema_version", "source_environment"}
    optional_manifest = {"decision_schema_version", "disposition_schema_version", "close_schema_version", "recovery_schema_version",
        "continuous_schema_version", "text_input_schema_version"}
    _require(required_manifest <= set(recording) <= required_manifest | optional_manifest, "source_recording_manifest_fields_invalid")
    _require(all(isinstance(recording[key], str) for key in ("schema", "session_id", "timeline_id", "created_at", "recorder_version",
        "recorder_source_revision", "platform", "capture_profile_id", "capture_profile_sha256"))
        and all(recording.get(key) is None or type(recording[key]) is int for key in optional_manifest), "source_recording_manifest_fields_invalid")
    _integer(recording.get("schema_version"), (1 << 31) - 1); _integer(recording.get("source_schema_version"), (1 << 31) - 1)
    _timestamp(recording.get("created_at"))
    profile_bytes = (raw / "capture-profile.json").read_bytes()
    profile = _object(_json(profile_bytes))
    _require(recording.get("schema") == fmt.schema("source-session-manifest") and recording.get("schema_version") == fmt.version
             and recording.get("source_schema_version") == fmt.version and recording.get("capture_profile_id") == fmt.profile_id
             and recording.get("capture_profile_sha256") == _sha(profile_bytes)
             and all(recording.get(key) is None for key in ("decision_schema_version", "text_input_schema_version",
                 "close_schema_version", "disposition_schema_version", "continuous_schema_version"))
             and recording.get("supported_families") == [] and recording.get("non_claims") == list(NON_CLAIMS),
             "source_recording_manifest_invalid")
    session, timeline = _identifier(recording.get("session_id")), _identifier(recording.get("timeline_id"))
    _require(bundle.get("session_id") == session and bundle.get("timeline_id") == timeline
             and bundle.get("capture_profile_id") == fmt.profile_id and bundle.get("capture_profile_sha256") == _sha(profile_bytes),
             "source_manifest_bundle_mismatch")
    _require(set(profile) == {"schema", "profile_id", "input_profile", "publication_profile_id",
             "publication_profile_definition_sha256", "eager_scope", "limits", "non_claims"}
             and profile.get("schema") == fmt.schema("source-capture-profile") and profile.get("profile_id") == fmt.profile_id
             and profile.get("input_profile") == "native-logical-v1" and profile.get("publication_profile_id") == PUBLICATION_PROFILE_ID
             and profile.get("publication_profile_definition_sha256") == PUBLICATION_PROFILE_SHA
             and profile.get("eager_scope") == ["persistent", "interaction", "referents", "catalog"]
             and profile.get("non_claims") == list(NON_CLAIMS), "source_profile_invalid")
    limits = _object(profile.get("limits"))
    upper = {"max_capture_bytes": 64 * 1024 * 1024, "max_payload_bytes": 512 * 1024 * 1024,
             "max_row_bytes": 1024 * 1024, "max_rows_per_stream": 65536, "max_bytes_per_stream": 64 * 1024 * 1024,
             "max_segments": 256, "max_epochs": 256, "max_retiring_epochs": 2, "max_metadata_packets": 32,
             "max_copied_payload_bytes": 128 * 1024 * 1024, "max_pending_inputs": 128,
             "encoding_deadline_ms": 2000, "close_barrier_grace_ms": 3000}
    _require(set(limits) == set(upper) and all(type(limits[key]) is int and 1 <= limits[key] <= cap for key, cap in upper.items())
             and limits["max_row_bytes"] >= 512 and limits["max_payload_bytes"] >= limits["max_capture_bytes"]
             and limits["max_bytes_per_stream"] >= limits["max_row_bytes"] and limits["encoding_deadline_ms"] == 2000,
             "source_limits_invalid")
    environment = _environment(recording["source_environment"], manifest=True)
    receipt = _object(_json((raw / "source-close-receipt.json").read_bytes()))
    _require(set(receipt) == {"schema", "session_id", "timeline_id", "status", "accounting_complete", "final_position",
             "sealed_epochs", "final_drains", "counts", "stream_sha256", "gap_count", "input_count", "epoch_count", "source_kinds"}
             | ({"final_input_prefix_ordinal"} if fmt.version == 3 else set())
             and receipt.get("schema") == fmt.schema("source-session-close") and receipt.get("status") == "closed"
             and receipt.get("accounting_complete") is True and receipt.get("session_id") == session and receipt.get("timeline_id") == timeline,
             "source_close_receipt_invalid")
    _require(set(_object(receipt.get("counts"))) == set(STREAMS) and set(_object(receipt.get("stream_sha256"))) == set(STREAMS),
             "source_close_stream_inventory_invalid")
    for key in ("gap_count", "input_count", "epoch_count"): _integer(receipt.get(key))
    for value in receipt["counts"].values(): _integer(value)
    fields = {
        "source-attachment-epochs.jsonl": {"schema", "sequence", "session_id", "timeline_id", "epoch_id", "previous_epoch_id",
             "context", "starting_position", "initial_position", "predecessor_seal", "transition", "recorded_at"},
        "source-segments.jsonl": {"schema", "sequence", "session_id", "timeline_id", "segment_id", "previous_segment_id",
             "declaration", "boundary_position", "recorded_at"},
        "source-boundaries.jsonl": {"schema", "sequence", "session_id", "timeline_id", "kind", "segment_id", "position",
             "sealed_epochs", "paused_intervals", "transition", "recorded_at"},
        "public-observations.jsonl": {"schema", "sequence", "session_id", "timeline_id", "epoch_id", "segment_id", "position",
             "source_seam", "source_index", "phase", "snapshot_id", "owner_occurrence", "game_continuity_id", "completeness",
             "capture", "catalog", "missing_reason", "gap_after_index"},
        "native-input-witnesses.jsonl": {"schema", "sequence", "session_id", "timeline_id", "input_id", "epoch_id", "segment_id",
             "pre_position", "pre_capture", "catalog", "outcome", "recorded_at"},
    }
    if fmt.version == 3:
        for file in ("source-attachment-epochs.jsonl", "source-segments.jsonl", "source-boundaries.jsonl"):
            fields[file].add("after_input_ordinal")
        fields["native-input-witnesses.jsonl"].update({"input_prefix_ordinal", "basis_order"})
    rows: dict[str, list[dict[str, Any]]] = {}
    for file, schema in fmt.streams.items():
        data = (raw / file).read_bytes()
        _require(len(data) <= limits["max_bytes_per_stream"] and (not data or data.endswith(b"\n")), "source_stream_bytes_invalid")
        values = []
        for line in data.splitlines():
            _require(len(line) <= limits["max_row_bytes"] and len(values) < limits["max_rows_per_stream"], "source_row_capacity")
            row = _object(_json(line))
            _require(set(row) == fields[file] and row.get("schema") == schema and type(row.get("sequence")) is int
                     and row["sequence"] == len(values) + 1 and row.get("session_id") == session and row.get("timeline_id") == timeline,
                     "source_row_binding_invalid")
            if "recorded_at" in row: _timestamp(row["recorded_at"])
            values.append(row)
        _require(receipt["counts"][file] == len(values) and receipt["stream_sha256"][file] == _sha(data), "source_stream_receipt_mismatch")
        rows[file] = values
    epochs = rows["source-attachment-epochs.jsonl"]; segments = rows["source-segments.jsonl"]; boundaries = rows["source-boundaries.jsonl"]
    observations = rows["public-observations.jsonl"]; inputs = rows["native-input-witnesses.jsonl"]
    _require(0 < len(epochs) <= limits["max_epochs"] and 0 < len(segments) <= limits["max_segments"]
             and bool(boundaries) and boundaries[-1]["kind"] == "close" and sum(row["kind"] == "close" for row in boundaries) == 1,
             "source_epoch_segment_or_close_missing")
    epoch_map: dict[str, dict[str, Any]] = {}; epoch_order: dict[str, int] = {}; seals: dict[str, dict[str, Any]] = {}; generations: set[str] = set()

    def position(value: Any) -> tuple[int, int]:
        value = _object(value)
        _require(set(value) == {"epoch_id", "stream_generation", "publication_index"}, "source_native_position_fields_invalid")
        epoch_id = _identifier(value["epoch_id"])
        _require(epoch_id in epoch_map and value["stream_generation"] == epoch_map[epoch_id]["context"]["stream_generation"], "source_epoch_position_mismatch")
        return epoch_order[epoch_id], _index(value["publication_index"])

    def seal(value: Any) -> dict[str, Any]:
        value = _object(value)
        _require(set(value) == {"epoch_id", "stream_generation", "reserved_through", "completed_through"} | fmt.fence_fields, "source_native_seal_fields_invalid")
        epoch_id = _identifier(value["epoch_id"])
        _require(epoch_id in epoch_map and value["stream_generation"] == epoch_map[epoch_id]["context"]["stream_generation"]
                 and _index(value["completed_through"]) <= _index(value["reserved_through"]), "source_epoch_seal_invalid")
        return value

    def in_seal(value: Any) -> tuple[int, int]:
        result = position(value); epoch_id = value["epoch_id"]
        _require(position(epoch_map[epoch_id]["starting_position"])[1] <= result[1] <= _index(seals[epoch_id]["reserved_through"]),
                 "source_original_epoch_range_exceeded")
        return result

    for offset, epoch in enumerate(epochs):
        epoch_id = _identifier(epoch["epoch_id"]); context = _object(epoch["context"])
        _require(epoch_id not in epoch_map and set(context) == {"publication_profile_id", "publication_profile_definition_sha256",
                 "scope_id", "stream_generation", "eager_scope", "seam_coverage", "environment", "game_continuity_id"}, "source_epoch_context_invalid")
        generation = _identifier(context.get("stream_generation")); _identifier(context.get("scope_id"))
        _require(generation not in generations and _environment(context.get("environment")) == environment
                 and context.get("publication_profile_id") == PUBLICATION_PROFILE_ID and context.get("publication_profile_definition_sha256") == PUBLICATION_PROFILE_SHA
                 and context.get("eager_scope") == profile["eager_scope"], "source_epoch_context_invalid")
        coverage = _object(context.get("seam_coverage")); _require(len(coverage) <= 256, "source_seam_coverage_invalid")
        for seam_name, coverage_value in coverage.items():
            _identifier(seam_name); coverage_value = _object(coverage_value); _identifier(coverage_value.get("version"))
            _require(set(coverage_value) == {"version", "coverage"} and coverage_value["coverage"] in {"complete_at_seam", "sampled", "unsupported"}, "source_seam_coverage_invalid")
        if context["game_continuity_id"] is not None: _identifier(context["game_continuity_id"])
        epoch_map[epoch_id] = epoch; epoch_order[epoch_id] = offset; generations.add(generation)
        start, initial = position(epoch["starting_position"]), position(epoch["initial_position"])
        _require(epoch["starting_position"]["epoch_id"] == epoch_id and epoch["initial_position"]["epoch_id"] == epoch_id
                 and start[1] < (1 << 64) - 1 and initial == (offset, start[1] + 1), "source_initial_reservation_invalid")
        if offset == 0:
            _require(epoch["previous_epoch_id"] is None and epoch["predecessor_seal"] is None and epoch["transition"] is None, "source_initial_epoch_invalid")
        else:
            previous = epochs[offset - 1]; original_seal = seal(epoch["predecessor_seal"]); transition = _transition(epoch["transition"])
            _require(epoch["previous_epoch_id"] == previous["epoch_id"] and original_seal["epoch_id"] == previous["epoch_id"]
                     and transition["kind"] in {"setup_handoff", "cleanup"}
                     and transition["previous_game_continuity_id"] == previous["context"]["game_continuity_id"]
                     and transition["game_continuity_id"] == context["game_continuity_id"]
                     and transition["previous_game_continuity_id"] != transition["game_continuity_id"], "source_epoch_transition_invalid")
            seals[previous["epoch_id"]] = original_seal
    close = boundaries[-1]
    _require(close["position"] == receipt["final_position"] and close["position"]["epoch_id"] == epochs[-1]["epoch_id"]
             and close["sealed_epochs"] == receipt["sealed_epochs"] and len(close["sealed_epochs"]) == len(epochs), "source_original_close_seals_invalid")
    close_ids: set[str] = set()
    for value in close["sealed_epochs"]:
        value = seal(value); epoch_id = value["epoch_id"]
        _require(epoch_id not in close_ids and (epoch_id not in seals or value == seals[epoch_id]), "source_original_seal_rewritten")
        close_ids.add(epoch_id); seals[epoch_id] = value
    _require(set(seals) == set(epoch_map) and position(close["position"])[1] == _index(seals[close["position"]["epoch_id"]]["reserved_through"]), "source_original_close_seals_invalid")
    for epoch in epochs:
        _require(position(epoch["initial_position"])[1] <= _index(seals[epoch["epoch_id"]]["reserved_through"]), "source_initial_reservation_not_sealed")
    segment_map: dict[str, dict[str, Any]] = {}; kinds: set[str] = set(); previous_segment = None
    for value in segments:
        segment_id = _identifier(value["segment_id"]); declaration = _object(value["declaration"])
        _require(segment_id not in segment_map and set(declaration) == {"source_kind", "actor_id", "declaration_id", "machine_verifiable"}
                 and declaration["source_kind"] in SOURCE_KINDS and declaration["machine_verifiable"] is False, "source_declaration_invalid")
        _identifier(declaration["actor_id"]); _identifier(declaration["declaration_id"]); in_seal(value["boundary_position"])
        _require(value["previous_segment_id"] == (previous_segment["segment_id"] if previous_segment else None)
                 and (previous_segment is None or position(previous_segment["boundary_position"]) <= position(value["boundary_position"])), "source_segment_binding_invalid")
        segment_map[segment_id] = value; previous_segment = value; kinds.add(declaration["source_kind"])
    _require(segments[0]["boundary_position"] == epochs[0]["starting_position"], "source_initial_segment_invalid")
    paused_intervals: list[dict[str, Any]] = []; paused = False; pause = None; last_boundary = None; gaps = 0
    for value in boundaries:
        current_position = in_seal(value["position"])
        _require(value["segment_id"] in segment_map and (last_boundary is None or last_boundary <= current_position), "source_boundary_position_invalid")
        kind = value["kind"]; intervals = value["paused_intervals"]
        _require(isinstance(intervals, list) and isinstance(value["sealed_epochs"], list), "source_boundary_shape_invalid")
        if kind == "pause":
            _require(not paused and value["transition"] is None and intervals == [] and value["sealed_epochs"] == [], "source_pause_transition_invalid")
            paused, pause = True, value["position"]
        elif kind in {"resume", "close", "epoch_transition"}:
            if kind == "epoch_transition":
                epoch = epoch_map[value["position"]["epoch_id"]]
                _require(value["position"] == epoch["starting_position"] and value["transition"] == epoch["transition"]
                         and value["sealed_epochs"] == [epoch["predecessor_seal"]], "source_epoch_boundary_invalid")
                expected_epoch = epoch["previous_epoch_id"]
                expected_end = _index(seals[expected_epoch]["reserved_through"])
            else:
                _require(kind == "close" or paused, "source_resume_transition_invalid")
                _require(value["transition"] is None and (kind == "close" or value["sealed_epochs"] == []), "source_resume_transition_invalid")
                expected_epoch = value["position"]["epoch_id"]; expected_end = current_position[1]
            _require((len(intervals) == 1) if paused else (not intervals), "source_pause_interval_unclosed")
            if paused:
                interval = _object(intervals[0])
                _require(set(interval) == {"epoch_id", "stream_generation", "after_index", "through_index", "reason"}
                         | ({"after_input_ordinal", "through_input_ordinal"} if fmt.version == 3 else set())
                         and interval["epoch_id"] == expected_epoch and interval["stream_generation"] == epoch_map[expected_epoch]["context"]["stream_generation"]
                         and interval["after_index"] == pause["publication_index"] and _index(interval["through_index"]) == expected_end
                         and _index(interval["after_index"]) <= expected_end and interval["reason"] == "recording_paused", "source_pause_interval_invalid")
                paused_intervals.append(interval); gaps += int(expected_end > _index(interval["after_index"]))
            if kind == "epoch_transition":
                if paused: pause = value["position"]
            else:
                paused, pause = False, None
        elif kind in {"launch", "terminal"}:
            transition = _transition(value["transition"]); continuity = epoch_map[value["position"]["epoch_id"]]["context"]["game_continuity_id"]
            _require(transition["kind"] == kind and transition["previous_game_continuity_id"] == continuity
                     and transition["game_continuity_id"] == continuity and intervals == [] and value["sealed_epochs"] == [], "source_native_boundary_continuity_invalid")
        else:
            raise SourceSessionError("source_boundary_kind_invalid")
        last_boundary = current_position
    epoch_boundaries = [row for row in boundaries if row["kind"] == "epoch_transition"]
    _require(len(epoch_boundaries) == len(epochs) - 1 and all(
        sum(row["position"]["epoch_id"] == epoch["epoch_id"] for row in epoch_boundaries) == 1
        for epoch in epochs[1:]), "source_epoch_boundary_accounting_incomplete")
    for segment in segments[1:]:
        point = position(segment["boundary_position"])
        _require(any(row["kind"] == "pause" and position(row["position"]) <= point
                     and not any(later["kind"] in {"resume", "close"} and later["sequence"] > row["sequence"]
                                 and position(later["position"]) < point for later in boundaries) for row in boundaries), "source_change_not_paused")

    def is_paused(point: dict[str, Any]) -> bool:
        index = position(point)[1]
        return any(value["epoch_id"] == point["epoch_id"] and _index(value["after_index"]) < index <= _index(value["through_index"])
                   for value in paused_intervals)

    payloads: set[str] = set(); capture_ids: dict[tuple[str, str], dict[str, Any]] = {}; catalog_ids: dict[tuple[str, str], dict[str, Any]] = {}
    catalog_actions: dict[tuple[str, str], list[dict[str, Any]]] = {}
    capture_owners: dict[tuple[str, str], str] = {}

    def blob(reference: dict[str, Any], family: str, hash_key: str) -> bytes:
        digest = reference.get(hash_key)
        _require(isinstance(digest, str) and _SHA.fullmatch(digest) is not None, "source_blob_hash_invalid")
        path = f"{family}/sha256/{digest[:2]}/{digest}.bin"
        _require(reference.get("payload_ref") == path and type(reference.get("byte_count")) is int
                 and 1 <= reference["byte_count"] <= limits["max_capture_bytes"], "source_blob_reference_invalid")
        data = (raw / path).read_bytes()
        _require(len(data) == reference["byte_count"] and _sha(data) == digest, "source_blob_missing_or_changed")
        payloads.add(path); return data

    def join(capture: Any, catalog: Any, required_epoch: str, full: bool = False) -> None:
        body = None
        if capture is not None:
            capture = _object(capture)
            _require(set(capture) == {"epoch_id", "capture_id", "snapshot_id", "scope_id", "stream_generation", "captured_at", "byte_count", "sha256", "payload_ref"}
                     and capture["epoch_id"] == required_epoch, "source_capture_reference_fields_invalid")
            _timestamp(capture["captured_at"])
            _identifier(capture["capture_id"]); _identifier(capture["snapshot_id"]); context = epoch_map[required_epoch]["context"]
            key = (required_epoch, capture["capture_id"])
            _require(key not in capture_ids or capture_ids[key] == capture, "source_capture_identity_conflict"); capture_ids[key] = capture
            body = _object(_json(blob(capture, "public-captures", "sha256")))
            _require(body.get("schema") == "sts2.player-environment/native-logical-observation-1" and body.get("input_profile") == "native-logical-v1"
                     and body.get("protocol_version") == environment["player_environment_protocol"] and body.get("snapshot_id") == capture["snapshot_id"]
                     and _object(body.get("session")).get("runtime_instance_id") == environment["runtime_instance_id"]
                     and body["session"].get("environment_fingerprint") == environment["environment_fingerprint"]
                     and _object(body.get("information_policy")).get("includes_hidden_information") is False
                     and capture["scope_id"] == context["scope_id"] and capture["stream_generation"] == context["stream_generation"], "source_capture_identity_invalid")
            _capture_shape(body, capture, full)
            capture_owners[key] = _identifier(_object(body.get("owner_occurrence")).get("occurrence_id"))
        if catalog is None: return
        catalog = _object(catalog)
        _require(set(catalog) == {"epoch_id", "catalog_ref", "snapshot_id", "scope_id", "stream_generation", "total_count", "structural_digest", "byte_count", "payload_sha256", "payload_ref"}
                 and capture is not None and catalog["epoch_id"] == required_epoch
                 and all(catalog.get(key) == capture.get(key) for key in ("epoch_id", "snapshot_id", "scope_id", "stream_generation")), "source_capture_catalog_join_invalid")
        _identifier(catalog["catalog_ref"]); key = (required_epoch, catalog["catalog_ref"])
        _require(key not in catalog_ids or catalog_ids[key] == catalog, "source_catalog_identity_conflict"); catalog_ids[key] = catalog
        actions = _json(blob(catalog, "public-catalogs", "payload_sha256")); digest = _catalog_digest(actions)
        _require(type(catalog["total_count"]) is int and catalog["total_count"] == len(actions) and catalog["structural_digest"] == digest, "source_catalog_digest_mismatch")
        descriptor = _object(body.get("catalog"))
        _require(descriptor.get("status") == "complete" and descriptor.get("digest") == digest
                 and all(descriptor.get(key) == catalog.get(key) for key in ("catalog_ref", "snapshot_id", "scope_id", "stream_generation", "total_count")), "source_capture_catalog_descriptor_mismatch")
        _capture_shape(body, capture, full, actions); catalog_actions[key] = actions

    ranges = {epoch["epoch_id"]: [] for epoch in epochs}; last_observation = {epoch["epoch_id"]: position(epoch["starting_position"])[1] for epoch in epochs}
    for value in paused_intervals:
        ranges[value["epoch_id"]].append((_index(value["after_index"]), _index(value["through_index"])))
    source_indexes: dict[tuple[str, str], int] = {}
    for value in observations:
        epoch_id = value["epoch_id"]; _, index = in_seal(value["position"])
        _require(epoch_id == value["position"]["epoch_id"] and index > last_observation[epoch_id] and not is_paused(value["position"])
                 and value["game_continuity_id"] == epoch_map[epoch_id]["context"]["game_continuity_id"], "source_observation_original_position_invalid")
        original_segments = [segment for segment in segments if position(segment["boundary_position"]) < position(value["position"])]
        _require(bool(original_segments) and original_segments[-1]["segment_id"] == value["segment_id"], "source_observation_original_segment_mismatch")
        _identifier(value["source_seam"]); _identifier(value["phase"]); source_index = _index(value["source_index"])
        if value["gap_after_index"] is not None:
            after = _index(value["gap_after_index"])
            _require(value["source_seam"] == "source_gap" and source_index == 0 and value["phase"] == "retention_overflow"
                     and value["missing_reason"] == "retention_overflow" and value["completeness"] == "failed"
                     and value["capture"] is None and value["catalog"] is None and after < index, "source_diagnostic_gap_invalid")
            ranges[epoch_id].append((after, index))
        else:
            key = (epoch_id, value["source_seam"])
            _require(key not in source_indexes or source_index > source_indexes[key], "source_source_index_order_invalid")
            source_indexes[key] = source_index; ranges[epoch_id].append((index - 1, index))
        join(value["capture"], value["catalog"], epoch_id, value["completeness"] == "complete")
        if value["owner_occurrence"] is not None:
            _identifier(value["owner_occurrence"])
            _require(value["capture"] is None or capture_owners[(epoch_id, value["capture"]["capture_id"])] == value["owner_occurrence"],
                     "source_observation_owner_occurrence_mismatch")
        _require(value["completeness"] in {"complete", "partial", "capacity_exceeded", "failed"}
                 and (value["capture"] is None or value["capture"]["snapshot_id"] == value["snapshot_id"])
                 and ((value["capture"] is not None and value["catalog"] is not None and value["missing_reason"] is None)
                      if value["completeness"] == "complete" else isinstance(value["missing_reason"], str) and bool(value["missing_reason"])), "source_full_reference_incomplete")
        gaps += int(value["missing_reason"] is not None or value["gap_after_index"] is not None); last_observation[epoch_id] = index
    input_ids: set[str] = set()
    for value in inputs:
        epoch_id = value["epoch_id"]; point = in_seal(value["pre_position"]); input_id = _identifier(value["input_id"])
        _require(input_id not in input_ids and epoch_id == value["pre_position"]["epoch_id"] and value["segment_id"] in segment_map
                 and not is_paused(value["pre_position"]), "source_input_original_binding_invalid"); input_ids.add(input_id)
        segment = segment_map[value["segment_id"]]; offset = segments.index(segment)
        _require(position(segment["boundary_position"]) <= point and (offset + 1 == len(segments) or point <= position(segments[offset + 1]["boundary_position"])), "source_input_original_segment_mismatch")
        outcome = _object(value["outcome"])
        _require(set(outcome) == {"mapping_status", "match_count", "selected_action", "native_mechanism", "delivery", "reason_code", "stages"}
                 and outcome["mapping_status"] in {"exact", "unmapped", "ambiguous", "capture_missing"}
                 and type(outcome["match_count"]) is int and 0 <= outcome["match_count"] <= 65536 and outcome["delivery"] in DELIVERIES,
                 "source_input_outcome_invalid")
        _text(outcome["native_mechanism"])
        if outcome["reason_code"] is not None: _text(outcome["reason_code"])
        _require(isinstance(outcome["stages"], list) and len(outcome["stages"]) <= 16, "source_input_stage_capacity")
        for stage in outcome["stages"]:
            stage = _object(stage); _require(set(stage) == {"stage", "delivery", "evidence"} and stage["delivery"] in DELIVERIES, "source_input_stage_delivery_invalid")
            for text in stage.values(): _text(text)
        mapping, count, selected = outcome["mapping_status"], outcome["match_count"], outcome["selected_action"]
        join(value["pre_capture"], value["catalog"], epoch_id, mapping == "exact")
        if mapping == "exact":
            _require(count == 1 and value["pre_capture"] is not None and value["catalog"] is not None and isinstance(selected, dict), "source_exact_input_basis_missing")
            actions = catalog_actions[(epoch_id, value["catalog"]["catalog_ref"])]
            _require(sum(action == selected for action in actions) == 1, "source_selected_action_not_in_original_catalog")
        else:
            _require(selected is None and (mapping != "ambiguous" or count >= 2) and (mapping not in {"unmapped", "capture_missing"} or count == 0), "source_input_mapping_invalid")
        _require(value["pre_capture"] is not None or mapping == "capture_missing", "source_input_capture_missing")
    drains = receipt["final_drains"]
    _require(isinstance(drains, list) and len(drains) == len(epochs) and len({value["epoch_id"] for value in drains}) == len(epochs), "source_final_drain_count_invalid")
    for value in drains:
        value = _object(value)
        _require(set(value) == {"epoch_id", "stream_generation", "sealed_reserved_through", "completed_through", "durable_through", "admitted_inputs_terminal"}
                 | fmt.fence_fields
                 and value["epoch_id"] in epoch_map, "source_final_drain_fields_invalid")
        epoch_id = value["epoch_id"]; reserved = _index(seals[epoch_id]["reserved_through"])
        _require(value["stream_generation"] == epoch_map[epoch_id]["context"]["stream_generation"]
                 and value["sealed_reserved_through"] == seals[epoch_id]["reserved_through"] and _index(value["completed_through"]) == reserved
                 and _index(value["durable_through"]) == reserved and value["admitted_inputs_terminal"] is True, "source_final_drain_incomplete")
        covered = position(epoch_map[epoch_id]["starting_position"])[1]
        for after, through in sorted(ranges[epoch_id]):
            if through == after: continue
            _require(after <= covered and through <= reserved, "source_original_durable_prefix_gap")
            covered = max(covered, through)
        _require(covered == reserved, "source_original_durable_prefix_gap")
    for file in ("semantic-boundary-trace.jsonl", "canonical-transitions.jsonl", "native-semantic-discriminator.jsonl", "invalidations.jsonl"):
        _require((raw / file).read_bytes() == b"", "source_session_contains_human_semantics")
    raw_inventory = _inventory(raw)
    allowed_raw = set(STREAMS) | {"recording-manifest.json", "capture-profile.json", "source-close-receipt.json", "source-coverage.json",
        "source-accounting-failure.json", "performance-profile.json", "recording-owner.lock", "run-journal.jsonl", "invalidations.jsonl",
        "semantic-boundary-trace.jsonl", "canonical-transitions.jsonl", "native-semantic-discriminator.jsonl"}
    _require(all(value in allowed_raw or re.fullmatch(r"(public-captures|public-catalogs)/sha256/[0-9a-f]{2}/[0-9a-f]{64}\.bin", value)
                 for value in raw_inventory), "source_unexpected_raw_file")
    payload_files = {value for value in raw_inventory if value.startswith(("public-captures/", "public-catalogs/"))}
    _require(payload_files == payloads and sum((raw / value).stat().st_size for value in payload_files) <= limits["max_payload_bytes"], "source_payload_inventory_invalid")
    _require(receipt["epoch_count"] == len(epochs) and receipt["input_count"] == len(inputs) and receipt["gap_count"] == gaps
             and receipt["source_kinds"] == sorted(kinds) and bundle.get("gap_count") == gaps
             and bundle.get("observation_count") == len(observations) and bundle.get("input_count") == len(inputs), "source_close_summary_mismatch")
    final_ordinal = None
    if fmt.version == 3:
        from .source_session_order import _verify_order
        final_ordinal = receipt["final_input_prefix_ordinal"]
        _verify_order(epochs, segments, boundaries, inputs, drains, final_ordinal, _index)
    return recording, observations, inputs, segments, gaps, sorted(kinds), epochs, drains, boundaries, final_ordinal


def verify_source_session_bundle_v2(source: str | Path, expected: Mapping[str, object] | None = None) -> VerificationResult[SourceSessionBundleV2]:
    return SourceSessionBundleV2Verifier().verify(source, expected)
