#!/bin/bash
# Fast-chain GPU job (submitted by launch.py; port of the private fc-gpu3.sh).
# One job holds every card of the layout: card FC_SERVICE_CARD runs the K inference
# services under MPS; the learner (SegmentLearner, sgd_process) sees FC_LEARNER_VISIBLE
# and uses FC_LEARNER_DEVICE. All values come from $FC_RUN/job-env.sh (run profile +
# site profile + layout); this script holds no defaults of its own.
umask 077
set +u; source /etc/profile >/dev/null 2>&1
[ -n "$FC_RUN" ] || { echo "FC_RUN unset"; exit 2; }
source "$FC_RUN/job-env.sh"
module load "$FC_SITE_MODULE_LOAD" >/dev/null 2>&1; set -u
RUN=$FC_RUN; H=$FC_HARNESS; PY=$FC_SITE_PYTHON
export PYTHONPATH=$FC_SITE_SRC_DIR/python:$FC_SITE_LZ4_PATH PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1
export LD_LIBRARY_PATH=$FC_SITE_LIB_PATH:${LD_LIBRARY_PATH:-}
export TORCHINDUCTOR_CACHE_DIR=$FC_SITE_INDUCTOR_CACHE TMPDIR=$FC_SITE_GPU_LOCAL_TMP/fc-$SLURM_JOB_ID
export STPD_P1_LOCAL_INFERENCE_AUTH=$(cat "$FC_SITE_INFERENCE_AUTH_FILE")
export STPD_REMOTE_PREPARE_AUTH=$(cat "$FC_SITE_PREPARE_AUTH_FILE")
mkdir -p $TMPDIR $TORCHINDUCTOR_CACHE_DIR
# /dev/shm hand-off blocks left by a failed service exit: clear this user's blocks, but
# only when no other job of this user runs on this node.
shm_clean() { [ "$(squeue -u $USER -w $(hostname -s) -h -o %i | grep -vc "^$SLURM_JOB_ID$")" = 0 ] && \
  echo "shm cleanup ($1): $(find /dev/shm -maxdepth 1 -user $USER -name 'psm_*' -print -delete | wc -l) blocks"; }
shm_clean start
ulimit -n 65536 2>/dev/null || ulimit -n $(ulimit -Hn)
echo "node=$(hostname -s) start=$(date -Is) job=$SLURM_JOB_ID run=$FC_RUNTAG src=$(cat $FC_SITE_SRC_DIR/REV) cpus=$(taskset -pc $$ | sed 's/.*: //')"
echo "layout: $FC_NAME services=$FC_SERVICES -> card $FC_SERVICE_CARD; learner sees $FC_LEARNER_VISIBLE device $FC_LEARNER_DEVICE ranks $FC_RANKS"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
# Start checks on this node (refuse = no run): bz2, lz4, python_env_sha256, local tmp.
stop_once() { ( set -o noclobber; echo "$1" > "$RUN/STOP" ) 2>/dev/null; }  # never overwrite a reason
$PY $H/preflight.py job-gpu --run $RUN --path $TMPDIR || { stop_once "service failure: gpu preflight"; exit 9; }
JG=(${CUDA_VISIBLE_DEVICES//,/ })
[ ${#JG[@]} -eq $FC_CARDS ] || { echo "job gpus ${CUDA_VISIBLE_DEVICES} != $FC_CARDS planned"; stop_once "service failure: gpus"; exit 7; }
SVCG=${JG[$FC_SERVICE_CARD]}
LG=$(for i in ${FC_LEARNER_VISIBLE//,/ }; do echo -n "${JG[$i]},"; done); LG=${LG%,}
echo "placement: services on physical card $SVCG; learner CUDA_VISIBLE_DEVICES=$LG device $FC_LEARNER_DEVICE" | tee $RUN/placement.txt
export FC_LEARNER_GPUS=$LG FC_SERVICE_GPU=$SVCG
export CUDA_MPS_PIPE_DIRECTORY=$TMPDIR/mps-pipe CUDA_MPS_LOG_DIRECTORY=$TMPDIR/mps-log
mkdir -p $CUDA_MPS_PIPE_DIRECTORY $CUDA_MPS_LOG_DIRECTORY
# The services share the inference card under MPS; without it the measured setup changes
# silently (review S3): refuse.
CUDA_VISIBLE_DEVICES=$SVCG nvidia-cuda-mps-control -d || { stop_once "service failure: mps daemon did not start"; exit 8; }
sleep 1; echo get_server_list | nvidia-cuda-mps-control >/dev/null 2>&1 || { stop_once "service failure: mps control not answering"; exit 8; }
echo "mps daemon started (card $SVCG only)"
BG=()
trap 'kill "${BG[@]}" 2>/dev/null; echo quit | nvidia-cuda-mps-control 2>/dev/null' EXIT
CPUS=($($PY -c "import os; print(' '.join(map(str, sorted(os.sched_getaffinity(0)))))"))
LC=$FC_LEARNER_CORES
LCPU=$(IFS=,; echo "${CPUS[*]:0:$LC}"); SCPU=$(IFS=,; echo "${CPUS[*]:$LC}")
( while sleep 2; do nvidia-smi --query-gpu=index,utilization.gpu,memory.used --format=csv,noheader,nounits | sed "s/^/$(date +%s%3N), /"; done ) > $RUN/gpu.csv 2>/dev/null &
GS=$!; BG+=($GS)
CG=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); while [ "$CG" != /sys/fs/cgroup ] && [[ "$(basename $CG)" != job_* ]]; do CG=$(dirname $CG); done
# Memory guard on non-reclaimable memory (anon+shmem+file_dirty+file_writeback; clean page
# cache is reclaimable: c3c5). Above the guard: STOP once; above the hard stop: scancel.
GUARD=$FC_MEM_GUARD_BYTES; HARD=$FC_MEM_HARD_BYTES
( fired=0; while sleep 5; do m=$(awk '$1=="anon"||$1=="shmem"||$1=="file_dirty"||$1=="file_writeback"{s+=$2} END{printf "%d", s}' $CG/memory.stat 2>/dev/null || echo 0)
  echo "$(date +%s) $(cat /proc/loadavg) mem=$m peak=$(cat $CG/memory.peak 2>/dev/null) $(awk '$1=="anon"||$1=="file"||$1=="shmem"||$1=="file_dirty"||$1=="file_writeback"{printf "%s=%s ",$1,$2}' $CG/memory.stat 2>/dev/null)"
  if [ "$m" -gt "$GUARD" ] && [ $fired = 0 ]; then fired=1; ( set -o noclobber; echo "service failure memory guard mem=$m" > $RUN/STOP ) 2>/dev/null; echo "MEMGUARD fired mem=$m $(date -Is)" >> $RUN/memguard.txt; fi
  if [ "$m" -gt "$HARD" ]; then echo "MEMGUARD hard mem=$m $(date -Is) scancel" >> $RUN/memguard.txt; scancel $SLURM_JOB_ID; fi
done ) > $RUN/load.txt &
LS=$!; BG+=($LS)
# A2 disk watermarks on the run dir (segments) and the node-local tmp: below the warn
# level one loud line; below the stop level DRAIN once (env nodes stop admitting new
# games; the learner keeps training on what it has). Samples go to disk.txt.
( warned=""; while sleep $FC_DISK_SAMPLE_SECONDS; do
  for d in $RUN $FC_SITE_GPU_LOCAL_TMP; do
    set -- $(df -P -B1 "$d" 2>/dev/null | awk 'NR==2{print $2, $4}'); [ -n "${1:-}" ] || continue
    pct=$(( $2 * 100 / $1 )); echo "$(date +%s) $d free_pct=$pct avail=$2 size=$1"
    if [ $pct -lt $FC_DISK_STOP_FREE_PCT ] && [ ! -e $RUN/disk-stop ]; then
      echo "DISK STOP $d free ${pct}% < ${FC_DISK_STOP_FREE_PCT}%: no new games $(date -Is)" | tee $RUN/disk-stop >> $RUN/disk-watermark.txt
      ( set -o noclobber; cat $RUN/disk-stop > $RUN/DRAIN ) 2>/dev/null
    elif [ $pct -lt $FC_DISK_WARN_FREE_PCT ] && [[ " $warned " != *" $d "* ]]; then
      warned="$warned $d"; echo "DISK WARN $d free ${pct}% < ${FC_DISK_WARN_FREE_PCT}% $(date -Is)" >> $RUN/disk-watermark.txt
    fi
  done; done ) > $RUN/disk.txt 2>&1 &
DS=$!; BG+=($DS)
$PY $H/proc_cpu.py $CG $RUN/proc-cpu.txt 10 &
PS_=$!; BG+=($PS_)
# The learner is no MPS client (the daemon serves the inference card only).
env -u CUDA_MPS_PIPE_DIRECTORY -u CUDA_MPS_LOG_DIRECTORY PYTORCH_CUDA_ALLOC_CONF=$FC_LEARNER_ALLOC_CONF CUDA_VISIBLE_DEVICES=$LG taskset -c $LCPU $PY -u $H/roles.py learner $RUN > $RUN/learner.log 2>&1 &
LP=$!
SPS=()
for k in $(seq 0 $((FC_SERVICES - 1))); do
  CUDA_VISIBLE_DEVICES=$SVCG SVC_INDEX=$k taskset -c $SCPU $PY -u $H/roles.py service $RUN > $RUN/service-$k.log 2>&1 &
  SPS+=($!)
done
# Every role's exit code counts (review B2): a learner that crashed without a report or a
# service that exited non-zero makes the job fail, whatever STOP says.
wait $LP; LRC=$?; echo "learner exit=$LRC $(date -Is)"
stop_once "gpu job end"
SRC=0
for pid in "${SPS[@]}"; do wait $pid; rc=$?; echo "service pid=$pid exit=$rc $(date -Is)"; [ $rc = 0 ] || SRC=$rc; done
kill $GS $LS $PS_ $DS 2>/dev/null
df -h $FC_SITE_GPU_LOCAL_TMP | tail -1; du -sh $TMPDIR/learner-live $RUN/segments-* 2>/dev/null
shm_clean end; echo quit | nvidia-cuda-mps-control 2>/dev/null; trap - EXIT; rm -rf $TMPDIR
# Smoke gate (FC_SMOKE=1): non-zero unless every service reported, rows were scored and
# handed off, and nothing failed.
RC=0
if [ "$FC_SMOKE" = 1 ]; then
  $PY - "$RUN" "$FC_SERVICES" <<'PYEOF' || RC=9
import json, sys
from pathlib import Path
run, n = Path(sys.argv[1]), int(sys.argv[2])
stop = (run / "STOP").read_text() if (run / "STOP").exists() else ""
bad = ["stop:" + stop] if stop.startswith(("service", "learner failure")) else []
total = [0, 0]
for k in range(n):
    f = run / f"service-report-{k}.json"
    if not f.exists():
        bad.append(f"no report {k}"); continue
    r = json.loads(f.read_text())
    rows, handed = r["service"].get("rows", 0), r.get("handoff", {}).get("handed_off", 0)
    print(f"smoke service {k}: rows={rows} handed_off={handed} error={r.get('error')}")
    total[0] += rows or 0; total[1] += handed or 0
    if r.get("error"):
        bad.append(f"service {k}")
if not total[0] or not total[1]:
    bad.append("no rows scored or handed off at all")
learner = json.loads((run / "learner-report.json").read_text())
if learner.get("failure"):
    bad.append("learner " + learner["failure"])
print("smoke gate:", "FAIL " + ", ".join(bad) if bad else "PASS")
sys.exit(1 if bad else 0)
PYEOF
fi
# A service failure stopped the run: exit 5 (env side: 4); a learner failure: exit 6.
if [ $RC = 0 ] && { grep -q "^service failure" $RUN/STOP 2>/dev/null || [ $SRC != 0 ]; }; then RC=5; fi
if [ $RC = 0 ] && { [ $LRC != 0 ] || [ ! -s $RUN/learner-report.json ] || grep -q "^learner failure" $RUN/STOP 2>/dev/null; }; then RC=6; fi
if [ $RC = 0 ] && [ -s $RUN/learner-report.json ] && ! $PY -c "import json,sys; sys.exit(json.load(open(sys.argv[1])).get(\"failure\") is not None)" $RUN/learner-report.json; then RC=6; fi
echo "end=$(date -Is) rc=$RC"
exit $RC
