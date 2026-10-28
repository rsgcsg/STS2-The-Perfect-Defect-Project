"""Batch reservation admission uses the same node-local lock as the CLI."""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from spireagent.resource_slot import require_managed_slot


def test_managed_admission_requires_live_matching_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("SPIREAGENT_SLOT_ROOT", str(tmp_path))
    monkeypatch.delenv("SPIREAGENT_SLOT_TOKEN", raising=False)
    with pytest.raises(subprocess.CalledProcessError):
        require_managed_slot()
    record = {
        "resource": "managed", "card": "test", "pid": os.getpid(), "token": "test-owner",
        "until": "2099-01-01T00:00:00Z",
    }
    (tmp_path / "managed.json").write_text(json.dumps(record))
    monkeypatch.setenv("SPIREAGENT_SLOT_TOKEN", "wrong")
    with pytest.raises(subprocess.CalledProcessError):
        require_managed_slot()
    monkeypatch.setenv("SPIREAGENT_SLOT_TOKEN", "test-owner")
    require_managed_slot()
