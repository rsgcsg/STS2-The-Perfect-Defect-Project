"""Pure program Agent law and honest code-artifact identity; no NN constructor/import."""

from __future__ import annotations

import ast
import copy
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from metadata_import_guard import no_torch_imports as no_torch_imports

from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.fullrun.native_structured_inputs import native_catalog_digest
from stpd.native_graph_spec import NativeGraphControl
from stpd.native_sampled_carry_spec import sampled_agent_execution_policy, sampled_agent_spec
from stpd.policy import native_teacher_agent as module
from stpd.policy.native_operational_outcome import (
    checked_execution_policy,
    checked_next_input,
    emitted_intention,
    manifest_execution_policy,
    owned_current_known_stale_policy,
)
from stpd.policy.native_public_teacher import NativePublicTeacher

REPO = Path(__file__).resolve().parents[2]
NORM = json.loads(
    (REPO / "components/policy-runtime/contracts/fixtures/owned-current-known-stale-v1.json")
    .read_text()
)
WIRE = json.loads(
    (REPO / "components/connector/contracts/fixtures/native-logical-v1.json").read_text()
)["wire_samples"]


def action(identity, verb, subject=None):
    return {
        "action_id": identity,
        "kind": "native_input",
        "verb": verb,
        "label": identity,
        "subject_referent_id": subject,
        "arguments": [],
        "effect_domain": "native",
    }


def current(number, kind="native_map", actions=None, surface=None, refs=()):
    catalog = [action("open-deck", "open_run_deck")] if actions is None else actions
    observation = copy.deepcopy(WIRE["observation"])
    observation.update(snapshot_id=f"snapshot-{number}", revision=number, status="interactive")
    observation["interaction"].update(kind=kind, content_schema="public-fixture")
    observation["interaction"]["content"]["surface"] = surface or {"kind": kind}
    observation["catalog"].update(
        snapshot_id=observation["snapshot_id"],
        total_count=len(catalog),
        digest=native_catalog_digest(catalog),
        catalog_ref=f"catalog-{number}",
    )
    observation["referents"] = [
        {
            "referent_id": identity,
            "role": role,
            "kind": "entity",
            "label": identity,
            "state": {
                "visible": True,
                "enabled": True,
                "selected": False,
                "focused": False,
                "observation_basis": "synthetic_public_fixture",
            },
            "properties_schema": None,
            "properties": None,
        }
        for identity, role in refs
    ]
    capture = copy.deepcopy(WIRE["capture"])
    capture.update(
        snapshot_id=observation["snapshot_id"], scope_id=observation["catalog"]["scope_id"]
    )
    return {
        "method": "current",
        "acquisition_id": f"acquisition-{number}",
        "value": {
            "capture": capture,
            "observation": observation,
            "catalog": catalog,
            "catalog_materialized": True,
        },
    }


def next_input(agent):
    value = {
        "continuity_token": "continuity",
        "consumption_id": agent.consumption_id,
        "state_version": agent.state_version,
        "basis_acquisition_id": agent.acquisition_id,
        "received_cursor": "cursor-known",
    }
    if agent.execution_policy is not None:
        value["operational_outcome"] = None
    return value


def ack(report):
    return {
        key: report[key]
        for key in ("consumption_id", "acquisition_id", "state_version", "advanced")
    } | {
        "prefix": {
            "continuity_token": report["continuity_token"],
            "history_mode": "sampled_current",
            "consumption_mode": "once_per_occurrence",
            "received_cursor": "cursor-known",
            "consumed_publication_index": None,
            "omissions": {"received_unconsumed_count": 0, "missing_scopes": [], "gap": None},
        }
    }


class TeacherAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        artifact = module.write_artifact(self.root / "code.json")
        self.manifest = {
            "schema": "sts2.policy-runtime/agent-manifest-1",
            "artifact": artifact,
            "adapter": module.descriptor()["adapter"],
            "input": {
                "input_spec": module.INPUT_SPEC,
                "history_mode": "sampled_current",
                "state_recovery": {"mode": "none", "max_state_bytes": 0, "model_bindings": []},
            },
            "limits": {"max_message_bytes": module.MAX_MESSAGE_BYTES},
        }
        self.path = self.root / "manifest.json"
        self.path.write_bytes(json_bytes(self.manifest))
        self.agent = module.NativeTeacherAgent(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def consume(self, value):
        before = next_input(self.agent)
        self.agent.begin_next(before)
        kind, report = self.agent.propose_current(value, before)
        self.assertEqual(kind, "consumed")
        self.agent.acknowledge(ack(report))
        return self.agent.directive(before)

    def test_mutable_script_state_staged_until_exact_ACK_and_duplicate_never_advances(self):
        before = next_input(self.agent)
        self.agent.begin_next(before)
        kind, report = self.agent.propose_current(current(1), before)
        self.assertEqual(kind, "consumed")
        self.assertEqual(self.agent.teacher.phase, "map")
        self.assertEqual(self.agent.state_version, 0)
        bad = ack(report)
        bad["state_version"] = True
        with self.assertRaises(BoundaryError):
            self.agent.acknowledge(bad)
        self.assertEqual(self.agent.teacher.phase, "map")
        self.agent.acknowledge(ack(report))
        self.assertEqual(self.agent.teacher.phase, "deck")
        self.assertEqual(
            self.agent.directive(before)["directive"]["selection"]["action_id"], "open-deck"
        )
        prior = next_input(self.agent)
        self.agent.begin_next(prior)
        kind, output = self.agent.propose_current(current(1), prior)
        self.assertEqual(kind, "directive")
        self.assertEqual(output["directive"]["type"], "await")
        self.assertEqual(self.agent.state_version, 1)
        self.assertEqual(self.agent.teacher.decisions, 1)

    def test_return_closing_and_pending_focus_are_explicit_Await_not_repeat_Act(self):
        self.consume(current(1))
        self.consume(
            current(
                2,
                "run_deck",
                [action("inspect", "inspect_deck_card", "card")],
                refs=[("card", "card")],
            )
        )
        self.consume(current(3, "inspect_card", [action("preview", "toggle_card_upgrade_preview")]))
        result = self.consume(current(4, "inspect_card", [action("return", "return_card_inspect")]))
        self.assertEqual(result["directive"]["type"], "act")
        choices = self.agent.teacher.decisions
        closing = self.consume(
            current(5, "inspect_card", [action("return", "return_card_inspect")])
        )
        self.assertEqual(closing["directive"]["type"], "await")
        self.assertEqual(self.agent.teacher.decisions, choices)
        arrived = self.consume(
            current(6, "run_deck", [action("back", "return_native_information")])
        )
        self.assertEqual(arrived["directive"]["selection"]["action_id"], "back")
        teacher = NativePublicTeacher(browse=False, focus_target_id="target")
        view = current(
            7,
            "combat_card_operation",
            [action("focus", "focus_target", "target")],
            refs=[("target", "creature")],
        )
        view["value"]["observation"]["interaction"]["stage"] = "card_targeting"
        view["value"]["observation"]["interaction"].update(
            content_schema="sts2.player-environment/surface/combat_card_operation_text_menu-1",
        )
        view["value"]["observation"]["interaction"]["content"]["surface"].update(
            kind="combat_card_operation", stage="card_targeting", focused_target_referent_id=None,
        )
        decision = teacher.decide(view["value"]["observation"], view["value"]["catalog"])
        self.assertEqual(decision.directive, "await")
        self.assertIsNone(decision.action_id)

    def test_empty_current_is_readiness_only_and_unsupported_is_honest_Close(self):
        original = next_input(self.agent)
        kind, output = self.agent.propose_current(current(1, actions=[]), original)
        self.assertEqual((kind, output["directive"]["type"]), ("directive", "await"))
        self.assertEqual(self.agent.state_version, 0)
        unsupported = self.consume(current(2, "event_option", [action("option", "activate")]))
        self.assertEqual(unsupported["directive"]["type"], "close")
        self.assertEqual(unsupported["directive"]["reason"], "scripted_owner_arrival_not_observed")

    def test_map_travel_sample_commits_ACK_then_Awaits_without_information_action_label(self):
        topbar_roles = [
            "topbar_deck", "topbar_floor", "topbar_boss", "topbar_gold", "topbar_hp",
            "topbar_settings", "topbar_potion_slot", "topbar_potion_slot", "topbar_potion_slot",
        ]
        actions = [
            action("deck", "open_run_deck"),
            action("relic", "inspect_relic", "public-relic"),
            action("relic-tips", "show_relic_tips", "public-relic"),
            *[action(f"info-{i}", "show_topbar_tips", f"public-topbar-{i}") for i in range(9)],
        ]
        value = current(
            1,
            actions=actions,
            surface={
                "kind": "map_navigation",
                "traveling": True,
                "travel_enabled": False,
                "next_options": [],
            },
            refs=[("public-relic", "relic")]
            + [(f"public-topbar-{i}", role) for i, role in enumerate(topbar_roles)],
        )
        for referent in value["value"]["observation"]["referents"][1:]:
            referent["kind"] = "control"
        value["value"]["observation"]["interaction"].update(
            stage="native_information_page",
            content_schema="sts2.player-environment/surface/map_navigation-1",
        )
        before = next_input(self.agent)
        kind, report = self.agent.propose_current(value, before)
        self.assertEqual(kind, "consumed")
        self.assertEqual(self.agent.state_version, 0)
        with self.assertRaisesRegex(BoundaryError, "known_ACK_before_directive"):
            self.agent.directive(before)
        self.agent.acknowledge(ack(report))
        waiting = self.agent.directive(before)
        self.assertEqual(waiting["directive"], {
            "type": "await", "after_cursor": "cursor-known",
            "condition": "any_event", "timeout_ms": 250,
        })
        self.assertEqual(waiting["consumption_id"], report["consumption_id"])
        self.assertEqual(waiting["state_version"], 1)
        self.assertEqual(self.agent.teacher.decisions, 0)
        self.assertEqual(self.agent.state_version, 1)
        kind, repeated = self.agent.propose_current(value, next_input(self.agent))
        self.assertEqual((kind, repeated["directive"]["type"]), ("directive", "await"))
        self.assertEqual(self.agent.state_version, 1)
        arrived = self.consume(current(2))
        self.assertEqual(arrived["directive"]["type"], "act")
        self.assertEqual(self.agent.teacher.decisions, 1)

    def test_executed_qualification_helper_mutation_invalidates_real_artifact(self):
        closure = self.root / "closure"
        for relative in module.CODE_FILES:
            destination = closure / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(module.ROOT / relative, destination)
        shutil.copyfile(module.ROOT / "uv.lock", closure / "uv.lock")
        with patch.object(module, "ROOT", closure):
            # The code closure remains complete and agrees before a mutation.
            module.NativeTeacherAgent(self.path)
            helper = closure / "stpd/fullrun/native_structured_inputs.py"
            helper.write_text(
                helper.read_text().replace(
                    '"includes_hidden_information"', '"changed_privacy_field"'
                )
            )
            with self.assertRaisesRegex(BoundaryError, "real_code_artifact_manifest_binding"):
                module.NativeTeacherAgent(self.path)



class OwnedStaleTeacherTests(TeacherAgentTests):
    def setUp(self):
        super().setUp()
        self.policy = owned_current_known_stale_policy()
        self.manifest["artifact"] = module.write_artifact(
            self.root / "owned-code.json", execution_policy=self.policy
        )
        self.manifest["adapter"] = module.descriptor(execution_policy=self.policy)["adapter"]
        self.manifest["execution_policy"] = copy.deepcopy(self.policy)
        self.manifest["input"].update(
            consumption_mode="once_per_occurrence",
            attachment={"eager_scope": [], "delivery_mode": "scoped"},
        )
        self.manifest["requirements"] = {"required_methods": [
            "current", "current_owned", "retain", "release",
        ]}
        self.path.write_bytes(json_bytes(self.manifest))
        self.agent = module.NativeTeacherAgent(self.path)

    def notice(self, *, observed=False):
        value = next_input(self.agent)
        outcome = copy.deepcopy(NORM["opted_next_after_stale"]["operational_outcome"])
        intention = self.agent.last_intention
        for key in ("basis_acquisition_id", "action_id", "consumption_id", "state_version"):
            outcome[key] = intention[key]
        outcome["result"] = copy.deepcopy(NORM[
            "result_with_observed_frame" if observed else "result"
        ])
        outcome["result"]["snapshot_id"] = intention["snapshot_id"]
        value["operational_outcome"] = outcome
        return value

    def test_declared_descriptor_and_parser_modes_do_not_upgrade_legacy(self):
        legacy = module.descriptor()
        proposed = module.descriptor(execution_policy=self.policy)
        self.assertEqual(legacy["agent_spec"], module.AGENT_SPEC)
        self.assertEqual(legacy["adapter"]["version"], "1.1.0")
        self.assertNotIn("execution_policy", legacy["agent_spec"])
        self.assertEqual(proposed["agent_spec"]["teacher"]["version"], "1.0.6")
        self.assertEqual(proposed["adapter"]["version"], "1.2.0")
        self.assertEqual(proposed["input_spec_body"], legacy["input_spec_body"])
        missing = next_input(self.agent)
        del missing["operational_outcome"]
        with self.assertRaises(BoundaryError):
            self.agent.begin_next(missing)
        self.manifest["adapter"] = legacy["adapter"]
        self.path.write_bytes(json_bytes(self.manifest))
        with self.assertRaisesRegex(BoundaryError, "artifact_manifest_binding"):
            module.NativeTeacherAgent(self.path)

    def test_unchanged_readiness_bad_ACK_and_repeated_notice_preserve_history(self):
        self.consume(current(1))
        before_teacher = self.agent.teacher.state()
        before_unit = self.agent.unit
        notice = self.notice(observed=True)
        self.agent.begin_next(notice)
        self.agent.begin_next(copy.deepcopy(notice))
        self.assertEqual(self.agent.teacher.state(), before_teacher)
        self.assertEqual(self.agent.state_version, 1)
        same = current(1)
        same["acquisition_id"] = "mechanically-new-acquisition"
        same["value"]["capture"]["capture_id"] = "mechanically-new-capture"
        kind, output = self.agent.propose_current(same, notice)
        self.assertEqual((kind, output["directive"]["type"]), ("directive", "await"))
        self.assertIs(self.agent.unit, before_unit)
        self.assertEqual(self.agent.teacher.state(), before_teacher)
        self.assertIsNotNone(self.agent.pending_outcome)
        fresh = current(2, actions=[action("new-open-deck", "open_run_deck")])
        kind, report = self.agent.propose_current(fresh, notice)
        self.assertEqual(kind, "consumed")
        self.assertEqual(self.agent.teacher.state(), before_teacher)
        bad = ack(report)
        bad["acquisition_id"] = "wrong"
        with self.assertRaises(BoundaryError):
            self.agent.acknowledge(bad)
        self.assertEqual(self.agent.teacher.state(), before_teacher)
        self.assertIsNotNone(self.agent.pending_outcome)
        self.agent.acknowledge(ack(report))
        self.assertIsNone(self.agent.pending_outcome)
        self.assertEqual(self.agent.state_version, 2)
        self.assertEqual(self.agent.teacher.decisions, 2)
        self.assertEqual(self.agent.teacher.browse_choices, 2)
        self.assertEqual(self.agent.teacher.counts["open_run_deck"], 2)
        output = self.agent.directive(notice)
        self.assertEqual(output["directive"]["selection"]["action_id"], "new-open-deck")

    def test_each_browse_phase_corrects_only_refused_marker_on_new_input(self):
        cases = (
            ("map", "native_map", "open_run_deck", "deck", None),
            ("deck", "run_deck", "inspect_deck_card", "inspect", "card"),
            ("inspect", "inspect_card", "toggle_card_upgrade_preview", "inspect_return", None),
            ("inspect_return", "inspect_card", "return_card_inspect", "deck_return", None),
            ("deck_return", "run_deck", "return_native_information", "map_return", None),
        )
        for phase, kind, verb, after, subject in cases:
            with self.subTest(verb=verb):
                self.agent = module.NativeTeacherAgent(self.path)
                self.agent.teacher.phase = phase
                refs = [("card", "card")] if subject else []
                self.consume(current(1, kind, [action("old", verb, subject)], refs=refs))
                self.assertEqual(self.agent.teacher.phase, after)
                notice = self.notice()
                self.agent.begin_next(notice)
                fresh = current(2, kind, [action("fresh", verb, subject)], refs=refs)
                _, report = self.agent.propose_current(fresh, notice)
                self.assertEqual(self.agent.teacher.phase, after)
                self.agent.acknowledge(ack(report))
                output = self.agent.directive(notice)
                self.assertEqual(output["directive"]["selection"]["action_id"], "fresh")
                self.assertEqual(self.agent.teacher.decisions, 2)
                self.assertEqual(self.agent.teacher.browse_choices, 2)

    def test_marker_mismatch_rejects_instead_of_restoring_teacher(self):
        self.consume(current(1))
        notice = self.notice()
        self.agent.begin_next(notice)
        self.agent.teacher.phase = "progress"
        unchanged = self.agent.teacher.state()
        with self.assertRaisesRegex(BoundaryError, "intention_marker_mismatch"):
            self.agent.propose_current(current(2), notice)
        self.assertEqual(self.agent.teacher.state(), unchanged)
        self.assertEqual(self.agent.state_version, 1)
        self.assertIsNone(self.agent.pending)
        self.assertIsNotNone(self.agent.pending_outcome)

    def test_focus_refusal_uses_actual_new_focus_and_original_confirm(self):
        self.agent.teacher.browse = False
        first = current(1, "combat_card_operation", [action("focus-old", "focus_target", "a")],
                        refs=[("a", "creature"), ("b", "creature")])
        def targeting(value, focused):
            page = value["value"]["observation"]["interaction"]
            page.update(stage="card_targeting",
                        content_schema="sts2.player-environment/surface/combat_card_operation_text_menu-1")
            page["content"]["surface"].update(
                kind="combat_card_operation", stage="card_targeting",
                focused_target_referent_id=focused,
            )
            return value
        self.consume(targeting(first, None))
        self.assertEqual(self.agent.teacher.focus_target_id, "a")
        notice = self.notice()
        self.agent.begin_next(notice)
        fresh = targeting(current(
            2, "combat_card_operation", [action("confirm-new", "confirm_target", "b")],
            refs=[("a", "creature"), ("b", "creature")],
        ), "b")
        _, report = self.agent.propose_current(fresh, notice)
        self.assertEqual(self.agent.teacher.focus_target_id, "a")
        self.agent.acknowledge(ack(report))
        self.assertIsNone(self.agent.teacher.focus_target_id)
        self.assertEqual(self.agent.directive(notice)["directive"]["selection"]["action_id"],
                         "confirm-new")
        self.assertEqual(self.agent.teacher.counts, {"focus_target": 1, "confirm_target": 1})

    def test_pile_and_card_intent_correction_preserve_prior_knowledge_and_counts(self):
        self.agent.teacher.phase = "progress"
        self.consume(current(1, "combat_turn", [action("pile-old", "open_combat_draw_pile")]))
        notice = self.notice()
        self.agent.begin_next(notice)
        _, report = self.agent.propose_current(current(
            2, "combat_turn", [action("pile-new", "open_combat_draw_pile")]
        ), notice)
        self.agent.acknowledge(ack(report))
        self.assertTrue(self.agent.teacher.combat_pile_viewed)
        self.assertEqual(self.agent.teacher.browse_choices, 2)
        self.assertEqual(self.agent.directive(notice)["directive"]["selection"]["action_id"],
                         "pile-new")
        for prior in (False, True):
            with self.subTest(prior_card_knowledge=prior):
                self.agent = module.NativeTeacherAgent(self.path)
                self.agent.teacher.browse = False
                self.agent.teacher.saw_card_entry = prior
                self.consume(current(
                    1, "combat_turn", [action("begin-old", "begin_card_play", "card")],
                    refs=[("card", "card")],
                ))
                notice = self.notice()
                self.agent.begin_next(notice)
                _, report = self.agent.propose_current(current(
                    2, "combat_turn", [action("end-new", "end_turn")]
                ), notice)
                self.assertTrue(self.agent.teacher.saw_card_entry)
                self.agent.acknowledge(ack(report))
                result = self.agent.directive(notice)["directive"]
                if prior:
                    self.assertEqual(result["selection"]["action_id"], "end-new")
                    self.assertTrue(self.agent.teacher.saw_card_entry)
                    self.assertEqual(self.agent.teacher.decisions, 2)
                else:
                    self.assertEqual(result["type"], "close")
                    self.assertFalse(self.agent.teacher.saw_card_entry)
                    self.assertEqual(self.agent.teacher.decisions, 1)
                self.assertEqual(self.agent.teacher.counts["begin_card_play"], 1)

    def test_result_forgery_duplicate_body_and_unchanged_revision_fail_closed(self):
        self.consume(current(1))
        valid = self.notice()
        mutations = (
            lambda v: v.update(action_id="wrong"),
            lambda v: v.update(basis_acquisition_id="wrong"),
            lambda v: v.update(consumption_id="wrong"),
            lambda v: v.update(state_version=True),
            lambda v: v["result"].update(snapshot_id="wrong"),
            lambda v: v["result"].update(delivery="unknown"),
            lambda v: v["result"].update(reason="another_not_started_reason"),
            lambda v: v["result"].update(stages=[
                {"stage": "input", "delivery": "delivered", "evidence": "x"}
            ]),
            lambda v: v["result"].update(action=action("old", "open_run_deck")),
            lambda v: v["result"].update(attribution=None),
            lambda v: v["result"]["attribution"].update(runtime_instance_id="wrong"),
            lambda v: v["result"]["attribution"].update(controller_generation=True),
            lambda v: v["result"].update(extra="undeclared"),
        )
        for mutate in mutations:
            forged = copy.deepcopy(valid)
            mutate(forged["operational_outcome"])
            with self.assertRaises(BoundaryError):
                self.agent.begin_next(forged)
        self.agent.begin_next(valid)
        changed = copy.deepcopy(valid)
        changed["operational_outcome"]["result"]["effect"] = "not_observed"
        with self.assertRaisesRegex(BoundaryError, "repeated_result_body_mismatch"):
            self.agent.begin_next(changed)
        drift = current(1)
        drift["value"]["observation"]["revision"] = 2
        with self.assertRaises(BoundaryError):
            self.agent.propose_current(drift, valid)
        self.assertEqual(self.agent.state_version, 1)
        self.assertIsNotNone(self.agent.pending_outcome)

    def test_actual_stdio_readiness_correction_and_interrupted_ACK_keep_exact_prefix(self):
        for mode in ("completed", "interrupt_before_ACK", "bad_ACK"):
            with self.subTest(mode=mode):
                self.agent = module.NativeTeacherAgent(self.path)
                agent = self.agent
                sink = io.StringIO()
                class Parent:
                    query_count = 0
                    consume_count = 0
                    act_count = 0
                    notice = None

                    def __init__(self, agent, sink, test, mode):
                        self.agent, self.sink, self.test, self.mode = agent, sink, test, mode

                    def readline(self, maximum):
                        agent, sink, test, mode = self.agent, self.sink, self.test, self.mode
                        reply = json.loads(sink.getvalue().splitlines()[-1])
                        kind = reply["message_type"]
                        if kind == "ready":
                            outgoing, request, body = "next", "initial", next_input(agent)
                        elif kind == "query":
                            self.query_count += 1
                            self_request = reply["request_id"]
                            observation = (current(1) if self.query_count < 3 else current(
                                2, actions=[action("fresh-open-deck", "open_run_deck")]
                            ))
                            outgoing, request, body = "query_result", self_request, observation
                        elif kind == "consumed":
                            self.consume_count += 1
                            if self.consume_count == 2 and mode == "interrupt_before_ACK":
                                return ""
                            completion = ack(reply["completion"])
                            if self.consume_count == 2 and mode == "bad_ACK":
                                completion["acquisition_id"] = "foreign"
                            outgoing, request, body = "consume_ack", reply["request_id"], completion
                        elif reply["output"]["directive"]["type"] == "act":
                            self.act_count += 1
                            if self.act_count == 2:
                                return ""
                            self.notice = test.notice()
                            outgoing, request, body = "next", "after-stale", self.notice
                        else:
                            test.assertEqual(reply["output"]["directive"]["type"], "await")
                            test.assertEqual(agent.state_version, 1)
                            test.assertEqual(agent.teacher.decisions, 1)
                            outgoing, request, body = "next", "changed-current", self.notice
                        field = ("result" if outgoing == "query_result" else
                                 "completion" if outgoing == "consume_ack" else "input")
                        return json_bytes({
                            "schema": module.SESSION_SCHEMA, "message_type": outgoing,
                            "session_id": "physical-session", "recovery_epoch": 0,
                            "request_id": request, field: body,
                        }).decode()

                parent = Parent(agent, sink, self, mode)
                if mode == "bad_ACK":
                    with self.assertRaises(BoundaryError):
                        module.serve(agent, parent, sink)
                else:
                    self.assertEqual(module.serve(agent, parent, sink), 0)
                replies = [json.loads(line) for line in sink.getvalue().splitlines()]
                queries = [reply for reply in replies if reply["message_type"] == "query"]
                self.assertEqual(len(queries), 3)
                self.assertTrue(all(query["input"]["method"] == "current" for query in queries))
                if mode == "completed":
                    self.assertEqual(agent.state_version, 2)
                    self.assertEqual(agent.teacher.decisions, 2)
                    self.assertIsNone(agent.pending_outcome)
                    self.assertEqual(replies[-1]["output"]["directive"]["selection"]["action_id"],
                                     "fresh-open-deck")
                else:
                    self.assertEqual(agent.state_version, 1)
                    self.assertEqual(agent.teacher.decisions, 1)
                    self.assertIsNotNone(agent.pending_outcome)
                    self.assertIsNotNone(agent.pending)
                self.assertEqual(agent.last_outcome, parent.notice["operational_outcome"])


class PureOperationalContractTests(unittest.TestCase):
    def test_closed_policy_bounds_presence_and_package_capability(self):
        policy = owned_current_known_stale_policy()
        self.assertEqual(policy, NORM["execution_policy"])
        self.assertEqual(manifest_execution_policy(NORM["manifest"]), policy)
        self.assertIsNone(manifest_execution_policy(NORM["legacy_manifest"]))
        for field, wrong in (("max_known_stale_rejections", True),
                             ("max_known_stale_rejections", 17),
                             ("max_consecutive_known_stale_rejections", 0),
                             ("max_consecutive_known_stale_rejections", 5)):
            bad = copy.deepcopy(policy)
            bad[field] = wrong
            with self.assertRaises(BoundaryError):
                checked_execution_policy(bad)
        with self.assertRaises(BoundaryError):
            checked_execution_policy(policy | {"undeclared": True})
        bad_manifest = copy.deepcopy(NORM["manifest"])
        bad_manifest["execution_policy"] = None
        with self.assertRaises(BoundaryError):
            manifest_execution_policy(bad_manifest)
        for mutation in (
            lambda m: m["input"].update(history_mode="full_reference"),
            lambda m: m["input"]["state_recovery"].update(max_state_bytes=False),
            lambda m: m["input"]["attachment"].update(eager_scope=["persistent"]),
            lambda m: m["requirements"].update(required_methods=["current", "retain", "release"]),
            lambda m: m["requirements"].update(required_methods=[{}]),
        ):
            invalid = copy.deepcopy(NORM["manifest"])
            mutation(invalid)
            with self.assertRaises(BoundaryError):
                manifest_execution_policy(invalid)
        control = NativeGraphControl()
        old = sampled_agent_spec(control)
        new = sampled_agent_spec(control, execution_policy=policy)
        self.assertEqual(old["version"], "1.1.0")
        self.assertEqual(new["version"], "1.2.0")
        self.assertIsNone(sampled_agent_execution_policy(old, control))
        self.assertEqual(sampled_agent_execution_policy(new, control), policy)
        self.assertEqual(old["model_control"], new["model_control"])
        for bad in (old | {"execution_policy": policy}, new | {"I": True},
                    {k: v for k, v in new.items() if k != "execution_policy"}):
            with self.assertRaises(BoundaryError):
                sampled_agent_execution_policy(bad, control)

    def test_original_long_handle_and_full_context_are_not_truncated_or_used_as_input(self):
        legacy = NORM["legacy_next"]
        frame = NORM["frames"]["map_a"]["observation"]
        item = copy.deepcopy(NORM["frames"]["map_a"]["catalog"][0])
        item["action_id"] = "x" * 65536
        intention = emitted_intention(
            acquisition_id=legacy["basis_acquisition_id"], consumption_id=legacy["consumption_id"],
            state_version=legacy["state_version"], observation=frame, action=item,
        )
        value = copy.deepcopy(NORM["opted_next_after_stale"])
        value["operational_outcome"]["action_id"] = item["action_id"]
        value["operational_outcome"]["result"] = copy.deepcopy(NORM["result_with_observed_frame"])
        clean, outcome = checked_next_input(
            value, execution_policy=owned_current_known_stale_policy(), intention=intention,
        )
        self.assertEqual(clean, legacy)
        self.assertEqual(outcome["action_id"], item["action_id"])
        self.assertEqual(outcome["result"]["observed_frame"],
                         NORM["result_with_observed_frame"]["observed_frame"])
        self.assertNotIn("observation", clean)
        value["operational_outcome"]["action_id"] += "x"
        with self.assertRaises(BoundaryError):
            checked_next_input(value, execution_policy=owned_current_known_stale_policy(),
                               intention=intention)

    def test_sampled_package_spec_builder_requires_explicit_policy_and_sampled_view(self):
        from stpd.native_graph_spec import optional_control
        from stpd.ordered_source_spec import DEFAULT_VIEW, SAMPLED_VIEW
        tree = ast.parse((REPO / "python/stpd/policy/native_structured_export.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "ordered_native_agent_spec")
        namespace = {
            "Any": object, "NativeGraphControl": NativeGraphControl,
            "optional_control": optional_control, "BoundaryError": BoundaryError,
            "DEFAULT_VIEW": DEFAULT_VIEW, "SAMPLED_VIEW": SAMPLED_VIEW,
            "sampled_agent_spec": sampled_agent_spec,
        }
        compiled = compile(ast.Module(body=[function], type_ignores=[]), "export-spec", "exec")
        exec(compiled, namespace)
        builder = namespace["ordered_native_agent_spec"]
        control = NativeGraphControl()
        self.assertEqual(builder(control, view=SAMPLED_VIEW), sampled_agent_spec(control))
        policy = owned_current_known_stale_policy()
        self.assertEqual(builder(control, view=SAMPLED_VIEW, execution_policy=policy),
                         sampled_agent_spec(control, execution_policy=policy))
        with self.assertRaises(BoundaryError):
            builder(control, view=DEFAULT_VIEW, execution_policy=policy)

    def test_actual_learned_begin_method_validates_then_strips_metadata_without_tensor_import(self):
        # Compile the actual production parser method only. The wrapper module
        # imports Torch; neither a fake numerical module nor model constructor is used.
        path = REPO / "python/stpd/policy/native_agent.py"
        tree = ast.parse(path.read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and n.name == "NativeStructuredAgent")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                      and n.name == "begin_sampled_next")
        namespace = {"Any": object, "checked_next_input": checked_next_input,
                     "BoundaryError": BoundaryError}
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
        frame = NORM["frames"]["map_a"]["observation"]
        action_value = NORM["frames"]["map_a"]["catalog"][0]
        legacy = NORM["legacy_next"]
        scorer = SimpleNamespace(
            pending=None, continuity=legacy["continuity_token"],
            consumption_id=legacy["consumption_id"], state_version=legacy["state_version"],
            acquisition_id=legacy["basis_acquisition_id"],
            memory=object(), scores=object(), frame=object(),
        )
        child = SimpleNamespace(
            sampled=True, execution_policy=owned_current_known_stale_policy(), scorer=scorer,
            last_intention=emitted_intention(
                acquisition_id=scorer.acquisition_id, consumption_id=scorer.consumption_id,
                state_version=scorer.state_version, observation=frame, action=action_value,
            ), last_outcome=None,
        )
        before = dict(vars(scorer))
        value = copy.deepcopy(NORM["opted_next_after_stale"])
        clean = namespace["begin_sampled_next"](child, value)
        self.assertEqual(clean, legacy)
        self.assertEqual(vars(scorer), before)
        self.assertNotIn("operational_outcome", clean)
        self.assertEqual(value, NORM["opted_next_after_stale"])
        self.assertEqual(child.last_outcome, value["operational_outcome"])
        namespace["begin_sampled_next"](child, copy.deepcopy(value))
        self.assertEqual(vars(scorer), before)
        child.execution_policy = None
        with self.assertRaises(BoundaryError):
            namespace["begin_sampled_next"](child, value)
        child.last_intention = child.last_outcome = None
        self.assertEqual(namespace["begin_sampled_next"](child, legacy), legacy)

if __name__ == "__main__":
    unittest.main()
