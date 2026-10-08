"""Standalone owner-lock tests: stdlib/pytest, no training or service setup."""

from __future__ import annotations

import importlib
import os
import selectors
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.instance_lock import instance_lock

lock_module = importlib.import_module("spireagent.workbench.instance_lock")

PROBE = """
import sys
from pathlib import Path
from spireagent.json_boundary import BoundaryError
from spireagent.workbench.instance_lock import instance_lock
try:
    with instance_lock(Path(sys.argv[1]), create=False):
        print('acquired')
except BoundaryError as error:
    print(error.code)
"""


def _probe(path: Path) -> str:
    result = subprocess.run([sys.executable, "-c", PROBE, str(path)],
                            capture_output=True, text=True, timeout=5, check=True)
    assert result.stderr == ""
    return result.stdout.strip()


@pytest.mark.skipif(os.name == "nt", reason="Real POSIX flock contention; Windows adapter is covered separately")
def test_real_posix_owner_blocks_other_process_and_releases_without_deleting_file(tmp_path):
    path = tmp_path / "owner.lock"
    path.write_bytes(b"existing owner path")
    inode = path.stat().st_ino
    with instance_lock(path, create=False):
        assert _probe(path) == "already_running"
    assert _probe(path) == "acquired"
    assert path.stat().st_ino == inode
    assert path.read_bytes() == b"existing owner path"


@pytest.mark.skipif(os.name == "nt", reason="Real POSIX process-lifetime owner; Windows byte adapter is covered separately")
def test_retained_lifetime_owner_remains_locked_after_report_until_process_exit(tmp_path):
    path = tmp_path / "child.lock"
    path.touch()
    script = """
import sys
from pathlib import Path
from spireagent.workbench.instance_lock import instance_lock
_LIFECYCLE_LOCK = instance_lock(Path(sys.argv[1]), create=False)
_LIFECYCLE_LOCK.__enter__()
print('ready', flush=True)
for command in sys.stdin:
    if command.strip() == 'report':
        print('report-completed', flush=True)
    elif command.strip() == 'exit':
        break
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None and child.stdin is not None
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout, selectors.EVENT_READ)
            assert ready.select(5), "Owner child did not reach acquisition"
            assert child.stdout.readline().strip() == "ready"
            child.stdin.write("report\n")
            child.stdin.flush()
            assert ready.select(5), "Owner child did not report"
            assert child.stdout.readline().strip() == "report-completed"
        assert _probe(path) == "already_running"
        stdout, stderr = child.communicate("exit\n", timeout=5)
        assert child.returncode == 0
        assert stdout == ""
        assert stderr == ""
        assert _probe(path) == "acquired"
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


def test_late_generator_finalization_requires_no_os_import(tmp_path):
    path = tmp_path / "late.lock"
    path.touch()
    # Deterministic reproduction of the actual teardown mechanism: the owner
    # was acquired before imports became unavailable; generator finalization
    # executes the same finally after its OS entry leaves the import table.
    script = """
import gc, os, sys
from pathlib import Path
from spireagent.workbench.instance_lock import instance_lock
_LIFECYCLE_LOCK = instance_lock(Path(sys.argv[1]), create=False)
_LIFECYCLE_LOCK.__enter__()
assert 'torch' not in sys.modules
sys.modules.pop('msvcrt' if os.name == 'nt' else 'fcntl', None)
sys.meta_path = None
_LIFECYCLE_LOCK = None
gc.collect()
"""
    result = subprocess.run([sys.executable, "-c", script, str(path)],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert result.stderr == "", result.stderr
    assert _probe(path) == "acquired"


def test_acquired_cleanup_has_no_helper_module_global_lookup(tmp_path, monkeypatch):
    path = tmp_path / "globals.lock"
    with instance_lock(path):
        # Replace this helper's bindings only, not the process os/importlib modules.
        monkeypatch.setattr(lock_module, "os", None)
        monkeypatch.setattr(lock_module, "importlib", None)
    monkeypatch.undo()
    assert _probe(path) == "acquired"


def test_windows_adapter_keeps_byte_zero_modes_callable_and_platform_bound(tmp_path, monkeypatch):
    path = tmp_path / "windows.lock"
    path.touch()
    calls = []
    imports = []

    def locking(fd, mode, count):
        calls.append((fd, mode, count, os.lseek(fd, 0, os.SEEK_CUR), os.fstat(fd).st_size))

    adapter = SimpleNamespace(locking=locking, LK_NBLCK=41, LK_UNLCK=43)
    monkeypatch.setattr(lock_module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(lock_module, "importlib", SimpleNamespace(
        import_module=lambda name: imports.append(name) or adapter))
    with instance_lock(path, create=False):
        fd = calls[0][0]
        os.lseek(fd, 17, os.SEEK_SET)
        adapter.locking = lambda *_: pytest.fail("Cleanup replaced acquired callable")
        adapter.LK_UNLCK = 99
        monkeypatch.setattr(lock_module, "os", None)
        monkeypatch.setattr(lock_module, "importlib", None)
    assert imports == ["msvcrt"]
    assert calls == [(fd, 41, 1, 0, 0), (fd, 43, 1, 0, 0)]
    assert path.read_bytes() == b""


@pytest.mark.parametrize("windows", [False, True])
def test_acquisition_os_error_keeps_already_running_and_never_unlocks(tmp_path, monkeypatch, windows):
    calls = []

    def fail(fd, mode, *count):
        calls.append((mode, count))
        raise OSError("another owner")

    adapter = SimpleNamespace(locking=fail, LK_NBLCK=41, LK_UNLCK=43,
                              flock=fail, LOCK_EX=4, LOCK_NB=8, LOCK_UN=16)
    monkeypatch.setattr(lock_module, "os", SimpleNamespace(name="nt" if windows else "posix"))
    monkeypatch.setattr(lock_module, "importlib", SimpleNamespace(import_module=lambda _: adapter))
    with pytest.raises(BoundaryError) as failure:
        with instance_lock(tmp_path / "busy.lock"):
            pytest.fail("Failed acquisition entered the owner body")
    assert failure.value.code == "already_running"
    assert calls == [(41, (1,))] if windows else calls == [(12, ())]


@pytest.mark.parametrize("windows", [False, True])
def test_unlock_failure_propagates_without_swallowing_or_reclassifying(tmp_path, monkeypatch, windows):
    calls = []

    def locking(fd, mode, *count):
        calls.append((mode, count))
        if mode in (43, 16):
            raise OSError("unlock failed")

    adapter = SimpleNamespace(locking=locking, LK_NBLCK=41, LK_UNLCK=43,
                              flock=locking, LOCK_EX=4, LOCK_NB=8, LOCK_UN=16)
    monkeypatch.setattr(lock_module, "os", SimpleNamespace(name="nt" if windows else "posix"))
    monkeypatch.setattr(lock_module, "importlib", SimpleNamespace(import_module=lambda _: adapter))
    with pytest.raises(OSError, match="unlock failed"):
        with instance_lock(tmp_path / "unlock.lock"):
            pass
    assert len(calls) == 2
    assert calls[-1] == (43, (1,)) if windows else calls[-1] == (16, ())


def test_observation_lock_does_not_create_missing_owner_and_keeps_existing_bytes(tmp_path):
    path = tmp_path / "absent" / "owner.lock"
    with pytest.raises(FileNotFoundError):
        with instance_lock(path, create=False):
            pytest.fail("Missing observation owner must not be initialized")
    assert not path.parent.exists()
    path.parent.mkdir()
    path.write_bytes(b"existing")
    with instance_lock(path, create=False):
        with pytest.raises(BoundaryError, match="already_running"):
            with instance_lock(path, create=False):
                pytest.fail("A second owner must not acquire the same lock")
    assert path.read_bytes() == b"existing"
