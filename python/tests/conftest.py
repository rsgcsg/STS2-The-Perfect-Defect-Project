"""Guard against process-global Torch inference settings leaking between tests.

`LocalMuScorer` turns off the MHA fastpath for its whole (actor) process by
design. A test module that builds one in the pytest process must use
`rl_actor_test_runtime.isolated_actor_runtime`; otherwise later tests in the
same shard silently run a different attention path (stage1a's in-process
scores then differ from its standalone subprocess). This fixture fails the
leaking test by name and restores the setting so the leak does not cascade.
Torch is only consulted when a test has already imported it.
"""

from __future__ import annotations

import sys

import pytest


def _fastpath() -> bool | None:
    torch = sys.modules.get("torch")
    return None if torch is None else bool(torch.backends.mha.get_fastpath_enabled())


@pytest.fixture(autouse=True)
def mha_fastpath_unchanged():
    before = _fastpath()
    yield
    after = _fastpath()
    if before is None:
        before = True  # Torch's default when the test imported it first
    if after is not None and after != before:
        sys.modules["torch"].backends.mha.set_fastpath_enabled(before)
        pytest.fail("test left torch MHA fastpath changed; use "
                    "rl_actor_test_runtime.isolated_actor_runtime", pytrace=False)
