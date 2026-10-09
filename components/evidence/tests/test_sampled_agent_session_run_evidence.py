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


if __name__ == "__main__":
    unittest.main()
