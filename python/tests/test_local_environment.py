from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.package_identity import directory_sha256
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.developer_server import instance_lock
from spireagent.workbench.local_environment import (
    HOST_PACKAGE,
    JOURNAL_FILE,
    PROFILE_FILE,
    SCENARIO,
    LocalEnvironmentService,
    configure_managed_host,
)


def fixture(tmp_path: Path):
    host = tmp_path / "package"
    (host / "consumers/python/sts2_headless").mkdir(parents=True)
    (host / "consumers/python/sts2_headless/__init__.py").write_text("")
    (host / "consumers/python/sts2_headless/client.py").write_text("# fixture\n")
    (host / "tools").mkdir()
    for name in ("managed-exact.mjs", "managed-pe-driver.mjs", "reference-pe-driver.mjs"):
        (host / "tools" / name).write_text("// fixture\n")
    (host / "package.json").write_text(
        json.dumps(
            {
                "name": HOST_PACKAGE,
                "version": "1.1.0-rc.20",
            }
        )
    )
    pin = {
        "schema": "stpd/platform-host-runtime-pin-v1",
        "package": HOST_PACKAGE,
        "version": "1.1.0-rc.20",
        "source_revision": "a" * 40,
        "component_tree_revision": "b" * 40,
        "release_asset_sha256": "c" * 64,
        "package_content_sha256": directory_sha256(host),
    }
    combo = combination()
    combo["node_packages"] = [
        pin if item["package"] == HOST_PACKAGE else item for item in combo["node_packages"]
    ]
    state = tmp_path / "state"
    state.mkdir()
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    config = ProjectConfig(state, "", "", None, combo)
    audit = {
        "upstream_revision": "d" * 40,
        "source_patch_sha256": SCENARIO["source_patch_sha256"],
        "artifact_sha256": SCENARIO["artifact_sha256"],
        "artifact_mvid": SCENARIO["artifact_mvid"],
        "original_sts2_sha256": SCENARIO["exact_game_assembly_sha256"],
        "runtime_sts2_sha256": SCENARIO["exact_game_assembly_sha256"],
    }

    def checked(_host: Path, _candidate: Path) -> dict[str, str]:
        assert _host == host and _candidate == candidate
        return audit.copy()

    configure_managed_host(
        config, candidate, host_root=host, host_pin=pin, input_profile="text-menu-v1", audit=checked
    )
    return config, host, candidate, pin, audit, checked


def snapshot(number: int) -> dict[str, Any]:
    return {
        "schema": "sts2.player-environment/text-menu-snapshot-1",
        "input_profile": "text-menu-v1",
        "snapshot_id": f"page-{number}",
        "status": "interactive",
        "interaction": {"kind": "map_navigation", "content": {"surface": {"kind": "map"}}},
        "persistent": {"content": {"run": {"floor": number}}},
        "menu_actions": {
            "status": "complete",
            "materialized_count": 1,
            "total_count": 1,
            "actions": [
                {
                    "action_id": f"action-{number}",
                    "kind": "native_input",
                    "verb": "select",
                    "label": "Choose route",
                    "effect_domain": "native_input",
                }
            ],
        },
    }


class PublicClientFixture:
    def __init__(
        self, audit: dict[str, str], *, blocked: bool = False, unknown: bool = False
    ) -> None:
        self.ready = {
            "protocol": "sts2.headless/managed-player-environment-driver-1",
            "candidate_build": audit.copy(),
            "exact_game": {"sts2_dll_sha256": SCENARIO["exact_game_assembly_sha256"]},
        }
        self.blocked = blocked
        self.unknown = unknown
        self.fail_close = False
        self.offered = threading.Event()
        self.release = threading.Event()
        self.closed = False
        self.force_closed = False
        self.submits = 0
        self.observations = 0

    def reset(self, seed: str) -> dict[str, Any]:
        assert seed == SCENARIO["seed"]
        return snapshot(0)

    def episode_identity(self) -> dict[str, Any]:
        return {
            "candidate_build": self.ready["candidate_build"],
            "environment_fingerprint": "e" * 64,
            "episode_provenance": {
                "verdict": "provenance_pass",
                "requested_seed": SCENARIO["seed"],
                "actual_seed": SCENARIO["seed"],
                "runtime_instance_id": "instance-1",
            },
        }

    def observe_text_menu(self) -> dict[str, Any]:
        page = snapshot(self.observations)
        self.observations += 1
        return {
            "schema": "sts2.player-environment/text-menu-observation-context-1",
            "snapshot": page,
            "game_continuity_id": "episode-1",
        }

    def submit_text_menu(
        self, action_id: str, snapshot_id: str, continuity_id: str, request_id: str
    ) -> dict[str, Any]:
        self.submits += 1
        self.offered.set()
        assert (action_id, snapshot_id, continuity_id) == ("action-0", "page-0", "episode-1")
        if self.blocked:
            self.release.wait(timeout=5)
            raise RuntimeError("driver stopped after offer")
        if self.unknown:
            return {
                "protocol_version": "1.0.0",
                "schema": "sts2.player-environment/text-menu-action-result-1",
                "input_profile": "text-menu-v1",
                "request_id": request_id,
                "status": "unknown",
                "effect_domain": "native_input",
                "native_delivery": "unknown",
                "action": {"action_id": action_id},
                "reason_code": "delivery_unknown",
                "detail": "",
                "retry": "never",
                "successor": None,
                "attribution": None,
            }
        return {
            "protocol_version": "1.0.0",
            "schema": "sts2.player-environment/text-menu-action-result-1",
            "input_profile": "text-menu-v1",
            "request_id": request_id,
            "status": "applied",
            "effect_domain": "native_input",
            "native_delivery": "delivered",
            "action": {"action_id": action_id},
            "reason_code": "accepted",
            "detail": "",
            "retry": "never",
            "successor": snapshot(1),
            "attribution": None,
        }

    def close(self, force: bool = False) -> None:
        if self.fail_close:
            raise RuntimeError("child cleanup failed")
        self.closed = True
        self.force_closed = force
        self.release.set()


def wait_status(service: LocalEnvironmentService, expected: str) -> dict[str, Any]:
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        value = service.status()
        if value["session"]["status"] == expected and (
            expected not in {"failed", "stopped", "stopped_outcome_unknown"}
            or value["session"].get("report_artifact_id")
        ):
            return value
        time.sleep(0.01)
    raise AssertionError(f"expected {expected}, observed {service.status()}")


def pause_startup_after_active_save(
    service: LocalEnvironmentService,
) -> tuple[threading.Event, threading.Event]:
    handed_off = threading.Event()
    resume_startup = threading.Event()
    underlying = service.lock

    class PauseAfterActiveSave:
        def __enter__(self):
            underlying.acquire()
            return self

        def __exit__(self, _type, _value, _traceback):
            pause = (threading.current_thread() is service.worker
                     and service.record.get("status") == "active"
                     and not handed_off.is_set())
            underlying.release()
            if pause:
                handed_off.set()
                assert resume_startup.wait(timeout=5)

    service.lock = PauseAfterActiveSave()
    return handed_off, resume_startup


def test_named_fixed_seed_scene_restarts_fresh_and_compares_closed_reports(tmp_path: Path) -> None:
    config, _host, _candidate, pin, audit, checked = fixture(tmp_path)
    clients = []

    class IndependentClient(PublicClientFixture):
        def __init__(self, number: int) -> None:
            super().__init__(audit)
            self.number = number

        def episode_identity(self) -> dict[str, Any]:
            value = super().episode_identity()
            value["episode_provenance"]["runtime_instance_id"] = f"instance-{self.number}"
            return value

    def make_client(_command: list[str], _host: Path, _pin: dict[str, Any]) -> IndependentClient:
        client = IndependentClient(len(clients) + 1)
        clients.append(client)
        return client

    service = LocalEnvironmentService(config, audit=checked, client_factory=make_client)
    scene = service.save_scene("A0 repeat")
    assert scene["seed"] == SCENARIO["seed"]
    assert scene["host_package_pin"] == pin
    assert "candidate_directory" not in json.dumps(scene)
    assert service.scenes()["items"][0]["artifact_id"] == scene["artifact_id"]
    reports = []
    for _ in range(2):
        started = service.start(SCENARIO["id"], scene_artifact_id=scene["artifact_id"])
        session_id = started["session"]["session_id"]
        active = wait_status(service, "active")["session"]
        assert active["initial_context"]["snapshot"]["menu_actions"]["status"] == "complete"
        service.stop(session_id)
        finished = wait_status(service, "stopped")["session"]
        reports.append(finished["report_artifact_id"])
    assert clients[0] is not clients[1] and all(client.closed for client in clients)
    comparison = service.compare(scene["artifact_id"], reports)
    assert comparison["status"] == "verified_fixed_seed_starts"
    assert [run["runtime_instance_id"] for run in comparison["runs"]] == [
        "instance-1", "instance-2"
    ]
    assert [run["initial_menu_count"] for run in comparison["runs"]] == [1, 1]
    store = service._report_store(create=False)
    assert store is not None
    manifest = store.get_manifest(comparison["artifact_id"])
    assert manifest.parent("scene") == scene["artifact_id"]
    assert manifest.parent("report-0") == reports[0]
    assert service.comparisons()["items"][0]["artifact_id"] == comparison["artifact_id"]
    assert service.comparison(comparison["artifact_id"])["runs"] == comparison["runs"]


def test_saved_scene_requires_current_profile_and_old_reports_cannot_prove_repeatability(
    tmp_path: Path,
) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    clients = []
    service = LocalEnvironmentService(
        config, audit=checked,
        client_factory=lambda *_: clients.append(PublicClientFixture(audit)) or clients[-1],
    )
    scene = service.save_scene("Original")
    assert not clients
    with pytest.raises(BoundaryError, match="scene_name_invalid"):
        service.save_scene(" ")
    profile_path = config.state_dir / PROFILE_FILE
    profile = json.loads(profile_path.read_text())
    profile["input_profile"] = "text-menu-v2"
    profile_path.write_text(json.dumps(profile))
    with pytest.raises(BoundaryError, match="scene_profile_mismatch"):
        service.start(SCENARIO["id"], scene_artifact_id=scene["artifact_id"])
    assert not clients
    profile["input_profile"] = "text-menu-v1"
    profile_path.write_text(json.dumps(profile))
    report_ids = []
    for _ in range(2):
        session = service.start(SCENARIO["id"])["session"]
        wait_status(service, "active")
        service.stop(session["session_id"])
        report_ids.append(wait_status(service, "stopped")["session"]["report_artifact_id"])
    with pytest.raises(BoundaryError, match="repeatability_proof_unavailable"):
        service.compare(scene["artifact_id"], report_ids)


def test_comparison_rejects_two_closed_reports_with_same_runtime_instance(tmp_path: Path) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    service = LocalEnvironmentService(
        config, audit=checked, client_factory=lambda *_: PublicClientFixture(audit),
    )
    scene_id = service.save_scene("Repeated identity")["artifact_id"]
    report_ids = []
    for _ in range(2):
        session = service.start(SCENARIO["id"], scene_artifact_id=scene_id)["session"]
        wait_status(service, "active")
        service.stop(session["session_id"])
        report_ids.append(wait_status(service, "stopped")["session"]["report_artifact_id"])
    with pytest.raises(BoundaryError, match="same_runtime_instance"):
        service.compare(scene_id, report_ids)


def test_exact_profile_start_one_explicit_action_stop_and_immutable_report(tmp_path: Path) -> None:
    config, host, candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit)
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    assert service.status()["session"] == {"status": "idle"}
    assert service.reports()["items"] == []
    pending = service.start(SCENARIO["id"])
    assert pending["session"]["status"] in {"starting", "active"}
    active = wait_status(service, "active")
    session_id = active["session"]["session_id"]
    with pytest.raises(BoundaryError, match="stale_text_context"):
        service.submit(session_id, "action-0", "other", "episode-1")
    with pytest.raises(BoundaryError, match="action_not_in_current_menu"):
        service.submit(session_id, "forged", "page-0", "episode-1")
    service.submit(session_id, "action-0", "page-0", "episode-1")
    next_page = wait_status(service, "active")
    assert next_page["session"]["context"]["snapshot"]["snapshot_id"] == "page-1"
    assert client.submits == 1
    stopped = service.stop(session_id)
    assert stopped["session"]["status"] == "stopped"
    assert client.closed and not client.force_closed
    report_id = stopped["session"]["report_artifact_id"]
    report = service.report(report_id)
    event_id = report["events"][0]["event_artifact_id"]
    assert service.event(event_id)["result"]["status"] == "applied"
    assert service.event(event_id)["before_context"]["snapshot"]["snapshot_id"] == "page-0"
    assert service.event(event_id)["before_context"]["game_continuity_id"] == "episode-1"
    assert str(candidate) not in json.dumps(report)
    assert service.reports()["items"][0]["artifact_id"] == report_id
    assert service.stop(session_id)["session"]["report_artifact_id"] == report_id


def test_report_retains_session_host_pin_without_private_paths_or_current_profile_lookup(
    tmp_path: Path,
) -> None:
    config, host, candidate, pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit)
    service = LocalEnvironmentService(
        config, audit=checked, client_factory=lambda _command, _host, _pin: client,
    )
    started = service.start(SCENARIO["id"])
    session_id = started["session"]["session_id"]
    try:
        active = wait_status(service, "active")
        assert active["session"]["host_package_pin"] == pin
    finally:
        stopped = service.stop(session_id)
    report_id = stopped["session"]["report_artifact_id"]
    report = service.report(report_id)
    assert report["host_package_pin"] == pin
    assert report["episode_identity"]["candidate_build"] == audit
    assert str(host) not in json.dumps(report)
    assert str(candidate) not in json.dumps(report)
    profile_path = config.state_dir / PROFILE_FILE
    profile = json.loads(profile_path.read_text())
    profile["host_package_pin"]["source_revision"] = "f" * 40
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    assert service.report(report_id) == report
    assert client.closed


def test_declared_v2_profile_uses_public_selection_and_exact_context(tmp_path: Path) -> None:
    config, host, candidate, pin, audit, checked = fixture(tmp_path)
    (config.state_dir / PROFILE_FILE).unlink()
    configure_managed_host(
        config,
        candidate,
        host_root=host,
        host_pin=pin,
        input_profile="text-menu-v2",
        audit=checked,
    )

    def page(number: int) -> dict[str, Any]:
        value = snapshot(number)
        value.update(
            schema="sts2.player-environment/text-menu-snapshot-2",
            input_profile="text-menu-v2",
            menu={"cursor": "root", "revision": number, "selection": []},
        )
        value["menu_actions"]["actions"][0].update(
            kind="system_selection",
            effect_domain="text_menu",
        )
        return value

    class V2Client(PublicClientFixture):
        def observe_text_menu(self, *, input_profile: str = "text-menu-v1") -> dict[str, Any]:
            assert input_profile == "text-menu-v2"
            observed = page(self.observations)
            self.observations += 1
            return {
                "schema": "sts2.player-environment/text-menu-observation-context-2",
                "snapshot": observed,
                "game_continuity_id": "episode-1",
            }

        def submit_text_menu(
            self,
            action_id: str,
            snapshot_id: str,
            continuity_id: str,
            request_id: str,
            *,
            input_profile: str = "text-menu-v1",
        ) -> dict[str, Any]:
            assert input_profile == "text-menu-v2"
            self.submits += 1
            assert (action_id, snapshot_id, continuity_id) == (
                "action-0",
                "page-0",
                "episode-1",
            )
            return {
                "protocol_version": "1.0.0",
                "schema": "sts2.player-environment/text-menu-action-result-2",
                "input_profile": "text-menu-v2",
                "request_id": request_id,
                "status": "applied",
                "effect_domain": "text_menu",
                "native_delivery": None,
                "action": {
                    "action_id": action_id,
                    "kind": "system_selection",
                    "effect_domain": "text_menu",
                },
                "reason_code": "accepted",
                "detail": "",
                "retry": "never",
                "successor": page(1),
                "attribution": None,
            }

    client = V2Client(audit)
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    assert service.status()["input_profile"] == "text-menu-v2"
    service.start(SCENARIO["id"])
    active = wait_status(service, "active")["session"]
    assert active["context"]["snapshot"]["schema"].endswith("snapshot-2")
    service.submit(active["session_id"], "action-0", "page-0", "episode-1")
    next_page = wait_status(service, "active")["session"]
    assert next_page["context"]["snapshot"]["snapshot_id"] == "page-1"
    final = service.stop(active["session_id"])["session"]
    event_id = service.report(final["report_artifact_id"])["events"][0]["event_artifact_id"]
    assert service.event(event_id)["result"]["native_delivery"] is None


def test_v2_profile_rejects_v1_context_after_public_observation(tmp_path: Path) -> None:
    config, host, candidate, pin, audit, checked = fixture(tmp_path)
    (config.state_dir / PROFILE_FILE).unlink()
    configure_managed_host(
        config,
        candidate,
        host_root=host,
        host_pin=pin,
        input_profile="text-menu-v2",
        audit=checked,
    )

    class MismatchedContextClient(PublicClientFixture):
        def observe_text_menu(self, *, input_profile: str = "text-menu-v1") -> dict[str, Any]:
            assert input_profile == "text-menu-v2"
            return super().observe_text_menu()

    client = MismatchedContextClient(audit)
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    service.start(SCENARIO["id"])
    failed = wait_status(service, "failed")["session"]
    assert failed["error_code"] == "text_context_invalid"
    assert client.closed and client.force_closed


def test_full_large_receipt_is_stored_once_outside_compact_session_journal(tmp_path: Path) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)

    class LargeResultClient(PublicClientFixture):
        def submit_text_menu(
            self,
            action_id: str,
            snapshot_id: str,
            continuity_id: str,
            request_id: str,
        ) -> dict[str, Any]:
            result = super().submit_text_menu(
                action_id,
                snapshot_id,
                continuity_id,
                request_id,
            )
            result["detail"] = "public-visible-detail-" * 30000
            return result

    client = LargeResultClient(audit)
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    service.start(SCENARIO["id"])
    session_id = wait_status(service, "active")["session"]["session_id"]
    service.submit(session_id, "action-0", "page-0", "episode-1")
    active = wait_status(service, "active")["session"]
    event_id = active["events"][0]["event_artifact_id"]
    assert len((config.state_dir / JOURNAL_FILE).read_bytes()) < 10000
    assert service.event(event_id)["result"]["detail"] == "public-visible-detail-" * 30000
    final = service.stop(session_id)["session"]
    report = service.report(final["report_artifact_id"])
    assert report["events"][0]["event_artifact_id"] == event_id
    store = service._report_store(create=False)
    assert store is not None
    assert store.get_manifest(final["report_artifact_id"]).parent("event-0") == event_id
    assert len(json.dumps(report)) < 10000


def test_stop_interrupts_inflight_submit_without_a_second_delivery(tmp_path: Path) -> None:
    config, host, _candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit, blocked=True)
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    service.start(SCENARIO["id"])
    active = wait_status(service, "active")
    session_id = active["session"]["session_id"]
    service.submit(session_id, "action-0", "page-0", "episode-1")
    assert client.offered.wait(timeout=2)
    stopped = service.stop(session_id)
    assert stopped["session"]["status"] == "stopped_outcome_unknown"
    assert client.force_closed and client.submits == 1
    with pytest.raises(BoundaryError, match="session_not_active"):
        service.submit(session_id, "action-0", "page-0", "episode-1")
    assert service.report(stopped["session"]["report_artifact_id"])["status"] == (
        "stopped_outcome_unknown"
    )


def test_restart_exposes_pending_as_unknown_until_explicit_stop(tmp_path: Path) -> None:
    config, host, _candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit)
    first = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    first.start(SCENARIO["id"])
    active = wait_status(first, "active")
    session_id = active["session"]["session_id"]
    original = (config.state_dir / JOURNAL_FILE).read_bytes()
    recovered = LocalEnvironmentService(config, audit=checked)
    assert recovered.status()["session"]["status"] == "interrupted_unknown"
    assert (config.state_dir / JOURNAL_FILE).read_bytes() == original
    with pytest.raises(BoundaryError, match="session_in_progress_or_unknown"):
        recovered.start(SCENARIO["id"])
    stopped = recovered.stop(session_id)
    assert stopped["session"]["status"] == "cleanup_unknown"
    assert "report_artifact_id" not in stopped["session"]
    first.close()


def test_unknown_receipt_is_retained_in_final_report_only_after_stop(tmp_path: Path) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit, unknown=True)
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    service.start(SCENARIO["id"])
    session_id = wait_status(service, "active")["session"]["session_id"]
    service.submit(session_id, "action-0", "page-0", "episode-1")
    unknown = wait_status(service, "unknown")
    assert unknown["session"]["events"][0]["result_status"] == "unknown"
    assert service.reports()["items"] == []
    with pytest.raises(BoundaryError, match="session_in_progress_or_unknown"):
        service.start(SCENARIO["id"])
    final = service.stop(session_id)["session"]
    assert final["status"] == "stopped_outcome_unknown"
    event_id = service.report(final["report_artifact_id"])["events"][0]["event_artifact_id"]
    assert service.event(event_id)["result"]["native_delivery"] == "unknown"
    assert client.submits == 1


def test_startup_handoff_cannot_reclassify_a_later_unknown_submission(
    tmp_path: Path,
) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit, unknown=True)
    service = LocalEnvironmentService(
        config, audit=checked, client_factory=lambda _command, _host, _pin: client,
    )
    handed_off, resume_startup = pause_startup_after_active_save(service)
    try:
        service.start(SCENARIO["id"])
        assert handed_off.wait(timeout=4)
        startup_worker = service.worker
        assert startup_worker is not None
        session_id = wait_status(service, "active")["session"]["session_id"]
        service.submit(session_id, "action-0", "page-0", "episode-1")
        assert wait_status(service, "unknown")["session"]["events"][0][
            "result_status"] == "unknown"
        assert service.reports()["items"] == []
        resume_startup.set()
        startup_worker.join(timeout=4)
        assert not startup_worker.is_alive()
        assert service.status()["session"]["status"] == "unknown"
        assert service.reports()["items"] == []
        final = service.stop(session_id)["session"]
        assert final["status"] == "stopped_outcome_unknown"
        assert len(service.reports()["items"]) == 1
    finally:
        resume_startup.set()
        service.close()


def test_stop_after_active_save_waits_for_startup_handoff_without_double_close(
    tmp_path: Path,
) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit)
    service = LocalEnvironmentService(
        config, audit=checked, client_factory=lambda _command, _host, _pin: client,
    )
    handed_off, resume_startup = pause_startup_after_active_save(service)
    try:
        service.start(SCENARIO["id"])
        assert handed_off.wait(timeout=4)
        startup_worker = service.worker
        assert startup_worker is not None
        session_id = wait_status(service, "active")["session"]["session_id"]
        stopping = service.stop(session_id)["session"]
        assert stopping["status"] == "stopping"
        assert service.reports()["items"] == []
        resume_startup.set()
        startup_worker.join(timeout=4)
        assert not startup_worker.is_alive()
        final = service.status()["session"]
        assert final["status"] == "stopped"
        assert client.closed and not client.force_closed
        assert len(service.reports()["items"]) == 1
    finally:
        resume_startup.set()
        service.close()


def test_startup_handoff_rejects_a_changed_client_binding(tmp_path: Path) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    original = PublicClientFixture(audit)
    replacement = PublicClientFixture(audit)
    service = LocalEnvironmentService(
        config, audit=checked, client_factory=lambda _command, _host, _pin: original,
    )
    handed_off, resume_startup = pause_startup_after_active_save(service)
    try:
        service.start(SCENARIO["id"])
        assert handed_off.wait(timeout=4)
        startup_worker = service.worker
        assert startup_worker is not None
        session_id = wait_status(service, "active")["session"]["session_id"]
        with service.lock:
            service.client = replacement
        resume_startup.set()
        startup_worker.join(timeout=4)
        assert not startup_worker.is_alive()
        assert original.closed and not replacement.closed
        assert service.status()["session"]["status"] == "cleanup_unknown"
        assert service.reports()["items"] == []
        final = service.stop(session_id)["session"]
        assert final["status"] == "stopped_outcome_unknown"
        assert replacement.closed
    finally:
        resume_startup.set()
        service.close()


def test_changed_client_binding_keeps_failed_original_close_unresolved(
    tmp_path: Path,
) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    original = PublicClientFixture(audit)
    original.fail_close = True
    replacement = PublicClientFixture(audit)
    service = LocalEnvironmentService(
        config, audit=checked, client_factory=lambda _command, _host, _pin: original,
    )
    handed_off, resume_startup = pause_startup_after_active_save(service)
    try:
        service.start(SCENARIO["id"])
        assert handed_off.wait(timeout=4)
        startup_worker = service.worker
        assert startup_worker is not None
        session_id = wait_status(service, "active")["session"]["session_id"]
        with service.lock:
            service.client = replacement
        resume_startup.set()
        startup_worker.join(timeout=4)
        assert not startup_worker.is_alive()
        assert service.status()["session"]["status"] == "cleanup_unknown"
        blocked = service.stop(session_id)["session"]
        assert blocked["status"] == "cleanup_unknown"
        assert replacement.closed and not original.closed
        assert service.reports()["items"] == []
        with pytest.raises(BoundaryError, match="session_in_progress_or_unknown"):
            service.start(SCENARIO["id"])
        original.fail_close = False
        final = service.stop(session_id)["session"]
        assert original.closed and final["status"] == "stopped_outcome_unknown"
        assert len(service.reports()["items"]) == 1
    finally:
        resume_startup.set()
        service.close()


def test_known_delivery_with_mismatched_observation_retains_receipt(tmp_path: Path) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)

    class MismatchedObservationClient(PublicClientFixture):
        def observe_text_menu(self) -> dict[str, Any]:
            value = super().observe_text_menu()
            if self.observations == 2:
                value["snapshot"] = snapshot(2)
            return value

    client = MismatchedObservationClient(audit)
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    service.start(SCENARIO["id"])
    session_id = wait_status(service, "active")["session"]["session_id"]
    service.submit(session_id, "action-0", "page-0", "episode-1")
    uncertain = wait_status(service, "unknown")["session"]
    assert uncertain["events"][0]["result_status"] == "applied"
    assert uncertain["error_code"] == "successor_observation_mismatch"
    final = service.stop(session_id)["session"]
    assert final["status"] == "stopped_outcome_unknown"
    event_id = final["events"][0]["event_artifact_id"]
    assert service.event(event_id)["result"]["native_delivery"] == "delivered"


def test_start_active_journal_failure_closes_child_before_failure_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit)
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    save = service._save
    failed_once = False

    def fail_active_write() -> None:
        nonlocal failed_once
        if service.record["status"] == "active" and not failed_once:
            failed_once = True
            raise OSError("active journal write failed")
        save()

    monkeypatch.setattr(service, "_save", fail_active_write)
    service.start(SCENARIO["id"])
    failed = wait_status(service, "failed")["session"]
    assert failed_once and client.closed and client.force_closed
    assert failed["error_code"] == "session_persistence_failed"
    assert failed["report_artifact_id"]
    assert json.loads((config.state_dir / JOURNAL_FILE).read_text())["status"] == "failed"


def test_initial_start_intent_write_failure_restores_idle_without_spawning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    constructed = 0

    def construct(_command: list[str], _host: Path, _pin: dict[str, Any]) -> PublicClientFixture:
        nonlocal constructed
        constructed += 1
        return PublicClientFixture(audit)

    service = LocalEnvironmentService(config, audit=checked, client_factory=construct)
    save = service._save
    failed_once = False

    def fail_intent_write() -> None:
        nonlocal failed_once
        if service.record["status"] == "starting" and not failed_once:
            failed_once = True
            raise OSError("intent write failed")
        save()

    monkeypatch.setattr(service, "_save", fail_intent_write)
    with pytest.raises(BoundaryError, match="session_persistence_failed"):
        service.start(SCENARIO["id"])
    assert failed_once and constructed == 0 and service.client is None
    assert service.status()["session"]["status"] == "idle"
    assert LocalEnvironmentService(config).status()["session"]["status"] == "idle"
    service.start(SCENARIO["id"])
    assert wait_status(service, "active")["session"]["status"] == "active"
    service.close()


def test_submit_preoffer_journal_failure_closes_without_native_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit)
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    service.start(SCENARIO["id"])
    session_id = wait_status(service, "active")["session"]["session_id"]
    save = service._save
    failed_once = False

    def fail_preoffer_write() -> None:
        nonlocal failed_once
        if service.record["status"] == "submitting" and not failed_once:
            failed_once = True
            raise OSError("pending journal write failed")
        save()

    monkeypatch.setattr(service, "_save", fail_preoffer_write)
    with pytest.raises(BoundaryError, match="session_persistence_failed_before_offer"):
        service.submit(session_id, "action-0", "page-0", "episode-1")
    assert failed_once and client.closed and client.submits == 0
    assert service.status()["session"]["status"] == "unknown"
    assert json.loads((config.state_dir / JOURNAL_FILE).read_text())["status"] == "unknown"
    assert service.stop(session_id)["session"]["status"] == "stopped_outcome_unknown"


def test_submit_postnative_journal_failure_preserves_event_and_closes_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit)
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    service.start(SCENARIO["id"])
    session_id = wait_status(service, "active")["session"]["session_id"]
    save = service._save
    failed_once = False

    def fail_result_write() -> None:
        nonlocal failed_once
        if service.record["status"] == "active" and service.record["events"] and not failed_once:
            failed_once = True
            raise OSError("result journal write failed")
        save()

    monkeypatch.setattr(service, "_save", fail_result_write)
    service.submit(session_id, "action-0", "page-0", "episode-1")
    unknown = wait_status(service, "unknown")["session"]
    assert failed_once and client.submits == 1 and client.closed
    assert unknown["error_code"] == "session_persistence_failed"
    event_id = unknown["events"][0]["event_artifact_id"]
    assert service.event(event_id)["result"]["native_delivery"] == "delivered"
    assert json.loads((config.state_dir / JOURNAL_FILE).read_text())["status"] == "unknown"


def test_stop_journal_failure_still_closes_child_and_publishes_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit)
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    service.start(SCENARIO["id"])
    session_id = wait_status(service, "active")["session"]["session_id"]
    save = service._save
    failed_once = False

    def fail_stop_write() -> None:
        nonlocal failed_once
        if service.record["status"] == "stopping" and not failed_once:
            failed_once = True
            raise OSError("stop journal write failed")
        save()

    monkeypatch.setattr(service, "_save", fail_stop_write)
    final = service.stop(session_id)["session"]
    assert failed_once and client.closed
    assert final["status"] == "stopped" and final["report_artifact_id"]
    assert json.loads((config.state_dir / JOURNAL_FILE).read_text())["status"] == "stopped"


def test_confirmed_close_with_event_writer_pending_stays_stopping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit)
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    service.start(SCENARIO["id"])
    session_id = wait_status(service, "active")["session"]["session_id"]
    entered = threading.Event()
    release = threading.Event()
    publish = service._publish_event

    def held_publish(value: dict[str, Any], sid: str, request_id: str) -> str:
        entered.set()
        assert release.wait(timeout=5)
        return publish(value, sid, request_id)

    monkeypatch.setattr(service, "_publish_event", held_publish)
    service.submit(session_id, "action-0", "page-0", "episode-1")
    assert entered.wait(timeout=2)
    intermediate = service.stop(session_id)["session"]
    assert client.closed and intermediate["status"] == "stopping"
    assert intermediate["error_code"] == "event_finalization_pending"
    release.set()
    final = wait_status(service, "stopped_outcome_unknown")["session"]
    assert final["error_code"] == "action_or_observation_unknown"
    assert final["report_artifact_id"]


def test_confirmed_stop_final_write_failure_retains_report_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit)
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    service.start(SCENARIO["id"])
    session_id = wait_status(service, "active")["session"]["session_id"]
    save = service._save
    failed_once = False

    def fail_terminal_write() -> None:
        nonlocal failed_once
        if service.record["status"] == "stopped" and not failed_once:
            failed_once = True
            raise OSError("terminal journal write failed")
        save()

    monkeypatch.setattr(service, "_save", fail_terminal_write)
    pending = service.stop(session_id)["session"]
    assert client.closed and failed_once
    assert pending["status"] == "stopped" and not pending.get("report_artifact_id")
    assert json.loads((config.state_dir / JOURNAL_FILE).read_text())["status"] == "stopping"
    final = service.stop(session_id)["session"]
    assert final["report_artifact_id"]
    assert json.loads((config.state_dir / JOURNAL_FILE).read_text())["status"] == "stopped"


def test_failed_start_waits_for_cleanup_before_report_or_next_start(tmp_path: Path) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit)
    client.ready["candidate_build"] = {**audit, "artifact_sha256": "0" * 64}
    client.fail_close = True
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    service.start(SCENARIO["id"])
    blocked = wait_status(service, "cleanup_unknown")
    assert not blocked["session"].get("report_artifact_id")
    with pytest.raises(BoundaryError, match="session_in_progress_or_unknown"):
        service.start(SCENARIO["id"])
    client.fail_close = False
    final = service.stop(blocked["session"]["session_id"])["session"]
    assert final["status"] == "stopped_outcome_unknown"
    assert final["report_artifact_id"]


@pytest.mark.parametrize("field", ["protocol", "exact_game"])
def test_ready_must_identify_fixed_driver_and_game(tmp_path: Path, field: str) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit)
    client.ready[field] = "wrong"
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    service.start(SCENARIO["id"])
    failed = wait_status(service, "failed")["session"]
    assert failed["error_code"] == "managed_candidate_changed"
    assert client.closed and client.force_closed
    assert failed["report_artifact_id"]


def test_constructor_failure_cannot_claim_child_cleanup(tmp_path: Path) -> None:
    config, _host, _candidate, _pin, _audit, checked = fixture(tmp_path)

    def fail_after_possible_spawn(_command: list[str], _host: Path, _pin: dict[str, Any]) -> None:
        raise RuntimeError("ready failed after spawn")

    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=fail_after_possible_spawn,
    )
    service.start(SCENARIO["id"])
    unknown = wait_status(service, "cleanup_unknown")["session"]
    assert unknown["error_code"] == "host_cleanup_unknown"
    assert not unknown.get("report_artifact_id")
    with pytest.raises(BoundaryError, match="session_in_progress_or_unknown"):
        service.start(SCENARIO["id"])


def test_constructor_explicitly_confirmed_cleanup_allows_failed_report(tmp_path: Path) -> None:
    config, _host, _candidate, _pin, _audit, checked = fixture(tmp_path)

    class ConfirmedInitializationError(RuntimeError):
        cleanup_confirmed = True

    def fail_after_confirmed_cleanup(
        _command: list[str], _host: Path, _pin: dict[str, Any]
    ) -> None:
        raise ConfirmedInitializationError("startup rejected and child closed")

    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=fail_after_confirmed_cleanup,
    )
    service.start(SCENARIO["id"])
    failed = wait_status(service, "failed")["session"]
    assert failed["error_code"] == "managed_start_failed"
    assert failed["report_artifact_id"]


def test_report_publish_failure_retries_without_replaying_game(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit)
    service = LocalEnvironmentService(
        config,
        audit=checked,
        client_factory=lambda _command, _host, _pin: client,
    )
    service.start(SCENARIO["id"])
    session_id = wait_status(service, "active")["session"]["session_id"]
    publish = service._publish

    def fail_publish() -> None:
        raise OSError("full")

    monkeypatch.setattr(service, "_publish", fail_publish)
    with pytest.raises(OSError, match="full"):
        service.stop(session_id)
    assert service.status()["session"]["status"] == "stopped"
    assert not service.status()["session"].get("report_artifact_id")
    assert client.closed and client.submits == 0
    monkeypatch.setattr(service, "_publish", publish)
    final = service.stop(session_id)["session"]
    assert final["report_artifact_id"]
    assert service.report(final["report_artifact_id"])["status"] == "stopped"


def test_stop_during_client_start_never_releases_a_live_constructor(tmp_path: Path) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    client = PublicClientFixture(audit)

    def constructing(_command: list[str], _host: Path, _pin: dict[str, Any]):
        entered.set()
        release.wait(timeout=5)
        return client

    service = LocalEnvironmentService(config, audit=checked, client_factory=constructing)
    session_id = service.start(SCENARIO["id"])["session"]["session_id"]
    assert entered.wait(timeout=2)
    unknown = service.stop(session_id)["session"]
    assert unknown["status"] == "cleanup_unknown"
    assert not unknown.get("report_artifact_id")
    with pytest.raises(BoundaryError, match="session_in_progress_or_unknown"):
        service.start(SCENARIO["id"])
    release.set()
    finished = wait_status(service, "stopped_outcome_unknown")["session"]
    assert client.closed and client.force_closed
    assert finished["report_artifact_id"]


def test_stop_during_constructor_retries_original_failed_close(tmp_path: Path) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    client = PublicClientFixture(audit)
    client.fail_close = True

    def constructing(_command: list[str], _host: Path, _pin: dict[str, Any]):
        entered.set()
        release.wait(timeout=5)
        return client

    service = LocalEnvironmentService(config, audit=checked, client_factory=constructing)
    try:
        session_id = service.start(SCENARIO["id"])["session"]["session_id"]
        assert entered.wait(timeout=2)
        assert service.stop(session_id)["session"]["status"] == "cleanup_unknown"
        release.set()
        worker = service.worker
        assert worker is not None
        worker.join(timeout=4)
        assert not worker.is_alive()
        assert service.status()["session"]["status"] == "cleanup_unknown"
        assert service.reports()["items"] == []
        client.fail_close = False
        final = service.stop(session_id)["session"]
        assert client.closed and client.force_closed
        assert final["status"] == "stopped_outcome_unknown"
        assert len(service.reports()["items"]) == 1
    finally:
        release.set()
        service.close()


def test_profile_collision_symlink_and_exact_package_drift_fail_closed(tmp_path: Path) -> None:
    config, host, candidate, pin, _audit, checked = fixture(tmp_path)
    different = tmp_path / "other-candidate"
    different.mkdir()
    with pytest.raises(BoundaryError, match="profile_collision"):
        configure_managed_host(
            config,
            different,
            host_root=host,
            host_pin=pin,
            input_profile="text-menu-v1",
            audit=lambda _host, _candidate: checked(host, candidate),
        )
    profile = config.state_dir / PROFILE_FILE
    profile.unlink()
    profile.symlink_to(candidate)
    with pytest.raises(BoundaryError, match="profile_path_unsafe"):
        configure_managed_host(
            config,
            candidate,
            host_root=host,
            host_pin=pin,
            input_profile="text-menu-v1",
            audit=checked,
        )
    profile.unlink()
    configure_managed_host(
        config, candidate, host_root=host, host_pin=pin, input_profile="text-menu-v1", audit=checked
    )
    (host / "tools/managed-pe-driver.mjs").write_text("// drift\n")
    service = LocalEnvironmentService(config, audit=checked)
    service.start(SCENARIO["id"])
    failed = wait_status(service, "failed")
    assert failed["session"]["error_code"] == "host_package_unavailable"
    assert failed["session"]["report_artifact_id"]


def test_private_setup_rejects_released_rc7_and_wrong_scenario_audit(tmp_path: Path) -> None:
    config, host, candidate, pin, audit, checked = fixture(tmp_path)
    (config.state_dir / PROFILE_FILE).unlink()
    old = {**pin, "version": "1.1.0-rc.7"}
    with pytest.raises(BoundaryError, match="host_cleanup_capability_required"):
        configure_managed_host(
            config,
            candidate,
            host_root=host,
            host_pin=old,
            input_profile="text-menu-v1",
            audit=checked,
        )
    old_rc19 = {**pin, "version": "1.1.0-rc.19"}
    with pytest.raises(BoundaryError, match="host_cleanup_capability_required"):
        configure_managed_host(
            config,
            candidate,
            host_root=host,
            host_pin=old_rc19,
            input_profile="text-menu-v2",
            audit=checked,
        )
    wrong = {**audit, "source_patch_sha256": "0" * 64}
    with pytest.raises(BoundaryError, match="managed_candidate_identity_mismatch"):
        configure_managed_host(
            config,
            candidate,
            host_root=host,
            host_pin=pin,
            input_profile="text-menu-v1",
            audit=lambda _host, _candidate: wrong,
        )
    assert not (config.state_dir / PROFILE_FILE).exists()


def test_private_profile_setup_requires_stopped_workbench(tmp_path: Path) -> None:
    config, host, candidate, pin, _audit, checked = fixture(tmp_path)
    with (
        instance_lock(config.state_dir / "instance.lock"),
        pytest.raises(BoundaryError, match="already_running"),
    ):
        configure_managed_host(
            config,
            candidate,
            host_root=host,
            host_pin=pin,
            input_profile="text-menu-v1",
            audit=checked,
        )


def test_real_public_python_consumer_from_private_pinned_fixture_package(
    tmp_path: Path,
) -> None:
    """Exercise the installed public JSONL client without a game or private candidate."""
    config, host, candidate, pin, audit, checked = fixture(tmp_path)
    source = Path(__file__).resolve().parents[2] / (
        "components/host-runtime/consumers/python/sts2_headless"
    )
    target = host / "consumers/python/sts2_headless"
    shutil.rmtree(target)
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    first, second = snapshot(0), snapshot(1)
    driver = (
        """
import readline from 'node:readline';
const build = __AUDIT__;
const pages = [__FIRST__, __SECOND__];
let observed = 0;
function emit(value) {{ process.stdout.write(JSON.stringify(value) + '\\n'); }}
emit({{type:'ready',protocol:'sts2.headless/managed-player-environment-driver-1',
  candidate_build:build,exact_game:{{sts2_dll_sha256:__GAME_SHA__}}}});
const input = readline.createInterface({{input:process.stdin}});
input.on('line', line => {{
  const request = JSON.parse(line);
  const base = {{request_id:request.request_id}};
  if (request.command === 'reset') emit({{...base,type:'reset_result',snapshot:pages[0]}});
  else if (request.command === 'episode_identity') emit({{
    ...base,type:'episode_identity_result',identity:{{
    candidate_build:build,environment_fingerprint:'e'.repeat(64),
    episode_provenance:{{verdict:'provenance_pass',requested_seed:'M2H0ST20260929A',
      actual_seed:'M2H0ST20260929A',runtime_instance_id:'synthetic'}}
  }}}});
  else if (request.command === 'text_observe') emit({{...base,type:'text_observe_result',context:{{
    schema:'sts2.player-environment/text-menu-observation-context-1',
    snapshot:pages[Math.min(observed++,1)],game_continuity_id:'episode-1'
  }}}});
  else if (request.command === 'text_submit') emit({{...base,type:'text_submit_result',result:{{
    protocol_version:'1.0.0',schema:'sts2.player-environment/text-menu-action-result-1',
    input_profile:'text-menu-v1',request_id:request.mutation_request_id,status:'applied',
    effect_domain:'native_input',native_delivery:'delivered',
    action:{{action_id:request.action_id,kind:'native_input',effect_domain:'native_input'}},
    reason_code:'accepted',detail:'',retry:'never',successor:pages[1],attribution:null
  }}}});
  else if (request.command === 'close') {{ emit({{...base,type:'close_result'}}); input.close(); }}
}});
""".replace("{{", "{")
        .replace("}}", "}")
        .replace("__AUDIT__", json.dumps(audit))
        .replace("__FIRST__", json.dumps(first))
        .replace("__SECOND__", json.dumps(second))
    )
    driver = driver.replace("__GAME_SHA__", json.dumps(SCENARIO["exact_game_assembly_sha256"]))
    (host / "tools/managed-pe-driver.mjs").write_text(driver)
    (host / "package.json").write_text(
        json.dumps(
            {
                "name": HOST_PACKAGE,
                "version": "1.1.0-rc.20",
                "type": "module",
            }
        )
    )
    pin["package_content_sha256"] = directory_sha256(host)
    (config.state_dir / PROFILE_FILE).unlink()
    configure_managed_host(
        config, candidate, host_root=host, host_pin=pin, input_profile="text-menu-v1", audit=checked
    )
    script = tmp_path / "run_public_consumer.py"
    script.write_text("""
import json
import sys
import time
from pathlib import Path
from spireagent.workbench.developer import ProjectConfig, combination
from spireagent.workbench.local_environment import LocalEnvironmentService, SCENARIO

state, audit_path = map(Path, sys.argv[1:])
audit = json.loads(audit_path.read_text())
config = ProjectConfig(state, '', '', None, combination())
service = LocalEnvironmentService(config, audit=lambda *_: audit)
service.start(SCENARIO['id'])
def ready(expected):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = service.status()['session']
        if value['status'] == expected:
            return value
        if value['status'] in {'failed', 'cleanup_unknown'}:
            raise RuntimeError(value['error_code'])
        time.sleep(0.01)
    raise RuntimeError('timeout')
active = ready('active')
sid = active['session_id']
service.submit(sid, 'action-0', 'page-0', 'episode-1')
next_page = ready('active')
assert next_page['context']['snapshot']['snapshot_id'] == 'page-1'
final = service.stop(sid)['session']
assert final['status'] == 'stopped'
event_id = service.report(final['report_artifact_id'])['events'][0]['event_artifact_id']
assert service.event(event_id)['result']['status'] == 'applied'
print(json.dumps({'status':final['status'],'submissions':len(final['events'])}))
""")
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps(audit))
    before = directory_sha256(host)
    completed = subprocess.run(
        [sys.executable, str(script), str(config.state_dir), str(audit_path)],
        text=True,
        capture_output=True,
        timeout=25,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"status": "stopped", "submissions": 1}
    assert directory_sha256(host) == before
