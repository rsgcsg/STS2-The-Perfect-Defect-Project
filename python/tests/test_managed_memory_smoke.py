"""Synthetic contract checks; these do not start the Managed game or load weights."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from test_text_menu_data import snapshot as text_snapshot

from stpd.fullrun.memory_token_inputs import project_memory_snapshot
from stpd.managed_memory_smoke import SmokeLimits, run_managed_memory_smoke


def page(name: str) -> dict:
    value = text_snapshot(name)
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
    monkeypatch.setattr(sys, "argv", ["managed_memory_smoke.py", "--candidate", "/unused",
                                     "--model-export", "/unused", "--seed", "invalid-seed"])
    assert cli.main() == 2
    assert constructed == []
    assert '"status": "unavailable"' in capsys.readouterr().out


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
