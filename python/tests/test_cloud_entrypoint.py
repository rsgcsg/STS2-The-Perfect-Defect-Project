from __future__ import annotations

import json
import runpy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_artifact_store_v1 import PRODUCER
from test_cloud_jobs import job_fixture
from test_cloud_modal import target

from spireagent.json_boundary import BoundaryError
from stpd.cloud_jobs.__main__ import execute_request, main


def test_entrypoint_requires_actual_clean_matching_checkout_before_opening_store(
    tmp_path: Path, monkeypatch
) -> None:
    store, _, _, _, request = job_fixture(tmp_path)
    monkeypatch.setattr("stpd.cloud_jobs.__main__.open_store", lambda _: store)
    monkeypatch.setattr(
        "stpd.cloud_jobs.__main__.source_identity",
        lambda _: replace(PRODUCER, source_revision="f" * 40),
    )
    with pytest.raises(BoundaryError, match="runtime_source_lock_mismatch"):
        execute_request(request.to_dict())
    monkeypatch.setattr("stpd.cloud_jobs.__main__.source_identity", lambda _: PRODUCER)
    assert execute_request(request.to_dict())["state"] == "candidate_prepared"


def test_entrypoint_failure_redacts_sdk_details(tmp_path: Path, monkeypatch, capsys) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text("{}")

    def fail(*args, **kwargs):
        raise RuntimeError("https://example.invalid?token=never-print")

    monkeypatch.setattr("stpd.cloud_jobs.__main__.execute_request", fail)
    assert main(["--request", str(request_path), "--store", str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "RuntimeError" in output and "never-print" not in output


def test_modal_definition_bounds_runtime_and_invokes_only_locked_worker(
    tmp_path: Path, monkeypatch
):
    selected = target()
    monkeypatch.setenv("STPD_IMAGE_PROFILE", "worker")
    target_file = tmp_path / "target.json"
    target_file.write_text(json.dumps(selected.to_dict()))
    monkeypatch.setenv("STPD_MODAL_TARGET", str(target_file))
    monkeypatch.setattr("spireagent.source.source_identity", lambda _: PRODUCER)
    calls = {}

    class App:
        def __init__(self, name):
            calls["app_name"] = name

        def function(self, **kwargs):
            calls["settings"] = kwargs
            return lambda function: function

    def image(image, **kwargs):
        calls["image"] = image
        return object()

    import importlib

    actual_import = importlib.import_module
    fake = SimpleNamespace(
        App=App,
        Image=SimpleNamespace(from_registry=image),
        Secret=SimpleNamespace(from_name=lambda name: name),
    )
    monkeypatch.setattr(
        importlib, "import_module", lambda name: fake if name == "modal" else actual_import(name)
    )
    namespace = runpy.run_path(str(Path(__file__).parents[1] / "deploy/cloud-worker/modal_app.py"))
    assert calls["app_name"] == selected.app_name and calls["image"] == selected.image
    assert calls["settings"]["retries"] == 0
    assert calls["settings"]["max_containers"] == 1
    assert calls["settings"]["min_containers"] == 0
    assert calls["settings"]["timeout"] == selected.timeout_seconds
    assert calls["settings"]["cpu"] == (selected.cpu_cores, selected.cpu_cores)
    assert calls["settings"]["memory"] == (selected.memory_mib, selected.memory_mib)

    def run(argv, **kwargs):
        assert argv[:4] == [
            "/opt/stpd/python/.venv/bin/python",
            "-m",
            "stpd.cloud_jobs",
            "--request",
        ]
        assert argv[5:] == ["--wall-seconds", str(selected.timeout_seconds)]
        assert kwargs["cwd"] == "/opt/stpd/python"
        assert json.loads(Path(argv[4]).read_text()) == {"example": "request"}
        return SimpleNamespace(returncode=0, stdout='{"example":"receipt"}')

    monkeypatch.setattr("subprocess.run", run)
    compute = namespace["compute"]
    assert compute({"example": "request"}, selected.target_id) == {
        "target_id": selected.target_id,
        "receipt": {"example": "receipt"},
    }
    monkeypatch.setenv("STPD_IMAGE_PROFILE", "hub")
    with pytest.raises(ValueError, match="qualified_worker_image_required"):
        compute({}, selected.target_id)
    monkeypatch.setenv("STPD_IMAGE_PROFILE", "worker")
    with pytest.raises(ValueError, match="deployed_target_mismatch"):
        compute({}, "different")
    monkeypatch.setattr(
        "spireagent.source.source_identity",
        lambda _: replace(PRODUCER, source_revision="f" * 40),
    )
    with pytest.raises(BoundaryError, match="wrapper_source_lock_mismatch"):
        runpy.run_path(str(Path(__file__).parents[1] / "deploy/cloud-worker/modal_app.py"))
