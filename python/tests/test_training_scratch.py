"""Operation-wide scratch limits with retained actual child attempts."""

from __future__ import annotations

import json
import time
from dataclasses import replace

import pytest
from test_training_service_contracts import child_script, ready, settle

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.local_training import OPERATION_FILE


def test_retained_scratch_is_cumulative_across_forced_child_attempts(tmp_path, monkeypatch):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    request = replace(request, limits={"wall_seconds": 600, "scratch_bytes": 16*1024*1024})
    markers = tmp_path/"spills"
    markers.mkdir()
    child_script(monkeypatch, """
import time, tempfile
from pathlib import Path
from stpd.models.structured_engine import StructuredTrainingEngine
def spill_and_hang(self):
    folder = Path(tempfile.gettempdir())
    (folder/'spill').write_bytes(b'x'*(9*1024*1024))
    (Path(""" + repr(str(markers)) + """)/folder.name).write_text('ready')
    time.sleep(20)
StructuredTrainingEngine.advance_chunk = spill_and_hang
""")
    first = service.start(request)["operation"]
    deadline = time.monotonic()+15
    while len(list(markers.iterdir())) < 1:
        assert service._thread.is_alive(), service.status()
        assert time.monotonic() < deadline
        time.sleep(.02)
    service.cancel(first["operation_id"], first["attempt_id"])
    cancelled = settle(service)
    assert cancelled["status"] == "cancelled" and cancelled["checkpoint_id"]
    assert cancelled["child_exit"]["forced"] and cancelled["worker_state"] == "terminal"
    path = owner.path.parent/OPERATION_FILE
    first_journal = json.loads(path.read_bytes())
    retained = path.parent/first_journal["scratch_name"]/"spill"
    assert retained.stat().st_size == 9*1024*1024
    service.resume(cancelled["operation_id"], cancelled["attempt_id"],
                   cancelled["checkpoint_id"], "2"*32, request.limits)
    stopped = settle(service)
    assert stopped["status"] == "interrupted_unknown", stopped
    assert stopped["error"]["code"] == "scratch_budget_exhausted", stopped
    assert stopped["child_exit"]["forced"] and stopped["worker_state"] == "terminal"
    assert stopped["child_exit"]["exit_code"] != 0 and stopped["checkpoint_id"]
    assert len(list(markers.iterdir())) == 2
    assert retained.stat().st_size == 9*1024*1024  # No cleanup of retained evidence.
    before = path.read_bytes()
    artifacts = set(store.manifest_ids())
    with pytest.raises(BoundaryError, match="cumulative_budget_exhausted"):
        service.resume(stopped["operation_id"], stopped["attempt_id"],
                       stopped["checkpoint_id"], "3"*32, request.limits)
    assert path.read_bytes() == before
    assert set(store.manifest_ids()) == artifacts
    assert not any(store.get_manifest(item).kind in {"model", "run_result"}
                   for item in artifacts)


@pytest.mark.parametrize("inventory", ["exhausted", "root_symlink", "nested_symlink"])
def test_resume_rejects_retained_inventory_before_mutation(tmp_path, monkeypatch, inventory):
    service, request, owner, store = ready(tmp_path, monkeypatch)
    request = replace(request, limits={"wall_seconds": 600, "scratch_bytes": 16*1024*1024})
    child_script(monkeypatch, """
import time
def hung(args, channel):
    channel.emit('started')
    time.sleep(20)
child.run_child = hung
""")
    operation = service.start(request)["operation"]
    service.cancel(operation["operation_id"], operation["attempt_id"])
    cancelled = settle(service)
    assert cancelled["status"] == "cancelled" and cancelled["worker_state"] == "terminal"
    path = owner.path.parent/OPERATION_FILE
    journal = json.loads(path.read_bytes())
    scratch = path.parent/journal["scratch_name"]
    if inventory == "exhausted":
        (scratch/"spill").write_bytes(b'x'*(16*1024*1024))
        code = "cumulative_budget_exhausted"
    else:
        external = tmp_path/"unowned"
        external.mkdir()
        sentinel = external/"preserved"
        sentinel.write_text("unowned")
        link = scratch/"unsafe"
        if inventory == "root_symlink":
            scratch.rmdir()
            link = scratch
        try:
            link.symlink_to(external, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation unavailable on this host")
        code = "scratch_inventory_invalid"
    before = path.read_bytes()
    artifacts = set(store.manifest_ids())
    with pytest.raises(BoundaryError, match=code):
        service.resume(cancelled["operation_id"], cancelled["attempt_id"],
                       "0"*64, "2"*32, request.limits)
    assert path.read_bytes() == before
    assert set(store.manifest_ids()) == artifacts
    if inventory != "exhausted":
        assert sentinel.read_text() == "unowned"
