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
    rows = [
        r
        for path in sorted(observer.glob("*.json"))
        for r in json.loads(path.read_bytes())["rows"]
        if r["uploaded_at"] <= cut
    ]
    eligible = [
        r
        for r in rows
        if r["recorded_status"] == "saved"
        and r["outcome"] in ("win", "loss")
        and not held_out(r["seed"])
        and not seed_membership(r["seed"])
        and r["seed"] not in excluded
    ]
    ordered = sorted(eligible, key=lambda r: (r["uploaded_at"], r["run_id"]), reverse=True)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in ordered:
        if row["run_id"] not in seen:
            seen.add(row["run_id"])
            selected.append(row | {"internal_split": split(row["seed"])})
        if len(selected) == 256:
            break
    if len(selected) < 64 or not all(
        any(r["internal_split"] == s for r in selected) for s in ("train", "dev")
    ):
        raise ValueError("insufficient_frozen_TRAIN_episodes")
    save(
        root / "manifest.json",
        {
            "schema": "rl3/offline-value-head-TRAIN-1",
            "frozen_at": cut,
            "selection": "latest uploaded_at/run_id; unique run; no outcome ranking",
            "point": str(point),
            "point_sha256": digest(point),
            "fixed_seeds_sha256": digest(seed_path),
            "eligible": len(eligible),
            "selected": selected,
            "World_calls": 0,
            "holdout_used_for_selection": False,
        },
    )


def load_base(point: dict[str, Any]) -> Any:
    from stpd.p1.export import load_model
    from stpd.rl.checkpoint import learner_state
    from stpd.rl.critic import enable_value_head
    from stpd.workers.checkpoint_codec import decode_checkpoint

    path = Path(point["checkpoint_file"])
    if digest(path) != point["checkpoint_payload_sha256"]:
        raise ValueError("frozen_checkpoint_changed")
    state = decode_checkpoint(path.read_bytes())
    saved = learner_state(state)
    if (
        saved["updates"] != point["critic_policy_updates"]
        or saved["version"] != point["model"]["version"]
    ):
        raise ValueError("frozen_policy_critic_binding_changed")
    model, _ = load_model(
        Path(point["package"]) / "model.tensor-tree", point["model"]["model_sha256"]
    )
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
    from stpd.evaluation.suites import seed_membership
    from stpd.p1.export import runtime_projection
    from stpd.p1.input import project
    from stpd.rl.critic import held_out, terminal_targets
    from stpd.rl.reward import episode_reward

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
        if (
            record["shard_artifact_id"] != row["shard_artifact_id"]
            or row["run_id"] not in record["run_ids"]
        ):
            raise ValueError("TRAIN_record_binding_mismatch")
        package = exchange.model(record["model_version"])
        if package["model_artifact_id"] != record["model_artifact_id"]:
            raise ValueError("TRAIN_model_binding_mismatch")
    episodes = [
        e
        for e in read_shard(exchange.store, row["shard_artifact_id"], scratch=scratch)
        if e["run_id"] == row["run_id"]
    ]
    if len(episodes) != 1 or episodes[0]["identity"]["seed"] != row["seed"]:
        raise ValueError("typed_episode_seed_run_mismatch")
    ep = episodes[0]
    if task["kind"] == "train" and (
        ep["identity"]["model_artifact_id"] != package["model_artifact_id"]
        or ep["identity"]["model_sha256"] != package["model_sha256"]
    ):
        raise ValueError("typed_TRAIN_model_mismatch")
    reward, disposition = episode_reward(ep)
    if reward is None or disposition not in ("win", "loss"):
        raise ValueError("complete_natural_episode_required")
    frames = [
        project(
            ep["observations"][s["observation_index"]]["observation"],
            input_projection=runtime_projection(model.vocabulary),
        )
        for s in ep["steps"]
    ]
    scenes, means, linear = [], [], []
    scores: list[Any] = []
    exact_zero = True
    rng_before = torch.get_rng_state().clone()
    # For fixed real holdout inputs compare full policy scores before/zero branch.
    with torch.no_grad():
        for start in range(0, len(frames), 8):
            captured: list[Any] = []
            out, mean = model._forward_batch(
                frames[start : start + 8],
                values_only=task["kind"] == "train",
                capture_scene=captured,
            )
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
                out, values = model.forward_with_values(frames[start : start + 8])
                for value in out:
                    exact_zero = exact_zero and torch.equal(value, scores[offset])
                    offset += 1
                exact_zero = exact_zero and torch.equal(
                    values[:, 1], torch.tensor(linear[start : start + 8])
                )
            if not exact_zero:
                raise ValueError("zero_head_policy_or_value_changed")
    cache = {
        "run_id": row["run_id"],
        "seed": row["seed"],
        "kind": task["kind"],
        "internal_split": row.get("internal_split"),
        "scene": scenes,
        "mean": torch.stack(means),
        "linear": torch.tensor(linear),
        "targets": torch.tensor(
            terminal_targets(reward, len(frames), point["critic_config"]["gamma"]),
            dtype=torch.float64,
        ),
        "floor": ep["outcome"]["max_observed_floor"],
        "result": disposition,
        "zero_head_exact_policy_and_value": exact_zero,
        "full_policy_scores": scores,
        "point_sha256": digest(Path(task["point"])),
    }
    torch.save(cache, task["output"])
    return {
        "run_id": row["run_id"],
        "rows": len(frames),
        "path": task["output"],
        "sha256": digest(Path(task["output"])),
        "internal_split": row.get("internal_split"),
    }


def extract(root: Path, kind: str) -> None:
    manifest = json.loads((root / "manifest.json").read_bytes())
    point_path = Path(manifest["point"])
    if digest(point_path) != manifest["point_sha256"]:
        raise ValueError("frozen_point_changed")
    if kind == "train":
        rows = manifest["selected"]
    else:
        from spireagent.workbench.evaluation_ledger import EvaluationLedger
        from value_validation import bound, load_plan

        plan = load_plan(point_path.parent / "plan.json")
        rows = []
        for g in plan["groups"]:
            slot = EvaluationLedger(Path(g["ledger"])).slots()[0]
            if slot["status"] == "saved" and slot["cleanup_accepted"] is True:
                rows.append(
                    {
                        "run_id": slot["run_id"],
                        "seed": g["seed"],
                        "shard_artifact_id": slot["trajectory_shard_id"],
                    }
                )
        # Exact identities were verified by the normal scorer before this experiment.
        report = json.loads((point_path.parent / "score/report.json").read_bytes())
        if sorted(r["run_id"] for r in rows) != sorted(
            g["run_id"] for g in report["metrics"]["per_game"]
        ):
            raise ValueError("normal_scorer_holdout_cohort_mismatch")
        if bound(plan["point"]) != json.loads(point_path.read_bytes()):
            raise ValueError("normal_point_binding_changed")
    directory = root / (kind + "-features")
    directory.mkdir(mode=0o700)
    tasks = [
        {
            "point": str(point_path),
            "row": row,
            "kind": kind,
            "output": str(directory / (row["run_id"] + ".pt")),
        }
        for row in rows
    ]
    complete = []
    with ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        futures = [pool.submit(extract_one, task) for task in tasks]
        for future in as_completed(futures):
            complete.append(future.result())
            print(
                json.dumps(
                    {
                        "stage": kind + "_features",
                        "completed": len(complete),
                        "total": len(tasks),
                        "last": complete[-1],
                    }
                ),
                flush=True,
            )
    save(
        root / (kind + "-features.json"),
        {
            "manifest_sha256": digest(root / "manifest.json"),
            "kind": kind,
            "files": complete,
            "World_calls": 0,
        },
    )


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
    return (
        scene,
        mask,
        torch.stack([features[e]["mean"][i] for e, i in rows]),
        torch.stack([features[e]["linear"][i] for e, i in rows]),
        torch.stack([features[e]["targets"][i] for e, i in rows]).float(),
    )


def train(root: Path) -> None:
    import torch
    from stpd.p1.value_head import AttentionValueResidual
    from stpd.workers.checkpoint_codec import encode_checkpoint

    began = time.monotonic()
    deadline = began + 5400
    torch.set_num_threads(16)
    torch.manual_seed(20261101)
    features = caches(root, "train")
    rows = {
        s: [
            (e, i)
            for e, f in enumerate(features)
            if f["internal_split"] == s
            for i in range(len(f["targets"]))
        ]
        for s in ("train", "dev")
    }
    if not rows["train"] or not rows["dev"]:
        raise ValueError("independent_internal_TRAIN_DEV_required")
    head = AttentionValueResidual()
    point = json.loads(
        Path(json.loads((root / "manifest.json").read_bytes())["point"]).read_bytes()
    )
    weight_decay = point["learner_config"]["weight_decay"]
    optimizer = torch.optim.AdamW(head.parameters(), lr=3e-4, weight_decay=weight_decay)
    # Original TRAIN-scale definition; no holdout statistics.
    stats = point["critic_target_statistics"]
    cfg = point["critic_config"]
    scale = (
        max(cfg["target_std_floor"], math.sqrt(max(0.0, stats["second"] - stats["mean"] ** 2)))
        if cfg["normalize_targets"]
        else 1.0
    )
    best, best_epoch, best_weights = math.inf, 0, None
    rng = random.Random(20261101)
    history = []
    budget_stop = False
    optimizer_updates = 0
    for epoch in range(1, 21):
        ordered = rows["train"].copy()
        rng.shuffle(ordered)
        train_total = 0.0
        head.train()
        for start in range(0, len(ordered), 256):
            if time.monotonic() >= deadline:
                budget_stop = True
                break
            scene, mask, mean, linear, target = batch(features, ordered[start : start + 256])
            optimizer.zero_grad(set_to_none=True)
            prediction = linear + head(scene, mask, mean)[:, 0]
            loss = (((prediction - target) / scale) ** 2).mean()
            if not bool(torch.isfinite(loss)):
                raise ValueError("nonfinite_offline_head_loss")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(head.parameters(), 1.0)
            optimizer.step()
            optimizer_updates += 1
            train_total += float(loss.detach()) * len(mean)
        if budget_stop:
            break  # A partial epoch never replaces the best fully validated candidate.
        head.eval()
        dev_total = 0.0
        with torch.no_grad():
            for start in range(0, len(rows["dev"]), 256):
                if time.monotonic() >= deadline:
                    budget_stop = True
                    break
                scene, mask, mean, linear, target = batch(
                    features, rows["dev"][start : start + 256]
                )
                dev_total += float(((linear + head(scene, mask, mean)[:, 0] - target) ** 2).sum())
        if budget_stop:
            break
        dev = dev_total / len(rows["dev"])
        if dev < best:
            best, best_epoch, best_weights = dev, epoch, copy.deepcopy(head.state_dict())
        record = {
            "epoch": epoch,
            "train_scaled_mse": train_total / len(rows["train"]),
            "dev_raw_mse": dev,
            "elapsed_seconds": time.monotonic() - began,
        }
        history.append(record)
        print(json.dumps(record), flush=True)
    if best_weights is None:
        raise ValueError("CPU_budget_exhausted_before_one_complete_internal_DEV_epoch")
    (root / "candidate.bin").write_bytes(encode_checkpoint({"weights": best_weights}))
    save(
        root / "candidate.json",
        {
            "schema": "rl3/offline-progress-residual-1",
            "candidate_sha256": digest(root / "candidate.bin"),
            "manifest_sha256": digest(root / "manifest.json"),
            "features_sha256": digest(root / "train-features.json"),
            "best_epoch": best_epoch,
            "internal_dev_mse": best,
            "history": history,
            "budget_stop": budget_stop,
            "partial_epoch_not_selected": budget_stop,
            "optimizer_updates_including_partial_epoch": optimizer_updates,
            "wall_seconds": time.monotonic() - began,
            "train_rows": len(rows["train"]),
            "internal_dev_rows": len(rows["dev"]),
            "lr": 3e-4,
            "weight_decay": weight_decay,
            "old_linear_and_backbone": "frozen",
            "Adam_state_on_deployment": "fresh residual moments",
            "holdout_used": False,
            "selection": "single candidate by internal DEV MSE",
        },
    )


def verify_one(task: dict[str, Any]) -> dict[str, Any]:
    import torch
    from spireagent.rl_exchange import Exchange
    from spireagent.workbench.managed_trajectory_store import read_shard
    from stpd.p1.export import action_weights, runtime_projection
    from stpd.p1.input import project
    from stpd.workers.checkpoint_codec import decode_checkpoint, encode_checkpoint

    torch.set_num_threads(4)
    point = json.loads(Path(task["point"]).read_bytes())
    model = load_base(point)
    baseline = action_weights(model.state_dict(), value_heads=True)
    baseline = {k: v.clone() for k, v in baseline.items()}
    model.enable_progress_residual(20261101)
    model.progress_residual.load_state_dict(
        decode_checkpoint(Path(task["candidate"]).read_bytes())["weights"]
    )
    model.eval()
    cached = torch.load(task["cache"], weights_only=True)
    exchange = Exchange(point["learner_config"]["peer"], point["learner_config"]["run"])
    ep = next(
        e
        for e in read_shard(exchange.store, task["shard"], scratch=Path(task["scratch"]))
        if e["run_id"] == cached["run_id"]
    )
    frames = [
        project(
            ep["observations"][step["observation_index"]]["observation"],
            input_projection=runtime_projection(model.vocabulary),
        )
        for step in ep["steps"]
    ]
    if len(frames) != len(cached["full_policy_scores"]):
        raise ValueError("fixed_input_count_changed")
    for phase in ("trained", "full_checkpoint_reload"):
        if phase == "full_checkpoint_reload":
            state = decode_checkpoint(encode_checkpoint({"weights": model.state_dict()}))
            model.load_state_dict(state["weights"])
        with torch.no_grad():
            for start in range(0, len(frames), 8):
                scores, _ = model.forward_with_values(frames[start : start + 8])
                if any(
                    not torch.equal(score, cached["full_policy_scores"][start + i])
                    or int(score.argmax()) != int(cached["full_policy_scores"][start + i].argmax())
                    for i, score in enumerate(scores)
                ):
                    raise ValueError("trained_or_reloaded_head_changed_fixed_policy_scores")
        exported = action_weights(model.state_dict(), value_heads=True)
        if exported.keys() != baseline.keys() or any(
            not torch.equal(exported[k], v) for k, v in baseline.items()
        ):
            raise ValueError("trained_head_changed_exported_policy_weights")
    return {
        "run_id": cached["run_id"],
        "rows": len(frames),
        "full_logits_exact": True,
        "argmax_exact": True,
        "exported_weights_exact": True,
        "full_CP_reload_exact": True,
    }


def quality_gates(
    old: dict[str, Any], new: dict[str, Any]
) -> tuple[dict[str, bool], dict[str, float | None]]:
    """Frozen relative acceptance; missing/zero temporal estimates cannot win by default."""

    def at_least(value: float | None, reference: float | None) -> bool:
        return (
            value is not None
            and reference is not None
            and math.isfinite(value)
            and math.isfinite(reference)
            and value >= reference
        )

    gates: dict[str, bool] = {}
    for field in ("per_decision", "equal_game_weighted"):
        old_ev = old[field]["explained_variance"]
        gates[field + "_EV"] = at_least(
            new[field]["explained_variance"], old_ev - 1e-6 if old_ev is not None else None
        )

        gates[field + "_MSE"] = new[field]["mse"] <= old[field]["mse"]
        gates[field + "_bias"] = (
            abs(new[field]["bias_prediction_minus_target"])
            <= abs(old[field]["bias_prediction_minus_target"]) + 0.001
        )
    gates["absolute_game_bias_mean"] = (
        new["mean_absolute_game_bias"] <= old["mean_absolute_game_bias"]
    )
    gates["absolute_game_bias_p90"] = (
        new["absolute_game_bias"]["p90"] <= old["absolute_game_bias"]["p90"]
    )
    old_by = {g["run_id"]: g for g in old["per_game"]}
    deltas = [
        g["temporal"]["correlation"] - old_by[g["run_id"]]["temporal"]["correlation"]
        for g in new["per_game"]
        if g["temporal"]["correlation"] is not None
        and old_by[g["run_id"]]["temporal"]["correlation"] is not None
    ]
    gates["correlation_valid_count"] = (
        new["within_game_correlation"]["valid_games"]
        >= old["within_game_correlation"]["valid_games"]
    )
    gates["correlation_median"] = at_least(
        new["within_game_correlation"]["median"], old["within_game_correlation"]["median"]
    )
    gates["paired_correlation_delta_median"] = bool(deltas) and statistics.median(deltas) >= 0
    gates["jitter_valid_count"] = (
        new["jitter_ratio"]["valid_games"] >= old["jitter_ratio"]["valid_games"]
    )

    def log_jitter(report: dict[str, Any]) -> float | None:
        ratios = [g["temporal"]["jitter_ratio"] for g in report["per_game"]]
        distances = [abs(math.log(v)) if v and v > 0 else math.inf for v in ratios if v is not None]
        median = statistics.median(distances) if distances else math.inf
        return median if math.isfinite(median) else None

    old_log, new_log = log_jitter(old), log_jitter(new)
    gates["jitter_log_distance_25percent"] = (
        old_log is not None and new_log is not None and new_log <= 0.75 * old_log
    )
    gates["jitter_no_flat_collapse"] = at_least(new["jitter_ratio"]["median"], 0.5)
    return gates, {"baseline": old_log, "candidate": new_log}


def evaluate(root: Path) -> None:
    import torch
    from spireagent.workbench.evaluation_ledger import EvaluationLedger
    from stpd.evaluation.value_quality import summarize
    from stpd.p1.value_head import AttentionValueResidual
    from stpd.workers.checkpoint_codec import decode_checkpoint

    torch.set_num_threads(16)
    candidate = json.loads((root / "candidate.json").read_bytes())
    if (
        candidate["candidate_sha256"] != digest(root / "candidate.bin")
        or candidate["holdout_used"] is not False
    ):
        raise ValueError("internally_selected_frozen_candidate_required")
    features = caches(root, "holdout")
    head = AttentionValueResidual().eval()
    head.load_state_dict(decode_checkpoint((root / "candidate.bin").read_bytes())["weights"])
    games, old_games = [], []
    for f in features:
        prediction = []
        # One game at a time; no optimizer and no holdout model selection.
        with torch.no_grad():
            for start in range(0, len(f["targets"]), 256):
                rows = [(0, i) for i in range(start, min(start + 256, len(f["targets"])))]
                scene, mask, mean, linear, _ = batch([f], rows)
                prediction.extend((linear + head(scene, mask, mean)[:, 0]).tolist())
        base = {k: f[k] for k in ("run_id", "seed", "floor", "result")}
        games.append(base | {"predictions": prediction, "targets": f["targets"].tolist()})
        old_games.append(
            base | {"predictions": f["linear"].tolist(), "targets": f["targets"].tolist()}
        )
    old, new = summarize(old_games), summarize(games)
    gates, log_distance = quality_gates(old, new)
    manifest = json.loads((root / "manifest.json").read_bytes())
    point_path = Path(manifest["point"])
    plan = json.loads((point_path.parent / "plan.json").read_bytes())
    shards = {
        s["run_id"]: s["trajectory_shard_id"]
        for g in plan["groups"]
        for s in EvaluationLedger(Path(g["ledger"])).slots()
        if s["status"] == "saved"
    }
    feature_index = json.loads((root / "holdout-features.json").read_bytes())
    tasks = [
        {
            "point": str(point_path),
            "candidate": str(root / "candidate.bin"),
            "cache": r["path"],
            "shard": shards[r["run_id"]],
            "scratch": str(root / "policy-check" / r["run_id"]),
        }
        for r in feature_index["files"]
    ]
    checks = []
    (root / "policy-check").mkdir(mode=0o700)
    with ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        for future in as_completed([pool.submit(verify_one, t) for t in tasks]):
            checks.append(future.result())
            print(
                json.dumps(
                    {"stage": "fixed_policy_check", "completed": len(checks), "total": len(tasks)}
                ),
                flush=True,
            )
    gates["fixed_full_real_policy_and_CP_reload"] = len(checks) == len(features) and all(
        f["zero_head_exact_policy_and_value"] for f in features
    )
    save(
        root / "qualification.json",
        {
            "schema": "rl3/offline-value-head-qualification-1",
            "candidate_sha256": digest(root / "candidate.bin"),
            "manifest_sha256": digest(root / "manifest.json"),
            "passed": all(gates.values()),
            "gates": gates,
            "baseline": old,
            "candidate": new,
            "jitter_median_abs_log": log_distance,
            "zero_jitter_games": {
                "baseline": sum(g["temporal"]["jitter_ratio"] == 0 for g in old["per_game"]),
                "candidate": sum(g["temporal"]["jitter_ratio"] == 0 for g in new["per_game"]),
            },
            "fixed_input_checks": checks,
            "scoring_count": 1,
            "holdout_optimizer_updates": 0,
            "scope": "same v518 fixed policy MC diagnostic; not closed-loop gameplay evidence",
        },
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "command", choices=("freeze", "extract-train", "extract-holdout", "train", "evaluate")
    )
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--point-root", type=Path)
    p.add_argument("--observer", type=Path)
    a = p.parse_args()
    if a.command == "freeze":
        freeze(a.root, a.point_root, a.observer)
    elif a.command.startswith("extract-"):
        extract(a.root, a.command.removeprefix("extract-"))
    elif a.command == "train":
        train(a.root)
    else:
        evaluate(a.root)


if __name__ == "__main__":
    main()
