"""Pure post-return diagnostic tests: no Application, Runtime, or network imports."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest
import reference_suite_diagnostics as diagnostic


@pytest.fixture
def returned_report(tmp_path):
    def original(label, value):
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        name = f"{len(list(tmp_path.glob('*.json'))) + 1:04d}-{label}.json"
        (tmp_path / name).write_bytes(raw)
        return {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}

    request = {
        "schema": "spireagent/agent-run-request-v1",
        "operation": "prepare-and-takeover",
        "run_profile": "short",
        "target": "native",
        "selection_id": "fixture-reference-teacher",
        "composition_id": "c" * 64,
        "operation_id": "d" * 32,
        "budget": {"limits": {"max_submissions": 16, "max_policy_calls": 32, "deadline_ms": 60000}},
        "admission_context": {
            "native_episode": {
                "environment_ref": "e" * 32,
                "binding": {"generation_id": "f" * 32, "application_instance_id": "a" * 32},
            }
        },
        "control_token": "DO_NOT_PRINT_TOKEN",
        "config": {"secret": "DO_NOT_PRINT_CONFIG"},
    }
    finish = {
        "phase": "closed",
        "finish_complete": True,
        "environment_ref": "e" * 32,
        "actor_finish": {
            "status": "stopped",
            "run_id": "run-original",
            "seal": "available",
            "evaluation_reference": {"sha256": "1" * 64, "owner": "LocalModelService"},
        },
        "cleanup": {
            "native_exit_disposition": "graceful",
            "native_shutdown_ack": "runtime_bound_acknowledged",
            "driver_exit_code": 0,
            "forced_fallback": False,
            "cleanup_error": None,
        },
        "headers": {"Authorization": "DO_NOT_PRINT_HEADER"},
    }
    evaluation = {
        "schema": "stpd/local-native-agent-operation-handoff-v1",
        "run_id": "run-original",
        "evaluation_id": "2" * 64,
        "evidence_content_id": "3" * 64,
        "evidence_verification": "pass",
        "controller_at_stop": "released",
        "tainted": False,
        "pending_request": None,
        "agent_execution": {"status": "bound"},
        "agent_task_outcome": {
            "task_completion": {"status": "unknown"},
            "terminal_observation": {"status": "observed", "result": "loss"},
            "operation_end": {"status": "censored", "controller": "released", "tainted": False},
        },
        "raw_world": "DO_NOT_PRINT_WORLD",
    }
    row = {
        "ordinal": 1,
        "status": "unknown",
        "reason": "owner_finalized",
        "intent_id": "b" * 32,
        "environment_ref": "e" * 32,
        "generation_id": "f" * 32,
        "run_id": "run-original",
        "operation_id": "d" * 32,
        "actor_started": True,
        "world_mutation_offered": True,
        "cleanup_accepted": False,
        "cleanup_error": "exact_evaluation_or_request_missing",
        "request": original("generated-request", request),
        "original_request": original("original-agent-request", request),
        "finish": original("finish", finish),
        "evaluation": original("original-evaluation", evaluation),
        "last_status": {"status": "stopped", "runtime": {"control_token": "DO_NOT_PRINT_STATE"}},
    }
    row["artifacts"] = [copy.deepcopy(row["request"])]
    report = {
        "schema": "spireagent/reference-suite-report-v1",
        "status": "stopped",
        "stop_reason": "owner_finalized",
        "attempt_id": "attempt-original",
        "planned": 20,
        "world_offers": 1,
        "actor_started": 1,
        "max_episodes": 2,
        "limits": {
            "preparation_seconds": 15,
            "episode_seconds": 20,
            "cleanup_seconds": 15,
            "suite_seconds": 90,
            "poll_seconds": 0.05,
            "http_seconds": 10,
        },
        "episodes": [row]
        + [
            {"ordinal": i, "status": "not_started", "reason": "owner_finalized"}
            for i in range(2, 21)
        ],
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    return json.loads(path.read_text())


def test_whole_returned_report_files_preserve_denominator_errors_and_typed_facts(
    returned_report,
    tmp_path,
    request,
):
    parent = copy.deepcopy(returned_report)
    message = diagnostic.retain_report_diagnostic(
        returned_report, tmp_path, request.node.user_properties
    )
    value = json.loads(message)
    assert len(value["episodes"]) == value["episode_count"]["value"] == 20
    assert value["top"]["stop_reason"]["value"] == "owner_finalized"
    assert (
        value["episodes"][0]["fields"]["cleanup_error"]["value"]
        == "exact_evaluation_or_request_missing"
    )
    facts = value["episodes"][0]["originals"]
    assert facts["request"]["file"]["availability"] == "verified"
    assert facts["request"]["facts"]["limits"]["deadline_ms"]["value"] == 60000
    assert facts["finish"]["facts"]["cleanup"]["driver_exit_code"]["value"] == 0
    assert facts["finish"]["facts"]["cleanup"]["cleanup_error"] == {"availability": "null"}
    assert facts["evaluation"]["facts"]["request_outcome_counts"]["unknown"] == {
        "availability": "missing"
    }
    assert value["episodes"][1]["fields"]["cleanup_accepted"] == {"availability": "missing"}
    assert (
        facts["request"]["facts"]["episode"]["environment_ref"]
        == value["episodes"][0]["identity"]["environment_ref"]
    )
    assert "DO_NOT_PRINT" not in message
    assert len(message.encode()) <= diagnostic.MAX_DIAGNOSTIC_BYTES
    assert returned_report == parent
    assert ("r0_suite_diagnostic", message) in request.node.user_properties


def test_missing_malformed_and_hash_mismatch_are_availability_not_success(
    returned_report, tmp_path
):
    row = returned_report["episodes"][0]
    del row["finish"]["path"]
    row["evaluation"]["sha256"] = "0" * 64
    row["request"]["path"] = "../runtime.json"
    row["last_status"] = []
    value = json.loads(diagnostic.report_diagnostic(returned_report, tmp_path))
    facts = value["episodes"][0]["originals"]
    assert facts["finish"]["file"]["availability"] == "missing"
    assert facts["evaluation"]["file"]["availability"] == "hash_or_length_mismatch"
    assert facts["request"]["file"]["availability"] == "unsafe_path"
    assert value["episodes"][0]["status_availability"]["status"] == {"availability": "missing"}
    assert json.loads(diagnostic.report_diagnostic({"episodes": "bad"}, tmp_path))[
        "episode_count"
    ] == {"availability": "missing"}


def test_actor_binding_observations_preserve_distinct_identities_without_private_fields(
    returned_report, tmp_path,
):
    raw = json.dumps({
        "read_order": "application_status_then_native_resource",
        "native_resource": {"phase": "actor-associated", "environment_ref": "e" * 32,
                            "actor": {"run_id": "foreign-run", "token": "DO_NOT_PRINT_TOKEN"}},
        "application_status": {"startup": {"run_id": "original-run"},
                               "config": "DO_NOT_PRINT_CONFIG"},
    }).encode()
    name = "0020-actor-binding-observations.json"
    (tmp_path / name).write_bytes(raw)
    returned_report["episodes"][0]["actor_binding_observations"] = {
        "path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
    }
    message = diagnostic.report_diagnostic(returned_report, tmp_path)
    fact = json.loads(message)["episodes"][0]["originals"]["actor_binding_observations"]
    assert fact["file"]["availability"] == "verified"
    assert fact["facts"]["resource_actor_run"] != fact["facts"]["application_run"]
    assert fact["facts"]["resource_actor_run"]["sha256"] == (
        hashlib.sha256(b"foreign-run").hexdigest())
    assert "DO_NOT_PRINT" not in message and "foreign-run" not in message


def test_diagnostic_fault_does_not_mask_parent_assertion_or_reason(
    returned_report, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        diagnostic,
        "report_diagnostic",
        lambda *_: (_ for _ in ()).throw(ValueError("DO_NOT_PRINT_EXCEPTION")),
    )
    message = diagnostic.retain_report_diagnostic(returned_report, tmp_path, None)
    assert "DO_NOT_PRINT_EXCEPTION" not in message
    assert json.loads(message)["stop_reason"]["value"] == "owner_finalized"
    with pytest.raises(AssertionError, match="owner_finalized"):
        assert returned_report["status"] == "completed", message
    assert returned_report["stop_reason"] == "owner_finalized"


def test_projection_size_is_bounded_and_marks_detail_omission(returned_report, tmp_path):
    row = returned_report["episodes"][0]
    returned_report["episodes"] = [copy.deepcopy(row) for _ in range(20)]
    message = diagnostic.report_diagnostic(returned_report, tmp_path)
    value = json.loads(message)
    assert len(message.encode()) <= diagnostic.MAX_DIAGNOSTIC_BYTES
    assert value["original_detail_omitted_for_size"] is True
    assert len(value["episodes"]) == 20
    assert value["top"]["stop_reason"]["value"] == "owner_finalized"


def test_native_call_arguments_remain_exact_and_diagnostics_follow_return():
    source = Path(__file__).with_name("test_reference_suite_application.py").read_text()
    tree = ast.parse(source)
    cases = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    assert len(cases) == 2
    for case in cases:
        run = next(
            n
            for n in ast.walk(case)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "run_suite"
        )
        limits = next(k.value for k in run.keywords if k.arg == "limits")
        assert [ast.literal_eval(n) for n in limits.args] == [15, 20, 15, 90, 0.05, 10]
        capture = next(
            n
            for n in ast.walk(case)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "retain_report_diagnostic"
        )
        assert capture.lineno > run.lineno
        strict = [
            n
            for n in ast.walk(case)
            if isinstance(n, ast.Assert)
            and isinstance(n.test, ast.Compare)
            and ast.unparse(n.test.left) == "report['status']"
        ]
        assert len(strict) == 1 and strict[0].lineno > capture.lineno
        assert ast.literal_eval(strict[0].test.comparators[0]) == (
            "completed" if "two_serial" in case.name else "stopped"
        )


def test_failing_pytest_retains_string_and_junit_property(returned_report, tmp_path):
    import subprocess
    import sys
    import xml.etree.ElementTree as ET

    helper_dir = str(Path(diagnostic.__file__).parent)
    child = tmp_path / "test_failure_capture.py"
    child.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {helper_dir!r})\n"
        "from reference_suite_diagnostics import retain_report_diagnostic\n"
        "def test_returned_failure(request):\n"
        "    root = Path(__file__).parent\n"
        "    report = json.loads((root / 'report.json').read_text())\n"
        "    message = retain_report_diagnostic(report, root, request.node.user_properties)\n"
        "    assert report['status'] == 'completed', message\n"
    )
    xml = tmp_path / "failure.xml"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(child), "-q", f"--junitxml={xml}"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    tree = ET.parse(xml)
    failure = next(tree.iter("failure"))
    message = failure.attrib["message"]
    assert "owner_finalized" in message and '"ordinal"' in message
    assert "DO_NOT_PRINT" not in message
    prop = next(p for p in tree.iter("property") if p.attrib["name"] == "r0_suite_diagnostic")
    value = json.loads(prop.attrib["value"])
    assert len(value["episodes"]) == 20
    assert value["top"]["stop_reason"]["value"] == "owner_finalized"
    assert len(prop.attrib["value"].encode()) <= diagnostic.MAX_DIAGNOSTIC_BYTES
