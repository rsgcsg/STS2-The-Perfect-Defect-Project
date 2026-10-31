"""RL-only metadata tensor keeps long histories outside the codec JSON header.

Legacy checkpoint-2 scalar metadata remains readable and unchanged. PPO's
checkpoint-3 uses one uint8 tensor: node/header costs do not grow per episode or
sample. Actual JSON bytes, model/optimizer/RNG and immutable shard references are
preserved, with the existing overall codec size limits still enforced.
"""

from __future__ import annotations

import json
from typing import Any

import torch

from spireagent.json_boundary import BoundaryError, json_bytes

LEGACY_SCHEMA = "stpd/rl-learner-checkpoint-2"
PPO_SCHEMA = "stpd/rl-learner-checkpoint-3"
METADATA_ENCODING = "json-utf8-u8-v1"
MAX_METADATA_BYTES = 64 * 1024 * 1024


def metadata_tensor(value: dict[str, Any]) -> torch.Tensor:
    raw = json_bytes(value)
    if len(raw) > MAX_METADATA_BYTES:
        raise BoundaryError("rl_checkpoint", "metadata_byte_limit")
    return torch.frombuffer(bytearray(raw), dtype=torch.uint8).clone()


def learner_state(checkpoint: dict[str, Any]) -> dict[str, Any]:
    value = checkpoint["learner"]
    if isinstance(value, str) and checkpoint.get("schema") == LEGACY_SCHEMA:
        result = json.loads(value)
    elif (
        checkpoint.get("schema") == PPO_SCHEMA
        and checkpoint.get("learner_encoding") == METADATA_ENCODING
        and isinstance(value, torch.Tensor)
        and value.dtype == torch.uint8
        and value.ndim == 1
        and value.numel() <= MAX_METADATA_BYTES
    ):
        result = json.loads(value.cpu().numpy().tobytes())
    else:
        raise BoundaryError("rl_checkpoint", "unsupported_metadata_encoding")
    if not isinstance(result, dict):
        raise BoundaryError("rl_checkpoint", "metadata_object_required")
    return result
