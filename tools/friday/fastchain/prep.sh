#!/bin/bash
# Fast-chain remote preparation host: one element (SLURM_ARRAY_TASK_ID) of a prep group's
# array (FC_PREP_GROUP; port of the private fc-prep3w.sh). launch.py submits the prep arrays
# first and the GPU job with --dependency=after:<every prep element>, so the GPU job queues
# only once every host runs (hwcheck 11-19); one element per host, never a multi-node or
# heterogeneous job (Slurm starts those only from backfill: CLUSTER-QUEUE-DIAG 11-14). Each
# host checks itself first (deps, env sha, scratch free >= 2x in-flight, scratch not on a
# network disk); a refusing host writes $RUN/prep-refused/<n>: the launcher cancels the run
# before the GPU job starts, a learner already waiting stops at once.
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
source $H/gpu_job.sh
# The launcher submits the GPU job right after the prep arrays: its id may still be on the way.
for i in $(seq 60); do [ -s $RUN/gpu-job.txt ] && break; sleep 2; done
[ -s $RUN/gpu-job.txt ] || { echo "no GPU job id in gpu-job.txt after 120 s: nothing to serve"; exit 3; }
GPU=$(cat $RUN/gpu-job.txt)
read -r V D <<< "$(gpu_job_state $GPU)"
[ "$V" = gone ] && { echo "GPU job $GPU gone before this prep job started: $D"; exit 0; }
# #523 a437612d: the host refuses to become ready with less free scratch than the
# in-flight budget (0 = not passed; preflight checked the build has the flag).
MINFREE=""; [ "${FC_PREP_MIN_FREE_BYTES:-0}" -gt 0 ] && MINFREE="--scratch-min-free-bytes $FC_PREP_MIN_FREE_BYTES"
# This group's workers, scratch and cpus, and this host's index n (the hosts of the groups
# before it come first, then the array index, so ready-<n> and prep-host-<n>.log stay unique).
read -r W TB C n <<< "$($PY -c "import json,os; gs=json.loads(os.environ['FC_PREP_GROUPS_JSON']); g=int(os.environ['FC_PREP_GROUP']); x=gs[g]; print(x['workers'], x['scratch'], x['cpus'], sum(y['nodes'] for y in gs[:g]) + int(os.environ.get('SLURM_ARRAY_TASK_ID', '0')))")"
for h in $(scontrol show hostnames $SLURM_JOB_NODELIST); do
  # srun does not inherit --cpus-per-task (c3c25: every host ran on 1 CPU): pass it.
  srun --nodes=1 --ntasks=1 --cpus-per-task=$C -w $h bash -c "T=$TB/fc-prep-\$SLURM_JOB_ID-h$n; trap \"rm -rf \$T\" EXIT; echo \$(hostname -s) \$(nproc) \$(grep -m1 \"model name\" /proc/cpuinfo | cut -d: -f2); $PY $H/preflight.py prep-host --run $RUN --path \$T --index $n || exit 9; (while sleep 30; do echo LOAD \$(date +%s) \$(cat /proc/loadavg) tmp_avail_kb=\$(df --output=avail \$T | tail -1) scratch_kb=\$(du -sk \$T 2>/dev/null | cut -f1); done) & exec $PY -u -m stpd.rl.remote_prepare --scratch \$T --decode-workers $W --assembly-workers $W --cache-bytes 1 --codec $FC_REMOTE_PREPARE_CODEC $MINFREE --ready-file $R/ready-$n" > $RUN/prep-host-$n.log 2>&1 &
  n=$((n+1))
done
echo "prep group $FC_PREP_GROUP host index $((n-1)) start=$(date -Is)"
# --- serve: until the learner is done (its report) or the GPU job is gone ---
# Not until STOP: after STOP the learner still finishes its frozen dataset, drains and
# publishes its final CP, reading from these hosts (V6 smoke attempt 2: the hosts stopped
# on an operator STOP, the learner's shard 2 got EOFError and fenced, no final CP). A STOP
# before the learner was ready (it never started) also ends the serving.
# Before the GPU job confirmed its start ($RUN/gpu-started) the launcher is the one
# watching: its heartbeat launcher-alive must stay fresh, by gpu.sh's rule (older than
# FC_LAUNCHER_STALE_SECONDS, read again after 30 s: stale). A stale one (launcher killed,
# login node lost) stops the hosts loudly; the queued GPU job then refuses on the same file
# (review #619 M3). squeue decides while it answers (gpu_job.sh: stdout only, a failure is
# unknown, never gone). While it cannot (slurmctld outage): before the GPU start the
# launcher's heartbeat decides (above), after it the GPU job's own heartbeat: gpu.sh
# rewrites load.txt every 5 s while it lives. The hosts stop only when squeue cannot answer
# AND that heartbeat is older than 600 s. Every stop writes prep-stopped-<group> with the
# reason (#540 review); the launcher, still waiting, sees the element end and cancels.
SERVE_START=$(date +%s); RC=0
learner_done() { [ -e $RUN/learner-report.json ] || { [ -e $RUN/STOP ] && [ ! -e $RUN/learner.ready ]; }; }
launcher_beat() { local m; m=$(date -r $RUN/launcher-alive +%s 2>/dev/null) && echo "$(( $(date +%s) - m )) s old" || echo missing; }
launcher_gone() {
  [ -e $RUN/gpu-started ] && return 1
  local b; b=$(launcher_beat)
  [ "$b" = missing ] || [ "${b%% *}" -gt $FC_LAUNCHER_STALE_SECONDS ] || return 1
  echo "launcher heartbeat $b before the GPU start: read again in 30 s $(date -Is)"
  sleep 30; cat $RUN/launcher-alive >/dev/null 2>&1
  [ -e $RUN/gpu-started ] && return 1
  b=$(launcher_beat)
  [ "$b" = missing ] || [ "${b%% *}" -gt $FC_LAUNCHER_STALE_SECONDS ]
}
while ! learner_done; do
  sleep 15
  if launcher_gone; then
    echo "launcher gone before the GPU start (launcher-alive $(launcher_beat)): prep group $FC_PREP_GROUP stopped $(date -Is)" | tee $RUN/prep-stopped-$FC_PREP_GROUP
    RC=4; break
  fi
  read -r V D <<< "$(gpu_job_state $GPU)"
  case $V in
    live) ;;
    gone) echo "GPU job $GPU gone: $D $(date -Is)"; break ;;
    *) if [ ! -e $RUN/gpu-started ]; then
         echo "squeue cannot answer ($D); before the GPU start, launcher heartbeat $(launcher_beat) $(date -Is)"
         continue
       fi
       AGE=$(( $(date +%s) - $(date -r $RUN/load.txt +%s 2>/dev/null || echo $SERVE_START) ))
       echo "squeue cannot answer ($D); GPU heartbeat load.txt ${AGE} s old $(date -Is)"
       if [ $AGE -gt 600 ]; then
         echo "squeue cannot answer and the GPU heartbeat is ${AGE} s old: prep group $FC_PREP_GROUP stopped $(date -Is)" | tee $RUN/prep-stopped-$FC_PREP_GROUP
         RC=4; break
       fi ;;
  esac
done
# --- end serve ---
echo "stopping hosts $(date -Is)"; kill $(jobs -p) 2>/dev/null; wait
echo "end=$(date -Is) rc=$RC"
exit $RC
