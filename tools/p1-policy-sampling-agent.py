#!/usr/bin/env python3
"""Diagnostic-only softmax sampling on an existing verified P1 installation.

No registration, input version or weights are changed. The parent Runtime owns
ACK, delivery, unknown, budget and stop; this child only chooses offered handles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, TextIO

import torch


def serve(
    config: Path,
    manifest: Path,
    binding_root: Path,
    source: TextIO = sys.stdin,
    destination: TextIO = sys.stdout,
) -> None:
    from stpd.p1.agent import installation
    from stpd.p1.input import project

    checked, _manifest, model, _payload = installation(config, manifest, binding_root)
    generator = torch.Generator(device="cpu")

    def send(message: dict[str, Any]) -> None:
        destination.write(json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n")
        destination.flush()

    for line in source:
        message = json.loads(line)
        if message["type"] == "hello":
            generator.manual_seed(
                int(hashlib.sha256(message["episode_id"].encode()).hexdigest()[:15], 16)
            )
            send(
                {
                    "type": "ready",
                    "protocol": message["protocol"],
                    "agent_id": "verified-p1-softmax-sampling",
                    "agent_version": "1",
                }
            )
        elif message["type"] == "observation" and message.get("requires_choice"):
            observation = message["observation"]
            frame = project(observation, input_projection=checked["input_projection"])
            with torch.inference_mode():
                scores = model(frame).detach().cpu()
            if (
                len(scores) != len(observation["catalog"])
                or not len(scores)
                or not torch.isfinite(scores).all()
            ):
                raise ValueError("sampling needs finite scores for the complete offered catalog")
            index = int(
                torch.multinomial(torch.softmax(scores, dim=0), 1, generator=generator).item()
            )
            send(
                {
                    "type": "choose",
                    "occurrence": observation["envelope"]["occurrence"],
                    "choice_id": observation["catalog"][index]["choice_id"],
                }
            )
        elif message["type"] == "episode_end":
            break


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--binding-root", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(1)
    serve(args.config.resolve(), args.manifest.resolve(), args.binding_root.resolve())


if __name__ == "__main__":
    main()
