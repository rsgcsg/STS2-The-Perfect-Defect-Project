#!/bin/bash
# P1-e save probe on one CPU node (save_probe.mjs explains what it compares). Uses a run's site
# settings as env.sh does: the env package (built from a commit that has fast-env saveMap; the
# probe script is taken from the same package), the candidate, the exact game and the initial
# progress. One Managed process at a time (1 + loads per seed); a few core-minutes. Writes only
# under OUT_DIR (private; saves never leave it).
#   sbatch -p <cpu partition> -c 2 --mem 8G -t 30 --export=ALL,FC_RUN=<run dir with job-env.sh> \
#     tools/friday/fastchain/save_probe.sh OUT_DIR [--seeds A,B] [--save-floor 1] [--steps 400] [--loads 2] [--ascension 10]
umask 077
source /etc/profile >/dev/null 2>&1
[ -n "${FC_RUN:-}" ] && [ -n "${1:-}" ] || { echo "usage: FC_RUN=<run dir> save_probe.sh OUT_DIR [probe flags]"; exit 2; }
source "$FC_RUN/job-env.sh"
module load "$FC_SITE_MODULE_LOAD" >/dev/null 2>&1
set -uo pipefail
rel=$FC_SITE_NODE_RELEASE
export PATH="$rel/$FC_SITE_NODE_BIN:$rel/$FC_SITE_DOTNET_DIR:$PATH" REL=$rel
export DOTNET_ROOT="$rel/$FC_SITE_DOTNET_DIR" DOTNET="$rel/$FC_SITE_DOTNET_DIR/dotnet"
export DOTNET_NOLOGO=1 DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_PROCESSOR_COUNT=1
export DOTNET_CLI_HOME=$FC_SITE_ENV_TMP_ROOT/dotnet-home/${SLURM_JOB_ID:-probe} TMPDIR=$FC_SITE_ENV_TMP_ROOT/${SLURM_JOB_ID:-probe}
export LD_LIBRARY_PATH=$FC_SITE_LIB_PATH:${LD_LIBRARY_PATH:-}
mkdir -p "$DOTNET_CLI_HOME" "$TMPDIR"
OUT=$1; shift
PROBE=$FC_SITE_ENV_PKG/tools/friday/fastchain/save_probe.mjs
[ -f "$PROBE" ] || { echo "env package has no save_probe.mjs (built before W3?): $FC_SITE_ENV_PKG"; exit 2; }
echo "node=$(hostname -s) start=$(date -Is) pkg=$(cut -c1-12 "$FC_SITE_ENV_PKG/SOURCE_REVISION" 2>/dev/null)"
node "$PROBE" --pkg "$FC_SITE_ENV_PKG" --candidate "$FC_SITE_ENV_CANDIDATE" --game "$rel/game" \
  --initial-progress "$FC_SITE_INITIAL_PROGRESS_FILE" --out "$OUT" "$@"
rc=$?
echo "end=$(date -Is) exit=$rc report=$OUT/report.json"
exit $rc
