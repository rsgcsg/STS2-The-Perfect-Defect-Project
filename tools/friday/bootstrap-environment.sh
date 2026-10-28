#!/bin/bash
# Run inside a one-core Slurm allocation, not on the login node.
set -euo pipefail
umask 077
: "${SLURM_JOB_ID:?Submit bootstrap with sbatch --cpus-per-task=1}"
F2_ROOT=${F2_ROOT:-/data/$(id -un)/friday-actor}
# Slurm executes a spooled copy; $0 does not identify the deployed source.
SCRIPT_DIR="${FRIDAY_SOURCE_ROOT:-$F2_ROOT/source}/tools/friday"
test -f "$SCRIPT_DIR/requirements-cpu.txt"
module load python/3.11.5-gcccore-13.2.0 nodejs/20.9.0-gcccore-13.2.0
mkdir -p "$F2_ROOT/bootstrap" "$F2_ROOT/evidence"
F2_SDK="$F2_ROOT/dotnet-sdk9.0.117"
if [ ! -x "$F2_SDK/dotnet" ]; then
    mkdir -p "$F2_SDK"
    curl -fsSL https://builds.dotnet.microsoft.com/dotnet/Sdk/9.0.117/dotnet-sdk-9.0.117-linux-x64.tar.gz -o "$F2_ROOT/dotnet-sdk.tar.gz"
    printf '%s  %s\n' 7fffdf27c780fae77933b887fd6409166d9cff548a26c2b87285a6f0dc126feb1e910a214e9d726717e44d4dbf6d69336e792e6085b08cad843d7b3ad9f4e9d8 "$F2_ROOT/dotnet-sdk.tar.gz" | sha512sum -c -
    tar -xzf "$F2_ROOT/dotnet-sdk.tar.gz" -C "$F2_SDK"
fi
"$F2_SDK/dotnet" --info > "$F2_ROOT/evidence/dotnet-info.txt"
python -m pip install --disable-pip-version-check --no-warn-script-location --target "$F2_ROOT/bootstrap" 'uv==0.11.25'
F2_UV="$F2_ROOT/bootstrap/bin/uv"
"$F2_UV" venv --clear --python "$(command -v python)" "$F2_ROOT/env"
"$F2_UV" pip install --python "$F2_ROOT/env/bin/python" -r "$SCRIPT_DIR/requirements-cpu.txt"
"$F2_UV" pip freeze --python "$F2_ROOT/env/bin/python" > "$F2_ROOT/evidence/environment-freeze.txt"
"$F2_ROOT/env/bin/python" - <<'PY'
import json,sys,torch,tokenizers,platform
assert sys.version_info[:3]==(3,11,5)
assert torch.__version__=='2.13.0+cpu' and torch.version.cuda is None
assert tokenizers.__version__=='0.22.2'
print(json.dumps({'python':sys.version,'torch':torch.__version__,'tokenizers':tokenizers.__version__,'cuda':torch.version.cuda,'machine':platform.machine()}))
PY
sha256sum "$SCRIPT_DIR/requirements-cpu.txt" "$F2_ROOT/evidence/environment-freeze.txt"
