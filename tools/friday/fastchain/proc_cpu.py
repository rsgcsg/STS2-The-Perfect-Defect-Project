"""Per-process CPU sampler for the GPU job (coordinator 2026-11-12: list every
process's CPU on the GPU node). Every PERIOD s, for each pid in the job cgroup:
utime+stime delta as % of one core, RSS, and a role label from argv/env."""
import os
import sys
import time

cg, out, period = sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
tick = os.sysconf("SC_CLK_TCK"); prev = {}; t0 = time.time()
def pids():
    s = set()
    for root, _d, files in os.walk(cg):
        if "cgroup.procs" in files:
            try: s.update(int(x) for x in open(os.path.join(root, "cgroup.procs")).read().split())
            except OSError: pass
    return s
def role(pid):
    try: a = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
    except OSError: return "?"
    try: env = open(f"/proc/{pid}/environ", "rb").read()
    except OSError: env = b""
    argv = " ".join(x.decode(errors="replace") for x in a if x)
    if "roles.py learner" in argv: return "learner"
    if "roles.py service" in argv:
        k = [x for x in env.split(b"\0") if x.startswith(b"SVC_INDEX=")]
        return "service-" + (k[0][10:].decode() if k else "?")
    return argv[:70]
with open(out, "a", buffering=1) as f:
    while True:
        now = time.time(); cur = {}
        for p in pids():
            try:
                st = open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()
                cur[p] = (int(st[11]) + int(st[12]), int(st[1]), int(st[21]) * 4096)
            except (OSError, IndexError, ValueError): continue
        for p, (cpu, ppid, rss) in sorted(cur.items()):
            if p in prev:
                pct = 100.0 * (cpu - prev[p][0]) / tick / (now - prev[p][1])
                if pct >= 1.0:
                    f.write(f"{now:.0f} {p} {ppid} {pct:.0f} {rss >> 20}M {role(p)}\n")
        prev = {p: (c[0], now) for p, c in cur.items()}
        time.sleep(period)
