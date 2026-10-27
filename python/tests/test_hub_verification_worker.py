from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from spireagent.hub.verification_worker import run_verifier


@pytest.mark.parametrize("cancel", [False, True])
def test_supervisor_reaps_actual_child_on_deadline_or_shutdown(cancel: bool) -> None:
    original = subprocess.Popen
    children = []
    scratches = []

    def spawn(*args: object, **kwargs: object) -> subprocess.Popen:
        scratch = Path(kwargs["env"]["TMPDIR"])
        (scratch / "expanded-partial").write_bytes(b"retained only during child lifetime")
        scratches.append(scratch)
        child = original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
        children.append(child)
        return child

    shutdown = threading.Event()
    if cancel:
        shutdown.set()
    with (
        patch("spireagent.hub.verification_worker.subprocess.Popen", side_effect=spawn),
        patch(
            "spireagent.hub.verification_worker.filesystem_capacity", return_value={"status": "ok"}
        ),
    ):
        failures = []
        assert not run_verifier([], timeout=0.15, shutdown=shutdown, on_failure=failures.append)
    assert failures == ["worker_interrupted" if cancel else "worker_timeout"]
    # The 30 s child was terminated (nonzero), not awaited until it exited by itself.
    assert len(children) == 1 and children[0].poll() not in (None, 0)
    assert all(not path.exists() for path in scratches)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux deployment process resource contract")
def test_linux_address_space_limit_rejects_oversized_allocation() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from spireagent.hub.verification_worker import constrain_worker; "
            "constrain_worker(); bytearray(2 * 1024**3)",
        ],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode != 0 and b"MemoryError" in result.stderr

@pytest.mark.skipif(sys.platform == "win32", reason="Unix resource module")
@pytest.mark.parametrize("dataset,cpu", [(False, 120), (True, 600)])
def test_batch_dataset_has_its_own_cpu_bound_without_changing_memory_bound(dataset, cpu):
    import resource

    from spireagent.hub.verification_worker import constrain_worker

    with patch("spireagent.hub.verification_worker.sys.platform", "linux"), patch.object(
        resource, "setrlimit"
    ) as limit:
        constrain_worker(dataset=dataset)
    assert limit.call_args_list[0].args == (resource.RLIMIT_AS, (1536 * 1024**2,) * 2)
    assert limit.call_args_list[1].args == (resource.RLIMIT_CPU, (cpu, cpu + 5))
    assert limit.call_args_list[2].args == (resource.RLIMIT_FSIZE, (3 * 1024**3,) * 2)
