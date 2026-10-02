from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from stpd.canonical import canonical_json
from stpd.contracts import QwenIdentity
from stpd.environment import ResearchProjectorV0
from stpd.policy import (
    DEFAULT_MANIFEST,
    PolicyAdapter,
    PolicyAdapterError,
    ResidentS1Model,
    adapter_code_sha256,
    serve_ndjson,
)
from stpd.policy.adapter import ADAPTER_CODE_DIGEST_SCOPE, ADAPTER_SOURCE_CLOSURE
from stpd.representation import InputProfile, ModelSerializerV1
from stpd.training.checkpoint import CheckpointIdentity, TrainerState


def _snapshot() -> dict[str, Any]:
    return {
        "snapshot_id": "policy-fixture-snapshot",
        "status": "interactive",
        "persistent": {
            "content": {
                "run": {"ascension": 0},
                "player": {"character_definition_id": "DEFECT"},
            }
        },
        "interaction": {
            "kind": "combat_turn",
            "content": {
                "context": {"kind": "combat", "turn_owner": "player", "is_play_phase": True}
            },
        },
        "referents": [
            {
                "referent_id": "card-runtime",
                "role": "hand_card",
                "kind": "entity",
                "state": {"visible": True},
                "properties": {
                    "definition_id": "DEFEND_DEFECT",
                    "cost": "1",
                    "description": "Gain 5 Block.",
                },
            }
        ],
        "bound_actions": {
            "status": "complete",
            "materialized_count": 2,
            "total_count": 2,
            "actions": [
                {
                    "bound_action_id": "candidate-play",
                    "verb": "play",
                    "subject_referent_id": "card-runtime",
                    "arguments": [],
                },
                {
                    "bound_action_id": "candidate-end",
                    "verb": "end_turn",
                    "arguments": [],
                },
            ],
        },
        "completeness": {"status": "complete"},
        "information_policy": {"id": "player_visible_v1"},
    }


class _FakeScorer:
    training = False

    def score(self, state_text: str, action_texts: tuple[str, ...]) -> list[float]:
        assert state_text
        assert len(action_texts) == 2
        return [0.25, 0.75]


def _fake_model() -> ResidentS1Model:
    qwen = QwenIdentity(
        "fixture-qwen",
        "fixture-revision",
        "fixture-revision",
        "float32",
        "cpu",
        True,
        control="fixture",
    )
    identity = CheckpointIdentity(
        "fixture-source",
        "fixture-data",
        "scheme1-linear-pretrained",
        "fixture-config",
        "stpd-model-serialization-v1",
        InputProfile.STANDARD.value,
        qwen,
    )
    return ResidentS1Model(
        _FakeScorer(),
        ResearchProjectorV0(),
        ModelSerializerV1(InputProfile.STANDARD),
        identity,
        TrainerState(1, 1, 0, 1),
        "f" * 64,
        0.0,
    )


def _adapter_fixture(
    tmp_path: Path,
    model: ResidentS1Model | None = None,
    manifest_mutator: Any | None = None,
) -> PolicyAdapter:
    config_path = tmp_path / "s1-config.json"
    checkpoint_path = tmp_path / "fixture.pt"
    config = {
        "schema": "stpd/s1-human-combat-live-config-v1",
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": "f" * 64,
        "model_read_policy": {
            "mode": "none",
            "training_basis": "human_annotator_importer_empty_reads",
            "standard_reads_line_expected": False,
        },
        "live_identity": {
            "protocol_version": "1.0.0",
            "host_kind": "test",
            "connector_version": "fixture-connector",
            "connector_source_revision": "fixture-source",
            "connector_artifact_sha256": "b" * 64,
            "connector_artifact_mvid": "fixture-mvid",
            "game_version": "fixture-game",
            "game_commit": "fixture-commit",
            "modset_status": "fixture-exact",
            "modset_fingerprint": "fixture-modset",
            "loaded_mod_ids": ["FIXTURE_MOD"],
        },
        "admission": {
            "character_definition_id": "DEFECT",
            "ascension": 0,
            "context_kind": "combat",
            "allowed_connector_verbs": ["play", "end_turn"],
            "allowed_research_action_kinds": ["play_card", "end_turn"],
        },
    }
    config_path.write_text(canonical_json(config) + "\n", encoding="utf-8")
    manifest = {
        "schema": "sts2.policy-runtime/policy-manifest-1",
        "manifest_id": "fixture-manifest",
        "policy": {
            "id": "fixture-policy",
            "version": "1",
            "provider": "stpd",
            "architecture": "fixture",
        },
        "adapter": {
            "id": "stpd-fixture",
            "version": "1",
            "protocol": "sts2.policy-runtime/decision-only-ndjson-1",
            "code_sha256": adapter_code_sha256(),
        },
        "artifact": {
            "id": "fixture-checkpoint",
            "path": str(checkpoint_path),
            "sha256": "f" * 64,
        },
        "representation": {
            "id": InputProfile.STANDARD.value,
            "version": "stpd-model-serialization-v1",
            "input_schema": "sts2.player-environment/snapshot-1",
        },
        "requirements": {
            "connector_protocol_version": "1.0.0",
            "environment": {
                "host_kind": "test",
                "connector_version": "fixture-connector",
                "connector_source_revision": "fixture-source",
                "connector_artifact_sha256": "b" * 64,
                "connector_module_version_id": "fixture-mvid",
                "modset_status": "fixture-exact",
                "modset_fingerprint": "fixture-modset",
                "loaded_mod_ids": ["FIXTURE_MOD"],
            },
            "reads": [],
            "whole_decision_admission": True,
            "candidate_order_digest": "sha256-json-bound-action-id-order",
            "score_count_matches_candidate_count": True,
            "selected_index": True,
            "successor_required": True,
        },
        "support": {
            "game_versions": ["fixture-game"],
            "game_commits": ["fixture-commit"],
            "interaction_kinds": ["combat_turn"],
            "action_verbs": ["play", "end_turn"],
        },
        "adapter_config": {
            "s1": {
                "code_digest_scope": ADAPTER_CODE_DIGEST_SCOPE,
                "config": {
                    "path": config_path.as_posix(),
                    "sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
                    "schema": config["schema"],
                },
                "qwen": {
                    "model_id": "fixture-qwen",
                    "model_revision": "fixture-revision",
                    "control": "fixture",
                },
                "serializer": {
                    "version": "stpd-model-serialization-v1",
                    "input_profile": InputProfile.STANDARD.value,
                },
                "admission": {
                    "character_definition_id": "DEFECT",
                    "ascension": 0,
                    "context_kind": "combat",
                    "research_action_kinds": ["play_card", "end_turn"],
                },
            }
        },
        "claims": {
            "full_run": False,
            "selector": False,
            "catalog_filtered": False,
            "creates_action_authority": False,
            "creates_native_operands": False,
        },
    }
    if manifest_mutator is not None:
        manifest_mutator(manifest)
    resident = model or _fake_model()
    return PolicyAdapter(
        config_path=config_path,
        manifest_path=tmp_path / "manifest.json",
        model_loader=lambda _: (resident, config),
        manifest=manifest,
    )


def _decision_request(snapshot: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    identities = [action["bound_action_id"] for action in snapshot["bound_actions"]["actions"]]
    return {
        "run_id": "run-fixture-1",
        "manifest": manifest,
        "bundle": {"observation": snapshot, "reads": []},
        "candidate_count": len(identities),
        "candidate_digest": hashlib.sha256(canonical_json(identities).encode("utf-8")).hexdigest(),
    }


def _request_for(adapter: PolicyAdapter, snapshot: dict[str, Any]) -> dict[str, Any]:
    return _decision_request(snapshot, dict(adapter.manifest))


def test_policy_adapter_parity_with_resident_project_and_score(tmp_path: Path) -> None:
    model = _fake_model()
    snapshot = _snapshot()
    direct_decision, direct_actions, direct_scores, _ = model.project_and_score(
        snapshot,
        {},
        game_version="fixture-game",
        game_commit="fixture-commit",
    )
    adapter = _adapter_fixture(tmp_path, model)
    adapter.initialize()
    request = _request_for(adapter, snapshot)
    result = adapter.decide(request)

    assert result["scores"] == direct_scores
    assert result["selected_index"] == max(range(len(direct_scores)), key=direct_scores.__getitem__)
    assert len(direct_decision.actions) == 2
    assert len(direct_actions) == 2
    assert result["candidate_digest"] == request["candidate_digest"]
    assert set(result) == {"candidate_digest", "scores", "selected_index"}


def test_checked_in_policy_manifest_pins_current_source_and_frozen_config() -> None:
    manifest = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    config_pin = manifest["adapter_config"]["s1"]["config"]
    config_path = Path(config_pin["path"])
    if not config_path.is_absolute():
        config_path = DEFAULT_MANIFEST.parents[1] / config_path
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert manifest["adapter"]["code_sha256"] == adapter_code_sha256()
    assert set(manifest["adapter"]) == {"id", "version", "protocol", "code_sha256"}
    assert manifest["adapter_config"]["s1"]["code_digest_scope"] == ADAPTER_CODE_DIGEST_SCOPE
    assert "stpd/data/training_handoff.py" not in ADAPTER_SOURCE_CLOSURE
    assert config_pin["sha256"] == hashlib.sha256(config_path.read_bytes()).hexdigest()
    assert manifest["artifact"]["sha256"] == config["checkpoint_sha256"]
    assert manifest["support"]["game_versions"] == [config["live_identity"]["game_version"]]
    assert manifest["support"]["game_commits"] == [config["live_identity"]["game_commit"]]
    assert (
        manifest["requirements"]["environment"]["modset_fingerprint"]
        == config["live_identity"]["modset_fingerprint"]
    )


def test_policy_digest_scope_matches_fresh_runtime_import_closure() -> None:
    root = DEFAULT_MANIFEST.parents[1]
    script = """
import json
import sys
from pathlib import Path
import stpd.policy.adapter
root = Path.cwd().resolve()
paths = sorted({
    Path(module.__file__).resolve().relative_to(root).as_posix()
    for name, module in sys.modules.items()
    if (name in ('stpd', 'spireagent') or name.startswith(('stpd.', 'spireagent.')))
    and getattr(module, '__file__', None)
    and str(Path(module.__file__).resolve()).startswith(str(root))
})
print(json.dumps(paths))
"""
    loaded = json.loads(
        subprocess.check_output([sys.executable, "-c", script], cwd=root, text=True)
    )
    assert set(loaded) == set(ADAPTER_SOURCE_CLOSURE) - {"tools/policy_adapter.py"}


def test_decision_import_is_isolated_from_host_collection_but_public_exports_remain() -> None:
    script = """
import sys
import stpd.policy.adapter
for name in ('stpd.environment.runtime_collection', 'stpd.training_smoke',
             'stpd.host_runtime_client', 'stpd.game_seed', 'sts2_headless'):
    assert name not in sys.modules, name
import stpd.environment
assert not hasattr(stpd.environment, 'unknown_collection_export')
from stpd.environment import (
    RuntimeCollection, collect_managed_runtime, token_profile_records,
)
from stpd.environment import runtime_collection
assert RuntimeCollection is runtime_collection.RuntimeCollection
assert collect_managed_runtime is runtime_collection.collect_managed_runtime
assert token_profile_records is runtime_collection.token_profile_records
assert 'sts2_headless' not in sys.modules
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=DEFAULT_MANIFEST.parents[1],
        check=True,
        capture_output=True,
        text=True,
    )


def test_v8_preserves_trained_policy_and_versions_adapter_for_public_contract_closure() -> None:
    root = DEFAULT_MANIFEST.parents[1]
    current = json.loads(DEFAULT_MANIFEST.read_text())
    old = json.loads((root / "policy-manifests/s1-policy-adapter-v7.json").read_text())
    assert DEFAULT_MANIFEST.name == "s1-policy-adapter-v8.json"
    assert current["adapter"]["version"] != old["adapter"]["version"]
    assert current["manifest_id"] != old["manifest_id"]
    assert current["adapter"]["code_sha256"] == adapter_code_sha256()
    assert "code_digest_scope" not in current["adapter"]
    assert current["adapter_config"]["s1"]["code_digest_scope"] == ADAPTER_CODE_DIGEST_SCOPE
    for field in ("policy", "artifact", "representation", "requirements", "support", "claims"):
        assert current[field] == old[field]
    for field in ("config", "qwen", "serializer", "admission"):
        assert current["adapter_config"]["s1"][field] == old["adapter_config"]["s1"][field]


def test_installed_public_package_accepts_manifest_ready_and_evidence(tmp_path: Path) -> None:
    """Optional exact-package gate: real public decoder/port/verifier, synthetic CPU model only."""
    from spireagent.workbench.developer import ProjectConfig, combination
    from spireagent.workbench.local_models import LocalModelService
    from spireagent.workbench.runtime_install import validate_runtime_install

    location = os.environ.get("STPD_TEST_POLICY_RUNTIME_NODE_MODULES")
    if not location:
        pytest.skip("install exact optional Runtime and set STPD_TEST_POLICY_RUNTIME_NODE_MODULES")
    node_modules = Path(location).resolve()
    root = DEFAULT_MANIFEST.parents[1]
    registry = json.loads((root / "configs/developer/local-policies-v1.json").read_text())
    connector = next(
        p for p in combination()["node_packages"] if p["package"] == "@rsgcsg/sts2-connector-client"
    )
    observed = validate_runtime_install(node_modules, registry["runtime_package"], connector)
    adapter = _adapter_fixture(tmp_path)
    output = io.StringIO()
    serve_ndjson(adapter, input_lines=iter(()), output=output)
    manifest_path = tmp_path / "fixture-policy.json"
    manifest_path.write_text(canonical_json(adapter.manifest))
    ready_path = tmp_path / "ready.ndjson"
    ready_path.write_text(output.getvalue())
    script = """
import assert from 'node:assert/strict';
import {mkdir,readFile} from 'node:fs/promises';
const [moduleUri,manifestPath,readyPath,evidenceRoot,currentPath,version,code]
  =process.argv.slice(1);
const {validatePolicyManifest,NdjsonPolicyPort,AgentRunEvidence}=await import(moduleUri);
validatePolicyManifest(JSON.parse(await readFile(currentPath,'utf8')));
const manifest=validatePolicyManifest(JSON.parse(await readFile(manifestPath,'utf8')));
assert.throws(()=>validatePolicyManifest({...manifest,adapter:{...manifest.adapter,
  code_digest_scope:'runtime-import-closure-v1'}}));
const replay="const fs=require('node:fs');"
  +"process.stdout.write(fs.readFileSync(process.argv[1]));process.stdin.resume();";
const port=NdjsonPolicyPort.spawn(process.execPath,['-e',replay,readyPath]);
let actual;
try {actual=await port.attest(manifest.adapter,3000);} finally {port.close();}
await mkdir(evidenceRoot,{recursive:true});
const evidence=await AgentRunEvidence.create({root:evidenceRoot,
  runId:'run-synthetic-public-port',policyManifest:manifest,runtimeVersion:version,
  runtimeCodeSha256:code,mode:'human'});
await evidence.attestAdapter(actual);
await evidence.append('stopped',{});
await evidence.finalize({status:'stopped',tainted:false,mode:'human'});
"""
    service = LocalModelService(ProjectConfig(tmp_path / "state", "", "", None, combination()))
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            script,
            (node_modules / "@rsgcsg/sts2-policy-runtime/dist/index.js").as_uri(),
            str(manifest_path),
            str(ready_path),
            str(service.directory / "agent-runs"),
            str(DEFAULT_MANIFEST),
            observed["version"],
            observed["code_sha256"],
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    startup = json.loads(
        (service.directory / "agent-runs/run-synthetic-public-port/manifest.json").read_text()
    )
    service.state.update(selection_id="synthetic-cpu-fixture", startup=startup)
    service._evaluation_handoff()
    report = service.evaluations()[0]
    assert report["evidence_verification"] == "pass", report
    assert report["event_counts"] == {"stopped": 1}
    assert report["game_outcome"] == "not_measured"
    assert report["training_admission"] == "not_claimed"


def test_unified_platform_v3_binding_preserves_the_frozen_s1_policy() -> None:
    root = DEFAULT_MANIFEST.parents[1]
    v2 = json.loads(
        (root / "policy-manifests/s1-policy-adapter-v2.json").read_text(encoding="utf-8")
    )
    v3_path = root / "policy-manifests" / "s1-policy-adapter-v3.json"
    v3 = json.loads(v3_path.read_text(encoding="utf-8"))
    config_pin = v3["adapter_config"]["s1"]["config"]
    config_path = root / config_pin["path"]
    config = json.loads(config_path.read_text(encoding="utf-8"))

    assert config_pin["sha256"] == hashlib.sha256(config_path.read_bytes()).hexdigest()
    assert (
        v3["adapter"]["code_sha256"]
        == "6472f5b7bcfa004dbba607adf6e2f74689740293c940de5e799d39e6cbf9c54b"
    )
    assert v3["artifact"] == v2["artifact"]
    assert v3["representation"] == v2["representation"]
    assert v3["adapter_config"]["s1"]["qwen"] == v2["adapter_config"]["s1"]["qwen"]
    assert v3["adapter_config"]["s1"]["serializer"] == v2["adapter_config"]["s1"]["serializer"]
    assert v3["requirements"]["reads"] == []
    assert v3["requirements"]["whole_decision_admission"] is True
    assert v3["support"]["action_verbs"] == ["play", "end_turn"]
    assert v3["claims"]["catalog_filtered"] is False
    assert config["checkpoint_sha256"] == (
        "c70c482ca1af52c9dc5477a45623f7ad531222400ba6eefd3c17c87b7cc922d3"
    )
    assert config["model_read_policy"]["mode"] == "none"
    assert config["admission"]["catalog_policy"] == "whole_complete_catalog_or_human"

    environment = v3["requirements"]["environment"]
    assert environment == {
        "host_kind": "live_ui",
        "connector_version": "1.2.0-rc.6",
        "connector_source_revision": "4de52cfd72c6bf5b0d2312538152e81c616dabfb",
        "connector_artifact_sha256": (
            "ef673b76469f3b7442a88e7c038a43dc23de1b0cfe1908a96d878b0cc18b2897"
        ),
        "connector_module_version_id": "6f4e58b7-f55e-46d2-8d4f-d1d37f29fd99",
        "modset_status": "exact_platform_modset",
        "modset_fingerprint": ("5a21659597de401d2ce34bc3205be3d535f92aba74538548a6e6145376af8149"),
        "loaded_mod_ids": ["STS2_PLATFORM"],
    }
    assert v2["requirements"]["environment"]["loaded_mod_ids"] == [
        "STS2_HUMAN_ANNOTATOR",
        "STS2_MCP",
        "STS2_PLATFORM_LIVE_UI",
    ]
    assert v2["requirements"]["environment"] != environment


def test_legacy_live_s1_runner_has_no_action_capable_command() -> None:
    result = subprocess.run(
        [sys.executable, "tools/live_s1.py", "run"],
        cwd=DEFAULT_MANIFEST.parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda manifest: manifest["adapter"].__setitem__(
                "code_digest_scope", ADAPTER_CODE_DIGEST_SCOPE
            ),
            "public adapter identity fields drift",
        ),
        (
            lambda manifest: manifest["adapter_config"]["s1"].__setitem__(
                "code_digest_scope", "entrypoint-only"
            ),
            "code digest scope drift",
        ),
        (
            lambda manifest: manifest["requirements"]["environment"].__setitem__(
                "connector_artifact_sha256", "c" * 64
            ),
            "connector_artifact_sha256 differs",
        ),
        (
            lambda manifest: manifest["requirements"]["environment"].__setitem__(
                "connector_module_version_id", "different-mvid"
            ),
            "connector_module_version_id differs",
        ),
        (
            lambda manifest: manifest["requirements"]["environment"].__setitem__(
                "modset_fingerprint", "different-fingerprint"
            ),
            "modset_fingerprint differs",
        ),
        (
            lambda manifest: manifest["requirements"]["environment"].__setitem__(
                "loaded_mod_ids", ["PREDECESSOR_MOD"]
            ),
            "loaded Mod IDs differ",
        ),
        (
            lambda manifest: manifest["support"].__setitem__("game_commits", ["different-commit"]),
            "game commit differs",
        ),
        (
            lambda manifest: manifest["artifact"].__setitem__("sha256", "d" * 64),
            "checkpoint differs",
        ),
        (
            lambda manifest: manifest["adapter_config"]["s1"]["admission"].__setitem__(
                "ascension", 1
            ),
            "S1 admission differs",
        ),
    ],
)
def test_policy_adapter_rejects_manifest_config_drift(
    tmp_path: Path, mutate: Any, message: str
) -> None:
    adapter = _adapter_fixture(tmp_path, manifest_mutator=mutate)
    with pytest.raises(PolicyAdapterError, match=message):
        adapter.initialize()


def test_policy_adapter_rejects_incomplete_catalog(tmp_path: Path) -> None:
    snapshot = _snapshot()
    snapshot["bound_actions"]["total_count"] = 3
    adapter = _adapter_fixture(tmp_path)
    adapter.initialize()
    with pytest.raises(PolicyAdapterError, match="candidate catalog is not complete"):
        adapter.decide(_request_for(adapter, snapshot))


def test_policy_adapter_rejects_model_candidate_count_drift(tmp_path: Path) -> None:
    class _DriftingModel:
        def __init__(self) -> None:
            self._base = _fake_model()
            self.checkpoint_sha256 = self._base.checkpoint_sha256
            self.identity = self._base.identity
            self.serializer = self._base.serializer

        def project_and_score(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> tuple[Any, list[str], list[float], float]:
            decision, actions, _, latency = self._base.project_and_score(*args, **kwargs)
            return decision, actions[:1], [0.5], latency

    adapter = _adapter_fixture(tmp_path, _DriftingModel())  # type: ignore[arg-type]
    adapter.initialize()
    with pytest.raises(PolicyAdapterError, match="changed the complete candidate count"):
        adapter.decide(_request_for(adapter, _snapshot()))


def test_policy_adapter_rejects_projector_candidate_reordering(tmp_path: Path) -> None:
    class _ReorderingModel:
        def __init__(self) -> None:
            self._base = _fake_model()
            self.checkpoint_sha256 = self._base.checkpoint_sha256
            self.identity = self._base.identity
            self.serializer = self._base.serializer

        def project_and_score(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> tuple[Any, list[str], list[float], float]:
            decision, actions, scores, latency = self._base.project_and_score(*args, **kwargs)
            reordered = decision.__class__(
                decision.state,
                decision.actions,
                tuple(reversed(decision.envelopes)),
            )
            return reordered, actions, scores, latency

    adapter = _adapter_fixture(tmp_path, _ReorderingModel())  # type: ignore[arg-type]
    adapter.initialize()
    with pytest.raises(PolicyAdapterError, match="reordered the Connector candidate catalog"):
        adapter.decide(_request_for(adapter, _snapshot()))


def test_policy_adapter_rejects_semantic_action_envelope_reordering(tmp_path: Path) -> None:
    class _SemanticReorderingModel:
        def __init__(self) -> None:
            self._base = _fake_model()
            self.checkpoint_sha256 = self._base.checkpoint_sha256
            self.identity = self._base.identity
            self.serializer = self._base.serializer

        def project_and_score(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> tuple[Any, list[str], list[float], float]:
            decision, action_texts, scores, latency = self._base.project_and_score(*args, **kwargs)
            reordered = decision.__class__(
                decision.state,
                tuple(reversed(decision.actions)),
                decision.envelopes,
            )
            return reordered, action_texts, scores, latency

    adapter = _adapter_fixture(tmp_path, _SemanticReorderingModel())  # type: ignore[arg-type]
    adapter.initialize()
    with pytest.raises(PolicyAdapterError, match="semantic actions and execution envelopes"):
        adapter.decide(_request_for(adapter, _snapshot()))


def test_policy_adapter_rejects_serialized_action_reordering(tmp_path: Path) -> None:
    class _TextReorderingModel:
        def __init__(self) -> None:
            self._base = _fake_model()
            self.checkpoint_sha256 = self._base.checkpoint_sha256
            self.identity = self._base.identity
            self.serializer = self._base.serializer

        def project_and_score(
            self,
            *args: Any,
            **kwargs: Any,
        ) -> tuple[Any, list[str], list[float], float]:
            decision, action_texts, scores, latency = self._base.project_and_score(*args, **kwargs)
            return decision, list(reversed(action_texts)), scores, latency

    adapter = _adapter_fixture(tmp_path, _TextReorderingModel())  # type: ignore[arg-type]
    adapter.initialize()
    with pytest.raises(PolicyAdapterError, match="semantic actions and serialized scores"):
        adapter.decide(_request_for(adapter, _snapshot()))


def test_policy_adapter_rejects_runtime_candidate_order_drift(tmp_path: Path) -> None:
    snapshot = _snapshot()
    adapter = _adapter_fixture(tmp_path)
    adapter.initialize()
    request = _request_for(adapter, snapshot)
    request["candidate_digest"] = "0" * 64
    with pytest.raises(PolicyAdapterError, match="candidate order digest mismatch"):
        adapter.decide(request)


def test_policy_adapter_preserves_live_s1_whole_decision_admission(tmp_path: Path) -> None:
    snapshot = _snapshot()
    snapshot["bound_actions"]["actions"][0]["verb"] = "use"
    adapter = _adapter_fixture(tmp_path)
    adapter.initialize()
    with pytest.raises(PolicyAdapterError, match="whole decision"):
        adapter.decide(_request_for(adapter, snapshot))


def test_policy_adapter_rejects_read_contract_drift(tmp_path: Path) -> None:
    adapter = _adapter_fixture(tmp_path)
    adapter.initialize()
    request = _request_for(adapter, _snapshot())
    request["bundle"]["reads"] = [{"read_id": "read:unexpected"}]
    with pytest.raises(PolicyAdapterError, match="unexpected prefetched"):
        adapter.decide(request)


def test_ndjson_child_protocol_matches_platform_policy_port(tmp_path: Path) -> None:
    prepared = _adapter_fixture(tmp_path)
    prepared.initialize()
    input_value = _request_for(prepared, _snapshot())
    adapter = _adapter_fixture(tmp_path)
    requests = json.dumps(
        {
            "schema": "sts2.policy-runtime/policy-port-1",
            "message_type": "decide",
            "request_id": "request-fixture-1",
            "input": input_value,
        }
    )
    output = io.StringIO()
    assert serve_ndjson(adapter, input_lines=iter(requests.splitlines()), output=output) == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(responses) == 2
    assert responses[0] == {
        "schema": "sts2.policy-runtime/policy-port-1",
        "message_type": "ready",
        "adapter": dict(prepared.manifest)["adapter"],
    }
    assert responses[1]["schema"] == "sts2.policy-runtime/policy-port-1"
    assert responses[1]["message_type"] == "decision"
    assert responses[1]["request_id"] == "request-fixture-1"
    assert responses[1]["output"] == {
        "candidate_digest": input_value["candidate_digest"],
        "scores": [0.25, 0.75],
        "selected_index": 1,
    }
