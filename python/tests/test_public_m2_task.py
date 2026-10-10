"""Qualified M2 terminal consumption and program abstention; no native/quality claim."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import torch
from test_public_m0_policy_port import capabilities
from test_public_m0_task import summary_snapshot
from test_public_m2_port import decide, previous, request, setup
from test_public_m2_runtime import metadata_export, typed_verify

from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.policy.public_m0_task import ready_summary_task_spec as m0_task_spec
from stpd.policy.public_m2_port import PORT_SCHEMA, PublicM2PolicyAdapter, serve
from stpd.policy.public_m2_task import ready_summary_task_spec
from stpd.public_m2_policy_installation import (
    ADAPTER_VERSION,
    CONFIG_SCHEMA,
    PROFILE,
    TASK_ADAPTER_VERSION,
    TASK_CONFIG_SCHEMA,
    bind_public_m2_export,
    validate,
)

ROOT = Path(__file__).resolve().parents[1]


def task_adapter(*, task=True):
    baseline, model = setup()
    manifest = copy.deepcopy(baseline.manifest)
    manifest["adapter"]["version"] = TASK_ADAPTER_VERSION if task else ADAPTER_VERSION
    manifest["support"]["interaction_kinds"].append("game_over")
    manifest["support"]["action_verbs"].append("activate")
    if task:
        manifest["adapter_config"]["public_m2_installation"] = {
            "task_spec": ready_summary_task_spec()
        }
    return PublicM2PolicyAdapter(
        model, baseline.tokenizer, baseline.config, manifest,
        weights_sha256="c" * 64, input_digest="d" * 64,
    ), model


@pytest.mark.parametrize("result", ["win", "loss"])
@pytest.mark.parametrize("destination", ["main_menu", "timeline"])
@pytest.mark.parametrize("count", [1, 2])
def test_summary_consumes_normal_model_step_and_w_once_then_caches_program_abstention(
    result, destination, count
):
    adapter, model = task_adapter()
    payload = request(adapter, snapshot=summary_snapshot(
        result=result, destination=destination, count=count))
    before = copy.deepcopy(payload)
    output, completion = decide(adapter, payload)
    assert output == {"candidate_digest": payload["decision"]["candidate_digest"],
                      "scores": [0.0] * count, "selected_index": None}
    assert len(model.calls) == 1 and model.calls[0][2] is True
    assert torch.equal(adapter._memory, torch.ones(8, 384))
    assert payload == before
    replay = copy.deepcopy(payload)
    replay["decision"]["bundle"]["observation"]["observed_at"] = "a-new-poll-time"
    replay["control"]["previous_action"] = previous()
    cached, acknowledged = decide(adapter, replay)
    assert cached == output and len(model.calls) == 1
    assert torch.equal(adapter._memory, torch.ones(8, 384))
    assert completion["previous_action_request_id"] is None
    assert acknowledged["previous_action_request_id"] == "receipt-a"
    output["scores"].clear()
    assert decide(adapter, replay)[0] == cached and len(model.calls) == 1


def test_terminal_new_ordinal_preserves_prior_w_and_does_not_reset_or_repeat():
    adapter, model = task_adapter()
    decide(adapter, request(adapter))
    payload = request(adapter, ordinal=2, sequence=8,
                      snapshot=summary_snapshot(), previous=previous())
    output, completion = decide(adapter, payload)
    assert output["selected_index"] is None and output["scores"] == [0.0, 0.0]
    assert len(model.calls) == 2 and model.calls[-1][2] is False
    assert torch.equal(adapter._memory, torch.full((8, 384), 2.0))
    assert completion["observation_ordinal"] == 2
    assert decide(adapter, payload) == (output, completion) and len(model.calls) == 2


@pytest.mark.parametrize("stage", ["intro", "intro_animating", "summary_animating"])
def test_intro_and_animation_keep_normal_neural_scores_and_observation_consumption(stage):
    adapter, model = task_adapter()
    output, _ = decide(adapter, request(adapter, snapshot=summary_snapshot(stage=stage)))
    assert output["scores"] == [0.0, 1.0] and output["selected_index"] == 1
    assert len(model.calls) == 1 and torch.equal(adapter._memory, torch.ones(8, 384))


def test_task_none_preserves_neural_summary_scoring_and_nonterminal_path_is_identical():
    for observed in (None, summary_snapshot(stage="intro"), summary_snapshot()):
        baseline, old_model = task_adapter(task=False)
        composed, new_model = task_adapter()
        old_payload = request(baseline, snapshot=observed)
        new_payload = request(composed, snapshot=observed)
        old, _ = decide(baseline, old_payload)
        new, _ = decide(composed, new_payload)
        assert old == {"candidate_digest": old_payload["decision"]["candidate_digest"],
                       "scores": [0.0, 1.0], "selected_index": 1}
        if observed is None or observed["interaction"]["stage"] == "intro":
            assert new == old
        else:
            assert new["selected_index"] is None
        assert old_model.calls == new_model.calls
        assert torch.equal(baseline._memory, composed._memory)


@pytest.mark.parametrize("change", [
    lambda p: p["decision"].update(candidate_count=True),
    lambda p: p["decision"].update(candidate_digest="0" * 64),
    lambda p: p["decision"]["bundle"].update(reads=[{}]),
    lambda p: p["decision"]["bundle"]["observation"].update(status="observed"),
    lambda p: p["decision"]["bundle"]["observation"]["completeness"].update(status="partial"),
    lambda p: p["decision"]["bundle"]["observation"]["information_policy"].update(
        includes_hidden_information=True),
    lambda p: p["decision"]["bundle"]["observation"]["bound_actions"].update(total_count=3),
    lambda p: p["decision"]["bundle"]["observation"]["bound_actions"].update(materialized_count=1),
    lambda p: p["decision"]["bundle"]["observation"]["bound_actions"]["actions"].reverse(),
    lambda p: p["decision"]["bundle"]["observation"]["bound_actions"]["actions"][0].update(
        subject_referent_id="missing"),
    lambda p: p["decision"]["bundle"]["observation"]["bound_actions"]["actions"][0].update(
        interaction_id="stale"),
    lambda p: p["decision"]["bundle"]["observation"]["interaction"]["content"].update(hidden_rng=3),
])
def test_terminal_branch_cannot_bypass_complete_public_input_or_catalog_admission(change):
    adapter, model = task_adapter()
    payload = request(adapter, snapshot=summary_snapshot())
    change(payload)
    with pytest.raises(BoundaryError):
        decide(adapter, payload)
    assert not model.calls and adapter._memory is None


@pytest.mark.parametrize("change", [
    lambda s: s["interaction"].update(stage="summary_animating"),
    lambda s: s["interaction"]["content"]["surface"].update(stage="intro"),
    lambda s: s["interaction"]["content"]["surface"].update(can_return=1),
    lambda s: s["interaction"]["content"]["surface"].update(can_advance_summary=True),
    lambda s: s["interaction"]["content"]["surface"].update(return_destination="unknown"),
    lambda s: s["interaction"]["content"]["context"].update(game_mode="daily"),
    lambda s: s["interaction"]["content"]["context"].update(result="unknown"),
    lambda s: s["interaction"]["content"]["context"].update(score=True),
    lambda s: s["interaction"]["content"]["context"].update(score=2**31),
])
def test_malformed_claimed_summary_fails_before_model_write_without_fallback(change):
    adapter, model = task_adapter()
    observed = summary_snapshot()
    change(observed)
    with pytest.raises(BoundaryError, match="malformed_game_over_task_input") as error:
        decide(adapter, request(adapter, snapshot=observed))
    assert error.value.stage == "public_m2_policy"
    assert not model.calls and adapter._active is None and adapter._memory is None


def test_terminal_qualification_includes_support_and_normal_codec_limits():
    adapter, model = task_adapter()
    adapter.manifest["support"]["action_verbs"] = ["play"]
    with pytest.raises(BoundaryError, match="unsupported_whole_decision"):
        decide(adapter, request(adapter, snapshot=summary_snapshot()))
    assert not model.calls
    adapter, model = task_adapter()
    adapter.config = replace(adapter.config, shape=replace(adapter.config.shape, max_tokens=1))
    with pytest.raises(BoundaryError, match="page_limit_no_truncation"):
        decide(adapter, request(adapter, snapshot=summary_snapshot()))
    assert not model.calls and adapter._memory is None


def test_terminal_neural_failure_is_unknown_write_and_cannot_become_program_abstention():
    adapter, model = task_adapter()
    model.fail = True
    with pytest.raises(RuntimeError, match="synthetic unknown write"):
        decide(adapter, request(adapter, snapshot=summary_snapshot()))
    assert len(model.calls) == 1 and adapter.closed and adapter._memory is None
    with pytest.raises(BoundaryError, match="adapter_closed_after_unknown"):
        decide(adapter, request(adapter, token="new-token", snapshot=summary_snapshot()))


def test_terminal_program_output_retains_port4_completion_and_wire_contract():
    adapter, model = task_adapter()
    payload = request(adapter, snapshot=summary_snapshot())
    wire = json_bytes({"schema": PORT_SCHEMA, "message_type": "decide",
                       "request_id": "summary", **payload})
    destination = io.StringIO()
    assert serve(adapter, io.StringIO(wire.decode("utf-8")), destination) == 0
    ready, response = map(json.loads, destination.getvalue().splitlines())
    assert ready["adapter"]["version"] == TASK_ADAPTER_VERSION
    assert response["output"] == {"candidate_digest": payload["decision"]["candidate_digest"],
                                  "scores": [0.0, 0.0], "selected_index": None}
    assert response["completion"]["observation_ordinal"] == 1 and len(model.calls) == 1


@pytest.mark.parametrize("change", [
    lambda m: m["adapter_config"]["public_m2_installation"].update(task_spec=None),
    lambda m: m["adapter_config"].update(public_m2_installation="future-layout"),
    lambda m: m["adapter_config"]["public_m2_installation"].update(task_spec=m0_task_spec()),
    lambda m: m["adapter_config"]["public_m2_installation"]["task_spec"].update(
        terminal_observation="skip-model-step"),
    lambda m: m["adapter"].update(version=ADAPTER_VERSION),
    lambda m: m["adapter_config"]["public_m2_installation"].pop("task_spec"),
])
def test_direct_adapter_cannot_cross_task_family_version_or_consumption_rule(change):
    prototype, model = task_adapter()
    manifest = copy.deepcopy(prototype.manifest)
    change(manifest)
    with pytest.raises(BoundaryError, match="unsupported_task_spec"):
        PublicM2PolicyAdapter(model, prototype.tokenizer, prototype.config, manifest,
                              weights_sha256="c" * 64, input_digest="d" * 64)
    assert not model.calls


@pytest.fixture
def bindings(tmp_path):
    export, _ = metadata_export(tmp_path)
    from spireagent.workbench.local_model_registration import _requirements

    requirements, support = _requirements(capabilities(), input_profile=PROFILE)

    def bind(name, *, task=None):
        config, manifest = tmp_path / name / "config.json", tmp_path / name / "manifest.json"
        result = bind_public_m2_export(
            ROOT, export, config, manifest, manifest_id=name,
            policy={"id": name, "version": "1.0.0", "provider": "fixture", "architecture": "M2"},
            requirements=requirements, support=support, binding_root=tmp_path, task=task,
        )
        return config, manifest, result

    return bind


def test_task_binding_versions_mirrors_exact_descriptor_without_changing_baseline_bytes(
    bindings, tmp_path
):
    old_path, old_manifest, (old, baseline) = bindings("baseline")
    before = old_path.read_bytes(), old_manifest.read_bytes()
    config_path, manifest_path, (config, manifest) = bindings(
        "summary", task=ready_summary_task_spec())
    assert old["schema"] == CONFIG_SCHEMA and baseline["adapter"]["version"] == ADAPTER_VERSION
    assert "task_spec" not in old
    assert "task_spec" not in baseline["adapter_config"]["public_m2_installation"]
    assert config["schema"] == TASK_CONFIG_SCHEMA
    assert manifest["adapter"]["version"] == TASK_ADAPTER_VERSION
    assert (config["task_spec"]
            == manifest["adapter_config"]["public_m2_installation"]["task_spec"]
            == ready_summary_task_spec())
    assert config["model_id"] == old["model_id"] and manifest["artifact"] == baseline["artifact"]
    assert validate(ROOT, config_path, manifest_path, binding_root=tmp_path) == (config, manifest)
    assert validate(ROOT, old_path, old_manifest, binding_root=tmp_path) == (old, baseline)
    assert before == (old_path.read_bytes(), old_manifest.read_bytes())


@pytest.mark.parametrize("change", [
    lambda c, m: c.update(task_spec=None),
    lambda c, m: c.update(task_spec=m0_task_spec()),
    lambda c, m: c["task_spec"].update(terminal_observation="skip-model-step"),
    lambda c, m: c["task_spec"].update(abstention_scores="neural-scores"),
    lambda c, m: c["task_spec"].update(id="future-task"),
    lambda c, m: c["task_spec"].update(extra=True),
    lambda c, m: m["adapter"].update(version=ADAPTER_VERSION),
    lambda c, m: m["adapter_config"]["public_m2_installation"].pop("task_spec"),
    lambda c, m: m["adapter_config"]["public_m2_installation"]["task_spec"].update(id="other"),
    lambda c, m: c.update(schema=CONFIG_SCHEMA),
])
def test_task_binding_rejects_cross_version_family_or_mirrored_rule_drift(
    bindings, tmp_path, change
):
    config_path, manifest_path, (config, manifest) = bindings(
        "summary", task=ready_summary_task_spec())
    change(config, manifest)
    config_path.write_bytes(json_bytes(config))
    manifest["adapter_config"]["public_m2_installation"]["config"]["sha256"] = hashlib.sha256(
        config_path.read_bytes()).hexdigest()
    manifest_path.write_bytes(json_bytes(manifest))
    with pytest.raises(BoundaryError):
        validate(ROOT, config_path, manifest_path, binding_root=tmp_path)


@pytest.mark.parametrize("task", [m0_task_spec(), {}, {**ready_summary_task_spec(), "extra": True}])
def test_binder_cannot_publish_an_undeclared_task(bindings, tmp_path, task):
    with pytest.raises(BoundaryError, match="unsupported_task_spec"):
        bindings("invalid", task=task)
    assert not (tmp_path / "invalid").exists()


def test_legacy_config_cannot_smuggle_task(bindings, tmp_path):
    config_path, manifest_path, (config, _) = bindings("baseline")
    config["task_spec"] = ready_summary_task_spec()
    config_path.write_bytes(json_bytes(config))
    with pytest.raises(BoundaryError, match="missing_or_unknown_fields"):
        validate(ROOT, config_path, manifest_path, binding_root=tmp_path)


def test_task_and_actual_adapter_use_unchanged_runtime_handoff_and_typed_evidence(tmp_path):
    adapter, _ = task_adapter()
    profile, snapshot = tmp_path / "profile.json", tmp_path / "snapshot.json"
    profile.write_bytes(json_bytes(adapter.manifest["adapter_config"]))
    snapshot.write_bytes(json_bytes(summary_snapshot()))
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}}
    result = subprocess.run(
        ["node", str(ROOT / "tests/public_m2_task_runtime.mjs"),
         str(ROOT.parent), sys.executable, str(tmp_path / "runtime"), str(profile), str(snapshot)],
        cwd=ROOT.parent, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    assert observed["submitted"] == 1 and observed["released"] == 1
    assert observed["terminal_submissions"] == 0
    assert observed["reason"] == "policy_abstained" and observed["mode"] == "human"
    assert observed["step_calls"] == 2 and observed["terminal_ordinal"] == 2
    assert observed["selected_index"] is None
    assert typed_verify(Path(observed["evidence"])) == {"verdict": "pass", "findings": []}
