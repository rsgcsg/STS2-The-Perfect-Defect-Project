"""RL5-METHODS A3: a three-arm exploration grid end to end, one arm with combat T < 1.

profile (ladder + sampling_temperature_min) -> arm per env driver (exploration_plan)
-> each arm's env driver config (real env_driver.mjs, fake fast env) -> that sampling at
episode open over the link (real NodeAggregator) -> real FastInferenceService with the
profile's temperature minimum -> real FastSegmentAssembler, segment format 3 -> decode
with the vocabulary's decision codes -> one prepared batch's exploration counts in a
learner progress event, the arms' closes in driver.jsonl -> the readout per arm. One game
opens from a save: its rows count under "<arm>+save" and its close is no arm result.
"""
from __future__ import annotations

import json
import os
import random
import tempfile
import threading
from typing import Any

import pytest

pytest.importorskip("torch")

import torch  # noqa: E402
from test_friday_fastchain import (  # noqa: E402
    PROFILE,
    AdmitAll,
    fake_driver_world,
    jsonl_text,
    readout,
    run_driver,
    run_profile,
    write_run,
)
from test_p1_fast_inference import (  # noqa: E402
    AGENT_SPEC,
    ENV,
    MU,
    FakeEnv,
    _aggregator,
    _decision_line,
)
from test_p1_input_v2 import public_state  # noqa: E402
from test_rl_fast_source import envelope_for  # noqa: E402

from spireagent.rl_contracts import HotPayloadRef, RunSequence  # noqa: E402
from stpd.p1.frame_compiler import FrameCompiler  # noqa: E402
from stpd.p1.inference_service import FastInferenceService  # noqa: E402
from stpd.p1.input import V2_INPUT_SPEC  # noqa: E402
from stpd.p1.model import MStd, MStdExecution  # noqa: E402
from stpd.p1.rows_scorer import RowsScorer  # noqa: E402
from stpd.p1.sampling import distribution, draw  # noqa: E402
from stpd.p1.vocabulary import public_vocabulary  # noqa: E402
from stpd.rl import batch_preparation as bp  # noqa: E402
from stpd.rl.fast_assembly import FastSegmentAssembler, MuHandoff  # noqa: E402
from stpd.rl.fast_segment import FORMAT_3  # noqa: E402
from stpd.rl.fast_source import decode_fast_packet  # noqa: E402
from stpd.rl.learning_diagnostics import (  # noqa: E402
    DECISIONS,
    decision_codes,
    exploration_fields,
    exploration_row,
)
from stpd.rl.prepared_batch import BatchMember, PrepareRequest  # noqa: E402

ARM = '{ share = %s, combat = { temperature = %s, uniform_mix = %s }, ' \
      'noncombat = { temperature = %s, uniform_mix = %s } }'
LADDER = ('exploration_ladder={ control = ' + ARM % (0.4, 1.0, 0.0, 1.0, 0.0)
          + ', bold = ' + ARM % (0.4, 0.7, 0.0, 2.0, 0.02)
          + ', hot = ' + ARM % (0.2, 1.2, 0.0, 1.5, 0.01) + ' }')
DRIVERS = 5
# One game per screen sequence: (kind, in combat) per step; the game's floors decide its act.
SCREENS = (("combat", True), ("map", False), ("combat", True), ("card_reward", False),
           ("combat", True), ("map", False))
CATALOGS = {"combat": ("combat.play_card", "combat.play_card", "combat.end_turn"),
            "map": ("map.travel",) * 3,
            "card_reward": ("reward.take_card", "reward.take_card", "reward.proceed")}
# The floor of each step: games that die in act 1, 2 and 3 (public acts pass every Boss).
GAME_FLOORS = ((1, 2, 3, 4, 5, 6), (1, 9, 17, 18, 21, 22), (1, 17, 18, 34, 40, 41))
# The last driver's act-3 game opens from a save (#553 review H): its rows count under
# "<arm>+save", apart from the arm's new runs, and its close is not an arm result.
SAVE_GAME = f"g{DRIVERS - 1}-{GAME_FLOORS[2][-1]}"


@pytest.fixture(scope="module")
def vocabulary() -> dict[str, Any]:
    return public_vocabulary(V2_INPUT_SPEC)


@pytest.fixture(scope="module")
def model(vocabulary: dict[str, Any]) -> MStd:
    torch.manual_seed(5)
    model = MStd(vocabulary, MStdExecution(
        implementation="experimental-packed-v1", microbatch_size=4,
        scene_attention_area=67108864, text_attention_area=67108864, cpu_threads=1))
    model.enable_rl4_heads(7)
    return model.eval().requires_grad_(False)


def observation(kind: str, step: int) -> dict[str, Any]:
    state = public_state()
    state["status"] = {"hp": step, "cards": [{"id": step, "x": [1]}]}
    state["catalog"] = [{"choice_id": f"c{j + 1}", "kind": action,
                         "subject": ("e1", "e2", None)[j % 3], "target": None,
                         "label": f"{kind} {j} {step}", "mapped": True}
                        for j, action in enumerate(CATALOGS[kind])]
    if kind == "combat":  # the aggregator binds the line's in_combat to this block
        state["combat"] = {"availability": "available", "content": {}}
    return state


def test_exploration_row_recovers_the_model_order_and_top_probability():
    """q is increasing in p at any T and mix; p comes back from q exactly enough to put
    the > 0.99 line where the model's own softmax puts it (T 0.7 included)."""
    for logits, sure in (([6.0, 0.0, -1.0], True), ([4.0, 0.0, -1.0], False)):
        p, _ = distribution(logits, 1.0, 0.0)
        assert (max(p) > 0.99) is sure
        for temperature, mix in ((1.0, 0.0), (0.7, 0.0), (2.0, 0.02), (1.5, 0.01)):
            _, q = distribution(logits, temperature, mix)
            assert [exploration_row(q, i, temperature, mix) for i in range(3)] == [
                (0, sure), (1, sure), (2, sure)]
    _, q = distribution([4.0, 0.0, -1.0], 0.7, 0.0)
    assert max(q) > 0.99  # the sampled q of a cold arm is sure where the model is not
    assert exploration_row([0.5, 0.5, 0.0], 1, 1.0, 0.0) == (0, False)  # a tie is top


def test_three_arm_grid_with_a_cold_combat_arm_end_to_end(vocabulary, model, tmp_path):
    values = run_profile.values(run_profile.load_profile(PROFILE, [
        LADDER, "sampling_temperature_min=0.7"]))
    ladder, low = values["exploration_ladder"], values["sampling_temperature_min"]
    assert low == 0.7 and ladder["bold"]["combat"]["temperature"] == 0.7
    # Shares: every prefix of the run's drivers holds each arm's share to within one.
    plan = run_profile.exploration_plan(ladder, 0, 10)
    for n in range(1, 11):
        for arm, spec in ladder.items():
            assert abs(plan[:n].count(arm) - spec["share"] * n) <= 1
    assert {arm: plan.count(arm) for arm in ladder} == {"control": 4, "bold": 4, "hot": 2}
    drivers = run_profile.exploration_plan(ladder, 0, DRIVERS)
    assert sorted(drivers) == ["bold", "bold", "control", "control", "hot"]

    # Each arm's env driver config: the arm's classes and the run's minimum.
    world, admit = fake_driver_world(tmp_path / "world"), AdmitAll()
    samplings = {}
    try:
        for arm in ladder:
            out = tmp_path / "drivers" / arm
            done = run_driver(world, out, admit.path, "--exploration-ladder",
                              json.dumps(ladder), "--arm", arm, "--replay-k", "1",
                              "--temperature-min", str(low))
            assert done.returncode == 0, done.stderr
            inference = json.loads((out / "config.json").read_text())["inference"]
            assert inference["temperature_min"] == 0.7
            samplings[arm] = inference["sampling"]
            assert samplings[arm]["arm_id"] == arm and samplings[arm]["classes"] == {
                name: ladder[arm][name] for name in ("combat", "noncombat")}
    finally:
        admit.close()

    compiler = FrameCompiler(vocabulary)
    sealed: list[Any] = []
    assembler = FastSegmentAssembler(
        assembler_id="node-a", compiler_identity=compiler.identity, emit=sealed.append,
        segment_format=FORMAT_3)
    service = FastInferenceService(
        vocabulary, compiler.identity, assembler, training_generation="gen-1",
        address=("127.0.0.1", 0), env_sha256=ENV, prepare_workers=1, temperature_min=low)
    assert service.publish(model, MuHandoff(MU, "gen-1", 1, 1))
    path = os.path.join(tempfile.mkdtemp(), "link.sock")
    aggregator = _aggregator(vocabulary, service, path)
    scorer = RowsScorer(model, bf16=False)
    games: dict[str, dict[str, Any]] = {}  # game -> arm, steps (kind, floor, reply), floor
    try:
        row_id = 0
        for index, arm in enumerate(drivers):
            env = FakeEnv(path)
            env.send({"type": "hello", "env_process_id": f"env-{index}", "slots": 2,
                      "p1_revision": "x", "input_spec": AGENT_SPEC})
            for floors in GAME_FLOORS[index % 3:index % 3 + 2]:
                game = f"g{index}-{floors[-1]}"
                save = game == SAVE_GAME
                env.send({"type": "episode_open", "ascension": 0, "game_instance_id": game,
                          "actor_id": f"actor-{index}", "sampling": samplings[arm],
                          "single_choice": "score",
                          "start": ({"kind": "from_save", "act": 1, "floor": 1} if save
                                    else {"kind": "new_run"})})
                steps = []
                for step, (kind, combat) in enumerate(SCREENS):
                    row_id += 1
                    floor = floors[step]
                    env.socket.sendall(_decision_line(
                        row_id, game, step, observation(kind, step), in_combat=combat,
                        floor=floor))
                    reply = env.reply()
                    assert reply["type"] == "scored", reply
                    steps.append((kind, combat, floor, reply))
                    last = step == len(SCREENS) - 1
                    env.send({"type": "step_outcome", "row_id": row_id,
                              "game_instance_id": game, "step_index": step,
                              "status": "completed", "receipt_sha256": "b" * 64,
                              "public_facts": {"act": 1 + (floor > 17) + (floor > 33),
                                               "floor": floor, "hp": 50, "max_hp": 70},
                              "terminal": {"result": "defeat"} if last else None})
                env.send({"type": "episode_close", "game_instance_id": game,
                          "reason": "terminal", "last_step_index": len(SCREENS) - 1})
                games[game] = {"arm": arm, "steps": steps, "floor": floor, "save": save,
                               "dose": arm + "+save" if save else arm}
        for _ in range(400):
            if len(sealed) == len(games):
                break
            threading.Event().wait(0.05)
    finally:
        stats = aggregator.close()
        report = service.close()
    assert stats["failure"] is None and report["refused"] == {}
    assert report["episodes_refused"] == 0 and report["rows"] == len(games) * len(SCREENS)
    # every game a natural terminal, labelled from its public facts
    assert [s.header["seal"]["truncation"] for s in sealed] == [None] * len(games)
    assert all(s.kind == "terminal" for s in sealed)

    # Sampling: each reply is the arm's exact distribution and draw (combat T 0.7 too).
    for game, record in games.items():
        sampling = samplings[record["arm"]]
        for step, (kind, combat, floor, reply) in enumerate(record["steps"]):
            policy = sampling["classes"]["combat" if combat else "noncombat"]
            frame = compiler.compile(observation(kind, step))
            scores = scorer.score([frame], [floor])[0].scores
            _, q = distribution(scores, policy["temperature"], policy["uniform_mix"])
            assert reply["applied"] == {"arm_id": record["arm"], **policy,
                                        "decision_class": "combat" if combat else "noncombat"}
            assert reply["behavior_q"] == pytest.approx(q, abs=1e-6)
            assert reply["selected_index"] == draw(reply["behavior_q"], sampling["seed"],
                                                   f"c-{game}-{step}")
            record.setdefault("p", []).append(distribution(scores, 1.0, 0.0)[0])

    # Decode: the exploration counts are metadata only; every training number is the same.
    codes = decision_codes(vocabulary["actions"])
    sources = []
    for segment in sealed:
        game = segment.header["game_instance_id"]
        assert segment.header["format"] == FORMAT_3
        envelope = envelope_for(segment, game)
        source, _ = decode_fast_packet(segment.data, envelope=envelope, compiler=compiler,
                                       decisions=codes)
        plain, _ = decode_fast_packet(segment.data, envelope=envelope, compiler=compiler)
        for name in ("actions", "policy_probabilities", "floors", "row_refs"):
            assert getattr(source, name) == getattr(plain, name), name
        for name in ("probabilities", "targets", "raw_advantages"):
            assert torch.equal(getattr(source, name), getattr(plain, name)), name
        assert [{k: v for k, v in m.items() if k != "exploration"} for m in source.metadata] \
            == list(plain.metadata)
        # One count table per source, on its first row only (not a tuple per row).
        assert all("exploration" not in m for m in source.metadata[1:])
        record, expected = games[game], {}
        for row, (kind, combat, floor, reply) in enumerate(record["steps"]):
            chosen = reply["behavior_q"][reply["selected_index"]]
            rank = min(sum(value > chosen for value in reply["behavior_q"]), 2)
            act = 1 if floor <= 17 else 2 if floor <= 33 else 3
            counts = expected.setdefault(
                f"{record['dose']}|act{act}|{DECISIONS[0 if combat else DECISIONS.index(kind)]}",
                [0, 0, 0, 0, 0])
            counts[0] += 1
            counts[1] += rank == 1
            counts[2] += rank == 2
            counts[3] += max(record["p"][row]) > 0.99
            counts[4] += rank > 0 and bool(source.raw_advantages[row] > 0)
            record.setdefault("ranks", []).append((act, rank))
        assert source.metadata[0]["exploration"] == expected
        sources.append(source)

    # The learner's dataset: assembled metadata -> the progress event's counts.
    count = sum(len(s.frames) for s in sources)
    request = PrepareRequest(
        RunSequence("run-1", "gen-1", 1),
        tuple(BatchMember(s.segment_id, HotPayloadRef(f"run-1/gen-1/{i}", "f" * 64, 1),
                          s.run_id, len(s.frames), MU, f"intent-{s.run_id}")
              for i, s in enumerate(sources)),
        (), (tuple(range(count)),), (), compiler.identity, random.Random(1).getstate(),
        128 * 1024**2)
    batch = bp.assemble(request, tuple(sources), microbatch_size=16, deduplicate_text=True,
                        prepared_layout="rows-v2")
    counts = exploration_fields(batch.metadata)
    assert sum(c[0] for c in counts.values()) == count
    doses = {"control", "bold", "hot", f"{drivers[-1]}+save"}
    assert {key.split("|")[0] for key in counts} == doses

    run = tmp_path / "run-t1"
    write_run(run, cards=1)
    events = run / "learner-events.jsonl"
    events.write_text(events.read_text() + jsonl_text([{
        "t": 2299.5, "kind": "progress", "stepped": True, "rows": count,
        "seconds": 1.0, "exploration": counts}]))
    lines = []
    for index, (game, record) in enumerate(games.items()):
        lines += [{"event": "admission", "game_ref": game, "ok": True, "code": None},
                  {"event": "closed", "t": 10_000 + index, "game_ref": game,
                   "reason": "terminal", "arm": record["arm"],
                   "start": "from_save" if record["save"] else "new_run",
                   "floor": record["floor"], "replay_k": 1, "group": game,
                   "ascension": 0, "terminal": "defeat"}]
    driver = run / "env-n0" / "p0"
    driver.mkdir(parents=True)
    (driver / "driver.jsonl").write_text(jsonl_text(lines))

    record = readout.build(run, 1200)
    dose = record["learning"]["latest"]["exploration"]
    assert set(dose) == doses
    for arm, part in dose.items():
        rows = [x for r in games.values() if r["dose"] == arm for x in r["ranks"]]
        assert part["all"]["rows"] == len(rows)
        assert part["all"]["top"] == pytest.approx(sum(x[1] == 0 for x in rows) / len(rows))
        assert set(part["by_decision"]) == {"combat", "map", "card_reward"}
        assert set(part["by_act"]) == {f"act{a}" for a in {x[0] for x in rows}}
    played = record["training_games"]["arms"]
    assert set(played) == {"control", "bold", "hot"}
    for arm, results in played.items():
        floors = [r["floor"] for r in games.values() if r["arm"] == arm and not r["save"]]
        run_all = results["run"]["all"]
        assert run_all["games"] == len(floors) == results["rolling"]["all"]["games"]
        assert run_all["mean_floor"] == pytest.approx(sum(floors) / len(floors))
        assert run_all["act3_reached_rate"] == sum(f > 33 for f in floors) / len(floors)
        assert run_all["win_rate"] == 0
    assert record["training_games"]["run"] == played["control"]["run"]
    text = readout.render(record)
    for arm in doses:
        assert f"exploration arm {arm}:" in text
    assert "training games arm bold A0" in text and "act 3 reached" in text


def test_the_default_single_control_arm_is_today(tmp_path):
    """The default recipe: one control arm at T 1, minimum 1. Every driver is control,
    the env driver's config has no temperature_min (the bytes a pre-A3 driver wrote),
    and the service default refuses T < 1 exactly as before."""
    from stpd.p1.inference_service import check_sampling

    values = run_profile.values(run_profile.load_profile(PROFILE))
    ladder = values["exploration_ladder"]
    assert values["sampling_temperature_min"] == 1.0
    assert set(ladder) == {"control"} and all(
        ladder["control"][name] == {"temperature": 1.0, "uniform_mix": 0.0}
        for name in ("combat", "noncombat"))
    assert run_profile.exploration_plan(ladder, 0, 12) == ["control"] * 12
    world, admit = fake_driver_world(tmp_path), AdmitAll()
    try:
        configs = []
        for name, flags in (("before", ()), ("today", ("--temperature-min", "1.0"))):
            done = run_driver(world, tmp_path / name, admit.path, "--exploration-ladder",
                              json.dumps(ladder), "--arm", "control", "--replay-k", "1",
                              *flags)
            assert done.returncode == 0, done.stderr
            configs.append((tmp_path / name / "config.json").read_text().replace(
                str(tmp_path / name), "OUT"))
    finally:
        admit.close()
    assert configs[0] == configs[1]
    assert "temperature_min" not in json.loads(configs[1])["inference"]
    cold = {"mode": "temperature_uniform", "seed": "s", "arm_id": "a",
            "classes": {"combat": {"temperature": 0.99, "uniform_mix": 0.0},
                        "noncombat": {"temperature": 1.0, "uniform_mix": 0.0}}}
    assert check_sampling(cold) == "sampling_temperature"
