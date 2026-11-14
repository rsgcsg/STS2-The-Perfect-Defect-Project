#!/bin/bash
# Fast-chain remote preparation hosts (submitted by launch.py as a heterogeneous job, one
# component per prep group; port of the private fc-prep3w.sh). Each host checks itself
# first (deps, env sha, scratch free >= 2x in-flight, scratch not on a network disk); a
# refusing host writes $RUN/prep-refused/<n> and the launcher cancels before any GPU job.
set -u; umask 077
set +u; source /etc/profile >/dev/null 2>&1
[ -n "$FC_RUN" ] || { echo "FC_RUN unset"; exit 2; }
source "$FC_RUN/job-env.sh"
module load "$FC_SITE_MODULE_LOAD" >/dev/null 2>&1; set -u
RUN=$FC_RUN; H=$FC_HARNESS; PY=$FC_SITE_PYTHON; R=$RUN/prep-ready; mkdir -p $R
export LD_LIBRARY_PATH=$FC_SITE_LIB_PATH:${LD_LIBRARY_PATH:-} OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH=$FC_SITE_SRC_DIR/python:$FC_SITE_LZ4_PATH
export STPD_REMOTE_PREPARE_AUTH=$(cat "$FC_SITE_PREPARE_AUTH_FILE")
NG=$($PY -c "import json,os; print(len(json.loads(os.environ['FC_PREP_GROUPS_JSON'])))")
# #523 a437612d: the host refuses to become ready with less free scratch than the
# in-flight budget (0 = not passed; preflight checked the build has the flag).
MINFREE=""; [ "${FC_PREP_MIN_FREE_BYTES:-0}" -gt 0 ] && MINFREE="--scratch-min-free-bytes $FC_PREP_MIN_FREE_BYTES"
n=0
for g in $(seq 0 $((NG - 1))); do
  read -r W TB C <<< "$($PY -c "import json,os; x=json.loads(os.environ['FC_PREP_GROUPS_JSON'])[$g]; print(x['workers'], x['scratch'], x['cpus'])")"
  NL=$(eval echo \${SLURM_JOB_NODELIST_HET_GROUP_$g:-$SLURM_JOB_NODELIST})
  # One active group = a plain job (launch.py adds ':' only between groups), and srun
  # refuses --het-group there ("No directives to start application on any available
  # hetjob components").
  HG=""; [ "$NG" -gt 1 ] && HG="--het-group=$g"
  for h in $(scontrol show hostnames $NL); do
    # srun does not inherit --cpus-per-task (c3c25: every host ran on 1 CPU): pass it.
    srun $HG --nodes=1 --ntasks=1 --cpus-per-task=$C -w $h bash -c "T=$TB/fc-prep-\$SLURM_JOB_ID-h$n; trap \"rm -rf \$T\" EXIT; echo \$(hostname -s) \$(nproc) \$(grep -m1 \"model name\" /proc/cpuinfo | cut -d: -f2); $PY $H/preflight.py prep-host --run $RUN --path \$T --index $n || exit 9; (while sleep 30; do echo LOAD \$(date +%s) \$(cat /proc/loadavg) tmp_avail_kb=\$(df --output=avail \$T | tail -1) scratch_kb=\$(du -sk \$T 2>/dev/null | cut -f1); done) & exec $PY -u -m stpd.rl.remote_prepare --scratch \$T --decode-workers $W --assembly-workers $W --cache-bytes 1 --codec $FC_REMOTE_PREPARE_CODEC $MINFREE --ready-file $R/ready-$n" > $RUN/prep-host-$n.log 2>&1 &
    n=$((n+1))
  done
done
echo "prep hosts=$n start=$(date -Is)"
# Serve until the GPU side stops (STOP) or its job is gone; then end every host.
while [ ! -e $RUN/STOP ]; do
  sleep 15
  [ -s $RUN/gpu-job.txt ] && ! squeue -h -j $(cat $RUN/gpu-job.txt) 2>/dev/null | grep -q . && break
done
echo "stopping hosts $(date -Is)"; kill $(jobs -p) 2>/dev/null; wait
echo "end=$(date -Is)"
