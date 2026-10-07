#!/usr/bin/env python3
"""Verify completed S0 teacher ledgers and project actual policy offers only.

Usage: python tools/baseline-s0-dataset.py --run train=/ABS/run \
    --run dev=/ABS/run --run test=/ABS/run --output /ABS/dataset.json

This checks recorded byte/identity/choice joins, not authenticity of a native
producer, Human origin, causal successors, optimal teachers, or statistical
independence. The owner separately qualifies sources and records training use.
Shared unlock/preferences templates condition all seed groups; active-run
templates and same-seed cross-split allocation are rejected.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from spireagent.json_boundary import (  # noqa: E402
    BoundaryError,
    decode_json,
    digest,
    json_bytes,
    object_fields,
)
from stpd.canonical import semantic_hash  # noqa: E402
from stpd.fullrun.structured_inputs import (  # noqa: E402
    INPUT_ID,
    MAX_SNAPSHOT_BYTES,
    PROJECTION_VERSION,
    project_structured_snapshot,
)
from stpd.fullrun.structured_sequences import (  # noqa: E402
    S0_INPUT_SPEC,
    SOURCE_SCHEMA,
    parse_structured_dataset,
)

RAW_SCHEMA = "sts2.baseline-s0/raw-record-1"
SUMMARY_SCHEMA = "sts2.baseline-s0/run-summary-1"
MAX_LEDGER_BYTES = 128 * 1024 * 1024
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 16384
MAX_CAPTURE_BYTES = 64 * 1024 * 1024
KNOWN_TYPES = frozenset(
    {
        "run_start",
        "episode_identity",
        "bootstrap_controller_handoff",
        "capabilities",
        "runtime_identity",
        "capture",
        "policy_offer",
        "policy_result",
        "tick",
        "run_error",
        "runtime_stop",
        "control_release_confirmation",
        "run_end",
    }
)
GROUPING_POLICY = "fresh_native_new_run_seed_conditioned_on_shared_unlock_preferences_template-v1"


def fail(code: str) -> NoReturn:
    raise BoundaryError("s0_dataset", code)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode()) > 4096:
        fail(code)
    return value


def integer(value: Any, code: str, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= 2**53 - 1:
        fail(code)
    return value


def safe_path(path: Path, *, directory: bool = False) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        fail("absolute_nontraversing_path_required")
    for part in (*reversed(path.parents), path):
        if part.is_symlink():
            fail("symlink_forbidden")
    if directory and not path.is_dir():
        fail("run_directory_required")
    return path


def read_file(path: Path, maximum: int) -> bytes:
    safe_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= maximum:
        fail("regular_bounded_file_required")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        raw = handle.read(maximum + 1)
    if len(raw) != info.st_size or len(raw) > maximum:
        fail("file_changed_or_size_limit")
    return raw


def relative_file(directory: Path, value: Any) -> Path:
    name = text(value, "relative_path_required")
    parsed = PurePosixPath(name)
    if (
        parsed.is_absolute()
        or "\\" in name
        or ":" in name
        or any(part in {"", ".", ".."} for part in name.split("/"))
    ):
        fail("unsafe_relative_path")
    return safe_path(directory.joinpath(*parsed.parts))


def seed(value: Any) -> str:
    canonical = text(value, "seed_required").strip().upper().replace("O", "0").replace("I", "1")
    if not re.fullmatch(r"[A-Z0-9]{1,64}", canonical):
        fail("canonical_seed_required")
    return canonical


def singleton(records: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    items = [row for row in records if row["type"] == kind]
    if len(items) != 1:
        fail("missing_or_duplicate_" + kind)
    return items[0]


def verify_fresh_bootstrap(trace: Any) -> None:
    """Accept the Host's delivered new-run chain, including tutorial handoff.

    The Host chooses the native disable-tutorial semantic option; its compact
    trace retains the localized label, not that semantic ID. Do not infer a
    new start from a resumed map, or invent an Embark/direct-map shortcut.
    """
    setup = {"main_menu", "singleplayer_menu", "character_select"}
    tutorials = {"tutorial", "tutorial_preference"}
    if not isinstance(trace, list) or not 0 < len(trace) <= 16:
        fail("fresh_native_new_run_bootstrap_required")
    embark = []
    for index, step in enumerate(trace):
        if (
            not isinstance(step, dict)
            or step.get("delivery") != "delivered"
            or step.get("interaction_kind") not in setup | tutorials
            or re.search(r"resume|continue.*run", str(step.get("label", "")), re.I)
        ):
            fail("fresh_native_new_run_bootstrap_required")
        if index + 1 < len(trace) and step.get("successor_kind") != trace[index + 1].get(
            "interaction_kind"
        ):
            fail("bootstrap_chain_disconnected")
        if step.get("label") == "Embark":
            if step.get("interaction_kind") != "character_select" or step.get("verb") != "activate":
                fail("bootstrap_embark_binding")
            embark.append(index)
    if (
        trace[0]["interaction_kind"] not in setup
        or len(embark) != 1
        or trace[-1].get("successor_kind") != "map_navigation"
    ):
        fail("fresh_native_new_run_bootstrap_required")
    position = embark[0]
    if (
        any(step["interaction_kind"] not in setup for step in trace[:position])
        or any(
            step["interaction_kind"] not in tutorials or step.get("verb") != "activate"
            for step in trace[position + 1 :]
        )
        or trace[position].get("successor_kind") not in tutorials | {"map_navigation"}
    ):
        fail("bootstrap_post_embark_handoff_invalid")


def verify_template(episode: dict[str, Any], run_start: dict[str, Any]) -> dict[str, Any]:
    profile = episode["profile"]
    template_id = text(profile.get("template_id"), "template_id")
    if template_id != run_start["template_id"] or not re.fullmatch(
        r"[a-z0-9][a-z0-9._-]{0,63}", template_id
    ):
        fail("template_identity")
    profile_root = safe_path(Path(text(profile.get("profile_root"), "profile_root")))
    if profile_root.parent.name != "profiles" or profile_root.name != profile["profile_id"]:
        fail("isolated_profile_path")
    template_root = profile_root.parent.parent / "profile-templates" / template_id
    raw = read_file(template_root / "template.json", MAX_METADATA_BYTES)
    manifest = decode_json(raw)
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("template_id") != template_id
        or not isinstance(manifest.get("files"), list)
        or manifest.get("game_identity") != profile.get("game_identity")
    ):
        fail("template_manifest_identity")
    inventory = manifest["files"]
    if not inventory or len(inventory) != integer(manifest["file_count"], "template_file_count"):
        fail("template_inventory")
    paths: set[str] = set()
    whole = hashlib.sha256()
    for item in inventory:
        entry = object_fields(item, {"path", "size", "sha256"}, "s0_dataset.template_file")
        path = text(entry["path"], "template_file_path")
        relative_file(template_root / "user-data", path)
        if path in paths:
            fail("duplicate_template_file")
        paths.add(path)
        basename = PurePosixPath(path).name.lower()
        if (
            basename in {"current_run.save", "run.save", "savegame.save", "current_game.save"}
            or "current_run" in basename
            or "active_run" in basename
        ):
            fail("active_run_template_not_independent_new_start")
        size = integer(entry["size"], "template_file_size")
        checksum = digest(entry["sha256"], "s0_dataset.template_sha256")
        whole.update(f"{path}\0{size}\0{checksum}\n".encode())
    if not any(path.endswith("/settings.save") for path in paths):
        fail("native_template_settings_missing")
    actual_paths = set()
    user_data = safe_path(template_root / "user-data", directory=True)
    for actual in user_data.rglob("*"):
        safe_path(actual)
        if actual.is_file():
            actual_paths.add(actual.relative_to(user_data).as_posix())
        elif not actual.is_dir():
            fail("unsupported_template_entry")
    if actual_paths != paths:
        fail("template_files_missing_or_unrecorded")
    for entry in inventory:
        file = relative_file(user_data, entry["path"])
        if file.stat().st_size != entry["size"]:
            fail("template_file_size_drift")
        actual_sha = hashlib.sha256()
        actual_size = 0
        with os.fdopen(os.open(file, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as handle:
            while chunk := handle.read(65536):
                actual_size += len(chunk)
                if actual_size > entry["size"]:
                    fail("template_file_changed_during_verification")
                actual_sha.update(chunk)
        if actual_size != entry["size"] or actual_sha.hexdigest() != entry["sha256"]:
            fail("template_file_checksum_drift")
    payload_sha = digest(manifest["payload_sha256"], "s0_dataset.template_payload")
    if whole.hexdigest() != payload_sha or profile.get("template_payload_sha256") != payload_sha:
        fail("template_payload_binding")
    return {
        "template_id": template_id,
        "payload_sha256": payload_sha,
        "manifest_sha256": sha(raw),
        "file_count": len(inventory),
        "basis": "recorded_host_verified_template_inventory_no_active_run_path",
        "conditioning": "shared_generic_unlock_preferences_and_game_build",
    }


def runtime_lineage(
    directory: Path, run_id: str, runtime: dict[str, Any], policy_manifest: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Verify byte joins of the existing Runtime's immutable output inventory.

    This does not establish origin or re-author native/causal evidence. The
    owning Runtime/Evidence qualification remains a separate lead gate.
    """
    folder = safe_path(directory / "runtime" / run_id, directory=True)
    core_names = {
        "adapter-attestation.json",
        "events.jsonl",
        "manifest.json",
        "policy-manifest.json",
    }
    names = core_names | {"evidence-manifest.json", "checksums.sha256"}
    if {path.name for path in folder.iterdir()} != names:
        fail("runtime_evidence_inventory")
    content = {
        name: read_file(
            folder / name, MAX_LEDGER_BYTES if name == "events.jsonl" else MAX_METADATA_BYTES
        )
        for name in names
    }
    expected = {name: sha(raw) for name, raw in content.items() if name != "checksums.sha256"}
    checksum_lines = content["checksums.sha256"].decode().splitlines()
    decoded_checksums = {}
    for line in checksum_lines:
        match = re.fullmatch(r"([a-f0-9]{64})  ([a-z-]+\.(?:json|jsonl))", line)
        if not match or match[2] not in expected or match[2] in decoded_checksums:
            fail("runtime_evidence_checksums")
        decoded_checksums[match[2]] = match[1]
    if decoded_checksums != expected:
        fail("runtime_evidence_checksum_mismatch")
    descriptor = decode_json(content["evidence-manifest.json"])
    inventory = [
        {"path": name, "sha256": sha(content[name]), "bytes": len(content[name])}
        for name in sorted(core_names)
    ]
    if (
        descriptor.get("schema") != "sts2.policy-runtime/immutable-evidence-manifest-1"
        or descriptor.get("run_id") != run_id
        or descriptor.get("complete") is not True
        or descriptor.get("append_only") is not True
        or descriptor.get("files") != inventory
        or descriptor.get("manifest_sha256")
        != semantic_hash({"run_id": run_id, "files": inventory})
    ):
        fail("runtime_evidence_descriptor")
    metadata = decode_json(content["manifest.json"])
    if (
        metadata.get("schema") != "sts2.policy-runtime/agent-run-1"
        or metadata.get("run_id") != run_id
        or metadata.get("status") != "stopped"
        or metadata.get("mode") != "human"
        or metadata.get("tainted") is not False
        or not metadata.get("ended_at")
        or metadata.get("append_only") is not True
        or metadata.get("runtime_version") != runtime["version"]
        or metadata.get("runtime_code_sha256") != runtime["code_sha256"]
        or metadata.get("policy_manifest_sha256") != semantic_hash(policy_manifest)
        or decode_json(content["policy-manifest.json"]) != policy_manifest
    ):
        fail("runtime_evidence_run_binding")
    attestation = decode_json(content["adapter-attestation.json"])
    if (
        attestation.get("run_id") != run_id
        or attestation.get("status") != "attested"
        or attestation.get("actual") != policy_manifest["adapter"]
        or attestation.get("expected") != policy_manifest["adapter"]
    ):
        fail("runtime_adapter_attestation")
    events = [decode_json(line) for line in content["events.jsonl"].splitlines()]
    if not events or events[-1].get("kind") != "stopped":
        fail("runtime_stop_event_missing")
    for index, event in enumerate(events, 1):
        if (
            event.get("schema") != "sts2.policy-runtime/agent-run-event-1"
            or type(event.get("sequence")) is not int
            or event["sequence"] != index
        ):
            fail("runtime_event_sequence_gap")
    lineage = [
        {
            "path": f"runtime/{run_id}/{name}",
            "sha256": sha(content[name]),
            "bytes": len(content[name]),
        }
        for name in sorted(names)
    ]
    return lineage, events


def runtime_decision_joins(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join the production text-v2 events, without inferring missing decisions.

    Port 2 has no separate durable adapter-completion event: Runtime emits its
    text_decision_input and decision only after validating completion/output.
    The raw completion is independently checked against the exact capsule.
    """
    joined_kinds = {
        "text_decision_input",
        "decision",
        "text_menu_dispatch_attempt",
        "menu_navigation",
        "text_native_delivery",
        "text_observed_successor",
        "text_menu_not_applied",
    }
    passive_kinds = {
        "environment_admitted",
        "text_observation_not_admitted",
        "stale_whole_bundle_discarded",
        "controller_acquired",
        "controller_released",
        "handoff_to_human",
        "autonomy_budget_exhausted",
        "one_step_completed",
        "mode_changed",
        "stopped",
    }
    groups: dict[str, dict[str, Any]] = {}
    ordered_ids = []
    decision_ids = []
    for event in events:
        kind = event.get("kind")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            fail("runtime_event_payload_required")
        if kind in passive_kinds:
            if "decision_id" in payload or kind == "stopped" and event is not events[-1]:
                fail("runtime_passive_event_correlation")
            continue
        if kind not in joined_kinds:
            fail("runtime_unknown_error_or_unsupported_event")
        decision_id = text(
            payload.get("decision", {}).get("decision_id")
            if kind == "decision"
            else payload.get("decision_id"),
            "runtime_decision_id",
        )
        if kind == "text_decision_input":
            if decision_id in groups or set(payload) != {"decision_id", "snapshot"}:
                fail("runtime_input_duplicate_or_wrong_port")
            groups[decision_id] = {}
            ordered_ids.append(decision_id)
        if decision_id not in groups or kind in groups[decision_id]:
            fail("runtime_orphan_or_duplicate_decision_event")
        groups[decision_id][kind] = event
        if kind == "decision":
            decision_ids.append(decision_id)
    if ordered_ids != decision_ids:
        fail("runtime_decision_order_or_inventory")
    previous_end = 0
    for identifier in ordered_ids:
        group = groups[identifier]
        if group["text_decision_input"]["sequence"] <= previous_end:
            fail("runtime_interleaved_or_reordered_decisions")
        previous_end = max(event["sequence"] for event in group.values())
    return [groups[identifier] for identifier in ordered_ids]


def bind_runtime_choice(
    group: dict[str, Any],
    snapshot: dict[str, Any],
    output: dict[str, Any],
    chosen: str | None,
    run_id: str,
    manifest_id: str,
) -> dict[str, Any]:
    if not {"text_decision_input", "decision"} <= set(group):
        fail("runtime_decision_missing_input_or_output")
    input_event, decision_event = group["text_decision_input"], group["decision"]
    payload = decision_event["payload"]
    decision = object_fields(
        payload["decision"],
        {
            "schema",
            "decision_id",
            "run_id",
            "manifest_id",
            "snapshot_id",
            "candidate_digest",
            "candidate_count",
            "scores",
            "selected_index",
            "disposition",
            "issued_at",
        },
        "s0_dataset.runtime_decision",
    )
    if (
        set(payload) != {"decision", "resolved_bound_action_id"}
        or input_event["payload"]["snapshot"] != snapshot
        or input_event["payload"]["decision_id"] != decision.get("decision_id")
        or input_event["sequence"] >= decision_event["sequence"]
        or decision.get("schema") != "sts2.policy-runtime/decision-1"
        or decision.get("run_id") != run_id
        or decision.get("manifest_id") != manifest_id
        or decision.get("snapshot_id") != snapshot["snapshot_id"]
        or decision.get("candidate_digest") != output["candidate_digest"]
        or decision.get("candidate_count") != len(output["scores"])
        or decision.get("scores") != output["scores"]
        or decision.get("selected_index") != output["selected_index"]
        or decision.get("disposition") != ("abstain" if chosen is None else "admit")
        or payload.get("resolved_bound_action_id") != chosen
    ):
        fail("runtime_offer_result_decision_join")
    return decision


def bind_runtime_tick(
    group: dict[str, Any],
    tick: dict[str, Any],
    snapshot: dict[str, Any],
    output: dict[str, Any],
    run_id: str,
) -> tuple[str | None, str | None]:
    decision_event = group["decision"]
    decision = decision_event["payload"]["decision"]
    if tick.get("decision") != decision:
        fail("runtime_raw_tick_decision_join")
    selected = output["selected_index"]
    if selected is None or tick.get("type") == "shadow":
        if tick.get("type") not in {"not_executed", "shadow"} or set(group) != {
            "text_decision_input",
            "decision",
        }:
            fail("runtime_unexecuted_decision_has_action_events")
        return None, None
    if "text_menu_dispatch_attempt" not in group:
        fail("runtime_dispatch_attempt_missing")
    action = snapshot["menu_actions"]["actions"][selected]
    dispatch = group["text_menu_dispatch_attempt"]
    if (
        dispatch["sequence"] <= decision_event["sequence"]
        or dispatch["payload"].get("action_id") != action["action_id"]
        or dispatch["payload"].get("effect_domain") != action["effect_domain"]
    ):
        fail("runtime_dispatch_choice_join")
    kind = {
        "navigated": "menu_navigation",
        "text_native_delivered": "text_native_delivery",
        "text_not_applied": "text_menu_not_applied",
    }.get(text(tick.get("type"), "runtime_tick_type"))
    expected = {"text_decision_input", "decision", "text_menu_dispatch_attempt", kind}
    if kind == "text_native_delivery":
        expected.add("text_observed_successor")
    if kind is None or set(group) != expected:
        fail("runtime_missing_or_orphan_result_event")
    result_event = group[kind]
    result = tick.get("result")
    request_id = f"request-{run_id}-{decision['decision_id']}"
    if (
        result_event["sequence"] <= dispatch["sequence"]
        or result_event["payload"].get("result") != result
        or not isinstance(result, dict)
        or result.get("request_id") != request_id
        or result.get("status") == "unknown"
        or result.get("native_delivery") == "unknown"
    ):
        fail("runtime_action_request_result_join")
    if kind == "text_menu_not_applied" and result.get("status") != "not_applied":
        fail("runtime_not_applied_result_join")
    if kind != "text_menu_not_applied" and (
        tick.get("action") != action
        or result.get("action") != action
        or result.get("status") != "applied"
        or result.get("retry") != "never"
    ):
        fail("runtime_applied_action_join")
    if kind == "menu_navigation" and (
        action["effect_domain"] != "text_menu"
        or result.get("effect_domain") != "text_menu"
        or result.get("native_delivery") is not None
        or result_event["payload"].get("action_id") != action["action_id"]
        or result.get("successor") != tick.get("successor")
    ):
        fail("runtime_navigation_join")
    if kind == "text_native_delivery":
        successor = group["text_observed_successor"]
        if successor["sequence"] <= result_event["sequence"] or successor["payload"].get(
            "successor"
        ) != tick.get("successor"):
            fail("runtime_observed_successor_join")
    return action["effect_domain"], request_id


def verify_run(directory: Path, split: str) -> tuple[dict[str, Any], dict[str, Any]]:
    safe_path(directory, directory=True)
    ledger_raw = read_file(directory / "records.jsonl", MAX_LEDGER_BYTES)
    if not ledger_raw.endswith(b"\n"):
        fail("ledger_truncated")
    records = [decode_json(line) for line in ledger_raw.splitlines()]
    if not 0 < len(records) <= MAX_RECORDS:
        fail("record_count_limit")
    for index, value in enumerate(records, 1):
        row = object_fields(
            value,
            {"schema", "record_index", "run_id", "recorded_at", "type", "payload"},
            "s0_dataset.record",
        )
        if (
            row["schema"] != RAW_SCHEMA
            or integer(row["record_index"], "record_index", 1) != index
            or row["type"] not in KNOWN_TYPES
            or not isinstance(row["payload"], dict)
        ):
            fail("ledger_order_or_type")
        text(row["recorded_at"], "record_timestamp")
    run_id = text(records[0]["run_id"], "run_id")
    if not re.fullmatch(r"s0-collect-[A-Za-z0-9_-]{1,128}", run_id):
        fail("safe_collect_run_id_required")
    if any(row["run_id"] != run_id for row in records):
        fail("ledger_run_identity")
    if records[0]["type"] != "run_start" or records[-1]["type"] != "run_end":
        fail("ledger_start_end")
    summary_raw = read_file(directory / "summary.json", MAX_METADATA_BYTES)
    summary = decode_json(summary_raw)
    if (
        not isinstance(summary, dict)
        or summary.get("schema") != SUMMARY_SCHEMA
        or summary.get("run_id") != run_id
        or summary.get("mode") != "collect"
        or summary.get("source_kind") != "agent"
        or summary.get("stop_confirmed") is not True
        or summary.get("errors") != []
        or summary.get("termination") not in {"bounded_loop_end", "runtime_handoff_or_budget"}
    ):
        fail("clean_collect_summary_required")
    end = singleton(records, "run_end")["payload"]
    for key in (
        "ticks",
        "offers",
        "captures",
        "native_deliveries",
        "stop_confirmed",
        "termination",
        "errors",
    ):
        if summary.get(key) != end.get(key):
            fail("summary_end_mismatch")
    start = singleton(records, "run_start")["payload"]
    if (
        start.get("source_kind") != "agent"
        or start.get("input_spec") != S0_INPUT_SPEC
        or start.get("I") is not False
        or start.get("F") is not False
        or start.get("character_id") != "DEFECT"
        or start.get("ascension") != 0
    ):
        fail("source_input_profile")
    actor = object_fields(
        start["actor"], {"id", "version", "code_sha256", "role"}, "s0_dataset.actor"
    )
    if actor["role"] != "teacher":
        fail("teacher_source_required")
    text(actor["id"], "teacher_id")
    text(actor["version"], "teacher_version")
    digest(actor["code_sha256"], "s0_dataset.teacher_code")
    if not isinstance(start.get("teacher_parameters"), dict):
        fail("teacher_parameters")
    teacher = {
        "id": actor["id"],
        "version": actor["version"],
        "parameters": {"code_sha256": actor["code_sha256"], "program": start["teacher_parameters"]},
    }
    canonical_seed = seed(start["seed"])
    episode_row = singleton(records, "episode_identity")
    episode = episode_row["payload"]
    runtime_id = text(episode["host"]["runtime_instance_id"], "runtime_id")
    handoff = singleton(records, "bootstrap_controller_handoff")["payload"]
    if (
        handoff.get("schema") != "sts2.host-runtime/reference-controller-handoff-1"
        or handoff.get("runtime_instance_id") != runtime_id
        or handoff.get("controller") is not None
        or handoff.get("basis") != "fresh_control_observation_after_close"
    ):
        fail("bootstrap_controller_handoff_required")
    provenance = episode.get("episode_provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("verdict") != "provenance_pass"
        or provenance.get("errors") != []
        or provenance.get("requested_seed") != canonical_seed
        or provenance.get("actual_seed") != canonical_seed
        or provenance.get("runtime_instance_id") != runtime_id
        or provenance.get("host_status") != "seed_observed"
        or provenance.get("transport_status") != "observed"
        or episode.get("requested_character_id") != "DEFECT"
        or episode.get("requested_ascension") != 0
    ):
        fail("native_episode_provenance_required")
    verify_fresh_bootstrap(episode.get("bootstrap_trace"))
    template = verify_template(episode, start)
    runtime_row = singleton(records, "runtime_identity")
    runtime = runtime_row["payload"]
    manifest_raw = read_file(directory / "policy-manifest.json", MAX_METADATA_BYTES)
    manifest = decode_json(manifest_raw)
    if runtime.get("manifest") != manifest or runtime.get("manifest_sha256") != sha(manifest_raw):
        fail("runtime_manifest_binding")
    digest(runtime["code_sha256"], "s0_dataset.runtime_code")
    capabilities = singleton(records, "capabilities")["payload"]
    if capabilities.get("host") != episode["host"] or capabilities.get("game") != episode["game"]:
        fail("episode_capability_identity")
    if (
        manifest["adapter"]
        != {
            "id": actor["id"],
            "version": actor["version"],
            "protocol": "sts2.policy-runtime/decision-only-ndjson-2",
            "code_sha256": actor["code_sha256"],
        }
        or manifest["artifact"]["sha256"] != actor["code_sha256"]
        or manifest["representation"]["id"] != S0_INPUT_SPEC
    ):
        fail("teacher_manifest_binding")
    expected_environment = {
        "host_kind": episode["host"]["host_kind"],
        "connector_version": episode["host"]["version"],
        "connector_source_revision": episode["host"]["implementation"]["source_revision"],
        "connector_artifact_sha256": episode["host"]["implementation"]["artifact_sha256"],
        "connector_module_version_id": episode["host"]["implementation"]["module_version_id"],
        "modset_status": episode["game"]["modset"]["status"],
        "modset_fingerprint": episode["game"]["modset"]["fingerprint"],
        "loaded_mod_ids": episode["game"]["modset"]["loaded_mod_ids"],
    }
    if manifest["requirements"]["environment"] != expected_environment:
        fail("manifest_environment_binding")
    stop_row = singleton(records, "runtime_stop")
    stop = stop_row["payload"]
    if (
        stop.get("run_id") != run_id
        or stop.get("lifecycle") != "stopped"
        or stop.get("mode") != "human"
        or stop.get("controller") != "released"
        or stop.get("tainted") is not False
        or stop.get("errors") != []
        or stop.get("runtime")
        != {"version": runtime["version"], "code_sha256": runtime["code_sha256"]}
    ):
        fail("clean_runtime_stop_required")
    release_row = singleton(records, "control_release_confirmation")
    release = release_row["payload"]
    control = release.get("control")
    if (
        release.get("confirmed") is not True
        or release.get("basis") != "fresh_control_observation_after_runtime_stop"
        or release_row["record_index"] <= stop_row["record_index"]
        or not isinstance(control, dict)
        or control.get("schema") != "sts2.player-environment/control-1"
        or control.get("protocol_version") != "1.0.0"
        or control.get("runtime_instance_id") != runtime_id
        or control.get("controller") is not None
        or not isinstance(control.get("clients"), list)
        or any(
            not isinstance(client, dict)
            or not client.get("client_session_id")
            or not client.get("client_instance_id")
            for client in control["clients"]
        )
    ):
        fail("fresh_control_release_required")
    if any(row["type"] == "run_error" for row in records):
        fail("run_error_not_admitted")
    phase = [
        singleton(records, kind)["record_index"]
        for kind in (
            "run_start",
            "episode_identity",
            "bootstrap_controller_handoff",
            "capabilities",
            "runtime_identity",
            "runtime_stop",
            "control_release_confirmation",
            "run_end",
        )
    ]
    if phase != sorted(phase) or any(
        not runtime_row["record_index"] < row["record_index"] < stop_row["record_index"]
        for row in records
        if row["type"] in {"capture", "policy_offer", "policy_result", "tick"}
    ):
        fail("run_phase_order")
    captures: dict[str, dict[str, Any]] = {}
    lineage = [
        {"path": "records.jsonl", "sha256": sha(ledger_raw), "bytes": len(ledger_raw)},
        {"path": "summary.json", "sha256": sha(summary_raw), "bytes": len(summary_raw)},
        {"path": "policy-manifest.json", "sha256": sha(manifest_raw), "bytes": len(manifest_raw)},
    ]
    runtime_files, runtime_events = runtime_lineage(directory, run_id, runtime, manifest)
    lineage += runtime_files
    runtime_groups = runtime_decision_joins(runtime_events)
    capture_bytes = ticks = native_deliveries = 0
    offered_captures: set[str] = set()
    steps: list[dict[str, Any]] = []
    latest: dict[str, Any] | None = None
    pending: dict[str, Any] | None = None
    current_token: str | None = None
    retired_tokens: set[str] = set()
    last_digest: str | None = None
    last_generation: str | None = None
    latest_result: dict[str, Any] | None = None
    seen_native_requests: set[str] = set()
    tick_decision_ids: set[str] = set()
    dispatch_native = dispatch_navigation = 0
    for row in records:
        kind, payload = row["type"], row["payload"]
        if kind == "capture":
            envelope = payload["capture"]
            capture_id = text(envelope["capture_id"], "capture_id")
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", capture_id):
                fail("safe_capture_id_required")
            ordinal = integer(envelope["capture_ordinal"], "capture_ordinal", 1)
            if capture_id in captures or ordinal != len(captures) + 1:
                fail("capture_duplicate_or_gap")
            expected_path = f"captures/{len(captures) + 1:05d}-{capture_id}.json"
            if payload.get("snapshot_path") != expected_path:
                fail("capture_path_binding")
            capsule_path = relative_file(directory, payload["snapshot_path"])
            capsule_raw = read_file(capsule_path, MAX_SNAPSHOT_BYTES)
            capsule_sha = sha(capsule_raw)
            snapshot = decode_json(capsule_raw)
            if (
                envelope.get("schema") != "sts2.player-environment/sealed-observation-1"
                or envelope.get("read_profile") != "text-menu-v2-sealed-1"
                or envelope.get("input_profile") != "text-menu-v2"
                or envelope.get("sha256") != capsule_sha
                or payload.get("sha256") != capsule_sha
                or envelope.get("total_bytes") != len(capsule_raw)
                or payload.get("bytes") != len(capsule_raw)
                or not isinstance(snapshot, dict)
                or snapshot.get("schema") != "sts2.player-environment/text-menu-snapshot-2"
                or snapshot.get("input_profile") != "text-menu-v2"
                or snapshot.get("snapshot_id") != envelope.get("source_snapshot_id")
                or snapshot.get("session") != envelope.get("session")
                or snapshot["session"].get("runtime_instance_id") != runtime_id
                or snapshot["session"].get("environment_fingerprint")
                != capabilities.get("environment_fingerprint")
                or snapshot["information_policy"].get("includes_hidden_information") is not False
            ):
                fail("capsule_bytes_or_identity_mismatch")
            generation = text(envelope.get("generation_id"), "capture_generation")
            if last_generation is not None and generation != last_generation:
                fail("capsule_generation_changed")
            last_generation = generation
            capture_bytes += len(capsule_raw)
            if capture_bytes > MAX_CAPTURE_BYTES:
                fail("raw_capture_budget")
            if latest is None:
                interaction = snapshot["interaction"]
                native_map = (
                    interaction["kind"] == "native_map"
                    and interaction.get("content_schema")
                    == "sts2.player-environment/surface/map_navigation-1"
                    and interaction.get("content", {}).get("surface", {}).get("kind")
                    == "map_navigation"
                )
                if interaction["kind"] != "map_navigation" and not native_map:
                    fail("first_capture_not_fresh_map")
            if snapshot["persistent"] is not None:
                persistent = snapshot["persistent"]["content"]
                if (
                    persistent["player"]["character_definition_id"] != "DEFECT"
                    or persistent["run"]["ascension"] != 0
                ):
                    fail("captured_character_or_ascension_drift")
            latest = {
                "envelope": envelope,
                "path": payload["snapshot_path"],
                "sha256": capsule_sha,
                "raw": capsule_raw,
                "snapshot": snapshot,
            }
            captures[capture_id] = latest
            lineage.append(
                {"path": payload["snapshot_path"], "sha256": capsule_sha, "bytes": len(capsule_raw)}
            )
        elif kind == "policy_offer":
            if pending is not None or latest is None:
                fail("overlapping_or_unjoined_offer")
            offer_id = f"offer-{len(steps) + 1}"
            envelope, snapshot = latest["envelope"], latest["snapshot"]
            frame = project_structured_snapshot(snapshot)
            if (
                payload.get("offer_id") != offer_id
                or payload.get("capture_id") != envelope["capture_id"]
                or payload.get("capture_ordinal") != envelope["capture_ordinal"]
                or payload.get("snapshot_path") != latest["path"]
                or payload.get("snapshot_sha256") != latest["sha256"]
                or payload.get("snapshot_id") != snapshot["snapshot_id"]
                or payload.get("input_spec") != S0_INPUT_SPEC
                or payload.get("source_kind") != "agent"
                or payload.get("actor") != actor
                or payload.get("I") is not False
                or payload.get("F") is not False
                or payload.get("candidate_digest") != frame.candidate_digest
                or payload.get("candidate_count") != len(frame.candidates)
                or snapshot["status"] != "interactive"
                or not frame.candidates
            ):
                fail("policy_offer_binding")
            token = text(payload.get("continuity_token"), "continuity_token")
            if token in retired_tokens:
                fail("retired_continuity_reused")
            reset = current_token != token
            if reset:
                if current_token is not None:
                    retired_tokens.add(current_token)
                last_digest = None
            pending = {
                "offer_id": offer_id,
                "capture": latest,
                "frame": frame,
                "token": token,
                "reset": reset,
            }
            offered_captures.add(envelope["capture_id"])
        elif kind == "policy_result":
            if pending is None or payload.get("offer_id") != pending["offer_id"]:
                fail("missing_duplicate_or_reordered_result")
            snapshot = pending["capture"]["snapshot"]
            frame = pending["frame"]
            output = object_fields(
                payload["output"],
                {"candidate_digest", "scores", "selected_index"},
                "s0_dataset.policy_output",
            )
            completion = object_fields(
                payload["completion"],
                {"continuity_token", "snapshot_id", "sequence"},
                "s0_dataset.completion",
            )
            selected = output["selected_index"]
            if (
                output["candidate_digest"] != frame.candidate_digest
                or not isinstance(output["scores"], list)
                or len(output["scores"]) != len(frame.candidates)
                or any(
                    type(value) not in {int, float} or not math.isfinite(value)
                    for value in output["scores"]
                )
                or selected is not None
                and (type(selected) is not int or not 0 <= selected < len(frame.candidates))
                or completion
                != {
                    "continuity_token": pending["token"],
                    "snapshot_id": snapshot["snapshot_id"],
                    "sequence": snapshot["sequence"],
                }
            ):
                fail("policy_result_binding")
            chosen = None if selected is None else frame.action_ids[selected]
            if payload.get("chosen_action_id") != chosen:
                fail("selected_member_mismatch")
            if len(steps) >= len(runtime_groups):
                fail("raw_offer_has_no_runtime_decision")
            runtime_group = runtime_groups[len(steps)]
            runtime_decision = bind_runtime_choice(
                runtime_group, snapshot, output, chosen, run_id, manifest["manifest_id"]
            )
            steps.append(
                {
                    "position": len(steps),
                    "snapshot": snapshot,
                    "capsule_json": pending["capture"]["raw"].decode("utf-8"),
                    "capsule_sha256": pending["capture"]["sha256"],
                    "capture_id": pending["capture"]["envelope"]["capture_id"],
                    "chosen_action_id": chosen,
                    "reset_before": pending["reset"],
                    "advance": frame.state_digest != last_digest,
                    **(
                        {"reset_reason": "run_start" if not steps else "continuity_change"}
                        if pending["reset"]
                        else {}
                    ),
                }
            )
            current_token, last_digest = pending["token"], frame.state_digest
            latest_result = {
                "snapshot": snapshot,
                "frame": frame,
                "output": output,
                "chosen_action_id": chosen,
                "runtime_group": runtime_group,
                "runtime_decision": runtime_decision,
            }
            pending = None
        elif kind == "tick":
            ticks += 1
            status = payload.get("status", {})
            if (
                payload.get("type") in {"unknown", "error"}
                or status.get("tainted") is True
                or status.get("errors") not in (None, [])
            ):
                fail("unknown_or_error_run_not_admitted")
            result = payload.get("result", {})
            if result.get("status") == "unknown" or result.get("native_delivery") == "unknown":
                fail("unknown_native_result")
            if "decision" in payload:
                if latest_result is None:
                    fail("raw_tick_without_policy_result")
                decision_id = latest_result["runtime_decision"]["decision_id"]
                if decision_id in tick_decision_ids:
                    fail("runtime_decision_has_duplicate_raw_tick")
                tick_decision_ids.add(decision_id)
                domain, _request = bind_runtime_tick(
                    latest_result["runtime_group"],
                    payload,
                    latest_result["snapshot"],
                    latest_result["output"],
                    run_id,
                )
                if domain is not None:
                    dispatch_native += domain == "native_input"
                    dispatch_navigation += domain == "text_menu"
                    dispatch_payload = latest_result["runtime_group"]["text_menu_dispatch_attempt"][
                        "payload"
                    ]
                    if (
                        dispatch_payload.get("native_submissions_used") != dispatch_native
                        or dispatch_payload.get("menu_navigations_used") != dispatch_navigation
                    ):
                        fail("runtime_dispatch_counter_join")
            if payload.get("type") == "text_native_delivered":
                request_id = text(result.get("request_id"), "native_request_id")
                if request_id in seen_native_requests:
                    fail("duplicate_native_delivery_request")
                seen_native_requests.add(request_id)
                if (
                    result.get("status") != "applied"
                    or result.get("effect_domain") != "native_input"
                    or result.get("native_delivery") != "delivered"
                    or payload.get("action", {}).get("effect_domain") != "native_input"
                ):
                    fail("native_delivery_record_mismatch")
                if latest_result is None or latest_result["chosen_action_id"] is None:
                    fail("native_delivery_without_joined_policy_choice")
                decision = payload.get("decision", {})
                expected_output = latest_result["output"]
                selected_action = latest_result["snapshot"]["menu_actions"]["actions"][
                    expected_output["selected_index"]
                ]
                if (
                    decision.get("schema") != "sts2.policy-runtime/decision-1"
                    or decision.get("run_id") != run_id
                    or decision.get("manifest_id") != manifest["manifest_id"]
                    or decision.get("snapshot_id") != latest_result["snapshot"]["snapshot_id"]
                    or decision.get("candidate_digest") != expected_output["candidate_digest"]
                    or decision.get("candidate_count") != len(latest_result["frame"].candidates)
                    or decision.get("scores") != expected_output["scores"]
                    or decision.get("selected_index") != expected_output["selected_index"]
                    or payload.get("action") != selected_action
                    or result.get("action") != selected_action
                ):
                    fail("native_delivery_choice_join")
                native_deliveries += 1
    if pending is not None or not steps or native_deliveries < 1:
        fail("completed_offers_and_native_receipt_required")
    if len(runtime_groups) != len(steps) or len(tick_decision_ids) != len(steps):
        fail("runtime_raw_offer_tick_inventory_mismatch")
    if {path.name for path in (directory / "captures").iterdir()} != {
        PurePosixPath(item["path"]).name for item in captures.values()
    }:
        fail("unrecorded_or_missing_capsule_file")
    if (
        ticks != summary["ticks"]
        or len(steps) != summary["offers"]
        or len(captures) != summary["captures"]
        or native_deliveries != summary["native_deliveries"]
        or capture_bytes != end.get("capture_bytes")
    ):
        fail("raw_counts_mismatch")
    grouping = {
        "canonical_seed": canonical_seed,
        "template_payload_sha256": template["payload_sha256"],
        "game_identity": episode["profile"]["game_identity"],
    }
    identity = {
        "input_spec": S0_INPUT_SPEC,
        "source_kind": "agent",
        "episode": episode,
        "runtime": runtime,
        "run_start": start,
        "template": template,
        "grouping_policy": GROUPING_POLICY,
        "grouping_basis": grouping,
        "statistically_strong_independence": False,
        "projection": {"id": INPUT_ID, "version": PROJECTION_VERSION},
        "lineage": lineage,
        "counts": {
            "raw_captures": len(captures),
            "model_offers": len(steps),
            "excluded_unoffered_captures": len(captures) - len(offered_captures),
            "native_deliveries": native_deliveries,
            "raw_records": len(records),
        },
    }
    return {
        "run_id": run_id,
        "source_group": semantic_hash(grouping),
        "split": split,
        "identity": identity,
        "steps": steps,
    }, teacher


def convert_runs(inputs: list[tuple[str, Path]]) -> dict[str, Any]:
    if not inputs or not any(split == "train" for split, _ in inputs):
        fail("train_run_required")
    runs = []
    teacher = None
    seed_splits: dict[str, str] = {}
    seen_runtime: set[str] = set()
    seen_profile_generation: set[str] = set()
    common_template = None
    for split, directory in inputs:
        if split not in {"train", "dev", "test"}:
            fail("invalid_split")
        run, actor = verify_run(directory, split)
        if teacher is not None and actor != teacher:
            fail("teacher_identity_or_parameters_changed")
        teacher = actor
        identity = run["identity"]
        canonical_seed = identity["grouping_basis"]["canonical_seed"]
        if canonical_seed in seed_splits and seed_splits[canonical_seed] != split:
            fail("same_seed_cross_split_leakage")
        seed_splits[canonical_seed] = split
        runtime_id = identity["episode"]["host"]["runtime_instance_id"]
        generation = identity["episode"]["profile"]["generation_id"]
        if runtime_id in seen_runtime or generation in seen_profile_generation:
            fail("fresh_native_process_and_profile_required")
        seen_runtime.add(runtime_id)
        seen_profile_generation.add(generation)
        template = identity["grouping_basis"] | {"canonical_seed": None}
        if common_template is not None and template != common_template:
            fail("shared_template_or_game_changed")
        common_template = template
        runs.append(run)
    output = {"schema": SOURCE_SCHEMA, "source_kind": "agent", "teacher": teacher, "runs": runs}
    parse_structured_dataset(json_bytes(output))
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", required=True, metavar="SPLIT=/ABS/RUN")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = []
    for value in args.run:
        if "=" not in value:
            fail("run_split_path_required")
        split, directory = value.split("=", 1)
        inputs.append((split, Path(directory)))
    output = convert_runs(inputs)
    raw = json_bytes(output)
    safe_path(args.output)
    if not args.output.parent.is_dir():
        fail("output_parent_required")
    with args.output.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    print(
        json_bytes(
            {
                "output": str(args.output),
                "sha256": sha(raw),
                "runs": len(output["runs"]),
                "offers": sum(len(run["steps"]) for run in output["runs"]),
                "grouping_policy": GROUPING_POLICY,
                "qualification": "owner_source_and_training_use_admission_required",
            }
        ).decode(),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
