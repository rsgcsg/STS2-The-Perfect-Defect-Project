"""Closed historical proof checks without importing a numerical backend."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from spireagent.artifact_contracts import Producer
from spireagent.json_boundary import BoundaryError
from stpd import native_code_scope as scope
from stpd.canonical import semantic_hash

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/native_compatibility/evidence_rc27_basis.json"


@pytest.fixture
def proof():
    return json.loads(FIXTURE.read_bytes())


def recorded_rc29_target(proof):
    # Reconstruct the exact archived two-record migration, then check its
    # independently recorded complete hash. The live lock now targets rc30.
    target = proof["original_lock"].replace(
        'name = "rsgcsg-sts2-platform-evidence"\nversion = "0.1.0rc27"',
        'name = "rsgcsg-sts2-platform-evidence"\nversion = "0.1.0rc29"',
    ).replace(
        "cd319ba9146eddb39bb09439fc9b2e366061360e",
        "ba05adce73d067f4fb400fb2ac3af6a2b2279a80",
    )
    assert hashlib.sha256(target.encode()).hexdigest() == scope.TARGET_LOCK_SHA256
    return target


def test_fixed_original_inventory_and_complete_evidence_lock_delta(proof):
    assert [row["path"] for row in proof["source_files"]] == list(scope.LEGACY_PATHS)
    assert len(proof["source_files"]) == 39
    assert semantic_hash(proof["source_files"]) == scope.ORIGINAL_SOURCE_SHA256
    # Latest executable closure deliberately changed its operational decoder;
    # the archived old proof cannot waive those new source bytes.
    with pytest.raises(BoundaryError, match="inference_source_changed"):
        scope.checked_native_compatibility_proof(proof)
    assert scope.compatibility_original_identity()["scope"] == scope.LEGACY_GRAPH_SCOPE
    scope._evidence_only_lock_delta(proof["original_lock"], recorded_rc29_target(proof))


@pytest.mark.parametrize(
    "mutation",
    [
        "profile",
        "producer",
        "reorder",
        "extra",
        "duplicate",
        "owner_hash",
        "old_lock",
        "proof_extra",
    ],
)
def test_untrusted_or_incomplete_recorded_basis_is_rejected(proof, mutation):
    value = copy.deepcopy(proof)
    if mutation == "profile":
        value["profile"] += "-unknown"
    elif mutation == "producer":
        value["original_export_producer"]["source_revision"] = "a" * 40
    elif mutation == "reorder":
        value["source_files"].reverse()
    elif mutation == "extra":
        value["source_files"].append(value["source_files"][0])
    elif mutation == "duplicate":
        value["source_files"][1] = value["source_files"][0]
    elif mutation == "owner_hash":
        next(row for row in value["source_files"] if row["path"] == "stpd/native_code_scope.py")[
            "sha256"
        ] = "b" * 64
    elif mutation == "old_lock":
        value["original_lock"] += "\n# forged lock\n"
    else:
        value["ignored_source_paths"] = []
    with pytest.raises(BoundaryError):
        scope.checked_native_compatibility_proof(value)


@pytest.mark.parametrize(
    "changed_path",
    [
        "stpd/models/structured_m2.py",
        "stpd/policy/native_agent.py",
        "stpd/fullrun/native_structured_inputs.py",
        "uv.lock",
    ],
)
def test_changed_current_math_agent_input_or_lock_is_rejected(
    proof, tmp_path, monkeypatch, changed_path
):
    for path in (*scope.PATHS, "uv.lock"):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / path).read_bytes())
    target = tmp_path / changed_path
    target.write_bytes(target.read_bytes() + b"\n# changed current source\n")
    monkeypatch.setattr(scope, "ROOT", tmp_path)
    with pytest.raises(BoundaryError):
        scope.checked_native_compatibility_proof(proof)


@pytest.mark.parametrize(
    "replacement",
    [
        ('name = "safetensors"\nversion = "0.8.0"', 'name = "safetensors"\nversion = "0.8.1"'),
        ('name = "torch"\nversion = "2.13.0"', 'name = "torch"\nversion = "2.13.1"'),
        ("resolution-markers = [", "unreviewed-resolution-markers = ["),
        (
            'name = "rsgcsg-sts2-platform-evidence"\nversion = "0.1.0rc29"',
            'name = "rsgcsg-sts2-platform-evidence"\nversion = "0.1.0rc30"',
        ),
    ],
)
def test_structural_delta_rejects_even_if_a_caller_rehashes_the_changed_lock(proof, replacement):
    target = recorded_rc29_target(proof)
    before, after = replacement
    assert before in target
    changed = target.replace(before, after, 1)
    with pytest.raises(BoundaryError, match="not_exact_evidence_only_lock_delta"):
        scope._evidence_only_lock_delta(proof["original_lock"], changed)


def test_missing_original_git_is_explicit_and_cannot_fall_back_to_current(proof, monkeypatch):
    calls = []

    def missing(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(128, command)

    monkeypatch.setattr(scope.subprocess, "run", missing)
    origin = Producer.decode(proof["original_export_producer"])
    with pytest.raises(BoundaryError, match="original_git_source_unavailable"):
        scope.native_compatibility_proof(origin)
    assert len(calls) == 1
    assert calls[0][-1] == scope.ORIGINAL_EXPORT_REVISION + ":python/" + scope.PATHS[0]


def test_schema_discovery_and_proof_validation_do_not_import_torch():
    code = (
        "import sys; from stpd.native_code_scope import is_native_model_schema; "
        "assert is_native_model_schema('stpd/native-compatible-model-v1'); "
        "assert 'torch' not in sys.modules; assert 'safetensors' not in sys.modules"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
