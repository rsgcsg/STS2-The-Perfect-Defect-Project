"""C-SCALE data parallel, first version: two real gloo ranks on CPU.

Each rank runs `prepared_update` on its own minibatch with `GradientAverage`;
both must end bitwise equal to each other and equal to one process that applies
the mean of the two minibatch gradients and totals.
"""

from __future__ import annotations

import copy
import os

import pytest
import torch
from test_rl41_w1 import model as model  # noqa: F401  (fixture)
from test_rl_rows_forward import prepared
from test_rl_rows_forward import varied as varied  # noqa: F401  (fixture)

from stpd.rl.loss import LossConfig, prepared_update
from stpd.rl.performance import PerformanceConfig

CONFIG = LossConfig(mode="ppo", value_coef=0.2, entropy_coef=0.01)
PERFORMANCE = PerformanceConfig(prepared_forward="rows-v2")


def _rank(rank: int, world: int, store: str, model, minibatches, out: str) -> None:
    import torch.distributed as dist

    from stpd.rl.data_parallel import GradientAverage

    torch.set_num_threads(1)
    # spawn shares parameter storage with the parent and the other rank: own copy.
    model = copy.deepcopy(model)
    dist.init_process_group("gloo", init_method="file://" + store, rank=rank,
                            world_size=world)
    try:
        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
        result = prepared_update(model, optimizer, minibatches[rank], config=CONFIG,
                                 performance=PERFORMANCE, target_kl=1e9,
                                 gradient_sync=GradientAverage())
        torch.save({"state": model.state_dict(), "result": result,
                    "adam": optimizer.state_dict()}, os.path.join(out, f"rank{rank}.pt"))
    finally:
        dist.destroy_process_group()


class _Capture:
    def __init__(self) -> None:
        self.gradients: dict[str, torch.Tensor | None] = {}
        self.totals: torch.Tensor | None = None

    def __call__(self, model, totals, valid):
        self.gradients = {name: None if p.grad is None else p.grad.clone()
                          for name, p in model.named_parameters()}
        self.totals = totals.clone()
        return totals, valid


@pytest.mark.skipif(os.name != "posix", reason="real spawned gloo ranks")
def test_two_ranks_take_the_mean_gradient_step_and_stay_identical(model, varied, tmp_path):
    minibatches = [prepared(model, "rows-v2").minibatches[0] for _ in range(2)]
    captures = []
    for minibatch in minibatches:
        capture = _Capture()
        trained = copy.deepcopy(model)
        prepared_update(trained, torch.optim.AdamW(trained.parameters(), lr=1e-3), minibatch,
                        config=CONFIG, performance=PERFORMANCE, target_kl=1e9,
                        gradient_sync=capture)
        captures.append(capture)

    def mean(model, totals, valid):
        for name, p in model.named_parameters():
            values = [c.gradients[name] for c in captures]
            if all(v is None for v in values):
                continue
            p.grad = sum(torch.zeros_like(p) if v is None else v for v in values) / 2
        return (captures[0].totals + captures[1].totals) / 2, valid

    expected_model = copy.deepcopy(model)
    expected_optimizer = torch.optim.AdamW(expected_model.parameters(), lr=1e-3)
    expected = prepared_update(expected_model, expected_optimizer, minibatches[0],
                               config=CONFIG, performance=PERFORMANCE, target_kl=1e9,
                               gradient_sync=mean)
    torch.multiprocessing.spawn(_rank, args=(2, str(tmp_path / "store"), model, minibatches,
                                             str(tmp_path)), nprocs=2, join=True)
    ranks = [torch.load(tmp_path / f"rank{r}.pt", weights_only=False) for r in range(2)]
    for name, value in ranks[0]["state"].items():
        assert torch.equal(value, ranks[1]["state"][name]), name
        torch.testing.assert_close(value, expected_model.state_dict()[name], rtol=1e-5,
                                   atol=1e-6, msg=name)
    for key in ("td_loss", "policy_loss", "approx_kl_mu_theta", "gradient_norm"):
        assert ranks[0]["result"][key] == ranks[1]["result"][key], key
        assert ranks[0]["result"][key] == pytest.approx(expected[key], rel=1e-5, abs=1e-7)
    assert ranks[0]["result"]["stepped"] and "gradient_sync" in ranks[0]["result"][
        "host_timing"]


def test_gradient_average_needs_a_process_group():
    from stpd.rl.data_parallel import GradientAverage

    with pytest.raises(ValueError, match="data_parallel_requires_initialized_process_group"):
        GradientAverage()
