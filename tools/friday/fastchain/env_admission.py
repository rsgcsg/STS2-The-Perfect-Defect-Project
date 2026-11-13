"""Fast-chain env-node admission sidecar (port of the private fc_admit.py; env.sh).

Stands in for the actor's learner control connection (rl_actor `self.service`):
one `SegmentConnection` to the learner per env node, and the production
`fast_instance_admission` / `fast_instance_excluded` calls, unchanged. Drivers on
this node send one JSON line per checked `opened` (admit) or evidence failure
(exclude) over a Unix socket and get the outcome back; a refused or failed
admission makes the driver `close_slot` that game (production: censored close).
Never resent.

R7 audit games (#529): with FC_R7_GAMES_PER_HOUR > 0 the sidecar holds this node's
`R7Selection` (production sampler: the node's share of the hourly rate and of the
generation's `max_games`, its own random key, state next to the log); op `r7` returns its
decision before the driver opens a game. A failed choice is returned as a failure, never
a default.

argv: learner_attachment socket_path log_path
"""
from __future__ import annotations

import json
import math
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path


def r7_selection(directory: Path):  # type: ignore[no-untyped-def]
    """This node's R7Selection, or None when the run chose no R7 games."""
    rate = float(os.environ.get("FC_R7_GAMES_PER_HOUR", "0"))
    if rate <= 0:
        return None
    from spireagent.workbench.fast_chain_episodes import R7Selection

    nodes = max(1, int(os.environ["FC_ENV_NODES"]))
    return R7Selection.from_configuration({
        "key": secrets.token_hex(32), "quota": rate / nodes,
        "expected_games": int(os.environ["FC_R7_EXPECTED_GAMES_PER_NODE_HOUR"]),
        "window_seconds": 3600.0, "state_path": str(directory / "r7-state.json"),
        "max_games": math.ceil(int(os.environ["FC_R7_MAX_GAMES"]) / nodes)}, work=directory)


def main() -> None:
    from spireagent.workbench.rl_segment_producer import (
        fast_instance_admission,
        fast_instance_excluded,
    )
    from stpd.p1.local_inference import SegmentConnection

    attachment = json.loads(Path(sys.argv[1]).read_bytes())
    owner = {"run": attachment["run"], "training_generation": attachment["training_generation"]}
    control = SegmentConnection(attachment)
    log = open(sys.argv[3], "a", buffering=1)
    counters = {"admitted": 0, "admission_failed": 0, "excluded": 0, "exclusion_failed": 0}
    lock = threading.Lock()
    r7 = r7_selection(Path(sys.argv[3]).parent)

    def handle(line: bytes) -> dict:
        request = json.loads(line)
        if request["op"] == "r7":
            if r7 is None:
                return {"ok": False, "code": "r7_not_configured"}
            try:
                decision = r7()
            except Exception as error:  # noqa: BLE001 - the driver counts and retries
                return {"ok": False, "code": getattr(error, "code", type(error).__name__),
                        "detail": str(error)[:200]}
            log.write(json.dumps({"t": time.time(), "op": "r7", **decision}) + "\n")
            return {"ok": True, **decision}
        row = {"intent_id": request["intent_id"], "seed": request["seed"],
               "game_instance_id": request["game_instance_id"],
               "segment_admission_owner": owner}
        began = time.monotonic()
        try:
            if request["op"] == "admit":
                fast_instance_admission(row, {"game_instance_id": request["game_instance_id"]},
                                        admission=control, actor=request["actor"])
                key, result = "admitted", {"ok": True}
            else:
                fast_instance_excluded(row, admission=control, actor=request["actor"],
                                       reason=request.get("reason", "evidence_failed"))
                key, result = "excluded", {"ok": True}
        except Exception as error:  # noqa: BLE001 - outcome goes back to the driver
            key = "admission_failed" if request["op"] == "admit" else "exclusion_failed"
            result = {"ok": False, "code": getattr(error, "code", type(error).__name__),
                      "detail": str(error)[:200]}
        with lock:
            counters[key] += 1
        log.write(json.dumps({"t": time.time(), "op": request["op"],
                              "game_instance_id": request["game_instance_id"],
                              "seconds": round(time.monotonic() - began, 4), **result}) + "\n")
        return result

    def serve(client: socket.socket) -> None:
        reader = client.makefile("rb")
        for line in reader:
            client.sendall(json.dumps(handle(line)).encode() + b"\n")

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sys.argv[2])
    server.listen(64)
    print(json.dumps({"ready": True, **owner}), flush=True)

    def report() -> None:
        while True:
            time.sleep(30)
            with lock:
                print(json.dumps({"t": int(time.time()), **counters,
                                  **({"r7": r7.report()} if r7 is not None else {})},
                                 default=str), flush=True)

    threading.Thread(target=report, daemon=True).start()
    while True:
        client, _ = server.accept()
        threading.Thread(target=serve, args=(client,), daemon=True).start()


if __name__ == "__main__":
    main()
