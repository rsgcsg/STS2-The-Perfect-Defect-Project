"""Bench of the rl-inference-3 service under a production-shaped load (no game, no CP).

Diagnostic only: it never trains, publishes a checkpoint or touches a game. The
model is a random-init production-shape M-std; rows are real encoded observations
(an `obs.jsonl` of public observations). Each config runs K independent services
(`"services": K`, default 1), each in its own spawned host process with its own
main process, assembler (the real `FastSegmentAssembler`, emit discarded),
prepare workers and device process(es); node clients are split over the services
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
import threading
import time
from typing import Any

SAMPLING: dict[str, Any] = {
    "mode": "temperature_uniform", "seed": "bench-rl-inference-3", "arm_id": "control",
    "classes": {"combat": {"temperature": 1.0, "uniform_mix": 0.0},
                "noncombat": {"temperature": 1.0, "uniform_mix": 0.0}}}
ENV_SHA = "e" * 64
DRAIN_S = 60.0  # a reply missing this long after the end is a loud failure, not a hang
PROFC: dict[str, list[float]] = {}


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
        c.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "episode_open", "request_id": rid,
                                   "game_instance_id": name, "actor_id": "a",
                                   "sampling": SAMPLING, "single_choice": "score",
                                   "start": {"kind": "new_run"}}))
    return c, rid


def node_client(address: Any, authkey: bytes, encoder: dict[str, Any], compiler: dict[str, Any],
                rows: list[Any], slots: int, think_ms: float, agg_ms: float,
                batch: tuple[int, int] | None, seconds: float, offset: int, out: Any) -> None:
    from stpd.p1.inference_service import V3_MAX_MESSAGE, V3_SCHEMA, pack_message, unpack_message
    rng = random.Random(offset)
    sigma = 0.35
    mu = math.log(think_ms / 1000) - sigma * sigma / 2
    names = [f"n{offset}-{g}" for g in range(slots)]
    c, rid = connect(address, authkey, encoder, compiler, f"node-{offset}", names)
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

    def receiver() -> None:
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

    threading.Thread(target=receiver, daemon=True).start()
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
                c.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "step_outcome",
                                           "row_request_id": r, "game_instance_id": name,
                                           "step_index": s_, "status": "completed",
                                           "receipt_sha256": "0" * 64,
                                           "public_facts": {"floor": floor}, "terminal": None}))
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
            wire, binary = [], bytearray()
            sent = time.perf_counter()
            for name, at in send:
                meta, data = rows[cursor[name] % len(rows)]
                cursor[name] += 1
                rid += 1
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
        c.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "score_request",
                                   "packet_id": packet_id, "rows": wire}, binary))
    for name in names:  # free the games' segments in the assembler
        rid += 1
        c.send_bytes(pack_message({"schema": V3_SCHEMA, "kind": "episode_close", "request_id": rid,
                                   "game_instance_id": name, "reason": "stop",
                                   "last_step_index": step[name] - 1 if step[name] else None}))
    time.sleep(0.5)
    c.close()
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
            "devices": sum(ticks(p.pid) for p in service.device_processes),
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


def pct(values: list[float], q: float) -> float | None:
    return round(1000 * values[min(len(values) - 1, int(len(values) * q))], 1) if values else None


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
                 cpus: list[int] | None, prof: bool) -> None:
    from stpd.p1.inference_service import FastInferenceService
    from stpd.rl.fast_assembly import FastSegmentAssembler, MuHandoff
    try:
        vocabulary, model, compiler = build(device, spec)
        assembler = FastSegmentAssembler(assembler_id=f"bench-{index}",
                                         compiler_identity=compiler.identity,
                                         emit=lambda segment: None)
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
            conn.send({k: report.get(k) for k in ("rows", "jobs", "batches", "audit_failures",
                                                  "refused")})
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
    parser.add_argument("--prof", action="store_true")
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
    batch = tuple(int(x) for x in a.batch.split(",")) if a.batch else None
    emit({"setup": True, "rows": len(rows), "cpus": len(cpus) if cpus else os.cpu_count(),
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
    for config in json.loads(a.configs):
        config = dict(config)
        name = config.pop("name")
        if bool(config.pop("mps", a.mps)) != a.mps:
            continue
        sat = config.pop("sat", a.sat)
        services = config.pop("services", 1)
        loads_here = [int(x) for x in str(config.pop("loads", "")).split(",") if x] or loads
        workers = config.get("prepare_workers", 2)
        started = time.perf_counter()
        hosts = []
        for k in range(services):
            mine = (cpus[len(cpus) - workers * (k + 1):len(cpus) - workers * k]
                    if cpus and len(cpus) >= workers * services else None)
            parent, child = context.Pipe()
            p = context.Process(target=service_host,
                                args=(child, k, config, a.device, a.spec, mine, a.prof))
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

        warm = time.perf_counter()
        worst: dict[str, Any] | None = None
        for k in range(services):
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
            here = {"service": k, "rows": probe_n, "max_behavior_q_abs_diff_vs_fp32": max(diffs),
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
            for (s0, c0, p0), (s1, c1, p1) in zip(before[0], after, strict=True):
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
                      "cpu_us_per_row": {k: round(1e6 * v / per_row, 1) for k, v in cpu.items()},
                      "refused": {k: v for k, v in refused.items() if v}}
            if prof:
                result["main_us_per_row"] = {k: round(1e6 * v / per_row, 1)
                                             for k, v in sorted(prof.items())}
            return result

        for phase, slots in enumerate(loads_here):
            before = snapshot()
            out = context.Queue()
            clients = [context.Process(target=node_client, args=(
                *args_of(n), slots, a.think_ms, a.agg_ms, batch, a.phase_s,
                1000 * n + slots + 100000 * phase, out)) for n in range(a.nodes)]
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
            before = snapshot()
            out = context.Queue()
            procs = 4 * services
            clients = [context.Process(target=closed_client, args=(
                *args_of(n), max(1, conns // procs), games, a.phase_s, 900 + n, out))
                for n in range(procs)]
            for p in clients:
                p.start()
            results = collect(out, clients)
            wall = max(r[1] for r in results)
            lat = sorted(x for r in results for x in r[2])
            emit({"config": name, "saturation": sat, "in_flight_rows": conns * games,
                  "rows_per_s": round(sum(r[0] for r in results) / wall),
                  "p50_ms": pct(lat, 0.5), "p95_ms": pct(lat, 0.95),
                  **deltas(before, time.perf_counter() - before[1])})
        closed = []
        for p, conn in hosts:
            conn.send("close")
            closed.append(conn.recv())
            p.join(30)
        emit({"config": name, "closed": closed})


if __name__ == "__main__":
    main()
