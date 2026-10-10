"""No-fit meta-factory tensor inventory gate; no real model parameters allocated."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from spireagent.json_boundary import BoundaryError
from stpd.canonical import semantic_hash
from stpd.reward_m0_spec import RewardM0Config
from stpd.stage1a_recipes import LIGHT_ACTION_M0_GRAPH


def test_completed_tensor_inventory_uses_owner_factory_meta_without_real_weights():
    torch = pytest.importorskip("torch")
    torch.set_num_threads(2)
    from stpd.models.stage1a import build_scorer
    from stpd.models.token_core import ScratchShape, ScratchTokenCore
    from stpd.workers.reward_m0_run import _validate_completed_tensor_shape
    from stpd.workers.token_ranking import LightActionM0Config, _scoring_seed, config_payload

    config = RewardM0Config(steps=2, state_vocab_size=256)
    codec = {"vocab_size": 256, "family": "train-only-byte-bpe", "sha256": "a" * 64}
    shape = ScratchShape(256, max_tokens=config.max_state_tokens)
    backbone = {"kind": "scratch", "shape": asdict(shape), "state_codec": codec}
    backbone["core_fingerprint"] = semantic_hash(
        {
            "core": backbone,
            "graph": LIGHT_ACTION_M0_GRAPH,
            "vocabulary_size": 256,
            "max_state_tokens": config.max_state_tokens,
        }
    )
    with torch.device("meta"):
        model = build_scorer(
            LightActionM0Config().recipe,
            ScratchTokenCore(shape),
            max_action_bytes=config.max_action_bytes,
            scoring_seed=_scoring_seed(config.seed),
        )
    tensors = model.state_dict()
    assert tensors and all(value.device.type == "meta" for value in tensors.values())
    numerical = LightActionM0Config(steps=2)
    state = {
        "schema": "stpd/stage1a-light-action-m0-checkpoint-v1",
        "config": config_payload(numerical),
        "model": tensors,
    }
    _validate_completed_tensor_shape(state, backbone, codec, config)
    with pytest.raises(BoundaryError, match="completed_admitted_tensor_inventory_mismatch"):
        _validate_completed_tensor_shape({**state, "model": {}}, backbone, codec, config)
    wrong = dict(tensors)
    key = next(key for key, value in wrong.items() if value.numel() > 1)
    wrong[key] = torch.empty((1,), device="meta", dtype=wrong[key].dtype)
    with pytest.raises(BoundaryError, match="completed_admitted_tensor_inventory_mismatch"):
        _validate_completed_tensor_shape({**state, "model": wrong}, backbone, codec, config)
