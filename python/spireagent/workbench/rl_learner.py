"""Run the research RL learner composed with the application's trajectory and model owners.

``stpd.rl.learner`` stays free of workbench imports; this entry point supplies
the Managed trajectory-shard reader and the detached P1 model import.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
from pathlib import Path

from stpd.rl.learner import Learner, LearnerConfig, LearnerIO

from .global_evidence_store import publish_detached_p1_model
from .managed_trajectory_store import read_shard

IO = LearnerIO(read_shard=read_shard, publish_model=publish_detached_p1_model)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    threading.current_thread().name = "rl-learner"
    summary = Learner(LearnerConfig.read(args.config), args.work.resolve(), IO).run()
    print(json.dumps({k: summary[k] for k in ("updates", "versions", "counts")}))


if __name__ == "__main__":
    main()
