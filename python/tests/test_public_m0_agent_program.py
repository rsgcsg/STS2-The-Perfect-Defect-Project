"""Optional declared chooser with synthetic scores/packages; no NN, game or quality claim."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_public_inputs import snapshot
from test_public_m0_policy_port import capabilities, pair
from test_public_m0_restoration import synthetic_completion
from test_public_m0_task import summary_snapshot

from spireagent.json_boundary import BoundaryError, json_bytes
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_model_registration import LocalModelRegistration, _requirements
from spireagent.workbench.local_models import LocalModelService
from stpd.fullrun.public_inputs import project_public_snapshot
from stpd.policy.public_m0_agent_program import card_reward_take_program_spec
from stpd.policy.public_m0_task import ready_summary_task_spec
from stpd.policy.token_decision import export_light_action_m0_model
from stpd.policy.token_port import PORT_SCHEMA, serve
from stpd.public_m0_policy_installation import (
    ADAPTER_VERSION,
    CONFIG_SCHEMA,
    PROFILE,
    PROGRAM_ADAPTER_VERSION,
    PROGRAM_CONFIG_SCHEMA,
    TASK_ADAPTER_VERSION,
    TASK_CONFIG_SCHEMA,
    bind_public_m0_export,
    validate,
)

ROOT = Path(__file__).resolve().parents[1]


def card_reward_snapshot(*, cards=3, reroll=True):
    """Source-shaped ordinary DTO, with semantic card/alternative joins and full C."""
    value = snapshot()
    value["persistent"] = None
    visible = [
        {
            "entity_id": f"card-{index}",
            "definition_id": f"CARD_{index}",
            "name": f"Card {index}",
            "cost": "1",
            "type": "Skill",
            "rarity": "Common",
            "description": "Gain 3 Block.",
            "is_upgraded": False,
            "is_selected": False,
        }
        for index in range(cards)
    ]
    alternatives = [{"entity_id": "alternative-skip", "index": 0, "label": "Skip", "enabled": True}]
    if reroll:
        alternatives.append(
            {"entity_id": "alternative-reroll", "index": 1, "label": "Reroll", "enabled": True}
        )
    value["interaction"].update(
        kind="card_reward_selection",
        stage="ready",
        content_schema="sts2.player-environment/surface/card_reward_selection-1",
        content={
            "surface": {
                "kind": "card_reward_selection",
                "cards": visible,
                "alternatives": alternatives,
                "selectable_card_entity_ids": [card["entity_id"] for card in visible],
            },
            "context": {"kind": "reward_flow", "reward_kind": "card_reward"},
        },
    )
    value["referents"] = [
        {
            "referent_id": item["entity_id"],
            "role": role,
            "kind": "entity",
            "label": item.get("name", item.get("label")),
            "properties_schema": f"sts2.player-environment/referent/{role}-1",
            "properties": copy.deepcopy(item),
            "state": {"visible": True, "enabled": True, "observation_basis": "native_visible_fact"},
        }
        for role, items in (("card", visible), ("option", alternatives))
        for item in items
    ]
    actions = [
        {
            "bound_action_id": f"bound-{item['entity_id']}",
            "verb": verb,
            "interaction_id": "interaction-1",
            "subject_referent_id": item["entity_id"],
            "arguments": [],
            "label": item.get("name", item.get("label")),
        }
        for verb, items in (("select", visible), ("activate", alternatives))
        for item in items
    ]
    value["bound_actions"].update(
        actions=actions, materialized_count=len(actions), total_count=len(actions)
    )
    return value


class RawScorer:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def score_snapshot(self, observed):
        self.calls.append(copy.deepcopy(observed))
        public = project_public_snapshot(observed, compact=True)
        return {action.key: self.values[action.key] for action in public.actions}


def scored_pair(observed=None, *, program=True, task=True):
    observed = card_reward_snapshot() if observed is None else observed
    adapter, request = pair(observed=observed, profile="public_compact")
    adapter.config = {"task_spec": ready_summary_task_spec()} if task else {}
    if program:
        adapter.config["program_spec"] = card_reward_take_program_spec()
    scores = {
        action["bound_action_id"]: (99.0 if action["verb"] == "activate" else float(index) - 20.0)
        for index, action in enumerate(request["bundle"]["observation"]["bound_actions"]["actions"])
    }
    adapter.scorer = RawScorer(scores)
    return adapter, request


def test_declared_card_choice_preserves_every_raw_neural_score_and_complete_catalog():
    adapter, request = scored_pair()
    before = copy.deepcopy(request)
    output = adapter.decide(request)
    assert output == {
        "candidate_digest": request["candidate_digest"],
        "scores": [-20.0, -19.0, -18.0, 99.0, 99.0],
        "selected_index": 2,
    }
    assert request == before
    assert adapter.scorer.calls == [request["bundle"]["observation"]]
    # Both Skip and Reroll remain scored higher than the chosen card. The output
    # is complete NN scores plus a declared final chooser, not an NN argmax claim.
    assert len(output["scores"]) == request["candidate_count"] == 5


def test_card_choice_uses_current_selectability_and_original_order_for_ties():
    observed = card_reward_snapshot()
    observed["interaction"]["content"]["surface"]["selectable_card_entity_ids"] = [
        "card-0",
        "card-1",
    ]
    actions = observed["bound_actions"]["actions"]
    observed["bound_actions"]["actions"] = [
        action for action in actions if action["subject_referent_id"] != "card-2"
    ]
    observed["bound_actions"].update(materialized_count=4, total_count=4)
    adapter, request = scored_pair(observed)
    adapter.scorer.values["bound-card-0"] = adapter.scorer.values["bound-card-1"] = -3.0
    assert adapter.decide(request)["selected_index"] == 0
    assert len(adapter.scorer.calls[0]["bound_actions"]["actions"]) == 4


def test_reordering_ids_and_translated_labels_cannot_turn_selection_into_native_index_mutation():
    observed = card_reward_snapshot()
    observed["bound_actions"]["actions"].reverse()
    adapter, request = scored_pair(observed)
    adapter.scorer.values.update({"bound-card-0": 7.0, "bound-card-1": 5.0, "bound-card-2": 3.0})
    chosen = adapter.decide(request)
    assert chosen["selected_index"] == 4
    assert (
        request["bundle"]["observation"]["bound_actions"]["actions"][4]["subject_referent_id"]
        == "card-0"
    )
    assert chosen["scores"] == [99.0, 99.0, 3.0, 5.0, 7.0]


@pytest.mark.parametrize("program", [False, True])
def test_legitimate_alternatives_only_uses_declared_unchanged_neural_argmax(program):
    adapter, request = scored_pair(card_reward_snapshot(cards=0), program=program)
    output = adapter.decide(request)
    assert output == {
        "candidate_digest": request["candidate_digest"],
        "scores": [99.0, 99.0],
        "selected_index": 0,
    }
    assert len(adapter.scorer.calls) == 1


def test_baseline_with_same_input_still_selects_neural_skip_and_other_decisions_are_identical():
    for observed in (card_reward_snapshot(), snapshot(), summary_snapshot(stage="intro")):
        baseline, old_request = scored_pair(observed, program=False)
        composed, new_request = scored_pair(observed)
        old, new = baseline.decide(old_request), composed.decide(new_request)
        assert old["scores"] == new["scores"]
        assert old["candidate_digest"] == new["candidate_digest"]
        assert baseline.scorer.calls == composed.scorer.calls
        if observed["interaction"]["kind"] == "card_reward_selection":
            assert old["selected_index"] == 3 and new["selected_index"] == 2
        else:
            assert old == new


def test_summary_task_abstains_unchanged_and_task_none_remains_neural():
    adapter, request = scored_pair(summary_snapshot())
    assert adapter.decide(request) == {
        "candidate_digest": request["candidate_digest"],
        "scores": [0.0, 0.0],
        "selected_index": None,
    }
    assert not adapter.scorer.calls
    adapter, request = scored_pair(summary_snapshot(), task=False)
    assert adapter.decide(request)["selected_index"] == 0 and len(adapter.scorer.calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s["interaction"].update(stage="settling"),
        lambda s: s["interaction"].update(content_schema="future-card-reward-schema"),
        lambda s: s["interaction"]["content"]["context"].update(reward_kind="future-reward"),
        lambda s: s["interaction"]["content"]["surface"].update(
            selectable_card_entity_ids=["missing"]
        ),
        lambda s: s["interaction"]["content"]["surface"].update(
            selectable_card_entity_ids=["card-0", "card-0"]
        ),
        lambda s: s["interaction"]["content"]["surface"]["cards"].append(
            copy.deepcopy(s["interaction"]["content"]["surface"]["cards"][0])
        ),
        lambda s: s["referents"][0].update(role="option"),
        lambda s: s["referents"][0].update(properties_schema="future-card-facts"),
    lambda s: s["referents"][0]["properties"].update(definition_id="other-visible-card"),
        lambda s: s["referents"][0]["state"].update(visible=False),
        lambda s: s["interaction"]["content"]["surface"]["alternatives"][0].update(index=True),
    ],
)
def test_malformed_claimed_card_reward_rejects_whole_before_scoring(change):
    observed = card_reward_snapshot()
    change(observed)
    adapter, request = scored_pair(observed)
    with pytest.raises(BoundaryError):
        adapter.decide(request)
    assert not adapter.scorer.calls


def test_duplicate_current_select_for_one_card_and_missing_selectable_binding_are_not_fallback():
    for duplicate in (False, True):
        observed = card_reward_snapshot()
        actions = observed["bound_actions"]["actions"]
        if duplicate:
            repeated = copy.deepcopy(actions[0])
            repeated["bound_action_id"] = "second-native-handle-for-same-card"
            actions.append(repeated)
        else:
            actions.pop(0)
        observed["bound_actions"].update(total_count=len(actions), materialized_count=len(actions))
        adapter, request = scored_pair(observed)
        with pytest.raises(BoundaryError, match="unqualified_card_reward_program_input"):
            adapter.decide(request)
        assert not adapter.scorer.calls


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(candidate_digest="0" * 64),
        lambda r: r.update(candidate_count=True),
        lambda r: r["bundle"]["observation"]["bound_actions"].update(total_count=6),
        lambda r: r["bundle"]["observation"]["information_policy"].update(
            includes_hidden_information=True
        ),
        lambda r: r["bundle"].update(reads=[{}]),
    ],
)
def test_program_cannot_bypass_complete_public_input_admission(change):
    adapter, request = scored_pair()
    change(request)
    with pytest.raises(BoundaryError):
        adapter.decide(request)
    assert not adapter.scorer.calls


@pytest.fixture
def bindings(tmp_path):
    archive, model, _, _ = synthetic_completion(tmp_path)
    export = tmp_path / "export"
    export_light_action_m0_model(archive, model.artifact_id, export)
    requirements, support = _requirements(capabilities(), input_profile=PROFILE)

    def bind(name, *, task=None, program=None):
        config, manifest = tmp_path / name / "config.json", tmp_path / name / "manifest.json"
        value = bind_public_m0_export(
            ROOT,
            export,
            config,
            manifest,
            manifest_id=name,
            policy={"id": name, "version": "1.0.0", "provider": "fixture", "architecture": "M0"},
            requirements=requirements,
            support=support,
            binding_root=tmp_path,
            task=task,
            program=program,
        )
        return config, manifest, value

    return bind


@pytest.mark.parametrize("task", [None, ready_summary_task_spec()])
def test_program_binding_has_new_identity_and_unchanged_model_or_baseline_bytes(
    bindings, tmp_path, task
):
    old_path, old_manifest, (old, baseline) = bindings("baseline")
    summary_path, summary_manifest, (_, summary) = bindings(
        "summary", task=ready_summary_task_spec()
    )
    before = [
        path.read_bytes() for path in (old_path, old_manifest, summary_path, summary_manifest)
    ]
    config_path, manifest_path, (config, manifest) = bindings(
        "program", task=task, program=card_reward_take_program_spec()
    )
    assert old["schema"] == CONFIG_SCHEMA and baseline["adapter"]["version"] == ADAPTER_VERSION
    assert summary["adapter"]["version"] == TASK_ADAPTER_VERSION
    assert config["schema"] == PROGRAM_CONFIG_SCHEMA
    assert manifest["adapter"]["version"] == PROGRAM_ADAPTER_VERSION
    assert config["program_spec"] == manifest["adapter_config"]["public_m0"]["program_spec"]
    assert config["task_spec"] == manifest["adapter_config"]["public_m0"]["task_spec"] == task
    assert config["model_id"] == old["model_id"] and manifest["artifact"] == baseline["artifact"]
    assert validate(ROOT, config_path, manifest_path, binding_root=tmp_path) == (config, manifest)
    assert before == [
        path.read_bytes() for path in (old_path, old_manifest, summary_path, summary_manifest)
    ]


@pytest.mark.parametrize(
    "change",
    [
        lambda c, m: c.update(program_spec=None),
        lambda c, m: c["program_spec"].update(selection="hidden-score-boost"),
        lambda c, m: c["program_spec"].update(scores="program-adjusted-scores"),
        lambda c, m: c["program_spec"].update(zero_eligible="automatic-reroll"),
        lambda c, m: c["program_spec"].update(extra=True),
        lambda c, m: m["adapter_config"]["public_m0"].pop("program_spec"),
        lambda c, m: m["adapter"].update(version=TASK_ADAPTER_VERSION),
        lambda c, m: c.update(schema=TASK_CONFIG_SCHEMA),
        lambda c, m: c.pop("task_spec"),
    ],
)
def test_program_descriptor_and_version_drift_cannot_be_resealed_into_a_binding(
    bindings, tmp_path, change
):
    config_path, manifest_path, (config, manifest) = bindings(
        "program", task=ready_summary_task_spec(), program=card_reward_take_program_spec()
    )
    change(config, manifest)
    config_path.write_bytes(json_bytes(config))
    manifest["adapter_config"]["public_m0"]["config"]["sha256"] = hashlib.sha256(
        config_path.read_bytes()
    ).hexdigest()
    manifest_path.write_bytes(json_bytes(manifest))
    with pytest.raises(BoundaryError):
        validate(ROOT, config_path, manifest_path, binding_root=tmp_path)


def test_legacy_binding_cannot_smuggle_the_new_program(bindings, tmp_path):
    config_path, manifest_path, (config, _) = bindings("baseline")
    config["program_spec"] = card_reward_take_program_spec()
    config_path.write_bytes(json_bytes(config))
    with pytest.raises(BoundaryError, match="missing_or_unknown_fields"):
        validate(ROOT, config_path, manifest_path, binding_root=tmp_path)


@pytest.mark.parametrize(
    "program", [{}, {**card_reward_take_program_spec(), "id": "future-program"}]
)
def test_undeclared_program_rejects_before_binding_publication(bindings, tmp_path, program):
    with pytest.raises(BoundaryError, match="unsupported_program_spec"):
        bindings("rejected", program=program)
    assert not (tmp_path / "rejected").exists()


def test_common_explicit_program_registration_keeps_default_and_task_none_selectable(
    tmp_path, monkeypatch
):
    archive, model, _, _ = synthetic_completion(tmp_path)
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    export = config.state_dir / "model-exports" / model.artifact_id
    export_light_action_m0_model(archive, model.artifact_id, export)
    verify_calls = []
    exported = SimpleNamespace(
        status_for_model=lambda _: {
            "availability": "ready",
            "operation": {
                "status": "completed",
                "model_id": model.artifact_id,
                "model_type": "public_m0",
                "verified_receipt": {"synthetic": True},
            },
        },
        verified_public_m0_for_registration=lambda identity, **kwargs: (
            verify_calls.append(identity) or export
        ),
    )
    service = LocalModelService(config)
    registration = LocalModelRegistration(config, exported, service)
    monkeypatch.setattr(
        registration, "_native_runtime", lambda **kwargs: (tmp_path, tmp_path / "sdk")
    )
    monkeypatch.setattr(registration, "_capabilities", lambda *args, **kwargs: capabilities())
    monkeypatch.setattr(service, "_public_manifest_contract", lambda *args, **kwargs: None)
    default = registration.register(model.artifact_id)
    task_none = registration.register(model.artifact_id, task=None)
    default_entry = service.selection(default["selection_id"])
    unchanged = [
        service.entry_path(default_entry, name).read_bytes() for name in ("config", "manifest")
    ]
    assert (
        registration.status(model.artifact_id, program=card_reward_take_program_spec())["status"]
        == "not_registered"
    )
    composed = registration.register(model.artifact_id, program=card_reward_take_program_spec())
    assert len({default["selection_id"], task_none["selection_id"], composed["selection_id"]}) == 3
    assert (
        registration.register(model.artifact_id, program=card_reward_take_program_spec())
        == composed
    )
    assert (
        registration.status(model.artifact_id, program=card_reward_take_program_spec()) == composed
    )
    assert registration.status(model.artifact_id) == default
    assert registration.register(model.artifact_id) == default
    assert registration.status(model.artifact_id, task=None) == task_none
    assert unchanged == [
        service.entry_path(default_entry, name).read_bytes() for name in ("config", "manifest")
    ]
    entry = service.selection(composed["selection_id"])
    catalog = next(
        row for row in service.catalog()["policies"] if row["selection_id"] == entry["id"]
    )
    assert catalog["program_spec"] == card_reward_take_program_spec()
    assert catalog["task_spec"] == ready_summary_task_spec() and catalog["agent_version"] == "1.2.0"
    assert "选牌奖励程序" in catalog["label"]
    manifest_path = service.entry_path(entry, "manifest")
    prototype, request = scored_pair()
    prototype.scorer.artifact = model
    monkeypatch.setattr(
        "stpd.policy.public_m0_port.LightActionM0DecisionScorer",
        lambda *args, **kwargs: prototype.scorer,
    )
    from stpd.policy.public_m0_port import PublicM0PolicyAdapter

    adapter = PublicM0PolicyAdapter(
        service.entry_path(entry, "config"), manifest_path, service.private_root
    )
    request["manifest"] = copy.deepcopy(adapter.manifest)
    wire = json_bytes(
        {"schema": PORT_SCHEMA, "message_type": "decide", "request_id": "choice", "input": request}
    )
    destination = io.StringIO()
    assert serve(adapter, io.StringIO(wire.decode("utf-8")), destination) == 0
    ready, reply = map(json.loads, destination.getvalue().splitlines())
    assert ready["adapter"]["version"] == "1.2.0"
    decision_path = tmp_path / "actual-port-output.json"
    decision_path.write_bytes(json_bytes(reply["output"]))
    script = (
        "import {readFile} from 'node:fs/promises';"
        "const rt=await import(process.argv[1]);"
        "const manifest=rt.validatePolicyManifest("
        "JSON.parse(await readFile(process.argv[2],'utf8')));"
        "const decision=rt.validateAdapterDecision("
        "JSON.parse(await readFile(process.argv[3],'utf8')),process.argv[4],5);"
        "console.log(JSON.stringify({adapter:manifest.adapter,decision}));"
    )
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            script,
            (ROOT.parent / "components/policy-runtime/dist/index.js").as_uri(),
            str(manifest_path),
            str(decision_path),
            request["candidate_digest"],
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == {"adapter": ready["adapter"], "decision": reply["output"]}
    assert reply["output"]["selected_index"] == 2 and reply["output"]["scores"] == [
        -20.0,
        -19.0,
        -18.0,
        99.0,
        99.0,
    ]
    assert verify_calls  # Real common registration rechecks the explicit export owner.


def test_program_argument_cannot_silently_apply_to_other_model_families(tmp_path):
    config = ProjectConfig(tmp_path / "state", "", "", None, combination())
    identity = "a" * 64
    exported = SimpleNamespace(
        status_for_model=lambda _: {
            "operation": {"model_id": identity, "model_type": "public_m2", "status": "completed"}
        }
    )
    registration = LocalModelRegistration(config, exported, LocalModelService(config))
    with pytest.raises(BoundaryError, match="program_requires_public_m0"):
        registration.register(identity, program=card_reward_take_program_spec())
    with pytest.raises(BoundaryError, match="program_requires_public_m0"):
        registration.status(identity, program=card_reward_take_program_spec())
