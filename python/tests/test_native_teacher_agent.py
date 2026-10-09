"""Pure program Agent law and honest code-artifact identity; no NN constructor/import."""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from metadata_import_guard import no_torch_imports as no_torch_imports

from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.fullrun.native_structured_inputs import native_catalog_digest
from stpd.policy import native_teacher_agent as module
from stpd.policy.native_public_teacher import NativePublicTeacher

REPO = Path(__file__).resolve().parents[2]
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
    return {
        "continuity_token": "continuity",
        "consumption_id": agent.consumption_id,
        "state_version": agent.state_version,
        "basis_acquisition_id": agent.acquisition_id,
        "received_cursor": "cursor-known",
    }


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
        before = next_input(self.agent)
        kind, report = self.agent.propose_current(value, before)
        self.assertEqual(kind, "consumed")
        self.assertEqual(self.agent.state_version, 0)
        self.agent.acknowledge(ack(report))
        self.assertEqual(self.agent.directive(before)["directive"]["type"], "await")
        self.assertEqual(self.agent.teacher.decisions, 0)
        self.assertEqual(self.agent.state_version, 1)

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


if __name__ == "__main__":
    unittest.main()
