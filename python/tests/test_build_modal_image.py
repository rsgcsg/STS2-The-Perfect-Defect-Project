"""An ordinary image build must clear a parent's synthetic engineering profile."""
from __future__ import annotations

import importlib.metadata
import json
import runpy
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from spireagent import source
from stpd.cloud_jobs.modal import MODAL_SDK_VERSION


@pytest.mark.parametrize("engineering", [False, True])
def test_builder_explicitly_selects_profile_even_when_parent_enabled(tmp_path, monkeypatch,
                                                                  engineering):
    builder = Path(__file__).resolve().parents[1] / "deploy/cloud-worker/build_modal_image.py"
    owner = runpy.run_path(str(builder))
    revision, lock = "a" * 40, "b" * 64
    producer = SimpleNamespace(source_revision=revision, uv_lock_sha256=lock,
                               to_dict=lambda: {"source_revision": revision,
                                                "uv_lock_sha256": lock})
    monkeypatch.setattr(source, "source_identity", lambda root: producer)
    monkeypatch.setattr(importlib.metadata, "version", lambda name: MODAL_SDK_VERSION)
    monkeypatch.setattr(owner["subprocess"], "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(owner["subprocess"], "check_output",
                        lambda *args, **kwargs: revision + " HEAD\n")

    class Image:
        object_id = "im-" + "B" * 22

        def __init__(self):
            self.environment = {"STPD_P1_ENGINEERING_PROFILE": "1"}

        def add_local_file(self, *args, **kwargs):
            return self

        def run_function(self, *args, **kwargs):
            return self

        def env(self, values):
            self.environment.update(values)
            return self

        def build(self, app):
            assert self.environment["STPD_P1_ENGINEERING_PROFILE"] == (
                "1" if engineering else "0")

    image = Image()
    app = SimpleNamespace(app_id="ap-image-only", run=lambda **kwargs: nullcontext())
    monkeypatch.setitem(sys.modules, "modal", SimpleNamespace(
        App=lambda name: app, Image=SimpleNamespace(from_id=lambda name: image),
        enable_output=nullcontext))
    bundle, output = tmp_path / "candidate.bundle", tmp_path / "receipt.json"
    bundle.write_bytes(b"exact-bundle-test")
    argv = [str(builder), "--parent-image", "im-" + "A" * 22,
            "--parent-source", revision, "--parent-lock", lock, "--source", revision,
            "--bundle", str(bundle), "--environment", "engineering", "--output", str(output)]
    monkeypatch.setattr(sys, "argv", argv + (["--p1-engineering-profile"] if engineering else []))
    assert owner["main"]() == 0
    receipt = json.loads(output.read_text())
    assert receipt["p1_engineering_profile"] is engineering
    assert receipt["state"] == "built" and receipt["compute_calls"] == 0
