"""Bench of the rl-inference-4 service under a production-shaped load (no game, no CP).

Diagnostic only: it never trains, publishes a checkpoint or touches a game. The
model is a random-init production-shape M-std; rows are real encoded observations
(an `obs.jsonl` of public observations). Each config runs K independent services
(`"services": K`, default 1), each in its own spawned host process with its own
main process, assembler (the real `FastSegmentAssembler`, emit discarded),
prepare workers and device process; node clients are split over the services
round-robin, as nodes over inference cards.

Load: `--nodes` node clients (one TCP link each, like a node aggregator), each with
`slots` games per load point. A game thinks (lognormal, mean `--think-ms`), then its
row joins the node packet; a packet leaves at a size drawn from `--batch LO,HI` or
`--agg-ms` after its first row, whichever is first. Each game sends its step outcome
before its next row and closes at the end. W = row ready to reply (includes the node
packing wait); RTT = packet sent to reply (the service's own latency).
Saturation: closed-loop clients keep `conns x games` rows in flight.
Correctness: a fixed 384-row probe per service vs an FP32 reference forward of the
same rows: max |behavior_q| difference, argmax agreement, selected_index agreement.
`--prof`: thread CPU per main-process function, per row (nested ones overlap).
`--stub-forward`: the device forward is replaced by random outputs of the real shapes
(masked by the real catalog and outcome-floor masks), so the device costs ~0 and the host side
(main process, prepare workers, staging) is measured alone; no correctness probe.
`--threads cpu`: at close each service host reports every main-process thread role's
own CPU per row. `--threads profile` (or a path prefix, to also write pstats files)
runs those threads under cProfile timed by thread CPU and adds per-function self CPU
per row (profiler overhead included: read shares, take absolutes from `cpu`).

`--bind game`: like `NodeAggregator(services=...)` (#473), every node client links to
all K services and binds each game by `service_for_game` (default `node`: node n ->
service n % K, which is uneven when K does not divide the node count).
Config keys handled here, not by the service: `segment_format` (the assembler's T3
format, #466), `switch_interval_ms` (the service host's `sys.setswitchinterval`).
`"trace": true` (service option): per-row stage time percentiles per load point
(`trace_ms` = [p50, p95, mean, rows_over_500ms, max] ms, see `hist_pct`). Trace is
diagnostic: the stage histograms are updated without a lock, so concurrent packet
completions may rarely lose a count.

Example: python tools/bench_rl_inference_3.py obs.jsonl out.jsonl \
  --configs '[{"name":"S2","services":2,"prepare_workers":10,"in_flight":2}]' \
  --loads 24,48,72 --batch 25,30 --agg-ms 40 --prof
"""

from __future__ import annotations

import argparse
import faulthandler
import heapq
import json
import math
import multiprocessing as mp
import os
import random
import signal
import sys
import threading
import time
from typing import Any

SAMPLING: dict[str, Any] = {
    "mode": "temperature_uniform", "seed": "bench-rl-inference-4", "arm_id": "control",
    "classes": {"combat": {"temperature": 1.0, "uniform_mix": 0.0},
                "noncombat": {"temperature": 1.0, "uniform_mix": 0.0}}}
ENV_SHA = "e" * 64
DRAIN_S = 60.0  # a reply missing this long after the end is a loud failure, not a hang
CLOSE_WAIT_S = 120.0  # a service close slower than this is a failed config (teardown)
PROFC: dict[str, list[float]] = {}
THREAD_PROFILES: list[Any] = []  # [thread name, cProfile.Profile | None, thread CPU at end]


def wire_row(meta: dict[str, Any], data: bytes, rid: int, name: str, step: int,
             offset: int) -> dict[str, Any]:
    return {"request_id": rid, "game_instance_id": name, "step_index": step,
            "consumption_id": f"{name}-{step}", "catalog_len": meta["catalog_len"],
            "catalog_sha256": meta["catalog_sha256"],
            "observation_sha256": meta["observation_sha256"], "in_combat": meta["in_combat"],
            "floor": meta["floor"] or 0,
            "payload": {"offset": offset, "length": len(data), "row_sha256": meta["row_sha256"]}}


def connect(address: Any, authkey: bytes, encoder: dict[str, Any], compiler: dict[str, Any],
            tag: str, names: list[str]) -> tuple[Any, int]:
    from multiprocessing.connection import Client

    from stpd.p1.inference_service import V3_MAX_MESSAGE, V3_SCHEMA, pack_message, unpack_message
    from stpd.p1.local_inference import tcp_nodelay
    c = Client(address, authkey=authkey)
    tcp_nodelay(c)
    c.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "attach", "request_id": 1,
                               "aggregator_id": tag, "chain": "fast_chain",
                               "python_env_sha256": ENV_SHA, "compiler_identity": compiler,
                               "encoder": encoder["encoder"],
                               "row_format": encoder["row_format"]}))
    unpack_message(c.recv_bytes(V3_MAX_MESSAGE))
    rid = 1
    for name in names:
        rid += 1
        c.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "episode_open",
                                   "ascension": 0, "request_id": rid,
                                   "game_instance_id": name, "actor_id": "a",
                                   "sampling": SAMPLING, "single_choice": "score",
                                   "start": {"kind": "new_run"}}))
    return c, rid


def node_client(address: Any, authkey: bytes, encoder: dict[str, Any], compiler: dict[str, Any],
                rows: list[Any], slots: int, think_ms: float, agg_ms: float,
                batch: tuple[int, int] | None, seconds: float, offset: int, out: Any,
                services: list[tuple[Any, bytes]] | None = None) -> None:
    """One node; with `services` (bind game) it links to all of them like the aggregator."""
    from stpd.p1.fast_aggregator import service_for_game
    from stpd.p1.inference_service import V3_MAX_MESSAGE, V3_SCHEMA, pack_message, unpack_message
    rng = random.Random(offset)
    sigma = 0.35
    mu = math.log(think_ms / 1000) - sigma * sigma / 2
    names = [f"n{offset}-{g}" for g in range(slots)]
    targets = services or [(address, authkey)]
    bound = {name: service_for_game(name, len(targets)) for name in names}
    links = []
    rid = 0
    for k, (k_address, k_auth) in enumerate(targets):
        mine = [name for name in names if bound[name] == k]
        link, last = connect(k_address, k_auth, encoder, compiler, f"node-{offset}-{k}", mine)
        links.append(link)
        rid = max(rid, last)
    step = dict.fromkeys(names, 0)
    cursor = {name: (offset * 7919 + i * 104729) % len(rows) for i, name in enumerate(names)}
    cond = threading.Condition()
    wake = [(time.perf_counter() + rng.random() * think_ms / 1000, name) for name in names]
    heapq.heapify(wake)
    ready_at: dict[int, float] = {}
    sent_at: dict[int, float] = {}
    lat: list[float] = []
    rtt: list[float] = []
    packets = [0, 0]
    row_of: dict[int, tuple[int, int, int]] = {}
    last_row: dict[str, tuple[int, int, int]] = {}  # game -> its scored row awaiting an outcome
    end = time.perf_counter() + seconds
    stop = [False]
    outstanding = [0]
    failure: list[str] = []

    def receiver(c: Any) -> None:
        while True:
            try:
                envelope, _ = unpack_message(c.recv_bytes(V3_MAX_MESSAGE))
            except (EOFError, OSError):
                with cond:
                    failure.append("link_closed")
                    cond.notify()
                return
            now = time.perf_counter()
            with cond:
                for result in envelope["results"]:
                    if result["status"] != "scored":
                        failure.append(f"refused:{result.get('error_code')}")
                    r = result["request_id"]
                    lat.append(now - ready_at.pop(r))
                    rtt.append(now - sent_at.pop(r))
                    last_row[result["game_instance_id"]] = row_of.pop(r)
                    outstanding[0] -= 1
                    if now < end:
                        heapq.heappush(wake, (now + rng.lognormvariate(mu, sigma),
                                              result["game_instance_id"]))
                cond.notify()
                if stop[0] and outstanding[0] == 0:
                    return

    for link in links:
        threading.Thread(target=receiver, args=(link,), daemon=True).start()
    pending: list[tuple[str, float]] = []
    first: float | None = None
    packet_id = 0
    target = rng.randint(*batch) if batch else 4096
    while True:
        with cond:
            if failure:
                raise RuntimeError(f"node_client_failed:{failure[0]}")
            now = time.perf_counter()
            if stop[0] and now > end + DRAIN_S:
                raise RuntimeError(f"node_client_reply_missing:{outstanding[0]}")
            outcomes = []
            while wake and wake[0][0] <= now and now < end:  # no new rows after the end
                _, name = heapq.heappop(wake)
                if name in last_row:
                    outcomes.append((name, last_row.pop(name)))
                pending.append((name, now))
                if first is None:
                    first = now
            send = None
            if pending and first is not None and (now - first >= agg_ms / 1000
                                                  or len(pending) >= target):
                send, pending, first = pending, [], None
                target = rng.randint(*batch) if batch else 4096
            for name, (r, s_, floor) in outcomes:  # the env's step outcome, before its next row
                links[bound[name]].send_bytes(pack_message({
                    "schema": V3_SCHEMA, "kind": "step_outcome", "row_request_id": r,
                    "game_instance_id": name, "step_index": s_, "status": "completed",
                    "receipt_sha256": "0" * 64, "public_facts": {"floor": floor},
                    "terminal": None}))
            if send is None:
                if now >= end and not pending:
                    stop[0] = True
                    if outstanding[0] == 0:
                        break
                    cond.wait(0.05)
                    continue
                timeout = min([w[0] for w in wake[:1]]
                              + ([first + agg_ms / 1000] if first is not None else [])
                              + [now + 0.05]) - now
                cond.wait(max(timeout, 0))
                continue
            wires: list[tuple[list[Any], bytearray]] = [([], bytearray()) for _ in links]
            sent = time.perf_counter()
            for name, at in send:
                meta, data = rows[cursor[name] % len(rows)]
                cursor[name] += 1
                rid += 1
                wire, binary = wires[bound[name]]
                wire.append(wire_row(meta, data, rid, name, step[name], len(binary)))
                binary += data
                step[name] += 1
                ready_at[rid] = at
                sent_at[rid] = sent
                row_of[rid] = (rid, step[name] - 1, meta["floor"] or 0)
            outstanding[0] += len(send)
            packets[0] += 1
            packets[1] += len(send)
        packet_id += 1
        for link, (wire, binary) in zip(links, wires, strict=True):
            if wire:  # the node's packet, split by service as the aggregator binds games
                link.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "score_request",
                                              "packet_id": packet_id, "rows": wire}, binary))
    for name in names:  # free the games' segments in the assembler
        rid += 1
        links[bound[name]].send_bytes(pack_message({
            "schema": V3_SCHEMA, "kind": "episode_close", "request_id": rid,
            "game_instance_id": name, "reason": "stop",
            "last_step_index": step[name] - 1 if step[name] else None}))
    time.sleep(0.5)
    for link in links:
        link.close()
    out.put((len(lat), lat, rtt, packets, time.process_time()))


def probe_client(address: Any, authkey: bytes, encoder: dict[str, Any], compiler: dict[str, Any],
                 rows: list[Any], count: int, tag: str, out: Any) -> None:
    """Fixed probe: 64 games x count/64 steps over rows[0:count]; results by row index."""
    from stpd.p1.inference_service import V3_MAX_MESSAGE, V3_SCHEMA, pack_message, unpack_message
    games = 64
    names = [f"probe{tag}-{g}" for g in range(games)]
    c, rid = connect(address, authkey, encoder, compiler, "probe", names)
    got = {}
    for s in range(count // games):
        wire, binary, index_of = [], bytearray(), {}
        for g, name in enumerate(names):
            index = s * games + g
            meta, data = rows[index]
            rid += 1
            wire.append(wire_row(meta, data, rid, name, s, len(binary)))
            binary += data
            index_of[rid] = index
        c.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "score_request",
                                   "packet_id": s + 1, "rows": wire}, binary))
        envelope, _ = unpack_message(c.recv_bytes(V3_MAX_MESSAGE))
        for result in envelope["results"]:
            if result["status"] != "scored":
                raise RuntimeError(f"probe_refused:{result.get('error_code')}")
            got[index_of[result["request_id"]]] = (result["behavior_q"], result["selected_index"])
    c.close()
    out.put(got)


def closed_client(address: Any, authkey: bytes, encoder: dict[str, Any], compiler: dict[str, Any],
                  rows: list[Any], conns: int, games: int, seconds: float, offset: int,
                  out: Any) -> None:
    """`conns` links, each keeping `games` rows in flight (one per game); a game's next
    row leaves when its reply arrives, packed per reply like the node aggregator."""
    from stpd.p1.inference_service import V3_MAX_MESSAGE, V3_SCHEMA, pack_message, unpack_message
    latencies: list[float] = []
    done = [0]
    end = time.perf_counter() + seconds
    lock = threading.Lock()
    errors: list[str] = []

    def run(index: int) -> None:
        try:
            names = [f"g{offset}-{index}-{g}" for g in range(games)]
            c, rid = connect(address, authkey, encoder, compiler, f"b{offset}-{index}", names)
            step = dict.fromkeys(names, 0)
            cursor = (offset * 7919 + index * 104729) % len(rows)
            sent_at: dict[int, float] = {}
            packet = 0

            def send(batch: list[str]) -> None:
                nonlocal rid, packet, cursor
                wire, binary = [], bytearray()
                for name in batch:
                    meta, data = rows[cursor % len(rows)]
                    cursor += 1
                    rid += 1
                    wire.append(wire_row(meta, data, rid, name, step[name], len(binary)))
                    binary += data
                    step[name] += 1
                    sent_at[rid] = time.perf_counter()
                packet += 1
                c.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "score_request",
                                           "packet_id": packet, "rows": wire}, binary))

            send(names)
            mine, count = [], 0
            while True:
                envelope, _ = unpack_message(c.recv_bytes(V3_MAX_MESSAGE))
                now = time.perf_counter()
                ready = []
                for result in envelope["results"]:
                    if result["status"] != "scored":
                        raise RuntimeError(f"refused:{result.get('error_code')}")
                    mine.append(now - sent_at.pop(result["request_id"]))
                    count += 1
                    ready.append(result["game_instance_id"])
                if now < end:
                    send(ready)
                elif not sent_at:
                    break
            for name in names:
                rid += 1
                c.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "episode_close",
                                           "request_id": rid, "game_instance_id": name,
                                           "reason": "stop", "last_step_index": step[name] - 1}))
            c.close()
            with lock:
                latencies.extend(mine)
                done[0] += count
        except BaseException as error:  # noqa: BLE001 - reported loudly below
            with lock:
                errors.append(f"{type(error).__name__}: {str(error)[:200]}")

    started = time.perf_counter()
    threads = [threading.Thread(target=run, args=(i,)) for i in range(conns)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise RuntimeError(f"closed_client_failed:{errors[0]}")
    out.put((done[0], time.perf_counter() - started, latencies, time.process_time()))


def teardown(hosts: list[tuple[Any, Any]], wait: float = 30.0) -> None:
    """Close every service host still alive, then end every other child process.

    Graceful first (the host closes its service and that service's processes),
    then SIGTERM, then SIGKILL; a client process left by a failed phase ends too.
    A terminated host's own children (prepare/join/device) are not killed here:
    they exit on their pipes' EOF. Hosts are waited for one after another.
    """
    for process, conn in hosts:
        if process.is_alive():
            try:
                conn.send("close")
                if conn.poll(wait):
                    conn.recv()
            except (OSError, EOFError):
                pass  # the host is already gone; its exit code says why
            process.join(wait)
    for process in [p for p, _ in hosts] + mp.active_children():
        if process.is_alive():
            process.terminate()
            process.join(10)
        if process.is_alive():
            process.kill()
            process.join(5)


def collect(out: Any, procs: list[Any]) -> list[Any]:
    """One result per client process; a client that died loudly fails the bench."""
    import queue
    results: list[Any] = []
    while len(results) < len(procs):
        try:
            results.append(out.get(timeout=5))
        except queue.Empty:
            dead = [p.exitcode for p in procs if p.exitcode not in (None, 0)]
            if dead:
                raise RuntimeError(f"bench_client_failed:exitcode={dead[0]}") from None
    for p in procs:
        p.join()
    return results


def ticks(pid: int) -> float:
    try:
        with open(f"/proc/{pid}/stat") as handle:
            fields = handle.read().rsplit(")", 1)[1].split()
        return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")
    except OSError:
        return 0.0


def cpu_of(service: Any) -> dict[str, float]:
    return {"main": ticks(os.getpid()),
            "devices": ticks(service.device_process.pid),
            "joiners": sum(ticks(p.pid) for p in service.joiners),
            "workers": sum(ticks(w[0].pid) for w in service.workers)}


def profiled(name: str, fn: Any) -> Any:
    def wrapper(*a: Any, **k: Any) -> Any:
        t0 = time.thread_time()
        try:
            return fn(*a, **k)
        finally:
            c = PROFC.setdefault(name, [0.0, 0])
            c[0] += time.thread_time() - t0
            c[1] += 1
    return wrapper


def instrument(service: Any, assembler: Any) -> None:
    import pickle
    import types

    import stpd.p1.inference_service as svc
    import stpd.p1.rows_scorer as rs
    import stpd.p1.sampling as sampling
    svc.unpack_message = profiled("unpack_message", svc.unpack_message)
    svc.pack_message = profiled("pack_message", svc.pack_message)
    svc.pickle = types.SimpleNamespace(  # type: ignore[assignment]
        loads=profiled("pickle.loads", pickle.loads), dumps=profiled("pickle.dumps", pickle.dumps),
        HIGHEST_PROTOCOL=pickle.HIGHEST_PROTOCOL)
    sampling.distribution = profiled("distribution", sampling.distribution)
    sampling.draw = profiled("draw", sampling.draw)
    setattr(rs.RowsScorer, "rows", staticmethod(profiled("scorer_rows", rs.RowsScorer.rows)))  # noqa: B010
    for name in ("_score_request", "_finish_part", "_scored", "_complete", "_dispatch"):
        setattr(service, name, profiled(name, getattr(service, name)))
    for name in ("begin_row", "finish_row", "step_outcome"):
        setattr(assembler, name, profiled("asm." + name, getattr(assembler, name)))


def thread_profiling(profile: bool) -> None:
    """Record every later thread's own CPU; with `profile` also run it under cProfile."""
    import cProfile

    run = threading.Thread.run

    def profiled_run(self: threading.Thread) -> None:
        profiler = cProfile.Profile(time.thread_time) if profile else None
        entry = [self.name, profiler, 0.0]
        THREAD_PROFILES.append(entry)
        if profiler is not None:
            profiler.enable()
        try:
            run(self)
        finally:
            if profiler is not None:
                profiler.disable()
            entry[2] = time.thread_time()

    setattr(threading.Thread, "run", profiled_run)  # noqa: B010


def thread_report(rows: int, dump: str, top: int = 25) -> dict[str, Any]:
    """Per thread role: thread CPU per row; when profiled, self CPU of its top functions
    (and the merged stats written to `dump`.<role>.pstats when `dump` is set)."""
    import pstats

    roles: dict[str, list[Any]] = {}
    for entry in THREAD_PROFILES:
        name = entry[0]
        role = name.rstrip("0123456789").rstrip("-") if "rl-inference-4" in name else "other"
        roles.setdefault(role, []).append(entry)
    report: dict[str, Any] = {}
    per_row = 1e6 / max(rows, 1)
    for role, entries in sorted(roles.items()):
        here: dict[str, Any] = {"threads": len(entries), "cpu_us_per_row": round(
            sum(entry[2] for entry in entries) * per_row, 1)}
        profiles = [entry[1] for entry in entries if entry[1] is not None]
        if profiles:
            stats = pstats.Stats(profiles[0])
            for profile in profiles[1:]:
                stats.add(profile)
            if dump:
                stats.dump_stats(f"{dump}.{role}.pstats")
            table = stats.stats  # type: ignore[attr-defined]
            ranked = sorted(table.items(), key=lambda item: -item[1][2])[:top]
            here["profiled_us_per_row"] = round(sum(e[2] for e in table.values()) * per_row, 1)
            here["top"] = [[f"{func[0].rsplit('/', 1)[-1]}:{func[1]}:{func[2]}", entry[1],
                            round(entry[2] * per_row, 1), round(entry[3] * per_row, 1)]
                           for func, entry in ranked]
        report[role] = here
    return report


def hist_pct(counts: list[int]) -> list[Any]:
    """[p50, p95, mean, rows_over_500ms, max] in ms of a service trace histogram.

    Bins are 0.25 ms below 500 ms, then 10 ms up to 10 s (`trace_bin_edges`), so a
    p95 above 500 ms is reported as such, never capped (#478 review F3). A value in
    a bin is its midpoint; in the open last bin it is the string ">=9990". `max` is
    the upper edge of the highest non-empty bin (">=9990" when open); the service's
    exact running max is `trace_max_ms` in the snapshot.
    """
    from stpd.p1.inference_service import TRACE_FINE_BINS, trace_bin_edges

    total = sum(counts)
    if not total:
        return [None, None, None, 0, None]

    def mid(index: int) -> float | str:
        low, high = trace_bin_edges(index)
        return f">={low:g}" if high is None else round((low + high) / 2, 2)

    out: list[Any] = []
    for q in (0.5, 0.95):
        seen = 0
        for i, n in enumerate(counts):
            seen += n
            if seen >= q * total:
                out.append(mid(i))
                break
    # the open last bin counts at its lower edge: the mean is then a lower bound
    weighted = 0.0
    for i, n in enumerate(counts):
        if n:
            low, high = trace_bin_edges(i)
            weighted += n * (low if high is None else (low + high) / 2)
    out.append(round(weighted / total, 2))
    out.append(sum(counts[TRACE_FINE_BINS:]))
    top = max(i for i, n in enumerate(counts) if n)
    low, high = trace_bin_edges(top)
    out.append(f">={low:g}" if high is None else high)
    return out


def pct(values: list[float], q: float) -> float | None:
    return round(1000 * values[min(len(values) - 1, int(len(values) * q))], 1) if values else None


class StubForward:
    """Stands in for M-std on the device: random outputs of the real shapes."""

    training = False

    def __init__(self) -> None:
        self.rl4_heads = True
        self.calls = 0

    def parameters(self) -> Any:
        import torch

        return iter([torch.zeros(1)])

    def eval(self) -> StubForward:
        return self

    def requires_grad_(self, flag: bool) -> StubForward:
        return self

    def forward_prepared_rl4(self, group: Any, *, compile_rows: bool = False) -> Any:
        import torch

        mask = group.tensors["catalog_mask"]
        rows = len(group.rows)
        self.calls += 1
        generator = torch.Generator().manual_seed(self.calls)
        scores = torch.randn((rows, mask.shape[1]), generator=generator)
        scores = scores.masked_fill(mask[:rows].to("cpu", torch.bool), -torch.inf)
        valid = group.tensors["outcome_valid"][:rows].to("cpu", torch.bool)
        logits = torch.randn(valid.shape, generator=generator).masked_fill(~valid, -torch.inf)
        return scores, logits, torch.randn((rows,), generator=generator)


def build(device: str, spec: str) -> tuple[Any, Any, Any]:
    import torch

    from stpd.p1.frame_compiler import FrameCompiler
    from stpd.p1.input import V2_INPUT_SPEC, V2_R19_INPUT_SPEC
    from stpd.p1.model import MStd, MStdExecution
    from stpd.p1.vocabulary import public_vocabulary
    vocabulary = public_vocabulary(V2_INPUT_SPEC if spec == "v2" else V2_R19_INPUT_SPEC)
    torch.manual_seed(3)
    model = MStd(vocabulary, MStdExecution(implementation="experimental-packed-v1",
                                           microbatch_size=4, scene_attention_area=67108864,
                                           text_attention_area=67108864, cpu_threads=1))
    model.enable_rl4_heads(7)
    return vocabulary, model.to(device).eval().requires_grad_(False), FrameCompiler(vocabulary)


def service_host(conn: Any, index: int, config: dict[str, Any], device: str, spec: str,
                 cpus: list[int] | None, prof: bool, threads: str | None = None,
                 stub: bool = False) -> None:
    import sys

    from stpd.p1.inference_service import FastInferenceService
    from stpd.rl.fast_assembly import FastSegmentAssembler, MuHandoff
    if threads is not None:
        thread_profiling(threads != "cpu")
    config = dict(config)
    switch = config.pop("switch_interval_ms", None)
    if switch is not None:
        sys.setswitchinterval(switch / 1000)
    formats = {"segment_format": config.pop("segment_format")} if "segment_format" in config else {}
    try:
        vocabulary, model, compiler = build(device, spec)
        if stub:
            model = StubForward()
        assembler = FastSegmentAssembler(assembler_id=f"bench-{index}",
                                         compiler_identity=compiler.identity,
                                         emit=lambda segment: None, **formats)
        service = FastInferenceService(vocabulary, compiler.identity, assembler,
                                       training_generation="gen-1", address=("127.0.0.1", 0),
                                       prepare_cpus=cpus, max_rows=4096, device=device,
                                       env_sha256=ENV_SHA, **config)
        service.publish(model, MuHandoff("a" * 64, "gen-1", 1, 0))
        if prof:
            instrument(service, assembler)
        conn.send(("ready", service.address, service.auth))
    except BaseException as error:
        conn.send(("failed", f"{type(error).__name__}: {str(error)[:300]}", None))
        raise
    while True:
        command = conn.recv()
        if command == "snap":
            conn.send((json.loads(json.dumps(service.stats)), cpu_of(service),
                       {k: list(v) for k, v in PROFC.items()}))
        elif command == "close":
            report = service.close()
            summary = {k: report.get(k) for k in ("rows", "jobs", "batches", "audit_failures",
                                                  "refused", "device_dynamo")}
            if threads is not None:
                time.sleep(0.5)  # the link threads end once their clients are gone
                summary["threads"] = thread_report(int(report.get("rows") or 0),
                                                   "" if threads in ("cpu", "profile")
                                                   else f"{threads}.{index}")
            conn.send(summary)
            return


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("obs")
    parser.add_argument("out")
    parser.add_argument("--configs", required=True, help="JSON list of service configs")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--spec", default="v2_r19", choices=("v2_r19", "v2"))
    parser.add_argument("--max-obs", type=int, default=100000)
    parser.add_argument("--nodes", type=int, default=9)
    parser.add_argument("--loads", default="24,48,72,96", help="games per node per load point")
    parser.add_argument("--think-ms", type=float, default=25.0)
    parser.add_argument("--agg-ms", type=float, default=40.0)
    parser.add_argument("--batch", default="25,30", help="packet size range LO,HI; '' = window")
    parser.add_argument("--phase-s", type=float, default=15.0)
    parser.add_argument("--sat", default="64x32", help="closed-loop conns x games; '' = none")
    parser.add_argument("--mps", action="store_true", help="label: running under CUDA MPS")
    parser.add_argument("--bind", default="node", choices=("node", "game"),
                        help="node: node n -> service n %% K; game: service_for_game per game")
    parser.add_argument("--prof", action="store_true")
    parser.add_argument("--sat-warm-s", type=float, default=0.0,
                        help="an unscored saturation window of this many seconds before "
                             "the scored one (its compiles stay out of the score)")
    parser.add_argument("--client-cpus", type=int, default=0,
                        help="first N cores of this process's affinity run the clients only; "
                             "services (all their processes) get the rest; 0 = shared")
    parser.add_argument("--stub-forward", action="store_true",
                        help="random device outputs of the real shapes: host side alone")
    parser.add_argument("--threads", default=None,
                        help="'cpu': each main-process thread's own CPU per row at close; "
                             "'profile' or a path prefix: also cProfile every thread "
                             "(prefix: write <prefix>.<service>.<role>.pstats)")
    a = parser.parse_args(argv)
    faulthandler.register(signal.SIGUSR1, all_threads=True)

    import torch

    from stpd.p1.rows_encoder import RowsEncoder
    from stpd.p1.rows_encoder_wire import decode_row
    from stpd.p1.rows_scorer import RowsScorer
    from stpd.p1.sampling import distribution, draw

    log = open(a.out, "a")  # noqa: SIM115 - appended per line for the whole run

    def emit(d: dict[str, Any]) -> None:
        line = json.dumps({"t": round(time.time(), 1), **d})
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    vocabulary, model, compiler = build(a.device, a.spec)
    encoder = RowsEncoder(vocabulary)
    rows: list[tuple[dict[str, Any], bytes]] = []
    with open(a.obs, "rb") as handle:
        for line in list(handle)[:a.max_obs]:
            e = encoder.encode(line.rstrip(b"\n"))
            if e.catalog_len >= 1:
                rows.append(({"catalog_len": e.catalog_len, "catalog_sha256": e.catalog_sha256,
                              "observation_sha256": e.observation_sha256,
                              "in_combat": e.in_combat, "floor": e.floor,
                              "row_sha256": e.row_sha256}, e.row))
    catalog = [m["catalog_len"] for m, _ in rows]
    cpus = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None
    client_cpus = everything = cpus
    if a.client_cpus:
        if not cpus or a.client_cpus >= len(cpus):
            raise SystemExit("client_cpus_needs_a_larger_affinity")
        client_cpus, cpus = cpus[:a.client_cpus], cpus[a.client_cpus:]

    def side(mine: list[int] | None) -> None:
        """Spawned children inherit this process's affinity: switch it before each start."""
        if mine and sys.platform == "linux":
            os.sched_setaffinity(0, mine)
    batch = tuple(int(x) for x in a.batch.split(",")) if a.batch else None
    emit({"setup": True, "rows": len(rows),
          "cpus": len(everything) if everything else os.cpu_count(),
          "service_cpus": len(cpus) if cpus else None,
          "client_cpus": len(client_cpus) if a.client_cpus and client_cpus else "shared",
          "gpu": torch.cuda.get_device_name(0) if a.device == "cuda" else a.device,
          "torch": torch.__version__, "catalog_mean": round(sum(catalog) / len(catalog), 1),
          "mean_row_bytes": sum(len(r) for _, r in rows) // len(rows), "mps": a.mps,
          "load": {"nodes": a.nodes, "think_ms": a.think_ms, "agg_ms": a.agg_ms,
                   "batch": batch, "phase_s": a.phase_s}})
    probe_n = 384
    frames = [decode_row(r, validator=compiler, expected_sha256=m["row_sha256"])
              for m, r in rows[:probe_n]]
    floors = [m["floor"] or 0 for m, _ in rows[:probe_n]]
    fp32 = RowsScorer(model, bf16=False)
    reference = []
    for i in range(0, probe_n, 64):
        reference.extend(fp32.score(frames[i:i + 64], floors[i:i + 64]))
    if any(r is None for r in reference):
        raise SystemExit("fp32_reference_not_finite")
    ref_behavior = [distribution(r.scores, 1.0, 0.0)[1] for r in reference if r is not None]
    del model, fp32
    if a.device == "cuda":
        torch.cuda.empty_cache()

    context = mp.get_context("spawn")
    loads = [int(x) for x in a.loads.split(",") if x]
    failed: list[str] = []
    for config in json.loads(a.configs):
        config = dict(config)
        name = config.pop("name")
        if bool(config.pop("mps", a.mps)) != a.mps:
            continue
        sat = config.pop("sat", a.sat)
        services = config.pop("services", 1)
        loads_here = [int(x) for x in str(config.pop("loads", "")).split(",") if x] or loads
        workers = config.get("prepare_workers", 2)
        hosts: list[tuple[Any, Any]] = []
        try:
            started = time.perf_counter()
            side(cpus)
            for k in range(services):
                mine = (cpus[len(cpus) - workers * (k + 1):len(cpus) - workers * k]
                        if cpus and len(cpus) >= workers * services else None)
                parent, child = context.Pipe()
                p = context.Process(target=service_host,
                                    args=(child, k, config, a.device, a.spec, mine, a.prof,
                                          a.threads, a.stub_forward))
                p.start()
                hosts.append((p, parent))
            addresses = []
            for _, conn in hosts:
                state, address, auth = conn.recv()
                if state != "ready":
                    raise SystemExit(f"service_host_failed:{address}")
                addresses.append((address, auth))
            emit({"config": name, "services": services, "kwargs": config, "mps": a.mps,
                  "start_s": round(time.perf_counter() - started, 1)})

            def args_of(k: int) -> tuple[Any, ...]:
                address, auth = addresses[k % services]  # noqa: B023 - this config's services
                return (address, auth, encoder.identity, compiler.identity, rows)

            side(client_cpus)
            warm = time.perf_counter()
            worst: dict[str, Any] | None = None
            for k in range(0 if a.stub_forward else services):
                out = context.Queue()
                for w in range(2):  # the first pass warms (compile buckets), the second is scored
                    p = context.Process(target=probe_client,
                                        args=(*args_of(k), probe_n, f"{k}x{w}", out))
                    p.start()
                    got = collect(out, [p])[0]
                diffs = [max(abs(x - y) for x, y in zip(got[i][0], ref_behavior[i], strict=True))
                         for i in range(probe_n)]
                argmax = sum(max(range(len(got[i][0])), key=got[i][0].__getitem__)
                             == max(range(len(ref_behavior[i])), key=ref_behavior[i].__getitem__)
                             for i in range(probe_n))
                selected = sum(got[i][1] == draw(ref_behavior[i], SAMPLING["seed"],
                                                 f"probe{k}x1-{i % 64}-{i // 64}")
                               for i in range(probe_n))
                here = {"service": k, "rows": probe_n,
                        "max_behavior_q_abs_diff_vs_fp32": max(diffs),
                        "argmax_agree": argmax, "selected_agree": selected}
                if worst is None or (here["max_behavior_q_abs_diff_vs_fp32"]
                                     > worst["max_behavior_q_abs_diff_vs_fp32"]):
                    worst = here
            emit({"config": name, "warm_s": round(time.perf_counter() - warm, 1), "correct": worst})

            def snapshot() -> tuple[list[Any], float]:
                for _, conn in hosts:  # noqa: B023 - this config's hosts
                    conn.send("snap")
                return [conn.recv() for _, conn in hosts], time.perf_counter()  # noqa: B023

            def deltas(before: tuple[list[Any], float], wall: float) -> dict[str, Any]:
                after, _ = snapshot()
                forwards = rows_done = 0
                device_s = 0.0
                cpu: dict[str, float] = {}
                main_max = 0.0
                prof: dict[str, float] = {}
                busy = dict.fromkeys(("join_seconds", "stage_seconds", "finish_seconds",
                                      "dispatch_seconds"), 0.0)
                refused: dict[str, int] = {}
                trace: dict[str, list[int]] = {}
                dynamo: list[list[int]] = []  # per service: frames_ok, total - ok (eager)
                for (s0, c0, p0), (s1, c1, p1) in zip(before[0], after, strict=True):
                    for stage, counts in (s1.get("trace") or {}).items():
                        old = (s0.get("trace") or {}).get(stage, [0] * len(counts))
                        mine = trace.setdefault(stage, [0] * len(counts))
                        for i, (x, y) in enumerate(zip(counts, old, strict=True)):
                            mine[i] += x - y
                    d0, d1 = s0.get("device_dynamo") or {}, s1.get("device_dynamo") or {}
                    ok = d1.get("dynamo_frames_ok", 0) - d0.get("dynamo_frames_ok", 0)
                    dynamo.append([ok, d1.get("dynamo_frames_total", 0)
                                   - d0.get("dynamo_frames_total", 0) - ok])
                    forwards += s1["batches"] - s0["batches"]
                    rows_done += s1["rows"] - s0["rows"]
                    device_s += s1["device_seconds"] - s0["device_seconds"]
                    for k in busy:
                        busy[k] += s1[k] - s0[k]
                    for k in c1:
                        cpu[k] = cpu.get(k, 0.0) + c1[k] - c0[k]
                    main_max = max(main_max, c1["main"] - c0["main"])
                    for k, v in p1.items():
                        prof[k] = prof.get(k, 0.0) + v[0] - p0.get(k, [0.0, 0])[0]
                    for k, v in s1["refused"].items():
                        refused[k] = refused.get(k, 0) + v - s0["refused"].get(k, 0)
                per_row = max(rows_done, 1)
                result = {"forwards": forwards,
                          "rows_per_forward": round(rows_done / max(forwards, 1), 1),
                          "device_busy": round(device_s / wall, 3),
                          "busy": {k: round(v / wall, 3) for k, v in busy.items()},
                          "cpu_cores": {k: round(v / wall, 2) for k, v in cpu.items()},
                          "main_cores_max": round(main_max / wall, 2),
                          "cpu_us_per_row": {k: round(1e6 * v / per_row, 1)
                                             for k, v in cpu.items()},
                          "refused": {k: v for k, v in refused.items() if v},
                          "device_dynamo_window": dynamo,
                          "device_dynamo_window_fields": ["frames_ok", "frames_eager"]}
                if prof:
                    result["main_us_per_row"] = {k: round(1e6 * v / per_row, 1)
                                                 for k, v in sorted(prof.items())}
                if trace:
                    result["trace_ms"] = {stage: hist_pct(counts)
                                          for stage, counts in trace.items()}
                    result["trace_fields"] = ["p50", "p95", "mean", "rows_over_500ms",
                                              "max"]
                    peaks: dict[str, float] = {}
                    for s1, _, _ in after:
                        for stage, ms in (s1.get("trace_max_ms") or {}).items():
                            peaks[stage] = max(peaks.get(stage, 0.0), ms)
                    if peaks:
                        result["trace_max_ms_since_start"] = peaks
                return result

            for phase, slots in enumerate(loads_here):
                before = snapshot()
                out = context.Queue()
                clients = [context.Process(target=node_client, args=(
                    *args_of(n), slots, a.think_ms, a.agg_ms, batch, a.phase_s,
                    1000 * n + slots + 100000 * phase, out,
                    addresses if a.bind == "game" else None)) for n in range(a.nodes)]
                for p in clients:
                    p.start()
                results = collect(out, clients)
                wall = time.perf_counter() - before[1]
                lat = sorted(x for r in results for x in r[1])
                rtt = sorted(x for r in results for x in r[2])
                packets = sum(r[3][0] for r in results)
                emit({"config": name, "load": {"nodes": a.nodes, "slots_per_node": slots},
                      "rows_per_s": round(len(lat) / a.phase_s),
                      "W_p50_ms": pct(lat, 0.5), "W_p95_ms": pct(lat, 0.95),
                      "rtt_p50_ms": pct(rtt, 0.5), "rtt_p95_ms": pct(rtt, 0.95),
                      "rtt_p99_ms": pct(rtt, 0.99),
                      "rows_per_packet": round(sum(r[3][1] for r in results) / max(packets, 1), 1),
                      "client_cores": round(sum(r[4] for r in results) / wall, 2),
                      **deltas(before, wall)})
            if sat:
                conns, games = (int(x) for x in sat.split("x"))
                # An unscored saturation window first (--sat-warm-s): the services
                # compile the shapes this load really forms (large buckets) before
                # the scored window; each record carries its window's compile frames.
                windows = ([("sat_warm", a.sat_warm_s, 700)] if a.sat_warm_s > 0 else [])
                for label, seconds, base in [*windows, ("saturation", a.phase_s, 900)]:
                    before = snapshot()
                    out = context.Queue()
                    procs = 4 * services
                    clients = [context.Process(target=closed_client, args=(
                        *args_of(n), max(1, conns // procs), games, seconds, base + n, out))
                        for n in range(procs)]
                    for p in clients:
                        p.start()
                    results = collect(out, clients)
                    wall = max(r[1] for r in results)
                    lat = sorted(x for r in results for x in r[2])
                    emit({"config": name, label: sat, "window_s": seconds,
                          "in_flight_rows": conns * games,
                          "rows_per_s": round(sum(r[0] for r in results) / wall),
                          "p50_ms": pct(lat, 0.5), "p95_ms": pct(lat, 0.95),
                          **deltas(before, time.perf_counter() - before[1])})
            closed = []
            for _, conn in hosts:
                conn.send("close")
            for p, conn in hosts:
                # #478 review S1: a stuck close goes to teardown, never waits for the
                # job's timeout (job 1460356 idled 38 min).
                if not conn.poll(CLOSE_WAIT_S):
                    raise TimeoutError(f"service_close_timeout:{CLOSE_WAIT_S}s")
                closed.append(conn.recv())
                p.join(30)
            emit({"config": name, "closed": closed})
        except BaseException as error:  # noqa: BLE001 - reported, then the next config runs
            failed.append(name)
            emit({"config": name, "failed": f"{type(error).__name__}: {str(error)[:300]}"})
            if isinstance(error, KeyboardInterrupt):
                raise
        finally:
            # A failed phase must not leave this config's services holding the GPU
            # (job 1460356 hung 38 min on them): close, then terminate, then kill.
            teardown(hosts)
    if failed:
        raise SystemExit(f"bench_configs_failed:{failed}")


if __name__ == "__main__":
    main()
