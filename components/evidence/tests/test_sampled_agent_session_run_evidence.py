"""Independent typed sample inventory/ACK joins; all captures are synthetic."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import test_agent_session_run_evidence as old
from test_agent_session_run_evidence import canonical, sha, ROOT
from sts2_platform_evidence import verify_agent_session_run_evidence
from sts2_platform_evidence.agent_session_run_evidence import _EVENT_FIELDS, _CONTEXT
from sts2_platform_evidence.source_session_bundle import _catalog_digest


class SampledAgentSessionEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.f = old.NativeAgentSessionEvidenceTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        shared = json.loads((ROOT / "policy-runtime/contracts/fixtures/sampled-current-carry-v1.json").read_bytes())
        self.shared = shared
        f = self.f
        f.agent = copy.deepcopy(shared["manifest"])
        attachment = f.events[0]
        capture = shared["frames"]["map_a"]["capture"]
        attachment["payload"]["subscription"].update(eager_scope=[], delivery_mode="scoped",
                                                      stream_generation=capture["stream_generation"], scope_id=capture["scope_id"])
        attachment["payload"]["environment"].update(runtime_instance_id=capture["session"]["runtime_instance_id"],
                                                     environment_fingerprint=capture["session"]["environment_fingerprint"])
        f.events = [attachment]
        self.add_sample("map_a", 1)
        self.end()
        f.write()

    def add_sample(self, name: str, version: int) -> None:
        f = self.f
        frame = self.shared["frames"][name]
        capture, body, actions = frame["capture"], frame["observation"], frame["catalog"]
        acquisition = "acq-" + name
        witness = {"acquisition_id": acquisition, "capture": capture, "publication_index": None,
                   "snapshot_id": body["snapshot_id"], "revision": body["revision"],
                   "owner_occurrence": body["owner_occurrence"], "status": body["status"],
                   "included": body["completeness"]["included"], "missing": [],
                   "catalog_digest": body["catalog"]["digest"], "catalog_count": len(actions),
                   "catalog_materialized": True}
        f.add("agent_sample_next_requested", request_id="parent-N" + str(version), input={
            "continuity_token": "segment-1", "consumption_id": None if version == 1 else "consume-" + str(version-1),
            "state_version": version - 1, "basis_acquisition_id": None if version == 1 else "acq-" + ("map_a" if version == 2 else "inspect_b"),
            "received_cursor": f.events[0]["payload"]["subscription"]["starting_cursor"]})
        f.add("native_acquisition_registered", witness=witness)
        f.add("agent_sample_query_offered", acquisition_id=acquisition, request_id="child-query-" + str(version))
        binding = {"acquisition_id": acquisition, "input_spec": f.agent["input"]["input_spec"],
                   "continuity_token": "segment-1", "publication_index": None, "capture": capture}
        stem = "agent-sample-" + sha(canonical(binding))
        raw = frame["observation_utf8"].encode("utf-8")
        catalog = canonical(actions)
        metadata = {"schema": "sts2.policy-runtime/agent-sample-input-1", **binding,
                    "observation": {"path": stem + ".observation.json", "bytes": len(raw), "sha256": sha(raw)},
                    "catalog": {"path": stem + ".catalog.json", "bytes": len(catalog), "sha256": sha(catalog),
                                "digest": body["catalog"]["digest"], "count": len(actions)}}
        for path, value in ((metadata["observation"]["path"], raw), (metadata["catalog"]["path"], catalog),
                            (stem + ".json", canonical(metadata) + b"\n")):
            (f.directory / path).write_bytes(value)
        f.add("agent_sample_input_stored", metadata_path=stem + ".json", metadata=metadata,
              disposition="consume_proposed", proposal=None)
        if version == 1:
            f.add("agent_sample_segment_started", continuity_token="segment-1", acquisition_id=acquisition)
        report = {"acquisition_id": acquisition, "input_spec": f.agent["input"]["input_spec"],
                  "continuity_token": "segment-1", "previous_consumption_id": None if version == 1 else "consume-" + str(version-1),
                  "consumption_id": "consume-" + str(version), "state_version": version, "advanced": True}
        sample_event = next(e for e in reversed(f.events) if e["kind"] == "agent_sample_input_stored")
        sample_event["payload"]["proposal"] = {"session_id": "session-fixture", "recovery_epoch": 0, "request_id": "child-consume-" + str(version), "report": report}
        ack = {key: report[key] for key in ("acquisition_id", "consumption_id", "state_version", "advanced")}
        ack["prefix"] = {"continuity_token": "segment-1", "history_mode": "sampled_current",
                         "consumption_mode": "once_per_occurrence",
                         "received_cursor": f.events[0]["payload"]["subscription"]["starting_cursor"],
                         "consumed_publication_index": None,
                         "omissions": {"received_unconsumed_count": 0, "missing_scopes": [], "gap": None}}
        f.add("agent_consumed", report=report, acknowledgement=ack, witness=witness)
        f.add("agent_sample_consume_ack_offered", request_id="child-consume-" + str(version), acknowledgement=ack)
        directive = {"type": "act", "basis_acquisition_id": acquisition,
                     "selection": {"kind": "handle", "action_id": actions[0]["action_id"]}, "scores": None}
        output = {"continuity_token": "segment-1", "consumption_id": report["consumption_id"],
                  "state_version": version, "directive": directive}
        f.add("agent_sample_next_completed", request_id="parent-N" + str(version), output=copy.deepcopy(output))
        f.add("agent_directive", output=copy.deepcopy(output))
        self.last_version = version

    def end(self) -> None:
        f = self.f
        f.add("agent_sample_segment_ended", continuity_token="segment-1", reason="external_stop", state_version=self.last_version)
        f.add("stopped", autonomy_budget=f.budget, controller="released", pending_request=None, agent_state="known")

    def verify(self) -> str:
        return verify_agent_session_run_evidence(self.f.directory).status

    def test_malformed_sample_metadata_containers_fail_through_the_public_verifier(self) -> None:
        payload = self.f.event("agent_sample_input_stored")["payload"]
        for malformed in ([], None, "not-an-object", 17, True):
            with self.subTest(metadata=malformed):
                payload["metadata"] = malformed
                self.f.write()  # Recompute every outer inventory/checksum to reach typed validation.
                result = verify_agent_session_run_evidence(self.f.directory)
                self.assertEqual(result.status, "fail")
                self.assertEqual(result.findings[0].code, "native_session_object_required")

    def test_shared_event_field_sets_match_the_independent_closed_validator(self) -> None:
        for kind, fields in self.shared["evidence"]["event_fields"].items():
            self.assertEqual(sorted(_EVENT_FIELDS[kind] | _CONTEXT), fields)

    def test_three_real_units_with_equal_map_features_remain_three_samples(self) -> None:
        self.f.events = self.f.events[:-2]
        self.add_sample("inspect_b", 2)
        self.add_sample("map_c", 3)
        self.end(); self.f.write()
        self.assertEqual(self.verify(), "pass")

    def test_independent_verifier_binds_exact_original_bytes_and_complete_catalog(self) -> None:
        self.assertEqual(self.verify(), "pass")
        sample = self.f.event("agent_sample_input_stored")["payload"]["metadata"]
        (self.f.directory / sample["observation"]["path"]).write_bytes(b'{}')
        self.f.rehash()
        self.assertEqual(self.verify(), "fail")

    def test_missing_catalog_and_extra_sample_file_fail_inventory_after_rehash(self) -> None:
        sample = self.f.event("agent_sample_input_stored")["payload"]["metadata"]
        (self.f.directory / sample["catalog"]["path"]).unlink(); self.f.rehash()
        self.assertEqual(self.verify(), "fail")

    def test_changed_catalog_member_with_rehashed_container_does_not_pass_structural_binding(self) -> None:
        event = self.f.event("agent_sample_input_stored")
        meta = event["payload"]["metadata"]
        catalog = json.loads((self.f.directory / meta["catalog"]["path"]).read_bytes())
        catalog[0]["verb"] = "forged"
        raw = canonical(catalog)
        meta["catalog"].update(bytes=len(raw), sha256=sha(raw))
        (self.f.directory / meta["catalog"]["path"]).write_bytes(raw)
        (self.f.directory / event["payload"]["metadata_path"]).write_bytes(canonical(meta)+b"\n")
        self.f.write()
        self.assertEqual(self.verify(), "fail")

    def test_ack_without_original_sample_fails_even_when_all_container_hashes_match(self) -> None:
        self.f.events = [event for event in self.f.events if event["kind"] != "agent_sample_input_stored"]
        self.f.write(); self.assertEqual(self.verify(), "fail")

    def test_offer_is_not_consumption_and_requires_explicit_disposition(self) -> None:
        # Preserve original query bytes on an interrupted call without claiming ACK.
        self.f.events = [event for event in self.f.events if event["kind"] in {
            "native_session_attached", "agent_sample_next_requested", "native_acquisition_registered",
            "agent_sample_query_offered", "agent_sample_input_stored"}]
        self.f.event("agent_sample_input_stored")["payload"].update(disposition="query_offered", proposal=None)
        self.f.add("stopped", autonomy_budget=self.f.budget, controller="released", pending_request=None, agent_state="uncertain")
        self.f.write(); self.assertEqual(self.verify(), "pass")
        self.f.events = [event for event in self.f.events if event["kind"] != "agent_sample_input_stored"]
        for path in self.f.directory.glob("agent-sample-*"): path.unlink()
        self.f.write(); self.assertEqual(self.verify(), "fail")

    def test_directive_does_not_substantiate_ack_until_write_attempt(self) -> None:
        self.f.events = [event for event in self.f.events if event["kind"] != "agent_sample_consume_ack_offered"]
        self.f.write(); self.assertEqual(self.verify(), "fail")

    def test_unmatched_or_duplicate_ack_and_reentry_after_segment_end_fail(self) -> None:
        ack = copy.deepcopy(self.f.event("agent_sample_consume_ack_offered"))
        self.f.events.insert(-2, ack); self.f.write(); self.assertEqual(self.verify(), "fail")

    def test_ledger_or_ack_write_without_child_directive_cannot_claim_known_stop(self) -> None:
        baseline = copy.deepcopy(self.f.events)
        for keep_ack in (False, True):
            self.f.events = [event for event in copy.deepcopy(baseline) if event["kind"] not in {
                "agent_directive", "agent_sample_next_completed"}
                and (keep_ack or event["kind"] != "agent_sample_consume_ack_offered")]
            self.f.write(); self.assertEqual(self.verify(), "fail")
            self.f.event("stopped")["payload"]["agent_state"] = "uncertain"
            self.f.write(); self.assertEqual(self.verify(), "pass")

    def test_ack_reply_parent_id_context_and_basis_are_exact(self) -> None:
        baseline = copy.deepcopy(self.f.events)
        for key, value in (("request_id", "parent-forged"), ("recovery_epoch", 1)):
            self.f.events = copy.deepcopy(baseline)
            self.f.event("agent_sample_next_completed")["payload"][key] = value
            self.f.write(); self.assertEqual(self.verify(), "fail")
        self.f.events = copy.deepcopy(baseline)
        self.f.event("agent_sample_next_requested")["payload"]["input"]["basis_acquisition_id"] = "forged-basis"
        self.f.write(); self.assertEqual(self.verify(), "fail")

    def test_ack_write_context_and_child_phase_bind_the_original_pending_next(self) -> None:
        baseline = copy.deepcopy(self.f.events)
        self.f.event("agent_sample_consume_ack_offered")["payload"]["recovery_epoch"] = 1
        self.f.write(); self.assertEqual(self.verify(), "fail")
        self.f.events = copy.deepcopy(baseline)
        completed = next(i for i, event in enumerate(self.f.events) if event["kind"] == "agent_sample_next_completed")
        late_query = copy.deepcopy(self.f.event("agent_sample_query_offered"))
        late_query["payload"]["request_id"] = "child-late-query"
        self.f.events.insert(completed+1, late_query)
        self.f.write(); self.assertEqual(self.verify(), "fail")
        self.f.events = copy.deepcopy(baseline)
        ack = self.f.event("agent_sample_consume_ack_offered")
        self.f.events.remove(ack)
        self.f.events.insert(next(i for i,e in enumerate(self.f.events) if e["kind"] == "agent_sample_next_completed")+1, ack)
        self.f.write(); self.assertEqual(self.verify(), "fail")

    def test_queried_proposal_original_context_is_not_current_recovery_context(self) -> None:
        self.f.event("agent_sample_input_stored")["payload"]["proposal"]["recovery_epoch"] = 1
        self.f.write(); self.assertEqual(self.verify(), "fail")

    def test_sampled_handle_directive_requires_exact_stored_original_member(self) -> None:
        for kind in ("agent_sample_next_completed", "agent_directive"):
            self.f.event(kind)["payload"]["output"]["directive"]["selection"]["action_id"] = "forged-nonmember"
        self.f.write(); self.assertEqual(self.verify(), "fail")

    def test_original_basis_catalog_is_used_instead_of_latest_or_foreign_member(self) -> None:
        self.f.events = self.f.events[:-2]
        self.add_sample("inspect_b", 2)
        self.end()
        for event in self.f.events:
            if event["kind"] in {"agent_sample_next_completed", "agent_directive"} and event["payload"]["output"]["state_version"] == 2:
                event["payload"]["output"]["directive"]["selection"]["action_id"] = self.shared["frames"]["map_a"]["catalog"][0]["action_id"]
        self.f.write(); self.assertEqual(self.verify(), "fail")

    def _submission(self, *, expression: bool = False, forged_id: bool = False, changed_member: bool = False) -> None:
        f = self.f
        f.events = f.events[:-2]
        witness = f.event("native_acquisition_registered")["payload"]["witness"]
        action = copy.deepcopy(self.shared["frames"]["map_a"]["catalog"][0])
        if expression:
            for kind in ("agent_sample_next_completed", "agent_directive"):
                f.event(kind)["payload"]["output"]["directive"]["selection"] = {"kind": "expression", "expression": {"verb": "inspect"}}
        if forged_id: action["action_id"] = "resolved-forged-member"
        f.add("controller_acquired", controller="held")
        attempt = {"request_id": "original-request", "basis_acquisition_id": witness["acquisition_id"],
                   "snapshot_id": witness["snapshot_id"], "action_id": action["action_id"],
                   "catalog_digest": witness["catalog_digest"], "run_id": f.run["run_id"],
                   "runtime_instance_id": witness["capture"]["session"]["runtime_instance_id"]}
        f.add("native_submission_requested", **attempt)
        wire = json.loads((ROOT / "connector/contracts/fixtures/native-logical-v1.json").read_bytes())["wire_samples"]
        if changed_member: action["verb"] = "forged_same_id_verb"
        result = {**wire["result"], "request_id": attempt["request_id"], "snapshot_id": attempt["snapshot_id"],
                  "action": action, "delivery": "delivered", "execution": "native_accepted", "effect": "pending",
                  "cancel": "not_requested", "stages": [], "reason": None, "observed_frame": None, "attribution": None}
        f.add("native_result", result=result)
        f.add("controller_released", controller="released")
        self.end(); f.write()

    def test_expression_resolution_is_connector_owned_but_submitted_member_still_binds_full_c(self) -> None:
        self._submission(expression=True); self.assertEqual(self.verify(), "pass")
        event = self.f.event("native_submission_requested")
        event["payload"]["action_id"] = "resolved-forged-member"
        self.f.event("native_result")["payload"]["result"]["action"]["action_id"] = "resolved-forged-member"
        self.f.write(); self.assertEqual(self.verify(), "fail")

    def test_same_member_id_with_different_native_verb_subject_or_arguments_fails(self) -> None:
        self._submission(); self.assertEqual(self.verify(), "pass")
        action = self.f.event("native_result")["payload"]["result"]["action"]
        original = copy.deepcopy(action)
        for key, value in (("verb", "forged"), ("subject_referent_id", "forged-referent"),
                           ("arguments", [{"role": "target", "referent_id": "forged-referent"}])):
            action.clear(); action.update(copy.deepcopy(original)); action[key] = value
            self.f.write(); self.assertEqual(self.verify(), "fail")

    def test_unbound_extra_sample_file_fails_even_with_valid_checksums(self) -> None:
        (self.f.directory / ("agent-sample-" + "a"*64 + ".json")).write_bytes(b'{}')
        self.f.rehash(); self.assertEqual(self.verify(), "fail")


class OwnedStaleEvidenceFixture:
    """Complete opt-in original trace over the existing synthetic owner writer.

    No game/HTTP/controller/model runs here. Consumer tests may replace only
    their declared synthetic producer metadata, not the event/sample grammar.
    The writer retains real byte inventory, sample hashes and full result joins.
    """

    def __init__(self) -> None:
        self.base = SampledAgentSessionEvidenceTests()
        self.base.setUp()
        self.f = self.base.f
        self.owned = json.loads((ROOT / "policy-runtime/contracts/fixtures/owned-current-known-stale-v1.json").read_bytes())
        self.f.agent = copy.deepcopy(self.owned["manifest"])
        self.f.run.update(manifest_id=self.f.agent["manifest_id"],
                          agent_id=self.f.agent["agent"]["id"],
                          agent_version=self.f.agent["agent"]["version"],
                          agent_artifact_sha256=self.f.agent["artifact"]["sha256"])
        self.f.startup["agent_manifest_id"] = self.f.agent["manifest_id"]
        self.f.events = self.f.events[:-2]
        initial = copy.deepcopy(self.owned["initial_mode_event"])
        initial["session_id"] = "session-fixture"
        self.f.events.insert(1, {"schema": "sts2.policy-runtime/agent-session-event-1",
                                 "sequence": 2, "recorded_at": self.f.events[0]["recorded_at"],
                                 "kind": "mode_changed", "payload": initial})
        self.f.event("agent_sample_next_requested")["payload"]["input"]["operational_outcome"] = None
        self.version = 1
        self.frame_name = "map_a"
        self.basis = "acq-map_a"
        self.consumption = "consume-1"
        self.binding = copy.deepcopy(self.owned["submission"]["dispatch_binding"])
        self.outcome = None
        self.requests = 0
        self.queries = 0
        self.total = self.streak = 0
        self.held = False

    @property
    def directory(self) -> Path:
        return self.f.directory

    def close(self) -> None:
        self.base.doCleanups()

    def initial_action_id(self, action_id: str) -> None:
        """Rebuild only this newly generated synthetic initial Current/C."""
        frame = self.base.shared["frames"]["map_a"]
        frame["catalog"][0]["action_id"] = action_id
        frame["observation"]["catalog"]["digest"] = _catalog_digest(frame["catalog"])
        raw = json.dumps(frame["observation"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        frame["observation_utf8"] = raw.decode("utf-8")
        frame["capture"].update(sha256=sha(raw), byte_count=len(raw))
        self.f.events = self.f.events[:2]
        for file in self.directory.glob("agent-sample-*.json"):
            file.unlink()  # Owned temporary synthetic files, never an original run.
        self.base.add_sample("map_a", 1)
        self.f.events[2]["payload"]["input"]["operational_outcome"] = None

    def sample(self, name: str = "inspect_b") -> None:
        previous = self.basis
        self.version += 1
        frame = copy.deepcopy(self.owned["frames"][name])
        frame["observation"]["session"]["runtime_instance_id"] = self.binding["runtime_instance_id"]
        frame["capture"]["session"]["runtime_instance_id"] = self.binding["runtime_instance_id"]
        key = name + "-" + str(self.version)
        body = frame["observation"]
        body.update(snapshot_id=key, revision=self.version)
        body["catalog"].update(snapshot_id=key, catalog_ref=key + "-catalog")
        body["owner_occurrence"].update(occurrence_id=key, binding_revision=key)
        for action in frame["catalog"]:
            action["action_id"] += "-" + str(self.version)
        body["catalog"]["digest"] = _catalog_digest(frame["catalog"])
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        frame["capture"].update(snapshot_id=key, capture_id=key + "-capture",
                                capture_ordinal=str(self.version), byte_count=len(raw), sha256=sha(raw))
        frame["observation_utf8"] = raw.decode("utf-8")
        self.base.shared["frames"][key] = frame
        start = len(self.f.events)
        self.base.add_sample(key, self.version)
        input_ = self.f.events[start]["payload"]["input"]
        input_["basis_acquisition_id"] = previous
        input_["operational_outcome"] = copy.deepcopy(self.outcome)
        self.frame_name, self.basis = key, "acq-" + key
        self.consumption = "consume-" + str(self.version)
        self.outcome = None  # New advanced ACK plus matched completed Next.

    def readiness(self) -> None:
        self.queries += 1
        suffix = str(self.queries)
        request, acquisition = "parent-ready-" + suffix, "acq-ready-" + suffix
        previous = next(e["payload"]["witness"] for e in reversed(self.f.events)
                        if e["kind"] == "agent_consumed")
        witness = copy.deepcopy(previous)
        witness["acquisition_id"] = acquisition
        cursor = self.f.events[0]["payload"]["subscription"]["starting_cursor"]
        self.f.add("agent_sample_next_requested", request_id=request, input={
            "continuity_token": "segment-1", "consumption_id": self.consumption,
            "state_version": self.version, "basis_acquisition_id": self.basis,
            "received_cursor": cursor, "operational_outcome": copy.deepcopy(self.outcome)})
        self.f.add("native_acquisition_registered", witness=witness)
        self.f.add("agent_sample_query_offered", acquisition_id=acquisition,
                   request_id="child-ready-" + suffix)
        output = {"continuity_token": "segment-1", "consumption_id": self.consumption,
                  "state_version": self.version, "directive": {
                      "type": "await", "after_cursor": cursor, "condition": "any_event", "timeout_ms": 250}}
        self.f.add("agent_sample_next_completed", request_id=request, output=copy.deepcopy(output))
        self.f.add("agent_directive", output=copy.deepcopy(output))
        self.f.add("agent_sample_query_discarded", acquisition_id=acquisition, reason="readiness_check")

    def submission(self) -> dict:
        self.requests += 1
        if not self.held:
            self.f.add("controller_acquired", controller="held")
            self.held = True
        frame = self.base.shared["frames"][self.frame_name]
        attempt = {"request_id": "request-" + str(self.requests), "basis_acquisition_id": self.basis,
                   "snapshot_id": frame["observation"]["snapshot_id"],
                   "action_id": frame["catalog"][0]["action_id"],
                   "catalog_digest": frame["observation"]["catalog"]["digest"],
                   "run_id": self.f.run["run_id"], "runtime_instance_id": self.binding["runtime_instance_id"],
                   "dispatch_binding": copy.deepcopy(self.binding)}
        self.f.add("native_submission_requested", **attempt)
        return attempt

    def terminal(self, attempt: dict, *, delivery: str = "not_started", defer: bool = False,
                 reason: str | None = "stale_snapshot_or_binding") -> dict:
        result = copy.deepcopy(self.owned["result"])
        result["attribution"].update(self.binding)
        result.update(request_id=attempt["request_id"], snapshot_id=attempt["snapshot_id"],
                      delivery=delivery, reason=reason)
        if delivery in {"delivered", "partially_delivered", "unknown"}:
            result["action"] = copy.deepcopy(self.base.shared["frames"][self.frame_name]["catalog"][0])
        if delivery == "delivered":
            result.update(execution="native_accepted", effect="pending", cancel="not_requested", reason=None)
            self.streak = 0
        elif delivery == "not_started" and reason == "stale_snapshot_or_binding":
            self.total += 1
            self.streak += 1
        self.f.add("native_result", result=result)
        if defer:
            self.f.add("native_stale_decision_deferred", request_id=attempt["request_id"],
                       basis_acquisition_id=self.basis, consumption_id=self.consumption,
                       state_version=self.version, known_stale_rejections=self.total,
                       consecutive_known_stale_rejections=self.streak)
            self.outcome = {"schema": "sts2.policy-runtime/known-not-started-outcome-1",
                            "basis_acquisition_id": self.basis, "action_id": attempt["action_id"],
                            "consumption_id": self.consumption, "state_version": self.version,
                            "result": copy.deepcopy(result)}
        if delivery in {"partially_delivered", "unknown"}:
            self.f.run["tainted"] = True
        return result

    def stop(self, reason: str = "external_stop", *, pending: dict | None = None) -> None:
        if self.held:
            self.f.add("controller_released", controller="released")
            self.held = False
        self.base.last_version = self.version
        self.base.end()
        self.f.events[-2]["payload"]["reason"] = reason
        self.f.events[-1]["payload"]["pending_request"] = pending
        if pending is not None:
            self.f.run["tainted"] = True
        self.f.write()


class OwnedStaleAgentSessionEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = OwnedStaleEvidenceFixture()
        self.addCleanup(self.fixture.close)

    def check(self, passed: bool = True):
        result = verify_agent_session_run_evidence(self.fixture.directory)
        self.assertEqual(result.passed, passed, result.findings)
        return result

    def fresh(self) -> OwnedStaleEvidenceFixture:
        fixture = OwnedStaleEvidenceFixture()
        self.addCleanup(fixture.close)
        return fixture

    def assert_fixture(self, fixture: OwnedStaleEvidenceFixture, passed: bool = True):
        result = verify_agent_session_run_evidence(fixture.directory)
        self.assertEqual(result.passed, passed, result.findings)
        return result

    def test_closed_opt_in_stale_readiness_new_ack_and_delivered_original_member(self) -> None:
        f = self.fixture
        f.terminal(f.submission(), defer=True)
        f.readiness(); f.readiness()
        f.sample(); f.terminal(f.submission(), delivery="delivered")
        f.stop()
        result = self.check()
        summary = result.value.terminal_summary
        self.assertEqual({key: summary[key] for key in (
            "original_submission_count", "terminal_result_count", "known_delivered",
            "known_stale_rejections", "consecutive_known_stale_rejections")}, {
                "original_submission_count": 2, "terminal_result_count": 2, "known_delivered": 1,
                "known_stale_rejections": 1, "consecutive_known_stale_rejections": 0})
        self.assertEqual(summary["run_id"], result.value.run_id)
        self.assertEqual(summary["content_id"], result.value.content_id)
        self.assertFalse(summary["live_eligibility_proved"])
        summary["known_stale_rejections"] = 900
        self.assertEqual(result.value.terminal_summary["known_stale_rejections"], 1)

    def test_threshold_terminal_counts_without_any_deferred_marker(self) -> None:
        f = self.fixture
        for i in range(3):
            if i: f.sample("inspect_b" if i == 1 else "map_c")
            f.terminal(f.submission(), defer=i < 2)
        f.stop("known_stale_streak_limit")
        summary = self.check().value.terminal_summary
        self.assertEqual(summary["known_stale_rejections"], 3)
        self.assertEqual(summary["consecutive_known_stale_rejections"], 3)
        self.assertEqual(summary["terminal_result_count"], 3)

    def test_policy_bounds_modes_methods_and_unknown_fields_are_closed(self) -> None:
        mutations = [
            lambda m: m["execution_policy"].update(schema="future"),
            lambda m: m["execution_policy"].update(current_mode="legacy"),
            lambda m: m["execution_policy"].update(extra=True),
            lambda m: m["execution_policy"].update(max_known_stale_rejections=0),
            lambda m: m["execution_policy"].update(max_known_stale_rejections=True),
            lambda m: m["execution_policy"].update(max_known_stale_rejections=17),
            lambda m: m["execution_policy"].update(max_consecutive_known_stale_rejections=5),
            lambda m: m["execution_policy"].update(max_known_stale_rejections=1,
                                                   max_consecutive_known_stale_rejections=2),
            lambda m: m["input"].update(history_mode="scoped_query"),
            lambda m: m["input"].update(consumption_mode="incremental_view"),
            lambda m: m["input"]["attachment"].update(eager_scope=["persistent"]),
            lambda m: m["requirements"]["required_methods"].remove("current_owned"),
            lambda m: m.update(execution_policy=None),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                f = self.fresh(); mutation(f.f.agent); f.stop()
                self.assert_fixture(f, False)

    def test_no_policy_keeps_exact_five_next_and_event_shapes(self) -> None:
        f = self.fresh(); f.f.agent = copy.deepcopy(f.owned["legacy_manifest"])
        f.f.agent["manifest_id"] = f.f.run["manifest_id"]
        f.stop(); self.assert_fixture(f, False)  # New sixth field is not tolerated.
        for event in f.f.events:
            if event["kind"] == "agent_sample_next_requested":
                del event["payload"]["input"]["operational_outcome"]
        f.f.write()
        self.assertIsNone(self.assert_fixture(f).value.terminal_summary)
        f.f.events.insert(-2, {"schema": "sts2.policy-runtime/agent-session-event-1",
                              "sequence": 0, "recorded_at": f.f.events[0]["recorded_at"],
                              "kind": "native_stale_decision_deferred",
                              "payload": copy.deepcopy(f.owned["deferred"])})
        f.f.events[-3]["payload"]["session_id"] = "session-fixture"
        f.f.write(); self.assert_fixture(f, False)

    def test_opted_next_requires_six_fields_and_no_unbound_outcome(self) -> None:
        for mutation in [lambda v: v.pop("operational_outcome"),
                         lambda v: v.update(extra=0),
                         lambda v: v.update(operational_outcome={})]:
            with self.subTest(mutation=mutation):
                f = self.fresh()
                mutation(f.f.event("agent_sample_next_requested")["payload"]["input"])
                f.stop(); self.assert_fixture(f, False)

    def test_all_four_original_dispatch_fields_join_each_terminal(self) -> None:
        for field, bad in [("runtime_instance_id", "foreign-runtime"),
                           ("client_session_id", "later-client"),
                           ("controller_lease_id", "renewed-lease"),
                           ("controller_generation", 2)]:
            with self.subTest(field=field):
                f = self.fresh(); f.terminal(f.submission()); f.stop()
                f.f.event("native_result")["payload"]["result"]["attribution"][field] = bad
                f.f.write(); self.assert_fixture(f, False)
        for mutation in [lambda b: b.pop("client_session_id"),
                         lambda b: b.update(extra=1),
                         lambda b: b.update(controller_generation=True)]:
            f = self.fresh(); f.terminal(f.submission()); f.stop()
            mutation(f.f.event("native_submission_requested")["payload"]["dispatch_binding"])
            f.f.write(); self.assert_fixture(f, False)

    def test_operational_outcome_binds_entire_original_result_and_watermark(self) -> None:
        mutations = [lambda o: o.update(schema="future"),
                     lambda o: o.update(extra=True),
                     lambda o: o.update(basis_acquisition_id="foreign"),
                     lambda o: o.update(action_id="other"),
                     lambda o: o.update(consumption_id="other"),
                     lambda o: o.update(state_version=True),
                     lambda o: o["result"].update(request_id="other"),
                     lambda o: o["result"].update(effect="not_observed"),
                     lambda o: o["result"]["attribution"].update(client_session_id="other")]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                f = self.fresh(); f.terminal(f.submission(), defer=True); f.readiness(); f.stop()
                outcome = next(e["payload"]["input"]["operational_outcome"] for e in f.f.events
                               if e["kind"] == "agent_sample_next_requested"
                               and e["payload"]["input"]["operational_outcome"] is not None)
                mutation(outcome); f.f.write(); self.assert_fixture(f, False)

    def test_pending_notification_is_not_cleared_by_readiness_or_fabricated_act(self) -> None:
        f = self.fixture; f.terminal(f.submission(), defer=True); f.readiness(); f.stop()
        next_event = [e for e in f.f.events if e["kind"] == "agent_sample_next_requested"][-1]
        original = copy.deepcopy(next_event["payload"])
        next_event["payload"]["input"]["operational_outcome"] = None
        f.f.write(); self.check(False)
        next_event["payload"] = original
        for kind in ("agent_sample_next_completed", "agent_directive"):
            event = [e for e in f.f.events if e["kind"] == kind][-1]
            event["payload"]["output"]["directive"] = {
                "type": "act", "basis_acquisition_id": f.basis,
                "selection": {"kind": "handle", "action_id": "map_a-action"}, "scores": None}
        f.f.write(); self.check(False)

    def test_deferred_counters_and_recorded_auto_epoch_known_control_are_exact(self) -> None:
        for mutation in [lambda p: p.update(known_stale_rejections=0),
                         lambda p: p.update(consecutive_known_stale_rejections=True),
                         lambda p: p.update(consumption_id="other"),
                         lambda p: p.update(recovery_epoch=1),
                         lambda p: p.update(extra="unrecorded-live-proof")]:
            f = self.fresh(); f.terminal(f.submission(), defer=True); f.stop()
            mutation(f.f.event("native_stale_decision_deferred")["payload"])
            f.f.write(); self.assert_fixture(f, False)
        f = self.fresh(); f.terminal(f.submission(), defer=True); f.stop()
        f.f.events = [e for e in f.f.events if e["kind"] != "mode_changed"]
        f.f.write(); self.assert_fixture(f, False)  # None is not recorded Auto.
        for kind, payload in [("controller_released", {"controller": "released"}),
                              ("fail_closed", {"reason": "query_failed", "agent_state": "known"}),
                              ("runtime_tainted", {"reason": "release_unknown", "retry": False})]:
            f = self.fresh(); f.terminal(f.submission(), defer=True); f.stop()
            index = next(i for i,e in enumerate(f.f.events) if e["kind"] == "native_stale_decision_deferred")
            f.f.events.insert(index, {"schema": "sts2.policy-runtime/agent-session-event-1", "sequence": 0,
                                     "recorded_at": f.f.events[0]["recorded_at"], "kind": kind,
                                     "payload": {"session_id": "session-fixture", "recovery_epoch": 0, **payload}})
            if kind == "runtime_tainted": f.f.run["tainted"] = True
            f.f.write(); self.assert_fixture(f, False)

    def test_inclusive_threshold_never_defers_and_omitted_terminal_is_not_clean(self) -> None:
        f = self.fixture
        f.f.agent["execution_policy"].update(max_known_stale_rejections=1,
                                             max_consecutive_known_stale_rejections=1)
        f.terminal(f.submission(), defer=True); f.stop()
        self.check(False)
        f.f.events = [e for e in f.f.events if e["kind"] != "native_stale_decision_deferred"]
        f.f.write(); self.assertEqual(self.check().value.terminal_summary["known_stale_rejections"], 1)
        f.f.events = [e for e in f.f.events if e["kind"] != "native_result"]
        f.f.write(); self.check(False)

    def test_duplicate_original_terminal_counts_once_and_conflicting_body_fails(self) -> None:
        f = self.fixture; f.terminal(f.submission(), defer=True); f.stop()
        index = next(i for i,e in enumerate(f.f.events) if e["kind"] == "native_result")
        f.f.events.insert(index + 1, copy.deepcopy(f.f.events[index])); f.f.write()
        summary = self.check().value.terminal_summary
        self.assertEqual((summary["terminal_result_count"], summary["known_stale_rejections"]), (1, 1))
        f.f.events[index + 1]["payload"]["result"]["effect"] = "not_observed"
        f.f.write(); self.check(False)

    def test_arbitrary_reason_unknown_partial_never_authorize_following_next(self) -> None:
        for delivery, reason in [("not_started", "controller_required"),
                                 ("unknown", None), ("partially_delivered", None)]:
            f = self.fresh(); f.terminal(f.submission(), delivery=delivery, reason=reason)
            f.stop(); summary = self.assert_fixture(f).value.terminal_summary
            self.assertEqual(summary["known_stale_rejections"], 0)
            f.f.events = f.f.events[:-3]
            f.held = True; f.sample(); f.stop()
            self.assert_fixture(f, False)

    def test_nonnull_observed_context_is_preserved_in_operational_notification(self) -> None:
        f = self.fixture; attempt = f.submission()
        result = f.terminal(attempt, defer=True)
        observed = copy.deepcopy(f.owned["result_with_observed_frame"]["observed_frame"])
        result["observed_frame"] = observed
        f.outcome["result"]["observed_frame"] = copy.deepcopy(observed)
        f.readiness(); f.stop(); self.check()

    def test_legacy_full_reference_summary_is_absent(self) -> None:
        f = old.NativeAgentSessionEvidenceTests(); f.setUp(); self.addCleanup(f.tearDown)
        result = verify_agent_session_run_evidence(f.directory)
        self.assertTrue(result.passed, result.findings)
        self.assertIsNone(result.value.terminal_summary)

    def test_total_ceiling_counts_eighth_refusal_after_delivered_streak_resets(self) -> None:
        f = self.fixture
        for i in range(8):
            if i: f.sample("map_a")
            f.terminal(f.submission(), defer=i < 7)
            if i < 7:
                f.sample("inspect_b")
                f.terminal(f.submission(), delivery="delivered")
        f.stop("known_stale_rejection_limit")
        summary = self.check().value.terminal_summary
        self.assertEqual((summary["original_submission_count"], summary["terminal_result_count"],
                          summary["known_delivered"], summary["known_stale_rejections"],
                          summary["consecutive_known_stale_rejections"]), (15, 15, 7, 8, 1))

    def test_nonstale_not_started_does_not_reset_a_preceding_stale_streak(self) -> None:
        f = self.fixture; f.terminal(f.submission(), defer=True); f.sample()
        f.terminal(f.submission(), reason="controller_required"); f.stop()
        summary = self.check().value.terminal_summary
        self.assertEqual((summary["known_stale_rejections"], summary["consecutive_known_stale_rejections"]), (1, 1))

    def test_late_stop_and_deadline_results_still_count_original_binding(self) -> None:
        for reason in ("stopped", "autonomy_budget_exhausted"):
            with self.subTest(reason=reason):
                f = self.fresh(); attempt = f.submission()
                start = len(f.f.events)
                budget = copy.deepcopy(f.f.budget)
                kind = "handoff_to_human" if reason == "stopped" else "autonomy_budget_exhausted"
                f.f.add(kind, reason=reason, autonomy_budget=budget, controller="released")
                f.terminal(attempt); f.stop(reason)
                for event in f.f.events[start:]: event["payload"]["recovery_epoch"] = 1
                f.f.write()
                summary = self.assert_fixture(f).value.terminal_summary
                self.assertEqual((summary["terminal_result_count"], summary["known_stale_rejections"]), (1, 1))
                self.assertFalse(any(e["kind"] == "native_stale_decision_deferred" for e in f.f.events))

    @staticmethod
    def original(attempt: dict) -> dict:
        return {**{key: copy.deepcopy(attempt[key]) for key in (
            "request_id", "run_id", "runtime_instance_id", "basis_acquisition_id", "snapshot_id",
            "action_id", "dispatch_binding")}, "session_id": "session-fixture", "submission_epoch": 0,
            "status": "pending", "reason": None}

    def reconciled_fixture(self, *, delivery: str = "not_started"):
        f = self.fresh(); attempt = f.submission(); original = self.original(attempt)
        f.f.add("native_request_pending", original=copy.deepcopy(original))
        f.f.add("mode_changed", mode="human", autonomy_budget=f.f.budget, controller="released")
        result = copy.deepcopy(f.owned["result"])
        result.update(request_id=attempt["request_id"], snapshot_id=attempt["snapshot_id"], delivery=delivery)
        if delivery == "delivered":
            result.update(action=copy.deepcopy(f.base.shared["frames"][f.frame_name]["catalog"][0]),
                          execution="native_accepted", effect="pending", cancel="not_requested", reason=None)
        f.f.add("native_request_reconciled", original=copy.deepcopy(original), resolution="resolved", result=result)
        f.stop()
        return f

    def test_terminal_reconciliation_shares_counter_and_duplicate_original_law(self) -> None:
        f = self.reconciled_fixture()
        self.assertEqual(self.assert_fixture(f).value.terminal_summary["known_stale_rejections"], 1)
        index = next(i for i,e in enumerate(f.f.events) if e["kind"] == "native_request_reconciled")
        f.f.events.insert(index + 1, copy.deepcopy(f.f.events[index]))
        # A later identical direct full-result observation also counts once.
        f.f.events.insert(index + 2, {"schema": "sts2.policy-runtime/agent-session-event-1", "sequence": 0,
                                     "recorded_at": f.f.events[0]["recorded_at"], "kind": "native_result",
                                     "payload": {"session_id": "session-fixture", "recovery_epoch": 0,
                                                 "result": copy.deepcopy(f.f.events[index]["payload"]["result"])}})
        f.f.write(); summary = self.assert_fixture(f).value.terminal_summary
        self.assertEqual((summary["terminal_result_count"], summary["known_stale_rejections"]), (1, 1))
        f.f.events[index + 1]["payload"]["result"]["effect"] = "not_observed"
        f.f.write(); self.assert_fixture(f, False)

    def test_reconciled_terminal_all_four_fields_and_pending_binding_are_original(self) -> None:
        for field, bad in [("runtime_instance_id", "new-runtime"), ("client_session_id", "new-client"),
                           ("controller_lease_id", "new-lease"), ("controller_generation", 2)]:
            with self.subTest(field=field):
                f = self.reconciled_fixture()
                f.f.event("native_request_reconciled")["payload"]["result"]["attribution"][field] = bad
                f.f.write(); self.assert_fixture(f, False)
                f = self.reconciled_fixture()
                f.f.event("native_request_pending")["payload"]["original"]["dispatch_binding"][field] = bad
                f.f.write(); self.assert_fixture(f, False)

    def test_human_reconciliation_never_grants_deferred_continuation(self) -> None:
        f = self.reconciled_fixture()
        event = copy.deepcopy(f.owned["deferred"])
        event.update(session_id="session-fixture", request_id="request-1", consumption_id="consume-1")
        f.f.events.insert(-3, {"schema": "sts2.policy-runtime/agent-session-event-1", "sequence": 0,
                              "recorded_at": f.f.events[0]["recorded_at"], "kind": "native_stale_decision_deferred",
                              "payload": event})
        f.f.write(); self.assert_fixture(f, False)

    def test_pending_unresolved_and_pre_submit_closure_are_not_full_terminal_results(self) -> None:
        f = self.fresh(); attempt = f.submission(); original = self.original(attempt)
        f.f.add("native_request_pending", original=copy.deepcopy(original))
        f.f.add("mode_changed", mode="human", autonomy_budget=f.f.budget, controller="released")
        f.f.add("native_request_reconciled", original=copy.deepcopy(original), resolution="pending", result=None)
        f.stop(pending=original)
        summary = self.assert_fixture(f).value.terminal_summary
        self.assertEqual((summary["original_submission_count"], summary["terminal_result_count"],
                          summary["known_stale_rejections"]), (1, 0, 0))
        f = self.fresh(); attempt = f.submission()
        f.f.add("native_submission_not_started", request_id=attempt["request_id"], submission_epoch=0,
                reason="stale_snapshot_or_binding")
        f.stop(); summary = self.assert_fixture(f).value.terminal_summary
        self.assertEqual((summary["terminal_result_count"], summary["known_stale_rejections"]), (0, 0))

    def test_notification_retires_only_after_advanced_ack_and_matching_completion(self) -> None:
        f = self.fresh(); f.terminal(f.submission(), defer=True)
        old_outcome = copy.deepcopy(f.outcome)
        f.sample(); f.readiness(); f.stop()
        self.assert_fixture(f)
        # Re-offering the old original after the complete fresh ACK/Next is not
        # a new refusal and cannot reopen its retired operational notification.
        next_event = [e for e in f.f.events if e["kind"] == "agent_sample_next_requested"][-1]
        next_event["payload"]["input"]["operational_outcome"] = old_outcome
        f.f.write(); self.assert_fixture(f, False)
        f = self.fresh(); f.terminal(f.submission(), defer=True); f.sample(); f.stop()
        # A new recorded ACK with no child completion leaves the offer uncertain.
        f.f.events = [e for e in f.f.events if not (
            e["kind"] in {"agent_sample_next_completed", "agent_directive"}
            and e["payload"]["output"]["state_version"] == 2)]
        f.f.write(); self.assert_fixture(f, False)
        f.f.events[-1]["payload"]["agent_state"] = "uncertain"
        f.f.write(); self.assert_fixture(f)  # No private correction/known-W claim.

    def test_result_without_deferred_marker_cannot_authorize_another_next(self) -> None:
        f = self.fixture; f.terminal(f.submission(), defer=True); f.readiness(); f.stop()
        f.f.events = [e for e in f.f.events if e["kind"] != "native_stale_decision_deferred"]
        for event in f.f.events:
            if event["kind"] == "agent_sample_next_requested":
                event["payload"]["input"]["operational_outcome"] = None
        f.f.write(); self.check(False)

    def test_original_stale_predicate_requires_null_action_empty_stages_and_exact_reason(self) -> None:
        for mutation in [lambda r: r.update(action={
            "action_id": "map_a-action", "kind": "native_input", "verb": "inspect", "label": "inspect",
            "subject_referent_id": None, "arguments": [], "effect_domain": "native"}),
                         lambda r: r.update(stages=[{"stage": "attempted", "delivery": "not_started", "evidence": "fixture"}]),
                         lambda r: r.update(reason="not_a_stale_refusal")]:
            f = self.fresh(); f.terminal(f.submission()); f.stop()
            mutation(f.f.event("native_result")["payload"]["result"])
            f.f.write(); summary = self.assert_fixture(f).value.terminal_summary
            self.assertEqual((summary["known_stale_rejections"], summary["consecutive_known_stale_rejections"]), (0, 0))
            event = copy.deepcopy(f.owned["deferred"])
            event.update(session_id="session-fixture", request_id="request-1", consumption_id="consume-1")
            f.f.events.insert(-3, {"schema": "sts2.policy-runtime/agent-session-event-1", "sequence": 0,
                                  "recorded_at": f.f.events[0]["recorded_at"], "kind": "native_stale_decision_deferred",
                                  "payload": event})
            f.f.write(); self.assert_fixture(f, False)

    def test_original_action_handle_65536_bytes_survives_full_outcome_and_fresh_ack(self) -> None:
        f = self.fixture
        handle = "h" * 65536
        f.initial_action_id(handle)
        attempt = f.submission()
        self.assertEqual(attempt["action_id"], handle)
        f.terminal(attempt, defer=True); f.readiness()
        self.assertEqual(f.outcome["action_id"], handle)
        f.sample(); f.terminal(f.submission(), delivery="delivered"); f.stop()
        summary = self.check().value.terminal_summary
        self.assertEqual((summary["known_stale_rejections"], summary["known_delivered"]), (1, 1))
        event = next(e for e in f.f.events if e["kind"] == "agent_sample_next_requested"
                     and e["payload"]["input"]["operational_outcome"] is not None)
        event["payload"]["input"]["operational_outcome"]["action_id"] = "h" * 65537
        f.f.write(); self.check(False)

    def test_recorded_readiness_unit_revision_and_metadata_falsifiers(self) -> None:
        mutations = [lambda w: w.update(revision=2), lambda w: w.update(revision=0),
                     lambda w: w["owner_occurrence"].update(owner_id="other-owner"),
                     lambda w: w.update(catalog_digest="0" * 64),
                     lambda w: w.update(included=["catalog"], missing=["persistent", "interaction", "referents"])]
        for mutation in mutations:
            f = self.fresh(); f.terminal(f.submission(), defer=True); f.readiness(); f.stop()
            witness = [e["payload"]["witness"] for e in f.f.events if e["kind"] == "native_acquisition_registered"][-1]
            mutation(witness); f.f.write(); self.assert_fixture(f, False)
        f = self.fresh(); f.terminal(f.submission(), defer=True); f.readiness(); f.stop()
        witness = [e["payload"]["witness"] for e in f.f.events if e["kind"] == "native_acquisition_registered"][-1]
        witness["capture"].update(capture_id="new-mechanical-capture", scope_id="new-mechanical-scope")
        f.f.write(); self.assert_fixture(f)  # Capture/scope do not retire the notification.

    def test_opted_pending_preserves_long_original_action_but_legacy_limit_stays(self) -> None:
        f = self.fixture; handle = "h" * 65536
        f.initial_action_id(handle)
        attempt = f.submission(); original = self.original(attempt)
        f.f.add("native_request_pending", original=copy.deepcopy(original))
        f.f.add("mode_changed", mode="human", autonomy_budget=f.f.budget, controller="released")
        result = copy.deepcopy(f.owned["result"])
        result.update(request_id=attempt["request_id"], snapshot_id=attempt["snapshot_id"])
        f.f.add("native_request_reconciled", original=copy.deepcopy(original), resolution="resolved", result=result)
        f.stop(); self.assertEqual(self.check().value.terminal_summary["known_stale_rejections"], 1)
        f.f.event("native_request_pending")["payload"]["original"]["action_id"] = "h" * 65537
        f.f.write(); self.check(False)
        # An unchanged historical carrier still rejects >256; opt-in is additive.
        from sts2_platform_evidence.agent_run_evidence import AgentRunEvidenceError
        from sts2_platform_evidence.agent_session_run_evidence import _pending
        with self.assertRaises(AgentRunEvidenceError):
            _pending({key: value for key, value in original.items() if key != "dispatch_binding"},
                     f.f.run["run_id"], "session-fixture", {attempt["request_id"]: {
                         **attempt, "session_id": "session-fixture", "recovery_epoch": 0}})

    def test_dispatch_three_ids_match_sdk_utf16_boundaries_in_complete_trace(self) -> None:
        for text in ("c" * 128, "界" * 128, "🐉" * 64):
            with self.subTest(text=text[:4]):
                f = self.fresh(); f.binding.update(client_session_id=text, controller_lease_id=text)
                f.terminal(f.submission(), defer=True); f.readiness()
                f.sample(); f.terminal(f.submission(), delivery="delivered"); f.stop()
                self.assert_fixture(f)
                attempt = f.f.event("native_submission_requested")["payload"]
                attempt["dispatch_binding"]["client_session_id"] = text + "x"
                f.f.write(); self.assert_fixture(f, False)
        f = self.fresh(); runtime = "r" * 128
        f.binding["runtime_instance_id"] = runtime
        frame = f.base.shared["frames"]["map_a"]
        frame["observation"]["session"]["runtime_instance_id"] = runtime
        frame["capture"]["session"]["runtime_instance_id"] = runtime
        f.f.events[0]["payload"]["environment"]["runtime_instance_id"] = runtime
        f.initial_action_id("map_a-action")
        f.terminal(f.submission(), defer=True); f.readiness(); f.sample()
        f.terminal(f.submission(), delivery="delivered"); f.stop(); self.assert_fixture(f)


if __name__ == "__main__":
    unittest.main()
