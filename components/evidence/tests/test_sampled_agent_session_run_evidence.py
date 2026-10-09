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
        sample_event["payload"]["proposal"] = {"request_id": "child-consume-" + str(version), "report": report}
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
        f.add("agent_directive", output={"continuity_token": "segment-1", "consumption_id": report["consumption_id"],
                                        "state_version": version, "directive": directive})
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
        self.f.events = self.f.events[:3] + [self.f.events[3]]
        self.f.events[3]["payload"].update(disposition="query_offered", proposal=None)
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

    def test_unbound_extra_sample_file_fails_even_with_valid_checksums(self) -> None:
        (self.f.directory / ("agent-sample-" + "a"*64 + ".json")).write_bytes(b'{}')
        self.f.rehash(); self.assertEqual(self.verify(), "fail")


if __name__ == "__main__":
    unittest.main()
