"""The production GLOBAL reader's expected-budget binding, without executing Runtime.

Semantic replay and code trust remain owned by the normal verifier; the existing
real producer archive is independently checked during the review handoff.
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from sts2_platform_evidence.agent_run_evidence import AgentRunEvidenceError
from sts2_platform_evidence.global_agent_session_run_evidence import _verify_global_budget_facts


class GlobalBudgetBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vectors = json.loads((Path(__file__).resolve().parents[2] / "policy-runtime/test/fixtures/autonomy-v2.json").read_bytes())

    def events(self, *budgets: dict[str, object]) -> bytes:
        return b"".join(json.dumps({"payload": {"autonomy_budget": budget}}).encode() + b"\n" for budget in budgets)

    def test_v2_expected_mode_and_each_cap_match_every_recorded_budget(self) -> None:
        for mode in ("bounded", "continuous"):
            with self.subTest(mode=mode):
                raw = self.events(self.vectors[f"{mode}_status_v2"])
                config = self.vectors[f"{mode}_startup_v2"]
                expected = {"schema": "sts2.policy-runtime/agent-session-startup-2", "autonomy_budget": config}
                _verify_global_budget_facts(raw, expected, 2)
                for invalid in (None, self.vectors["continuous_startup_v2" if mode == "bounded" else "bounded_startup_v2"]):
                    with self.assertRaises(AgentRunEvidenceError):
                        _verify_global_budget_facts(raw, {**expected, "autonomy_budget": invalid}, 2)
                with self.assertRaises(AgentRunEvidenceError):
                    _verify_global_budget_facts(raw, {**expected, "schema": "sts2.policy-runtime/agent-session-startup-1"}, 2)
                if mode == "bounded":
                    for key in ("maxSubmissions", "maxPolicyCalls", "deadlineMs"):
                        with self.assertRaises(AgentRunEvidenceError):
                            _verify_global_budget_facts(raw, {**expected, "autonomy_budget": {**config, key: config[key] + 1}}, 2)

    def test_v2_journal_cannot_change_mode_or_bounded_caps_without_expected_config(self) -> None:
        original = self.vectors["bounded_status_v2"]
        variants = [self.vectors["continuous_status_v2"]]
        for key in ("max_submissions", "max_policy_calls", "deadline_ms"):
            changed = copy.deepcopy(original)
            changed[key] += 1
            if key == "deadline_ms":
                changed["remaining_ms"] += 1
            variants.append(changed)
        for changed in variants:
            with self.subTest(changed=changed):
                with self.assertRaises(AgentRunEvidenceError):
                    _verify_global_budget_facts(self.events(original, changed), {}, 2)

    def test_v1_absent_budget_facts_remain_legacy_but_recorded_facts_cannot_contradict_expected(self) -> None:
        config = {key: value for key, value in self.vectors["bounded_startup_v2"].items() if key != "mode"}
        expected = {"schema": "sts2.policy-runtime/agent-session-startup-1", "autonomy_budget": config}
        _verify_global_budget_facts(b'{"payload":{}}\n', expected, 1)
        raw = self.events(self.vectors["bounded_status_v1"])
        _verify_global_budget_facts(raw, expected, 1)
        with self.assertRaises(AgentRunEvidenceError):
            _verify_global_budget_facts(raw, {**expected, "autonomy_budget": {**config, "maxSubmissions": 1}}, 1)
