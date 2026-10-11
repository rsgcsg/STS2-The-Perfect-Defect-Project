"""Pure policy3/closure falsifiers; no numerical model or original weights."""

from __future__ import annotations

import copy
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from spireagent.json_boundary import BoundaryError
from stpd import native_code_scope as scope
from stpd.native_graph_spec import NativeGraphControl
from stpd.native_sampled_carry_spec import INPUT_SPEC, sampled_agent_execution_policy, sampled_agent_spec
from stpd.policy import native_execution_policy as policy
from stpd.policy import native_teacher_agent as teacher
from stpd.policy.native_operational_outcome import owned_current_known_stale_policy
from stpd.policy.native_operational_outcome_v2 import progress_aware_strict_policy

ROOT = Path(__file__).resolve().parents[1]
SHARED = json.loads((ROOT.parent / "components/policy-runtime/contracts/fixtures/native-known-stale-window-v3.json").read_bytes())
OLD = json.loads((ROOT / "tests/fixtures/native_compatibility/evidence_rc27_basis.json").read_bytes())
OWNED = json.loads((ROOT.parent / "components/policy-runtime/contracts/fixtures/owned-current-known-stale-v1.json").read_bytes())


def operational_proof():
    return {**copy.deepcopy(OLD), "profile": scope.OPERATIONAL_COMPATIBILITY_PROFILE,
            "target_scope": scope.GRAPH_SCOPE,
            "target_lock_sha256": scope.OPERATIONAL_TARGET_LOCK_SHA256,
            "target_evidence": {"version": scope.OPERATIONAL_EVIDENCE_VERSION,
                                "source_revision": scope.OPERATIONAL_EVIDENCE_REVISION},
            "changed_paths": ["stpd/native_code_scope.py", "stpd/native_sampled_carry_spec.py",
                              "stpd/policy/native_agent.py", "stpd/policy/native_structured_export.py"],
            "new_paths": ["stpd/policy/native_execution_policy.py"]}


def test_shared_closed_declarations_and_new_versioned_specs_keep_numeric_input():
    selected = policy.known_stale_window_policy()
    assert selected == SHARED["execution_policy"]
    for declared in (selected, owned_current_known_stale_policy(), progress_aware_strict_policy()):
        assert policy.checked_execution_policy(declared) == declared
    control = NativeGraphControl()
    new = sampled_agent_spec(control, execution_policy=selected)
    assert new["version"] == "1.3.0"
    assert new["I"] is new["F"] is False
    assert sampled_agent_execution_policy(new, control) == selected
    old = sampled_agent_spec(control, execution_policy=owned_current_known_stale_policy())
    assert old["version"] == "1.2.0"
    assert INPUT_SPEC["version"] == "1.0.0"
    descriptor = teacher.descriptor(execution_policy=selected)
    assert descriptor["agent_spec"]["version"] == "1.8.0"
    assert descriptor["agent_spec"]["teacher"]["version"] == "1.0.12"
    assert descriptor["input_spec"] == teacher.descriptor()["input_spec"]
    assert "stpd/policy/native_execution_policy.py" in teacher.CODE_FILES
    assert set(scope.PATHS) - set(scope.LEGACY_PATHS) == {"stpd/policy/native_execution_policy.py"}


@pytest.mark.parametrize("mutation", ["schema", "bounds", "boolean", "counter", "missing", "extra"])
def test_policy3_rejects_undeclared_variants(mutation):
    selected = policy.known_stale_window_policy()
    if mutation == "schema": selected["schema"] += "-unknown"
    elif mutation == "bounds": selected["known_stale_recovery_window_ms"] = 9999
    elif mutation == "boolean": selected["known_stale_recovery_window_ms"] = True
    elif mutation == "counter": selected["max_known_stale_rejections"] = 8
    elif mutation == "missing": selected.pop("known_stale_recovery_window_ms")
    else: selected["retry"] = "automatic"
    with pytest.raises(BoundaryError): policy.checked_execution_policy(selected)


def test_manifest_next_and_original_outcome_grammar_without_published_sentinel():
    manifest = copy.deepcopy(OWNED["manifest"])
    selected = policy.known_stale_window_policy(); manifest["execution_policy"] = selected
    before = copy.deepcopy(manifest)
    assert policy.manifest_execution_policy(manifest) == selected
    assert manifest == before
    value = copy.deepcopy(OWNED["opted_next_after_stale"])
    intention = {key: value["operational_outcome"][key] for key in (
        "basis_acquisition_id", "action_id", "consumption_id", "state_version")}
    intention["snapshot_id"] = value["operational_outcome"]["result"]["snapshot_id"]
    intention["runtime_instance_id"] = value["operational_outcome"]["result"]["attribution"]["runtime_instance_id"]
    checked, outcome = policy.checked_next_input(value, execution_policy=selected, intention=intention)
    assert outcome == value["operational_outcome"]
    assert "operational_outcome" not in checked
    assert manifest == before
    bad = copy.deepcopy(value); bad["operational_outcome"]["result"]["delivery"] = "unknown"
    with pytest.raises(BoundaryError):
        policy.checked_next_input(bad, execution_policy=selected, intention=intention)


def test_actual_learned_composition_guard_requires_new_immutable_package_selection(tmp_path):
    # Extract only the production admission predicate before manifest creation.
    # Package decoding/scorer are explicit mocked seams: no weights or inference.
    tree = ast.parse((ROOT / "stpd/policy/native_agent.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "bind_native_agent")
    boundary = next(i for i, n in enumerate(function.body) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "manifest" for t in n.targets))
    function.body = function.body[:boundary]
    control = NativeGraphControl(); selected = policy.known_stale_window_policy()
    metadata = {"input_spec": INPUT_SPEC, "agent_spec": sampled_agent_spec(control, execution_policy=selected)}
    namespace = {"Any": Any, "Path": Path, "BoundaryError": BoundaryError,
                 "load_native_package": lambda _: (metadata, SimpleNamespace(model_control=control)),
                 "SAMPLED_INPUT_SPEC": INPUT_SPEC, "checked_execution_policy": policy.checked_execution_policy,
                 "sampled_agent_execution_policy": sampled_agent_execution_policy}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "actual-learned-composition-guard", "exec"), namespace)
    kwargs = dict(manifest_id="fixture", requirements={}, support={}, required_seams=[], execution_policy=selected)
    namespace["bind_native_agent"](tmp_path / "no-weights", tmp_path / "new-manifest", **kwargs)
    metadata["agent_spec"] = sampled_agent_spec(control, execution_policy=owned_current_known_stale_policy())
    with pytest.raises(BoundaryError, match="explicit_package_execution_policy_binding"):
        namespace["bind_native_agent"](tmp_path / "no-weights", tmp_path / "new-manifest", **kwargs)
    assert not (tmp_path / "new-manifest").exists()


def test_new_operational_proof_is_separate_and_old_latest_checker_remains_strict():
    proof = operational_proof()
    assert scope.checked_native_operational_compatibility_proof(proof) == proof
    assert proof["source_files"] == OLD["source_files"]
    with pytest.raises(BoundaryError, match="inference_source_changed"):
        scope.checked_native_compatibility_proof(OLD)
    with pytest.raises(BoundaryError): scope.checked_native_compatibility_proof(proof)
    with pytest.raises(BoundaryError): scope.checked_native_operational_compatibility_proof(OLD)


@pytest.mark.parametrize("field", ["changed_paths", "new_paths", "target_scope", "source_files", "original_lock", "target_lock_sha256", "target_evidence"])
def test_caller_cannot_widen_operational_waiver_or_original_basis(field):
    proof = operational_proof()
    if field == "changed_paths": proof[field].append("stpd/models/structured_m2.py")
    elif field == "new_paths": proof[field].append("stpd/models/hidden_model.py")
    elif field == "target_scope": proof[field] = scope.LEGACY_GRAPH_SCOPE
    elif field == "source_files": proof[field][0]["sha256"] = "a" * 64
    elif field == "target_lock_sha256": proof[field] = "a" * 64
    elif field == "target_evidence": proof[field]["version"] = "0.1.0rc99"
    else: proof[field] += "\n# caller changed lock"
    with pytest.raises(BoundaryError): scope.checked_native_operational_compatibility_proof(proof)


@pytest.mark.parametrize("changed_path", ["stpd/models/structured_m2.py", "stpd/models/native_structured_scorer.py",
                                        "stpd/fullrun/native_structured_inputs.py", "stpd/policy/native_operational_outcome.py"])
def test_even_rehashed_current_model_feature_or_original_outcome_changes_are_rejected(tmp_path, monkeypatch, changed_path):
    for path in (*scope.PATHS, "uv.lock"):
        target = tmp_path / path; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / path).read_bytes())
    target = tmp_path / changed_path; target.write_bytes(target.read_bytes() + b"\n# forbidden delta\n")
    monkeypatch.setattr(scope, "ROOT", tmp_path)
    with pytest.raises(BoundaryError, match="inference_source_changed"):
        scope.checked_native_operational_compatibility_proof(operational_proof())


@pytest.mark.parametrize("replacement", [
    ('name = "torch"\nversion = "2.13.0"', 'name = "torch"\nversion = "2.13.1"'),
    ('name = "rsgcsg-sts2-platform-evidence"\nversion = "0.1.0rc29"', 'name = "rsgcsg-sts2-platform-evidence"\nversion = "0.1.0rc99"'),
])
def test_separate_operational_lock_validator_rejects_rehashed_dependency_or_unselected_evidence(replacement):
    before, after = replacement
    target = (ROOT / "uv.lock").read_text(); assert before in target
    with pytest.raises(BoundaryError, match="not_exact_operational_evidence_only_lock_delta"):
        scope._operational_evidence_only_lock_delta(OLD["original_lock"], target.replace(before, after, 1))
