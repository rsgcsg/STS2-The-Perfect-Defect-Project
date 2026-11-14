# Sourced by prep.sh and env.sh: the run's GPU job as squeue sees it (review #540).
# `gpu_job_state JOB` prints "live|gone|unknown <what squeue said>". Only stdout decides;
# stderr (warnings even at rc 0) is shown, never read as an answer. gone = an empty answer,
# a finished state, or "Invalid job id" (purged); PENDING/REQUEUED/SUSPENDED and every other
# state are live; a failed squeue is unknown, never gone.
GPU_JOB_ENDED='COMPLETED|CANCELLED|FAILED|TIMEOUT|NODE_FAIL|BOOT_FAIL|OUT_OF_MEMORY|PREEMPTED|DEADLINE'
gpu_job_state() {
  local err out rc state said
  err=$(mktemp); out=$(squeue -h -j "$1" -o %T 2>"$err"); rc=$?
  state=$(echo $out)
  said="rc=$rc out=[$state] err=[$(head -c 200 "$err" | tr '\n' ' ')]"
  if [ $rc != 0 ]; then
    if grep -q 'Invalid job id' "$err"; then echo "gone $said"; else echo "unknown $said"; fi
  elif [ -z "$state" ] || [[ $state =~ ^($GPU_JOB_ENDED)$ ]]; then echo "gone $said"
  elif [[ $state =~ ^[A-Z_]+$ ]]; then echo "live $said"
  else echo "unknown $said"; fi
  rm -f "$err"
}
