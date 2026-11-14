#!/bin/bash
# Fast-chain remote preparation hosts of one prep group (FC_PREP_GROUP; port of the private
# fc-prep3w.sh). launch.py submits one plain job per group with --dependency=after:<GPU job>,
# so no prep host is held while the GPU job pends; never a heterogeneous job (Slurm starts
# those only from backfill: CLUSTER-QUEUE-DIAG 11-14). Each host checks itself first (deps,
# env sha, scratch free >= 2x in-flight, scratch not on a network disk); a refusing host
# writes $RUN/prep-refused/<n> and the learner stops waiting at once.
set -u; umask 077
set +u; source /etc/profile >/dev/null 2>&1
[ -n "$FC_RUN" ] || { echo "FC_RUN unset"; exit 2; }
source "$FC_RUN/job-env.sh"
module load "$FC_SITE_MODULE_LOAD" >/dev/null 2>&1; set -u
RUN=$FC_RUN; H=$FC_HARNESS; PY=$FC_SITE_PYTHON; R=$RUN/prep-ready; mkdir -p $R
export LD_LIBRARY_PATH=$FC_SITE_LIB_PATH:${LD_LIBRARY_PATH:-} OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=$FC_SITE_SRC_DIR/python:$FC_SITE_LZ4_PATH
export STPD_REMOTE_PREPARE_AUTH=$(cat "$FC_SITE_PREPARE_AUTH_FILE")
# The GPU side may have ended while this job was still pending: nothing to serve.
[ -e $RUN/STOP ] && { echo "gpu side ended before this prep job started: $(cat $RUN/STOP)"; exit 0; }
# #523 a437612d: the host refuses to become ready with less free scratch than the
# in-flight budget (0 = not passed; preflight checked the build has the flag).
MINFREE=""; [ "${FC_PREP_MIN_FREE_BYTES:-0}" -gt 0 ] && MINFREE="--scratch-min-free-bytes $FC_PREP_MIN_FREE_BYTES"
# This group's workers, scratch and cpus, and its first host index n (the hosts of the
# groups before it come first, so ready-<n> and prep-host-<n>.log stay unique).
read -r W TB C n <<< "$($PY -c "import json,os; gs=json.loads(os.environ['FC_PREP_GROUPS_JSON']); g=int(os.environ['FC_PREP_GROUP']); x=gs[g]; print(x['workers'], x['scratch'], x['cpus'], sum(y['nodes'] for y in gs[:g]))")"
for h in $(scontrol show hostnames $SLURM_JOB_NODELIST); do
  # srun does not inherit --cpus-per-task (c3c25: every host ran on 1 CPU): pass it.
  srun --nodes=1 --ntasks=1 --cpus-per-task=$C -w $h bash -c "T=$TB/fc-prep-\$SLURM_JOB_ID-h$n; trap \"rm -rf \$T\" EXIT; echo \$(hostname -s) \$(nproc) \$(grep -m1 \"model name\" /proc/cpuinfo | cut -d: -f2); $PY $H/preflight.py prep-host --run $RUN --path \$T --index $n || exit 9; (while sleep 30; do echo LOAD \$(date +%s) \$(cat /proc/loadavg) tmp_avail_kb=\$(df --output=avail \$T | tail -1) scratch_kb=\$(du -sk \$T 2>/dev/null | cut -f1); done) & exec $PY -u -m stpd.rl.remote_prepare --scratch \$T --decode-workers $W --assembly-workers $W --cache-bytes 1 --codec $FC_REMOTE_PREPARE_CODEC $MINFREE --ready-file $R/ready-$n" > $RUN/prep-host-$n.log 2>&1 &
  n=$((n+1))
done
echo "prep group $FC_PREP_GROUP hosts up to index $((n-1)) start=$(date -Is)"
# --- serve: until the GPU side stops (STOP) or its job is gone ---
# A failed squeue is not "gone" (CLUSTER-QUEUE-DIAG 11-14: one failure ended every host).
# Gone = squeue answers without a live state, or "Invalid job id" (purged). 40 failures in a
# row (10 min without an answer): stop the hosts loudly, exit 4.
GPU=$(cat $RUN/gpu-job.txt); FAILS=0; RC=0
while [ ! -e $RUN/STOP ]; do
  sleep 15
  OUT=$(squeue -h -j $GPU -o %T 2>&1); QRC=$?
  if grep -qxE 'RUNNING|CONFIGURING|COMPLETING' <<< "$OUT"; then
    FAILS=0
  elif [ $QRC = 0 ] || [[ $OUT == *"Invalid job id"* ]]; then
    echo "GPU job $GPU gone: ${OUT:-not listed} $(date -Is)"; break
  else
    FAILS=$((FAILS + 1)); echo "squeue failed ($FAILS in a row) $(date -Is): $OUT"
    [ $FAILS -lt 40 ] || { echo "squeue failed $FAILS times in a row: stopping the hosts"; RC=4; break; }
  fi
done
# --- end serve ---
echo "stopping hosts $(date -Is)"; kill $(jobs -p) 2>/dev/null; wait
echo "end=$(date -Is) rc=$RC"
exit $RC
