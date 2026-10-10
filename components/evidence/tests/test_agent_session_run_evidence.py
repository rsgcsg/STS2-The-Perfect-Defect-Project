"""Typed consumer regressions using the checked-in native producer wire fixture."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from sts2_platform_evidence import (
    verify_agent_run_evidence,
    verify_agent_session_run_evidence,
)
from sts2_platform_evidence.agent_session_run_evidence import TYPE_ID
from sts2_platform_evidence.cli import registry
from sts2_platform_evidence.store import ContentAddressedStore
from sts2_platform_evidence.transfer import DirectoryReceiver, DirectoryTransferManifest

ROOT = Path(__file__).resolve().parents[2]


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class NativeAgentSessionEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name) / "run-fixture"
        self.directory.mkdir()
        shared = json.loads(
            (ROOT / "policy-runtime/contracts/fixtures/agent-session-v1.json").read_bytes()
        )
        wire = json.loads(
            (ROOT / "connector/contracts/fixtures/native-logical-v1.json").read_bytes()
        )["wire_samples"]
        self.agent = copy.deepcopy(shared["manifest"])
        # A verifier binds bytes and does not open this local implementation path.
        self.agent["artifact"]["path"] = "/does/not/exist/private/model.json"
        self.run = {
            "schema": "sts2.policy-runtime/agent-session-run-1",
            "run_id": "run-fixture",
            "manifest_id": self.agent["manifest_id"],
            "agent_manifest_sha256": sha(canonical(self.agent)),
            "agent_id": self.agent["agent"]["id"],
            "agent_version": self.agent["agent"]["version"],
            "agent_artifact_sha256": self.agent["artifact"]["sha256"],
            "runtime_version": "fixture",
            "runtime_code_sha256": "d" * 64,
            "started_at": "2026-10-08T00:00:00.000Z",
            "ended_at": "2026-10-08T00:01:00.000Z",
            "status": "stopped",
            "mode": "human",
            "tainted": False,
            "append_only": True,
        }
        self.startup = {
            "schema": "sts2.policy-runtime/agent-session-startup-1",
            "run_id": self.run["run_id"],
            "agent_manifest_id": self.run["manifest_id"],
            **{
                key: self.run[key]
                for key in (
                    "agent_manifest_sha256",
                    "agent_artifact_sha256",
                    "runtime_version",
                    "runtime_code_sha256",
                )
            },
            "adapter": self.agent["adapter"],
        }
        a = shared["acquisitions"]["A"]
        o, capture = a["observation"], a["capture"]
        self.witness = {
            "acquisition_id": a["acquisition_id"],
            "capture": capture,
            "publication_index": a["publication_index"],
            "snapshot_id": o["snapshot_id"],
            "revision": o["revision"],
            "owner_occurrence": o["owner_occurrence"],
            "status": o["status"],
            "included": o["completeness"]["included"],
            "missing": o["completeness"]["missing"],
            "catalog_digest": o["catalog"]["digest"],
            "catalog_count": o["catalog"]["total_count"],
            "catalog_materialized": True,
        }
        action = a["catalog"][0]
        self.attempt = {
            "request_id": "request-fixture",
            "basis_acquisition_id": a["acquisition_id"],
            "snapshot_id": o["snapshot_id"],
            "action_id": action["action_id"],
            "catalog_digest": o["catalog"]["digest"],
            "run_id": self.run["run_id"],
            "runtime_instance_id": capture["session"]["runtime_instance_id"],
        }
        self.original = {
            key: self.attempt[key]
            for key in (
                "request_id",
                "run_id",
                "runtime_instance_id",
                "basis_acquisition_id",
                "snapshot_id",
                "action_id",
            )
        }
        self.original.update(
            session_id="session-fixture",
            submission_epoch=0,
            status="pending",
            reason=None,
        )
        self.result = {
            **wire["result"],
            "request_id": self.attempt["request_id"],
            "snapshot_id": o["snapshot_id"],
            "action": action,
            "delivery": "delivered",
            "execution": "native_accepted",
            "effect": "pending",
            "cancel": "not_requested",
            "stages": [],
            "reason": None,
            "observed_frame": None,
            "attribution": None,
        }
        prefix = {
            "continuity_token": "continuity-fixture",
            "history_mode": "full_reference",
            "consumption_mode": "once_per_occurrence",
            "received_cursor": "cursor-fixture",
            "consumed_publication_index": a["publication_index"],
            "omissions": {
                "received_unconsumed_count": 0,
                "missing_scopes": [],
                "gap": None,
            },
        }
        report = {
            "acquisition_id": a["acquisition_id"],
            "input_spec": self.agent["input"]["input_spec"],
            "continuity_token": prefix["continuity_token"],
            "previous_consumption_id": None,
            "consumption_id": "consumed-1",
            "state_version": 1,
            "advanced": True,
        }
        self.report = report
        self.ack = {
            key: report[key]
            for key in ("consumption_id", "acquisition_id", "state_version", "advanced")
        }
        self.ack["prefix"] = prefix
        self.budget = {
            "max_submissions": 16,
            "submissions_used": 1,
            "max_policy_calls": 32,
            "policy_calls_used": 1,
            "deadline_ms": 60000,
            "elapsed_ms": 10,
            "state": "inactive",
            "remaining_ms": 59990,
            "exhausted_reason": None,
            "ended_reason": "mode_changed",
        }
        environment = {
            **self.agent["requirements"]["environment"],
            "connector_protocol_version": self.agent["requirements"]["connector_protocol_version"],
            "runtime_instance_id": self.attempt["runtime_instance_id"],
            "environment_fingerprint": capture["session"]["environment_fingerprint"],
            "game_version": self.agent["support"]["game_versions"][0],
            "game_commit": self.agent["support"]["game_commits"][0],
        }
        self.events: list[dict[str, Any]] = []
        subscription = {
            **wire["attach"]["subscription"],
            "coverage": self.agent["input"]["attachment"]["required_seams"],
            "stream_generation": capture["stream_generation"],
            "scope_id": capture["scope_id"],
        }
        self.add(
            "native_session_attached",
            subscription=subscription,
            environment=environment,
        )
        self.add(
            "mode_changed",
            mode="one_step",
            autonomy_budget=self.budget,
            controller="released",
        )
        self.source_received(self.witness, "cursor-fixture")
        self.add("native_acquisition_registered", witness=self.witness)
        self.add(
            "agent_consumed",
            report=report,
            acknowledgement=self.ack,
            witness=self.witness,
        )
        self.add(
            "agent_directive",
            output={
                "continuity_token": report["continuity_token"],
                "consumption_id": report["consumption_id"],
                "state_version": 1,
                "directive": {
                    "type": "act",
                    "basis_acquisition_id": a["acquisition_id"],
                    "selection": {"kind": "handle", "action_id": action["action_id"]},
                    "scores": None,
                },
            },
        )
        self.add("controller_acquired", controller="held")
        self.add("native_submission_requested", **self.attempt)
        self.add("native_result", result=self.result)
        self.add("controller_released", controller="released")
        self.add(
            "handoff_to_human",
            reason="one_step_completed",
            autonomy_budget=self.budget,
            controller="released",
        )
        self.add(
            "stopped",
            autonomy_budget=self.budget,
            controller="released",
            pending_request=None,
            agent_state="known",
        )
        self.write()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add(self, kind: str, **payload: Any) -> None:
        self.events.append(
            {
                "schema": "sts2.policy-runtime/agent-session-event-1",
                "sequence": len(self.events) + 1,
                "recorded_at": "2026-10-08T00:00:30.000Z",
                "kind": kind,
                "payload": {"session_id": "session-fixture", "recovery_epoch": 0, **payload},
            }
        )

    def source_received(self, witness: dict[str, Any], cursor: str) -> None:
        wire = json.loads(
            (ROOT / "connector/contracts/fixtures/native-logical-v1.json").read_bytes()
        )["wire_samples"]
        original = copy.deepcopy(wire["event_batch"]["events"][0])
        original["availability"] = "available"
        original["event"].update(
            cursor=cursor,
            publication_index=witness["publication_index"],
            stream_generation=witness["capture"]["stream_generation"],
            scope_id=witness["capture"]["scope_id"],
            capture_ref=witness["capture"]["capture_id"],
            payload_reference=copy.deepcopy(witness["capture"]),
            missing_reason=None,
        )
        self.add("native_event_received", original=original, received_cursor=cursor)

    def event(self, kind: str) -> dict[str, Any]:
        return next(event for event in self.events if event["kind"] == kind)

    def write(self) -> None:
        self.run["agent_manifest_sha256"] = sha(canonical(self.agent))
        self.startup["agent_manifest_sha256"] = self.run["agent_manifest_sha256"]
        attestation = {
            "schema": "sts2.policy-runtime/agent-session-adapter-attestation-1",
            "run_id": self.run["run_id"],
            "manifest_id": self.agent["manifest_id"],
            "agent_manifest_sha256": self.run["agent_manifest_sha256"],
            "status": "attested",
            "expected": self.agent["adapter"],
            "actual": self.agent["adapter"],
            "attested_at": self.run["started_at"],
        }
        for name, value in (
            ("manifest.json", self.run),
            ("agent-manifest.json", self.agent),
            ("adapter-attestation.json", attestation),
        ):
            (self.directory / name).write_bytes(canonical(value) + b"\n")
        for index, event in enumerate(self.events, 1):
            event["sequence"] = index
        (self.directory / "events.jsonl").write_bytes(
            b"".join(canonical(event) + b"\n" for event in self.events)
        )
        self.rehash()

    def rehash(self) -> None:
        names = sorted(
            path.name
            for path in self.directory.iterdir()
            if path.is_file() and path.name not in {"evidence-manifest.json", "checksums.sha256"}
        )
        entries = [
            {
                "path": name,
                "bytes": len((self.directory / name).read_bytes()),
                "sha256": sha((self.directory / name).read_bytes()),
            }
            for name in names
        ]
        immutable = {
            "schema": "sts2.policy-runtime/immutable-evidence-manifest-1",
            "run_id": self.run["run_id"],
            "complete": True,
            "append_only": True,
            "files": entries,
            "manifest_sha256": sha(canonical({"run_id": self.run["run_id"], "files": entries})),
        }
        (self.directory / "evidence-manifest.json").write_bytes(canonical(immutable) + b"\n")
        (self.directory / "checksums.sha256").write_text(
            "".join(
                sha((self.directory / name).read_bytes()) + "  " + name + "\n"
                for name in sorted([*names, "evidence-manifest.json"])
            ),
            encoding="utf-8",
        )

    def check(self, passed: bool = True) -> Any:
        result = verify_agent_session_run_evidence(self.directory, self.startup)
        self.assertEqual(result.passed, passed, result.findings)
        return result

    def pending(self) -> None:
        result_event = self.event("native_result")
        result_event.update(
            {
                "kind": "native_request_pending",
                "payload": {
                    "session_id": "session-fixture",
                    "recovery_epoch": 0,
                    "original": self.original,
                },
            }
        )
        self.events[-1]["payload"]["pending_request"] = self.original
        self.run.update(tainted=True, status="tainted")
        self.write()

    def test_native_namespace_and_expected_identity(self) -> None:
        self.check()
        self.assertFalse(verify_agent_run_evidence(self.directory).passed)
        self.startup["agent_artifact_sha256"] = "e" * 64
        self.check(False)

    def test_registered_descriptor_is_additive(self) -> None:
        self.assertTrue(registry().verify(TYPE_ID, self.directory).passed)
        self.assertFalse(registry().verify("policy-runtime-agent-run", self.directory).passed)

    def test_rehashed_foreign_event_does_not_pass_checksums_alone(self) -> None:
        self.event("agent_consumed")["kind"] = "decision"
        self.write()
        self.check(False)

    def test_ack_boolean_does_not_alias_integer_version(self) -> None:
        self.ack["state_version"] = True
        self.write()
        self.check(False)

    def test_rehashed_acquisition_cannot_move_to_another_game_or_stream(self) -> None:
        self.witness["capture"]["session"]["runtime_instance_id"] = "replacement-game"
        self.write()
        self.check(False)

    def test_original_result_must_match_request_and_basis(self) -> None:
        for field in ("request_id", "snapshot_id"):
            with self.subTest(field=field):
                original = self.result[field]
                self.result[field] = "replacement-current"
                self.write()
                self.check(False)
                self.result[field] = original

    def test_unknown_delivery_cannot_be_cleared_by_human_stop(self) -> None:
        self.result["delivery"] = "unknown"
        self.write()
        self.check(False)
        self.run.update(tainted=True, status="tainted")
        self.write()
        self.check()

    def test_pending_stop_remains_tainted_with_original_request(self) -> None:
        self.pending()
        self.check()
        self.run.update(tainted=False, status="stopped")
        self.write()
        self.check(False)

    def test_explicit_known_reconciliation_appends_fact_before_clear(self) -> None:
        self.pending()
        stopped = self.events.pop()
        self.add(
            "native_request_reconciled",
            original=self.original,
            resolution="resolved",
            result=self.result,
        )
        stopped["payload"]["pending_request"] = None
        self.events.append(stopped)
        self.run.update(tainted=False, status="stopped")
        self.write()
        self.check()
        self.events[-2]["payload"]["original"]["basis_acquisition_id"] = "different-original"
        self.write()
        self.check(False)

    def test_pending_reconciliation_never_invents_terminal_result(self) -> None:
        self.pending()
        stopped = self.events.pop()
        self.add(
            "native_request_reconciled", original=self.original, resolution="pending", result=None
        )
        self.events.append(stopped)
        self.write()
        self.check()
        self.events[-2]["payload"]["result"] = self.result
        self.write()
        self.check(False)

    def test_sealed_transfer_retains_native_typed_content_id(self) -> None:
        verified = self.check().require_value()
        transfer = DirectoryTransferManifest.from_directory(
            self.directory, content_id=verified.content_id, artifact_type=TYPE_ID
        )

        def verify(directory: Path, manifest: DirectoryTransferManifest) -> None:
            self.assertEqual(manifest.artifact_type, TYPE_ID)
            self.assertEqual(
                verify_agent_session_run_evidence(directory).require_value().content_id,
                manifest.content_id,
            )

        receiver = DirectoryReceiver(
            ContentAddressedStore(Path(self.temporary.name) / "store"), promotion_verifier=verify
        )
        receipt = receiver.receive(self.directory, transfer)
        self.assertEqual(receipt.status, "promoted")

    def state(self) -> tuple[str, dict[str, Any]]:
        metadata = {
            "agent_artifact_id": self.agent["artifact"]["id"],
            "agent_artifact_sha256": self.agent["artifact"]["sha256"],
            "adapter_code_sha256": self.agent["adapter"]["code_sha256"],
            "model_bindings": self.agent["input"]["state_recovery"]["model_bindings"],
            "input_spec": self.agent["input"]["input_spec"],
            "profile": "native-logical-v1",
            "state_format_version": self.agent["input"]["state_format_version"],
            "stream_generation": self.witness["capture"]["stream_generation"],
            **{
                key: self.report[key]
                for key in ("continuity_token", "consumption_id", "state_version")
            },
            "prefix": self.ack["prefix"],
            "last_acknowledged_basis": {
                "acquisition_id": self.witness["acquisition_id"],
                "capture_sha256": self.witness["capture"]["sha256"],
                **{
                    key: self.witness[key]
                    for key in (
                        "snapshot_id",
                        "owner_occurrence",
                        "revision",
                        "included",
                        "publication_index",
                    )
                },
            },
        }
        raw = b"opaque fixture bytes; no Model codec assertion"
        digest = sha(raw)
        identity = sha(canonical({"metadata": metadata, "sha256": digest}))
        path, metadata_path = f"agent-state-{identity}.bin", f"agent-state-{identity}.json"
        (self.directory / path).write_bytes(raw)
        (self.directory / metadata_path).write_bytes(
            canonical(
                {
                    "schema": "sts2.policy-runtime/agent-state-snapshot-1",
                    "metadata": metadata,
                    "payload": {"path": path, "bytes": len(raw), "sha256": digest},
                }
            )
            + b"\n"
        )
        stopped = self.events.pop()
        self.add(
            "agent_state_stored",
            metadata=metadata,
            path=path,
            metadata_path=metadata_path,
            bytes=len(raw),
            sha256=digest,
        )
        self.events.append(stopped)
        self.write()
        return path, metadata

    def test_declared_opaque_state_is_hash_bound_without_decoding_a_model_codec(self) -> None:
        path, _ = self.state()
        self.check()
        (self.directory / path).write_bytes(b"tampered opaque bytes")
        self.rehash()
        self.check(False)

    def test_opaque_state_cannot_replace_last_acknowledged_input_basis(self) -> None:
        _, metadata = self.state()
        metadata["last_acknowledged_basis"]["capture_sha256"] = "0" * 64
        self.write()
        self.check(False)

    def test_state_recovery_requires_a_previously_stored_exact_metadata_record(self) -> None:
        _, metadata = self.state()
        stopped = self.events.pop()
        self.add("agent_state_restored", metadata=metadata)
        self.events.append(stopped)
        self.write()
        self.check()
        metadata = copy.deepcopy(metadata)
        metadata["consumption_id"] = "unacknowledged-prefix"
        self.events[-2]["payload"]["metadata"] = metadata
        self.write()
        self.check(False)

    def test_act_without_accepted_consumption_is_rejected_after_rehash(self) -> None:
        self.events.remove(self.event("agent_consumed"))
        self.write()
        self.check(False)

    def test_once_occurrence_cannot_claim_another_advance(self) -> None:
        event = copy.deepcopy(self.event("agent_consumed"))
        payload = event["payload"]
        payload["report"].update(
            previous_consumption_id="consumed-1",
            consumption_id="consumed-2",
            state_version=2,
            advanced=True,
        )
        payload["acknowledgement"].update(
            consumption_id="consumed-2",
            state_version=2,
            advanced=True,
        )
        self.events.insert(self.events.index(self.event("agent_consumed")) + 1, event)
        self.event("agent_directive")["payload"]["output"].update(
            consumption_id="consumed-2", state_version=2
        )
        self.write()
        self.check(False)

    def test_ack_publication_and_missing_scopes_must_follow_the_witness(self) -> None:
        original = copy.deepcopy(self.ack["prefix"])
        for field, value in (("consumed_publication_index", "0"), ("missing_scopes", ["catalog"])):
            with self.subTest(field=field):
                if field == "missing_scopes":
                    self.ack["prefix"]["omissions"][field] = value
                else:
                    self.ack["prefix"][field] = value
                self.write()
                self.check(False)
                self.ack["prefix"] = copy.deepcopy(original)

    def test_ack_cursor_must_be_the_latest_recorded_source_position(self) -> None:
        self.ack["prefix"]["received_cursor"] = "never-received-cursor"
        self.write()
        result = self.check(False)
        self.assertEqual(result.findings[0].code, "native_session_acknowledged_prefix")

    def test_publication_and_capture_must_join_the_original_received_source(
        self,
    ) -> None:
        baseline = copy.deepcopy(self.witness)
        for field in ("publication_index", "capture_id", "scope_id", "capture_ordinal"):
            with self.subTest(field=field):
                self.witness.clear()
                self.witness.update(copy.deepcopy(baseline))
                if field == "publication_index":
                    self.witness[field] = "123"
                    self.ack["prefix"]["consumed_publication_index"] = "123"
                else:
                    self.witness["capture"][field] = (
                        "replacement" if field != "capture_ordinal" else "123"
                    )
                self.write()
                result = self.check(False)
                self.assertEqual(
                    result.findings[0].code,
                    "native_session_acquisition_source_position",
                )
        self.witness.clear()
        self.witness.update(baseline)

    def test_completed_empty_batch_can_advance_opaque_cursor_without_an_input(
        self,
    ) -> None:
        consumed = self.event("agent_consumed")
        position = self.events.index(consumed) + 1
        self.events.insert(
            position,
            {
                **copy.deepcopy(consumed),
                "kind": "native_event_batch_received",
                "payload": {
                    "session_id": "session-fixture",
                    "recovery_epoch": 0,
                    "after_cursor": self.events[0]["payload"]["subscription"]["starting_cursor"],
                    "next_cursor": "global-Z",
                    "high_watermark": "global-Z",
                    "retained_start_cursor": "opaque-A",
                    "event_count": 1,
                },
            },
        )
        tail = copy.deepcopy(self.events[position])
        tail["payload"].update(
            after_cursor="global-Z",
            next_cursor="global-A",
            high_watermark="global-A",
            event_count=0,
        )
        self.events.insert(position + 1, tail)
        # A subsequent Current acquisition is neutral, but its new ACK reflects
        # the operational tail. The earlier accepted ACK remains unchanged.
        witness = copy.deepcopy(self.witness)
        witness.update(acquisition_id="current-query", publication_index=None)
        report = {
            **self.report,
            "acquisition_id": "current-query",
            "previous_consumption_id": "consumed-1",
            "advanced": False,
        }
        ack = {
            **copy.deepcopy(self.ack),
            "acquisition_id": "current-query",
            "advanced": False,
        }
        ack["prefix"]["received_cursor"] = "global-A"
        saved = [self.events[-1]]
        self.events = self.events[: position + 2]
        self.add("native_acquisition_registered", witness=witness)
        self.add("agent_consumed", report=report, acknowledgement=ack, witness=witness)
        self.events += saved
        self.write()
        self.check()
        self.assertEqual(self.ack["prefix"]["received_cursor"], "cursor-fixture")
        tail["payload"]["event_count"] = 1
        self.write()
        self.assertEqual(self.check(False).findings[0].code, "native_session_batch_prefix")

    def test_scoped_terminal_view_is_an_omission_until_a_source_position_is_consumed(
        self,
    ) -> None:
        wire = json.loads(
            (ROOT / "connector/contracts/fixtures/native-logical-v1.json").read_bytes()
        )["wire_samples"]
        self.agent["input"]["history_mode"] = "scoped_query"
        self.agent["input"]["attachment"]["delivery_mode"] = "scoped"
        self.agent["input"]["state_recovery"] = {
            "mode": "none",
            "max_state_bytes": 0,
            "model_bindings": [],
        }
        self.events[0]["payload"]["subscription"]["delivery_mode"] = "scoped"
        self.witness["publication_index"] = None
        self.ack["prefix"].update(history_mode="scoped_query", consumed_publication_index=None)
        self.ack["prefix"]["omissions"]["received_unconsumed_count"] = 1
        event = copy.deepcopy(wire["event_batch"]["events"][0])
        event["event"].update(
            kind="terminal",
            publication_index="12",
            stream_generation=self.witness["capture"]["stream_generation"],
            scope_id=self.witness["capture"]["scope_id"],
            capture_ref=self.witness["capture"]["capture_id"],
            payload_reference=copy.deepcopy(self.witness["capture"]),
        )
        existing = [item for item in self.events if item["kind"] != "native_event_received"]
        self.events = existing[:2]
        self.add(
            "native_event_received",
            original=event,
            received_cursor=event["event"]["cursor"],
        )
        self.ack["prefix"]["received_cursor"] = event["event"]["cursor"]
        self.events += existing[2:]
        self.write()
        self.check()
        self.ack["prefix"]["omissions"]["received_unconsumed_count"] = 0
        self.write()
        self.check(False)

    def _replacement_intent(self) -> None:
        stopped = self.events.pop()
        self.add("mode_changed", mode="auto", autonomy_budget=self.budget, controller="held")
        self.add(
            "agent_directive",
            output=copy.deepcopy(self.event("agent_directive")["payload"]["output"]),
        )
        replacement = {**self.attempt, "request_id": "replacement-request"}
        self.add("native_submission_requested", **replacement)
        self.events.append(stopped)
        self.write()

    def test_unknown_terminal_cannot_authorize_a_replacement_submission(self) -> None:
        self.result["delivery"] = "unknown"
        self.run.update(tainted=True, status="tainted")
        self._replacement_intent()
        self.check(False)

    def test_pending_original_cannot_authorize_a_replacement_submission(self) -> None:
        self.pending()
        self._replacement_intent()
        self.check(False)

    def test_missing_terminal_result_cannot_end_as_clean_stop(self) -> None:
        self.events.remove(self.event("native_result"))
        self.write()
        self.check(False)

    def test_pre_submit_closure_is_original_bound_and_is_not_a_native_result(
        self,
    ) -> None:
        self.events.remove(self.event("native_result"))
        stopped = self.events.pop()
        self.add(
            "native_submission_not_started",
            request_id=self.attempt["request_id"],
            submission_epoch=0,
            reason="runtime_recovery_epoch_mismatch",
        )
        self.events[-1]["payload"]["recovery_epoch"] = 1
        stopped["payload"]["recovery_epoch"] = 1
        self.events.append(stopped)
        self.write()
        self.check()
        for key, value in (("request_id", "another-intent"), ("submission_epoch", 1)):
            with self.subTest(field=key):
                original = self.events[-2]["payload"][key]
                self.events[-2]["payload"][key] = value
                self.write()
                self.check(False)
                self.events[-2]["payload"][key] = original

    def test_pending_post_cannot_be_relabelled_as_not_started(self) -> None:
        self.pending()
        stopped = self.events.pop()
        self.add(
            "native_submission_not_started",
            request_id=self.attempt["request_id"],
            submission_epoch=0,
            reason="not_a_post_start_escape",
        )
        self.events.append(stopped)
        self.write()
        self.check(False)

    def test_closed_manifest_matches_runtime_state_scope_and_count_contract(self) -> None:
        cases = (
            (
                "state_32MiB",
                lambda a: a["input"]["state_recovery"].update(max_state_bytes=32 * 1024 * 1024),
            ),
            (
                "models_17",
                lambda a: a["input"]["state_recovery"].update(
                    model_bindings=[
                        {"model_id": f"model-{i}", "weights_sha256": "a" * 64} for i in range(17)
                    ]
                ),
            ),
            (
                "sampled_full_reference",
                lambda a: a["input"]["attachment"]["required_seams"][0].update(coverage="sampled"),
            ),
            ("scoped_full_delivery", lambda a: a["input"].update(history_mode="scoped_query")),
            ("long_artifact_path", lambda a: a["artifact"].update(path="x" * 4097)),
            (
                "long_seam_id",
                lambda a: a["input"]["attachment"]["required_seams"][0].update(
                    source_seam="x" * 129
                ),
            ),
            (
                "zero_opaque_budget",
                lambda a: a["input"]["state_recovery"].update(max_state_bytes=0),
            ),
        )
        original = copy.deepcopy(self.agent)
        for name, mutate in cases:
            with self.subTest(name=name):
                self.agent = copy.deepcopy(original)
                mutate(self.agent)
                self.write()
                self.check(False)
        for count in (0, 16):
            with self.subTest(valid_model_count=count):
                self.agent = copy.deepcopy(original)
                self.agent["input"]["state_recovery"].update(
                    max_state_bytes=16 * 1024 * 1024,
                    model_bindings=[
                        {"model_id": f"model-{i}", "weights_sha256": "a" * 64} for i in range(count)
                    ],
                )
                self.write()
                self.check()

    def test_actual_owner_ledger_vectors_preserve_neutral_incremental_and_null_input(
        self,
    ) -> None:
        vectors = json.loads(
            (
                Path(__file__).parent
                / "fixtures/native_agent_session/owner-ledger-conformance.json"
            ).read_bytes()
        )
        for record in vectors["records"]:
            with self.subTest(case=record["name"]):
                current = NativeAgentSessionEvidenceTests()
                current.setUp()
                try:
                    current.agent["input"] = copy.deepcopy(record["input"])
                    attachment = copy.deepcopy(current.events[0])
                    capture = record["messages"][0]["witness"]["capture"]
                    attachment["payload"]["environment"].update(capture["session"])
                    attachment["payload"]["subscription"].update(
                        stream_generation=capture["stream_generation"],
                        scope_id=capture["scope_id"],
                        eager_scope=current.agent["input"]["attachment"]["eager_scope"],
                        delivery_mode=current.agent["input"]["attachment"]["delivery_mode"],
                    )
                    current.events = [attachment]
                    current.budget["submissions_used"] = 0
                    previous_ack = None
                    cursor = attachment["payload"]["subscription"]["starting_cursor"]
                    for message in record["messages"]:
                        witness, report = (
                            copy.deepcopy(message["witness"]),
                            copy.deepcopy(message["report"]),
                        )
                        ack = copy.deepcopy(message.get("acknowledgement"))
                        if ack is None:
                            assert previous_ack is not None
                            ack = {
                                key: report[key]
                                for key in (
                                    "consumption_id",
                                    "acquisition_id",
                                    "state_version",
                                    "advanced",
                                )
                            }
                            ack["prefix"] = copy.deepcopy(previous_ack["prefix"])
                        next_cursor = ack["prefix"]["received_cursor"]
                        if witness["publication_index"] is not None:
                            current.source_received(witness, next_cursor)
                        else:
                            current.add(
                                "native_event_batch_received",
                                after_cursor=cursor,
                                next_cursor=next_cursor,
                                high_watermark=next_cursor,
                                retained_start_cursor=cursor,
                                event_count=0,
                            )
                        current.add("native_acquisition_registered", witness=witness)
                        current.add(
                            "agent_consumed",
                            witness=witness,
                            report=report,
                            acknowledgement=ack,
                        )
                        if witness["publication_index"] is not None:
                            current.add(
                                "native_event_batch_received",
                                after_cursor=cursor,
                                next_cursor=next_cursor,
                                high_watermark=next_cursor,
                                retained_start_cursor=cursor,
                                event_count=1,
                            )
                        cursor = next_cursor
                        previous_ack = ack
                    current.add(
                        "stopped",
                        autonomy_budget=current.budget,
                        controller="released",
                        pending_request=None,
                        agent_state="known",
                    )
                    current.write()
                    current.check(all(m["accepted"] for m in record["messages"]))
                finally:
                    current.tearDown()


if __name__ == "__main__":
    unittest.main()
