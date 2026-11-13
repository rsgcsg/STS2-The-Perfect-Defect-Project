"""Env-node per-process CPU sampler (fast-chain harness). argv: procs_file out_path interval_s
Every interval: for each pid in the job cgroup, utime+stime (ticks) and context switches;
emits per-category deltas (pids alive at both samples) as one JSON line. Processes that
exit between samples lose at most one interval of CPU; the analyzer compares the sum with
the cgroup total and reports coverage.
"""
import json
import os
import sys
import time

procs_file, out_path, interval = sys.argv[1], sys.argv[2], float(sys.argv[3])
TICK = os.sysconf("SC_CLK_TCK")
me = os.getpid()


def category(pid, cache):
    if pid in cache:
        return cache[pid]
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        comm = open(f"/proc/{pid}/comm").read().strip()
    except OSError:
        return None
    if pid == me or "proc-sampler" in cmd:
        cat = "sampler"
    elif "chain-driver.mjs" in cmd:
        cat = "driver"
    elif "fast-env/process.js" in cmd:
        cat = "kernel"
    elif "chain-agg.py" in cmd:
        cat = "aggregator"
    elif "python" in comm or "python" in cmd.split(" ")[0]:
        cat = "encoder"  # spawned EncoderPool workers (the only other Python on this node)
    elif "dotnet" in comm or "dotnet" in cmd or "Sts2" in cmd or "managed" in cmd.lower():
        cat = "native"
    elif comm == "node":
        cat = "node_other"
    else:
        cat = "other:" + comm[:16]
    cache[pid] = cat
    return cat


def read(pid):
    try:
        stat = open(f"/proc/{pid}/stat").read()
        fields = stat[stat.rindex(")") + 2:].split()
        cpu = int(fields[11]) + int(fields[12])
        vol = nonvol = 0
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("voluntary_ctxt_switches"):
                vol = int(line.split()[1])
            elif line.startswith("nonvoluntary_ctxt_switches"):
                nonvol = int(line.split()[1])
        return cpu, vol, nonvol
    except (OSError, ValueError, IndexError):
        return None


cache, prev = {}, {}
with open(out_path, "a") as out:
    while True:
        try:
            pids = [int(x) for x in open(procs_file).read().split()]
        except OSError:
            pids = [int(p) for p in os.listdir("/proc") if p.isdigit()
                    and os.stat(f"/proc/{p}").st_uid == os.getuid()]
        now = time.time()
        cur, agg = {}, {}
        for pid in pids:
            value = read(pid)
            cat = category(pid, cache)
            if value is None or cat is None:
                continue
            cur[pid] = value
            if pid in prev:
                d = [a - b for a, b in zip(value, prev[pid])]
                slot = agg.setdefault(cat, [0, 0, 0, 0])
                slot[0] += d[0] * 1000 / TICK
                slot[1] += d[1]
                slot[2] += d[2]
            slot = agg.setdefault(cat, [0, 0, 0, 0])
            slot[3] += 1
        out.write(json.dumps({"t": int(now * 1000), "cat": agg}) + "\n")
        out.flush()
        prev = cur
        time.sleep(max(0.0, interval - (time.time() - now)))
