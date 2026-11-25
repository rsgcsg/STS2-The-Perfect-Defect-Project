"""RL4 public-scene outcome and absolute post-combat HP readouts.

The two heads read the shared scene, never each other's predictions. Reward
values describe the standard configuration of one ascension; observed terminal
reward must match before a class label is admitted. Missing Boss witnesses are
not filled.

Ascension (2026-11-13, owner: every level enters the fast chain): the 51 classes
and the head are the same for every level (the model sees the public ascension in
its input); only the reward each class stands for depends on the level. A0-A9
share the A0 table (48 floors, 3 Bosses: reward v2.1); A10 has 49 floors and 4
Bosses, so its table divides by 49, and an A10 natural loss on floor 49 or later
(the second Boss of the last act) has no class here: it is refused
(`terminal_a10_floor49_loss_unsupported`), never mapped onto class 48.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, cast

import torch
from torch import Tensor, nn

from stpd.p1.sampling import (
    ARM_TEMPERATURE_FLOOR,
    ARM_TEMPERATURE_MAX,
    ARM_UNIFORM_MIX_MAX,
    is_greedy,
)
from stpd.p1.value_head import AttentionValueResidual

# Golden Compass has a witnessed alternate Act-2 map: natural death on floor35
# with only Boss1 cleared. Keep the original default classes, and split only the
# actual reward ambiguity; this is still one outcome head, not another objective.
OUTCOME_CLASSES = 51
CLASS_FLOORS = tuple(range(49)) + (49, 35)
WIN_CLASS = 49
HEAD_PREFIX = "rl4_heads."
# Ascensions whose class rewards this module describes (the fast chain refuses others).
SUPPORTED_ASCENSIONS = tuple(range(11))
_BOSS_FLOORS = (17, 33, 48)


# Value-config input (`segments.value_config_input`, owner 2026-11-19): the outcome and
# HP heads also read the sampling configuration ("exploration arm") the game was played
# with, so V(s) is the value of one play style instead of a mixture of arms. Four numbers
# per row: (ln T, mix / 0.1) of the combat class, then of the noncombat class. Control
# (T 1, mix 0) is all zeros. Within the rl-inference-4 arm envelope (`stpd.p1.sampling`)
# ln T spans [ln 0.25, ln 3] ~ [-1.39, 1.10] ([0, 1.10] at the default temperature
# minimum 1) and mix / 0.1 spans [0, 1], so both inputs are order one; a greedy class reads
# ln T = ln(floor / 2) (`GREEDY_VALUE_LOG_TEMPERATURE`). The policy logits never read it.
VALUE_CONFIG_SIZE = 4
VALUE_CONFIG_HEAD = "rl4-value-config-4"  # architecture["auxiliary_heads"] entry
# Rl4Heads state keys: one ParameterDict registered after `hp`, so last in parameter order.
VALUE_CONFIG_KEYS = ("value_config.outcome", "value_config.hp")
CONTROL_VALUE_CONFIG = (0.0,) * VALUE_CONFIG_SIZE
# A greedy class (T = 0, `stpd.p1.sampling.GREEDY_TEMPERATURE`; user 2026-11-24) has no
# ln T. It reads as T = floor / 2: colder than every sampled cell, distinct from all of
# them by construction (no run rule needed) and still order one (-2.08). The 4 inputs
# and every stored config of a sampled cell are unchanged, so an older CP loads as is.
GREEDY_VALUE_LOG_TEMPERATURE = math.log(ARM_TEMPERATURE_FLOOR / 2)


def value_config_vector(classes: dict[str, Any]) -> tuple[float, float, float, float]:
    """The value-config input of one rl-inference-4 `sampling.classes`."""
    out: list[float] = []
    for name in ("combat", "noncombat"):
        policy = classes[name]
        temperature, mix = policy["temperature"], policy["uniform_mix"]
        greedy = is_greedy(temperature)
        if (type(temperature) not in (int, float)
                or not (greedy or ARM_TEMPERATURE_FLOOR <= temperature <= ARM_TEMPERATURE_MAX)
                or type(mix) not in (int, float) or not 0 <= mix <= ARM_UNIFORM_MIX_MAX):
            raise ValueError("invalid_value_config_sampling")
        out += [GREEDY_VALUE_LOG_TEMPERATURE if greedy else math.log(temperature),
                mix / ARM_UNIFORM_MIX_MAX]
    return out[0], out[1], out[2], out[3]


def _checked_config(value: Any) -> tuple[float, ...]:
    if (value is None or len(value) != VALUE_CONFIG_SIZE
            or not all(type(v) in (int, float) and math.isfinite(v) for v in value)):
        raise ValueError("value_config_missing_or_invalid")
    return tuple(float(v) for v in value)


def row_value_configs(metadata: Any, sources: Any) -> list[tuple[float, ...]]:
    """Per PPO dataset row, its source's value config (`metadata["value_config"]` on the
    source's first row, written by fast_source). A source without one fails (heads with
    the input never guess an arm). `sources` as for `learning_diagnostics.row_arms`."""
    out: list[tuple[float, ...]] = []
    for rows in sources:
        if rows:
            out.extend([_checked_config(metadata[len(out)].get("value_config"))] * rows)
    if len(out) != len(metadata):
        raise ValueError(f"row_value_configs: sources cover {len(out)} rows, metadata has "
                         f"{len(metadata)}")
    return out


def batch_value_configs(metadata: Any, request: Any) -> list[tuple[float, ...]]:
    """Every row of a prepared dataset in its row order: the PPO rows (`row_value_configs`)
    then the auxiliary terminal/HP rows `n + local`, each with the config the learner
    recorded from its own segment (`AuxiliarySelection.value_config`)."""
    out = row_value_configs(metadata, [member.rows for member in request.members])
    out.extend(_checked_config(selection.value_config) for selection in request.auxiliary)
    return out


def mlp_readout(mlp: nn.Sequential, features: Tensor, shift: Tensor | None) -> Tensor:
    """`AttentionValueResidual.mlp(features)`, with `shift` (the value-config term) added
    to the first layer's pre-activation. None is the original call, unchanged."""
    if shift is None:
        return cast(Tensor, mlp(features))
    hidden = mlp[0](features)
    return cast(Tensor, mlp[1:](hidden + shift.to(hidden.dtype)))


def finish_floor(ascension: int) -> int:
    """Reward v2.1 finish floor of a supported ascension; ValueError otherwise."""
    if type(ascension) is not int or ascension not in SUPPORTED_ASCENSIONS:
        raise ValueError("unsupported_ascension")
    return 49 if ascension == 10 else 48


class Rl4Heads(nn.Module):
    def __init__(self, frequencies: Sequence[float] | None = None, *,
                 value_config: bool = False) -> None:
        super().__init__()
        counts = torch.ones(OUTCOME_CLASSES) if frequencies is None else torch.tensor(
            list(frequencies), dtype=torch.float32)
        if (counts.shape != (OUTCOME_CLASSES,) or not bool(torch.isfinite(counts).all())
                or bool((counts < 0).any())):
            raise ValueError("invalid_outcome_frequencies")
        # Reuse the established learned query/attention/mean/MLP implementation.
        self.outcome = AttentionValueResidual()
        self.outcome.mlp[-1] = nn.Linear(256, OUTCOME_CLASSES)
        final = cast(nn.Linear, self.outcome.mlp[-1])
        nn.init.zeros_(final.weight)
        with torch.no_grad():
            final.bias.copy_((counts + 1e-3).log())
        self.hp = AttentionValueResidual()
        if value_config:
            self.add_value_config()

    @property
    def has_value_config(self) -> bool:
        return hasattr(self, "value_config")

    def add_value_config(self) -> None:
        """Zero value-config weights (no RNG drawn): every output stays as it was until
        they are trained. Registered after `hp`, so they come last in parameter order
        (a CP upgraded at a continuation and a fresh run share one optimizer order)."""
        if self.has_value_config:
            raise ValueError("value_config_already_enabled")
        self.value_config = nn.ParameterDict()
        for name, head in (("outcome", self.outcome), ("hp", self.hp)):  # item by item:
            first = cast(nn.Linear, head.mlp[0])                         # a dict sorts
            self.value_config[name] = nn.Parameter(torch.zeros(
                (first.out_features, VALUE_CONFIG_SIZE), dtype=first.weight.dtype,
                device=first.weight.device))

    def config_shifts(self, config: Tensor | None, rows: int
                      ) -> tuple[Tensor | None, Tensor | None]:
        """(outcome, HP) first-layer terms of a [rows, 4] value config; (None, None) for
        heads without the input. Heads with it need the rows' config (no silent control)."""
        if not self.has_value_config:
            if config is not None:
                raise ValueError("value_config_given_to_heads_without_it")
            return None, None
        if config is None:
            raise ValueError("value_config_required")
        if config.shape != (rows, VALUE_CONFIG_SIZE):
            raise ValueError("value_config_batch_mismatch")
        weights = [cast(Tensor, self.value_config[name]) for name in ("outcome", "hp")]
        values = config.to(device=weights[0].device, dtype=weights[0].dtype)
        return (torch.nn.functional.linear(values, weights[0]),
                torch.nn.functional.linear(values, weights[1]))

    def forward(self, scene: Tensor, mask: Tensor, mean: Tensor,
                floors: Sequence[int] | Tensor, config: Tensor | None = None
                ) -> tuple[Tensor, Tensor]:
        shifts = self.config_shifts(config, scene.shape[0])
        logits = module_readout(self.outcome, scene, mask, mean, shifts[0]).float()
        valid = outcome_mask(floors, device=logits.device)
        if logits.shape != valid.shape:
            raise ValueError("outcome_floor_batch_mismatch")
        hp = module_readout(self.hp, scene, mask, mean, shifts[1])
        return logits.masked_fill(~valid, -torch.inf), hp[:, 0].float()


def module_readout(module: AttentionValueResidual, scene: Tensor, mask: Tensor, mean: Tensor,
                    shift: Tensor | None) -> Tensor:
    """`AttentionValueResidual.forward` with the value-config term (None: the module)."""
    if shift is None:
        return cast(Tensor, module(scene, mask, mean))
    pooled, _ = module.attention(module.query.expand(scene.shape[0], -1, -1),
                                 scene, scene, key_padding_mask=mask, need_weights=False)
    return mlp_readout(module.mlp, torch.cat((pooled[:, 0], mean), dim=1), shift)


def load_heads_state(heads: Rl4Heads, state: dict[str, Tensor]) -> None:
    """Strict load; heads with the value-config input also take a state without it (an
    older head snapshot) with zero config weights. Anything else differing fails."""
    if heads.has_value_config and not any(key in state for key in VALUE_CONFIG_KEYS):
        state = {**state, **{key: torch.zeros_like(heads.state_dict()[key])
                             for key in VALUE_CONFIG_KEYS}}
    heads.load_state_dict(state, strict=True)


def outcome_mask(floors: Sequence[int] | Tensor, *, device: torch.device | None = None) -> Tensor:
    values = torch.as_tensor(floors, device=device)
    if (values.ndim != 1 or values.dtype not in (torch.int32, torch.int64)
            or bool((values < 0).any())):
        raise ValueError("invalid_max_observed_floor")
    # Golden Compass lengthens the native map. Reward-v2 already saturates its
    # floor term at 48, so class48 also represents later equal-reward losses.
    # Preserve actual public floors in inputs/records; only this mask is capped.
    values = values.clamp(max=48)
    class_floors = torch.tensor(CLASS_FLOORS, device=values.device)
    return class_floors[None, :] >= values[:, None]


def outcome_cross_entropy(logits: Tensor, targets: Tensor) -> Tensor:
    """Per-row soft-label CE; impossible classes never multiply minus infinity."""
    values = logits.float()
    target = targets.to(device=values.device, dtype=torch.float32)
    valid = torch.isfinite(values)
    if (values.ndim != 2 or values.shape[1] != OUTCOME_CLASSES
            or target.shape != values.shape or not bool(valid.any(1).all())
            or bool(torch.isnan(values).any()) or bool(torch.isposinf(values).any())
            or not bool(torch.isfinite(target).all()) or bool((target < 0).any())
            or bool(target.masked_select(~valid).ne(0).any())
            or not torch.allclose(target.sum(1), torch.ones_like(target[:, 0]), atol=1e-5)):
        raise ValueError("invalid_outcome_distribution_or_target")
    logp = values.log_softmax(-1).masked_fill(~valid, 0)
    return -(target * logp).sum(-1)


def class_reward_values(*, ascension: int = 0,
                        device: torch.device | None = None) -> Tensor:
    """Class representation of one ascension, checked against actual reward at admission.

    A natural loss at a Boss floor has not cleared that Boss. A higher floor has
    passed it in this fixed native layout. This table is not evidence of clearance.
    `ascension` defaults to 0 for the old chain, which opens A0 only; the fast
    chain always passes the game's own (Host-checked) ascension.
    """
    finish = finish_floor(ascension)
    return torch.tensor([0.5 * (floor / finish) ** 2
                         + 0.1 * sum(boss < floor for boss in _BOSS_FLOORS)
                         for floor in range(49)] + [1.0, 0.5 * (35 / finish) ** 2 + 0.1],
                        device=device)


def checked_terminal_class(*, result: str, floor: int, reward: float,
                           ascension: int = 0) -> int:
    if type(floor) is not int or floor < 0 or result not in {"win", "loss"}:
        raise ValueError("invalid_natural_terminal_class")
    if result == "loss" and floor >= finish_floor(ascension) > 48:
        # A10 floor-49 loss: its reward (floor term saturated, Boss3 possibly
        # cleared) is not class 48's; refused loudly, never relabelled.
        raise ValueError("terminal_a10_floor49_loss_unsupported")
    floor = min(floor, 48)
    candidates = [WIN_CLASS] if result == "win" else [
        index for index, value in enumerate(CLASS_FLOORS)
        if value == floor and index != WIN_CLASS]
    values = class_reward_values(ascension=ascension)
    matched = [label for label in candidates if math.isfinite(reward)
        and math.isclose(reward, float(values[label]), abs_tol=1e-6)]
    if len(matched) != 1:
        raise ValueError("terminal_class_reward_mismatch")
    return matched[0]


def check_target_distribution(tail: Tensor) -> None:
    """One 51-class target row (a segment's tail): shape, finite, >= 0, mass 1 within 1e-5."""
    if (tail.shape != (OUTCOME_CLASSES,) or not bool(torch.isfinite(tail).all())
            or bool((tail < 0).any())
            or not torch.isclose(tail.sum(), tail.new_tensor(1), atol=1e-5)):
        raise ValueError("invalid_segment_probability_mass")


def categorical_lambda_targets(probabilities: Tensor, boundary: Tensor | None, *,
                               terminal_class: int | None = None,
                               gae_lambda: float = 0.99,
                               ascension: int = 0) -> tuple[Tensor, Tensor]:
    """Frozen gamma=1 segment targets and scalar advantages, without extra rho.

    probabilities contains only the T current states. A nonterminal segment also
    supplies its confirmed successor distribution; a real terminal supplies its
    checked class instead. Nothing is stitched here: under the fast chain's "game"
    value targets (`fast_source.game_target_tails`) the boundary of a non-final
    segment is the stitched tail computed from the later segments' recorded values
    (each row's own mu, no extra rho); otherwise it is the confirmed successor.
    """
    p = probabilities.detach().float()
    if (p.ndim != 2 or p.shape[1] != OUTCOME_CLASSES or not 1 <= len(p) <= 64
            or not math.isfinite(gae_lambda) or not 0 <= gae_lambda <= 1):
        raise ValueError("invalid_segment_distribution_or_lambda")
    if terminal_class is not None:
        if type(terminal_class) is not int or not 0 <= terminal_class < OUTCOME_CLASSES:
            raise ValueError("invalid_terminal_class")
        if boundary is not None:
            raise ValueError("terminal_has_no_bootstrap")
        tail = torch.nn.functional.one_hot(torch.tensor(terminal_class, device=p.device),
                                          OUTCOME_CLASSES).float()
    elif boundary is None:
        raise ValueError("nonterminal_requires_confirmed_boundary")
    else:
        tail = boundary.detach().to(device=p.device, dtype=torch.float32)
    if (not bool(torch.isfinite(p).all()) or bool((p < 0).any())
            or not torch.allclose(p.sum(1), torch.ones_like(p[:, 0]), atol=1e-5)):
        raise ValueError("invalid_segment_probability_mass")
    check_target_distribution(tail)
    targets = torch.empty_like(p)
    targets[-1] = tail
    for index in range(len(p) - 2, -1, -1):
        targets[index] = (1 - gae_lambda) * p[index + 1] + gae_lambda * targets[index + 1]
    advantages = (targets - p) @ class_reward_values(ascension=ascension, device=p.device)
    return targets, advantages
