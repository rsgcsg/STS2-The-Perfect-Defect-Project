"""A fast mocked boundary test; it never imports torch or calls Modal."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

MODULE_PATH = (
    Path(__file__).parents[1]
    / "deploy"
    / "cloud-worker"
    / "m0_synthetic_gpu_smoke.py"
)
spec = importlib.util.spec_from_file_location("m0_synthetic_gpu_smoke", MODULE_PATH)
assert spec is not None and spec.loader is not None
smoke = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = smoke
spec.loader.exec_module(smoke)


def test_execute_is_pinned_single_gpu_synthetic_only_and_fails_closed(monkeypatch):
    sha40 = "a" * 40
    sha64 = "b" * 64
    image = "registry.example/project/candidate@sha256:" + "c" * 64
    plan = smoke.build_plan(
        image=image,
        environment="existing-dev",
        source_revision=sha40,
        uv_lock_sha256=sha64,
        harness_sha256=sha64,
        run_id="d" * 32,
    )
    assert plan.input_scope == "synthetic-only"
    assert plan.secrets == ()
    assert plan.volumes == ()
    assert smoke.build_plan(
        image=image,
        environment="existing-dev",
        source_revision=sha40,
        uv_lock_sha256=sha64,
        harness_sha256=sha64,
    ).run_id != smoke.build_plan(
        image=image,
        environment="existing-dev",
        source_revision=sha40,
        uv_lock_sha256=sha64,
        harness_sha256=sha64,
    ).run_id

    class FakeApp:
        def __init__(self, name: str) -> None:
            self.name = name
            self.decorator_options: dict[str, Any] | None = None
            self.remote_payload: dict[str, str] | None = None
            self.run_environment: str | None = None

        def function(self, **options: Any):
            self.decorator_options = options

            def decorate(_function):
                app = self

                class FakeFunction:
                    def remote(self, payload: dict[str, str]):
                        app.remote_payload = payload
                        return {
                            "schema": "spireagent/m0-synthetic-cuda-smoke-receipt-v1",
                            "run_id": payload["run_id"],
                            "input_scope": "synthetic-only",
                        }

                return FakeFunction()

            return decorate

        def run(self, *, environment_name: str):
            app = self

            class RunContext:
                def __enter__(self):
                    app.run_environment = environment_name
                    return app

                def __exit__(self, *_exc):
                    return False

            return RunContext()

    created_apps: list[FakeApp] = []
    listed = {"count": 0}

    class FakeEnvironmentManager:
        @staticmethod
        def list():
            listed["count"] += 1
            return [SimpleNamespace(name="existing-dev")]

    class FakeImage:
        @staticmethod
        def from_registry(reference: str):
            assert reference == image
            return object()

    fake_modal = SimpleNamespace(
        Environment=SimpleNamespace(objects=FakeEnvironmentManager()),
        Image=FakeImage,
        App=lambda name: created_apps.append(FakeApp(name)) or created_apps[-1],
    )
    monkeypatch.setattr(
        smoke, "run_engine_smoke", lambda _payload: pytest.fail("worker ran locally"),
    )
    monkeypatch.setattr(smoke, "_require_cuda_configuration", lambda: None)
    monkeypatch.setitem(sys.modules, "modal", fake_modal)

    receipt = smoke.execute(plan)
    assert receipt["input_scope"] == "synthetic-only"
    assert listed["count"] == 1
    assert len(created_apps) == 1
    app = created_apps[0]
    assert app.name == plan.app_name
    assert app.run_environment == "existing-dev"
    assert app.remote_payload is not None
    assert app.remote_payload["image"] == image
    assert app.remote_payload["source_revision"] == sha40
    assert app.remote_payload["uv_lock_sha256"] == sha64
    assert app.remote_payload["max_billable_seconds"] == 90
    options = app.decorator_options
    assert options is not None
    assert options["serialized"] is True
    assert {
        "gpu": options["gpu"],
        "cpu": options["cpu"],
        "memory": options["memory"],
        "min_containers": options["min_containers"],
        "max_containers": options["max_containers"],
        "retries": options["retries"],
        "timeout": options["timeout"],
        "startup_timeout": options["startup_timeout"],
        "scaledown_window": options["scaledown_window"],
        "block_network": options["block_network"],
    } == {
        "gpu": "L4",
        "cpu": 2.0,
        "memory": 4096,
        "min_containers": 0,
        "max_containers": 1,
        "retries": 0,
        "timeout": 30,
        "startup_timeout": 30,
        "scaledown_window": 30,
        "block_network": True,
    }
    assert "secrets" not in options
    assert "volumes" not in options

    class MissingEnvironmentManager:
        @staticmethod
        def list():
            return []

    missing_modal = SimpleNamespace(
        Environment=SimpleNamespace(objects=MissingEnvironmentManager()),
    )
    with pytest.raises(smoke.PlanError, match="existing_modal_environment_not_found"):
        smoke._preflight_environment(missing_modal, "existing-dev")
    with pytest.raises(smoke.PlanError, match="immutable_registry_digest"):
        smoke.build_plan(
            image="registry.example/project:mutable-tag",
            environment="existing-dev",
            source_revision=sha40,
            uv_lock_sha256=sha64,
            harness_sha256=sha64,
            run_id="e" * 32,
        )
