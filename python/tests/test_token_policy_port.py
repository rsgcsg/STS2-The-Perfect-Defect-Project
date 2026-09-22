"""Decision-only token port regressions; no native execution or quality claim."""

import copy
import hashlib
import io
import json

import pytest
from test_public_inputs import snapshot

from spireagent.encoding import canonical_json
from spireagent.json_boundary import BoundaryError
from spireagent.package_identity import file_sha256
from spireagent.policies import policy_support
from stpd import token_policy_installation as installation
from stpd.fullrun.public_inputs import project_public_snapshot
from stpd.policy.token_port import PORT_SCHEMA, TokenPolicyAdapter, serve


def metadata(tmp_path, monkeypatch):
    export = tmp_path / "export"
    export.mkdir()
    (export / "model.json").write_text(json.dumps({"model_id": "a" * 64}))
    config = {
        "schema": installation.CONFIG_SCHEMA,
        "export_path": str(export),
        "export_manifest_sha256": file_sha256(export / "model.json"),
        "model_id": "a" * 64,
        "qwen_snapshot": None,
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    monkeypatch.setattr(installation, "code_digest", lambda _: "b" * 64)
    manifest = {
        "schema": "sts2.policy-runtime/policy-manifest-1",
        "adapter": {
            "id": "stpd-token-decision-adapter",
            "version": "1.0.0",
            "protocol": installation.PROTOCOL,
            "code_sha256": "b" * 64,
        },
        "artifact": {
            "id": "a" * 64,
            "path": "export/model.json",
            "sha256": config["export_manifest_sha256"],
        },
        "adapter_config": {
            "stage1a": {
                "code_digest_scope": installation.CODE_SCOPE,
                "config": {
                    "path": "config.json",
                    "sha256": file_sha256(config_path),
                    "schema": installation.CONFIG_SCHEMA,
                },
            }
        },
        "requirements": {"reads": []},
        "claims": {
            "full_run": False,
            "selector": False,
            "catalog_filtered": False,
            "creates_action_authority": False,
            "creates_native_operands": False,
        },
        "support": {
            "interaction_kinds": [snapshot()["interaction"]["kind"]],
            "action_verbs": [a["verb"] for a in snapshot()["bound_actions"]["actions"]],
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return config_path, manifest_path, manifest


def test_trusted_metadata_checks_exact_export_and_code(tmp_path, monkeypatch):
    config, manifest, value = metadata(tmp_path, monkeypatch)
    installation.validate(tmp_path, config, manifest)
    assert policy_support("token-v1") is installation
    assert installation.arguments({"config": "config.json", "manifest": "manifest.json"})[:2] == [
        "-m",
        "stpd.policy.token_port",
    ]
    value["adapter"]["code_sha256"] = "c" * 64
    manifest.write_text(json.dumps(value))
    with pytest.raises(BoundaryError, match="identity_drift"):
        installation.validate(tmp_path, config, manifest)
    value["adapter"]["code_sha256"] = "b" * 64
    manifest.write_text(json.dumps(value))
    (tmp_path / "export/model.json").write_text("{}")
    with pytest.raises(BoundaryError, match="export_identity"):
        installation.validate(tmp_path, config, manifest)


class FakeScorer:
    def score_snapshot(self, value):
        public = project_public_snapshot(value)
        return {a.key: float(i) for i, a in enumerate(public.actions)}


@pytest.fixture
def pair(tmp_path, monkeypatch):
    _, _, manifest = metadata(tmp_path, monkeypatch)
    adapter = object.__new__(TokenPolicyAdapter)
    adapter.manifest, adapter.scorer, adapter.closed = manifest, FakeScorer(), False
    value = snapshot()
    keys = [a["bound_action_id"] for a in value["bound_actions"]["actions"]]
    request = {
        "run_id": "run-1",
        "manifest": copy.deepcopy(manifest),
        "bundle": {"observation": value, "reads": []},
        "candidate_count": len(keys),
        "candidate_digest": hashlib.sha256(canonical_json(keys).encode()).hexdigest(),
    }
    return adapter, request


def test_unchanged_catalog_scores_protocol_and_close(pair):
    adapter, request = pair
    result = adapter.decide(request)
    assert result == {
        "candidate_digest": request["candidate_digest"],
        "scores": [0.0, 1.0],
        "selected_index": 1,
    }
    line = {"schema": PORT_SCHEMA, "message_type": "decide", "request_id": "r1", "input": request}
    out = io.StringIO()
    assert serve(adapter, io.StringIO(json.dumps(line) + "\n"), out) == 0
    ready, decision = map(json.loads, out.getvalue().splitlines())
    assert ready["message_type"] == "ready"
    assert decision["request_id"] == "r1" and decision["output"] == result
    with pytest.raises(BoundaryError, match="closed"):
        adapter.decide(request)


@pytest.mark.parametrize(
    "change",
    [
        lambda r: r.update(candidate_count=True),
        lambda r: r.update(candidate_digest="0" * 64),
        lambda r: r["manifest"].update(manifest_id="other"),
        lambda r: r["bundle"].update(reads=[{}]),
        lambda r: r["bundle"]["observation"]["bound_actions"]["actions"].reverse(),
        lambda r: r["bundle"]["observation"]["bound_actions"].update(total_count=3),
        lambda r: r["bundle"]["observation"]["interaction"].update(kind="not_supported"),
        lambda r: r["bundle"]["observation"]["bound_actions"]["actions"][0].update(
            verb="native_mutation"
        ),
        lambda r: r["bundle"]["observation"]["information_policy"].update(
            includes_hidden_information=True
        ),
    ],
)
def test_rejects_whole_request_without_filtering(pair, change):
    adapter, request = pair
    change(request)
    with pytest.raises(BoundaryError):
        adapter.decide(request)


@pytest.mark.parametrize("scores", [{"wrong": 1.0}, {"first": float("nan")}])
def test_rejects_changed_or_nonfinite_model_outputs(pair, monkeypatch, scores):
    adapter, request = pair
    monkeypatch.setattr(adapter.scorer, "score_snapshot", lambda _: scores)
    with pytest.raises(BoundaryError, match="score_binding"):
        adapter.decide(request)


def test_protocol_error_never_returns_an_action(pair):
    adapter, _ = pair
    output = io.StringIO()
    serve(adapter, io.StringIO('{"arbitrary":"command"}\nnot-json\n'), output)
    messages = list(map(json.loads, output.getvalue().splitlines()))
    assert [m["message_type"] for m in messages] == ["ready", "error", "error"]
    assert all("output" not in m for m in messages)


def test_code_digest_tracks_source_but_not_docs(tmp_path):
    for name in (
        "stpd/policy/token_port.py",
        "spireagent/__init__.py",
        "uv.lock",
        "configs/v0/qwen/qwen3-0.6b-base-l2.json",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original")
    before = installation.code_digest(tmp_path)
    (tmp_path / "note.md").write_text("unrelated doc")
    assert installation.code_digest(tmp_path) == before
    (tmp_path / "stpd/policy/token_port.py").write_text("changed")
    assert installation.code_digest(tmp_path) != before


def test_workbench_token_inspection_import_does_not_require_ml_packages():
    import subprocess
    import sys

    program = """
import sys
class RejectML:
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'torch', 'transformers'}:
            raise AssertionError('Workbench imported ML: ' + fullname)
sys.meta_path.insert(0, RejectML())
from spireagent.policies import policy_support
assert policy_support('token-v1').CONFIG_SCHEMA == 'stpd/token-policy-config-v1'
"""
    subprocess.run([sys.executable, '-c', program], check=True)
