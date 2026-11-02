"""Run the research RL learner composed with the application's trajectory and model owners.

``stpd.rl.learner`` stays free of workbench imports; this entry point supplies
the Managed trajectory-shard reader and the detached P1 model import.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import threading
from pathlib import Path

from spireagent.json_boundary import BoundaryError
from stpd.rl.learner import LearnerConfig, LearnerIO

from .global_evidence_store import publish_detached_p1_model
from .managed_segment_store import read_segment
from .managed_trajectory_store import read_shard

# Historical readers/engineering fixtures keep the old composition available.
# The live CLI below exclusively selects the short-segment implementation.
IO = LearnerIO(read_shard=read_shard, publish_model=publish_detached_p1_model)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    threading.current_thread().name = "rl-learner"
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=True)

    def stop(_number: int, _frame: object) -> None:
        # Defer graceful stop to the next update boundary; keep final publication alive.
        (work / "STOP").touch()

    previous = {number: signal.signal(number, stop)
                for number in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1)}
    try:
        config = LearnerConfig.read(args.config)
        if config.segments is None:
            raise BoundaryError("rl_learner", "whole_episode_live_training_retired_use_segments")
        from stpd.rl.segment_learner import SegmentLearner, SegmentLearnerIO

        summary = SegmentLearner(config, work,
            SegmentLearnerIO(read_segment=read_segment,
                             publish_model=publish_detached_p1_model)).run()
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    print(json.dumps({k: summary[k] for k in ("updates", "versions", "counts")}))


if __name__ == "__main__":
    main()
