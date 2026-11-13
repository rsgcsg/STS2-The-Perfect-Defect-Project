"""Fast-chain env node aggregator (port of the private chain-agg.py; launched by env.sh).

argv: service.json socket_path encoder_workers report_path [window_ms]
Runs the real NodeAggregator (encoder pool on this node) until SIGTERM, then writes
its stats. Prints one "ready" line once the Unix socket listens.
"""
from __future__ import annotations

import json
import signal
import sys
import threading
import time


def main() -> None:
    from stpd.p1.fast_aggregator import NodeAggregator
    from stpd.p1.inference_service import python_env_sha256
    from stpd.p1.input import V2_R19_INPUT_SPEC
    from stpd.p1.vocabulary import public_vocabulary

    info = json.load(open(sys.argv[1]))
    workers = int(sys.argv[3])
    # The real env digest of this node (same venv as the GPU side); the old smoke marker only.
    mark = info["env_mark"] if info["env_mark"] == "e" * 64 else python_env_sha256()
    aggregator = NodeAggregator(public_vocabulary(V2_R19_INPUT_SPEC),
                                service_address=(info["host"], info["port"]),
                                authkey=bytes.fromhex(info["authkey_hex"]),
                                socket_path=sys.argv[2], aggregator_id=__import__("os").environ.get("AGG_ID", "wp8m-node"),
                                python_env_sha256=mark, encoder_workers=workers,
                                encoder_cpus=None,
                                window_ms=float(sys.argv[5]) if len(sys.argv) > 5 else 1.5)
    print(json.dumps({"ready": True, "workers": workers}), flush=True)
    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    while not done.wait(10):
        print(json.dumps({"t": int(time.time()), "failure": aggregator.failure,
                          **{k: aggregator.stats[k] for k in ("rows", "packets", "replies")}}),
              flush=True)
        if aggregator.failure is not None:
            break
    report = aggregator.close()
    with open(sys.argv[4], "w") as handle:
        json.dump(report, handle, indent=1, default=str)


if __name__ == "__main__":
    main()
