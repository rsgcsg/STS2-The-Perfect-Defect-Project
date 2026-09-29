"""Synthetic contract checks; these do not start the Managed game or load weights."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_text_menu_data import snapshot as text_snapshot

from stpd.fullrun.memory_token_inputs import (
    project_memory_profile_snapshot,
    project_memory_snapshot,
)
from stpd.managed_memory_smoke import SmokeLimits, run_managed_memory_smoke


def page(name: str) -> dict:
    value = text_snapshot(name)
    value["persistent"]["content"]["run"]["ascension"] = 0
    value["persistent"]["content"]["player"]["character_definition_id"] = "DEFECT"
    value["session"] = {"runtime_instance_id": "managed-runtime",
                        "environment_fingerprint": "exact-candidate"}
    return value


def terminal(name: str) -> dict:
    value = page(name)
    value["status"] = "observed"
    value["interaction"] = {
        "interaction_id": f"opaque-interaction-{name}", "kind": "game_over",
        "stage": "complete", "content_schema": "sts2.player-environment/surface/game_over-1",
        "capabilities": [], "content": {
            "surface": {"kind": "game_over", "stage": "complete", "victory": False},
            "context": {"kind": "terminal"}}}
    value["completeness"]["missing"] = []
    value["menu_actions"].update(status="complete", actions=[], materialized_count=0,
                                 total_count=0)
    return value


V2_FIXTURES = Path(__file__).parent / "fixtures" / "text_menu_v2"


def v2_fixture(name: str) -> dict:
    return json.loads((V2_FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def v2_page(name: str) -> dict:
    value = v2_fixture(name)
    value["persistent"] = copy.deepcopy(page("before")["persistent"])
    value["session"] = {"runtime_instance_id": "managed-runtime",
                        "environment_fingerprint": "exact-candidate"}
    return value


def v2_trajectory() -> list[dict]:
    root = v2_page("targeted-root")
    selected = v2_fixture("targeted-select")["successor"]
    selected["persistent"] = copy.deepcopy(root["persistent"])
    selected["session"] = copy.deepcopy(root["session"])
    confirmation = copy.deepcopy(selected)
    confirmation["snapshot_id"] = "v2-menu-22"
    confirmation["sequence"] += 1
    confirmation["menu"].update(cursor="card_confirmation", revision=2,
                                selection=[{"role": "card", "referent_id": "card-C"},
                                           {"role": "target", "referent_id": "enemy-E"}])
    confirmation["menu_actions"]["actions"] = [
        {"action_id": "v2-play-card-C-on-E", "kind": "native_input", "verb": "play",
         "label": "Play Strike on Jaw Worm", "subject_referent_id": "card-C",
         "arguments": [{"role": "target", "referent_id": "enemy-E"}],
         "effect_domain": "native_input"},
        {"action_id": "v2-cancel-target-E", "kind": "system_selection",
         "verb": "cancel_selection", "label": "Cancel target selection",
         "subject_referent_id": None, "arguments": [], "effect_domain": "text_menu"},
    ]
    confirmation["menu_actions"].update(materialized_count=2, total_count=2)
    done = v2_page("managed-game-over")
    for snapshot in (root, selected, confirmation):
        project_memory_profile_snapshot(snapshot, "text-menu-v2")
    return [root, selected, confirmation, done]


class Environment:
    def __init__(self, pages: list[dict], *, continuities: list[str] | None = None,
                 result_status: str = "applied", successor: dict | None = None) -> None:
        self.pages = pages
        self.continuities = continuities or [f"game-{index}" for index in range(len(pages))]
        self.result_status = result_status
        self.successor = successor
        self.resets: list[str] = []
        self.submissions: list[tuple[str, str, str, str]] = []
        self.closed = False

    def reset(self, seed: str) -> dict:
        self.resets.append(seed)
        return {"schema": "raw-managed-snapshot"}

    def episode_identity(self) -> dict:
        return {"episode_provenance": {"verdict": "provenance_pass",
                "requested_seed": self.resets[-1], "actual_seed": self.resets[-1]}}

    def observe_text_menu(self) -> dict:
        index = len(self.resets) - 1
        return {"schema": "sts2.player-environment/text-menu-observation-context-1",
                "snapshot": self.pages[index], "game_continuity_id": self.continuities[index]}

    def submit_text_menu(self, action_id: str, expected_snapshot_id: str,
                         expected_game_continuity_id: str, request_id: str) -> dict:
        self.submissions.append((action_id, expected_snapshot_id,
                                 expected_game_continuity_id, request_id))
        source = self.pages[len(self.resets) - 1]
        action = next(item for item in source["menu_actions"]["actions"]
                      if item["action_id"] == action_id)
        status = self.result_status
        return {"schema": "sts2.player-environment/text-menu-action-result-1",
                "input_profile": "text-menu-v1", "request_id": request_id,
                "status": status, "action": action if status == "applied" else None,
                "effect_domain": action["effect_domain"] if status == "applied" else None,
                "native_delivery": "delivered" if status == "applied" else
                    "unknown" if status == "unknown" else None,
                "successor": self.successor if status == "applied" else None}

    def close(self) -> None:
        self.closed = True


class Scorer:
    def __init__(self, scores: tuple[float, ...] = (0.0, 1.0),
                 wrong_binding: bool = False) -> None:
        self.scores = scores
        self.wrong_binding = wrong_binding
        self.tokens: list[str] = []

    def observe_and_score(self, *, continuity_token: str, snapshot_bytes: bytes,
                          expected_candidate_digest: str,
                          expected_candidate_count: int) -> SimpleNamespace:
        import json

        public = project_memory_snapshot(json.loads(snapshot_bytes))
        assert expected_candidate_digest == public.candidate_digest
        assert expected_candidate_count == len(public.action_ids)
        self.tokens.append(continuity_token)
        return SimpleNamespace(
            action_ids=(tuple(reversed(public.action_ids)) if self.wrong_binding
                        else public.action_ids),
            candidate_digest=public.candidate_digest, scores=self.scores)


class V2Environment(Environment):
    def __init__(self, trajectory: list[dict], *, unknown: bool = False) -> None:
        super().__init__([trajectory[0]])
        self.trajectory = trajectory
        self.position = 0
        self.unknown = unknown
        self.profiles: list[str] = []

    def observe_text_menu(self, *, input_profile: str) -> dict:
        self.profiles.append(input_profile)
        return {"schema": "sts2.player-environment/text-menu-observation-context-2",
                "snapshot": self.trajectory[self.position], "game_continuity_id": "game-0"}

    def submit_text_menu(self, action_id: str, expected_snapshot_id: str,
                         expected_game_continuity_id: str, request_id: str, *,
                         input_profile: str) -> dict:
        self.profiles.append(input_profile)
        source = self.trajectory[self.position]
        self.submissions.append((action_id, expected_snapshot_id,
                                 expected_game_continuity_id, request_id))
        assert expected_snapshot_id == source["snapshot_id"]
        assert expected_game_continuity_id == "game-0"
        action = next(item for item in source["menu_actions"]["actions"]
                      if item["action_id"] == action_id)
        result = v2_fixture("targeted-select")
        result.update(input_profile=input_profile, request_id=request_id,
                      action=action, effect_domain=action["effect_domain"])
        if self.unknown:
            result.update(status="unknown", action=None, effect_domain="native_input",
                          native_delivery="unknown", successor=None, retry="never",
                          reason_code="native_delivery_unknown", attribution=None)
            return result
        self.position += 1
        result.update(native_delivery="delivered" if action["effect_domain"] == "native_input"
                      else None, successor=self.trajectory[self.position])
        return result


class V2Scorer:
    def __init__(self, *, wrong_binding: bool = False) -> None:
        self.wrong_binding = wrong_binding
        self.tokens: list[str] = []

    def observe_and_score(self, *, continuity_token: str, snapshot_bytes: bytes,
                          expected_candidate_digest: str,
                          expected_candidate_count: int) -> SimpleNamespace:
        public = project_memory_profile_snapshot(json.loads(snapshot_bytes), "text-menu-v2")
        assert expected_candidate_digest == public.candidate_digest
        assert expected_candidate_count == len(public.action_ids)
        self.tokens.append(continuity_token)
        return SimpleNamespace(
            action_ids=public.action_ids,
            candidate_digest=("wrong-digest" if self.wrong_binding
                              else public.candidate_digest),
            scores=tuple(1.0 if index == 0 else 0.0 for index in range(len(public.action_ids))),
        )


def run(environment: Environment, scorer: Scorer, *, seeds: tuple[str, ...] = ("SEED1",),
        limits: SmokeLimits | None = None, clock=None) -> dict:
    return run_managed_memory_smoke(
        environment, scorer, seeds=seeds, model_id="a" * 64,
        reset_each_step=True, limits=limits, **({} if clock is None else {"clock": clock}))


def test_one_exact_native_leaf_and_terminal_release() -> None:
    source = page("before")
    environment = Environment([source], successor=terminal("after"))
    scorer = Scorer()
    report = run(environment, scorer)
    assert report["status"] == "engineering_smoke_complete"
    assert report["native_delivered"] == report["terminal_observed"] == 1
    assert report["submissions"] == report["policy_calls"] == 1
    assert environment.submissions[0][:3] == ("opaque-play", source["snapshot_id"], "game-0")
    assert report["qualification"] == "engineering_only"
    assert report["policy_runtime_http"] is False
    assert report["requested_character"] == "Defect"
    assert report["observed_character"] == "DEFECT"
    assert report["requested_ascension"] == report["observed_ascension"] == 0
    assert environment.closed


def test_wrong_binding_and_nonfinite_scores_stop_before_submit() -> None:
    for scorer, reason in ((Scorer(wrong_binding=True), "score_binding_invalid"),
                           (Scorer((0.0, float("nan"))), "score_binding_invalid")):
        environment = Environment([page("before")])
        report = run(environment, scorer)
        assert report["stop_reason"] == reason
        assert not environment.submissions
        assert environment.closed


def test_unknown_and_stale_stop_without_retry_or_reset() -> None:
    for status, reason in (("unknown", "native_delivery_unknown"),
                           ("not_applied", "text_action_not_applied")):
        environment = Environment([page("before")], result_status=status)
        report = run(environment, Scorer(), seeds=("SEED1", "SEED2"))
        assert report["stop_reason"] == reason
        assert report["episodes_started"] == 1
        assert len(environment.submissions) == 1
        assert environment.closed


def test_complete_terminal_has_no_model_call_or_submission() -> None:
    environment = Environment([terminal("done")])
    scorer = Scorer()
    report = run(environment, scorer)
    assert report["stop_reason"] == "terminal_observed"
    assert report["status"] == "stopped"
    assert report["policy_calls"] == report["submissions"] == 0
    assert scorer.tokens == []
    assert environment.closed


def test_malformed_terminal_menu_cannot_count_as_complete() -> None:
    broken = terminal("done")
    del broken["menu"]
    environment = Environment([broken])
    report = run(environment, Scorer())
    assert report["status"] == "stopped"
    assert report["stop_reason"] == "terminal_incomplete"
    assert report["terminal_observed"] == report["submissions"] == 0
    assert environment.closed

    successor_environment = Environment([page("before")], successor=broken)
    successor_report = run(successor_environment, Scorer())
    assert successor_report["stop_reason"] == "terminal_incomplete"
    assert successor_report["native_delivered"] == 1
    assert successor_report["terminal_observed"] == 0
    assert successor_environment.closed


def test_reset_rotates_continuity_even_for_same_seed() -> None:
    environment = Environment([terminal("first"), terminal("second")],
                              continuities=["same-game", "same-game"])
    report = run(environment, Scorer(), seeds=("SEED1", "SEED1"))
    assert report["stop_reason"] == "continuity_reused_after_reset"
    assert report["episodes_started"] == 2
    assert environment.closed


def test_two_scored_episodes_receive_distinct_host_continuities() -> None:
    class TwoEpisodeEnvironment(Environment):
        def submit_text_menu(self, action_id: str, expected_snapshot_id: str,
                             expected_game_continuity_id: str, request_id: str) -> dict:
            self.successor = terminal(f"after-{len(self.resets)}")
            return super().submit_text_menu(action_id, expected_snapshot_id,
                                            expected_game_continuity_id, request_id)

    environment = TwoEpisodeEnvironment([page("first"), page("second")])
    scorer = Scorer()
    report = run(environment, scorer, seeds=("SEED1", "SEED1"),
                 limits=SmokeLimits(max_submissions=2))
    assert report["terminal_observed"] == report["native_delivered"] == 2
    assert scorer.tokens == ["game-0", "game-1"]
    assert [value[2] for value in environment.submissions] == scorer.tokens
    assert environment.closed


def test_submission_budget_and_wall_budget_close_child() -> None:
    environment = Environment([page("before")], successor=page("after"))
    report = run(environment, Scorer())
    assert report["stop_reason"] == "submission_budget_exhausted"
    assert report["native_delivered"] == 1
    assert len(environment.submissions) == 1
    assert environment.closed

    calls = iter((0.0, 1.0, 2.0, 3.0))
    timed = Environment([page("before")])
    report = run(timed, Scorer(), limits=SmokeLimits(max_seconds=2.5),
                 clock=lambda: next(calls))
    assert report["stop_reason"] == "wall_budget_exhausted"
    assert timed.submissions == []
    assert timed.closed


def test_post_submit_deadline_preserves_known_native_receipt_and_stops() -> None:
    environment = Environment([page("before"), page("next")],
                              successor=terminal("after"))
    report = run(environment, Scorer(), seeds=("SEED1", "SEED2"),
                 limits=SmokeLimits(max_seconds=1),
                 clock=lambda: 2.0 if environment.submissions else 0.0)
    assert report["stop_reason"] == "wall_budget_exhausted"
    assert report["native_delivered"] == report["terminal_observed"] == 1
    assert report["submissions"] == 1
    assert environment.resets == ["SEED1"]
    assert environment.closed


def test_invalid_seed_closes_owned_child_and_cli_never_constructs_it(
    monkeypatch, capsys,
) -> None:
    environment = Environment([page("before")])
    report = run(environment, Scorer(), seeds=("invalid-seed",))
    assert report["stop_reason"] == "invalid_smoke_request"
    assert environment.resets == []
    assert environment.closed

    script = Path(__file__).resolve().parents[1] / "tools" / "managed_memory_smoke.py"
    spec = importlib.util.spec_from_file_location("managed_memory_smoke_cli_test", script)
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    constructed: list[object] = []

    class Child:
        def observe_text_menu(self) -> None:
            pass

        def submit_text_menu(self) -> None:
            pass

        def __init__(self, *args, **kwargs) -> None:
            constructed.append((args, kwargs))

    monkeypatch.setitem(sys.modules, "sts2_headless",
                        SimpleNamespace(ManagedPlayerEnvironment=Child))
    monkeypatch.setattr(cli, "activate_host_runtime_client", lambda *args: None)
    monkeypatch.setattr(cli, "load_host_runtime_pin", lambda *args: {})
    monkeypatch.setattr(cli, "validate_memory_package", lambda *args: (
        {"ids": {"model": "a" * 64}}, None, None,
        SimpleNamespace(reset_each_step=True)))
    for extra in (("--seed", "invalid-seed"),
                  ("--seed", "SEEDO"), ("--seed", "SEEDI"),
                  ("--seed", "SEED1", "--character", "not-a-character"),
                  ("--seed", "SEED1", "--ascension", "1")):
        monkeypatch.setattr(sys, "argv", ["managed_memory_smoke.py", "--candidate", "/unused",
                                         "--model-export", "/unused", *extra])
        assert cli.main() == 2
    assert constructed == []
    assert '"status": "unavailable"' in capsys.readouterr().out


@pytest.mark.parametrize("character,extra", [
    ("Defect", ()), ("Ironclad", ("--character", "Ironclad")),
])
def test_cli_passes_character_and_reports_observed_selection(
    monkeypatch, capsys, character: str, extra: tuple[str, ...],
) -> None:
    script = Path(__file__).resolve().parents[1] / "tools" / "managed_memory_smoke.py"
    spec = importlib.util.spec_from_file_location("managed_memory_smoke_cli_launch_test", script)
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    started: list[tuple[list[str], Environment]] = []

    class Child(Environment):
        def __init__(self, command: list[str], **kwargs) -> None:
            source, successor = page("before"), terminal("after")
            for snapshot in (source, successor):
                snapshot["persistent"]["content"]["player"]["character_definition_id"] = \
                    character.upper()
            super().__init__([source], successor=successor)
            started.append((command, self))

    monkeypatch.setitem(sys.modules, "sts2_headless",
                        SimpleNamespace(ManagedPlayerEnvironment=Child))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda _: None))
    monkeypatch.setattr(cli, "activate_host_runtime_client", lambda *args: None)
    monkeypatch.setattr(cli, "load_host_runtime_pin", lambda *args: {})
    monkeypatch.setattr(cli, "validate_memory_package", lambda *args: (
        {"ids": {"model": "a" * 64}}, None, None,
        SimpleNamespace(reset_each_step=True, cpu_threads=2)))
    monkeypatch.setattr(cli.OnlineM2Scorer, "from_export", lambda *args: Scorer())
    monkeypatch.setattr(cli, "driver_command", lambda *args: ["node", "/exact/driver.mjs"])
    monkeypatch.setattr(sys, "argv", ["managed_memory_smoke.py", "--candidate", "/unused",
                                     "--model-export", "/unused", "--seed", "SEED1", *extra])
    assert cli.main() == 0
    assert len(started) == 1
    command, child = started[0]
    assert command == ["node", "/exact/driver.mjs", "--character", character,
                       "--timeout-ms", "5000"]
    report = json.loads(capsys.readouterr().out)
    assert report["requested_character"] == character
    assert report["observed_character"] == character.upper()
    assert report["requested_ascension"] == report["observed_ascension"] == 0
    assert child.closed


@pytest.mark.parametrize("seed", ["SEEDO", "SEEDI"])
def test_ambiguous_game_seed_is_rejected_before_reset(seed: str) -> None:
    environment = Environment([page("before")])
    scorer = Scorer()
    report = run(environment, scorer, seeds=(seed,))
    assert report["stop_reason"] == "invalid_smoke_request"
    assert environment.resets == []
    assert scorer.tokens == []
    assert environment.closed


def test_missing_text_consumer_capability_never_uses_raw_fallback() -> None:
    environment = Environment([page("before")])
    environment.observe_text_menu = None  # type: ignore[method-assign]
    report = run(environment, Scorer())
    assert report["stop_reason"] == "text_menu_consumer_unavailable"
    assert environment.resets == []
    assert environment.closed


def test_seed_provenance_must_match_the_requested_episode() -> None:
    class WrongSeed(Environment):
        def episode_identity(self) -> dict:
            return {"episode_provenance": {"verdict": "provenance_pass",
                    "requested_seed": "OTHER", "actual_seed": "OTHER"}}

    environment = WrongSeed([page("before")])
    report = run(environment, Scorer())
    assert report["stop_reason"] == "episode_provenance_unverified"
    assert report["observations"] == report["submissions"] == 0
    assert environment.closed


def test_character_and_ascension_must_match_observed_page_before_scoring() -> None:
    for field, value in (("character_definition_id", "IRONCLAD"), ("ascension", 1)):
        source = page("before")
        parent = source["persistent"]["content"]
        parent["player" if field == "character_definition_id" else "run"][field] = value
        environment = Environment([source])
        scorer = Scorer()
        report = run(environment, scorer)
        assert report["stop_reason"] == "episode_configuration_mismatch"
        assert report["observed_character"] == parent["player"]["character_definition_id"]
        assert report["observed_ascension"] == parent["run"]["ascension"]
        assert report["submissions"] == report["policy_calls"] == 0
        assert scorer.tokens == []
        assert environment.closed

    missing = page("missing")
    del missing["persistent"]["content"]["run"]["ascension"]
    environment = Environment([missing])
    report = run(environment, Scorer())
    assert report["stop_reason"] == "episode_configuration_unverified"
    assert report["submissions"] == report["policy_calls"] == 0
    assert environment.closed


def test_post_submit_exception_never_retries_and_closes_child() -> None:
    class BrokenResponse(Environment):
        def submit_text_menu(self, action_id: str, expected_snapshot_id: str,
                             expected_game_continuity_id: str, request_id: str) -> dict:
            self.submissions.append((action_id, expected_snapshot_id,
                                     expected_game_continuity_id, request_id))
            raise RuntimeError("post-submit response malformed")

    environment = BrokenResponse([page("before"), page("later")])
    report = run(environment, Scorer(), seeds=("SEED1", "SEED2"))
    assert report["stop_reason"] == "experiment_failed"
    assert report["submissions"] == len(environment.submissions) == 1
    assert environment.resets == ["SEED1"]
    assert environment.closed


def test_v2_text_selection_counts_as_submission_but_not_native_delivery() -> None:
    trajectory = v2_trajectory()
    environment = V2Environment(trajectory)
    scorer = V2Scorer()
    report = run_managed_memory_smoke(
        environment, scorer, seeds=("SEED1",), model_id="a" * 64,
        reset_each_step=True, input_profile="text-menu-v2")
    assert report["input_profile"] == "text-menu-v2"
    assert report["status"] == "stopped"
    assert report["stop_reason"] == "submission_budget_exhausted"
    assert report["submissions"] == 1 and report["native_delivered"] == 0
    assert environment.submissions[0][:3] == (
        trajectory[0]["menu_actions"]["actions"][0]["action_id"],
        trajectory[0]["snapshot_id"], "game-0")
    assert environment.profiles == ["text-menu-v2", "text-menu-v2"]
    assert scorer.tokens == ["game-0"]
    assert environment.closed


def test_v2_complete_bound_selection_target_native_leaf_and_observed_terminal() -> None:
    trajectory = v2_trajectory()
    environment = V2Environment(trajectory)
    report = run_managed_memory_smoke(
        environment, V2Scorer(), seeds=("SEED1",), model_id="a" * 64,
        reset_each_step=True, input_profile="text-menu-v2",
        limits=SmokeLimits(max_submissions=3, max_policy_calls=3))
    assert report["status"] == "engineering_smoke_complete"
    assert report["stop_reason"] == "terminal_observed"
    assert report["submissions"] == report["policy_calls"] == 3
    assert report["native_delivered"] == report["terminal_observed"] == 1
    assert [item[0] for item in environment.submissions] == [
        snapshot["menu_actions"]["actions"][0]["action_id"]
        for snapshot in trajectory[:3]]
    assert [item[1] for item in environment.submissions] == [
        snapshot["snapshot_id"] for snapshot in trajectory[:3]]
    assert set(environment.profiles) == {"text-menu-v2"}
    assert environment.closed


def test_v2_unknown_and_wrong_score_binding_never_retry() -> None:
    for environment, scorer, reason, submissions in (
        (V2Environment(v2_trajectory(), unknown=True), V2Scorer(),
         "native_delivery_unknown", 1),
        (V2Environment(v2_trajectory()), V2Scorer(wrong_binding=True),
         "score_binding_invalid", 0),
    ):
        report = run_managed_memory_smoke(
            environment, scorer, seeds=("SEED1", "SEED2"), model_id="a" * 64,
            reset_each_step=True, input_profile="text-menu-v2",
            limits=SmokeLimits(max_submissions=3))
        assert report["stop_reason"] == reason
        assert report["episodes_started"] == 1
        assert len(environment.submissions) == submissions
        assert environment.closed


@pytest.mark.parametrize("mutation,reason", [
    ("request", "text_result_invalid"),
    ("schema", "text_result_invalid"),
    ("action", "text_result_binding_invalid"),
    ("session", "text_successor_identity_invalid"),
    ("continuity", "continuity_changed_within_episode"),
])
def test_v2_wrong_result_or_next_page_binding_stops_without_another_submit(
    mutation: str, reason: str,
) -> None:
    class Mutated(V2Environment):
        def submit_text_menu(self, action_id: str, expected_snapshot_id: str,
                             expected_game_continuity_id: str, request_id: str, *,
                             input_profile: str) -> dict:
            result = super().submit_text_menu(action_id, expected_snapshot_id,
                                              expected_game_continuity_id, request_id,
                                              input_profile=input_profile)
            if mutation == "request":
                result["request_id"] = "other-request"
            elif mutation == "schema":
                result["schema"] = "sts2.player-environment/text-menu-action-result-1"
            elif mutation == "action":
                result["action"] = self.trajectory[1]["menu_actions"]["actions"][0]
            elif mutation == "session":
                result["successor"] = copy.deepcopy(result["successor"])
                result["successor"]["session"]["runtime_instance_id"] = "other-runtime"
            return result

        def observe_text_menu(self, *, input_profile: str) -> dict:
            context = super().observe_text_menu(input_profile=input_profile)
            if mutation == "continuity" and self.position > 0:
                context["game_continuity_id"] = "other-game"
            return context

    environment = Mutated(v2_trajectory())
    report = run_managed_memory_smoke(
        environment, V2Scorer(), seeds=("SEED1",), model_id="a" * 64,
        reset_each_step=True, input_profile="text-menu-v2",
        limits=SmokeLimits(max_submissions=3))
    assert report["stop_reason"] == reason
    assert len(environment.submissions) == 1
    assert environment.closed


def test_v2_terminal_is_only_observed_and_malformed_selection_is_rejected() -> None:
    done = v2_page("managed-game-over")
    environment = V2Environment([done])
    report = run_managed_memory_smoke(
        environment, V2Scorer(), seeds=("SEED1",), model_id="a" * 64,
        reset_each_step=True, input_profile="text-menu-v2")
    assert report["stop_reason"] == "terminal_observed"
    assert report["terminal_observed"] == 1 and report["native_delivered"] == 0
    assert report["status"] == "stopped"
    assert environment.closed

    done["menu"]["selection"] = [{"role": "card", "referent_id": "opaque"}]
    malformed = V2Environment([done])
    bad_report = run_managed_memory_smoke(
        malformed, V2Scorer(), seeds=("SEED1",), model_id="a" * 64,
        reset_each_step=True, input_profile="text-menu-v2")
    assert bad_report["stop_reason"] == "terminal_incomplete"
    assert bad_report["terminal_observed"] == 0
    assert malformed.closed


def test_cli_explicit_v2_profile_reaches_package_scorer_public_host_and_budget(
    monkeypatch, capsys,
) -> None:
    script = Path(__file__).resolve().parents[1] / "tools" / "managed_memory_smoke.py"
    spec = importlib.util.spec_from_file_location("managed_memory_smoke_v2_cli_test", script)
    assert spec is not None and spec.loader is not None
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    calls: list[tuple[str, object]] = []
    children: list[V2Environment] = []

    class Child(V2Environment):
        def __init__(self, command: list[str], **kwargs) -> None:
            assert command == ["node", "/exact/driver.mjs", "--character", "Defect",
                               "--timeout-ms", "5000"]
            super().__init__(v2_trajectory())
            children.append(self)

    def validate(path: Path, *, input_profile: str) -> tuple:
        calls.append(("package", input_profile))
        return ({"ids": {"model": "a" * 64}}, None, None,
                SimpleNamespace(reset_each_step=True, cpu_threads=2))

    def scorer(*args, input_profile: str) -> V2Scorer:
        calls.append(("scorer", input_profile))
        return V2Scorer()

    monkeypatch.setitem(sys.modules, "sts2_headless",
                        SimpleNamespace(ManagedPlayerEnvironment=Child))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda _: None))
    monkeypatch.setattr(cli, "activate_host_runtime_client", lambda *args: None)
    monkeypatch.setattr(cli, "load_host_runtime_pin", lambda *args: {})
    monkeypatch.setattr(cli, "validate_memory_package", validate)
    monkeypatch.setattr(cli.OnlineM2Scorer, "from_export", scorer)
    monkeypatch.setattr(cli, "driver_command", lambda *args: ["node", "/exact/driver.mjs"])
    monkeypatch.setattr(sys, "argv", ["managed_memory_smoke.py", "--candidate", "/unused",
                                     "--model-export", "/unused", "--seed", "SEED1",
                                     "--input-profile", "text-menu-v2",
                                     "--max-policy-calls", "3", "--max-submissions", "3"])
    assert cli.main() == 0
    assert calls == [("package", "text-menu-v2"), ("scorer", "text-menu-v2")]
    assert len(children) == 1 and children[0].closed
    assert json.loads(capsys.readouterr().out)["native_delivered"] == 1
