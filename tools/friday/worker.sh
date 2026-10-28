#!/bin/bash
# User-owned launcher sources the user's private R2 environment; never enable xtrace.
set +x
set -euo pipefail
umask 077
configuration=${1:?configuration JSON path required}
cell=${2:?declared comparison cell required}
actor_root=${FRIDAY_ACTOR_ROOT:-/data/s4961270/friday-actor}
source_root=${FRIDAY_SOURCE_ROOT:-$actor_root/source}
credentials=${FRIDAY_R2_ENV:-/data/s4961270/.config/spireagent/friday-r2.env}
if [[ -e "$credentials" ]]; then
  [[ ! -L "$credentials" && $(stat -c '%a' "$credentials") = 600 && $(stat -c '%u' "$credentials") = $(id -u) ]] || {
    echo 'R2 environment must be a user-owned regular600 file' >&2; exit 2;
  }
  set -a
  source "$credentials"
  set +a
fi
if ! type module >/dev/null 2>&1; then source /etc/profile; fi
module load python/3.11.5-gcccore-13.2.0 nodejs/20.9.0-gcccore-13.2.0
export PATH="$actor_root/env/bin:$actor_root/dotnet-sdk9.0.117:/data/s4961270/dotnet9:$PATH"
export PYTHONPATH="$source_root/python"
export STS2_GAME_DIR="$actor_root/game"
export DOTNET_PROCESSOR_COUNT=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
export FRIDAY_INFERENCE_THREADS=${FRIDAY_INFERENCE_THREADS:-2}
exec "$actor_root/env/bin/python" "$source_root/tools/friday/batch.py" "$configuration" --cell "$cell" \
  --wall-seconds "${FRIDAY_WALL_SECONDS:-3600}" --episode-seconds "${FRIDAY_EPISODE_SECONDS:-900}" \
  --max-groups "${FRIDAY_MAX_GROUPS:-100}"
