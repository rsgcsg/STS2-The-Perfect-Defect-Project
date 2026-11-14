"""Fast-chain GPU-side roles (port of the private fc_gpu.py; started by gpu.sh).

learner  the real SegmentLearner (binary dataplane, fast_chain, sgd_process), warm from
         the start package, local peer only (the gate's archive subtree is dropped: no R2),
         its control/get_mu listener on this node, and a Unix-socket Listener whose
         connections become `attach_fast_handoff` readers.
service  the real FastInferenceService + FastSegmentAssembler + SegmentHandoff
         (DurableSegmentWriter + ShmSegmentSender to the learner + instance labels), and
         the FastMuFollower on NodeMuSource(learner attachment).

Glue and counters only; every decision stays in the production components. Every
setting comes from RUN/effective-profile.json (the run profile) and RUN/job-env.sh
(site paths, layout); nothing here has a default of its own. With
`producer_identity = "placeholder"` the CPs carry a scratch producer and are void (probe
only); "checkout" uses the real source identity.

Fixed here, not knobs: device "cuda" for the services, the gate's learner-base.json and
dataplane.json as the base (minus gil_probe/lock_timing and the archive peer), the
local peer, R2 off, and `exit_code_on_failure=70` passed only when the source's
FastInferenceService accepts it (older builds: the service failure still writes STOP).

argv: learner|service RUN
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import learner_config  # noqa: E402

MODE, RUN = sys.argv[1], Path(sys.argv[2])
STOP = RUN / "STOP"
K = {name: knob["value"] for name, knob in
     json.loads((RUN / "effective-profile.json").read_text())["knobs"].items()}
SITE = os.environ  # FC_SITE_* from job-env.sh


def site(key: str) -> str:
    return SITE["FC_SITE_" + key.upper()]


def check_learner_cards() -> None:
    """The learner sees exactly its planned cards (c2a: it once landed on the inference card)."""
    seen = os.environ.get("CUDA_VISIBLE_DEVICES", "")
    planned, service = os.environ["FC_LEARNER_GPUS"], os.environ["FC_SERVICE_GPU"]
    shared = os.environ["FC_SHARED_CARD"] == "1"
    if seen != planned or (not shared and int(os.environ["FC_RANKS"]) == 1
                           and service in seen.split(",")):
        raise SystemExit(f"learner placement refused: sees {seen!r}, planned {planned!r}, "
                         f"inference card {service} (shared={shared})")
    print(f"learner placement ok: CUDA_VISIBLE_DEVICES={seen} device "
          f"{os.environ['FC_LEARNER_DEVICE']} ranks {os.environ['FC_RANKS']}", flush=True)


def check_prep_env(own: str, hosts: int) -> None:
    """Every prep host runs the same python env as this learner (python_env_sha256)."""
    shas = {f.name: f.read_text().strip() for f in (RUN / "prep-env").glob("*")} \
        if (RUN / "prep-env").exists() else {}
    bad = {name: sha for name, sha in shas.items() if sha != own}
    if len(shas) < hosts or bad:
        raise SystemExit(f"prep host env refused: {len(shas)}/{hosts} reported, "
                         f"different from {own[:12]}: {sorted(bad)}")


def stop_once(text: str) -> bool:
    """Write STOP only if no STOP exists: the first reason (a failure) is never overwritten
    by a later "learner ended" (review B2). True when this call wrote it."""
    try:
        with open(STOP, "x") as handle:
            handle.write(text)
        return True
    except FileExistsError:
        return False


def jline(path: Path, record: dict) -> None:
    with path.open("a") as handle:
        handle.write(json.dumps(record, default=str) + "\n")


def learner() -> None:
    import multiprocessing.connection as mpc

    import stpd.rl.segment_learner as module
    from spireagent.artifact_contracts import Producer
    from spireagent.workbench.global_evidence_store import publish_detached_p1_model
    from stpd.rl.learner import LearnerConfig
    from stpd.rl.segment_learner import SegmentLearner, SegmentLearnerIO

    check_learner_cards()
    # A git-archive source has no checkout: "placeholder" = a scratch producer identity
    # (CPs of these runs are void, probe only; preflight allows it only for --smoke or
    # --probe-only). "checkout" keeps the real source_identity (clean git checkout).
    if K["producer_identity"] == "placeholder":
        producer = Producer("fastchain-scratch", "a" * 40, "b" * 64)
        module.source_identity = lambda _root: producer
    jline(RUN / "learner-events.jsonl", {"t": time.time(), "kind": "harness_producer_identity",
                                         "mode": K["producer_identity"],
                                         "checkpoints_void": K["producer_identity"]
                                         == "placeholder"})
    gate = Path(site("gate_dir"))
    resume = os.environ.get("FC_RESUME_CHECKPOINT") or None
    window = None
    if os.environ.get("FC_CHAIN_ID"):  # a generation of a resume chain (supervise.py)
        cp_config = None
        if resume:
            # The CP's own deadline/budget decide which window the learner accepts; they
            # live only inside the CP payload, read with the learner's own reader.
            from types import SimpleNamespace

            from spireagent.rl_exchange import Exchange
            exchange = Exchange({"kind": "local", "directory": site("peer_dir")},
                                site("run_name"))
            restored = SegmentLearner._read_checkpoint(SimpleNamespace(exchange=exchange),
                                                       resume)
            cp_config = {k: restored.config.get(k) for k in ("deadline_at", "operating_budget")}
            del restored
        window = learner_config.window(
            os.environ["FC_CHAIN_ID"], int(os.environ["FC_CHAIN_TOTAL_SECONDS"]),
            float(os.environ["FC_CHAIN_DEADLINE"]), resume, cp_config)
        jline(RUN / "learner-events.jsonl", {"t": time.time(), "kind": "harness_window",
                                             "chain": os.environ["FC_CHAIN_ID"],
                                             "resume_checkpoint": resume,
                                             "checkpoint_window": cp_config, **window})
    base = learner_config.build(json.loads((gate / "learner-base.json").read_bytes()), K, {
        "run_name": site("run_name"), "peer_dir": site("peer_dir"),
        "start_package": site("start_package"), "wall_seconds": os.environ["FC_WALL"],
        "device": os.environ["FC_LEARNER_DEVICE"], "generation": os.environ["FC_GENERATION"],
        "warmup_artifact_id": Path(site("warmup_file")).read_text().strip(),
        "rendezvous_path": str(RUN / "inference.json"), "ranks": int(os.environ["FC_RANKS"]),
        # A resumed generation (supervisor): the newest valid CP of the previous one.
        "resume_checkpoint": resume, "window": window})
    ranks = int(os.environ["FC_RANKS"])
    work = RUN / "learner"
    work.mkdir(parents=True)
    (work / "learner.json").write_text(json.dumps(base))
    config = LearnerConfig.read(work / "learner.json")
    dataplane = json.loads((gate / "dataplane.json").read_bytes())
    for key in ("gil_probe", "lock_timing"):
        dataplane.pop(key, None)
    if K["prefetch_referenced_only"]:  # PR #520
        dataplane["prefetch_referenced_only"] = True
    if K["fast_batched_apply"]:  # PR #522
        dataplane["fast_batched_apply"] = True
    if K["shards"]:  # PR #523 data-plane shards
        dataplane["shards"] = K["shards"]
    if K["lock_timing"]:
        dataplane["lock_timing"] = True
    LIVE = Path(os.environ["TMPDIR"]) / "learner-live"  # node-local (gpu.sh checked the fs)
    source_bytes = K["source_gb"] * 1024**3
    dataplane.update({"lanes": K["lanes"], "root": str(LIVE), "source_slots": K["source_slots"],
                      "source_bytes": source_bytes, "prepare_source_bytes": source_bytes,
                      "wal_max_bytes": K["wal_max_gib"] * 1024**3, "fast_chain": True,
                      "sgd_process": True,
                      "preparation_workers": K["preparation_workers"],
                      "assembly_workers": K["assembly_workers"],
                      "prepared_batches": K["prepared_batches"],
                      "prepare_slots": K["prepare_slots"],
                      "persistent_pools": K["persistent_pools"],
                      "ahead_while_waiting": K["ahead_while_waiting"],
                      "sgd_stage_thread": K["sgd_stage_thread"],
                      "prepare_dispatch_workers": K["dispatch_workers"],
                      **({"fast_verify_workers": K["fast_verify_workers"],
                          "fast_verify_arena_slots": K["fast_verify_arena_slots"],
                          "fast_verify_slot_bytes": K["fast_verify_slot_bytes"]}
                         if K["fast_verify_workers"] else {}),
                      "sgd_start_seconds": float(K["sgd_start_seconds"])})
    # PR #525 staging options (preflight checked the build has them when any is on).
    for key in ("sgd_stage_stream_reuse", "sgd_stage_lean_tree"):
        if K[key]:
            dataplane[key] = True
    if K["sgd_verify_split"] and ranks > 1:  # only data-parallel ranks split the hash
        dataplane["sgd_verify_split"] = True
    if K["sgd_verify_threads"] != 4:  # 4 = sgd_process.VERIFY_THREADS
        dataplane["sgd_verify_threads"] = K["sgd_verify_threads"]
    if K["sgd_switch_interval_seconds"]:
        dataplane["sgd_switch_interval_seconds"] = float(K["sgd_switch_interval_seconds"])
    # Compile warm-up (#526 warmup-cover picks the dumps; preflight checked they exist):
    # every graph the run will need compiles before the first online step.
    if K["levers"] and K["compile_warmup"]:
        dataplane["sgd_compile"] = {
            "warmup": json.loads(site("compile_warmup_dumps")),
            "recompile_limit": K["compile_recompile_limit"]}
    if K["release_trained_segments"]:  # #529: delete trained originals once a CP covers them
        dataplane["release_trained_segments"] = True
    if K["sgd_gc_freeze"]:  # #525: one collect + gc.freeze after the first dataset
        dataplane["sgd_gc_freeze"] = True
    if K["sgd_step_probe"]:  # #525: gc seconds/collections per step (diagnostics)
        dataplane["sgd_step_probe"] = True
    if K["shards_adopt_unsharded_checkpoint"]:  # #523 a437612d, first sharded start only
        dataplane["shards_adopt_unsharded_checkpoint"] = True
    if ranks > 1:
        dataplane["sgd_data_parallel"] = {"world": ranks, "backend": "nccl",
                                          "timeout_seconds": K["data_parallel_timeout_seconds"]}
        dataplane["sgd_cpus"] = sorted(os.sched_getaffinity(0))[:K["sgd_cpus"]]
    # Remote preparation hosts (PR #491/#496): the prep jobs start only after this GPU job
    # started (--dependency=after), so wait here for every host's RUN/prep-ready/ready-<n>
    # (their queue time + start), all of them, never fewer; a refusing host ends the wait.
    if K["remote_prepare"]:
        from stpd.p1.inference_service import python_env_sha256

        want, ready = int(os.environ["FC_PREP_HOSTS"]), RUN / "prep-ready"
        deadline = time.time() + float(K["remote_prepare_wait_seconds"])
        while len(list(ready.glob("ready-*"))) < want:
            refused = sorted(f for f in (RUN / "prep-refused").glob("*")
                             if not f.name.endswith(".tmp"))
            if refused:
                raise SystemExit("remote prepare host refused: "
                                 + "; ".join(f.read_text().strip() for f in refused))
            if time.time() > deadline:
                raise SystemExit("remote prepare hosts not ready: "
                                 f"{len(list(ready.glob('ready-*')))}/{want}")
            time.sleep(2)
        check_prep_env(python_env_sha256(), want)
        endpoints = sorted(f.read_text().strip() for f in ready.glob("ready-*"))
        dataplane["remote_prepare"] = {
            "endpoints": endpoints, "connections": K["remote_prepare_connections"],
            "codec": K["remote_prepare_codec"],
            "timeout_seconds": float(K["remote_prepare_timeout_seconds"]),
            **({"client_processes": K["remote_prepare_client_processes"]}
               if K["remote_prepare_client_processes"] else {})}
        jline(RUN / "learner-events.jsonl", {"t": time.time(), "kind": "harness_remote_prepare",
                                             **dataplane["remote_prepare"]})
    # h100i (playbook 11.4 item 2): the actual speed switches this learner runs with.
    perf = json.loads((work / "learner.json").read_bytes()).get("performance") or {}
    eff = {"t": time.time(), "kind": "harness_effective_config",
           "device": json.loads((work / "learner.json").read_bytes()).get("device"),
           "threads": json.loads((work / "learner.json").read_bytes()).get("threads"),
           "performance": perf,
           "dataplane": {k: dataplane.get(k) for k in (
               "sgd_process", "sgd_stage_thread", "sgd_data_parallel", "sgd_cpus", "fast_chain",
               "prepared_batches", "fast_verify_workers", "preparation_workers",
               "assembly_workers", "persistent_pools", "shards", "prefetch_referenced_only",
               "fast_batched_apply", "source_bytes", "ahead_while_waiting",
               "sgd_stage_stream_reuse", "sgd_stage_lean_tree", "sgd_verify_split",
               "sgd_verify_threads", "sgd_switch_interval_seconds",
               "shards_adopt_unsharded_checkpoint", "sgd_compile", "sgd_gc_freeze",
               "sgd_step_probe", "release_trained_segments")},
           "diagnostics_every": K["diagnostics_every"],
           "remote_prepare": dataplane.get("remote_prepare"),
           "python_path": os.environ.get("PYTHONPATH")}
    jline(RUN / "learner-events.jsonl", eff)
    print(json.dumps({"harness_effective_config": eff}), flush=True)
    # Playbook §11.4 item 2 as code: the switches the learner actually reads must be the
    # profile's (a gate default must not silently win over the profile).
    want = {"prepared_compile": True, "prepared_kernels": K["prepared_kernels"],
            "prepared_bucket_rows": K["prepared_bucket_rows"]} if K["levers"] else {}
    diff = {key: (perf.get(key), value) for key, value in want.items() if perf.get(key) != value}
    if eff["device"] != os.environ["FC_LEARNER_DEVICE"]:
        diff["device"] = (eff["device"], os.environ["FC_LEARNER_DEVICE"])
    if diff:
        raise SystemExit(f"effective speed switches differ from the profile: {diff}")
    LIVE.mkdir(parents=True)  # node-local: the binary dataplane refuses NFS
    owner = SegmentLearner(config, work / "owner", SegmentLearnerIO(
        None, publish_detached_p1_model, dataplane=dataplane))
    events = RUN / "learner-events.jsonl"
    original = owner._event

    # c3c17: the event log no longer opens/appends/closes an NFS file on the learner
    # main thread per step (each syscall gave up the GIL into the convoy). The record
    # is serialised here (same content, same moment) and written by one thread.
    import queue as _queue
    event_lines: _queue.SimpleQueue[str | None] = _queue.SimpleQueue()

    def event_writer() -> None:
        with events.open("a", buffering=1 << 20) as handle:
            while True:
                line = event_lines.get()
                if line is None:
                    return
                handle.write(line)
                while True:
                    try:
                        line = event_lines.get_nowait()
                    except _queue.Empty:
                        break
                    if line is None:
                        return
                    handle.write(line)
                handle.flush()

    threading.Thread(target=event_writer, name="harness-event-writer", daemon=True).start()

    def capture(kind: str, **fields) -> None:  # type: ignore[no-untyped-def]
        record = {"t": time.time(), "kind": kind,
                  **{k: v for k, v in fields.items() if k != "_build"}}
        event_lines.put(json.dumps(record, default=str) + "\n")
        original(kind, **fields)

    owner._event = capture  # type: ignore[method-assign]
    auth = bytes.fromhex(os.environ["STPD_P1_LOCAL_INFERENCE_AUTH"])
    listener = mpc.Listener(str(RUN / "handoff.sock"), family="AF_UNIX", authkey=auth)
    attached: list = []

    def accept() -> None:
        while True:
            try:
                connection = listener.accept()
            except OSError:
                return
            attached.append(connection)  # kept open until the learner stopped
            owner.attach_fast_handoff(connection)
            jline(events, {"t": time.time(), "kind": "harness_handoff_attached",
                           "count": len(attached)})

    threading.Thread(target=accept, daemon=True).start()

    def status() -> None:
        while not STOP.exists():
            time.sleep(10)
            if STOP.exists() and STOP.read_text().startswith(("service", "disk stop")):
                # The service failed (or the disk watermark drained the env side): the
                # learner stops now, never waits for data until its deadline. A service's
                # hand-off EOF also fences input.
                (work / "owner" / "STOP").write_text("service stopped")
            try:
                jline(RUN / "learner-status.jsonl", {
                    "t": time.time(), "version": owner.version, "updates": owner.updates,
                    "queued_rows": owner.queue.occupied, "timing": dict(owner.timing),
                    "fenced": owner.input_fenced, **owner.fast_status(),
                    **({"control_lock_timing": owner.control_lock.snapshot()}
                       if hasattr(owner.control_lock, "snapshot") else {})})
            except Exception as error:  # noqa: BLE001 - a status read never stops the run
                jline(RUN / "learner-status.jsonl", {"t": time.time(), "error": repr(error)})

    threading.Thread(target=status, daemon=True).start()
    def profile() -> None:
        # Stack sampling of every learner thread, in-process: ptrace is blocked on
        # Friday and faulthandler sampling crashed processes (h100e/h100f).
        # sys._current_frames under the GIL is safe; 5 Hz, top stacks every 60 s.
        import collections
        import traceback
        counts: dict = collections.defaultdict(collections.Counter)
        last = time.time()
        tick = os.sysconf("SC_CLK_TCK")

        def thread_cpu() -> dict:
            # Per-thread CPU seconds from /proc (utime+stime), keyed by Python name.
            out: dict = collections.Counter()
            for t in threading.enumerate():
                try:
                    f = Path(f"/proc/self/task/{t.native_id}/stat").read_text()
                    v = f[f.rindex(")") + 2:].split()
                    out[t.name] += (int(v[11]) + int(v[12])) / tick
                except (OSError, ValueError, TypeError):
                    pass
            return out
        cpu_last = thread_cpu()
        while not STOP.exists():
            time.sleep(0.2)
            names = {t.ident: t.name for t in threading.enumerate()}
            for ident, frame in sys._current_frames().items():
                name = names.get(ident, str(ident))
                if name == "rl-profile":
                    continue
                stack = traceback.extract_stack(frame)[-6:]
                counts[name][" < ".join(f"{Path(f.filename).name}:{f.name}:{f.lineno}"
                                        for f in reversed(stack))] += 1
            if time.time() - last >= 60:
                last = time.time()
                top = {name: c.most_common(6) for name, c in counts.items()
                       if name == "MainThread" or sum(c.values()) >= 50}
                cpu_now = thread_cpu()
                cpu = {n: round(cpu_now[n] - cpu_last.get(n, 0.0), 2) for n in cpu_now
                       if cpu_now[n] - cpu_last.get(n, 0.0) >= 0.05}
                cpu_last = cpu_now
                jline(RUN / "learner-profile.jsonl", {"t": last, "samples": {
                    name: sum(c.values()) for name, c in counts.items()}, "top": top,
                    "thread_cpu_s": cpu})
                counts.clear()

    threading.Thread(target=profile, daemon=True, name="rl-profile").start()
    (RUN / "learner.ready").write_text(json.dumps({"generation": owner.generation}))
    failure = summary = trace = None
    try:
        summary = owner.run()
    except BaseException as error:  # noqa: BLE001 - reported
        failure = f"{type(error).__name__}: {getattr(error, 'code', '')} {error}"
        import traceback
        trace = "".join(traceback.format_exception(error))  # keeps the __cause__ chain
        print(trace, file=sys.stderr, flush=True)
    finally:
        report = {"failure": failure, "summary": summary, "timing": dict(owner.timing),
                  "input_failure": owner.input_failure,
                  "failure_trace": trace if failure else None,
                  "version": owner.version, "updates": owner.updates,
                  "fast": owner.fast_status(), "attached": len(attached)}
        try:
            owner.close()
        except BaseException as error:  # noqa: BLE001
            report["close_failure"] = repr(error)
        (RUN / "learner-report.json").write_text(json.dumps(report, default=str, indent=1))
        stop_once(f"learner failure {failure}" if failure else "learner ended")
        listener.close()
    return failure


def service() -> None:
    import multiprocessing.connection as mpc

    import torch

    from stpd.p1.fast_mu_follower import FastMuFollower
    from stpd.p1.frame_compiler import FrameCompiler
    from stpd.p1.inference_service import FastInferenceService, NodeMuSource, python_env_sha256
    from stpd.p1.input import V2_R19_INPUT_SPEC
    from stpd.p1.vocabulary import public_vocabulary
    from stpd.rl.fast_assembly import FastSegmentAssembler
    from stpd.rl.fast_handoff import DurableSegmentWriter, SegmentHandoff, ShmSegmentSender
    from stpd.rl.fast_source import build_instance_labels

    index = int(os.environ.get("SVC_INDEX", "0"))  # s1d: K services on one card (MPS)
    tag = f"-{index}"
    while not (RUN / "learner.ready").exists():
        if STOP.exists():
            return
        time.sleep(1)
    vocabulary = public_vocabulary(V2_R19_INPUT_SPEC)
    compiler = FrameCompiler(vocabulary)
    # s1f: the node mu cache takes flock locks on every read (refresh.lock, readers.lock);
    # NodeMuCache assumes node-local scratch, so keep it off NFS ($TMPDIR is node-local).
    # s1g/h100a: NodeMuSource._cache() re-reads the learner attachment on every latest()
    # and load(); with it on NFS a follower thread sat in D state in nfs open
    # (nfs_set_open_stateid_locked) for > 120 s -> MU_FOLLOWER_STALLED (stage load; s1f's
    # stall was the same open, before read.lock). The attachment is written once per run
    # (it is replaced only on a learner restore, which this harness never does: a fence
    # fails the run), so give each service a node-local copy taken after learner.ready.
    local = Path(os.environ["TMPDIR"]) / f"inference{tag}.json"
    pending = local.with_suffix(".pending")
    pending.write_bytes((RUN / "inference.json").read_bytes())
    pending.replace(local)
    source = NodeMuSource(local, Path(os.environ["TMPDIR"]) / f"mu-cache{tag}", interval=2)
    first = source.latest()
    holder: dict = {}
    kwargs = dict(K["service_kwargs"])
    formats = ({"segment_format": kwargs.pop("segment_format")}
               if "segment_format" in kwargs else {})
    # #529 R7 audit games: every mu such a game's segments bind is archived by sha256
    # (run dir, `mu_archive_gb` split over the services; full = that row refused loudly),
    # read from this node's mu cache under its reader lock.
    audit: dict = {}
    if K["r7_games_per_hour"] > 0:
        from stpd.rl.fast_mu_pack import local_mu_archive, node_cache_weights

        audit["mu_archive"] = local_mu_archive(
            RUN / f"mu-archive{tag}",
            max_bytes=int(K["mu_archive_gb"] * 1e9 / K["services"]))
    assembler = FastSegmentAssembler(
        assembler_id=f"fc{tag}", compiler_identity=compiler.identity, **formats, **audit,
        emit=lambda segment: holder["h"].submit(segment),
        on_closed=lambda item: holder["h"].submit_closed(item))
    auth = bytes.fromhex(os.environ["STPD_P1_LOCAL_INFERENCE_AUTH"])
    connection = mpc.Client(str(RUN / "handoff.sock"), family="AF_UNIX", authkey=auth)
    sender = ShmSegmentSender(connection, assembler_id=assembler.instance_id)
    holder["h"] = SegmentHandoff(
        DurableSegmentWriter(RUN / f"segments{tag}", instance_id=assembler.instance_id), sender,
        labeler=build_instance_labels,
        write_workers=K["handoff_write_workers"])
    workers = K["service_prepare_workers"]
    cpus = sorted(os.sched_getaffinity(0))
    mine = cpus[len(cpus) - workers * (index + 1):len(cpus) - workers * index] or cpus[-workers:]
    import inspect
    exit_option = ({"exit_code_on_failure": 70} if "exit_code_on_failure" in
                   inspect.signature(FastInferenceService).parameters else {})
    service = FastInferenceService(
        vocabulary, compiler.identity, assembler,
        training_generation=first["training_generation"], prepare_workers=workers,
        prepare_cpus=mine, max_rows=K["service_max_rows"], device="cuda",
        failure_dir=str(RUN / f"failures{tag}"),
        **{"window_ms": float(K["service_window_ms"]),
           "max_job_rows": K["service_max_job_rows"],
           "compile": K["service_compile"], **kwargs}, **exit_option)
    weights = ({"weights": lambda identity: node_cache_weights(source._cache(refresh=False),
                                                               identity)} if audit else {})
    follower = FastMuFollower(source, service, interval=float(K["mu_poll_seconds"]), **weights)
    if not follower.step():
        raise SystemExit("first mu refused")
    follower.start()
    # s1g: service 4's mu load (normally 0.2-0.6 s) passed the 120 s limit, service 1's
    # took 18.6 s. Diagnostics only: once a stage is held > STALL_DUMP_S, dump every
    # thread's stack into this service's log (one dump per stage episode), so the next
    # stall names the blocking call. Limits and the watchdog stay as merged (#488).
    def stall_dumper() -> None:
        import faulthandler
        limit, dumped = float(K["stall_dump_seconds"]), None
        while not follower.stopped():
            stage, began = follower.mark
            held = time.monotonic() - began if began is not None else 0.0
            if stage and began is not None and held > limit and dumped != (stage, began):
                dumped = (stage, began)
                print(json.dumps({"stall_dump": stage, "held_seconds": round(held, 1),
                                  "loadavg": os.getloadavg()}), flush=True)
                faulthandler.dump_traceback(file=sys.stdout, all_threads=True)
                sys.stdout.flush()
            time.sleep(2)
    threading.Thread(target=stall_dumper, name="stall-dumper", daemon=True).start()
    info = {"host": service.address[0], "port": service.address[1],
            "authkey_hex": service.auth.hex(), "env_mark": python_env_sha256(),
            "compiler_identity": compiler.identity}
    temporary = RUN / f".service{tag}.json.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(info, handle)
    os.rename(temporary, RUN / f"service{tag}.json")
    print(json.dumps({"ready": True, "index": index, "device": torch.cuda.get_device_name(0),
                      "mu": follower.offered, "kwargs": kwargs}), flush=True)
    log = RUN / f"service-status{tag}.jsonl"
    error = None
    # Orderly end: the services drain (finish(), end message, follower stopped) before the
    # learner's wall closes the mu source; otherwise the follower sees the source gone and
    # the service fails loud (smoke2). Deadline = learner ready + WALL - DRAIN_S.
    drain_at = ((RUN / "learner.ready").stat().st_mtime + float(os.environ["FC_WALL"])
                - float(K["drain_seconds"]))
    # s1f: env nodes join and leave on their own; DRAIN tells them to stop their drivers
    # cleanly ENV_DRAIN_S before the services drain, so a late node ends with the GPU.
    env_drain_at = drain_at - float(K["env_drain_seconds"])
    if os.environ.get("SVC_INDEX", "0") == "0" and not (RUN / "env-drain-at").exists():
        (RUN / "env-drain-at").write_text(f"{env_drain_at:.0f}\n")  # launcher: late env join check
    while not STOP.exists() and time.time() < drain_at:
        time.sleep(5)
        if time.time() >= env_drain_at and not (RUN / "DRAIN").exists():
            (RUN / "DRAIN").write_text(f"{time.time()}\n")
        try:
            follower.check()
            handoff_error = holder["h"].error
            if handoff_error is not None:
                raise handoff_error
        except BaseException as caught:  # noqa: BLE001 - stops the chain loudly
            error = repr(caught)
            stop_once("service failure " + error)
        jline(log, {"t": time.time(), "service": {k: v for k, v in service.stats.items()
                                                  if isinstance(v, (int, float))},
                    "follower": follower.status(), "handoff": dict(holder["h"].counters),
                    "handoff_q": holder["h"].queue.qsize(),  # c3c: SegmentHandoff queue depth
                    "assembler": {k: v for k, v in assembler.report().items()
                                  if k not in ("referenced_mu",)}})
    follower.stop()
    report = {"error": error, "service": {k: v for k, v in service.stats.items()
                                          if isinstance(v, (int, float, str, dict))},
              "follower": follower.status(), "mu_history": follower.history,
              "assembler": assembler.report()}
    closed = service.close()
    report["close"] = closed if isinstance(closed, dict) else repr(closed)
    # #482: only a clean run says the orderly end; a failure releases only (learner fences).
    if error is None:
        holder["h"].finish()
    else:
        holder["h"].close()
    report["handoff"] = holder["h"].counters
    (RUN / f"service-report{tag}.json").write_text(json.dumps(report, default=str, indent=1))
    if error is not None:
        raise SystemExit(70)  # a failed service never exits 0 (review B2)


if __name__ == "__main__":
    if MODE == "learner":
        try:
            failed = learner()
        except BaseException as error:  # noqa: BLE001 - setup failed before owner.run()
            # Review D2: a setup failure (an unrestorable CP, no prep hosts, a refused
            # config) writes a report too, so the supervisor stops instead of resuming.
            import traceback
            failed = f"setup {type(error).__name__}: {getattr(error, 'code', '')} {error}"
            if not (RUN / "learner-report.json").exists():
                (RUN / "learner-report.json").write_text(json.dumps({
                    "failure": failed, "setup_failure": True, "summary": None,
                    "failure_trace": traceback.format_exc()}, default=str, indent=1))
            stop_once(f"learner failure {failed}")
            traceback.print_exc()
        if failed is not None:
            sys.exit(6)
    else:
        try:
            service()
        except BaseException:  # noqa: BLE001 - any service failure ends the process loudly
            import traceback
            traceback.print_exc()
            stop_once("service failure (see service-*.log)")
            sys.stderr.flush()
            os._exit(70)
