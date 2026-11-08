"""RL5 S4 hook (P-REWRITE WP7 slice 1): a game starts from a seed or a verified map save.

The executor no longer hard-codes ``reset``: a save start loads the save in its fresh
Host process and proves progress through the save lineage, because the native load
path never replays ``initial_progress``. Host, model and Runtime are faked; the Host's
own load rules are covered by its tests (``managed-map-continue``).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.episode_start import (
    SAVE_START_SCHEMA,
    EpisodeStart,
    decode_episode_starts,
    require_first_frame_start,
    require_loaded_identity,
)

ROOT = Path(__file__).parents[2]
PROGRESS = {"recipe": "normal-sp-all-unlocked-v1", "progress_sha256": "progress"}
SAVE_SHA = "a" * 64
CANDIDATE = {"artifact_sha256": "b" * 64, "runtime_sts2_sha256": "c" * 64}


def _save_start(**changes):
    value = {
        "schema": SAVE_START_SCHEMA,
        "save": {"schema": "sts2.host-runtime/managed-map-continue-1",
                 "path": "/private/saves/one.save", "bytes": 40068, "sha256": SAVE_SHA,
                 "candidate": dict(CANDIDATE), "seed": "seed", "act": 2, "floor": 23,
                 "save_boundary": "map_presentation", "source_room_type": "MonsterRoom",
                 "parent_runtime_instance_id": "runtime-parent",
                 "parent_game_continuity_id": "game-parent"},
        "lineage": {"initial_progress_sha256": "progress", "character": "Defect",
                    "ascension": 0, "parent_intent_id": "intent-parent",
                    "parent_run_id": "run-parent"},
        # The parent game's own Host episode_identity (seeded, progress replayed).
        "parent_identity": {"adapter_runtime_instance_id": "runtime-parent",
                            "episode_provenance": {"origin": {"kind": "seed"},
                                                   "verdict": "provenance_pass",
                                                   "actual_seed": "seed"},
                            "initial_progress": {"progress_sha256": "progress",
                                                 "run_unlock_state_matches": True}},
    }
    for path, item in changes.items():
        target = value
        *parents, last = path.split(".")
        for name in parents:
            target = target[name]
        if item is KeyError:
            target.pop(last)
        else:
            target[last] = item
    return value


def _definition(schema: Path, name: str) -> Draft202012Validator:
    document = json.loads(schema.read_bytes())
    return Draft202012Validator({**document["$defs"][name], "$defs": document["$defs"]})


def test_new_run_is_todays_seeded_reset_and_wire_starts_match_both_contracts():
    start = EpisodeStart.decode(None, seed="seed")
    assert (start.kind, start.fresh_native_required, start.record()) == (
        "new_run", False, {"kind": "new_run", "seed": "seed"})
    loaded = EpisodeStart.decode(_save_start(), seed="seed")
    assert loaded.fresh_native_required  # U1 (in-process load) is not in this batch
    inference = _definition(ROOT / "python/schemas/rl-inference-3.schema.json", "start")
    native = _definition(ROOT / "contracts/managed-native/managed-native-compact-1.schema.json",
                         "start")
    for value in (start, loaded):
        inference.validate(value.inference_start())
        native.validate(value.native_start())
    inference.validate(loaded.inference_start("g-origin"))
    # Nothing seed-derived reaches the inference side (rl-inference-3 rule).
    assert "seed" not in json.dumps(loaded.inference_start("g-origin"))
    assert loaded.native_start() == {"kind": "from_save", "save_sha256": SAVE_SHA}
    assert loaded.record()["lineage"]["initial_progress_sha256"] == "progress"
    assert "path" not in loaded.record()


@pytest.mark.parametrize("value,code", [
    ({"schema": "other"}, "save_start_invalid"),
    ({**_save_start(), "extra": 1}, "save_start_invalid"),
    (_save_start(**{"save.schema": "other"}), "save_descriptor_invalid"),
    (_save_start(**{"save.sha256": "A" * 64}), "save_descriptor_invalid"),
    (_save_start(**{"save.bytes": 0}), "save_descriptor_invalid"),
    (_save_start(**{"save.path": "relative.save"}), "save_descriptor_invalid"),
    (_save_start(**{"save.act": 0}), "save_descriptor_invalid"),
    (_save_start(**{"save.candidate": {"artifact_sha256": "b" * 64}}),
     "save_descriptor_invalid"),
    (_save_start(**{"save.parent_game_continuity_id": KeyError}), "save_descriptor_invalid"),
    (_save_start(**{"save.save_boundary": "pre_room_rollback"}), "save_boundary_not_map"),
    (_save_start(**{"save.seed": "other"}), "save_start_seed_changed"),
    (_save_start(**{"lineage.initial_progress_sha256": KeyError}), "save_lineage_invalid"),
    (_save_start(**{"lineage.ascension": "0"}), "save_lineage_invalid"),
    (_save_start(**{"lineage.world_key": "x"}), "save_lineage_invalid"),
    (_save_start(**{"parent_identity": KeyError}), "save_start_invalid"),
    (_save_start(**{"parent_identity": None}), "save_parent_identity_invalid"),
    (_save_start(**{"parent_identity.adapter_runtime_instance_id": "runtime-other"}),
     "save_parent_identity_changed"),
    (_save_start(**{"parent_identity.episode_provenance.actual_seed": "other"}),
     "save_parent_identity_changed"),
    (_save_start(**{"parent_identity.episode_provenance.verdict": "provenance_incomplete"}),
     "save_parent_identity_changed"),
    # Lineage and parent record cross-checked: the configured digest is not trusted alone.
    (_save_start(**{"lineage.initial_progress_sha256": "other"}),
     "save_parent_progress_unproved"),
    # A parent that was itself loaded has no replayed proof: chained loads refused.
    (_save_start(**{"parent_identity.initial_progress": None}),
     "save_parent_progress_unproved"),
])
def test_bad_save_start_is_typed_before_any_process(value, code):
    with pytest.raises(BoundaryError) as raised:
        EpisodeStart.decode(value, seed="seed")
    assert raised.value.code == code


def test_episode_starts_are_decoded_once_with_their_progress_lineage():
    starts = decode_episode_starts({"seed": _save_start()}, initial_progress=PROGRESS)
    assert starts["seed"].kind == "from_save"
    assert decode_episode_starts({}, initial_progress=None) == {}
    for value, progress, code in (
            ([], PROGRESS, "invalid_episode_starts"),
            ({"": _save_start()}, PROGRESS, "invalid_episode_starts"),
            ({"other": _save_start()}, PROGRESS, "save_start_seed_changed"),
            ({"seed": _save_start()}, {**PROGRESS, "progress_sha256": "other"},
             "loaded_initial_progress_lineage_changed"),
            ({"seed": _save_start()}, None, "loaded_initial_progress_lineage_changed")):
        with pytest.raises(BoundaryError) as raised:
            decode_episode_starts(value, initial_progress=progress)
        assert raised.value.code == code


def _loaded_identity(**origin_changes):
    origin = {"kind": "map_continue_save", "save_sha256": SAVE_SHA,
              "candidate": dict(CANDIDATE), "act": 2, "floor": 23,
              "parent_runtime_instance_id": "runtime-parent",
              "parent_game_continuity_id": "game-parent", **origin_changes}
    return {"episode_provenance": {"origin": origin, "verdict": "provenance_pass",
                                   "requested_seed": "seed", "actual_seed": "seed"},
            "initial_progress": None}


@pytest.mark.parametrize("identity,progress,code", [
    (None, PROGRESS, "prepared_identity_invalid"),
    ({**_loaded_identity(), "episode_provenance": {
        **_loaded_identity()["episode_provenance"], "actual_seed": "other"}},
     PROGRESS, "prepared_seed_changed"),
    ({**_loaded_identity(), "episode_provenance": {
        **_loaded_identity()["episode_provenance"], "verdict": "provenance_incomplete"}},
     PROGRESS, "prepared_provenance_not_passed"),
    (_loaded_identity(kind="seed"), PROGRESS, "loaded_origin_changed"),
    (_loaded_identity(save_sha256="d" * 64), PROGRESS, "loaded_origin_changed"),
    (_loaded_identity(floor=22), PROGRESS, "loaded_origin_changed"),
    (_loaded_identity(parent_game_continuity_id="game-other"), PROGRESS,
     "loaded_origin_changed"),
    (_loaded_identity(candidate={**CANDIDATE, "artifact_sha256": "e" * 64}), PROGRESS,
     "loaded_origin_changed"),
    # The save was made under a different progress recipe than this run requires.
    (_loaded_identity(), {**PROGRESS, "progress_sha256": "other"},
     "loaded_initial_progress_lineage_changed"),
    # A loaded world never claims a different progress than its lineage.
    ({**_loaded_identity(), "initial_progress": {"progress_sha256": "other",
                                                 "run_unlock_state_matches": True}},
     PROGRESS, "loaded_initial_progress_changed"),
])
def test_loaded_identity_fails_closed_with_a_typed_code(identity, progress, code):
    start = EpisodeStart.decode(_save_start(), seed="seed")
    with pytest.raises(BoundaryError) as raised:
        require_loaded_identity(identity, start=start, initial_progress=progress)
    assert raised.value.code == code


def test_loaded_progress_comes_from_lineage_and_records_whether_native_replayed():
    start = EpisodeStart.decode(_save_start(), seed="seed")
    proof = require_loaded_identity(_loaded_identity(), start=start, initial_progress=PROGRESS)
    assert proof == {"source": "save_lineage", "progress_sha256": "progress",
                     "save_sha256": SAVE_SHA, "parent_intent_id": "intent-parent",
                     "native_replayed": False}
    replayed = {**_loaded_identity(), "initial_progress": {
        "progress_sha256": "progress", "run_unlock_state_matches": True}}
    assert require_loaded_identity(replayed, start=start,
                                   initial_progress=PROGRESS)["native_replayed"] is True
    with pytest.raises(BoundaryError, match="loaded_identity_requires_save_start"):
        require_loaded_identity(_loaded_identity(), start=EpisodeStart.new_run("seed"),
                                initial_progress=PROGRESS)


def test_fast_chain_first_frame_start_must_name_the_offered_start():
    loaded = EpisodeStart.decode(_save_start(), seed="seed")
    require_first_frame_start({"kind": "from_save", "save_sha256": SAVE_SHA}, start=loaded)
    require_first_frame_start({"kind": "new_run"}, start=EpisodeStart.new_run("seed"))
    for observed in ({"kind": "new_run"}, {"kind": "from_save", "save_sha256": "d" * 64}, None):
        with pytest.raises(BoundaryError, match="first_frame_start_changed"):
            require_first_frame_start(observed, start=loaded)


# Captured by running this fake against the pre-S4 executor (base 6c517cff).
_OLD_CHAIN_ROW_KEYS = {
    "cleanup_accepted", "detail_seconds", "detail_timing_schema", "elapsed_seconds",
    "episode_identity", "finish", "host_kind", "host_service_instance_id", "host_service_pid",
    "intent_id", "phase_seconds", "phase_timing_schema", "preservation_store",
    "read_concurrency", "reason", "reset", "seed", "selection_id", "settling_timeout_ms",
    "status", "world_mutation_offered"}


class _Stop(BoundaryError):
    pass


def _executor(tmp_path, monkeypatch, *, starts, identity, load=None, reply=None):
    """The real executor path through the identity gate; stops before the Runtime."""
    from spireagent.workbench import managed_evaluation as backend

    executor = object.__new__(backend.ManagedEpisodeExecutor)
    executor.adapter_environment_keys = ()
    executor.configuration = {"models": {"model": {"package_directory": str(tmp_path)}},
        "host_pin": {}, "initial_progress": dict(PROGRESS), "episode_starts": starts}
    executor.episode_starts = decode_episode_starts(starts, initial_progress=PROGRESS)
    executor.preparation = {"store": "fixture"}
    executor.read_concurrency = 1
    executor.settling_timeout_ms = None
    executor.measure_host_performance = False
    executor.host = executor.candidate = executor.runtime = tmp_path
    executor.episode_seconds = executor.cleanup_seconds = 10
    executor.build = {"artifact_sha256": "candidate"}
    executor.inference_pool = None
    ready = {"service_instance_id": "service", "adapter_runtime_instance_id": "runtime",
             "episode": {"game_continuity_id": None}}
    calls = []

    def reset(*args, **kwargs):
        calls.append(("reset", args, kwargs))
        return {}

    def admin(route, body):
        calls.append(("admin", route, copy.deepcopy(body)))
        if load is not None:
            raise load
        if reply is not None:
            return reply
        return {"result": {"type": "load_map_result", "game_continuity_id": "game-new",
                           "source": {"kind": "map_continue_save", "save": body["save"]}}}

    manager = SimpleNamespace(reset=reset, _admin=admin,
                              request=lambda _: {"result": {"identity": identity}},
                              close_host=lambda: {"result": {"exit": {"code": 0}}})
    launch = SimpleNamespace(pid=1, process=SimpleNamespace(wait=lambda **_: 0))

    def launched(**_):
        calls.append(("launch",))
        return launch

    executor.api = SimpleNamespace(
        launch_managed_host_service=launched,
        ManagedHostServiceClient=SimpleNamespace(from_attachment=lambda _: SimpleNamespace(
            ready=lambda: ready)),
        ManagedHostServiceManager=SimpleNamespace(from_attachment=lambda _: manager),
    )

    def stop_here(*_args, **_kwargs):
        raise _Stop("test", "stopped_after_identity_gate")

    monkeypatch.setattr(backend, "runtime_arguments", stop_here)
    monkeypatch.setattr("spireagent.resource_slot.require_managed_slot", lambda: None)
    stored = {}
    artifacts = SimpleNamespace(root=tmp_path,
                                put=lambda name, value: stored.setdefault(name, value) and name)
    row = {"selection_id": "model", "seed": "seed", "intent_id": "intent",
           "world_mutation_offered": False}
    executor(row, {"ascension": 0, "host": "Managed", "character": "Defect"},
             artifacts, lambda: None)
    return row, calls, stored


def test_executor_loads_the_save_instead_of_reset_and_records_lineage_progress(
        tmp_path, monkeypatch):
    row, calls, stored = _executor(tmp_path, monkeypatch, starts={"seed": _save_start()},
                                   identity=_loaded_identity())
    # Reached the next step (model/Runtime) only after the loaded-world gate passed.
    assert (row["status"], row["reason"]) == ("unknown", "stopped_after_identity_gate")
    assert [call[0] for call in calls] == ["launch", "admin"]
    _, route, body = calls[1]
    assert route == "/v1/admin/load-map"
    assert set(body) == {"save", "expected_runtime_instance_id", "expected_game_continuity_id"}
    assert body["save"] == _save_start()["save"] and body["expected_game_continuity_id"] is None
    assert list(stored)[:2] == ["managed-load-map.json", "episode-identity.json"]
    assert row["episode_start"]["kind"] == "from_save"
    assert row["initial_progress_proof"]["source"] == "save_lineage"
    assert row["world_mutation_offered"] is True


def test_executor_without_save_start_keeps_the_seeded_reset(tmp_path, monkeypatch):
    seeded = {"episode_provenance": {"origin": {"kind": "seed"}, "verdict": "provenance_pass",
                                     "actual_seed": "seed"},
              "initial_progress": {"progress_sha256": "progress",
                                   "run_unlock_state_matches": True}}
    row, calls, stored = _executor(tmp_path, monkeypatch, starts={}, identity=seeded)
    assert row["reason"] == "stopped_after_identity_gate"
    assert [call[0] for call in calls] == ["launch", "reset"]
    # Exactly today's reset call, argument for argument.
    assert calls[1][1:] == (("seed",), {"expected_runtime_instance_id": "runtime",
                                        "expected_game_continuity_id": None,
                                        "initial_progress": PROGRESS})
    assert list(stored)[:1] == ["managed-reset.json"]
    # Old-chain rows keep exactly the pre-S4 key set (captured from the base executor).
    assert set(row) == _OLD_CHAIN_ROW_KEYS


@pytest.mark.parametrize("start,code", [
    (_save_start(**{"lineage.ascension": 10}), "save_start_scenario_changed"),
    (_save_start(**{"lineage.character": "Ironclad"}), "save_start_scenario_changed"),
])
def test_bad_save_start_is_not_started_without_any_world_mutation(
        tmp_path, monkeypatch, start, code):
    row, calls, _ = _executor(tmp_path, monkeypatch, starts={"seed": start},
                              identity=_loaded_identity())
    assert (row["status"], row["reason"], row["world_mutation_offered"]) == (
        "not_started", code, False)
    # Refused before any Host process: nothing launched, so nothing to clean up.
    assert calls == [] and row["cleanup_accepted"] is True


def test_bad_episode_starts_fail_the_executor_before_any_game(tmp_path, monkeypatch):
    with pytest.raises(BoundaryError, match="save_start_seed_changed"):
        _executor(tmp_path, monkeypatch, starts={"seed": _save_start(**{"save.seed": "x"})},
                  identity=_loaded_identity())


@pytest.mark.parametrize("reply", [
    {"result": {"type": "reset_result"}},
    {"result": {"type": "load_map_result", "game_continuity_id": "g",
                "source": {"kind": "map_continue_save", "save": {"sha256": "d" * 64}}}},
    {"result": {"type": "load_map_result", "source": {"save": {"sha256": SAVE_SHA}}}},
])
def test_unconfirmed_load_reply_is_unknown_and_never_retried(tmp_path, monkeypatch, reply):
    row, calls, _ = _executor(tmp_path, monkeypatch, starts={"seed": _save_start()},
                              identity=_loaded_identity(), reply=reply)
    assert (row["status"], row["reason"]) == ("unknown", "managed_service_load_map_unconfirmed")
    assert [call[0] for call in calls] == ["launch", "admin"]


def test_named_load_map_prefers_a_consumer_method_and_refreshes_the_session():
    from spireagent.workbench.local_environment import (
        _ManagedServiceEnvironment,
        managed_load_map,
    )

    seen = []

    def named(**body):
        seen.append(body)
        return {"result": {"type": "load_map_result", "game_continuity_id": "g",
                           "source": {"save": body["save"]}}}

    save = _save_start()["save"]
    managed_load_map(SimpleNamespace(load_map=named), save=save,
                     expected_runtime_instance_id="r", expected_game_continuity_id=None)
    assert seen == [{"save": save, "expected_runtime_instance_id": "r",
                     "expected_game_continuity_id": None}]
    ready = {"adapter_runtime_instance_id": "r", "episode": {"game_continuity_id": None}}
    refreshed = []
    session = object.__new__(_ManagedServiceEnvironment)
    session._manager, session.ready = SimpleNamespace(load_map=named), ready
    session._client = SimpleNamespace(ready=lambda: refreshed.append(1) or ready)
    assert session.load_map(save)["game_continuity_id"] == "g" and refreshed == [1]


def test_lost_load_reply_is_unknown_and_never_retried(tmp_path, monkeypatch):
    lost = BoundaryError("managed_host", "managed_host_reply_unknown")
    row, calls, _ = _executor(tmp_path, monkeypatch, starts={"seed": _save_start()},
                              identity=_loaded_identity(), load=lost)
    assert (row["status"], row["reason"]) == ("unknown", "managed_host_reply_unknown")
    assert [call[0] for call in calls] == ["launch", "admin"]


def test_loaded_world_with_a_different_save_is_unknown_with_its_identity_kept(
        tmp_path, monkeypatch):
    row, _, stored = _executor(tmp_path, monkeypatch, starts={"seed": _save_start()},
                               identity=_loaded_identity(save_sha256="d" * 64))
    assert (row["status"], row["reason"]) == ("unknown", "loaded_origin_changed")
    assert "episode-identity.json" in stored and "initial_progress_proof" not in row
