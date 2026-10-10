"""Actual Public M2 adapter/wire with a synthetic counting model, no weights."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import torch
from test_public_m2_port import setup

from stpd.policy.public_m2_port import PublicM2PolicyAdapter, serve


def main() -> int:
    torch.set_num_threads(2)
    manifest = json.loads(Path(sys.argv[1]).read_bytes())
    record = Path(sys.argv[2])
    old, model = setup()
    config = replace(old.config, max_actions_per_step=31)
    adapter = PublicM2PolicyAdapter(
        model,
        old.tokenizer,
        config,
        manifest,
        weights_sha256="c" * 64,
        input_digest="d" * 64,
    )
    decide = adapter.decide

    def observed(decision, control):
        before = len(model.calls)
        try:
            output, completion = decide(decision, control)
        except ValueError as error:
            raw_memory = adapter._memory.detach().cpu().contiguous().numpy().tobytes()
            with record.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "error": getattr(error, "code", type(error).__name__),
                            "step_calls": len(model.calls),
                            "advanced": len(model.calls) != before,
                            "memory_sha256": hashlib.sha256(raw_memory).hexdigest(),
                        }
                    )
                    + "\n"
                )
            raise
        raw_memory = adapter._memory.detach().cpu().contiguous().numpy().tobytes()
        with record.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "control": control,
                        "output": output,
                        "completion": completion,
                        "step_calls": len(model.calls),
                        "advanced": len(model.calls) != before,
                        "memory_sha256": hashlib.sha256(raw_memory).hexdigest(),
                        "reset": model.calls[-1][2],
                    }
                )
                + "\n"
            )
        return output, completion

    adapter.decide = observed
    return serve(adapter, sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
