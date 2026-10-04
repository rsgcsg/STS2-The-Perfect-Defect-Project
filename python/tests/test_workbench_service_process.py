"""Real process/resource boundaries with private synthetic scratch only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.service_process import service_environment, service_process

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name == "nt", reason="POSIX file limits")
@pytest.mark.parametrize("limit", ["soft", "hard"])
def test_real_launcher_descendant_rejects_inherited_file_limit(tmp_path, limit):
    code = """
import json, resource, subprocess, sys
hard = resource.RLIM_INFINITY if sys.argv[3]=='soft' else 65536
resource.setrlimit(resource.RLIMIT_FSIZE, (65536, hard))
child = subprocess.run([sys.executable, '-c', '''
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from spireagent.workbench.service_process import service_environment
from spireagent.json_boundary import BoundaryError
try:
    service_environment(Path(sys.argv[2]))
except BoundaryError as error:
    print(json.dumps({'error':error.code}))
else:
    raise AssertionError('limited service admitted')
''', sys.argv[1], sys.argv[2]], capture_output=True, text=True, timeout=5, start_new_session=True)
print(json.dumps({'exit':child.returncode, 'result':child.stdout, 'stderr':child.stderr}))
"""
    result = subprocess.run([sys.executable, "-c", code, str(ROOT), str(tmp_path / "state"), limit],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    child = json.loads(result.stdout)
    assert child["exit"] == 0, child["stderr"]
    assert json.loads(child["result"]) == {"error": "workbench_file_limit_incompatible"}
    assert not (tmp_path / "state").exists()


def test_real_service_outlives_launcher_and_uses_profile_scratch(tmp_path):
    phase = tmp_path / "phase-tmp"
    phase.mkdir()
    state, result_path = tmp_path / "state", tmp_path / "result.json"
    child_code = """
import json, sqlite3, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from spireagent.workbench.service_process import service_process
with service_process(Path(sys.argv[2])) as resources:
    time.sleep(0.5)
    with tempfile.TemporaryDirectory(prefix='service-probe-') as scratch:
        db = sqlite3.connect(Path(scratch)/'rows.sqlite')
        try:
            db.execute('CREATE TABLE rows(body TEXT)')
            db.execute('INSERT INTO rows VALUES (?)', ('x'*2097152,))
            db.commit()
            assert db.execute('SELECT length(body) FROM rows').fetchone()[0] == 2097152
        finally:
            db.close()
        Path(sys.argv[3]).write_text(json.dumps({**resources, 'scratch':scratch, 'written':True}))
"""
    launcher = """
import subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from spireagent.workbench.service_process import service_environment
subprocess.Popen([sys.executable,'-c',sys.argv[4],*sys.argv[1:4]],
                 env=service_environment(Path(sys.argv[2])),
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL, start_new_session=True)
"""
    environment = dict(os.environ, TMPDIR=str(phase), TMP=str(phase), TEMP=str(phase))
    result = subprocess.run(
        [sys.executable, "-c", launcher, str(ROOT), str(state), str(result_path), child_code],
        env=environment, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    phase.rmdir()  # The private test launcher phase is now over.
    deadline = time.monotonic() + 8
    while not result_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    report = json.loads(result_path.read_text())
    assert report["written"] is True
    assert report["temporary_directory"] == str((state / "workbench-tmp").resolve())
    assert Path(report["scratch"]).parent == (state / "workbench-tmp").resolve()
    assert report["file_size_limit"] == (
        "not_available_on_platform" if os.name == "nt" else "unlimited")
    assert not Path(report["scratch"]).exists()


def test_service_context_restores_caller_and_rejects_scratch_symlink(tmp_path, monkeypatch):
    import tempfile

    phase = tmp_path / "phase"
    phase.mkdir()
    monkeypatch.setenv("TMPDIR", str(phase))
    monkeypatch.setenv("TMP", str(phase))
    monkeypatch.setenv("TEMP", str(phase))
    old_temp = tempfile.tempdir
    with service_process(tmp_path / "state") as report:
        assert tempfile.gettempdir() == report["temporary_directory"]
    assert tempfile.tempdir == old_temp
    assert os.environ["TMPDIR"] == str(phase)
    bad = tmp_path / "bad"
    bad.mkdir()
    try:
        (bad / "workbench-tmp").symlink_to(phase, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    with pytest.raises(BoundaryError, match="workbench_temp_directory_invalid"):
        service_environment(bad)
