"""Common app outcomes through production Recorder and the installed typed SDK."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from metadata_import_guard import no_torch_imports as no_torch_imports
from sts2_platform_evidence import verify_agent_run_evidence

from spireagent.workbench.agent_task_outcome import project
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_models import LocalModelService, _terminal_screen
from stpd.canonical import canonical_json

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def produced_public(tmp_path):
    module = Path(
        os.environ.get(
            "STPD_TEST_TASK_OUTCOME_RUNTIME_MODULE",
            ROOT / "components/policy-runtime/dist/index.js",
        )
    )

    def produce(variant="summary"):
        result = subprocess.run(
            [
                "node",
                str(ROOT / "python/tests/agent_task_outcome_producer.mjs"),
                str(module),
                str(ROOT),
                str(tmp_path / variant),
                variant,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return Path(json.loads(result.stdout)["directory"])

    return produce


def original_expected(directory):
    manifest = json.loads((directory / "manifest.json").read_bytes())
    return {
        key: manifest[key]
        for key in (
            "run_id",
            "manifest_id",
            "policy_manifest_sha256",
            "policy_artifact_sha256",
            "runtime_version",
            "runtime_code_sha256",
        )
    }


def test_real_recorder_sdk_app_reports_task_completion_and_observed_loss(produced_public, tmp_path):
    directory = produced_public()
    expected = original_expected(directory)
    verified = verify_agent_run_evidence(directory, expected)
    assert verified.status == "pass", verified.findings
    outcome = project(verified, native=False, text_terminal=_terminal_screen)
    assert outcome["task_completion"]["status"] == "completed"
    assert outcome["operation_end"]["status"] == "task_ended"
    assert outcome["terminal_observation"]["status"] == "observed"
    assert outcome["terminal_observation"]["result"] == "loss"
    assert outcome["terminal_observation"]["ready_summary"] is True
    assert outcome["strategy_quality"] == "not_assessed"
    assert outcome["evidence"]["content_id"] == verified.value.content_id
    assert "path" not in outcome["agent_composition"]["model"]
    assert outcome["task_completion"]["witness"]["decision_event_sequence"] > 0
    service = LocalModelService(ProjectConfig(tmp_path / "app", "", "", None, combination()))
    shutil.copytree(directory, service.directory / "agent-runs" / expected["run_id"])
    service.state.update(startup=expected, selection_id="sealed-selection")
    service._evaluation_handoff()
    report = service.state["evaluation"]
    assert report["schema"] == "stpd/local-runtime-evaluation-handoff-v1"
    assert (
        report["game_outcome"] == "not_measured" and report["scientific_verdict"] == "not_claimed"
    )
    assert report["training_admission"] == "not_claimed"
    assert report["agent_task_outcome"] == outcome
    assert service.status()["evaluation"]["agent_task_outcome"] == outcome
    assert service.catalog()["evaluations"][0]["agent_task_outcome"] == outcome
    first = report["evaluation_id"]
    service._evaluation_handoff()
    assert service.state["evaluation"]["evaluation_id"] == first
    assert len(list((service.directory / "evaluations").glob("*.json"))) == 1
    legacy = {
        key: value
        for key, value in report.items()
        if key not in {"agent_task_outcome", "evaluation_id"}
    }
    legacy["evaluation_id"] = hashlib.sha256(canonical_json(legacy).encode()).hexdigest()
    legacy_path = service.directory / "evaluations" / (legacy["evaluation_id"] + ".json")
    legacy_raw = json.dumps(legacy).encode()
    legacy_path.write_bytes(legacy_raw)
    service._evaluation_handoff()
    assert legacy_path.read_bytes() == legacy_raw
    assert len(service.evaluations()) == 2
    assert {item["evaluation_id"] for item in service.evaluations()} == {
        first,
        legacy["evaluation_id"],
    }


def test_summary_completion_and_later_cleanup_failure_are_independent(produced_public):
    directory = produced_public("cleanup-failure")
    verified = verify_agent_run_evidence(directory, original_expected(directory))
    assert verified.status == "pass", verified.findings
    outcome = project(verified, native=False, text_terminal=_terminal_screen)
    assert outcome["task_completion"]["status"] == "completed"
    assert outcome["terminal_observation"]["result"] == "loss"
    assert outcome["operation_end"]["status"] == "failed"
    assert outcome["strategy_quality"] == "not_assessed"


def test_no_declared_task_does_not_hide_observed_loss_or_prove_completion(produced_public):
    directory = produced_public("no-task")
    verified = verify_agent_run_evidence(directory, original_expected(directory))
    assert verified.status == "pass", verified.findings
    outcome = project(verified, native=False, text_terminal=_terminal_screen)
    assert outcome["task_completion"]["status"] == "unknown"
    assert outcome["terminal_observation"]["result"] == "loss"
    assert outcome["operation_end"]["status"] == "censored"


@pytest.mark.parametrize("variant", ["wrong-task", "ordinary-abstain", "budget"])
def test_wrong_task_and_nonterminal_program_end_never_prove_completion(produced_public, variant):
    directory = produced_public(variant)
    verified = verify_agent_run_evidence(directory, original_expected(directory))
    assert verified.status == "pass", verified.findings
    outcome = project(verified, native=False, text_terminal=_terminal_screen)
    assert outcome["task_completion"]["status"] == (
        "unknown" if variant == "wrong-task" else "unproved"
    )
    assert outcome["operation_end"]["status"] == "censored"
    if variant == "budget":
        assert outcome["operation_end"]["reason_codes"] == ["recorded_budget_end"]


def test_public_m0_ordinary_abstention_has_no_original_completion_witness(produced_public):
    directory = produced_public("public-m0")
    verified = verify_agent_run_evidence(directory, original_expected(directory))
    assert verified.status == "pass", verified.findings
    outcome = project(verified, native=False, text_terminal=_terminal_screen)
    assert outcome["task_completion"]["status"] == "unknown"
    assert outcome["task_completion"]["task_spec"]["schema"] == "stpd/public-m0-task-spec-v1"
    assert outcome["terminal_observation"]["status"] == "unknown"
    assert outcome["operation_end"]["status"] == "censored"


@pytest.mark.parametrize("variant", ["no-input", "conflicting-terminal"])
def test_missing_or_conflicting_originals_remain_unknown(produced_public, variant):
    directory = produced_public(variant)
    verified = verify_agent_run_evidence(directory, original_expected(directory))
    assert verified.status == "pass", verified.findings
    outcome = project(verified, native=False, text_terminal=_terminal_screen)
    assert outcome["task_completion"]["status"] == "unknown"
    assert outcome["terminal_observation"]["status"] == (
        "ambiguous" if variant == "conflicting-terminal" else "unknown"
    )
    assert outcome["terminal_observation"]["result"] is None


def test_http_application_views_carry_the_same_owner_outcome(produced_public, tmp_path):
    import threading
    from http.cookiejar import CookieJar
    from urllib.request import HTTPCookieProcessor, build_opener

    from spireagent.workbench.developer_server import Application, create_server

    directory = produced_public()
    expected = original_expected(directory)
    app = Application(ProjectConfig(tmp_path / "http-app", "", "", None, combination()))
    service = app.models
    shutil.copytree(directory, service.directory / "agent-runs" / expected["run_id"])
    service.state.update(startup=expected, selection_id="sealed-selection")
    service._evaluation_handoff()
    original = service.state["evaluation"]["agent_task_outcome"]
    server = create_server(app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with client.open(base + "/", timeout=5) as response:
            assert response.status == 200
        with client.open(base + "/api/local-models/status", timeout=5) as response:
            assert json.load(response)["evaluation"]["agent_task_outcome"] == original
        with client.open(base + "/api/local-models", timeout=5) as response:
            catalog = json.load(response)
        assert catalog["session"]["evaluation"]["agent_task_outcome"] == original
        assert catalog["evaluations"][0]["agent_task_outcome"] == original
    finally:
        server.shutdown()
        server.server_close()
        app.close()
        thread.join(timeout=3)


def test_invalid_or_changed_original_evidence_cannot_prove_completion(produced_public):
    directory = produced_public()
    expected = original_expected(directory)
    verified = verify_agent_run_evidence(directory, expected)
    (directory / "events.jsonl").write_bytes((directory / "events.jsonl").read_bytes() + b" ")
    outcome = project(verified, native=False, text_terminal=_terminal_screen)
    assert outcome["task_completion"]["status"] == "unknown"
    assert outcome["terminal_observation"]["status"] == "unknown"
    assert outcome["operation_end"]["status"] == "unknown"
    failed = verify_agent_run_evidence(directory, expected)
    assert failed.status == "fail"
    assert project(failed, native=False)["task_completion"]["status"] == "unknown"


def test_projection_limit_is_explicit_unknown_without_partial_completion(
    produced_public, monkeypatch
):
    from spireagent.workbench import agent_task_outcome

    directory = produced_public()
    verified = verify_agent_run_evidence(directory, original_expected(directory))
    assert verified.status == "pass"
    monkeypatch.setattr(agent_task_outcome, "MAX_EVENTS_BYTES", 1)
    outcome = project(verified, native=False, text_terminal=_terminal_screen)
    assert outcome["task_completion"]["status"] == "unknown"
    assert outcome["task_completion"]["reason_codes"] == [
        "projection_file_unavailable_or_over_limit"
    ]
    assert outcome["task_completion"]["witness"] is None


def test_generic_handoff_rejects_event_drift_after_sdk_before_projection(
    produced_public, tmp_path, monkeypatch
):
    import sts2_platform_evidence

    from spireagent.json_boundary import BoundaryError

    directory = produced_public()
    expected = original_expected(directory)
    service = LocalModelService(ProjectConfig(tmp_path / "drift-app", "", "", None, combination()))
    copied = service.directory / "agent-runs" / expected["run_id"]
    shutil.copytree(directory, copied)
    previous = {"prior_immutable_report": True}
    service.state.update(startup=expected, selection_id="sealed-selection", evaluation=previous)
    original_verify = sts2_platform_evidence.verify_agent_run_evidence

    def changed_after_verify(path, startup):
        verified = original_verify(path, startup)
        assert verified.status == "pass"
        with (path / "events.jsonl").open("ab") as output:
            output.write(b' {"foreign_event":true}\n')
        return verified

    monkeypatch.setattr(sts2_platform_evidence, "verify_agent_run_evidence", changed_after_verify)
    with pytest.raises(BoundaryError, match="finalized_stop_evidence_changed"):
        service._evaluation_handoff()
    assert service.state["evaluation"] == previous
    assert service.evaluations() == []


def test_generic_handoff_uses_same_frozen_events_for_dto_and_legacy_counts(
    produced_public, tmp_path, monkeypatch
):
    from collections import Counter

    from spireagent.workbench import agent_task_outcome

    directory = produced_public()
    expected = original_expected(directory)
    original_events = (directory / "events.jsonl").read_bytes()
    expected_counts = dict(
        Counter(json.loads(line)["kind"] for line in original_events.splitlines())
    )
    service = LocalModelService(ProjectConfig(tmp_path / "frozen-app", "", "", None, combination()))
    copied = service.directory / "agent-runs" / expected["run_id"]
    shutil.copytree(directory, copied)
    service.state.update(startup=expected, selection_id="sealed-selection")
    original_project = agent_task_outcome.project

    def changed_after_projection(*args, **kwargs):
        assert kwargs["frozen_events"] == original_events
        outcome = original_project(*args, **kwargs)
        with (copied / "events.jsonl").open("ab") as output:
            output.write(b' {"foreign_event":true}\n')
        return outcome

    monkeypatch.setattr(agent_task_outcome, "project", changed_after_projection)
    service._evaluation_handoff()
    report = service.state["evaluation"]
    assert report["event_counts"] == expected_counts
    assert report["agent_task_outcome"]["task_completion"]["status"] == "completed"
    assert (
        report["agent_task_outcome"]["evidence"]["events"]["sha256"]
        == hashlib.sha256(original_events).hexdigest()
    )


def test_recorded_budget_survives_unavailable_task_manifest(produced_public):
    directory = produced_public("budget")
    verified = verify_agent_run_evidence(directory, original_expected(directory))
    assert verified.status == "pass"
    with (directory / "policy-manifest.json").open("ab") as output:
        output.write(b" ")
    outcome = project(verified, native=False, text_terminal=_terminal_screen)
    assert outcome["task_completion"]["status"] == "unknown"
    assert outcome["terminal_observation"]["status"] == "unknown"
    assert outcome["operation_end"]["status"] == "censored"
    assert outcome["operation_end"]["reason_codes"] == ["recorded_budget_end"]
    assert outcome["operation_end"]["budget"]["exhausted_reason"] == "policy_call_limit"


@pytest.mark.parametrize("variant", ["handoff-terminal", "handoff-cleanup"])
def test_actual_runtime_handoff_reason_survives_reasonless_stop(produced_public, tmp_path, variant):
    directory = produced_public(variant)
    expected = original_expected(directory)
    verified = verify_agent_run_evidence(directory, expected)
    assert verified.status == "pass", verified.findings
    events = [json.loads(line) for line in (directory / "events.jsonl").read_bytes().splitlines()]
    handoff = next(event for event in events if event["kind"] == "handoff_to_human")
    assert handoff["payload"]["reason"] == "auto_surface_not_admitted"
    assert not any(event["kind"] in {"decision", "receipt"} for event in events)
    outcome = project(verified, native=False, text_terminal=_terminal_screen)
    end = outcome["operation_end"]
    assert end["status"] == "censored"
    assert end["recorded_reason"] == "auto_surface_not_admitted"
    assert end["event_sequence"] == handoff["sequence"]
    assert end["controller"] == "released" and end["budget"]["ended_reason"] == "mode_changed"
    assert outcome["task_completion"]["status"] == "unknown"
    assert outcome["terminal_observation"]["status"] == "unknown"
    service = LocalModelService(
        ProjectConfig(tmp_path / "handoff-app", "", "", None, combination())
    )
    shutil.copytree(directory, service.directory / "agent-runs" / expected["run_id"])
    service.state.update(startup=expected, selection_id="sealed-selection")
    service._evaluation_handoff()
    assert service.state["evaluation"]["agent_task_outcome"] == outcome
    assert service.state["evaluation"]["game_outcome"] == "not_measured"


@pytest.mark.parametrize("variant", ["handoff-continued", "handoff-resumed-no-step"])
def test_resumed_auto_work_or_intent_blocks_old_handoff_reason(produced_public, variant):
    directory = produced_public(variant)
    verified = verify_agent_run_evidence(directory, original_expected(directory))
    assert verified.status == "pass", verified.findings
    outcome = project(verified, native=False, text_terminal=_terminal_screen)
    events = [json.loads(line) for line in (directory / "events.jsonl").read_bytes().splitlines()]
    assert len([event for event in events if event["kind"] == "handoff_to_human"]) == 1
    assert any(event["kind"] == "decision" for event in events) == (variant == "handoff-continued")
    assert outcome["operation_end"]["status"] == "censored"
    assert outcome["operation_end"]["recorded_reason"] is None
    assert outcome["operation_end"]["event_sequence"] == events[-1]["sequence"]


def test_two_auto_epochs_only_attach_the_terminal_handoff_reason(produced_public):
    directory = produced_public("handoff-two-epochs")
    verified = verify_agent_run_evidence(directory, original_expected(directory))
    assert verified.status == "pass", verified.findings
    events = [json.loads(line) for line in (directory / "events.jsonl").read_bytes().splitlines()]
    handoffs = [event for event in events if event["kind"] == "handoff_to_human"]
    assert [event["payload"]["reason"] for event in handoffs] == [
        "auto_surface_not_admitted",
        "policy_abstained",
    ]
    outcome = project(verified, native=False, text_terminal=_terminal_screen)
    assert outcome["operation_end"]["recorded_reason"] == "policy_abstained"
    assert outcome["operation_end"]["event_sequence"] == handoffs[-1]["sequence"]
    assert outcome["task_completion"]["status"] == "unknown"


@pytest.mark.parametrize("variant", ["handoff-failed", "handoff-tainted", "handoff-budget"])
def test_handoff_does_not_override_failure_taint_or_budget_precedence(produced_public, variant):
    directory = produced_public(variant)
    verified = verify_agent_run_evidence(directory, original_expected(directory))
    assert verified.status == "pass", verified.findings
    outcome = project(verified, native=False, text_terminal=_terminal_screen)
    end = outcome["operation_end"]
    assert end["recorded_reason"] != "auto_surface_not_admitted"
    if variant == "handoff-budget":
        assert end["status"] == "censored" and end["reason_codes"] == ["recorded_budget_end"]
        assert end["budget"]["exhausted_reason"] == "policy_call_limit"
    else:
        assert end["status"] == "failed"
        assert end["reason_codes"] == [
            "recorded_fail_closed" if variant == "handoff-failed" else "recorded_runtime_taint"
        ]


@pytest.mark.parametrize("variant", ["handoff-substantive", "handoff-unknown"])
def test_substantive_or_unverified_tail_cannot_reuse_handoff_reason(produced_public, variant):
    directory = produced_public(variant)
    verified = verify_agent_run_evidence(directory, original_expected(directory))
    assert verified.status == ("fail" if variant == "handoff-unknown" else "pass")
    outcome = project(verified, native=False, text_terminal=_terminal_screen)
    assert outcome["operation_end"]["recorded_reason"] is None
    assert outcome["operation_end"]["status"] == (
        "unknown" if variant == "handoff-unknown" else "censored"
    )


def test_changed_handoff_events_cannot_supply_original_end_reason(produced_public):
    directory = produced_public("handoff-terminal")
    verified = verify_agent_run_evidence(directory, original_expected(directory))
    assert verified.status == "pass"
    with (directory / "events.jsonl").open("ab") as output:
        output.write(b" ")
    outcome = project(verified, native=False, text_terminal=_terminal_screen)
    assert outcome["operation_end"]["status"] == "unknown"
    assert outcome["operation_end"]["recorded_reason"] is None


def test_stop_reason_and_uncertainty_are_not_overwritten_by_handoff():
    """Pure precedence seam; current typed generic Stop does not carry a reason."""
    from types import SimpleNamespace

    from spireagent.workbench.agent_task_outcome import _empty, _operation_end

    events = [
        {"kind": "handoff_to_human", "sequence": 1, "payload": {"reason": "old_handoff"}},
        {"kind": "stopped", "sequence": 2, "payload": {"reason": "explicit_stop"}},
    ]
    value = SimpleNamespace(manifest={"tainted": False})
    outcome = _empty("pass")
    _operation_end(outcome, value, events)
    assert outcome["operation_end"]["recorded_reason"] == "explicit_stop"
    assert outcome["operation_end"]["event_sequence"] == 2
    events[-1]["payload"] = {"controller": "unknown"}
    _operation_end(outcome, value, events)
    assert outcome["operation_end"]["status"] == "unknown"
    assert outcome["operation_end"]["recorded_reason"] is None


@pytest.fixture
def native_source(tmp_path, monkeypatch):
    """Owning SDK source-shaped sample fixture; no neural/native implementation."""

    monkeypatch.syspath_prepend(str(ROOT / "components/evidence/tests"))
    from test_agent_session_run_evidence import canonical
    from test_sampled_agent_session_run_evidence import SampledAgentSessionEvidenceTests

    from stpd.policy.native_task import ready_summary_task_spec

    holder = SampledAgentSessionEvidenceTests()
    holder.setUp()
    yield holder, canonical, ready_summary_task_spec
    holder.f.tearDown()


def native_summary(holder, canonical, *, result="loss"):
    import copy
    import hashlib

    frame = copy.deepcopy(holder.shared["frames"]["map_a"])
    body = frame["observation"]
    body["interaction"].update(
        kind="game_over",
        stage="summary",
        content_schema="sts2.player-environment/surface/game_over-1",
        content={
            "context": {
                "kind": "game_over",
                "result": result,
                "game_mode": "standard",
                "score": 10,
                "floor_reached": 1,
                "ascension": 0,
            },
            "surface": {
                "kind": "game_over",
                "stage": "summary",
                "can_return": True,
                "can_advance_summary": False,
                "return_destination": "timeline",
            },
        },
    )
    raw = canonical(body)
    frame["observation_utf8"] = raw.decode()
    frame["capture"]["sha256"] = hashlib.sha256(raw).hexdigest()
    frame["capture"]["byte_count"] = len(raw)
    holder.shared["frames"]["map_a"] = frame
    f = holder.f
    f.events = f.events[:1]
    for old_file in f.directory.glob("agent-sample-*"):
        old_file.unlink()
    holder.add_sample("map_a", 1)
    for event in f.events:
        if event["kind"] in {"agent_sample_next_completed", "agent_directive"}:
            event["payload"]["output"]["directive"] = {
                "type": "close",
                "reason": "native_ready_summary_task_complete",
            }
    holder.end()
    f.write()
    return f


def native_package(f, task):
    raw = json.dumps(
        {
            "model_id": f.agent["artifact"]["id"],
            "input_spec": f.agent["input"]["input_spec"],
            "agent_spec": {
                **{key: f.agent["agent"][key] for key in ("id", "version")},
                "task_spec": task,
            },
        }
    ).encode()
    f.agent["artifact"]["sha256"] = hashlib.sha256(raw).hexdigest()
    f.run["agent_artifact_sha256"] = f.agent["artifact"]["sha256"]
    f.startup["agent_artifact_sha256"] = f.agent["artifact"]["sha256"]
    f.write()
    return raw


def test_native_acknowledged_original_summary_requires_declared_task(native_source):
    from sts2_platform_evidence import verify_agent_session_run_evidence

    holder, canonical, task_spec = native_source
    f = native_summary(holder, canonical)
    verified = verify_agent_session_run_evidence(f.directory, f.startup)
    assert verified.status == "pass", verified.findings
    package = native_package(f, task_spec())
    verified = verify_agent_session_run_evidence(f.directory, f.startup)
    assert verified.status == "pass", verified.findings
    outcome = project(verified, native=True, native_package=package)
    assert f.agent["input"]["state_recovery"]["model_bindings"] == []
    assert outcome["agent_composition"]["models"] == [{
        "model_id": f.agent["artifact"]["id"], "weights_sha256": None,
        "binding": "original_native_model_package",
    }]
    assert outcome["task_completion"]["status"] == "completed"
    assert outcome["terminal_observation"]["result"] == "loss"
    assert outcome["operation_end"]["status"] == "task_ended"
    assert outcome["task_completion"]["witness"]["consumption_id"] == "consume-1"
    missing = project(
        verified,
        native=True,
        native_package=None,
        package_reason="original_package_metadata_unavailable",
    )
    assert missing["agent_composition"]["models"] is None
    assert missing["task_completion"]["status"] == "unknown"
    assert missing["terminal_observation"]["result"] == "loss"


@pytest.mark.parametrize("variant", ["wrong-task", "ordinary-close", "tainted-cleanup"])
def test_native_summary_preserves_task_binding_and_independent_cleanup(native_source, variant):
    from sts2_platform_evidence import verify_agent_session_run_evidence

    holder, canonical, task_spec = native_source
    f = native_summary(holder, canonical)
    if variant == "ordinary-close":
        for event in f.events:
            if event["kind"] in {"agent_sample_next_completed", "agent_directive"}:
                event["payload"]["output"]["directive"]["reason"] = "operator_requested_stop"
    elif variant == "tainted-cleanup":
        stopped = f.events.pop()
        f.add("runtime_tainted", reason="release_unknown", retry=False)
        f.events.append(stopped)
        f.run.update(tainted=True, status="tainted")
    package = native_package(f, {"id": "wrong-task"} if variant == "wrong-task" else task_spec())
    verified = verify_agent_session_run_evidence(f.directory, f.startup)
    assert verified.status == "pass", verified.findings
    outcome = project(verified, native=True, native_package=package)
    assert outcome["terminal_observation"]["result"] == "loss"
    assert (
        outcome["task_completion"]["status"]
        == {"wrong-task": "unknown", "ordinary-close": "unproved", "tainted-cleanup": "completed"}[
            variant
        ]
    )
    assert outcome["operation_end"]["status"] == (
        "failed" if variant == "tainted-cleanup" else "censored"
    )
    if variant == "tainted-cleanup":
        assert outcome["operation_end"]["recorded_reason"] == "release_unknown"
        assert outcome["operation_end"]["reason_codes"] == ["recorded_runtime_taint"]


@pytest.mark.parametrize("variant", ["body-change", "package-limit", "events-change"])
def test_native_projection_failure_degrades_only_its_independent_axes(
    native_source, monkeypatch, variant
):
    from sts2_platform_evidence import verify_agent_session_run_evidence

    from spireagent.workbench import agent_task_outcome

    holder, canonical, task_spec = native_source
    f = native_summary(holder, canonical)
    stopped = f.events.pop()
    f.add("runtime_tainted", reason="release_unknown", retry=False)
    f.events.append(stopped)
    f.run.update(tainted=True, status="tainted")
    package = native_package(f, task_spec())
    verified = verify_agent_session_run_evidence(f.directory, f.startup)
    assert verified.status == "pass", verified.findings
    if variant == "body-change":
        metadata = f.event("agent_sample_input_stored")["payload"]["metadata"]
        with (f.directory / metadata["observation"]["path"]).open("ab") as output:
            output.write(b" ")
    elif variant == "package-limit":
        monkeypatch.setattr(agent_task_outcome, "MAX_METADATA_BYTES", len(package) - 1)
    else:
        with (f.directory / "events.jsonl").open("ab") as output:
            output.write(b" ")
    outcome = project(verified, native=True, native_package=package)
    assert outcome["task_completion"]["status"] == "unknown"
    if variant == "package-limit":
        assert outcome["terminal_observation"]["result"] == "loss"
        assert outcome["task_completion"]["reason_codes"] == [
            "original_package_metadata_over_limit"
        ]
    else:
        assert outcome["terminal_observation"]["status"] == "unknown"
    if variant == "events-change":
        assert outcome["operation_end"]["status"] == "unknown"
    else:
        assert outcome["operation_end"]["status"] == "failed"
        assert outcome["operation_end"]["recorded_reason"] == "release_unknown"
        assert outcome["operation_end"]["reason_codes"] == ["recorded_runtime_taint"]


def test_prior_summary_cannot_complete_a_later_nonterminal_close(native_source):
    from sts2_platform_evidence import verify_agent_session_run_evidence

    holder, canonical, task_spec = native_source
    f = native_summary(holder, canonical)
    f.events = [
        event
        for event in f.events
        if event["kind"] not in {"agent_sample_segment_ended", "stopped"}
    ]
    for event in f.events:
        if event["kind"] in {"agent_sample_next_completed", "agent_directive"}:
            event["payload"]["output"]["directive"] = {
                "type": "abstain",
                "reason": "ordinary_policy_abstention",
            }
    holder.add_sample("inspect_b", 2)
    for event in f.events:
        if (
            event["kind"] in {"agent_sample_next_completed", "agent_directive"}
            and event["payload"]["output"]["state_version"] == 2
        ):
            event["payload"]["output"]["directive"] = {
                "type": "close",
                "reason": "native_ready_summary_task_complete",
            }
    holder.end()
    package = native_package(f, task_spec())
    verified = verify_agent_session_run_evidence(f.directory, f.startup)
    assert verified.status == "pass", verified.findings
    outcome = project(verified, native=True, native_package=package)
    assert outcome["terminal_observation"]["result"] == "loss"
    assert outcome["terminal_observation"]["witnesses"][0]["consumption_id"] == "consume-1"
    assert outcome["task_completion"]["status"] == "unproved"
    assert outcome["task_completion"]["witness"] is None
    assert outcome["operation_end"]["status"] == "censored"


def test_native_summary_without_exact_ack_is_rejected_by_installed_sdk(native_source):
    from sts2_platform_evidence import verify_agent_session_run_evidence

    holder, canonical, _ = native_source
    f = native_summary(holder, canonical)
    event = f.event("agent_sample_consume_ack_offered")
    event["payload"]["acknowledgement"]["state_version"] = 2
    f.write()
    verified = verify_agent_session_run_evidence(f.directory, f.startup)
    assert verified.status == "fail"
    assert project(verified, native=True)["task_completion"]["status"] == "unknown"


@pytest.mark.parametrize(
    "variant,result",
    [("await", "loss"), ("abstain", "win"), ("stop", "loss"), ("stop-no-ack", "loss")],
)
def test_acknowledged_native_terminal_is_observed_without_close(native_source, variant, result):
    from sts2_platform_evidence import verify_agent_session_run_evidence

    holder, canonical, task_spec = native_source
    f = native_summary(holder, canonical, result=result)
    if variant.startswith("stop"):
        f.events = [
            event
            for event in f.events
            if event["kind"] not in {"agent_sample_next_completed", "agent_directive"}
        ]
        f.event("stopped")["payload"]["agent_state"] = "uncertain"
        if variant == "stop-no-ack":
            f.events = [
                event for event in f.events if event["kind"] != "agent_sample_consume_ack_offered"
            ]
    else:
        directive = {"type": "abstain", "reason": "ordinary_policy_abstention"}
        if variant == "await":
            directive = {
                "type": "await",
                "after_cursor": f.events[0]["payload"]["subscription"]["starting_cursor"],
                "condition": "any_event",
                "timeout_ms": 250,
            }
        for event in f.events:
            if event["kind"] in {"agent_sample_next_completed", "agent_directive"}:
                event["payload"]["output"]["directive"] = directive
    package = native_package(f, task_spec())
    verified = verify_agent_session_run_evidence(f.directory, f.startup)
    assert verified.status == "pass", verified.findings
    outcome = project(verified, native=True, native_package=package)
    assert outcome["task_completion"]["status"] == "unproved"
    assert outcome["task_completion"]["witness"] is None
    if variant == "stop-no-ack":
        assert outcome["terminal_observation"]["status"] == "unknown"
        assert outcome["terminal_observation"]["witnesses"] == []
    else:
        assert outcome["terminal_observation"]["status"] == "observed"
        assert outcome["terminal_observation"]["result"] == result
        assert outcome["terminal_observation"]["ready_summary"] is True
        witness = outcome["terminal_observation"]["witnesses"][0]
        assert witness["consume_event_sequence"] < witness["acknowledgement_event_sequence"]
        assert "directive_event_sequence" not in witness
    assert outcome["operation_end"]["status"] == (
        "unknown" if variant.startswith("stop") else "censored"
    )


def test_full_reference_capture_pointer_cannot_become_terminal_body(native_source):
    from sts2_platform_evidence import verify_agent_session_run_evidence
    from test_agent_session_run_evidence import NativeAgentSessionEvidenceTests

    holder, _, task_spec = native_source
    f = NativeAgentSessionEvidenceTests()
    f.setUp()
    try:
        verified = verify_agent_session_run_evidence(f.directory, f.startup)
        assert verified.status == "pass"
        outcome = project(verified, native=True, native_package=None)
        assert outcome["task_completion"]["status"] == "unknown"
        assert outcome["terminal_observation"]["status"] == "unknown"
        assert outcome["task_completion"]["reason_codes"] == [
            "original_observation_body_not_recorded"
        ]
    finally:
        f.tearDown()


def test_native_application_resolves_only_exact_original_composition(
    native_source, tmp_path, monkeypatch
):
    from sts2_platform_evidence import verify_agent_session_run_evidence

    from spireagent.workbench.native_agent_support import (
        CONFIG_SCHEMA,
        PUBLICATION_PROFILE_ID,
        PUBLICATION_PROFILE_SHA256,
    )

    holder, canonical, task_spec = native_source
    f = native_summary(holder, canonical)
    service = LocalModelService(ProjectConfig(tmp_path / "native-app", "", "", None, combination()))
    package_dir = service.config.state_dir / "model-exports" / "fixture"
    package_dir.mkdir(parents=True)
    f.agent["artifact"].update(id="9" * 64, path=str((package_dir / "model.json").resolve()))
    package = {
        "model_id": "9" * 64,
        "input_spec": f.agent["input"]["input_spec"],
        "agent_spec": {
            **{key: f.agent["agent"][key] for key in ("id", "version")},
            "task_spec": task_spec(),
        },
        "weights": {"sha256": "a" * 64},
    }
    raw = json.dumps(package, sort_keys=True).encode()
    (package_dir / "model.json").write_bytes(raw)
    f.agent["artifact"]["sha256"] = hashlib.sha256(raw).hexdigest()
    f.run["agent_artifact_sha256"] = f.agent["artifact"]["sha256"]
    f.startup["agent_artifact_sha256"] = f.agent["artifact"]["sha256"]
    f.write()
    verified = verify_agent_session_run_evidence(f.directory, f.startup)
    assert verified.status == "pass", verified.findings
    registration = service.private_root / "model-registrations" / "fixture"
    registration.mkdir(parents=True)
    config = {
        "schema": CONFIG_SCHEMA,
        "export_path": str(package_dir.resolve()),
        "artifact_id": "8" * 64,
        "package_model_id": "9" * 64,
        "package_manifest_sha256": f.agent["artifact"]["sha256"],
        "weights_sha256": "a" * 64,
        "input_spec": package["input_spec"],
        "agent_spec": package["agent_spec"],
        "state_format_version": f.agent["input"]["state_format_version"],
        "code_identity": {},
        "publication_profile": {
            "id": PUBLICATION_PROFILE_ID,
            "definition_sha256": PUBLICATION_PROFILE_SHA256,
        },
    }
    config_path = registration / "config.json"
    manifest_path = registration / "manifest.json"
    config_path.write_text(json.dumps(config))
    manifest_path.write_text(json.dumps(f.agent))
    entry = {
        "id": f.agent["manifest_id"],
        "config": config_path.relative_to(service.private_root).as_posix(),
        "manifest": manifest_path.relative_to(service.private_root).as_posix(),
    }
    service._private_ids = {entry["id"]}
    monkeypatch.setattr(
        service, "selection", lambda identity: entry if identity == entry["id"] else None
    )
    bound, reason = service._original_native_composition(verified)
    assert bound == raw and reason is None
    copied = service.directory / "agent-runs" / f.run["run_id"]
    shutil.copytree(f.directory, copied)
    service.state.update(startup=f.startup, selection_id=entry["id"])
    service._evaluation_handoff()
    report = service.state["evaluation"]
    outcome = report["agent_task_outcome"]
    assert report["schema"] == "stpd/local-native-agent-operation-handoff-v1"
    assert report["game_outcome"] == "not_measured" and report["human_origin"] == "not_claimed"
    assert (
        outcome["task_completion"]["status"] == "completed"
        and outcome["terminal_observation"]["result"] == "loss"
    )
    assert str(package_dir) not in json.dumps(outcome)
    original_id = report["evaluation_id"]
    config["agent_spec"] = {**config["agent_spec"], "task_spec": {"id": "different-mutable-task"}}
    config_path.write_text(json.dumps(config))
    service._evaluation_handoff()
    new = service.state["evaluation"]
    assert new["agent_task_outcome"]["task_completion"]["status"] == "unknown"
    assert new["agent_task_outcome"]["terminal_observation"]["result"] == "loss"
    assert new["evaluation_id"] != original_id
    assert len(list((service.directory / "evaluations").glob("*.json"))) == 2


@pytest.mark.parametrize("result", ["win", "loss"])
@pytest.mark.parametrize("variant", ["matching", "opposite", "prefix", "ordinary", "missing-ACK"])
def test_teacher_requires_original_program_exact_ack_and_matching_close(
    native_source, result, variant
):
    from sts2_platform_evidence import verify_agent_session_run_evidence

    from stpd.policy.native_teacher_agent import descriptor

    holder, canonical, _ = native_source
    teacher = descriptor()
    f = holder.f
    f.agent["adapter"] = teacher["adapter"]
    f.agent["agent"].update({key: teacher["agent_spec"][key] for key in ("id", "version")})
    f.agent["input"]["input_spec"] = teacher["input_spec"]
    f.run.update(agent_id=teacher["agent_spec"]["id"],
                 agent_version=teacher["agent_spec"]["version"])
    f.startup["adapter"] = teacher["adapter"]
    f = native_summary(holder, canonical, result=result)
    reason = "natural_ready_summary_" + result
    if variant == "opposite":
        reason = "natural_ready_summary_" + ("loss" if result == "win" else "win")
    elif variant == "prefix":
        reason += "_forged"
    elif variant == "ordinary":
        reason = "operator_requested_stop"
    for event in f.events:
        if event["kind"] in {"agent_sample_next_completed", "agent_directive"}:
            event["payload"]["output"]["directive"]["reason"] = reason
    if variant == "missing-ACK":
        f.events = [e for e in f.events if e["kind"] != "agent_sample_consume_ack_offered"]
    raw = json.dumps(teacher).encode()
    f.agent["artifact"]["sha256"] = hashlib.sha256(raw).hexdigest()
    f.run["agent_artifact_sha256"] = f.agent["artifact"]["sha256"]
    f.startup["agent_artifact_sha256"] = f.agent["artifact"]["sha256"]
    f.write()
    verified = verify_agent_session_run_evidence(f.directory, f.startup)
    if variant == "missing-ACK":
        assert verified.status == "fail"
        assert [finding.code for finding in verified.findings] == ["native_session_sample_next_ack"]
        failed = project(verified, native=True, native_package=raw)
        assert failed["task_completion"]["status"] == "unknown"
        return
    assert verified.status == "pass", verified.findings
    outcome = project(verified, native=True, native_package=raw)
    assert outcome["agent_composition"]["models"] == []
    assert outcome["agent_composition"]["model"] is None
    assert outcome["agent_composition"]["program"] == teacher["agent_spec"]
    assert outcome["task_completion"]["status"] == (
        "completed" if variant == "matching" else "unproved"
    )
    if variant == "matching":
        witness = outcome["task_completion"]["witness"]
        assert (witness["consume_event_sequence"] < witness["acknowledgement_event_sequence"]
                < witness["directive_event_sequence"])
        assert outcome["terminal_observation"]["result"] == result
        missing_request = project(verified, native=True, native_package=raw,
                                  application_request_status="unknown")
        assert missing_request["task_completion"]["status"] == "unknown"
        assert missing_request["terminal_observation"]["result"] == result
        assert missing_request["operation_end"]["status"] == "censored"
