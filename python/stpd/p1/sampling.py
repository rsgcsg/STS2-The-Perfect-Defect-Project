"""Training-only behavior distribution for the stateless P1 Agent.

One function defines q from recorded logits so the sampler and the trajectory
projection can never disagree: p = softmax(logits),
q_i = (1-eps) p_i^(1/T) / sum_j p_j^(1/T) + eps/N. Draws are a pure function of
(seed, consumption id), so a re-observed consumption unit never draws again and
a replay of the same original decides identically.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any

MODE = "temperature_uniform"
TEMPERATURE_RANGE = (1.0, 1.3)
# rl-inference-4 exploration-arm envelope (`inference_service.check_sampling`; the value
# heads' config input `stpd.rl.heads.value_config_vector` scales by the same bounds).
# At T >= 0.25 a probability p > 0 keeps q > 0 for logit gaps up to ~180 nats, beyond
# what the float32 policy distinguishes from 0; a lower T would round reachable actions
# to 0. A run configures its own minimum in [ARM_TEMPERATURE_FLOOR, 1].
ARM_TEMPERATURE_FLOOR = 0.25
ARM_TEMPERATURE_MAX = 3.0
ARM_UNIFORM_MIX_MAX = 0.1


def validate_policy(policy: Any) -> dict[str, Any]:
    if (not isinstance(policy, dict) or set(policy) != {"mode", "temperature", "uniform_mix",
                                                        "seed"}
            or policy["mode"] != MODE
            or type(policy["temperature"]) not in {int, float}
            or not TEMPERATURE_RANGE[0] <= policy["temperature"] <= TEMPERATURE_RANGE[1]
            or type(policy["uniform_mix"]) not in {int, float}
            or not 0 <= policy["uniform_mix"] < 1
            or not isinstance(policy["seed"], str) or not 1 <= len(policy["seed"]) <= 128):
        raise ValueError("invalid P1 sampling behavior policy")
    return policy


def distribution(logits: list[float], temperature: float, uniform_mix: float
                 ) -> tuple[list[float], list[float]]:
    """Return (model probabilities p, behavior probabilities q) in catalog order."""
    if not logits or not all(type(v) in {int, float} and math.isfinite(v) for v in logits):
        raise ValueError("sampling needs finite logits for the full catalog")
    top = max(logits)
    weights = [math.exp(v - top) for v in logits]
    total = sum(weights)
    model = [w / total for w in weights]
    tempered = [p ** (1 / temperature) for p in model]
    scale = sum(tempered)
    count = len(logits)
    behavior = [(1 - uniform_mix) * w / scale + uniform_mix / count for w in tempered]
    return model, behavior


def draw(probabilities: list[float], seed: str, consumption_id: str) -> int:
    """Inverse-CDF draw from a 53-bit uniform derived from seed and decision identity."""
    raw = hashlib.sha256(f"{seed}\0{consumption_id}".encode()).digest()
    uniform = (int.from_bytes(raw[:8], "big") >> 11) / float(1 << 53)
    cumulative = 0.0
    for index, value in enumerate(probabilities):
        cumulative += value
        if uniform < cumulative and value > 0:
            return index
    return max(i for i, value in enumerate(probabilities) if value > 0)
