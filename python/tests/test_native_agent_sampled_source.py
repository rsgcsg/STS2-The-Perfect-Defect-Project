"""Direct native machine sample admission, using typed synthetic original bytes."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import runpy
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from metadata_import_guard import no_torch_imports as no_torch_imports
from test_protocol_source import setup_store

from spireagent.artifact_contracts import Manifest, Producer
from spireagent.json_boundary import BoundaryError, FrozenObject, json_bytes
from stpd.fullrun import native_agent_sampled_source as sources
from stpd.native_agent_sampled_source_spec import (
    FIXTURE_COHORT,
    PARTITION_SCHEMA,
)
from stpd.native_sampled_carry_spec import INPUT_SPEC

ROOT = Path(__file__).resolve().parents[2]
SHARED = json.loads(
    (
        ROOT / "components/policy-runtime/contracts/fixtures/sampled-current-carry-v1.json"
    ).read_bytes()
)
WIRE = json.loads(
    (ROOT / "components/connector/contracts/fixtures/native-logical-v1.json").read_bytes()
)["wire_samples"]
IMPORTER = Producer("fixture://honest-importer-not-recording-author", "a" * 40, "b" * 64)
PROJECTOR = Producer("fixture://direct-native-projector", "c" * 40, "d" * 64)


def canonical(value):
    return json_bytes(value)[:-1]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class OriginalFixture:
    """Byte writer over the public conformance frames and existing owner packer."""

    def __init__(self, helper):
        self.f = helper
        self.shared = copy.deepcopy(SHARED)
        self.f.agent = copy.deepcopy(SHARED["manifest"])
        attachment = self.f.events[0]
        capture = SHARED["frames"]["map_a"]["capture"]
        attachment["payload"]["subscription"].update(
            eager_scope=[],
            delivery_mode="scoped",
            stream_generation=capture["stream_generation"],
            scope_id=capture["scope_id"],
        )
        attachment["payload"]["environment"].update(**capture["session"])
        self.f.events = [attachment]
        self.last = None
        self.held = False

    @property
    def directory(self):
        return self.f.directory

    def sample(self, name, number, *, deliver=True):
        frame = self.shared["frames"][name]
        capture, observation, actions = frame["capture"], frame["observation"], frame["catalog"]
        acquisition = f"acq-{number}"
        previous = self.last
        version = 1 if previous is None else previous["state_version"] + 1
        self.f.add(
            "agent_sample_next_requested",
            request_id=f"parent-N{number}",
            input={
                "continuity_token": "segment-1",
                "consumption_id": None if previous is None else previous["consumption_id"],
                "state_version": version - 1,
                "basis_acquisition_id": None if previous is None else previous["acquisition_id"],
                "received_cursor": self.f.events[0]["payload"]["subscription"]["starting_cursor"],
            },
        )
        witness = {
            "acquisition_id": acquisition,
            "capture": capture,
            "publication_index": None,
            "snapshot_id": observation["snapshot_id"],
            "revision": observation["revision"],
            "owner_occurrence": observation["owner_occurrence"],
            "status": observation["status"],
            "included": observation["completeness"]["included"],
            "missing": [],
            "catalog_digest": observation["catalog"]["digest"],
            "catalog_count": len(actions),
            "catalog_materialized": True,
        }
        self.f.add("native_acquisition_registered", witness=witness)
        self.f.add(
            "agent_sample_query_offered",
            acquisition_id=acquisition,
            request_id=f"child-query-{number}",
        )
        binding = {
            "acquisition_id": acquisition,
            "input_spec": INPUT_SPEC,
            "continuity_token": "segment-1",
            "publication_index": None,
            "capture": capture,
        }
        stem = "agent-sample-" + sha(canonical(binding))
        raw, catalog = frame["observation_utf8"].encode("utf-8"), canonical(actions)
        metadata = {
            "schema": "sts2.policy-runtime/agent-sample-input-1",
            **binding,
            "observation": {
                "path": stem + ".observation.json",
                "bytes": len(raw),
                "sha256": sha(raw),
            },
            "catalog": {
                "path": stem + ".catalog.json",
                "bytes": len(catalog),
                "sha256": sha(catalog),
                "digest": observation["catalog"]["digest"],
                "count": len(actions),
            },
        }
        for path, data in (
            (metadata["observation"]["path"], raw),
            (metadata["catalog"]["path"], catalog),
            (stem + ".json", json_bytes(metadata)),
        ):
            (self.directory / path).write_bytes(data)
        report = {
            "acquisition_id": acquisition,
            "input_spec": INPUT_SPEC,
            "continuity_token": "segment-1",
            "previous_consumption_id": None if previous is None else previous["consumption_id"],
            "consumption_id": f"consume-{number}",
            "state_version": version,
            "advanced": True,
        }
        self.f.add(
            "agent_sample_input_stored",
            metadata_path=stem + ".json",
            metadata=metadata,
            disposition="consume_proposed",
            proposal={
                "session_id": "session-fixture",
                "recovery_epoch": 0,
                "request_id": f"child-consume-{number}",
                "report": report,
            },
        )
        if previous is None:
            self.f.add(
                "agent_sample_segment_started",
                continuity_token="segment-1",
                acquisition_id=acquisition,
            )
        ack = {
            key: report[key]
            for key in ("acquisition_id", "consumption_id", "state_version", "advanced")
        }
        ack["prefix"] = {
            "continuity_token": "segment-1",
            "history_mode": "sampled_current",
            "consumption_mode": "once_per_occurrence",
            "received_cursor": self.f.events[0]["payload"]["subscription"]["starting_cursor"],
            "consumed_publication_index": None,
            "omissions": {"received_unconsumed_count": 0, "missing_scopes": [], "gap": None},
        }
        self.f.add("agent_consumed", report=report, acknowledgement=ack, witness=witness)
        self.f.add(
            "agent_sample_consume_ack_offered",
            request_id=f"child-consume-{number}",
            acknowledgement=ack,
        )
        directive = (
            {
                "type": "act",
                "basis_acquisition_id": acquisition,
                "selection": {"kind": "handle", "action_id": actions[0]["action_id"]},
                "scores": None,
            }
            if actions
            else {"type": "close", "reason": "native_ready_summary_task_complete"}
        )
        output = {
            "continuity_token": "segment-1",
            "consumption_id": report["consumption_id"],
            "state_version": version,
            "directive": directive,
        }
        self.f.add(
            "agent_sample_next_completed",
            request_id=f"parent-N{number}",
            output=copy.deepcopy(output),
        )
        self.f.add("agent_directive", output=copy.deepcopy(output))
        if actions and deliver:
            if not self.held:
                self.f.add("controller_acquired", controller="held")
                self.held = True
            attempt = {
                "request_id": f"request-{number}",
                "basis_acquisition_id": acquisition,
                "snapshot_id": observation["snapshot_id"],
                "action_id": actions[0]["action_id"],
                "catalog_digest": observation["catalog"]["digest"],
                "run_id": self.f.run["run_id"],
                "runtime_instance_id": capture["session"]["runtime_instance_id"],
            }
            self.f.add("native_submission_requested", **attempt)
            result = {
                **WIRE["result"],
                "request_id": attempt["request_id"],
                "snapshot_id": attempt["snapshot_id"],
                "action": actions[0],
                "delivery": "delivered",
                "execution": "unknown",
                "effect": "unknown",
                "cancel": "not_requested",
                "stages": [],
                "reason": None,
                "observed_frame": None,
                "attribution": None,
            }
            self.f.add("native_result", result=result)
        self.last = report

    def readiness(self, number):
        last = self.last
        source = next(
            e["payload"]["witness"]
            for e in reversed(self.f.events)
            if e["kind"] == "native_acquisition_registered"
        )
        witness = copy.deepcopy(source)
        witness["acquisition_id"] = f"readiness-{number}"
        self.f.add(
            "agent_sample_next_requested",
            request_id=f"parent-ready-{number}",
            input={
                "continuity_token": "segment-1",
                "consumption_id": last["consumption_id"],
                "state_version": last["state_version"],
                "basis_acquisition_id": last["acquisition_id"],
                "received_cursor": self.f.events[0]["payload"]["subscription"]["starting_cursor"],
            },
        )
        self.f.add("native_acquisition_registered", witness=witness)
        self.f.add(
            "agent_sample_query_offered",
            acquisition_id=witness["acquisition_id"],
            request_id=f"child-ready-{number}",
        )
        self.f.add(
            "agent_sample_query_discarded",
            acquisition_id=witness["acquisition_id"],
            reason="readiness_check",
        )
        output = {
            "continuity_token": "segment-1",
            "consumption_id": last["consumption_id"],
            "state_version": last["state_version"],
            "directive": {
                "type": "await",
                "after_cursor": self.f.events[0]["payload"]["subscription"]["starting_cursor"],
                "condition": "any_event",
                "timeout_ms": 250,
            },
        }
        self.f.add(
            "agent_sample_next_completed",
            request_id=f"parent-ready-{number}",
            output=copy.deepcopy(output),
        )
        self.f.add("agent_directive", output=copy.deepcopy(output))

    def end(self, *, uncertain=False):
        self.f.add(
            "agent_sample_segment_ended",
            continuity_token="segment-1",
            reason="external_stop",
            state_version=self.last["state_version"] if self.last else 0,
        )
        if self.held:
            self.f.add("controller_released", controller="released")
        self.f.add(
            "stopped",
            autonomy_budget=self.f.budget,
            controller="released",
            pending_request=None,
            agent_state="uncertain" if uncertain else "known",
        )
        self.f.write()


@pytest.fixture
def original(tmp_path):
    helper_type = runpy.run_path(
        str(ROOT / "components/evidence/tests/test_agent_session_run_evidence.py")
    )["NativeAgentSessionEvidenceTests"]
    helper = helper_type()
    helper.setUp()
    fixture = OriginalFixture(helper)
    fixture.sample("map_a", 1)
    fixture.readiness(1)
    fixture.sample("inspect_b", 2)
    fixture.sample("map_c", 3)
    fixture.readiness(2)
    fixture.readiness(3)
    fixture.sample("ready_summary", 4)
    fixture.end()
    verified = sources.evidence_owner.verify_agent_session_run_evidence(fixture.directory)
    assert verified.passed, verified.findings
    yield fixture
    helper.tearDown()


def project(original):
    verified = sources._verified(original.directory)
    return sources._projection(verified, "a" * 64, FIXTURE_COHORT)


def publish(store, original):
    raw = sources.publish_native_agent_sampled_raw(store, original.directory, IMPORTER)
    ref = sources.publish_native_agent_sampled_admission(store, raw.artifact_id, PROJECTOR)
    partition = sources.publish_native_agent_sampled_partition(store, (ref,), "train", PROJECTOR)
    return raw, ref, partition


def test_typed_four_known_three_N_fixture_uses_original_samples_and_sparse_summary(original):
    runs, report = project(original)
    assert report["counts"] == {
        "original_offers": 7,
        "known_context_samples": 4,
        "eligible_unique_N": 3,
        "readiness_exclusions": 3,
        "excluded_offers": 3,
        "known_ready_summary_samples": 1,
        "real_native_samples": 0,
    }
    assert [s["chosen_action_id"] for s in runs[0]["steps"]] == [
        "map_a-action",
        "inspect_b-action",
        "map_c-action",
        None,
    ]
    assert [s["reset_before"] for s in runs[0]["steps"]] == [True, False, False, False]
    assert runs[0]["identity"]["native_game_continuity_id"] is None
    assert runs[0]["source_group"] == "protocol-runtime:runtime-fixture"
    assert runs[0]["steps"][1]["catalog"] == SHARED["frames"]["inspect_b"]["catalog"]
    assert runs[0]["steps"][3]["observation"] == SHARED["frames"]["ready_summary"]["observation"]


def test_raw_admission_partition_reverify_bytes_with_honest_importer_and_no_capsule_claim(
    tmp_path, original
):
    store, _ = setup_store(tmp_path)
    raw, ref, partition = publish(store, original)
    assert raw.producer == IMPORTER
    assert raw.parameters.value()["original"]["original_git_revision"] is None
    assert raw.parameters.value()["source_kind"] == "agent_protocol"
    assert partition.manifest.parameters.value()["partition_schema"] == PARTITION_SCHEMA
    assert (
        partition.dataset.source_kind == "agent_protocol"
        and not partition.dataset.capsules_verified
    )
    assert partition.dataset.input_spec.value() == INPUT_SPEC
    assert len(partition.index) == 7 and len(partition.dataset.runs[0].steps) == 4
    assert (
        sources.verify_native_agent_sampled_partition(store, partition.manifest.artifact_id)
        == partition
    )
    report = json.loads(store.bytes(store.get_manifest(ref.admission_id).payload("report")))
    assert report["archive_sha256"] == raw.payload("archive").sha256
    assert report["import_producer"] == IMPORTER.to_dict()
    assert report["producer"]["origin_claim"].startswith("explicit_synthetic")


def test_direct_partition_cannot_enter_historical_Source3_dispatch_before_PhaseB(
    tmp_path, original
):
    from stpd.ordered_source_spec import SCOPE
    from stpd.structured_profiles import validate_profile

    store, _ = setup_store(tmp_path)
    _, _, partition = publish(store, original)
    with pytest.raises(BoundaryError, match="projection_target_preset_mismatch"):
        validate_profile(partition.dataset, SCOPE)


@pytest.mark.parametrize("which", ["report", "source"])
def test_rehashed_projection_cannot_replace_original_archive_join(tmp_path, original, which):
    store, _ = setup_store(tmp_path)
    _, ref, partition = publish(store, original)
    manifest = store.get_manifest(ref.admission_id) if which == "report" else partition.manifest
    role = "report" if which == "report" else "source"
    value = json.loads(store.bytes(manifest.payload(role)))
    if which == "report":
        value["counts"]["eligible_unique_N"] = 400
    else:
        value["runs"][0]["steps"][0]["chosen_action_id"] = None
    payload = store.put_payload(role, io.BytesIO(json_bytes(value)), "application/json")
    info = manifest.parameters.value()
    if which == "source":
        info["source_sha256"] = payload.sha256
    forged = Manifest(
        manifest.kind, manifest.producer, manifest.parents, (payload,), FrozenObject.of(info)
    )
    store.publish(forged)
    with pytest.raises(BoundaryError, match="admission_binding|projected_original_join_mismatch"):
        if which == "report":
            sources.publish_native_agent_sampled_partition(
                store,
                (sources.NativeAgentSampledRef(ref.raw_id, forged.artifact_id),),
                "train",
                PROJECTOR,
            )
        else:
            sources.verify_native_agent_sampled_partition(store, forged.artifact_id)


@pytest.mark.parametrize("field", ["ack", "completed_next", "directive", "request", "catalog"])
def test_wrong_original_joins_fail_through_typed_public_owner(original, field):
    f = original.f
    if field == "ack":
        f.event("agent_sample_consume_ack_offered")["payload"]["request_id"] = "child-forged"
    elif field == "completed_next":
        f.event("agent_sample_next_completed")["payload"]["request_id"] = "parent-forged"
    elif field == "directive":
        f.event("agent_directive")["payload"]["output"]["state_version"] = 7
    elif field == "request":
        f.event("native_result")["payload"]["result"]["request_id"] = "foreign-request"
    else:
        metadata = f.event("agent_sample_input_stored")["payload"]["metadata"]
        (original.directory / metadata["catalog"]["path"]).write_bytes(b"[]")
    f.write()
    with pytest.raises(BoundaryError, match="native_agent_bundle_verification_failed"):
        sources._verified(original.directory)


def test_missing_original_payload_cannot_be_rebuilt_from_later_current(original):
    metadata = original.f.event("agent_sample_input_stored")["payload"]["metadata"]
    (original.directory / metadata["observation"]["path"]).unlink()
    original.f.rehash()
    with pytest.raises(BoundaryError, match="native_agent_bundle_verification_failed"):
        sources._verified(original.directory)


@pytest.mark.parametrize("split", ["dev", "test", "gold"])
def test_first_direct_slice_rejects_heldout_or_Gold_partition_before_replay(
    tmp_path, original, split
):
    store, _ = setup_store(tmp_path)
    raw = sources.publish_native_agent_sampled_raw(store, original.directory, IMPORTER)
    ref = sources.publish_native_agent_sampled_admission(store, raw.artifact_id, PROJECTOR)
    with pytest.raises(BoundaryError, match="train_only_partition_required"):
        sources.publish_native_agent_sampled_partition(store, (ref,), split, PROJECTOR)


@pytest.mark.parametrize("delivery", ["unknown", "partially_delivered"])
def test_unknown_delivery_retains_pre_context_without_N_and_censors_following_samples(
    original, delivery
):
    event = original.f.event("native_result")
    event["payload"]["result"].update(delivery=delivery, execution="unknown")
    original.f.run["tainted"] = True
    # An uncertain result cannot authorize the later native requests in this fixture.
    # Retain the original first known context and its uncertainty tail only.
    sequence = event["sequence"]
    original.f.events = original.f.events[:sequence]
    original.last = next(
        e["payload"]["report"] for e in reversed(original.f.events) if e["kind"] == "agent_consumed"
    )
    retained = set()
    for row in original.f.events:
        if row["kind"] == "agent_sample_input_stored":
            payload = row["payload"]
            retained.update(
                {
                    payload["metadata_path"],
                    payload["metadata"]["observation"]["path"],
                    payload["metadata"]["catalog"]["path"],
                }
            )
    for path in original.directory.glob("agent-sample-*"):
        if path.name not in retained:
            path.unlink()
    original.end()
    runs, report = project(original)
    assert len(runs[0]["steps"]) == 1 and runs[0]["steps"][0]["chosen_action_id"] is None
    assert report["censored_tail"]["reason"] == "original_delivery_uncertain_tail"


def test_native_rejected_result_never_becomes_N_but_known_context_survives(original):
    original.f.event("native_result")["payload"]["result"]["execution"] = "native_rejected"
    original.f.write()
    runs, report = project(original)
    assert len(runs[0]["steps"]) == 4
    assert runs[0]["steps"][0]["chosen_action_id"] is None
    assert report["counts"]["eligible_unique_N"] == 2
    assert report["censored_tail"] is None


def test_ACK_without_completed_directive_is_explicit_censored_tail(original):
    f = original.f
    first_of_last = next(
        e["sequence"]
        for e in f.events
        if e["kind"] == "agent_sample_next_requested" and e["payload"]["request_id"] == "parent-N4"
    )
    f.events = [
        e
        for e in f.events
        if e["sequence"] < first_of_last
        or e["kind"] not in {"agent_sample_next_completed", "agent_directive"}
    ]
    # Close the incomplete original Next with uncertainty, preserving the proposed/ACK payload.
    f.event("stopped")["payload"]["agent_state"] = "uncertain"
    f.write()
    runs, report = project(original)
    assert len(runs[0]["steps"]) == 3 and report["counts"]["eligible_unique_N"] == 3
    assert report["censored_tail"]["reason"] == "ACK_without_known_completedNext_directive_tail"
    assert report["index"][-1]["evidence"]["acquisition_id"] == "acq-4"


def test_fixture_relation_never_admits_real_native_cohort_from_manifest_only(tmp_path, original):
    store, _ = setup_store(tmp_path)
    with pytest.raises(BoundaryError, match="unsupported_producer_student_relation"):
        sources.publish_native_agent_sampled_raw(
            store, original.directory, IMPORTER, cohort="native_machine_teacher"
        )
    assert store.manifest_ids() == ()


def test_capacity_and_unsafe_paths_reject_before_decoder(tmp_path, original, monkeypatch):
    calls = []
    monkeypatch.setattr(sources, "MAX_ORIGINAL_BYTES", 1)
    monkeypatch.setattr(
        sources.evidence_owner, "verify_agent_session_run_evidence", lambda _: calls.append(True)
    )
    with pytest.raises(BoundaryError, match="original_inventory_capacity"):
        sources._verified(original.directory)
    assert not calls
    for name in ("../escape", "/absolute", "folder/child", "bad\\name", "C:windows"):
        with pytest.raises(BoundaryError, match="unsafe_original_path"):
            sources._file(tmp_path, name)


def test_projected_source_capacity_precedes_JSON_decoding(monkeypatch):
    calls = []
    monkeypatch.setattr(sources, "MAX_SOURCE_BYTES", 8)
    monkeypatch.setattr(sources, "decode_json", lambda _: calls.append(True))
    with pytest.raises(BoundaryError, match="projected_source_capacity"):
        sources.parse_native_agent_sampled_dataset(b"123456789")
    assert not calls


def replace_original_sample(original, number, frame, *, input_spec=INPUT_SPEC):
    """Reseal one explicitly generated fixture capture through the existing owner writer."""
    acquisition = f"acq-{number}"
    stored = next(
        e
        for e in original.f.events
        if e["kind"] == "agent_sample_input_stored"
        and e["payload"]["metadata"]["acquisition_id"] == acquisition
    )
    old = stored["payload"]["metadata"]
    old_names = {
        stored["payload"]["metadata_path"],
        old["observation"]["path"],
        old["catalog"]["path"],
    }
    observation, actions = frame["observation"], frame["catalog"]
    observation_raw = frame["observation_utf8"].encode("utf-8")
    catalog_raw = canonical(actions)
    capture = copy.deepcopy(frame["capture"])
    capture.update(sha256=sha(observation_raw), byte_count=len(observation_raw))
    binding = {
        "acquisition_id": acquisition,
        "input_spec": input_spec,
        "continuity_token": old["continuity_token"],
        "publication_index": None,
        "capture": capture,
    }
    stem = "agent-sample-" + sha(canonical(binding))
    metadata = {
        "schema": old["schema"],
        **binding,
        "observation": {
            "path": stem + ".observation.json",
            "bytes": len(observation_raw),
            "sha256": sha(observation_raw),
        },
        "catalog": {
            "path": stem + ".catalog.json",
            "bytes": len(catalog_raw),
            "sha256": sha(catalog_raw),
            "digest": observation["catalog"]["digest"],
            "count": len(actions),
        },
    }
    stored["payload"].update(metadata=metadata, metadata_path=stem + ".json")
    stored["payload"]["proposal"]["report"]["input_spec"] = input_spec
    witness = {
        "acquisition_id": acquisition,
        "capture": capture,
        "publication_index": None,
        "snapshot_id": observation["snapshot_id"],
        "revision": observation["revision"],
        "owner_occurrence": observation["owner_occurrence"],
        "status": observation["status"],
        "included": observation["completeness"]["included"],
        "missing": [],
        "catalog_digest": observation["catalog"]["digest"],
        "catalog_count": len(actions),
        "catalog_materialized": True,
    }
    for event in original.f.events:
        payload = event["payload"]
        if (
            event["kind"] == "native_acquisition_registered"
            and payload["witness"]["acquisition_id"] == acquisition
        ):
            payload["witness"] = copy.deepcopy(witness)
        if event["kind"] == "agent_consumed" and payload["report"]["acquisition_id"] == acquisition:
            payload["witness"] = copy.deepcopy(witness)
            payload["report"]["input_spec"] = input_spec
        if event["kind"] in {"agent_sample_next_completed", "agent_directive"}:
            directive = payload["output"]["directive"]
            if (
                directive.get("basis_acquisition_id") == acquisition
                and directive["selection"]["kind"] == "handle"
            ):
                directive["selection"]["action_id"] = actions[0]["action_id"]
        if (
            event["kind"] == "native_submission_requested"
            and payload["basis_acquisition_id"] == acquisition
        ):
            payload.update(
                snapshot_id=observation["snapshot_id"],
                action_id=actions[0]["action_id"],
                catalog_digest=observation["catalog"]["digest"],
            )
        if (
            event["kind"] == "native_result"
            and payload["result"]["request_id"] == f"request-{number}"
        ):
            payload["result"].update(snapshot_id=observation["snapshot_id"], action=actions[0])
    for name in old_names:
        (original.directory / name).unlink()
    for name, raw in (
        (metadata["observation"]["path"], observation_raw),
        (metadata["catalog"]["path"], catalog_raw),
        (stem + ".json", json_bytes(metadata)),
    ):
        (original.directory / name).write_bytes(raw)


def test_typed_wrong_InputSpec_descriptor_is_rejected_by_research_relation(tmp_path, original):
    wrong = {**INPUT_SPEC, "id": "arbitrary-sampler-not-approved", "sha256": "e" * 64}
    original.f.agent["input"]["input_spec"] = wrong
    for number, name in enumerate(("map_a", "inspect_b", "map_c", "ready_summary"), 1):
        replace_original_sample(
            original, number, copy.deepcopy(SHARED["frames"][name]), input_spec=wrong
        )
    original.f.write()
    assert sources.evidence_owner.verify_agent_session_run_evidence(original.directory).passed
    store, _ = setup_store(tmp_path)
    with pytest.raises(BoundaryError, match="producer_input_spec_relation"):
        sources.publish_native_agent_sampled_raw(store, original.directory, IMPORTER)
    assert store.manifest_ids() == ()


def test_missing_fresh_original_prefix_never_starts_from_middle_context(tmp_path, original):
    for event in original.f.events:
        event["payload"]["recovery_epoch"] = 1
        if event["kind"] == "agent_sample_input_stored":
            event["payload"]["proposal"]["recovery_epoch"] = 1
    original.f.write()
    runs, report = project(original)
    assert not runs and report["counts"]["known_context_samples"] == 0
    assert len(report["index"]) == 7
    assert report["censored_tail"]["reason"] == "missing_original_first_known_prefix"
    store, _ = setup_store(tmp_path)
    raw = sources.publish_native_agent_sampled_raw(store, original.directory, IMPORTER)
    ref = sources.publish_native_agent_sampled_admission(store, raw.artifact_id, PROJECTOR)
    with pytest.raises(BoundaryError, match="no_known_original_prefix"):
        sources.publish_native_agent_sampled_partition(store, (ref,), "train", PROJECTOR)


def test_same_occurrence_content_drift_cannot_bypass_typed_unit_prefix(original):
    frame = copy.deepcopy(SHARED["frames"]["map_a"])
    frame["observation"]["persistent"]["content"]["changed_public_field"] = "same occurrence drift"
    frame["observation_utf8"] = canonical(frame["observation"]).decode("utf-8")
    replace_original_sample(original, 2, frame)
    original.f.write()
    verified = sources.evidence_owner.verify_agent_session_run_evidence(original.directory)
    assert verified.failed and verified.findings[0].code == "native_session_consumption_unit"
    with pytest.raises(BoundaryError, match="native_agent_bundle_verification_failed"):
        sources._verified(original.directory)


@pytest.mark.parametrize("proposal", [False, True])
def test_offer_or_proposal_without_ACK_is_censored_not_known_context(original, proposal):
    stored = original.f.event("agent_sample_input_stored")
    through = original.f.event("agent_consumed")["sequence"] - 1 if proposal else stored["sequence"]
    original.f.events = original.f.events[:through]
    if not proposal:
        stored["payload"].update(disposition="query_offered", proposal=None)
    retained = {
        stored["payload"]["metadata_path"],
        stored["payload"]["metadata"]["observation"]["path"],
        stored["payload"]["metadata"]["catalog"]["path"],
    }
    for path in original.directory.glob("agent-sample-*"):
        if path.name not in retained:
            path.unlink()
    if proposal:
        original.f.add(
            "agent_sample_segment_ended",
            continuity_token="segment-1",
            reason="external_stop",
            state_version=0,
        )
    original.f.add(
        "stopped",
        autonomy_budget=original.f.budget,
        controller="released",
        pending_request=None,
        agent_state="uncertain",
    )
    original.f.write()
    runs, report = project(original)
    assert not runs and len(report["index"]) == 1
    assert report["counts"]["known_context_samples"] == report["counts"]["eligible_unique_N"] == 0
    assert report["censored_tail"]["reason"] == (
        "consume_proposed_without_ACK_tail"
        if proposal
        else "offered_without_known_consumption_tail"
    )
    assert all((original.directory / name).is_file() for name in retained)


def test_expression_selection_is_unlabelled_known_context(original):
    for event in original.f.events:
        if event["kind"] in {"agent_sample_next_completed", "agent_directive"}:
            directive = event["payload"]["output"]["directive"]
            if directive.get("basis_acquisition_id") == "acq-1":
                directive["selection"] = {"kind": "expression", "expression": {"verb": "inspect"}}
    original.f.write()
    runs, report = project(original)
    assert len(runs[0]["steps"]) == 4 and runs[0]["steps"][0]["chosen_action_id"] is None
    assert report["counts"]["eligible_unique_N"] == 2 and report["censored_tail"] is None


def test_exact_original_reconciliation_target_retained_and_Human_boundary_censors_suffix(original):
    result_event = original.f.event("native_result")
    result = copy.deepcopy(result_event["payload"]["result"])
    submission = original.f.event("native_submission_requested")["payload"]
    intent = {
        key: submission[key]
        for key in (
            "request_id",
            "run_id",
            "runtime_instance_id",
            "basis_acquisition_id",
            "snapshot_id",
            "action_id",
        )
    }
    intent.update(session_id="session-fixture", submission_epoch=0, status="pending", reason=None)
    position = original.f.events.index(result_event)
    context = {"session_id": "session-fixture", "recovery_epoch": 0}
    replacement = []
    for kind, payload in (
        ("native_request_pending", {"original": intent}),
        (
            "mode_changed",
            {"mode": "human", "autonomy_budget": original.f.budget, "controller": "held"},
        ),
        (
            "native_request_reconciled",
            {"original": intent, "resolution": "resolved", "result": result},
        ),
        (
            "mode_changed",
            {"mode": "auto", "autonomy_budget": original.f.budget, "controller": "held"},
        ),
    ):
        replacement.append({**result_event, "kind": kind, "payload": {**context, **payload}})
    original.f.events[position : position + 1] = replacement
    original.f.write()
    runs, report = project(original)
    assert len(runs[0]["steps"]) == 1 and runs[0]["steps"][0]["chosen_action_id"] == "map_a-action"
    assert runs[0]["steps"][0]["evidence"]["target"]["original_result"] == result
    assert report["censored_tail"]["reason"] == "original_segment_ended"
    assert len(report["index"]) == 7


def test_event_count_and_symlink_reject_before_public_verifier(original, monkeypatch):
    calls = []
    monkeypatch.setattr(sources, "MAX_EVENTS", 1)
    monkeypatch.setattr(
        sources.evidence_owner, "verify_agent_session_run_evidence", lambda _: calls.append(True)
    )
    with pytest.raises(BoundaryError, match="original_event_capacity"):
        sources._verified(original.directory)
    assert not calls
    monkeypatch.setattr(sources, "MAX_EVENTS", 16384)
    target = original.directory / "manifest.json"
    (original.directory / "undeclared-link").symlink_to(target)
    with pytest.raises(BoundaryError, match="original_flat_regular_inventory_required"):
        sources._verified(original.directory)
    assert not calls


def test_unsafe_raw_archive_member_never_reaches_typed_replay(tmp_path, original):
    import gzip
    import tarfile

    store, _ = setup_store(tmp_path)
    raw = sources.publish_native_agent_sampled_raw(store, original.directory, IMPORTER)
    buffer = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as archive,
    ):
        member = tarfile.TarInfo("../escape")
        member.size = 1
        archive.addfile(member, io.BytesIO(b"x"))
    payload = store.put_payload("archive", io.BytesIO(buffer.getvalue()), "application/gzip")
    forged = Manifest("evidence", raw.producer, payloads=(payload,), parameters=raw.parameters)
    store.publish(forged)
    with pytest.raises(BoundaryError, match="unsafe_or_duplicate_member"):
        sources.publish_native_agent_sampled_admission(store, forged.artifact_id, PROJECTOR)
    assert not (tmp_path / "escape").exists()


def declared_teacher_fixture(original, definition=None):
    """Approved production identity on synthetic bytes: never actual native data."""
    from stpd.native_agent_sampled_source_spec import TEACHER_PRODUCER

    definition = TEACHER_PRODUCER if definition is None else definition
    original.f.agent["adapter"] = copy.deepcopy(definition["adapter"])
    original.f.agent["agent"] = {
        "id": definition["agent_spec"]["id"],
        "version": definition["agent_spec"]["version"],
        "provider": "stpd",
        "architecture": "explicit_native_public_program_teacher",
    }
    original.f.agent["artifact"] = {
        "id": definition["artifact_id"],
        "sha256": definition["artifact_sha256"],
        "path": "/fixture/teacher-artifact-not-actually-executed.json",
    }
    original.f.agent["input"].update(
        input_spec=copy.deepcopy(definition["input_spec"]),
        projection={key: definition["input_spec"][key] for key in ("id", "version")},
        state_format_version=definition["state_format_version"],
        state_recovery=copy.deepcopy(definition["state_recovery"]),
    )
    original.f.run.update(
        agent_id=definition["agent_spec"]["id"],
        agent_version=definition["agent_spec"]["version"],
        agent_artifact_sha256=definition["artifact_sha256"],
    )
    for number, name in enumerate(("map_a", "inspect_b", "map_c", "ready_summary"), 1):
        replace_original_sample(
            original,
            number,
            copy.deepcopy(SHARED["frames"][name]),
            input_spec=definition["input_spec"],
        )
    original.f.write()
    assert sources.evidence_owner.verify_agent_session_run_evidence(original.directory).passed


def _synthetic_policy_relation(monkeypatch, producer_input=None):
    """Explicit private synthetic test tuple; never an offered production producer."""
    from stpd import native_agent_sampled_source_spec as spec
    from stpd.canonical import semantic_hash

    policy = json.loads((ROOT / "components/policy-runtime/contracts/fixtures/"
                        "owned-current-known-stale-v1.json").read_bytes())["execution_policy"]
    definition = copy.deepcopy(spec.FOCUS_TEACHER_PRODUCER)
    definition.update(source_commit="e" * 40, artifact_id="synthetic-owned-stale-fixture",
                      artifact_sha256="d" * 64, execution_policy=copy.deepcopy(policy))
    definition["adapter"].update(id="synthetic-owned-stale-adapter", code_sha256="c" * 64)
    definition["agent_spec"].update(id="synthetic-owned-stale-program", version="0.0.0-synthetic")
    original_body = spec.FOCUS_TEACHER_RELATION_BODY
    if producer_input is not None:
        from stpd.native_sampled_carry_spec import INPUT_SPEC_BODY

        definition.update(input_spec=copy.deepcopy(producer_input["input_spec"]),
                          input_spec_body=copy.deepcopy(INPUT_SPEC_BODY),
                          state_format_version=producer_input["state_format_version"],
                          state_recovery=copy.deepcopy(producer_input["state_recovery"]))
        original_body = spec.RELATION_BODY
    body = {**copy.deepcopy(original_body),
            "id": "synthetic-owned-stale-producer-relation-unit-test",
            "producer_definition": definition}
    relation = {"id": body["id"], "version": "1.0.0", "sha256": semantic_hash(body)}
    projection_body = {**copy.deepcopy(spec.PROJECTION_BODY),
                       "id": "synthetic-owned-stale-projection-unit-test",
                       "producer_student_relation": relation}
    projection = {"id": projection_body["id"], "version": "1.0.0",
                  "sha256": semantic_hash(projection_body)}
    tuples = spec._closed_relations()
    monkeypatch.setattr(spec, "_closed_relations", lambda: (
        *tuples, ((FIXTURE_COHORT,), body, relation, projection)))
    return definition, relation, projection


@pytest.fixture
def owned_stale_original(monkeypatch):
    """Owning sealed synthetic writer + actual source-local new Evidence verifier.

    Components/evidence is selected only by the explicit pure TEST command.
    No installed package/pin/version or production relation is changed here.
    """
    monkeypatch.syspath_prepend(str(ROOT / "components/evidence/tests"))
    tools = runpy.run_path(str(ROOT / "components/evidence/tests/"
                              "test_sampled_agent_session_run_evidence.py"))
    helper = tools["OwnedStaleEvidenceFixture"]()
    definition, relation, projection = _synthetic_policy_relation(
        monkeypatch, helper.f.agent["input"])
    helper.f.agent["adapter"] = copy.deepcopy(definition["adapter"])
    helper.f.agent["agent"] = {"id": definition["agent_spec"]["id"],
        "version": definition["agent_spec"]["version"], "provider": "stpd",
        "architecture": "explicit_native_public_program_teacher"}
    helper.f.agent["artifact"].update(id=definition["artifact_id"],
                                     sha256=definition["artifact_sha256"])
    helper.f.run.update(agent_id=definition["agent_spec"]["id"],
                        agent_version=definition["agent_spec"]["version"],
                        agent_artifact_sha256=definition["artifact_sha256"])
    try:
        yield helper, relation, projection, tools["OwnedStaleAgentSessionEvidenceTests"].original
    finally:
        helper.close()


@pytest.mark.parametrize("duplicate_delivery", [None, "not_started", "delivered"])
def test_typed_owned_stale_context_and_fresh_N_share_original_known_prefix(
    owned_stale_original, duplicate_delivery
):
    helper, relation, _, _ = owned_stale_original
    refused = helper.terminal(helper.submission(), defer=True)
    helper.readiness()
    helper.sample()
    helper.terminal(helper.submission(), delivery="delivered")
    helper.stop()
    if duplicate_delivery is not None:
        position = next(i for i, event in enumerate(helper.f.events)
                        if event["kind"] == "native_result"
                        and event["payload"]["result"]["delivery"] == duplicate_delivery)
        helper.f.events.insert(position + 1, copy.deepcopy(helper.f.events[position]))
        helper.f.write()
    original_bytes = {path.name: path.read_bytes() for path in helper.directory.iterdir()}
    verified = sources._verified(helper.directory)
    summary = verified.terminal_summary
    assert {key: summary[key] for key in (
        "original_submission_count", "terminal_result_count", "known_delivered",
        "known_stale_rejections", "consecutive_known_stale_rejections")} == {
            "original_submission_count": 2, "terminal_result_count": 2, "known_delivered": 1,
            "known_stale_rejections": 1, "consecutive_known_stale_rejections": 0}
    assert summary["proof_scope"] == "recorded_dispatch_and_terminal_results"
    assert summary["live_eligibility_proved"] is False
    runs, report = sources._projection(verified, "a" * 64, FIXTURE_COHORT, relation)
    assert len(runs) == 1 and len(runs[0]["steps"]) == 2
    first, later = runs[0]["steps"]
    assert first["chosen_action_id"] is None
    assert first["evidence"]["target"]["reason"] == "original_delivery_or_execution_not_N"
    assert first["evidence"]["target"]["original_result"] == refused
    assert later["chosen_action_id"] == later["catalog"][0]["action_id"]
    assert [step["reset_before"] for step in (first, later)] == [True, False]
    for key in ("runtime_instance_id", "session_id", "recovery_epoch"):
        assert first["evidence"][key] == later["evidence"][key]
    assert first["evidence"]["report"]["continuity_token"] == (
        later["evidence"]["report"]["continuity_token"])
    assert [step["evidence"]["report"]["state_version"] for step in (first, later)] == [1, 2]
    assert report["counts"]["known_context_samples"] == 2
    assert report["counts"]["eligible_unique_N"] == 1
    assert report["counts"]["readiness_exclusions"] == 1 and report["censored_tail"] is None
    assert report["counts"]["real_native_samples"] == 0
    for step in (first, later):
        target = step["evidence"]["target"]
        carriers = [event for event in helper.f.events if event["kind"] == "native_result"
                    and event["payload"]["result"]["request_id"]
                    == target["original_submission"]["request_id"]]
        assert target["result_sequence"] == min(event["sequence"] for event in carriers)
        assert all(event["payload"]["result"] == target["original_result"] for event in carriers)
    assert original_bytes == {path.name: path.read_bytes() for path in helper.directory.iterdir()}


@pytest.mark.parametrize("duplicate_direct", [False, True])
def test_typed_reconciled_delivery_keeps_one_N_and_earliest_original_carrier(
    owned_stale_original, duplicate_direct
):
    helper, relation, _, pending_original = owned_stale_original
    attempt = helper.submission()
    original = pending_original(attempt)
    helper.f.add("native_request_pending", original=copy.deepcopy(original))
    helper.f.add("mode_changed", mode="human", autonomy_budget=helper.f.budget,
                 controller="released")
    result = copy.deepcopy(helper.owned["result"])
    result.update(request_id=attempt["request_id"], snapshot_id=attempt["snapshot_id"],
                  delivery="delivered", execution="native_accepted", effect="pending",
                  cancel="not_requested", reason=None,
                  action=copy.deepcopy(helper.base.shared["frames"][helper.frame_name]["catalog"][0]))
    helper.f.add("native_request_reconciled", original=copy.deepcopy(original),
                 resolution="resolved", result=result)
    if duplicate_direct:
        helper.f.add("native_result", result=copy.deepcopy(result))
    helper.stop()
    original_bytes = {path.name: path.read_bytes() for path in helper.directory.iterdir()}
    verified = sources._verified(helper.directory)
    assert verified.terminal_summary["terminal_result_count"] == 1
    assert verified.terminal_summary["known_delivered"] == 1
    runs, report = sources._projection(verified, "a" * 64, FIXTURE_COHORT, relation)
    assert report["counts"]["known_context_samples"] == report["counts"]["eligible_unique_N"] == 1
    assert report["censored_tail"] is None
    step = runs[0]["steps"][0]
    assert step["chosen_action_id"] == result["action"]["action_id"]
    assert step["evidence"]["target"]["original_result"] == result
    earliest = next(event for event in helper.f.events
                    if event["kind"] == "native_request_reconciled")
    assert step["evidence"]["target"]["result_sequence"] == earliest["sequence"]
    assert original_bytes == {path.name: path.read_bytes() for path in helper.directory.iterdir()}


def test_conflicting_terminal_body_fails_owner_and_defensive_source_selector(owned_stale_original):
    helper, _, _, _ = owned_stale_original
    helper.terminal(helper.submission(), defer=True)
    helper.stop()
    position = next(i for i, event in enumerate(helper.f.events)
                    if event["kind"] == "native_result")
    duplicate = copy.deepcopy(helper.f.events[position])
    duplicate["payload"]["result"]["effect"] = "not_observed"
    helper.f.events.insert(position + 1, duplicate)
    helper.f.write()
    assert not sources.evidence_owner.verify_agent_session_run_evidence(helper.directory).passed
    with pytest.raises(BoundaryError, match="native_agent_bundle_verification_failed"):
        sources._verified(helper.directory)
    carriers = [event for event in helper.f.events if event["kind"] == "native_result"]
    with pytest.raises(BoundaryError, match="original_terminal_result_changed"):
        sources._terminal_result(carriers, True)


def test_terminal_selector_does_not_change_legacy_duplicate_cardinality(owned_stale_original):
    helper, _, _, _ = owned_stale_original
    helper.terminal(helper.submission(), defer=True)
    helper.stop()
    event = next(event for event in helper.f.events if event["kind"] == "native_result")
    assert sources._terminal_result([event], False) is event
    assert sources._terminal_result([event, copy.deepcopy(event)], False) is None
    assert sources._terminal_result([], True) is None


@pytest.mark.parametrize("binding", ["different_valid_policy", "legacy_relation"])
def test_new_typed_verifier_does_not_open_source_producer_policy_admission(
    tmp_path, owned_stale_original, binding
):
    from stpd.native_agent_sampled_source_spec import RELATION_SPEC

    helper, relation, _, _ = owned_stale_original
    if binding == "different_valid_policy":
        helper.f.agent["execution_policy"]["max_known_stale_rejections"] -= 1
    else:
        relation = RELATION_SPEC
    helper.terminal(helper.submission(), defer=True)
    helper.stop()
    # Actual strict Evidence accepts the declared policy and original trace.
    verified = sources._verified(helper.directory)
    assert verified.terminal_summary["known_stale_rejections"] == 1
    store, _ = setup_store(tmp_path)
    with pytest.raises(BoundaryError, match="producer_execution_policy_relation"):
        sources.publish_native_agent_sampled_raw(
            store, helper.directory, IMPORTER, relation=relation)
    assert not store.manifest_ids(), "typed grammar acceptance cannot bypass the closed producer"


@pytest.mark.parametrize("delivery,duplicate_terminal", [
    ("unknown", False), ("unknown", True),
    ("partially_delivered", False), ("partially_delivered", True), ("pending", False)])
def test_typed_owned_policy_keeps_uncertain_or_pending_original_tail_censored(
    owned_stale_original, delivery, duplicate_terminal
):
    helper, relation, _, pending_original = owned_stale_original
    attempt = helper.submission()
    if delivery == "pending":
        pending = pending_original(attempt)
        helper.f.add("native_request_pending", original=copy.deepcopy(pending))
        helper.stop(pending=pending)
    else:
        result = helper.terminal(attempt, delivery=delivery)
        if duplicate_terminal:
            helper.f.add("native_result", result=copy.deepcopy(result))
        helper.stop()
    verified = sources._verified(helper.directory)
    runs, report = sources._projection(verified, "a" * 64, FIXTURE_COHORT, relation)
    assert len(runs[0]["steps"]) == 1 and runs[0]["steps"][0]["chosen_action_id"] is None
    assert report["counts"]["eligible_unique_N"] == 0
    assert report["censored_tail"]["reason"] == (
        "original_delivery_outstanding_tail" if delivery == "pending"
        else "original_delivery_uncertain_tail")
    if delivery != "pending":
        first_result = next(event for event in helper.f.events if event["kind"] == "native_result")
        assert report["censored_tail"]["sequence"] == first_result["sequence"]


@pytest.mark.parametrize("name", ["fixture", "TEACHER", "MAP_TEACHER", "FOCUS_TEACHER"])
def test_legacy_closed_producer_rejects_execution_policy_presence_in_identity_join(original, name):
    """Producer closure unit law; a changed in-memory manifest is not new verified evidence."""
    from stpd import native_agent_sampled_source_spec as spec

    relation = spec.RELATION_SPEC if name == "fixture" else getattr(spec, name + "_RELATION_SPEC")
    if name != "fixture":
        declared_teacher_fixture(original, getattr(spec, name + "_PRODUCER"))
    verified = sources._verified(original.directory)
    sources._producer(verified, FIXTURE_COHORT, relation)
    changed = sources._plain(verified.agent_manifest)
    declared_policy = json.loads(
        (ROOT / "components/policy-runtime/contracts/fixtures/owned-current-known-stale-v1.json")
        .read_bytes())["execution_policy"]
    for policy in (declared_policy, None):
        changed["execution_policy"] = policy
        with pytest.raises(BoundaryError, match="producer_execution_policy_relation"):
            sources._producer(replace(verified, agent_manifest=changed), FIXTURE_COHORT, relation)


def test_private_synthetic_future_tuple_requires_exact_policy_without_opening_production(
    original, monkeypatch
):
    """Identity binding only, separate from the new Evidence grammar/Source qualification gate."""
    from stpd import native_agent_sampled_source_spec as spec

    closed = spec._closed_relations
    definition, relation, projection = _synthetic_policy_relation(monkeypatch)
    declared_teacher_fixture(original, definition)
    verified = sources._verified(original.directory)
    with pytest.raises(BoundaryError, match="producer_execution_policy_relation"):
        sources._producer(verified, FIXTURE_COHORT, relation)
    changed = sources._plain(verified.agent_manifest)
    changed["execution_policy"] = copy.deepcopy(definition["execution_policy"])
    candidate = replace(verified, agent_manifest=changed)
    joined = sources._producer(candidate, FIXTURE_COHORT, relation)
    assert joined["producer_relation_body"]["producer_definition"] == definition
    assert spec.checked_relation(relation, FIXTURE_COHORT) == relation
    assert spec.relation_specs(relation, FIXTURE_COHORT)[0] == projection
    altered = copy.deepcopy(changed)
    altered["execution_policy"]["max_known_stale_rejections"] -= 1
    with pytest.raises(BoundaryError, match="producer_execution_policy_relation"):
        sources._producer(replace(verified, agent_manifest=altered), FIXTURE_COHORT, relation)
    with pytest.raises(BoundaryError, match="unsupported_producer_student_relation"):
        spec.checked_relation(relation, spec.TEACHER_COHORT)
    monkeypatch.setattr(spec, "_closed_relations", closed)
    with pytest.raises(BoundaryError, match="unsupported_producer_student_relation"):
        spec.checked_relation(relation, FIXTURE_COHORT)


def test_fixed_teacher_descriptor_relation_keeps_synthetic_origin_explicit(tmp_path, original):
    from stpd.native_agent_sampled_source_spec import TEACHER_PRODUCER, TEACHER_RELATION_SPEC

    declared_teacher_fixture(original)
    store, _ = setup_store(tmp_path)
    raw = sources.publish_native_agent_sampled_raw(
        store, original.directory, IMPORTER, relation=TEACHER_RELATION_SPEC
    )
    ref = sources.publish_native_agent_sampled_admission(store, raw.artifact_id, PROJECTOR)
    partition = sources.publish_native_agent_sampled_partition(store, (ref,), "train", PROJECTOR)
    report = json.loads(store.bytes(store.get_manifest(ref.admission_id).payload("report")))
    assert partition.dataset.input_spec.value() == INPUT_SPEC
    assert (
        report["producer"]["agent_manifest"]["input"]["input_spec"]
        == TEACHER_PRODUCER["input_spec"]
    )
    assert (
        report["counts"]["known_context_samples"] == 4
        and report["counts"]["eligible_unique_N"] == 3
    )
    assert report["counts"]["real_native_samples"] == 0
    assert report["native_origin_status"] == "synthetic_conformance"
    assert report["producer"]["original_git_revision"] is None
    assert (
        report["producer"]["producer_relation_body"]["producer_definition"]["agent_spec"]["learned"]
        is False
    )
    assert partition.dataset.source_kind == "agent_protocol"
    assert (
        sources.verify_native_agent_sampled_partition(store, partition.manifest.artifact_id)
        == partition
    )


def test_historical_focus_teacher_relation_preserves_its_original_frozen_descriptor(
    tmp_path, original,
):
    from stpd.native_agent_sampled_source_spec import (
        FOCUS_TEACHER_PRODUCER,
        FOCUS_TEACHER_RELATION_SPEC,
        MAP_TEACHER_PRODUCER,
        MAP_TEACHER_RELATION_SPEC,
        TEACHER_PRODUCER,
        TEACHER_RELATION_SPEC,
        relation_body,
    )
    from stpd.policy.native_teacher_agent import descriptor

    new = FOCUS_TEACHER_PRODUCER
    declared_teacher_fixture(original, new)
    assert new["agent_spec"]["teacher"]["version"] == "1.0.5"
    assert MAP_TEACHER_PRODUCER["agent_spec"]["teacher"]["version"] == "1.0.4"
    assert TEACHER_PRODUCER["agent_spec"]["teacher"]["version"] == "1.0.2"
    for historical in (MAP_TEACHER_PRODUCER, TEACHER_PRODUCER):
        assert new["artifact_sha256"] != historical["artifact_sha256"]
        assert new["input_spec"] == historical["input_spec"]
    frozen = {"schema": new["artifact_schema"], **{key: new[key] for key in (
        "agent_spec", "input_spec_body", "input_spec", "adapter", "code_files",
        "runtime_provenance",
    )}}
    assert sha(json_bytes(frozen)) == new["artifact_sha256"]
    assert descriptor() != frozen
    for relation in (MAP_TEACHER_RELATION_SPEC, TEACHER_RELATION_SPEC):
        path = tmp_path / relation["id"]
        path.mkdir()
        with pytest.raises(BoundaryError, match="fixed_teacher_producer_identity"):
            sources.publish_native_agent_sampled_raw(
                setup_store(path)[0], original.directory, IMPORTER,
                relation=relation,
            )
    path = tmp_path / "new-relation"
    path.mkdir()
    store, _ = setup_store(path)
    raw = sources.publish_native_agent_sampled_raw(
        store, original.directory, IMPORTER, relation=FOCUS_TEACHER_RELATION_SPEC,
    )
    ref = sources.publish_native_agent_sampled_admission(store, raw.artifact_id, PROJECTOR)
    partition = sources.publish_native_agent_sampled_partition(store, (ref,), "train", PROJECTOR)
    report = json.loads(store.bytes(store.get_manifest(ref.admission_id).payload("report")))
    assert report["counts"]["eligible_unique_N"] == 3
    assert report["native_origin_status"] == "synthetic_conformance"
    assert partition.dataset.input_spec.value() == INPUT_SPEC
    assert relation_body(FOCUS_TEACHER_RELATION_SPEC, FIXTURE_COHORT)["producer_definition"] == new


def declared_owned_teacher_fixture(helper, definition):
    """Generate a private SYN trace under fixed current metadata; no Teacher execution."""
    f = helper.f
    f.agent["adapter"] = copy.deepcopy(definition["adapter"])
    f.agent["agent"] = {
        "id": definition["agent_spec"]["id"], "version": definition["agent_spec"]["version"],
        "provider": "stpd", "architecture": "explicit_native_public_program_teacher",
    }
    f.agent["artifact"].update(id=definition["artifact_id"], sha256=definition["artifact_sha256"])
    f.agent["input"].update(
        input_spec=copy.deepcopy(definition["input_spec"]),
        projection={key: definition["input_spec"][key] for key in ("id", "version")},
        state_format_version=definition["state_format_version"],
        state_recovery=copy.deepcopy(definition["state_recovery"]),
    )
    f.agent["execution_policy"] = copy.deepcopy(definition["execution_policy"])
    f.run.update(agent_id=definition["agent_spec"]["id"],
                 agent_version=definition["agent_spec"]["version"],
                 agent_artifact_sha256=definition["artifact_sha256"])
    # The owning fixture rebuilds all initial sample metadata and hashes from
    # this manifest before creating subsequent offers, ACKs or terminal facts.
    helper.initial_action_id(helper.base.shared["frames"]["map_a"]["catalog"][0]["action_id"])
    helper.terminal(helper.submission(), defer=True)
    helper.readiness()
    helper.sample()
    helper.terminal(helper.submission(), delivery="delivered")
    helper.stop()
    assert sources._verified(helper.directory).terminal_summary["known_delivered"] == 1


def test_owned_teacher_tuple_retains_exact_frozen_descriptor_and_sealed_original_prefix(
    tmp_path, owned_stale_original,
):
    from stpd.native_agent_sampled_source_spec import (
        FOCUS_TEACHER_PRODUCER,
        MAP_TEACHER_PRODUCER,
        OWNED_STALE_TEACHER_PRODUCER,
        OWNED_STALE_TEACHER_RELATION_SPEC,
        TEACHER_COHORT,
        TEACHER_PRODUCER,
        relation_body,
    )
    from stpd.policy.native_teacher_agent import descriptor

    new = OWNED_STALE_TEACHER_PRODUCER
    assert new["agent_spec"]["teacher"]["version"] == "1.0.6"
    assert new["adapter"]["version"] == "1.2.0"
    assert new["execution_policy"] == new["agent_spec"]["execution_policy"]
    frozen = {"schema": new["artifact_schema"], **{key: new[key] for key in (
        "agent_spec", "input_spec_body", "input_spec", "adapter", "code_files",
        "runtime_provenance",
    )}}
    assert sha(json_bytes(frozen)) == new["artifact_sha256"]
    current = descriptor(execution_policy=new["execution_policy"])
    # A qualified historical tuple includes its exact Python patch version.
    # Portable source checks compare current code, while retaining that old runtime.
    assert {**current, "runtime_provenance": frozen["runtime_provenance"]} == frozen
    assert current["runtime_provenance"]["python_version"] == list(sys.version_info[:3])
    assert frozen["runtime_provenance"]["python_version"] == [3, 11, 15]
    assert new["runtime_provenance"]["dependency_lock_sha256"] == (
        sha((ROOT / "python/uv.lock").read_bytes()))
    for old in (FOCUS_TEACHER_PRODUCER, MAP_TEACHER_PRODUCER, TEACHER_PRODUCER):
        assert old["runtime_provenance"]["dependency_lock_sha256"] != (
            new["runtime_provenance"]["dependency_lock_sha256"])
        assert old["artifact_sha256"] != new["artifact_sha256"]
    helper, _, _, _ = owned_stale_original
    declared_owned_teacher_fixture(helper, new)
    original_bytes = {path.name: path.read_bytes() for path in helper.directory.iterdir()}
    store, _ = setup_store(tmp_path)
    raw = sources.publish_native_agent_sampled_raw(
        store, helper.directory, IMPORTER, cohort=TEACHER_COHORT,
        relation=OWNED_STALE_TEACHER_RELATION_SPEC,
    )
    ref = sources.publish_native_agent_sampled_admission(store, raw.artifact_id, PROJECTOR)
    partition = sources.publish_native_agent_sampled_partition(store, (ref,), "train", PROJECTOR)
    verified = sources.verify_native_agent_sampled_partition(store, partition.manifest.artifact_id)
    report = json.loads(store.bytes(store.get_manifest(ref.admission_id).payload("report")))
    assert report["counts"]["known_context_samples"] == 2
    assert report["counts"]["eligible_unique_N"] == 1
    assert report["counts"]["real_native_samples"] is None
    assert report["native_origin_status"] == "not_established_by_this_verifier"
    assert [row["N_eligible"] for row in verified.index] == [False, False, True]
    assert original_bytes == {path.name: path.read_bytes() for path in helper.directory.iterdir()}
    assert relation_body(OWNED_STALE_TEACHER_RELATION_SPEC, TEACHER_COHORT)[
        "producer_definition"] == new


@pytest.mark.parametrize("mutation", ["policy", "artifact"])
def test_owned_teacher_tuple_rejects_other_valid_verified_producer_metadata(
    tmp_path, owned_stale_original, mutation,
):
    from stpd.native_agent_sampled_source_spec import (
        FOCUS_TEACHER_RELATION_SPEC,
        OWNED_STALE_TEACHER_PRODUCER,
        OWNED_STALE_TEACHER_RELATION_SPEC,
        TEACHER_COHORT,
    )

    definition = copy.deepcopy(OWNED_STALE_TEACHER_PRODUCER)
    if mutation == "policy":
        definition["execution_policy"]["max_known_stale_rejections"] -= 1
    else:
        definition["artifact_sha256"] = "f" * 64
    helper, _, _, _ = owned_stale_original
    declared_owned_teacher_fixture(helper, definition)
    store, _ = setup_store(tmp_path)
    with pytest.raises(BoundaryError, match=(
        "producer_execution_policy_relation" if mutation == "policy"
        else "fixed_teacher_producer_identity"
    )):
        sources.publish_native_agent_sampled_raw(
            store, helper.directory, IMPORTER, cohort=TEACHER_COHORT,
            relation=OWNED_STALE_TEACHER_RELATION_SPEC,
        )
    with pytest.raises(BoundaryError, match="producer_execution_policy_relation"):
        sources.publish_native_agent_sampled_raw(
            store, helper.directory, IMPORTER, cohort=TEACHER_COHORT,
            relation=FOCUS_TEACHER_RELATION_SPEC,
        )
    assert not store.manifest_ids()


def test_declared_teacher_origin_is_unknown_not_zero_and_does_not_block_known_N(tmp_path, original):
    from stpd.native_agent_sampled_source_spec import TEACHER_COHORT, TEACHER_RELATION_SPEC

    # Exercise the declaration branch on synthetic bytes, not a real-origin fixture.
    declared_teacher_fixture(original)
    store, _ = setup_store(tmp_path)
    raw = sources.publish_native_agent_sampled_raw(
        store, original.directory, IMPORTER, cohort=TEACHER_COHORT, relation=TEACHER_RELATION_SPEC
    )
    ref = sources.publish_native_agent_sampled_admission(store, raw.artifact_id, PROJECTOR)
    report = json.loads(store.bytes(store.get_manifest(ref.admission_id).payload("report")))
    assert report["counts"]["real_native_samples"] is None
    assert report["native_origin_status"] == "not_established_by_this_verifier"
    assert report["counts"]["eligible_unique_N"] == 3
    assert (
        sources.publish_native_agent_sampled_partition(
            store, (ref,), "train", PROJECTOR
        ).dataset.source_kind
        == "agent_protocol"
    )


@pytest.mark.parametrize("field", ["adapter_code", "artifact", "state", "wire_projection", "agent"])
def test_typed_wrong_teacher_tuple_does_not_enter_fixed_relation(tmp_path, original, field):
    from stpd.native_agent_sampled_source_spec import TEACHER_RELATION_SPEC

    declared_teacher_fixture(original)
    agent = original.f.agent
    if field == "adapter_code":
        agent["adapter"]["code_sha256"] = "f" * 64
    elif field == "artifact":
        agent["artifact"]["sha256"] = "e" * 64
        original.f.run["agent_artifact_sha256"] = "e" * 64
    elif field == "state":
        agent["input"]["state_format_version"] = "invented-script-state-v2"
    elif field == "wire_projection":
        agent["input"]["projection"] = {"id": "invented-projection", "version": "1.0.0"}
    else:
        agent["agent"]["architecture"] = "hidden-model-substitution"
    original.f.write()
    assert sources.evidence_owner.verify_agent_session_run_evidence(original.directory).passed
    store, _ = setup_store(tmp_path)
    with pytest.raises(
        BoundaryError, match="producer_input_spec_relation|fixed_teacher_producer_identity"
    ):
        sources.publish_native_agent_sampled_raw(
            store, original.directory, IMPORTER, relation=TEACHER_RELATION_SPEC
        )
    assert store.manifest_ids() == ()


def test_frozen_teacher_and_student_digest_recipes_remain_distinct():
    from stpd.canonical import semantic_hash
    from stpd.native_agent_sampled_source_spec import TEACHER_PRODUCER, TEACHER_RELATION_BODY
    from stpd.native_sampled_carry_spec import INPUT_SPEC_BODY

    teacher = TEACHER_PRODUCER
    assert sha(json_bytes(teacher["input_spec_body"])) == teacher["input_spec"]["sha256"]
    assert semantic_hash(INPUT_SPEC_BODY) == INPUT_SPEC["sha256"]
    assert (
        TEACHER_RELATION_BODY["producer_input_spec_body_encoding"]
        == "canonical_json_with_one_newline"
    )
    assert (
        TEACHER_RELATION_BODY["student_input_spec_body_encoding"]
        == "canonical_json_without_terminal_newline"
    )
    descriptor = {
        "schema": teacher["artifact_schema"],
        **{
            key: teacher[key]
            for key in (
                "agent_spec",
                "input_spec_body",
                "input_spec",
                "adapter",
                "code_files",
                "runtime_provenance",
            )
        },
    }
    assert sha(json_bytes(descriptor)) == teacher["artifact_sha256"]


@pytest.mark.parametrize("cohort,relation", [([], {}), (True, {}), (None, None)])
def test_malformed_relation_containers_fail_at_research_boundary(cohort, relation):
    from stpd.native_agent_sampled_source_spec import checked_relation

    with pytest.raises(BoundaryError, match="unsupported_producer_student_relation"):
        checked_relation(relation, cohort)


@pytest.mark.parametrize("python_version", [None, (3, 11, 17)])
def test_current_runtime_teacher_tuple_is_only_admitted_by_exact_private_fixture_relation(
    tmp_path, owned_stale_original, monkeypatch, python_version,
):
    from types import SimpleNamespace

    from stpd import native_agent_sampled_source_spec as spec
    from stpd.canonical import semantic_hash
    from stpd.policy import native_teacher_agent

    if python_version is not None:
        monkeypatch.setattr(native_teacher_agent, "sys", SimpleNamespace(
            implementation=sys.implementation, version_info=python_version))
    current = native_teacher_agent.descriptor(
        execution_policy=spec.OWNED_STALE_TEACHER_PRODUCER["execution_policy"])
    assert current["runtime_provenance"]["python_version"] == list(
        python_version or sys.version_info[:3])
    definition = copy.deepcopy(spec.OWNED_STALE_TEACHER_PRODUCER)
    definition.update({key: copy.deepcopy(value) for key, value in current.items()
                       if key != "schema"})
    definition["artifact_sha256"] = sha(json_bytes(current))
    definition["artifact_id"] = ("synthetic-current-runtime-teacher-" +
                                 definition["artifact_sha256"][:16])
    helper, _, _, _ = owned_stale_original
    declared_owned_teacher_fixture(helper, definition)
    store, _ = setup_store(tmp_path)
    # This current-runtime descriptor has no production qualification; no patch
    # version is rewritten in the frozen tuple to make it fit this machine.
    with pytest.raises(BoundaryError, match="fixed_teacher_producer_identity"):
        sources.publish_native_agent_sampled_raw(
            store, helper.directory, IMPORTER, cohort=spec.TEACHER_COHORT,
            relation=spec.OWNED_STALE_TEACHER_RELATION_SPEC)
    body = {**copy.deepcopy(spec.OWNED_STALE_TEACHER_RELATION_BODY),
            "id": "synthetic-current-runtime-teacher-exact-tuple",
            "producer_definition": definition}
    relation = {"id": body["id"], "version": "1.0.0", "sha256": semantic_hash(body)}
    projection_body = {**copy.deepcopy(spec.PROJECTION_BODY),
                      "id": "synthetic-current-runtime-teacher-projection",
                      "producer_student_relation": relation}
    projection = {"id": projection_body["id"], "version": "1.0.0",
                  "sha256": semantic_hash(projection_body)}
    tuples = spec._closed_relations()
    monkeypatch.setattr(spec, "_closed_relations", lambda: (
        *tuples, ((FIXTURE_COHORT,), body, relation, projection)))
    with pytest.raises(BoundaryError, match="unsupported_producer_student_relation"):
        spec.checked_relation(relation, spec.TEACHER_COHORT)
    raw = sources.publish_native_agent_sampled_raw(
        store, helper.directory, IMPORTER, cohort=FIXTURE_COHORT, relation=relation)
    ref = sources.publish_native_agent_sampled_admission(store, raw.artifact_id, PROJECTOR)
    report = json.loads(store.bytes(store.get_manifest(ref.admission_id).payload("report")))
    assert report["native_origin_status"] == "synthetic_conformance"
    assert report["counts"]["eligible_unique_N"] == 1
    assert spec.relation_body(relation, FIXTURE_COHORT)["producer_definition"] == definition
    # Exact fixture membership is also closed; restore the registry to show
    # fixture registration cannot grant production or persistent admission.
    monkeypatch.setattr(spec, "_closed_relations", lambda: tuples)
    with pytest.raises(BoundaryError, match="unsupported_producer_student_relation"):
        spec.checked_relation(relation, FIXTURE_COHORT)
