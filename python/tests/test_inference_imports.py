"""Clean-process inference dependency and preserved Python export regressions."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError
from stpd.canonical import semantic_hash
from stpd.fullrun.semantic_projection import _SemanticProjection

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = (
    "spireagent.hub",
    "spireagent.local_verified_bundle",
    "stpd.fullrun.platform_bundle3",
    "stpd.training",
    "stpd.qwen",
    "stpd.policy.adapter",
    "stpd.policy.s1",
    "stpd.models._backend",
    "stpd.models.batches",
    "stpd.models.losses",
    "stpd.models.objectives",
    "stpd.models.scheme1",
    "stpd.models.s2_simple",
    "stpd.models.s2_sdt",
)


def clean_process(body: str, forbidden: tuple[str, ...]) -> None:
    # -I discards inherited PYTHONPATH/user sites; select the tested source tree
    # explicitly, retaining this worktree's own locked dependency environment.
    script = f'''
import importlib.abc
import json
import sys
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
blocked = {forbidden!r}
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in blocked):
            raise AssertionError("unexpected inference dependency: " + fullname)
sys.meta_path.insert(0, BlockImports())
{body}
assert not any(name == prefix or name.startswith(prefix + ".")
               for name in sys.modules for prefix in blocked)
local_modules = {{name: Path(module.__file__).resolve() for name, module in sys.modules.items()
                 if (name == "stpd" or name.startswith("stpd.") or name == "spireagent"
                     or name.startswith("spireagent.")) and getattr(module, "__file__", None)}}
assert local_modules and all(path.is_relative_to(Path(sys.path[0]))
                             for path in local_modules.values()), local_modules
'''
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], text=True, capture_output=True,
        cwd=ROOT, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_structured_and_text_inputs_are_pure_without_application_or_torch() -> None:
    clean_process('''
from stpd.fullrun.structured_inputs import project_structured_snapshot
from stpd.fullrun.text_menu_inputs import project_text_menu_v2_snapshot
import stpd.models
import stpd.policy
fixture = Path(sys.path[0]) / "tests/fixtures/text_menu_v2/targeted-root.json"
snapshot = json.loads(fixture.read_text())
structured = project_structured_snapshot(snapshot)
text = project_text_menu_v2_snapshot(snapshot)
expected = (snapshot["menu_actions"]["actions"][0]["action_id"],)
assert structured.action_ids == text.action_ids == expected
assert structured.candidate_digest == text.candidate_digest
assert structured.nodes and text.state_text
''', (*FORBIDDEN, "torch"))


def test_actual_structured_inference_modules_do_not_load_legacy_or_hub() -> None:
    clean_process('''
import torch
torch.set_num_threads(2)
from stpd.policy.structured_export import code_digest
from stpd.policy.structured_port import StructuredOnlineScorer
from stpd.models.structured_m2 import StructuredM2
from stpd.canonical import semantic_hash
fixture = Path(sys.path[0]) / "tests/fixtures/text_menu_v2/targeted-root.json"
snapshot = json.loads(fixture.read_text())
scorer = StructuredOnlineScorer(StructuredM2(seed=0))
keys = [action["action_id"] for action in snapshot["menu_actions"]["actions"]]
frame, scores, advanced = scorer.observe(
    snapshot, run_id="synthetic", continuity_token="segment",
    expected_candidate_digest=semantic_hash(keys), expected_candidate_count=len(keys))
assert len(scores) == len(frame.candidates) == 1 and advanced
assert len(code_digest(Path(sys.path[0]))) == 64
''', FORBIDDEN)


def test_requested_model_export_loads_only_its_owner() -> None:
    blocked = tuple(name for name in FORBIDDEN if name != "stpd.models.batches")
    clean_process('''
from stpd.models import DynamicsBatch, RankBatch
import stpd.models
from stpd.models.batches import RankBatch as original
assert RankBatch is original is stpd.models.RankBatch
RankBatch("state", ("action",), 0, "synthetic").validate()
DynamicsBatch("state", "action", "successor", "synthetic").validate()
''', (*blocked, "torch"))


def test_lazy_export_propagates_dependency_failure() -> None:
    clean_process('''
import stpd.policy
try:
    _ = stpd.policy.PolicyAdapter
except AssertionError as error:
    assert "unexpected inference dependency: stpd.policy.adapter" in str(error)
else:
    raise AssertionError("requested dependency failure was hidden")
assert "PolicyAdapter" not in vars(stpd.policy)
''', FORBIDDEN)


EXPORTS = {
    "stpd.policy": {
        "adapter": ("DEFAULT_MANIFEST", "DEFAULT_CONFIG", "PolicyAdapter", "PolicyAdapterError",
                    "adapter_code_sha256", "serve_ndjson"),
        "s1": ("ResidentS1Model", "S1PolicyError"),
    },
    "stpd.models": {
        "batches": ("DynamicsBatch", "RankBatch"),
        "losses": ("anchor_loss", "listwise_rank_loss", "normalized_successor_loss"),
        "objectives": ("ObjectiveResult", "s2_sdt_objective", "s2_simple_objective",
                       "scheme1_objective"),
        "s2_sdt": ("S2SDTOutput", "S2SDTScorer"),
        "s2_simple": ("S2SimpleOutput", "S2SimpleScorer"),
        "scheme1": ("Scheme1Scorer",),
    },
}


@pytest.mark.parametrize("package_name", EXPORTS)
def test_every_existing_package_export_resolves_to_original_symbol(package_name: str) -> None:
    package = importlib.import_module(package_name)
    expected = {name for names in EXPORTS[package_name].values() for name in names}
    assert set(package.__all__) == expected
    assert expected <= set(dir(package))
    namespace: dict[str, object] = {}
    exec(f"from {package_name} import *", namespace)
    for module_name, names in EXPORTS[package_name].items():
        module = importlib.import_module(package_name + "." + module_name)
        for name in names:
            assert namespace[name] is getattr(package, name) is getattr(module, name)
    with pytest.raises(AttributeError, match="has no attribute"):
        _ = package.missing_export


def test_original_projection_alias_and_reference_semantics() -> None:
    from stpd.fullrun.platform_bundle3 import _SemanticProjection as historical

    assert historical is _SemanticProjection
    source = {"entity_id": "card-opaque", "name": "Strike", "owner_entity_id": "card-opaque"}
    projected = _SemanticProjection([source]).clean(source)
    assert projected == {
        "name": "Strike",
        "owner_entity_value": {
            "name": "Strike",
            "owner_entity_value": {"semantic_ref": semantic_hash({"name": "Strike"})},
        },
    }
    assert "card-opaque" not in json.dumps(projected)
    # Missing references fail closed in both direct and nested resolution.
    for resolving in (True, False):
        with pytest.raises(BoundaryError, match="missing_referent_semantics"):
            _SemanticProjection([]).reference("missing", resolving)
