"""RL5 A1: a chain from a randomly initialised start package (stpd.rl.random_start)."""
from __future__ import annotations

import dataclasses as dc
import json
import math
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from rl_actor_test_runtime import isolated_actor_runtime as isolated_actor_runtime
from test_p1_input_v2 import public_state
from test_rl41_w1 import SOURCE_PRODUCER, assert_tree, configuration
from test_rl_segment_learner import read_cp

from stpd.p1.data import json_bytes
from stpd.p1.execution import MStdExecution
from stpd.p1.export import action_weights, export_model, load_model
from stpd.p1.frame_compiler import FrameCompiler
from stpd.p1.model import ARCHITECTURE, MStd
from stpd.p1.vocabulary import public_vocabulary
from stpd.rl.random_start import SCHEMA, main, random_start_package


def template(tmp_path: Path) -> Path:
    """A trained-start stand-in with a non-default execution, as production packages have."""
    execution = MStdExecution(implementation="experimental-packed-v1", microbatch_size=4,
                              scene_attention_area=67108864, text_attention_area=67108864,
                              cpu_threads=1)
    source = tmp_path / "template-source"
    source.write_bytes(b"engineering fixture")
    export_model(MStd(public_vocabulary(), execution).eval(), source, tmp_path / "template", {})
    return tmp_path / "template"


def weights(package: Path) -> dict[str, torch.Tensor]:
    descriptor = json.loads((package / "model.json").read_bytes())
    return load_model(package / "model.tensor-tree", descriptor["package_sha256"])[0].state_dict()


def test_random_start_copies_the_template_structure_and_binds_seed_and_template(
        tmp_path, capsys):
    shape = template(tmp_path)
    before = torch.get_rng_state()
    descriptor = random_start_package(shape, 7, tmp_path / "seed7")
    assert torch.equal(torch.get_rng_state(), before)  # the caller's RNG is untouched
    package = tmp_path / "seed7" / "package"
    model, value = load_model(package / "model.tensor-tree", descriptor["package_sha256"])
    old, old_value = load_model(shape / "model.tensor-tree",
                                json.loads((shape / "model.json").read_bytes())["package_sha256"])
    # same structure: vocabulary, execution, architecture (q3-m-std-1), parameter set
    assert value["vocabulary"] == old_value["vocabulary"]
    assert model.execution == old.execution and model.execution != MStdExecution()
    assert value["architecture"] == ARCHITECTURE and ARCHITECTURE["id"] == "q3-m-std-1"
    assert {k: v.shape for k, v in model.state_dict().items()} == {
        k: v.shape for k, v in old.state_dict().items()}
    assert any(not torch.equal(v, old.state_dict()[k]) for k, v in model.state_dict().items())
    identity = {"schema": SCHEMA, "seed": 7, "torch": str(torch.__version__),
                "template_package_sha256": json.loads(
                    (shape / "model.json").read_bytes())["package_sha256"]}
    # the seed and the template are inside the hashed tree, not only in model.json
    assert descriptor["training_identity"] == value["training_identity"] == identity
    assert (tmp_path / "seed7" / "init.json").read_bytes() == json_bytes(identity)
    # the CLI: same seed -> the same package; another seed -> another model
    assert main(["--template", str(shape), "--seed", "7", "--out", str(tmp_path / "again")]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["package_sha256"] == descriptor["package_sha256"]
    assert printed["start_package"] == str(tmp_path / "again" / "package")
    other = random_start_package(shape, 8, tmp_path / "seed8")
    assert other["package_sha256"] != descriptor["package_sha256"]
    with pytest.raises(FileExistsError):
        random_start_package(shape, 7, tmp_path / "seed7")


def test_random_start_heads_keep_their_zero_init_and_the_policy_starts_near_uniform(tmp_path):
    """What the learner builds from a random package without fitted heads (`_start_model`
    with no warmup artifact): MStd's own init for the trunk and policy head; the RL4
    outcome/HP heads keep their zero final layers (uniform outcome, HP 0)."""
    descriptor = random_start_package(template(tmp_path), 3, tmp_path / "random")
    model, _ = load_model(tmp_path / "random/package/model.tensor-tree",
                          descriptor["package_sha256"])
    model.enable_rl4_heads(0)
    model.eval()
    outcome, hp = model.rl4_heads.outcome.mlp[-1], model.rl4_heads.hp.mlp[-1]
    assert not outcome.weight.any() and not hp.weight.any() and not hp.bias.any()
    assert torch.equal(outcome.bias, torch.ones(51).add(1e-3).log())  # log-frequency, uniform
    observation = public_state()
    observation["run"] = {"content": {"floor": 3}}
    observation["combat"] = {"availability": "available"}
    catalog = observation["catalog"]
    observation["catalog"] = [{**catalog[i % 2], "choice_id": f"c{i}"} for i in range(8)]
    with torch.no_grad():
        scores, logits, values = model.forward_rl4(
            [FrameCompiler(model.vocabulary).compile(observation)], [3])
    policy = torch.softmax(scores[0], 0)
    assert float(-(policy * policy.log()).sum()) > 0.98 * math.log(8)  # measured 0.995+
    valid = torch.log_softmax(logits[0][torch.isfinite(logits[0])], 0)
    assert torch.allclose(valid, torch.full_like(valid, -math.log(valid.numel())))
    assert values.tolist() == [0.0]


def test_an_existing_start_with_its_sha_pinned_learns_from_the_same_state(tmp_path, monkeypatch):
    """The fast-chain launcher now passes every start package's package_sha256 as
    start_model_sha256 (learner_config.build). For an existing start the learner loads the
    same model: every learning-state field is equal; the CP config records the sha, and a
    package that does not match it is refused."""
    from stpd.rl.segment_learner import SegmentLearner

    cps = {}
    for pinned in (False, True):
        path = tmp_path / ("pinned" if pinned else "today")
        path.mkdir()
        torch.manual_seed(20261115)  # the fixture's start package draws from global torch RNG
        config, io = configuration(path, monkeypatch)
        sha = json.loads((Path(config.start_package) / "model.json").read_bytes())[
            "package_sha256"]
        owner = SegmentLearner(dc.replace(config, start_model_sha256=sha if pinned else None),
                               path / "owner", io)
        try:
            owner.run(initialize_only=True)
            cps[pinned] = read_cp(owner)[1]
        finally:
            owner.close()
    today, pinned_cp = cps[False], cps[True]
    for field in ("weights", "optimizer", "optimizer_parameter_names", "ema"):
        assert_tree(getattr(today, field), getattr(pinned_cp, field))
    assert_tree(dc.asdict(today.rng), dc.asdict(pinned_cp.rng))
    assert today.counters == pinned_cp.counters and today.construction == pinned_cp.construction
    paths = ("peer", "start_package", "start_model_sha256")
    assert {k: v for k, v in today.config.items() if k not in paths} == {
        k: v for k, v in pinned_cp.config.items() if k not in paths}
    assert today.config["start_model_sha256"] is None
    assert pinned_cp.config["start_model_sha256"] == sha
    with pytest.raises(ValueError, match="checksum mismatch"):
        SegmentLearner(dc.replace(config, start_model_sha256="0" * 64), tmp_path / "wrong", io)


@pytest.mark.skipif(os.name != "posix", reason="real spawned SGD process")
def test_a_chain_from_a_random_start_reaches_its_first_update(tmp_path, monkeypatch):
    """The learner, its inference pool and SGD child, the CP lineage and the evaluation
    export, exactly as for any start; only the start package differs."""
    import test_rl_sgd_process as split

    def random_configuration(path, patch, **kwargs):
        config, io = configuration(path, patch, producer=SOURCE_PRODUCER, **kwargs)
        descriptor = random_start_package(Path(config.start_package), 11, path / "random")
        return dc.replace(config, start_package=str(path / "random" / "package"),
                          start_model_sha256=descriptor["package_sha256"]), io

    monkeypatch.setattr(split, "configuration", random_configuration)
    owner, _, _ = split.terminal_owner(tmp_path / "chain", monkeypatch, episodes=2)
    package = tmp_path / "chain" / "random" / "package"
    sha = json.loads((package / "model.json").read_bytes())["package_sha256"]
    try:
        start = owner._learning_state()  # theta lives in the SGD child
        assert_tree(action_weights(start.weights), weights(package))
        split.train(owner, 1)
        assert owner.updates >= 1
        owner.checkpoint(final=True)
        record, cp = read_cp(owner)
    finally:
        owner.close()
    assert cp.config["start_package"] == str(package)
    assert cp.config["start_model_sha256"] == sha
    assert cp.counters.updates == owner.updates
    from stpd.evaluation.rl_checkpoint import export_checkpoint

    binding = export_checkpoint(owner.exchange.store, record["checkpoint_artifact_id"],
                                tmp_path / "evaluation")
    evaluated = weights(Path(binding["package_directory"]))
    assert_tree(evaluated, action_weights(cp.weights))
    assert any(not torch.equal(v, weights(package)[k]) for k, v in evaluated.items())


def test_the_launcher_starts_a_random_package_and_records_it(tmp_path, monkeypatch):
    """Fast-chain login side with site start_package = a random package and warmup_file = "":
    the facts name the start, preflight passes, the learner config pins its sha and loads no
    fitted heads, the run record carries seed and sha. A random start that keeps the trained
    start's warmup artifact is refused before submit (the learner would refuse the heads)."""
    import test_friday_fastchain as fc

    shape = template(tmp_path)
    descriptor = random_start_package(shape, 5, tmp_path / "random")
    package, sha = tmp_path / "random" / "package", descriptor["package_sha256"]
    plan = fc.plan_for(cards=3, wall_seconds=2400)
    plan["site"].update(start_package=str(package), warmup_file="")
    monkeypatch.setattr(fc.launch, "test_start", lambda _cmd: None)  # no Slurm here
    monkeypatch.setattr(fc.launch, "queue_facts", lambda _partitions: {})
    facts = fc.launch.collect_facts(plan, {"gpu": ["sbatch", "gpu.sh"]})
    assert facts["warmup_artifact"] == {"id": None, "present": False, "none": True}
    assert facts["start_model"] == {"package_sha256": sha,
                                    "training_identity": descriptor["training_identity"]}
    assert fc.preflight.RANDOM_START == SCHEMA
    # the cluster facts this machine cannot have come from the consistent-plan fixture
    login = {**fc.good_facts(plan), "warmup_artifact": facts["warmup_artifact"],
             "start_model": facts["start_model"]}
    results = fc.preflight.checks(plan, login)
    assert all(r.ok for r in results), fc.preflight.report(results)
    assert f"random-init start {sha[:12]} seed 5" in fc.by_name(results)["warmup_artifact"].detail
    trained_heads = {**login, "warmup_artifact": {"id": "f" * 64, "present": True}}
    refused = fc.by_name(fc.preflight.checks(plan, trained_heads))["warmup_artifact"]
    assert not refused.ok and 'warmup_file = ""' in refused.detail
    # the learner config as roles.py builds it
    run = {"run_name": "r", "peer_dir": "/p", "start_package": str(package),
           "start_model_sha256": sha, "wall_seconds": 60, "device": "cuda", "generation": "g",
           "warmup_artifact_id": None, "rendezvous_path": "/r", "ranks": 1,
           "resume_checkpoint": None}
    built = fc.learner_config.build(fc.gate_base(), fc.knobs(), run)
    assert built["start_package"] == str(package) and built["start_model_sha256"] == sha
    assert built["segments"]["warmup_artifact_id"] is None
    assert fc.learner_config.recipe_drift(built, fc.FROZEN) == []
    from stpd.rl.learner import LearnerConfig

    (tmp_path / "learner.json").write_text(json.dumps(built))  # roles.py writes, then reads
    read = LearnerConfig.read(tmp_path / "learner.json")
    assert read.start_model_sha256 == sha and read.segments.warmup_artifact_id is None
    # the run record (readout): the sha the learner was given (learner.json), the seed from
    # the facts.json launch.py writes at freeze
    record = tmp_path / "run"
    (record / "learner").mkdir(parents=True)
    (record / "learner" / "learner.json").write_text(json.dumps(built))
    (record / "facts.json").write_text(json.dumps({"start_model": facts["start_model"]}))
    identity = fc.readout.identity_edge(record, plan, record / "plan.json")
    assert identity["start_model"] == {"package_sha256": sha,
                                       "training_identity": descriptor["training_identity"],
                                       "site_package_at_submit": sha}
    assert f"start model: {sha[:12]}, schema {SCHEMA}, seed 5" in fc.readout.render_record(
        {"identity": identity})
    # the site package replaced between submit and GPU start: the learner's sha stands, the
    # submit-time identity is not attached to it, the difference is written out
    other = random_start_package(shape, 6, tmp_path / "other")
    (record / "facts.json").write_text(json.dumps({"start_model": {
        "package_sha256": other["package_sha256"],
        "training_identity": other["training_identity"]}}))
    identity = fc.readout.identity_edge(record, plan, record / "plan.json")
    assert identity["start_model"] == {"package_sha256": sha, "training_identity": None,
                                       "site_package_at_submit": other["package_sha256"]}
    assert (f"start model: {sha[:12]}; site start_package at submit was "
            f"{other['package_sha256'][:12]}") in fc.readout.render_record({"identity": identity})


def test_a_resume_takes_its_start_from_the_checkpoint_not_the_site_package(tmp_path,
                                                                          monkeypatch):
    """roles.py on a resume: the learner restores the CP and never loads the start package,
    so learner_config.start_fields takes the start from the restored CP's config (read as
    roles.py reads it). (a) A resume (auto-resume, continuation chain) runs after the site's
    package was removed; (b) another package exported to the same path is not recorded: the
    resumed generation's CP keeps the sha the first generation verified."""
    import test_friday_fastchain as fc

    from spireagent.rl_exchange import Exchange
    from stpd.rl.segment_learner import SegmentLearner

    config, io = configuration(tmp_path, monkeypatch)
    site = str(tmp_path / "site" / "package")
    first = random_start_package(Path(config.start_package), 21, tmp_path / "site")
    fresh = fc.learner_config.start_fields(site, None)
    assert fresh == {"start_package": site, "start_model_sha256": first["package_sha256"]}
    owner = SegmentLearner(dc.replace(config, **fresh), tmp_path / "first", io)
    try:
        owner.run(initialize_only=True)
        resume = read_cp(owner)[0]["checkpoint_artifact_id"]
    finally:
        owner.close()

    def restored_config():  # roles.py's reader
        reader = SimpleNamespace(exchange=Exchange(config.peer, config.run))
        return SegmentLearner._read_checkpoint(reader, resume).config

    shutil.rmtree(tmp_path / "site")  # (a)
    resumed = fc.learner_config.start_fields(site, restored_config())
    assert resumed == fresh
    second = SegmentLearner(dc.replace(
        config, **resumed, resume_checkpoint=resume,
        segments=dc.replace(config.segments, training_generation="g-resumed")),
        tmp_path / "second", io)
    try:
        second.checkpoint(final=True)
        cp = read_cp(second)[1]
    finally:
        second.close()
    assert {key: cp.config[key] for key in fresh} == fresh
    replaced = random_start_package(Path(config.start_package), 22, tmp_path / "site")  # (b)
    assert replaced["package_sha256"] != first["package_sha256"]
    assert fc.learner_config.start_fields(site, None)["start_model_sha256"] == replaced[
        "package_sha256"]  # what reading the site package on a resume would have recorded
    assert fc.learner_config.start_fields(site, restored_config()) == fresh
    assert fc.learner_config.start_fields(site, cp.config) == fresh  # and every later one


def warmup_artifact(peer: Path, package_sha256: str, schema: str | None = None,
                    heads: dict | None = None, report: dict | None = None) -> str:
    """A fitted-heads artifact in a local peer store, as the learner reads it (_start_model)."""
    from spireagent.artifact_contracts import Manifest
    from spireagent.storage.local import LocalBlobStore
    from spireagent.storage.store import ManifestArtifactStore
    from stpd.rl.checkpoint_codec import encode_checkpoint
    from stpd.rl.warmup import HEAD_WARMUP_SCHEMA

    store = ManifestArtifactStore(LocalBlobStore(peer))
    raw = encode_checkpoint({"schema": schema or HEAD_WARMUP_SCHEMA,
                             "model_package_sha256": package_sha256,
                             "feature_manifest_sha256": "c" * 64,
                             "heads": heads or {"w": torch.ones(3)},
                             "report": report or {"epochs": 1}})
    payload = store.put_bytes("checkpoint", raw, "application/octet-stream")
    return store.publish(Manifest("checkpoint", SOURCE_PRODUCER, payloads=(payload,)))


def test_an_existing_start_keeps_its_launcher_facts_and_warmup_check(tmp_path, monkeypatch):
    """A trained start (a CP export with its fitted-heads artifact): the warmup check reads
    the package the heads were fitted on from the peer store and compares it with the start
    package, as the learner does; the start's binding goes to the run record; old callers of
    build pin nothing."""
    import test_friday_fastchain as fc

    package = tmp_path / "start-package"
    binding = {"checkpoint_artifact_id": "a" * 64, "weights": "raw", "updates": 7}
    source = tmp_path / "raw-theta"
    source.write_bytes(b"engineering fixture")
    descriptor = export_model(MStd(public_vocabulary()).eval(), source, package, binding)
    sha = descriptor["package_sha256"]
    peer = tmp_path / "peer"
    heads = warmup_artifact(peer, sha)
    (tmp_path / "warmup.id").write_text(heads + "\n")
    plan = fc.plan_for(cards=3, wall_seconds=2400)
    plan["site"].update(start_package=str(package), warmup_file=str(tmp_path / "warmup.id"),
                        peer_dir=str(peer))
    monkeypatch.setattr(fc.launch, "test_start", lambda _cmd: None)
    monkeypatch.setattr(fc.launch, "queue_facts", lambda _partitions: {})
    facts = fc.launch.collect_facts(plan, {"gpu": ["sbatch", "gpu.sh"]})
    assert facts["warmup_artifact"] == {"id": heads, "present": True, "package_sha256": sha}
    assert facts["start_model"] == {"package_sha256": sha, "training_identity": binding}
    results = fc.by_name(fc.preflight.checks(plan, {**fc.good_facts(plan), **facts}))
    assert results["warmup_artifact"].ok
    assert results["warmup_artifact"].detail == (f"{heads[:12]} in peer_dir, fitted on the "
                                                 f"start package {sha[:12]}")
    # heads fitted on another package: refused before submit, both identities named
    other = warmup_artifact(peer, "d" * 64, heads={"w": torch.zeros(3)})
    (tmp_path / "warmup.id").write_text(other + "\n")
    facts = fc.launch.collect_facts(plan, {"gpu": ["sbatch", "gpu.sh"]})
    refused = fc.by_name(fc.preflight.checks(plan, {**fc.good_facts(plan), **facts}))
    detail = refused["warmup_artifact"].detail
    assert not refused["warmup_artifact"].ok and "d" * 64 in detail and sha in detail
    # a resume restores the heads from its CP: the start package is not compared
    resumed = {**plan, "resume_checkpoint": "cp-1"}
    assert fc.by_name(fc.preflight.checks(resumed, {**fc.good_facts(plan), **facts}))[
        "warmup_artifact"].ok
    # an artifact that is not fitted heads (or cannot be read) is refused, never passed
    wrong = warmup_artifact(peer, sha, schema="stpd/other-1")
    (tmp_path / "warmup.id").write_text(wrong + "\n")
    facts = fc.launch.collect_facts(plan, {"gpu": ["sbatch", "gpu.sh"]})
    assert "stpd/other-1" in facts["warmup_artifact"]["package_error"]
    unread = fc.by_name(fc.preflight.checks(plan, {**fc.good_facts(plan), **facts}))
    assert not unread["warmup_artifact"].ok and "cannot read" in unread["warmup_artifact"].detail
    resumed_unread = fc.by_name(fc.preflight.checks(resumed, {**fc.good_facts(plan), **facts}))
    assert resumed_unread["warmup_artifact"].ok  # a resume never reads the heads: not refused
    # a header longer than one 8 MiB store chunk is read across chunks
    from spireagent.storage.store import CHUNK_BYTES

    long = warmup_artifact(peer, sha, report={"note": "x" * (CHUNK_BYTES + 1024)})
    (tmp_path / "warmup.id").write_text(long + "\n")
    payload = json.loads((peer / "manifests" / f"{long}.json").read_text())["payloads"][0]
    index = json.loads((peer / "payload-indexes" / "v1" / f"{payload['sha256']}.json").read_text())
    assert len(index["chunks"]) > 1
    assert fc.launch.warmup_package(peer, long) == sha
    from stpd.rl.checkpoint_codec import MAGIC
    from stpd.rl.warmup import HEAD_WARMUP_SCHEMA

    assert fc.launch.CHECKPOINT_MAGIC == MAGIC
    assert fc.launch.WARMUP_SCHEMA == HEAD_WARMUP_SCHEMA
    run = {"run_name": "r", "peer_dir": "/p", "start_package": "/s", "wall_seconds": 60,
           "device": "cuda", "generation": "g", "warmup_artifact_id": "w",
           "rendezvous_path": "/r", "ranks": 1, "resume_checkpoint": None}
    assert fc.learner_config.build(fc.gate_base(), fc.knobs(), run)["start_model_sha256"] is None
