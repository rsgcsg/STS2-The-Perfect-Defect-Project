"""A start package with freshly initialised weights: an RL chain from scratch (RL5 A1).

The structure is copied from a template start package (vocabulary, inference execution and
model size, so architecture q3-m-std-1 for today's start): the inference services, the
compiler identity and the compile warm-up dumps see the same model, only the weights are
new. They are drawn by MStd's own constructor under `seed`, the same initialisation a
from-scratch SFT run used. The result is an ordinary start package (`export_model`): the
learner, services and evaluation treat it like any other start. Its `training_identity`
names the seed and the template, and `package_sha256` binds both.

The RL4 outcome/HP heads are not in a package; the learner adds them at start with their
constructor init (zero final layers: a uniform outcome over the valid classes, HP 0). Fitted
heads belong to another package, so a random start runs without a warm-up artifact
(fast-chain site `warmup_file = ""`).

    python -m stpd.rl.random_start --template START_PACKAGE --seed 1 --out DIR
writes DIR/init.json (the source of the weights) and DIR/package (the start package).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import torch

from stpd.p1.data import json_bytes
from stpd.p1.export import export_model, load_model
from stpd.p1.model import MStd

SCHEMA = "stpd/rl-random-start-1"


def random_start_package(template: Path, seed: int, output: Path) -> dict[str, Any]:
    """Export a seeded, untrained model of the template's structure; returns model.json."""
    descriptor = json.loads((template / "model.json").read_bytes())
    shape, _ = load_model(template / "model.tensor-tree", descriptor["package_sha256"])
    with torch.random.fork_rng(devices=[]):  # the caller's RNG stays untouched
        torch.manual_seed(seed)
        model = MStd(shape.vocabulary, shape.execution, model_size=shape.model_size)
    identity = {"schema": SCHEMA, "seed": seed,
                "template_package_sha256": descriptor["package_sha256"],
                "torch": str(torch.__version__)}
    output.mkdir(parents=True, exist_ok=False)
    source = output / "init.json"
    source.write_bytes(json_bytes(identity))
    return export_model(model.eval(), source, output / "package", identity)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m stpd.rl.random_start")
    parser.add_argument("--template", type=Path, required=True,
                        help="start package whose structure the new model copies")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True, help="new directory")
    args = parser.parse_args(argv)
    if args.seed < 0:
        parser.error("--seed must be >= 0")
    descriptor = random_start_package(args.template, args.seed, args.out)
    print(json.dumps({"start_package": str(args.out / "package"),
                      "package_sha256": descriptor["package_sha256"],
                      "training_identity": descriptor["training_identity"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
