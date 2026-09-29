#!/usr/bin/env python3
"""Run one bounded exported M2/Reset model on the exact Managed text-menu consumer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stpd.host_runtime_client import (  # noqa: E402
    DEFAULT_HOST_RUNTIME,
    DEFAULT_HOST_RUNTIME_PIN,
    activate_host_runtime_client,
    load_host_runtime_pin,
)
from stpd.managed_memory_smoke import (  # noqa: E402
    SmokeLimits,
    run_managed_memory_smoke,
    validate_smoke_request,
)
from stpd.policy.memory_export import validate_memory_package  # noqa: E402
from stpd.policy.memory_scorer import OnlineM2Scorer  # noqa: E402
from stpd.training_smoke import driver_command  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-runtime", type=Path, default=DEFAULT_HOST_RUNTIME)
    parser.add_argument("--host-runtime-pin", type=Path, default=DEFAULT_HOST_RUNTIME_PIN)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--model-export", type=Path, required=True)
    parser.add_argument("--seed", action="append", required=True)
    parser.add_argument("--character", default="Defect")
    parser.add_argument("--ascension", type=int, default=0)
    parser.add_argument("--max-policy-calls", type=int, default=4)
    parser.add_argument("--max-submissions", type=int, default=1)
    parser.add_argument("--max-observations", type=int, default=8)
    parser.add_argument("--max-seconds", type=float, default=30.0)
    args = parser.parse_args()
    try:
        limits = SmokeLimits(args.max_policy_calls, args.max_submissions,
                             args.max_observations, args.max_seconds)
        host_runtime = args.host_runtime.resolve()
        activate_host_runtime_client(host_runtime, load_host_runtime_pin(args.host_runtime_pin))
        from sts2_headless import ManagedPlayerEnvironment

        if (not callable(getattr(ManagedPlayerEnvironment, "observe_text_menu", None))
                or not callable(getattr(ManagedPlayerEnvironment, "submit_text_menu", None))):
            raise RuntimeError("text_menu_consumer_unavailable")
        package, weights, tokenizer, config = validate_memory_package(args.model_export)
        validate_smoke_request(tuple(args.seed), package["ids"]["model"],
                               config.reset_each_step, args.character, args.ascension)
        import torch

        torch.set_num_threads(config.cpu_threads)
        scorer = OnlineM2Scorer.from_export(weights, config, tokenizer)
        command = [*driver_command(host_runtime, args.candidate.resolve()),
                   "--character", args.character, "--timeout-ms", "5000"]
        environment = ManagedPlayerEnvironment(
            command, response_timeout_seconds=min(limits.max_seconds, 10.0))
        report = run_managed_memory_smoke(
            environment, scorer, seeds=tuple(args.seed),
            model_id=package["ids"]["model"], reset_each_step=config.reset_each_step,
            limits=limits, character=args.character, ascension=args.ascension,
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report["status"] == "engineering_smoke_complete" else 2
    except Exception as error:
        code = "text_menu_consumer_unavailable" if str(error) == "text_menu_consumer_unavailable" \
            else "managed_memory_smoke_unavailable"
        print(json.dumps({"schema":"stpd/managed-memory-engineering-smoke-v1",
                          "status":"unavailable", "reason_code":code}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
