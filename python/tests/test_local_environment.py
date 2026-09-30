from __future__ import annotations

import hashlib
import json
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
    _ManagedServiceEnvironment,
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


def normalize_seed_fixture(_host: Path, _pin: dict[str, Any], seed: object) -> str:
    if not isinstance(seed, str):
        raise ValueError("seed must be a string")
    canonical = seed.strip().upper().replace("O", "0").replace("I", "1")
    if not canonical or len(canonical) > 64 or not canonical.isascii() or not canonical.isalnum():
        raise BoundaryError("local_environment", "scene_seed_invalid")
    return canonical


def snapshot(number: int, *, character: str = "DEFECT", ascension: int = 0) -> dict[str, Any]:
    return {
        "schema": "sts2.player-environment/text-menu-snapshot-1",
        "input_profile": "text-menu-v1",
        "snapshot_id": f"page-{number}",
        "status": "interactive",
        "interaction": {"kind": "map_navigation", "content": {"surface": {"kind": "map"}}},
        "persistent": {"content": {
            "run": {"floor": number, "ascension": ascension},
            "player": {"character_definition_id": character},
        }},
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
        self, audit: dict[str, str], *, blocked: bool = False, unknown: bool = False,
        seed: str = SCENARIO["seed"], actual_seed: str | None = None,
        character: str = "DEFECT", ascension: int = 0,
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
        self.reset_seeds: list[str] = []
        self.seed = seed
        self.actual_seed = actual_seed
        self.character = character
        self.ascension = ascension

    def reset(self, seed: str) -> dict[str, Any]:
        self.seed = seed
        self.reset_seeds.append(seed)
        return snapshot(0, character=self.character, ascension=self.ascension)

    def episode_identity(self) -> dict[str, Any]:
        return {
            "candidate_build": self.ready["candidate_build"],
            "environment_fingerprint": "e" * 64,
            "episode_provenance": {
                "verdict": "provenance_pass",
                "requested_seed": self.seed,
                "actual_seed": self.actual_seed or self.seed,
                "runtime_instance_id": "instance-1",
            },
        }

    def observe_text_menu(self) -> dict[str, Any]:
        page = snapshot(self.observations, character=self.character, ascension=self.ascension)
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

    service = LocalEnvironmentService(config, audit=checked, client_factory=make_client,
                                      seed_normalizer=normalize_seed_fixture)
    scene = service.save_scene("A0 repeat", "abcoi123")
    assert scene["seed"] == "ABC01123"
    with pytest.raises(BoundaryError, match="scene_seed_invalid"):
        service.save_scene("Invalid seed", "BAD SEED!")
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
        config, audit=checked, seed_normalizer=normalize_seed_fixture,
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


def test_distinct_scene_seeds_reach_host_and_immutable_reports(tmp_path: Path) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    clients: list[PublicClientFixture] = []

    def make_client(*_args: Any) -> PublicClientFixture:
        client = PublicClientFixture(audit)
        clients.append(client)
        return client

    service = LocalEnvironmentService(
        config, audit=checked, client_factory=make_client,
        seed_normalizer=normalize_seed_fixture,
    )
    first = service.save_scene("First", "SEED0001")
    second = service.save_scene("Second", "SEED0002")
    assert first["artifact_id"] != second["artifact_id"]
    reports = []
    for scene in (first, second):
        session = service.start(SCENARIO["id"], scene_artifact_id=scene["artifact_id"])["session"]
        active = wait_status(service, "active")["session"]
        assert active["seed"] == scene["seed"]
        service.stop(session["session_id"])
        reports.append(wait_status(service, "stopped")["session"]["report_artifact_id"])
    assert [client.reset_seeds for client in clients] == [["SEED0001"], ["SEED0002"]]
    values = [service.report(report_id) for report_id in reports]
    assert [value["seed"] for value in values] == ["SEED0001", "SEED0002"]
    assert [value["episode_identity"]["episode_provenance"]["actual_seed"]
            for value in values] == ["SEED0001", "SEED0002"]
    assert all(value["initial_context"]["snapshot"]["persistent"]["content"]["run"][
        "ascension"] == 0 for value in values)
    assert all(value["initial_context"]["snapshot"]["persistent"]["content"]["player"][
        "character_definition_id"] == "DEFECT" for value in values)
    from spireagent.workbench.local_managed_source import _expectation

    assert [_expectation(value).seed for value in values] == ["SEED0001", "SEED0002"]


@pytest.mark.parametrize(
    ("actual_seed", "character", "ascension", "expected_error"),
    [("WRONGSEED", "DEFECT", 0, "episode_identity_invalid"),
     (None, "IRONCLAD", 0, "initial_character_or_ascension_invalid"),
     (None, "DEFECT", 1, "initial_character_or_ascension_invalid"),
     (None, "DEFECT", False, "initial_character_or_ascension_invalid")],
)
def test_scene_start_fails_closed_on_host_seed_or_public_character_mismatch(
    tmp_path: Path, actual_seed: str | None, character: str, ascension: int,
    expected_error: str,
) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    client = PublicClientFixture(audit, actual_seed=actual_seed,
                                 character=character, ascension=ascension)
    service = LocalEnvironmentService(
        config, audit=checked, client_factory=lambda *_args: client,
        seed_normalizer=normalize_seed_fixture,
    )
    scene = service.save_scene("Bounded seed", "SEEDMISMATCH01")
    service.start(SCENARIO["id"], scene_artifact_id=scene["artifact_id"])
    failed = wait_status(service, "failed")["session"]
    assert failed["error_code"] == expected_error
    assert failed["seed"] == scene["seed"]
    assert client.closed


def test_comparison_rejects_two_closed_reports_with_same_runtime_instance(tmp_path: Path) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    service = LocalEnvironmentService(
        config, audit=checked, client_factory=lambda *_: PublicClientFixture(audit),
        seed_normalizer=normalize_seed_fixture,
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


def test_comparison_rejects_report_seed_mismatch_before_verified_claim(tmp_path: Path) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    service = LocalEnvironmentService(
        config, audit=checked, client_factory=lambda *_: PublicClientFixture(audit),
        seed_normalizer=normalize_seed_fixture,
    )
    scene = service.save_scene("Exact seed", "SEED0001")
    report_ids = []
    for _ in range(2):
        session = service.start(SCENARIO["id"], scene_artifact_id=scene["artifact_id"])[
            "session"
        ]
        wait_status(service, "active")
        service.stop(session["session_id"])
        report_ids.append(wait_status(service, "stopped")["session"]["report_artifact_id"])
    get_report = service.report
    service.report = lambda identity: {
        **get_report(identity),
        **({"seed": "OTHERSEED"} if identity == report_ids[0] else {}),
    }
    with pytest.raises(BoundaryError, match="repeatability_proof_unavailable"):
        service.compare(scene["artifact_id"], report_ids)


def test_resumed_segment_cannot_prove_a_fixed_seed_start(tmp_path: Path) -> None:
    config, _host, _candidate, _pin, audit, checked = fixture(tmp_path)
    clients: list[PublicClientFixture] = []

    def make_client(*_args: Any) -> PublicClientFixture:
        client = PublicClientFixture(audit)
        client.number = len(clients) + 1
        clients.append(client)
        return client

    service = LocalEnvironmentService(
        config, audit=checked, client_factory=make_client,
        seed_normalizer=normalize_seed_fixture,
    )
    scene = service.save_scene("Resumed segment", "SEEDRESUMED1")
    reports = []
    for _ in range(2):
        session = service.start(SCENARIO["id"], scene_artifact_id=scene["artifact_id"])[
            "session"
        ]
        wait_status(service, "active")
        service.stop(session["session_id"])
        reports.append(wait_status(service, "stopped")["session"]["report_artifact_id"])

    read_report = service.report
    service.report = lambda report_id: {
        **read_report(report_id),
        **({"continued_from_session_id": "prior-segment"} if report_id == reports[0] else {}),
    }
    with pytest.raises(BoundaryError, match="repeatability_proof_unavailable"):
        service.compare(scene["artifact_id"], reports)


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


def test_old_host_package_without_managed_service_fails_before_native_spawn(
    tmp_path: Path,
) -> None:
    config, host, _candidate, _pin, _audit, checked = fixture(tmp_path)
    service = LocalEnvironmentService(config, audit=checked)
    service.start(SCENARIO["id"])
    failed = wait_status(service, "failed")
    assert failed["session"]["error_code"] == "host_managed_service_unavailable"
    assert failed["session"]["report_artifact_id"]
    assert not (config.state_dir / "managed-host-service" / "client.json").exists()
    assert not (config.state_dir / "managed-host-service" / "manager.json").exists()
    assert not list((host / "tools").glob("launch-*.log"))


class ManagedServiceFixture:
    def __init__(self, *, held: bool = False, fail_release: bool = False) -> None:
        self.service_instance_id = "managed_service_fixture"
        self.calls: list[dict[str, Any]] = []
        self.held = held
        self.fail_release = fail_release
        self.audit = {
            "upstream_revision": "d" * 40,
            "source_patch_sha256": SCENARIO["source_patch_sha256"],
            "artifact_sha256": SCENARIO["artifact_sha256"],
            "artifact_mvid": SCENARIO["artifact_mvid"],
            "original_sts2_sha256": SCENARIO["exact_game_assembly_sha256"],
            "runtime_sts2_sha256": SCENARIO["exact_game_assembly_sha256"],
        }

    def ready(self) -> dict[str, Any]:
        return {"service_instance_id": self.service_instance_id,
                "adapter_runtime_instance_id": "runtime-fixture",
                "episode": {"game_continuity_id": "episode-fixture",
                            "control_held": self.held, "tainted": False, "closed": False},
                "text_menu_contracts": [
                    {"input_profile": "text-menu-v2",
                     "text_state_owner_contract": "sts2.host-runtime/text-menu-v2-owner-1"}
                ]}

    def request(self, command: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(command)
        operation = command["command"]
        if operation == "claim_control":
            if self.held:
                error = RuntimeError("managed_control_held")
                error.code = "managed_control_held"
                raise error
            self.held = True
            result = {"type": "claim_control_result", "control_token": "private-token",
                      "control_epoch": "epoch-fixture", "runtime_instance_id": "runtime-fixture",
                      "game_continuity_id": "episode-fixture"}
            if "text_state_owner" in command:
                result["text_state_owner"] = command["text_state_owner"]
        elif operation == "text_observe":
            result = {"type": "text_observe_result",
                      "context": {"schema": (
                          "sts2.player-environment/text-menu-observation-context-1"
                      ),
                                  "snapshot": snapshot(0),
                                  "game_continuity_id": "episode-fixture"}}
        elif operation == "text_submit":
            result = {"type": "text_submit_result", "result": {
                "status": "applied", "successor": snapshot(1)}}
        elif operation == "episode_identity":
            result = {"type": "episode_identity_result", "identity": {
                "candidate_build": self.audit, "environment_fingerprint": "f" * 64,
                "episode_provenance": {"verdict": "provenance_pass",
                    "requested_seed": SCENARIO["seed"], "actual_seed": SCENARIO["seed"],
                    "runtime_instance_id": "runtime-fixture"}}}
        elif operation == "release_control":
            if self.fail_release:
                raise RuntimeError("release response unknown")
            self.held = False
            result = {"type": "release_control_result", "status": "released",
                      "control_epoch": "epoch-fixture", "runtime_instance_id": "runtime-fixture",
                      "game_continuity_id": "episode-fixture"}
        else:
            raise AssertionError(operation)
        return {"service_instance_id": self.service_instance_id, "result": result}


def test_host_service_manual_observe_and_submit_use_acknowledged_control_without_extra_observe(
) -> None:
    transport = ManagedServiceFixture()
    environment = _ManagedServiceEnvironment(
        transport, object(), transport.ready(), "text-menu-v1"
    )
    context = environment.observe_text_menu()
    assert context["snapshot"]["snapshot_id"] == "page-0"
    assert [call["command"] for call in transport.calls] == [
        "claim_control", "text_observe", "release_control"
    ]
    assert transport.calls[1]["control_token"] == "private-token"
    assert transport.calls[1]["control_epoch"] == "epoch-fixture"
    transport.calls.clear()
    result = environment.submit_text_menu(
        "action-0", "page-0", "episode-fixture", "request-fixture",
    )
    assert result["status"] == "applied"
    assert [call["command"] for call in transport.calls] == [
        "claim_control", "text_submit", "release_control"
    ]
    assert transport.calls[1]["expected_snapshot_id"] == "page-0"
    assert transport.calls[1]["mutation_request_id"] == "request-fixture"


def test_workbench_v2_binds_claims_to_server_owned_segment_id_and_keeps_short_lease_continuity(
) -> None:
    transport = ManagedServiceFixture()
    session_id = "d" * 32
    environment = _ManagedServiceEnvironment(
        transport, object(), transport.ready(), "text-menu-v2", session_id=session_id
    )
    environment._claim()
    assert transport.calls[0]["text_state_owner"] == f"workbench:{session_id}"
    environment._release()
    environment._claim()
    assert transport.calls[2]["text_state_owner"] == f"workbench:{session_id}"
    environment._release()


def test_workbench_v2_fails_closed_if_host_or_segment_owner_identity_is_missing() -> None:
    transport = ManagedServiceFixture()
    with pytest.raises(BoundaryError, match="managed_text_state_owner_unsupported"):
        _ManagedServiceEnvironment(transport, object(),
            {**transport.ready(), "text_menu_contracts": []}, "text-menu-v2",
            session_id="d" * 32)
    with pytest.raises(BoundaryError, match="managed_text_state_owner_invalid"):
        _ManagedServiceEnvironment(transport, object(), transport.ready(),
            "text-menu-v2", session_id="not-a-session")


def test_known_host_owner_rejection_does_not_taint_workbench_claim_as_unknown() -> None:
    class RejectingTransport(ManagedServiceFixture):
        def request(self, command: dict[str, Any]) -> dict[str, Any]:
            if command.get("command") == "claim_control":
                error = RuntimeError("managed_text_state_owner_invalid")
                error.code = "managed_text_state_owner_invalid"
                raise error
            return super().request(command)

    transport = RejectingTransport()
    environment = _ManagedServiceEnvironment(
        transport, object(), transport.ready(), "text-menu-v2", session_id="e" * 32
    )
    with pytest.raises(BoundaryError, match="managed_text_state_owner_invalid"):
        environment._claim()
    assert environment.control is None


def test_lost_release_reply_can_be_explicitly_recovered_without_resolving_game_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, pin, _audit, _checked = fixture(tmp_path)
    service = LocalEnvironmentService(config)
    service.record = {
        "schema": "stpd/local-managed-environment-v1", "status": "failed",
        "error_code": "managed_control_release_unknown", "session_id": "f" * 32,
        "scenario_id": SCENARIO["id"], "seed": SCENARIO["seed"],
        "host_package_pin": pin, "producer": service._producer(), "events": [],
        "service_binding": {"service_instance_id": "managed_service_fixture",
                             "runtime_instance_id": "runtime-fixture",
                             "game_continuity_id": "episode-fixture"},
        "uncertain_control_epoch": "epoch-fixture",
        "session_semantics": "workbench-segment-v2-host-owned-service",
    }
    transport = ManagedServiceFixture(held=True)
    calls: list[dict[str, Any]] = []

    class ManagerFixture:
        def status(self) -> dict[str, Any]:
            return {"status": {"control_held": transport.held, "control": (
                {"control_epoch": "epoch-fixture", "runtime_instance_id": "runtime-fixture",
                 "game_continuity_id": "episode-fixture"} if transport.held else None
            )}}

        def recover_control(self, **expected: str) -> dict[str, Any]:
            calls.append(expected)
            assert expected == {"expected_control_epoch": "epoch-fixture",
                                "expected_runtime_instance_id": "runtime-fixture",
                                "expected_game_continuity_id": "episode-fixture"}
            transport.held = False
            return {"result": {"type": "release_control_result", "status": "released",
                               "control_epoch": "epoch-fixture",
                               "runtime_instance_id": "runtime-fixture",
                               "game_continuity_id": "episode-fixture"}}

    manager = ManagerFixture()
    monkeypatch.setattr(service, "_managed_service_handles",
                        lambda _profile: (transport, manager, transport.ready()))
    result = service.recover_control()
    assert result["status"] == "released"
    assert result["outcome"] == "still_unknown"
    assert calls and transport.calls == []
    assert service.record["error_code"] == "managed_control_release_unknown"
    assert service.record["control_recovery"] == (
        "released_acknowledged_outcome_still_unknown"
    )


def test_lost_control_reply_is_normalized_for_explicit_recovery() -> None:
    order: list[tuple[str, str]] = []

    class LostClaim(ManagedServiceFixture):
        def request(self, command: dict[str, Any]) -> dict[str, Any]:
            if command["command"] == "claim_control":
                self.calls.append(command)
                order.append(("post", command["request_id"]))
                self.held = True
                raise RuntimeError("claim response lost")
            return super().request(command)

    transport = LostClaim()
    environment = _ManagedServiceEnvironment(transport, object(), transport.ready(),
                                             "text-menu-v1",
                                             lambda request_id, _binding:
                                             order.append(("persist", request_id)))
    with pytest.raises(BoundaryError, match="managed_control_claim_unknown"):
        environment.observe_text_menu()
    assert environment.control is not None
    assert environment.control["uncertain"] == "claim"
    assert environment.control["request_id"]
    assert order == [("persist", environment.control["request_id"]),
                     ("post", environment.control["request_id"])]


def test_restart_retains_claim_correlation_and_explicitly_recovers_same_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, pin, _audit, _checked = fixture(tmp_path)
    journal = {
        "schema": "stpd/local-managed-environment-v1", "status": "submitting",
        "session_id": "e" * 32, "scenario_id": SCENARIO["id"], "seed": SCENARIO["seed"],
        "input_profile": "text-menu-v1", "host_package_pin": pin,
        "producer": {"source_revision": "a" * 40, "uv_lock_sha256": "b" * 64},
        "events": [], "context": None, "pending_request_id": "action-request",
        "pending_control_claim_request_id": "claim-before-crash",
        "service_binding": {"service_instance_id": "managed_service_fixture",
                             "runtime_instance_id": "runtime-fixture",
                             "game_continuity_id": "episode-fixture"},
    }
    (config.state_dir / JOURNAL_FILE).write_text(json.dumps(journal))
    service = LocalEnvironmentService(config)
    assert service.record["status"] == "interrupted_unknown"
    assert service.record["pending_control_claim_request_id"] == "claim-before-crash"
    transport = ManagedServiceFixture(held=True)

    class ManagerFixture:
        recovered: list[dict[str, str]] = []

        def status(self) -> dict[str, Any]:
            control = ({"control_epoch": "epoch-after-crash", "runtime_instance_id":
                        "runtime-fixture", "game_continuity_id": "episode-fixture",
                        "claim_request_id": "claim-before-crash"} if transport.held else None)
            return {"status": {"control_held": transport.held, "control": control}}

        def recover_control(self, **expected: str) -> dict[str, Any]:
            self.recovered.append(expected)
            transport.held = False
            return {"result": {"type": "release_control_result", "status": "released",
                               "control_epoch": "epoch-after-crash",
                               "runtime_instance_id": "runtime-fixture",
                               "game_continuity_id": "episode-fixture"}}

    manager = ManagerFixture()
    monkeypatch.setattr(service, "_managed_service_handles",
                        lambda _profile: (transport, manager, transport.ready()))
    service.resume()
    held = wait_status(service, "control_held")["session"]
    assert held["error_code"] == "managed_control_claim_unknown"
    assert service.record["uncertain_claim_request_id"] == "claim-before-crash"
    assert transport.calls == []
    assert service.recover_control()["outcome"] == "still_unknown"
    assert manager.recovered == [{"expected_control_epoch": "epoch-after-crash",
                                  "expected_runtime_instance_id": "runtime-fixture",
                                  "expected_game_continuity_id": "episode-fixture"}]
    assert service.record["error_code"] == "managed_control_claim_unknown"
    assert service.record["control_recovery"] == (
        "released_acknowledged_outcome_still_unknown"
    )


def test_lost_claim_reply_recovers_only_matching_claim_request_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, pin, _audit, _checked = fixture(tmp_path)
    service = LocalEnvironmentService(config)
    service.record = {
        "schema": "stpd/local-managed-environment-v1", "status": "failed",
        "error_code": "managed_control_claim_unknown", "session_id": "f" * 32,
        "scenario_id": SCENARIO["id"], "seed": SCENARIO["seed"],
        "host_package_pin": pin, "producer": service._producer(), "events": [],
        "service_binding": {"service_instance_id": "managed_service_fixture",
                             "runtime_instance_id": "runtime-fixture",
                             "game_continuity_id": "episode-fixture"},
        "uncertain_claim_request_id": "claim-workbench-old",
    }
    transport = ManagedServiceFixture(held=True)
    recovered: list[dict[str, str]] = []

    class MatchingManager:
        def status(self) -> dict[str, Any]:
            control = ({"control_epoch": "epoch-fixture", "runtime_instance_id":
                        "runtime-fixture", "game_continuity_id": "episode-fixture",
                        "claim_request_id": "claim-workbench-old"} if transport.held else None)
            return {"status": {"control_held": transport.held, "control": control}}

        def recover_control(self, **expected: str) -> dict[str, Any]:
            recovered.append(expected)
            transport.held = False
            return {"result": {"type": "release_control_result", "status": "released",
                               "control_epoch": "epoch-fixture",
                               "runtime_instance_id": "runtime-fixture",
                               "game_continuity_id": "episode-fixture"}}

    manager = MatchingManager()
    monkeypatch.setattr(service, "_managed_service_handles",
                        lambda _profile: (transport, manager, transport.ready()))
    assert service.recover_control()["outcome"] == "still_unknown"
    assert recovered == [{"expected_control_epoch": "epoch-fixture",
                          "expected_runtime_instance_id": "runtime-fixture",
                          "expected_game_continuity_id": "episode-fixture"}]


def test_release_recovery_does_not_release_a_newer_same_episode_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, pin, _audit, _checked = fixture(tmp_path)
    service = LocalEnvironmentService(config)
    service.record = {
        "schema": "stpd/local-managed-environment-v1", "status": "unknown",
        "error_code": "managed_control_release_unknown", "session_id": "f" * 32,
        "scenario_id": SCENARIO["id"], "seed": SCENARIO["seed"],
        "host_package_pin": pin, "producer": service._producer(), "events": [],
        "service_binding": {"service_instance_id": "managed_service_fixture",
                             "runtime_instance_id": "runtime-fixture",
                             "game_continuity_id": "episode-fixture"},
        "uncertain_control_epoch": "old-epoch",
    }
    transport = ManagedServiceFixture(held=True)
    calls: list[bool] = []

    class NewOwnerManager:
        def status(self) -> dict[str, Any]:
            return {"status": {"control_held": True, "control": {
                "control_epoch": "new-epoch", "runtime_instance_id": "runtime-fixture",
                "game_continuity_id": "episode-fixture",
                "claim_request_id": "claim-runtime-new"}}}

        def recover_control(self, **_expected: str) -> dict[str, Any]:
            calls.append(True)
            raise AssertionError("must not release a later owner's lease")

    manager = NewOwnerManager()
    monkeypatch.setattr(service, "_managed_service_handles",
                        lambda _profile: (transport, manager, transport.ready()))
    with pytest.raises(BoundaryError, match="managed_control_identity_mismatch"):
        service.recover_control()
    assert calls == []


def test_lost_claim_reply_does_not_recover_a_later_same_episode_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, pin, _audit, _checked = fixture(tmp_path)
    service = LocalEnvironmentService(config)
    service.record = {
        "schema": "stpd/local-managed-environment-v1", "status": "failed",
        "error_code": "managed_control_claim_unknown", "session_id": "f" * 32,
        "scenario_id": SCENARIO["id"], "seed": SCENARIO["seed"],
        "host_package_pin": pin, "producer": service._producer(), "events": [],
        "service_binding": {"service_instance_id": "managed_service_fixture",
                             "runtime_instance_id": "runtime-fixture",
                             "game_continuity_id": "episode-fixture"},
        "uncertain_claim_request_id": "claim-workbench-old",
    }
    transport = ManagedServiceFixture(held=True)

    class LaterOwnerManager:
        def status(self) -> dict[str, Any]:
            return {"status": {"control_held": True, "control": {
                "control_epoch": "later-epoch", "runtime_instance_id": "runtime-fixture",
                "game_continuity_id": "episode-fixture",
                "claim_request_id": "claim-runtime-new"}}}

        def recover_control(self, **_expected: str) -> dict[str, Any]:
            pytest.fail("must not release a later same-episode claim")

    manager = LaterOwnerManager()
    monkeypatch.setattr(service, "_managed_service_handles",
                        lambda _profile: (transport, manager, transport.ready()))
    with pytest.raises(BoundaryError, match="managed_control_identity_mismatch"):
        service.recover_control()


def test_host_service_held_control_rejects_without_observe_or_steal() -> None:
    transport = ManagedServiceFixture(held=True)
    environment = _ManagedServiceEnvironment(
        transport, object(), transport.ready(), "text-menu-v1"
    )
    with pytest.raises(BoundaryError, match="managed_control_held"):
        environment.observe_text_menu()
    assert [call["command"] for call in transport.calls] == ["claim_control"]


def test_host_service_unknown_release_is_not_retried_by_detach() -> None:
    transport = ManagedServiceFixture(fail_release=True)
    environment = _ManagedServiceEnvironment(
        transport, object(), transport.ready(), "text-menu-v1"
    )
    with pytest.raises(BoundaryError, match="managed_control_release_unknown"):
        environment.observe_text_menu()
    assert environment.control is not None
    assert environment.control["uncertain"] == "release"
    assert environment.control["epoch"] == "epoch-fixture"
    assert environment.control["request_id"]
    with pytest.raises(BoundaryError, match="managed_control_release_unknown"):
        environment.close()
    assert [call["command"] for call in transport.calls].count("release_control") == 1


def test_workbench_detach_never_calls_manager_close() -> None:
    transport = ManagedServiceFixture()
    manager = type("Manager", (), {"close_host": lambda _self: pytest.fail(
        "Workbench detach must not close the Host"
    )})()
    environment = _ManagedServiceEnvironment(
        transport, manager, transport.ready(), "text-menu-v1"
    )
    environment.close()
    assert transport.calls == []


def test_host_service_malformed_submit_result_releases_without_claiming_delivery() -> None:
    class MalformedSubmit(ManagedServiceFixture):
        def request(self, command: dict[str, Any]) -> dict[str, Any]:
            response = super().request(command)
            if command["command"] == "text_submit":
                response["result"]["result"] = None
            return response

    transport = MalformedSubmit()
    environment = _ManagedServiceEnvironment(
        transport, object(), transport.ready(), "text-menu-v1"
    )
    with pytest.raises(BoundaryError, match="managed_submit_unconfirmed"):
        environment.submit_text_menu("action-0", "page-0", "episode-fixture", "bad-result")
    assert environment.last_submit_result is None
    assert environment.control is None
    assert [call["command"] for call in transport.calls] == [
        "claim_control", "text_submit", "release_control"
    ]


def test_host_service_submit_unknown_does_not_reuse_prior_receipt() -> None:
    class FailsSecondSubmit(ManagedServiceFixture):
        submits = 0

        def request(self, command: dict[str, Any]) -> dict[str, Any]:
            if command["command"] == "text_submit":
                self.submits += 1
                if self.submits == 2:
                    self.calls.append(command)
                    raise RuntimeError("reply lost")
            return super().request(command)

    transport = FailsSecondSubmit()
    environment = _ManagedServiceEnvironment(
        transport, object(), transport.ready(), "text-menu-v1"
    )
    assert environment.submit_text_menu(
        "action-0", "page-0", "episode-fixture", "request-first"
    )["status"] == "applied"
    with pytest.raises(RuntimeError, match="reply lost"):
        environment.submit_text_menu(
            "action-1", "page-1", "episode-fixture", "request-second"
        )
    assert environment.last_submit_result is None


def test_resume_reconnects_same_host_episode_without_reset_and_archives_new_segment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, pin, audit, _checked = fixture(tmp_path)
    service = LocalEnvironmentService(config)
    service.record = {
        "schema": "stpd/local-managed-environment-v1", "status": "stopped",
        "session_id": "a" * 32, "scenario_id": SCENARIO["id"], "seed": SCENARIO["seed"],
        "input_profile": "text-menu-v1", "host_package_pin": pin,
        "producer": service._producer(), "events": [], "context": None,
        "episode_identity": None, "service_binding": {
            "service_instance_id": "managed_service_fixture",
            "runtime_instance_id": "runtime-fixture", "game_continuity_id": "episode-fixture"},
        "report_artifact_id": "b" * 64,
    }
    transport = ManagedServiceFixture()
    transport.audit = audit
    ready = transport.ready()
    ready["candidate_build"] = audit
    ready["exact_game"] = {"sts2_dll_sha256": SCENARIO["exact_game_assembly_sha256"]}
    monkeypatch.setattr(service, "_managed_service_handles",
                        lambda _profile: (transport, object(), ready))

    started = service.resume()["session"]
    resumed = wait_status(service, "active")["session"]
    assert started["status"] == "resuming"
    assert resumed["continued_from_session_id"] == "a" * 32
    assert resumed["continued_from_report_artifact_id"] == "b" * 64
    assert resumed["service_binding"]["game_continuity_id"] == "episode-fixture"
    assert [call["command"] for call in transport.calls] == [
        "episode_identity", "claim_control", "text_observe", "release_control"
    ]
    assert not any(call["command"] == "reset" for call in transport.calls)

    stopped = service.stop(resumed["session_id"])["session"]
    report = service.report(stopped["report_artifact_id"])
    assert report["session_semantics"] == "workbench-segment-v2-host-owned-service"
    assert report["host_service_status"] == "not_closed_by_workbench"
    assert report["evidence_scope"] == "workbench-client-actions-only"
    assert report["continued_from_report_artifact_id"] == "b" * 64


def test_resume_while_host_control_is_held_does_not_claim_or_observe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, pin, _audit, _checked = fixture(tmp_path)
    service = LocalEnvironmentService(config)
    service.record = {
        "schema": "stpd/local-managed-environment-v1", "status": "interrupted_unknown",
        "session_id": "c" * 32, "scenario_id": SCENARIO["id"], "seed": SCENARIO["seed"],
        "input_profile": "text-menu-v1", "host_package_pin": pin,
        "producer": service._producer(), "events": [], "context": None,
        "episode_identity": None, "service_binding": {
            "service_instance_id": "managed_service_fixture",
            "runtime_instance_id": "runtime-fixture", "game_continuity_id": "episode-fixture"},
    }
    transport = ManagedServiceFixture(held=True)
    ready = transport.ready()
    manager = type("ManagerFixture", (), {"status": lambda _self: {"status": {
        "control_held": True, "control": {"control_epoch": "epoch-other",
            "runtime_instance_id": "runtime-fixture", "game_continuity_id": "episode-fixture",
            "claim_request_id": "claim-other"}}}})()
    monkeypatch.setattr(service, "_managed_service_handles",
                        lambda _profile: (transport, manager, ready))
    service.resume()
    held = wait_status(service, "control_held")["session"]
    assert held["context"] is None
    assert held["error_code"] == "managed_control_held"
    assert transport.calls == []


def test_management_actions_reject_stale_session_target_before_host_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, pin, _audit, _checked = fixture(tmp_path)
    service = LocalEnvironmentService(config)
    service.record = {
        "schema": "stpd/local-managed-environment-v1", "status": "unknown",
        "session_id": "new-session", "scenario_id": SCENARIO["id"],
        "seed": SCENARIO["seed"], "input_profile": "text-menu-v1",
        "host_package_pin": pin, "producer": service._producer(), "events": [],
        "service_binding": {"service_instance_id": "service-new",
                             "runtime_instance_id": "runtime-new",
                             "game_continuity_id": "episode-new"},
        "error_code": "managed_control_release_unknown",
    }
    monkeypatch.setattr(service, "_managed_service_handles",
                        lambda _profile: pytest.fail("stale target must not reach Host"))
    expected = {"expected_session_id": "old-session",
                "expected_service_instance_id": "service-new",
                "expected_runtime_instance_id": "runtime-new",
                "expected_game_continuity_id": "episode-new"}
    with pytest.raises(BoundaryError, match="managed_service_binding_mismatch"):
        service.resume(**expected)
    with pytest.raises(BoundaryError, match="managed_service_binding_mismatch"):
        service.recover_control(**expected)
    with pytest.raises(BoundaryError, match="managed_service_binding_mismatch"):
        service.close_environment(**{key: value for key, value in expected.items()
                                    if key != "expected_session_id"},
                                  expected_session_id="old-session")


def test_managed_runtime_target_is_read_only_and_returns_private_attach_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, pin, audit, _checked = fixture(tmp_path)
    service = LocalEnvironmentService(config)
    transport = ManagedServiceFixture()
    manager_status = {"control_held": False, "control": None}

    class ManagerFixture:
        def status(self) -> dict[str, Any]:
            return {"status": manager_status}

    ready = {**transport.ready(), "host_identity": {
        "package_name": HOST_PACKAGE, "version": pin["version"]},
        "candidate_build": audit,
        "exact_game": {"sts2_dll_sha256": SCENARIO["exact_game_assembly_sha256"]},
        "environment_fingerprint": "f" * 64,
        "text_menu_contracts": [{"input_profile": "text-menu-v2"}]}
    monkeypatch.setattr(service, "_managed_service_handles",
                        lambda _profile: (transport, ManagerFixture(), ready))
    target = service.managed_runtime_target()
    assert target["schema"] == "stpd/workbench-managed-runtime-target-v1"
    assert target["client_attachment"] == config.state_dir / "managed-host-service" / "client.json"
    assert target["host_package_pin"] == pin
    assert target["candidate_build"] == audit
    assert target["service_instance_id"] == "managed_service_fixture"
    assert target["runtime_instance_id"] == "runtime-fixture"
    assert target["game_continuity_id"] == "episode-fixture"
    assert target["text_menu_contracts"] == ready["text_menu_contracts"]
    assert target["profile_sha256"] == hashlib.sha256(
        (config.state_dir / PROFILE_FILE).read_bytes()
    ).hexdigest()
    assert transport.calls == []
    ready["episode"]["control_held"] = True
    manager_status["control_held"] = True
    with pytest.raises(BoundaryError, match="managed_episode_unavailable"):
        service.managed_runtime_target()
    assert transport.calls == []


def test_explicit_host_close_requires_ack_then_removes_only_fixed_attachments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _host, _candidate, pin, _audit, _checked = fixture(tmp_path)
    service = LocalEnvironmentService(config)
    directory = config.state_dir / "managed-host-service"
    directory.mkdir(mode=0o700)
    client_file, manager_file = directory / "client.json", directory / "manager.json"
    client_file.write_text("private descriptor")
    manager_file.write_text("private descriptor")
    ready = {"service_instance_id": "managed_service_fixture",
             "episode": {"control_held": False, "tainted": False, "closed": False}}

    class ManagerFixture:
        def status(self) -> dict[str, Any]:
            return {"status": {"control_held": False, "control": None}}

        def close_host(self) -> dict[str, Any]:
            return {"result": {"type": "close_result"}}

    manager = ManagerFixture()
    monkeypatch.setattr(service, "_managed_service_handles",
                        lambda _profile: (object(), manager, ready))
    result = service.close_environment()
    assert result == {"status": "closed", "service_instance_id": "managed_service_fixture"}
    assert not client_file.exists() and not manager_file.exists()
    assert service.record["host_service_closed"] is True
