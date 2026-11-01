#!/usr/bin/env python3
"""One preregistered CPU-only residual critic candidate on frozen public features.

Freeze a TRAIN manifest before extraction. The fixed holdout is consumed only by
`evaluate`, after an internally selected candidate has been written permanently.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import multiprocessing
import random
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any


def save(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def split(seed: str) -> str:
    h = hashlib.sha256(("rl3-MLP-offline-internal-v1:" + seed).encode()).hexdigest()
    return "dev" if int(h[:8], 16) % 5 == 0 else "train"


def freeze(root: Path, point_root: Path, observer: Path) -> None:
    from stpd.evaluation.suites import seed_membership
    from stpd.rl.critic import held_out

    root.mkdir(mode=0o700)
    point = point_root / "point.json"
    seed_path = point_root / "fixed-seeds.json"
    excluded = set(json.loads(seed_path.read_bytes())["seeds"])
    cut = time.time()
    rows = [r for path in sorted(observer.glob("*.json"))
            for r in json.loads(path.read_bytes())["rows"] if r["uploaded_at"] <= cut]
    eligible = [r for r in rows if r["recorded_status"] == "saved"
                and r["outcome"] in ("win", "loss") and not held_out(r["seed"])
                and not seed_membership(r["seed"]) and r["seed"] not in excluded]
    ordered = sorted(eligible, key=lambda r: (r["uploaded_at"], r["run_id"]), reverse=True)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in ordered:
        if row["run_id"] not in seen:
            seen.add(row["run_id"])
            selected.append(row | {"internal_split": split(row["seed"])})
        if len(selected) == 256:
            break
    if len(selected) < 64 or not all(any(r["internal_split"] == s for r in selected)
                                     for s in ("train", "dev")):
        raise ValueError("insufficient_frozen_TRAIN_episodes")
    save(root / "manifest.json", {"schema": "rl3/offline-value-head-TRAIN-1",
        "frozen_at": cut, "selection": "latest uploaded_at/run_id; unique run; no outcome ranking",
        "point": str(point), "point_sha256": digest(point), "fixed_seeds_sha256": digest(seed_path),
        "eligible": len(eligible), "selected": selected, "World_calls": 0,
        "holdout_used_for_selection": False})


def load_base(point: dict[str, Any]) -> Any:
    import torch
    from stpd.p1.export import load_model
    from stpd.rl.checkpoint import learner_state
    from stpd.rl.critic import enable_value_head
    from stpd.workers.checkpoint_codec import decode_checkpoint

    path = Path(point["checkpoint_file"])
    if digest(path) != point["checkpoint_payload_sha256"]:
        raise ValueError("frozen_checkpoint_changed")
    state = decode_checkpoint(path.read_bytes())
    saved = learner_state(state)
    if saved["updates"] != point["critic_policy_updates"] or saved["version"] != point["model"]["version"]:
        raise ValueError("frozen_policy_critic_binding_changed")
    model, _ = load_model(Path(point["package"]) / "model.tensor-tree", point["model"]["model_sha256"])
    enable_value_head(model, point["learner_config"]["seed"])
    model.load_state_dict(state["weights"])
    model.eval()
    for p in model.parameters():
        p.requires_grad_(False)
    return model


def extract_one(task: dict[str, Any]) -> dict[str, Any]:
    import torch
    from spireagent.rl_exchange import Exchange
    from spireagent.workbench.managed_trajectory_store import read_shard
    from stpd.p1.input import project
    from stpd.p1.export import runtime_projection
    from stpd.rl.critic import held_out, terminal_targets
    from stpd.rl.reward import episode_reward
    from stpd.evaluation.suites import seed_membership

    torch.set_num_threads(4)
    point = json.loads(Path(task["point"]).read_bytes())
    model = load_base(point)
    exchange = Exchange(point["learner_config"]["peer"], point["learner_config"]["run"])
    row = task["row"]
    scratch = Path(task["output"]).parent / ("read-" + row["run_id"])
    scratch.mkdir(mode=0o700)
    if task["kind"] == "train":
        if held_out(row["seed"]) or seed_membership(row["seed"]):
            raise ValueError("TRAIN_manifest_exclusion_violation")
        record = exchange.shard_record(row["record_key"])
        if record["shard_artifact_id"] != row["shard_artifact_id"] or row["run_id"] not in record["run_ids"]:
            raise ValueError("TRAIN_record_binding_mismatch")
        package = exchange.model(record["model_version"])
        if package["model_artifact_id"] != record["model_artifact_id"]:
            raise ValueError("TRAIN_model_binding_mismatch")
    episodes = [e for e in read_shard(exchange.store, row["shard_artifact_id"], scratch=scratch)
                if e["run_id"] == row["run_id"]]
    if len(episodes) != 1 or episodes[0]["identity"]["seed"] != row["seed"]:
        raise ValueError("typed_episode_seed_run_mismatch")
    ep = episodes[0]
    if task["kind"] == "train" and (ep["identity"]["model_artifact_id"] != package["model_artifact_id"]
            or ep["identity"]["model_sha256"] != package["model_sha256"]):
        raise ValueError("typed_TRAIN_model_mismatch")
    reward, disposition = episode_reward(ep)
    if reward is None or disposition not in ("win", "loss"):
        raise ValueError("complete_natural_episode_required")
    frames = [project(ep["observations"][s["observation_index"]]["observation"],
                      input_projection=runtime_projection(model.vocabulary)) for s in ep["steps"]]
    scenes, means, linear, scores = [], [], [], []
    exact_zero = True
    rng_before = torch.get_rng_state().clone()
    # For fixed real holdout inputs compare full policy scores before/zero branch.
    with torch.no_grad():
        for start in range(0, len(frames), 8):
            captured: list[Any] = []
            out, mean = model._forward_batch(frames[start:start + 8],
                values_only=task["kind"] == "train", capture_scene=captured)
            scene, mask = captured
            for i in range(len(mean)):
                scenes.append(scene[i, ~mask[i]].clone())
            means.extend(mean.unbind())
            linear.extend(model.progress_head(mean)[:, 0].tolist())
            scores.extend(v.clone() for v in out)
        model.enable_progress_residual(20261101)
        if not torch.equal(rng_before, torch.get_rng_state()):
            raise ValueError("head_initialization_consumed_policy_RNG")
        if task["kind"] == "holdout":
            offset = 0
            for start in range(0, len(frames), 8):
                out, values = model.forward_with_values(frames[start:start + 8])
                for value in out:
                    exact_zero = exact_zero and torch.equal(value, scores[offset])
                    offset += 1
                exact_zero = exact_zero and torch.equal(values[:, 1], torch.tensor(linear[start:start+8]))
            if not exact_zero:
                raise ValueError("zero_head_policy_or_value_changed")
    cache = {"run_id": row["run_id"], "seed": row["seed"], "kind": task["kind"],
        "internal_split": row.get("internal_split"), "scene": scenes,
        "mean": torch.stack(means), "linear": torch.tensor(linear),
        "targets": torch.tensor(terminal_targets(reward, len(frames), .999)),
        "floor": ep["outcome"]["max_observed_floor"], "result": disposition,
        "zero_head_exact_policy_and_value": exact_zero,
        "full_policy_scores": scores, "point_sha256": digest(Path(task["point"]))}
    torch.save(cache, task["output"])
    return {"run_id": row["run_id"], "rows": len(frames), "path": task["output"],
            "sha256": digest(Path(task["output"])), "internal_split": row.get("internal_split")}


def extract(root: Path, kind: str) -> None:
    manifest = json.loads((root / "manifest.json").read_bytes())
    point_path = Path(manifest["point"])
    if digest(point_path) != manifest["point_sha256"]:
        raise ValueError("frozen_point_changed")
    if kind == "train":
        rows = manifest["selected"]
    else:
        from spireagent.workbench.evaluation_ledger import EvaluationLedger
        from value_validation import load_plan, bound
        plan = load_plan(point_path.parent / "plan.json")
        rows = []
        for g in plan["groups"]:
            slot = EvaluationLedger(Path(g["ledger"])).slots()[0]
            if slot["status"] == "saved" and slot["cleanup_accepted"] is True:
                rows.append({"run_id": slot["run_id"], "seed": g["seed"],
                             "shard_artifact_id": slot["trajectory_shard_id"]})
        # Exact identities were verified by the normal scorer before this experiment.
        report = json.loads((point_path.parent / "score/report.json").read_bytes())
        if sorted(r["run_id"] for r in rows) != sorted(g["run_id"] for g in report["metrics"]["per_game"]):
            raise ValueError("normal_scorer_holdout_cohort_mismatch")
        if bound(plan["point"]) != json.loads(point_path.read_bytes()):
            raise ValueError("normal_point_binding_changed")
    directory = root / (kind + "-features")
    directory.mkdir(mode=0o700)
    tasks = [{"point": str(point_path), "row": row, "kind": kind,
              "output": str(directory / (row["run_id"] + ".pt"))} for row in rows]
    complete = []
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = [pool.submit(extract_one, task) for task in tasks]
        for future in as_completed(futures):
            complete.append(future.result())
            print(json.dumps({"stage": kind + "_features", "completed": len(complete),
                              "total": len(tasks), "last": complete[-1]}), flush=True)
    save(root / (kind + "-features.json"), {"manifest_sha256": digest(root / "manifest.json"),
        "kind": kind, "files": complete, "World_calls": 0})


def caches(root: Path, kind: str) -> list[Any]:
    import torch
    manifest = json.loads((root / (kind + "-features.json")).read_bytes())
    output = []
    for ref in manifest["files"]:
        if digest(Path(ref["path"])) != ref["sha256"]:
            raise ValueError("frozen_features_changed")
        output.append(torch.load(ref["path"], weights_only=True))
    return output


def batch(features: list[Any], rows: list[tuple[int, int]]) -> tuple[Any, Any, Any, Any, Any]:
    import torch
    scenes = [features[e]["scene"][i] for e, i in rows]
    scene = torch.nn.utils.rnn.pad_sequence(scenes, batch_first=True)
    lengths = torch.tensor([len(s) for s in scenes])
    mask = torch.arange(scene.shape[1])[None, :] >= lengths[:, None]
    return (scene, mask, torch.stack([features[e]["mean"][i] for e, i in rows]),
            torch.stack([features[e]["linear"][i] for e, i in rows]),
            torch.stack([features[e]["targets"][i] for e, i in rows]))


def train(root: Path) -> None:
    import torch
    from stpd.p1.value_head import AttentionValueResidual
    from stpd.workers.checkpoint_codec import encode_checkpoint

    torch.set_num_threads(16)
    torch.manual_seed(20261101)
    features = caches(root, "train")
    rows = {s: [(e, i) for e, f in enumerate(features) if f["internal_split"] == s
                for i in range(len(f["targets"]))] for s in ("train", "dev")}
    if not rows["train"] or not rows["dev"]:
        raise ValueError("independent_internal_TRAIN_DEV_required")
    head = AttentionValueResidual()
    optimizer = torch.optim.AdamW(head.parameters(), lr=3e-4)
    point = json.loads(Path(json.loads((root / "manifest.json").read_bytes())["point"]).read_bytes())
    # Original TRAIN-scale definition; no holdout statistics.
    stats = point["critic_target_statistics"]
    cfg = point["critic_config"]
    scale = (max(cfg["target_std_floor"], math.sqrt(max(0., stats["second"]-stats["mean"]**2)))
             if cfg["normalize_targets"] else 1.)
    best, best_epoch, best_weights = math.inf, 0, None
    rng = random.Random(20261101)
    history = []
    began = time.monotonic()
    for epoch in range(1, 21):
        ordered = rows["train"].copy()
        rng.shuffle(ordered)
        train_total = 0.
        head.train()
        for start in range(0, len(ordered), 256):
            scene, mask, mean, linear, target = batch(features, ordered[start:start+256])
            optimizer.zero_grad(set_to_none=True)
            prediction = linear + head(scene, mask, mean)[:, 0]
            loss = (((prediction - target) / scale)**2).mean()
            if not bool(torch.isfinite(loss)):
                raise ValueError("nonfinite_offline_head_loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(head.parameters(), 1.)
            optimizer.step()
            train_total += float(loss.detach()) * len(mean)
        head.eval()
        dev_total = 0.
        with torch.no_grad():
            for start in range(0, len(rows["dev"]), 256):
                scene, mask, mean, linear, target = batch(features, rows["dev"][start:start+256])
                dev_total += float(((linear + head(scene,mask,mean)[:,0] - target)**2).sum())
        dev = dev_total / len(rows["dev"])
        if dev < best:
            best, best_epoch, best_weights = dev, epoch, copy.deepcopy(head.state_dict())
        record = {"epoch": epoch, "train_scaled_mse": train_total/len(rows["train"]),
                  "dev_raw_mse": dev, "elapsed_seconds": time.monotonic()-began}
        history.append(record)
        print(json.dumps(record), flush=True)
        if time.monotonic() - began >= 5400:
            break
    (root / "candidate.bin").write_bytes(encode_checkpoint({"weights": best_weights}))
    save(root / "candidate.json", {"schema": "rl3/offline-progress-residual-1",
        "candidate_sha256": digest(root / "candidate.bin"),
        "manifest_sha256": digest(root / "manifest.json"), "features_sha256": digest(root / "train-features.json"),
        "best_epoch": best_epoch, "internal_dev_mse": best, "history": history,
        "train_rows": len(rows["train"]), "internal_dev_rows": len(rows["dev"]),
        "lr": 3e-4, "old_linear_and_backbone": "frozen", "Adam_state_on_deployment": "fresh residual moments",
        "holdout_used": False, "selection": "single candidate by internal DEV MSE"})


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("freeze", "extract-train", "extract-holdout", "train"))
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--point-root", type=Path)
    p.add_argument("--observer", type=Path)
    a = p.parse_args()
    if a.command == "freeze":
        freeze(a.root, a.point_root, a.observer)
    elif a.command.startswith("extract-"):
        extract(a.root, a.command.removeprefix("extract-"))
    else:
        train(a.root)


if __name__ == "__main__":
    main()
