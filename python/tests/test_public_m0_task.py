"""Synthetic Public M0 task composition contracts; no native or quality claim."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from test_public_inputs import snapshot
from test_public_m0_policy_port import capabilities, pair, synthetic_noncombat_snapshot
from test_public_m0_restoration import synthetic_completion

from spireagent.json_boundary import BoundaryError, json_bytes
from stpd.fullrun.public_inputs import project_public_snapshot
from stpd.policy.public_m0_task import ready_summary_task_spec
from stpd.policy.token_decision import export_light_action_m0_model
from stpd.policy.token_port import PORT_SCHEMA, serve
from stpd.public_m0_policy_installation import (
    ADAPTER_VERSION,
    CONFIG_SCHEMA,
    TASK_ADAPTER_VERSION,
    TASK_CONFIG_SCHEMA,
    _validate_requirements,
    bind_public_m0_export,
    validate,
)

ROOT = Path(__file__).resolve().parents[1]


def summary_snapshot(*, result="loss", destination="main_menu", stage="summary", count=2):
    # Fields follow GameOverSurfaceReader and its public LiveHostContracts DTOs.
    # The synthetic catalog keeps two identical-looking, distinct legal handles
    # so program abstention must preserve complete ordered C without selecting.
    value = snapshot()
    value["persistent"] = None
    value["interaction"].update(
        kind="game_over",
        stage=stage,
        content_schema="sts2.player-environment/surface/game_over-1",
        content={
            "surface": {
                "kind": "game_over",
                "stage": stage,
                "return_destination": destination if stage == "summary" else None,
                "can_advance_summary": stage == "intro",
                "can_return": stage == "summary",
                "other_controls": [],
            },
            "context": {
                "kind": "game_over",
                "result": result,
                "game_mode": "standard",
                "score": 0 if stage == "summary" else None,
                "floor_reached": 1 if stage == "summary" else None,
                "ascension": 0 if stage == "summary" else None,
            },
        },
    )
    value["referents"] = []
    value["bound_actions"]["actions"] = [
        {
            "bound_action_id": key,
            "verb": "activate",
            "interaction_id": "interaction-1",
            "subject_referent_id": None,
            "arguments": [],
            "label": "Return",
        }
        for key in ("return-a", "return-b")[:count]
    ]
    value["bound_actions"].update(total_count=count, materialized_count=count)
    return value


def task_pair(observed=None, *, profile="public_compact", task=True):
    adapter, request = pair(observed=observed or summary_snapshot(), profile=profile)
    adapter.config = {"task_spec": ready_summary_task_spec()} if task else {}
    return adapter, request


@pytest.mark.parametrize("result", ["win", "loss"])
@pytest.mark.parametrize("destination", ["main_menu", "timeline"])
@pytest.mark.parametrize("profile", ["public_lite", "public_compact"])
@pytest.mark.parametrize("count", [1, 2])
def test_ready_summary_abstains_in_full_catalog_order_without_neural_scoring(
    result,
    destination,
    profile,
    count,
):
    adapter, request = task_pair(
        summary_snapshot(result=result, destination=destination, count=count), profile=profile
    )
    before = copy.deepcopy(request)
    with patch.object(adapter.scorer, "score_snapshot") as neural:
        assert adapter.decide(request) == {
            "candidate_digest": request["candidate_digest"],
            "scores": [0.0] * count,
            "selected_index": None,
        }
        neural.assert_not_called()
    assert request == before


def test_task_program_abstention_uses_existing_ndjson_decision_contract():
    adapter, request = task_pair()
    source = io.StringIO(
        json.dumps(
            {
                "schema": PORT_SCHEMA,
                "message_type": "decide",
                "request_id": "summary-request",
                "input": request,
            }
        )
        + "\n"
    )
    output = io.StringIO()
    assert serve(adapter, source, output) == 0
    ready, decision = map(json.loads, output.getvalue().splitlines())
    assert ready["message_type"] == "ready"
    assert decision["request_id"] == "summary-request"
    assert decision["output"] == {
        "candidate_digest": request["candidate_digest"],
        "scores": [0.0, 0.0],
        "selected_index": None,
    }
    assert adapter.closed is True


@pytest.mark.parametrize("stage", ["intro", "intro_animating", "summary_animating"])
def test_intro_and_animation_stages_do_not_complete_the_task(stage):
    adapter, request = task_pair(summary_snapshot(stage=stage))
    with patch.object(
        adapter.scorer, "score_snapshot", wraps=adapter.scorer.score_snapshot
    ) as neural:
        assert adapter.decide(request)["selected_index"] == 1
        neural.assert_called_once_with(request["bundle"]["observation"])


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s["interaction"].update(content_schema="future-game-over-schema"),
        lambda s: s["interaction"].update(stage="summary_animating"),
        lambda s: s["interaction"]["content"]["surface"].update(stage="intro"),
        lambda s: s["interaction"]["content"]["surface"].update(kind="main_menu"),
        lambda s: s["interaction"]["content"]["context"].update(kind="run"),
        lambda s: s["interaction"]["content"]["surface"].update(can_return=1),
        lambda s: s["interaction"]["content"]["surface"].update(can_return=False),
        lambda s: s["interaction"]["content"]["surface"].update(can_advance_summary=True),
        lambda s: s["interaction"]["content"]["surface"].update(return_destination="unknown"),
        lambda s: s["interaction"]["content"]["context"].update(game_mode="daily"),
        lambda s: s["interaction"]["content"]["context"].update(result="unknown"),
        lambda s: s["interaction"]["content"].pop("context"),
    ],
)
def test_malformed_summary_fails_before_any_neural_call(change):
    value = summary_snapshot()
    change(value)
    adapter, request = task_pair(value)
    with patch.object(adapter.scorer, "score_snapshot") as neural:
        with pytest.raises(BoundaryError, match="malformed_game_over_task_input"):
            adapter.decide(request)
        neural.assert_not_called()


@pytest.mark.parametrize("field", ["score", "floor_reached", "ascension"])
@pytest.mark.parametrize("value", [None, True, 1.0, "0", 2**31, -(2**31) - 1])
def test_summary_requires_public_int32_fields_without_numeric_coercion(field, value):
    observed = summary_snapshot()
    observed["interaction"]["content"]["context"][field] = value
    adapter, request = task_pair(observed)
    with pytest.raises(BoundaryError, match="malformed_game_over_task_input"):
        adapter.decide(request)


@pytest.mark.parametrize("value", [-(2**31), -1, 0, 2**31 - 1])
def test_task_adds_no_score_floor_or_ascension_threshold(value):
    observed = summary_snapshot()
    observed["interaction"]["content"]["context"].update(
        score=value,
        floor_reached=value,
        ascension=value,
    )
    adapter, request = task_pair(observed)
    assert adapter.decide(request)["selected_index"] is None


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(candidate_count=True),
        lambda r: r.update(candidate_digest="0" * 64),
        lambda r: r["bundle"].update(reads=[{}]),
        lambda r: r["bundle"]["observation"].update(status="observed"),
        lambda r: r["bundle"]["observation"]["completeness"].update(status="partial"),
        lambda r: r["bundle"]["observation"]["information_policy"].update(
            includes_hidden_information=True
        ),
        lambda r: r["bundle"]["observation"]["bound_actions"].update(total_count=3),
        lambda r: r["bundle"]["observation"]["bound_actions"].update(materialized_count=1),
        lambda r: r["bundle"]["observation"]["bound_actions"]["actions"].reverse(),
        lambda r: r["bundle"]["observation"]["bound_actions"]["actions"][0].update(
            subject_referent_id="missing"
        ),
        lambda r: r["bundle"]["observation"]["bound_actions"]["actions"][0].update(
            interaction_id="stale"
        ),
        lambda r: r["bundle"]["observation"]["interaction"]["content"].update(hidden_rng=3),
    ],
)
def test_terminal_branch_cannot_bypass_complete_public_input_admission(change):
    adapter, request = task_pair()
    change(request)
    with patch.object(adapter.scorer, "score_snapshot") as neural:
        with pytest.raises(BoundaryError):
            adapter.decide(request)
        neural.assert_not_called()


def test_terminal_branch_rejects_duplicate_handles_even_with_matching_request_digest():
    from spireagent.encoding import canonical_json

    observed = summary_snapshot()
    observed["bound_actions"]["actions"][1]["bound_action_id"] = "return-a"
    adapter, request = task_pair(observed)
    assert (
        request["candidate_digest"]
        == hashlib.sha256(
            canonical_json(["return-a", "return-a"]).encode(),
        ).hexdigest()
    )
    with pytest.raises(BoundaryError, match="unique_bindings_required"):
        adapter.decide(request)


def test_legacy_task_none_keeps_neural_summary_selection():
    adapter, request = task_pair(task=False)
    with patch.object(
        adapter.scorer, "score_snapshot", wraps=adapter.scorer.score_snapshot
    ) as neural:
        assert adapter.decide(request) == {
            "candidate_digest": request["candidate_digest"],
            "scores": [0.0, 1.0],
            "selected_index": 1,
        }
        neural.assert_called_once()


def test_terminal_branch_cannot_admit_an_empty_catalog_or_unsupported_whole_decision():
    adapter, request = task_pair(summary_snapshot(count=0))
    with pytest.raises(BoundaryError, match="complete_catalog_required"):
        adapter.decide(request)
    adapter, request = task_pair()
    adapter.manifest["support"]["action_verbs"] = ["play"]
    request["manifest"] = copy.deepcopy(adapter.manifest)
    with patch.object(adapter.scorer, "score_snapshot") as neural:
        with pytest.raises(BoundaryError, match="unsupported_whole_decision"):
            adapter.decide(request)
        neural.assert_not_called()


def test_nonterminal_task_composition_preserves_actual_neural_scores_and_public_texts():
    import torch
    from test_light_action_m0 import _small_bpe
    from tokenizers import Tokenizer

    from stpd.models.stage1a import build_scorer
    from stpd.models.token_core import ScratchShape, ScratchTokenCore
    from stpd.policy.token_decision import LightActionM0DecisionScorer

    # Real production projection, byte codecs, CPU neural scoring and decision
    # reader. Synthetic seeded parameters isolate composition from artifact
    # export/initialization; no training or historical model parity is claimed.
    scorer = object.__new__(LightActionM0DecisionScorer)
    scorer.canonical_input, scorer.public_input = False, True
    scorer.info = {"config": {"public_profile": "public_compact"}}
    scorer.config = SimpleNamespace(device="cpu", max_state_tokens=8192, max_action_bytes=8192)
    scorer.state_tokenizer = Tokenizer.from_str(_small_bpe().decode())
    scorer.model = build_scorer(
        "stage1a.dsimple.light-action.m0.s.v1",
        ScratchTokenCore(ScratchShape(512, 16, 1, 2, 32, 0.0, 8192)),
        max_action_bytes=8192,
        scoring_seed=994,
    ).eval()
    original_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        for observed in (snapshot(), synthetic_noncombat_snapshot("map_navigation")):
            baseline, old_request = task_pair(observed, task=False)
            task, new_request = task_pair(observed)
            baseline.scorer = task.scorer = scorer
            public = project_public_snapshot(observed, compact=True)
            with patch.object(scorer, "score_texts", wraps=scorer.score_texts) as score_texts:
                old, new = baseline.decide(old_request), task.decide(new_request)
            assert old == new
            assert len(old["scores"]) == len(public.actions)
            assert score_texts.call_args_list == [
                ((public.state_text, public.action_texts),),
                ((public.state_text, public.action_texts),),
            ]
    finally:
        torch.set_num_threads(original_threads)


@pytest.fixture
def bindings(tmp_path):
    archive, model, _, _ = synthetic_completion(tmp_path)
    export = tmp_path / "export"
    export_light_action_m0_model(archive, model.artifact_id, export)
    from spireagent.workbench.local_model_registration import _requirements
    from stpd.public_m0_policy_installation import PROFILE

    requirements, support = _requirements(capabilities(), input_profile=PROFILE)
    _validate_requirements(requirements, support)

    def bind(name, *, task=None):
        config, manifest = tmp_path / name / "config.json", tmp_path / name / "manifest.json"
        result = bind_public_m0_export(
            ROOT,
            export,
            config,
            manifest,
            manifest_id=name,
            policy={"id": name, "version": "1.0.0", "provider": "test", "architecture": "M0"},
            requirements=requirements,
            support=support,
            binding_root=tmp_path,
            task=task,
        )
        return config, manifest, result

    return bind


def test_new_task_binding_is_explicit_and_legacy_bytes_stay_unchanged(bindings, tmp_path):
    old_config, old_manifest, (old, baseline) = bindings("baseline")
    before = old_config.read_bytes(), old_manifest.read_bytes()
    config_path, manifest_path, (config, manifest) = bindings(
        "summary", task=ready_summary_task_spec()
    )
    assert old["schema"] == CONFIG_SCHEMA and baseline["adapter"]["version"] == ADAPTER_VERSION
    assert "task_spec" not in old and "task_spec" not in baseline["adapter_config"]["public_m0"]
    assert config["schema"] == TASK_CONFIG_SCHEMA
    assert manifest["adapter"]["version"] == TASK_ADAPTER_VERSION
    assert (
        config["task_spec"]
        == manifest["adapter_config"]["public_m0"]["task_spec"]
        == (ready_summary_task_spec())
    )
    assert config["model_id"] == old["model_id"]
    assert manifest["artifact"] == baseline["artifact"]
    assert validate(ROOT, config_path, manifest_path, binding_root=tmp_path) == (config, manifest)
    assert validate(ROOT, old_config, old_manifest, binding_root=tmp_path) == (old, baseline)
    assert before == (old_config.read_bytes(), old_manifest.read_bytes())


def test_new_task_manifest_and_program_abstention_pass_real_runtime_readers(bindings, tmp_path):
    _, manifest_path, (_, manifest) = bindings("summary", task=ready_summary_task_spec())
    adapter, request = task_pair()
    output = adapter.decide(request)
    decision_path = tmp_path / "decision.json"
    decision_path.write_bytes(json_bytes(output))
    # Actual unchanged Runtime contract readers consume the new producer bytes;
    # this is a protocol seam, not a native lifecycle or controller receipt.
    script = (
        "import {readFile} from 'node:fs/promises';"
        "const runtime=await import(process.argv[1]);"
        "const manifest=runtime.validatePolicyManifest("
        "JSON.parse(await readFile(process.argv[2],'utf8')));"
        "const decision=runtime.validateAdapterDecision("
        "JSON.parse(await readFile(process.argv[3],'utf8')),process.argv[4],2);"
        "console.log(JSON.stringify({adapter:manifest.adapter,"
        "task_spec:manifest.adapter_config.public_m0.task_spec,decision}));"
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
        check=True,
        timeout=10,
    )
    assert json.loads(result.stdout) == {
        "adapter": manifest["adapter"],
        "task_spec": ready_summary_task_spec(),
        "decision": output,
    }


@pytest.mark.parametrize(
    "change",
    [
        lambda c, m: c.update(task_spec=None),
        lambda c, m: c["task_spec"].update(id="future-task"),
        lambda c, m: c["task_spec"].update(added=True),
        lambda c, m: c["task_spec"].update(input_schema="native-logical-v1"),
        lambda c, m: c["task_spec"].update(abstention_scores="neural-scores"),
        lambda c, m: m["adapter"].update(version=ADAPTER_VERSION),
        lambda c, m: m["adapter_config"]["public_m0"].pop("task_spec"),
        lambda c, m: m["adapter_config"]["public_m0"]["task_spec"].update(id="future-task"),
        lambda c, m: c.update(schema=CONFIG_SCHEMA),
    ],
)
def test_task_binding_rejects_cross_version_or_mirrored_descriptor_drift(
    bindings, tmp_path, change
):
    config_path, manifest_path, (config, manifest) = bindings(
        "summary", task=ready_summary_task_spec()
    )
    change(config, manifest)
    config_path.write_bytes(json_bytes(config))
    manifest["adapter_config"]["public_m0"]["config"]["sha256"] = hashlib.sha256(
        config_path.read_bytes(),
    ).hexdigest()
    manifest_path.write_bytes(json_bytes(manifest))
    with pytest.raises(BoundaryError):
        validate(ROOT, config_path, manifest_path, binding_root=tmp_path)


def test_legacy_config_cannot_smuggle_task_spec(bindings, tmp_path):
    config_path, manifest_path, (config, _) = bindings("baseline")
    config["task_spec"] = ready_summary_task_spec()
    config_path.write_bytes(json_bytes(config))
    with pytest.raises(BoundaryError, match="missing_or_unknown_fields"):
        validate(ROOT, config_path, manifest_path, binding_root=tmp_path)


@pytest.mark.parametrize("task", [None, ready_summary_task_spec()])
def test_only_legacy_config_permits_implicit_cpu_runtime_device(bindings, tmp_path, task):
    config_path, manifest_path, (config, manifest) = bindings("binding", task=task)
    config.pop("runtime_device")
    config_path.write_bytes(json_bytes(config))
    manifest["adapter_config"]["public_m0"]["config"]["sha256"] = hashlib.sha256(
        config_path.read_bytes(),
    ).hexdigest()
    manifest_path.write_bytes(json_bytes(manifest))
    if task is None:
        assert validate(ROOT, config_path, manifest_path, binding_root=tmp_path) == (
            config,
            manifest,
        )
    else:
        with pytest.raises(BoundaryError, match="missing_or_unknown_fields"):
            validate(ROOT, config_path, manifest_path, binding_root=tmp_path)


@pytest.mark.parametrize("task", [{}, {**ready_summary_task_spec(), "id": "future-task"}])
def test_binder_rejects_undeclared_task_before_publishing_files(bindings, task):
    with pytest.raises(BoundaryError, match="unsupported_task_spec"):
        bindings("rejected", task=task)
