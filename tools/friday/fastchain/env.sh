#!/bin/bash
# Fast-chain environment node (one array element = one node; submitted by launch.py; port
# of the private fc-env2.sh). Admission sidecar + FC_ENV_AGGREGATORS aggregators (each to
# its own service) + drivers. Never retries a game; unknown stays unknown; originals kept.
# Joins when the services are up and leaves at the GPU side's DRAIN.
umask 077
source /etc/profile >/dev/null 2>&1
[ -n "$FC_RUN" ] || { echo "FC_RUN unset"; exit 2; }
source "$FC_RUN/job-env.sh"
module load "$FC_SITE_MODULE_LOAD" >/dev/null 2>&1
set -uo pipefail
NODEIDX=$(( ${FC_NODEIDX_BASE:-0} + ${SLURM_ARRAY_TASK_ID:-0} ))
ulimit -n 65536 2>/dev/null || ulimit -n $(ulimit -Hn)
RUN=$FC_RUN; H=$FC_HARNESS; PY=$FC_SITE_PYTHON; rel=$FC_SITE_NODE_RELEASE
export PATH="$rel/$FC_SITE_NODE_BIN:$rel/$FC_SITE_DOTNET_DIR:$PATH" REL=$rel
export DOTNET_ROOT="$rel/$FC_SITE_DOTNET_DIR" DOTNET="$rel/$FC_SITE_DOTNET_DIR/dotnet"
export DOTNET_NOLOGO=1 DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_PROCESSOR_COUNT=1
export DOTNET_SKIP_WORKLOAD_INTEGRITY_CHECK=1 DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=1
export DOTNET_CLI_HOME=$FC_SITE_ENV_TMP_ROOT/dotnet-home/$SLURM_JOB_ID TMPDIR=$FC_SITE_ENV_TMP_ROOT/$SLURM_JOB_ID
export PYTHONPATH=$FC_SITE_SRC_DIR/python:$FC_SITE_LZ4_PATH PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1
export LD_LIBRARY_PATH=$FC_SITE_LIB_PATH:${LD_LIBRARY_PATH:-}
export STPD_P1_LOCAL_INFERENCE_AUTH=$(cat "$FC_SITE_INFERENCE_AUTH_FILE")
mkdir -p $DOTNET_CLI_HOME $TMPDIR
O=$RUN/env-n$NODEIDX-$SLURM_JOB_ID; mkdir -p $O
CORES=${SLURM_CPUS_PER_TASK:-$FC_ENV_NODE_CORES}
P=$($PY -c "print(max(1, int($CORES * $FC_ENV_PROCS_FRACTION)))"); K=$FC_ENV_SLOTS
NSVC=$FC_SERVICES; AGGS=$FC_ENV_AGGREGATORS; ENC=$FC_ENV_ENCODERS
echo "array=${SLURM_ARRAY_JOB_ID:-none}_${SLURM_ARRAY_TASK_ID:-none} nodeidx=$NODEIDX pkg=$(cut -c1-12 $FC_SITE_ENV_PKG/SOURCE_REVISION 2>/dev/null) memo=$FC_ENV_MEMO"
echo "node=$(hostname -s) start=$(date -Is) model=$(lscpu | grep 'Model name' | sed 's/.*: *//') cpus=$(taskset -pc $$ | sed 's/.*: //') procs=$P slots=$K"
svc_of() { echo $(( (NODEIDX * AGGS + $1) % NSVC )); }
have() { for a in $(seq 0 $((AGGS - 1))); do [ -s $RUN/service-$(svc_of $a).json ] || return 1; done; [ -s $RUN/inference.json ] && [ -s $RUN/env-sha.txt ]; }
for i in $(seq 1 $FC_WAIT_N); do have && break; [ -e $RUN/STOP ] && break; sleep 5; done
# A node that arrives after the GPU side ended (or while it drains) has nothing to join.
if [ -e $RUN/STOP ] || [ -e $RUN/DRAIN ]; then
  echo "gpu side ended or draining before this node joined: $(cat $RUN/STOP 2>/dev/null) $(date -Is)"; exit 0
fi
have || { echo "no service"; exit 2; }
if [ -s $RUN/env-drain-at ] && [ $(( $(cat $RUN/env-drain-at) - $(date +%s) )) -lt $FC_MIN_JOIN_S ]; then
  echo "too late to join: $(( $(cat $RUN/env-drain-at) - $(date +%s) )) s left before env drain $(date -Is)"; exit 0; fi
# Start check: deps + python_env_sha256 equal to the GPU side's (attach would refuse later).
$PY $H/preflight.py env-node --run $RUN || { echo "preflight failed on $(hostname -s)"; exit 9; }
echo "preflight driver env_driver.mjs $(sha256sum $H/env_driver.mjs | cut -c1-12)"
cg=$(awk -F: '$2 ~ /cpuacct/ {print $3}' /proc/self/cgroup)
usage() { if [ -r /sys/fs/cgroup/cpu,cpuacct$cg/cpuacct.usage ]; then cat /sys/fs/cgroup/cpu,cpuacct$cg/cpuacct.usage;
  else awk '/usage_usec/ {print $2*1000}' /sys/fs/cgroup$(awk -F: '$1=="0" {print $3}' /proc/self/cgroup)/cpu.stat; fi; }
( while true; do echo "$(date +%s%3N) $(usage)"; sleep 2; done ) > $O/cgroup.txt &
SAMPLER=$!
PROCSF=/sys/fs/cgroup/cpu,cpuacct$cg/cgroup.procs
[ -r $PROCSF ] || PROCSF=/sys/fs/cgroup$(awk -F: '$1=="0" {print $3}' /proc/self/cgroup)/cgroup.procs
python3 -u $H/proc_sampler.py $PROCSF $O/procs.jsonl 2 &
PSAMP=$!
bash $H/native_nice.sh $FC_NATIVE_NICE $O/nice.log &
NICER=$!
$PY -u $H/env_admission.py $RUN/inference.json $TMPDIR/admit.sock $O/admit.jsonl > $O/admit.log 2>&1 &
ADM=$!
AGGP=()
for a in $(seq 0 $((AGGS - 1))); do
  AGG_ID=fc-n$NODEIDX-a$a $PY -u $H/env_aggregator.py $RUN/service-$(svc_of $a).json $TMPDIR/agg-$a.sock $ENC $O/aggregator-$a.json $FC_AGGREGATOR_WINDOW_MS > $O/aggregator-$a.log 2>&1 &
  AGGP+=($!)
done
ready() { for a in $(seq 0 $((AGGS - 1))); do grep -q '"ready"' $O/aggregator-$a.log 2>/dev/null || return 1; done; grep -q '"ready"' $O/admit.log 2>/dev/null; }
for i in $(seq 1 120); do ready && break; sleep 1; done
ready || { echo "aggregator/admission not ready"; cat $O/aggregator-*.log $O/admit.log; kill "${AGGP[@]}" $ADM; exit 3; }
echo "aggregators: $(for a in $(seq 0 $((AGGS - 1))); do echo -n "a$a->service-$(svc_of $a) "; done)"
echo "joined=$(date -Is) $(date +%s)"
GC=$([ "$FC_DRIVER_SINGLE_THREADED_GC" = 1 ] && echo --single-threaded-gc on || echo --single-threaded-gc off)
# #530 S-1: one ascension level per driver from ascension_mix (largest remainder over this
# node's drivers); #529: R7 audit games chosen by the admission sidecar when the rate > 0.
ASC=($($PY -c "import json, sys; sys.path.insert(0, sys.argv[3]); import run_profile; print(*run_profile.ascension_plan(json.loads(sys.argv[1]), int(sys.argv[2])))" "$FC_ASCENSION_MIX" $P $H))
[ ${#ASC[@]} -eq $P ] || { echo "ascension plan failed: ${ASC[*]}"; kill "${AGGP[@]}" $ADM; exit 3; }
# I4: one exploration arm per driver, interleaved over the run's driver numbers (node x P + p).
ARM=($($PY -c "import json, sys; sys.path.insert(0, sys.argv[4]); import run_profile; print(*run_profile.exploration_plan(json.loads(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])))" "$FC_EXPLORATION_LADDER" $(( NODEIDX * P )) $P $H))
[ ${#ARM[@]} -eq $P ] || { echo "exploration plan failed: ${ARM[*]}"; kill "${AGGP[@]}" $ADM; exit 3; }
R7=$(awk "BEGIN { print ($FC_R7_GAMES_PER_HOUR > 0) ? \"on\" : \"off\" }")
echo "ascension per driver: ${ASC[*]} arm per driver: ${ARM[*]} replay_k=$FC_REPLAY_K r7=$R7"
pids=()
for p in $(seq 1 $P); do
  node $H/env_driver.mjs --pkg $FC_SITE_ENV_PKG --memo "$FC_ENV_MEMO" --out $O/p$p --tag n$NODEIDX-p$p --slots $K --single-choice omit --compact on \
    --warm-s $FC_DRIVER_WARM_SECONDS --steady-s $FC_DRIVER_STEADY_SECONDS --status-s $FC_DRIVER_STATUS_SECONDS --link $TMPDIR/agg-$(( (p - 1) % AGGS )).sock --admit $TMPDIR/admit.sock \
    --seeds $FC_SITE_SEEDS_FILE --seed-offset $(( (NODEIDX * 32 + p - 1) * 600 )) \
    --input-spec $FC_SITE_INPUT_SPEC_FILE --initial-progress $FC_SITE_INITIAL_PROGRESS_FILE --candidate $FC_SITE_ENV_CANDIDATE \
    --cold-start $FC_DRIVER_COLD_START --elu-preload $H/elu_preload.mjs \
    --inference-timeout-ms $FC_DRIVER_INFERENCE_TIMEOUT_MS --reopen-backoff-start-ms $FC_DRIVER_REOPEN_BACKOFF_START_MS \
    --reopen-backoff-cap-ms $FC_DRIVER_REOPEN_BACKOFF_CAP_MS --reopen-backoff-jitter $FC_DRIVER_REOPEN_BACKOFF_JITTER \
    --reduce-light-interval $FC_DRIVER_REDUCE_LIGHT_INTERVAL $GC --ascension ${ASC[$((p - 1))]} --r7 $R7 \
    --exploration-ladder "$FC_EXPLORATION_LADDER" --arm ${ARM[$((p - 1))]} --replay-k $FC_REPLAY_K > $O/p$p.driver.log 2>&1 &
  pids+=($!)
done
drain() { cat $RUN/STOP > $O/stop-at-drain.txt 2>/dev/null; echo "drain $1 $(date -Is) stop=$(cat $O/stop-at-drain.txt 2>/dev/null)"; kill -USR1 "${pids[@]}" 2>/dev/null; }
trap "drain slurm_signal" USR1
( while :; do if [ -e $RUN/DRAIN ] || [ -e $RUN/STOP ]; then cat $RUN/STOP > $O/stop-at-drain.txt 2>/dev/null; echo "drain file $(date -Is) stop=$(cat $O/stop-at-drain.txt 2>/dev/null)"; kill -USR1 "${pids[@]}" 2>/dev/null; exit 0; fi; sleep 2; done ) &
WATCH=$!
FAIL=0
for pid in "${pids[@]}"; do
  wait $pid; rc=$?
  while [ $rc -gt 128 ] && kill -0 $pid 2>/dev/null; do wait $pid; rc=$?; done
  echo "pid=$pid exit=$rc"; [ $rc -eq 0 ] || FAIL=1
done
kill $WATCH 2>/dev/null
kill "${AGGP[@]}"; wait "${AGGP[@]}"
kill $ADM $SAMPLER $PSAMP $NICER 2>/dev/null
# Drivers that died because the GPU side failed are not an env error: exit 4.
STOPTXT=$(cat $O/stop-at-drain.txt 2>/dev/null || cat $RUN/STOP 2>/dev/null)
if [ $FAIL -ne 0 ] && [[ "$STOPTXT" == "service failure"* ]]; then
  echo "end=$(date -Is) fail=$FAIL ended by GPU-side failure: $STOPTXT"; exit 4
fi
echo "end=$(date -Is) fail=$FAIL"
exit $FAIL
