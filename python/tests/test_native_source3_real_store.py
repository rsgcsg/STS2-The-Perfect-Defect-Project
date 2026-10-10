"""Actual producer/consumer framing chain with explicit SYN Host/control/results.

The production Python parent writes the real Node v3 originals; no handwritten
final/report family, Evidence source fallback, game, data admission or training.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import socket
import sys
import unittest
from dataclasses import replace
from pathlib import Path

import test_native_source3_collection as collection_fixtures
from metadata_import_guard import no_torch_imports as no_torch_imports

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.workbench import native_source3_collection as module


class RealStoreCollectionTests(unittest.TestCase):
    setUp = collection_fixtures.CollectionTests.setUp
    tearDown = collection_fixtures.CollectionTests.tearDown
    mock = collection_fixtures.CollectionTests.mock
    application = collection_fixtures.CollectionTests.application

    @staticmethod
    def failure_diagnostic(result, proof):
        """Bound actual failures/phases; never dump the full generated report."""
        errors = result.get("runtime_status", {}).get("errors", [])
        phases = proof.get("diagnostics", {}).get("events", [])
        return json.dumps({
            "status": str(result.get("status"))[:64],
            "error_code": str(result.get("error_code"))[:256],
            "runtime_errors": [str(error)[:512] for error in errors[:3]],
            "producer_phases": [
                {"phase": str(event.get("phase"))[:64],
                 "operation": str(event.get("operation"))[:64]}
                for event in phases[-6:]
            ],
        }, ensure_ascii=True)[:2400]

    def run_real_chain(self, scenario: str):
        import sts2_platform_evidence as evidence

        self.assertIn("site-packages", str(Path(evidence.__file__).resolve()))
        self.assertEqual(
            importlib.metadata.version("rsgcsg-sts2-platform-evidence"), "0.1.0rc27"
        )
        self.assertTrue(module.installed_terminal_summary_available())
        fixture = module.ROOT.parent / "tools/test/native-source3-real-store-fixture.mjs"
        self.assertTrue(fixture.is_file())
        # Same real endpoint throughout Host mock, Python Ready admission and SDK;
        # no base-URL redirection or rewriting the received Node control DTOs.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        self.config = replace(self.config, platform_url=f"http://127.0.0.1:{port}")
        self.request = replace(
            self.request, seed=scenario, record_source3=False, target_choices=2,
            max_submissions=4, deadline_ms=30_000,
            execution_policy=module.OWNED_EXECUTION_POLICY,
        )
        policy = self.request.policy()
        actual_descriptor = module.descriptor(execution_policy=policy)
        frozen_descriptor = json_bytes(actual_descriptor)
        frozen_policy = json_bytes(policy)
        descriptor_sha = hashlib.sha256(frozen_descriptor).hexdigest()
        production_relation = module.closed_collection_producer(actual_descriptor, policy)
        if list(sys.version_info[:3]) != [3, 11, 15]:
            self.assertIsNone(production_relation)

        # Explicit test-only exact SYN promotion seam, not a new production
        # producer admission or a forged Python/lock/code-file provenance tuple.
        relation = {
            "schema": "spireagent/test-synthetic-contract-relation-1",
            "id": "SYN_contract_only_" + descriptor_sha,
            "production_admitted": False,
        }

        def exact_synthetic_relation(candidate, selected_policy):
            if (
                json_bytes(candidate) == frozen_descriptor
                and json_bytes(selected_policy) == frozen_policy
            ):
                return copy.deepcopy(relation)
            return None

        altered = copy.deepcopy(actual_descriptor)
        altered["runtime_provenance"]["python_version"] = [0, 0, 0]
        self.assertIsNone(exact_synthetic_relation(altered, policy))
        self.assertIsNone(exact_synthetic_relation(actual_descriptor, None))
        self.mock(module, "closed_collection_producer", exact_synthetic_relation)

        def preflight(config, request):
            request.validate()
            return {
                "child_path": str(fixture),
                "child_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                "source": {"scope": "SYN_portable_contract_only"},
                "endpoint": config.platform_url,
                "options": request.options(config.platform_url),
                "record_source3": False,
                "execution_policy": request.policy(),
                "fresh_episode_boundary": None,
                "promotion_gate": {
                    "installed_terminal_summary_available": True,
                    "closed_producer_relation": copy.deepcopy(relation),
                },
            }

        def actual_child(child_path, cancel):
            self.assertEqual(child_path, fixture)
            child = module.OwnedPipeChild(child_path, cancel)
            self.children.append(child)
            return child

        result = module.collect_source3(
            self.config, self.root / "config.json", self.request,
            app_factory=self.application, child_factory=actual_child,
            preflight=preflight, cancel=self.cancel,
        )
        self.assertEqual(self.commands, [])
        self.assertEqual(len(self.apps), 1)
        self.assertEqual(len(self.children), 1)
        self.assertEqual(self.children[0].process.poll(), 0)
        report_path = self.request.output / "report.json"
        self.assertEqual(json.loads(report_path.read_bytes()), result)
        proof = json.loads((self.request.output / "real-chain-producer-proof.json").read_bytes())
        diagnostic = self.failure_diagnostic(result, proof)
        self.assertEqual(proof["endpoint"], self.config.platform_url, diagnostic)
        self.assertEqual(proof["stats"], {
            "charged_bytes": 0, "charged_buffers": 0, "handles": 0,
            "live_captures": 0, "now": 0,
        }, diagnostic)
        self.assertEqual(proof["producer_exit"], {"code": 0, "signal": None}, diagnostic)
        self.assertGreater(proof["diagnostics"]["backend_pid"], 0, diagnostic)
        self.assertEqual(proof["diagnostics"]["pending_operations"], [], diagnostic)
        self.assertEqual(result["child"]["exit_code"], 0, diagnostic)
        self.assertTrue(result["child"]["reader_terminal"], diagnostic)
        self.assertTrue(result["child"]["diagnostics_terminal"], diagnostic)
        self.assertEqual(result["child_final"]["cleanup_errors"], [], diagnostic)
        self.assertEqual(result["admission"], "not_run")
        self.assertIsNone(result["eligible_unique_N"])
        self.assertIsNone(result["source_start"])
        self.assertIsNone(result["source_close"])
        self.assertIsNone(result["source_closed"])
        self.assertFalse(result["close_sent"])
        self.assertEqual(result["options"]["teacher_descriptor"], actual_descriptor)
        for catalog in proof["catalogs"]:
            self.assertEqual(catalog["total_count"], 2)
            self.assertEqual(len(catalog["actions"]), 2)
        for original in proof["submits"]:
            self.assertEqual(len(original["original_catalog"]), 2)
            self.assertIn(original["bound_action_id"], {
                action["action_id"] for action in original["original_catalog"]
            })
        directory = Path(result["direct_evidence"]["directory"])
        checked = evidence.verify_agent_session_run_evidence(directory)
        self.assertTrue(checked.passed, f"{diagnostic}; findings={str(checked.findings)[:1024]}")
        summary = checked.require_value().terminal_summary
        self.assertEqual(result["terminal_summary"], summary, diagnostic)
        self.assertEqual(result["child_final"]["terminal_summary"], summary, diagnostic)
        self.assertEqual(result["child_final_full"]["terminal_summary"], summary, diagnostic)
        self.assertEqual(result["quiesced"]["counts"], result["child_final"]["counts"], diagnostic)
        self.assertEqual(result["budget_submissions_used"], result["submissions"], diagnostic)
        events = [
            json.loads(line) for line in (directory / "events.jsonl").read_bytes().splitlines()
        ]
        self.assertEqual(len({x["request_id"] for x in proof["submits"]}), len(proof["submits"]))
        self.assertEqual(
            len([e for e in events if e["kind"] == "native_result"]), len(proof["submits"])
        )
        self.assertFalse(any(
            request["operation"] == "result" for request in proof["producer_methods"]
        ))
        return result, proof

    def fresh_request(self, result):
        marker = self.config.state_dir / module.MARKER_FILE
        report = self.request.output / "report.json"
        request = replace(
            self.request, output=self.root / "UNSTARTED_second_episode",
            predecessor_report_path=report,
            predecessor_report_sha256=hashlib.sha256(report.read_bytes()).hexdigest(),
            predecessor_marker_sha256=hashlib.sha256(marker.read_bytes()).hexdigest(),
        )
        self.assertEqual(json.loads(marker.read_bytes())["status"], result["status"])
        return marker, report, request

    def test_real_parent_closes_delivered_and_stale_complete_originals(self):
        result, proof = self.run_real_chain("SYN_REAL_CHAIN_COMPLETE")
        diagnostic = self.failure_diagnostic(result, proof)
        self.assertEqual(result["status"], "completed", diagnostic)
        self.assertEqual(result["terminal_summary"]["terminal_result_count"], 3, diagnostic)
        self.assertEqual(result["terminal_summary"]["known_delivered"], 2, diagnostic)
        self.assertEqual(result["terminal_summary"]["known_stale_rejections"], 1, diagnostic)
        self.assertEqual(len(proof["submits"]), 3, diagnostic)

    def test_real_current_gap_unknown_original_is_readonly_fresh_verifiable(self):
        result, proof = self.run_real_chain("SYN_REAL_CHAIN_CURRENT_GAP")
        diagnostic = self.failure_diagnostic(result, proof)
        self.assertEqual(result["status"], "unknown", diagnostic)
        self.assertEqual(result["terminal_summary"]["terminal_result_count"], 2, diagnostic)
        self.assertEqual(result["terminal_summary"]["known_delivered"], 1, diagnostic)
        self.assertEqual(result["terminal_summary"]["known_stale_rejections"], 1, diagnostic)
        self.assertEqual(len(proof["submits"]), 2, diagnostic)
        self.assertEqual(proof["currents"][-1]["status"], "source_capture_incomplete", diagnostic)
        failure_reply = json.loads(
            (self.request.output / "native-current-failure-reply.json").read_bytes()
        )
        self.assertEqual(failure_reply["original_sdk_reply"]["raw"], proof["currents"][-1], diagnostic)
        marker, report, request = self.fresh_request(result)
        before = {p: p.read_bytes() for p in (marker, report)}
        boundary = module.verified_fresh_predecessor(marker, request)
        self.assertFalse(boundary["can_resume_prior"], diagnostic)
        self.assertFalse(boundary["can_retry_prior"], diagnostic)
        self.assertFalse(boundary["prior_record_source3"], diagnostic)
        self.assertEqual({p: p.read_bytes() for p in before}, before, diagnostic)
        self.assertFalse(request.output.exists(), diagnostic)
        self.assertEqual(len(self.children), 1, diagnostic)

    def test_real_native_unknown_cannot_admit_fresh_episode(self):
        result, proof = self.run_real_chain("SYN_REAL_CHAIN_NATIVE_UNKNOWN")
        diagnostic = self.failure_diagnostic(result, proof)
        self.assertEqual(result["status"], "unknown", diagnostic)
        self.assertTrue(result["runtime_status"]["tainted"], diagnostic)
        self.assertEqual(len(proof["submits"]), 2, diagnostic)
        marker, report, request = self.fresh_request(result)
        before = {p: p.read_bytes() for p in (marker, report)}
        with self.assertRaisesRegex(BoundaryError, "fresh_predecessor_native_outcome_unresolved", msg=diagnostic):
            module.verified_fresh_predecessor(marker, request)
        self.assertEqual({p: p.read_bytes() for p in before}, before, diagnostic)
        self.assertFalse(request.output.exists(), diagnostic)
        self.assertEqual(len(self.children), 1, diagnostic)
