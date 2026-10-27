"""Per-model experiments over real Store/Snapshot/Curation, synthetic observations."""

import copy
import json

import pytest
from legacy_p1_fixture import freeze_p1_data as legacy_freeze
from test_artifact_store_v1 import PRODUCER
from test_p1_cohort_intake import fixture, projection

from spireagent.json_boundary import BoundaryError
from spireagent.workbench.p1_cohort_intake import PREVIEW, refresh_cohort, render_summary
from spireagent.workbench.p1_data_preparation import freeze_p1_data
from spireagent.workbench.p1_training_data import record_p1_training_use, require_p1_snapshot
from stpd.p1.cohort import experiment_plan


def pool_fixture(tmp_path):
    owner, store, config, policy_path = fixture(tmp_path)
    for n in ("d", "e", "f"):
        projection(store, n, level=10, result="loss")
    policy = json.loads(policy_path.read_bytes())
    policy["allocation_mode"] = "per_training"
    policy_path.write_text(json.dumps(policy))
    summary = refresh_cohort(config, policy_path)
    pool = json.loads((config.state_dir / PREVIEW).read_bytes())
    return owner, store, config, pool, summary


def draft(owner, pool, model="model-a", parent=None, **changes):
    return experiment_plan(
        pool,
        owner.ledger,
        {
            "model_id": model,
            "parent_model_id": parent,
            "split_salt": "fixed",
            "dev_fraction": 0.2,
            "test_fraction": 0.2,
            **changes,
        },
    )


def freeze(owner, store, plan):
    from spireagent.workbench.p1_cohort_cli import weight_rule_identity

    rule, definition = weight_rule_identity(plan)
    with owner.ledger.operations.transaction() as db:
        db.execute(
            "INSERT OR IGNORE INTO curation_p1_weight_approvals VALUES(?,?)", (rule, definition)
        )
    return freeze_p1_data(
        owner,
        store,
        PRODUCER,
        projection_ids=[s["projection_id"] for s in plan["sources"]],
        selected_decisions=plan["selection"],
        split_by_group=plan["splits"],
        split_salt=plan["configuration_id"],
        reserve=True,
        experiment={
            **plan["experiment"],
            "configuration_id": plan["configuration_id"],
            "weight_definition": plan["definition"],
            "weights_by_root": {
                r["root_run_id"]: r["train_weight"] for r in plan["runs"] if r["split"] == "train"
            },
        },
    )["snapshot_id"]


def test_pool_has_no_roles_or_claims_and_reweight_never_mutates_it(tmp_path):
    owner, store, config, pool, summary = pool_fixture(tmp_path)
    assert set(r["split"] for r in pool["runs"]) == {"pool"}
    assert summary["partitions"] == {"pool": {"runs": 6, "N": 6}}
    assert "POOL" in render_summary(config, {})
    with owner.ledger.operations.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_claims").fetchone()[0] == 0
    saved = copy.deepcopy(pool)
    new = draft(owner, pool)
    old = experiment_plan(pool, owner.ledger, new["allocation_request"], weight_rule="original")
    assert pool == saved and new["splits"] == old["splits"]
    assert new["configuration_id"] != old["configuration_id"]


def test_fresh_lineages_can_reshuffle_and_ancestor_training_blocks_exam(tmp_path):
    owner, store, _, pool, _ = pool_fixture(tmp_path)
    a = draft(owner, pool)
    snap = freeze(owner, store, a)
    record_p1_training_use(owner, store, snap, "a" * 32, loss_weighting=weighting(a))
    roles = a["splits"]
    trained = next(g for g, r in roles.items() if r == "train")
    prior_exam = next(g for g, r in roles.items() if r == "dev")
    swapped = {**roles, trained: "dev", prior_exam: "train"}
    with pytest.raises(BoundaryError, match="model_ancestor_trained_exam_run"):
        draft(owner, pool, model="child", parent="model-a", split_by_group=swapped)
    with pytest.raises(BoundaryError, match="parent_model_usage_history_required"):
        draft(owner, pool, parent="unregistered-old-model")
    b = draft(owner, pool, model="unrelated-fresh", split_by_group=swapped)
    other = freeze(owner, store, b)
    record_p1_training_use(owner, store, other, "b" * 32, loss_weighting=weighting(b))
    # An unrelated fresh model training A's exam does not contaminate A.
    require_p1_snapshot(owner, store, snap)
    child = draft(owner, pool, model="child", parent="model-a", split_by_group=roles)
    freeze(owner, store, child)


def test_existing_snapshot_remains_readable_when_fresh_model_uses_old_exam(tmp_path):
    owner, store, _, pool, _ = pool_fixture(tmp_path)
    plan = draft(owner, pool)
    legacy = legacy_freeze(
        owner,
        store,
        PRODUCER,
        projection_ids=[s["projection_id"] for s in plan["sources"]],
        selected_decisions=plan["selection"],
        split_by_group=plan["splits"],
        split_salt="legacy",
        reserve=True,
    )["snapshot_id"]
    record_p1_training_use(owner, store, legacy, "c" * 32)
    roles = plan["splits"]
    train = next(g for g, r in roles.items() if r == "train")
    dev = next(g for g, r in roles.items() if r == "dev")
    new = draft(owner, pool, split_by_group={**roles, train: "dev", dev: "train"})
    fresh = freeze(owner, store, new)
    record_p1_training_use(owner, store, fresh, "d" * 32, loss_weighting=weighting(new))
    require_p1_snapshot(owner, store, legacy)


def test_rule_approval_is_cached_by_definition_not_membership(tmp_path, monkeypatch):
    from spireagent.workbench import p1_cohort_cli as cli

    owner, store, config, pool, _ = pool_fixture(tmp_path)
    plan = draft(owner, pool)
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(plan))
    monkeypatch.setattr(cli.ProjectConfig, "load", lambda *a, **k: config)
    calls = []
    monkeypatch.setattr(cli, "freeze_preview", lambda c, p: calls.append(p) or {})
    args = ["freeze", "--config", str(tmp_path / "project.json"), "--plan-file", str(path)]
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert cli.main(args) == 0
    assert calls == []
    with owner.ledger.operations.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_p1_weight_approvals").fetchone()[0] == 0
    answers = []
    monkeypatch.setattr("builtins.input", lambda _: answers.append("yes") or "yes")
    cli.main(args)
    cli.main(args)
    assert len(answers) == 1 and len(calls) == 2
    changed = experiment_plan(
        pool, owner.ledger, plan["allocation_request"], weight_rule="original"
    )
    path.write_text(json.dumps(changed))
    cli.main(args)
    assert len(answers) == 2


def test_selecting_a_subset_preserves_full_runs_and_freezes_exact_members(tmp_path):
    from spireagent.workbench.training_cohort import cohort_weighting

    owner, store, config, pool, _ = pool_fixture(tmp_path)
    selected = [g["group_id"] for g in pool["groups"][:4]]
    plan = draft(owner, pool, selected_group_ids=selected)
    assert len(plan["runs"]) == 4
    assert set(plan["splits"]) == set(selected)
    snapshot = freeze(owner, store, plan)
    cohort_weighting(config, plan, plan["configuration_id"], snapshot)


def test_adopting_legacy_history_requires_real_use_then_checks_descendants(tmp_path):
    from spireagent.p1_curation import adopt_p1_model_history

    owner, store, _, pool, _ = pool_fixture(tmp_path)
    plan = draft(owner, pool)
    snapshot = legacy_freeze(
        owner,
        store,
        PRODUCER,
        projection_ids=[s["projection_id"] for s in plan["sources"]],
        selected_decisions=plan["selection"],
        split_by_group=plan["splits"],
        split_salt="old-model",
        reserve=True,
    )["snapshot_id"]
    experiment = {
        **plan["experiment"],
        "configuration_id": plan["configuration_id"],
        "weight_definition": plan["definition"],
        "weights_by_root": {
            r["root_run_id"]: r["train_weight"] for r in plan["runs"] if r["split"] == "train"
        },
    }
    with pytest.raises(BoundaryError, match="training_source_use_missing"):
        adopt_p1_model_history(owner.ledger, store, snapshot, "e" * 32, experiment)
    record_p1_training_use(owner, store, snapshot, "e" * 32)
    adopt_p1_model_history(owner.ledger, store, snapshot, "e" * 32, experiment)
    roles = plan["splits"]
    trained = next(g for g, r in roles.items() if r == "train")
    dev = next(g for g, r in roles.items() if r == "dev")
    with pytest.raises(BoundaryError, match="model_ancestor_trained_exam_run"):
        draft(
            owner,
            pool,
            model="child",
            parent="model-a",
            split_by_group={**roles, trained: "dev", dev: "train"},
        )


def weighting(plan):
    from spireagent.workbench.training_cohort import _loss_weighting

    return _loss_weighting(plan)


def test_unapproved_rule_and_changed_training_weights_fail_at_owner(tmp_path):
    owner, store, _, pool, _ = pool_fixture(tmp_path)
    plan = draft(owner, pool)
    experiment = {
        **plan["experiment"],
        "configuration_id": plan["configuration_id"],
        "weight_definition": plan["definition"],
        "weights_by_root": {
            r["root_run_id"]: r["train_weight"] for r in plan["runs"] if r["split"] == "train"
        },
    }
    with pytest.raises(BoundaryError, match="weight_rule_approval_required"):
        freeze_p1_data(
            owner,
            store,
            PRODUCER,
            projection_ids=[s["projection_id"] for s in plan["sources"]],
            selected_decisions=plan["selection"],
            split_by_group=plan["splits"],
            split_salt=plan["configuration_id"],
            reserve=True,
            experiment=experiment,
        )
    with owner.ledger.operations.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_p1_experiments").fetchone()[0] == 0
    snap = freeze(owner, store, plan)
    with pytest.raises(BoundaryError, match="experiment_training_weights_mismatch"):
        record_p1_training_use(owner, store, snap, "1" * 32)
    wrong = copy.deepcopy(weighting(plan))
    root = next(iter(wrong["weights_by_root"]))
    wrong["weights_by_root"][root] += 1
    with pytest.raises(BoundaryError, match="experiment_training_weights_mismatch"):
        record_p1_training_use(owner, store, snap, "1" * 32, loss_weighting=wrong)
    with pytest.raises(BoundaryError, match="fresh_model_requires_fresh_initialization"):
        record_p1_training_use(
            owner,
            store,
            snap,
            "1" * 32,
            loss_weighting=weighting(plan),
            initial_checkpoint="f" * 64,
        )


def test_quality_change_rejected_before_experiment_claim(tmp_path):
    from spireagent.p1_curation import _occurrence

    owner, store, _, pool, _ = pool_fixture(tmp_path)
    plan = draft(owner, pool)
    with owner.ledger.operations.transaction() as db:
        db.execute(
            "INSERT INTO curation_annotations(occurrence,actor,action,reason,at) VALUES(?,?,?,?,?)",
            (_occurrence("decision-a"), "fixture", "exclude", "bad", 0),
        )
    with pytest.raises(BoundaryError, match="snapshot_selected_excluded_N"):
        draft(owner, pool)
    with pytest.raises(BoundaryError, match="snapshot_selected_excluded_N"):
        freeze(owner, store, plan)
    with owner.ledger.operations.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_p1_experiments").fetchone()[0] == 0


def test_interactive_freeze_receipt_reads_through_original_consumer(tmp_path, monkeypatch):
    from spireagent.workbench import p1_cohort_cli as cli
    from spireagent.workbench import p1_data_cli
    from spireagent.workbench.local_recordings import LocalRecordingCatalog
    from spireagent.workbench.training_cohort import checked_cohort_plan, cohort_weighting

    owner, store, config, pool, _ = pool_fixture(tmp_path)
    plan = draft(owner, pool)
    rule, definition = cli.weight_rule_identity(plan)
    with owner.ledger.operations.transaction() as db:
        db.execute("INSERT INTO curation_p1_weight_approvals VALUES(?,?)", (rule, definition))
    monkeypatch.setattr(
        LocalRecordingCatalog, "read", lambda _: {"status": "ready", "game_running": False}
    )
    monkeypatch.setattr(
        p1_data_cli,
        "p1_data_command",
        lambda c, a, b, **kw: freeze_p1_data(
            owner, store, PRODUCER, **b["selection"], reserve=True
        ),
    )
    receipt = cli.freeze_preview(config, plan)
    raw = json.loads(
        (
            config.state_dir / "cohorts" / plan["configuration_id"] / "weight-candidates.json"
        ).read_bytes()
    )
    selected = checked_cohort_plan(raw, plan["configuration_id"])
    weights = cohort_weighting(config, selected, plan["configuration_id"], receipt["snapshot_id"])
    assert weights["definition"] == plan["definition"]
    assert weights["weights_by_root"] == weighting(plan)["weights_by_root"]


def test_api_cannot_substitute_numbers_under_an_approved_formula(tmp_path):
    from spireagent.workbench.p1_cohort_cli import weight_rule_identity

    owner, store, _, pool, _ = pool_fixture(tmp_path)
    plan = draft(owner, pool)
    rule, definition = weight_rule_identity(plan)
    with owner.ledger.operations.transaction() as db:
        db.execute("INSERT INTO curation_p1_weight_approvals VALUES(?,?)", (rule, definition))
    wrong = copy.deepcopy(weighting(plan)["weights_by_root"])
    wrong[next(iter(wrong))] = 100
    with pytest.raises(BoundaryError, match="experiment_weights_do_not_match_approved_formula"):
        freeze_p1_data(
            owner,
            store,
            PRODUCER,
            projection_ids=[s["projection_id"] for s in plan["sources"]],
            selected_decisions=plan["selection"],
            split_by_group=plan["splits"],
            split_salt=plan["configuration_id"],
            reserve=True,
            experiment={
                **plan["experiment"],
                "configuration_id": plan["configuration_id"],
                "weight_definition": plan["definition"],
                "weights_by_root": wrong,
            },
        )
    with owner.ledger.operations.transaction() as db:
        assert db.execute("SELECT count(*) FROM curation_p1_experiments").fetchone()[0] == 0


def test_new_freeze_without_model_and_legacy_snapshot_new_rule_are_rejected(tmp_path):
    owner, store, _, pool, _ = pool_fixture(tmp_path)
    plan = draft(owner, pool)
    args = {
        "projection_ids": [s["projection_id"] for s in plan["sources"]],
        "selected_decisions": plan["selection"],
        "split_by_group": plan["splits"],
        "split_salt": "legacy-omission",
        "reserve": True,
    }
    with pytest.raises(BoundaryError, match="new_snapshot_requires_model_experiment"):
        freeze_p1_data(owner, store, PRODUCER, **args)
    legacy = legacy_freeze(owner, store, PRODUCER, **args)["snapshot_id"]
    with pytest.raises(BoundaryError, match="new_weight_rule_requires_model_experiment"):
        record_p1_training_use(owner, store, legacy, "2" * 32, loss_weighting=weighting(plan))


def test_deferred_ancestor_cannot_contaminate_an_already_used_descendant(tmp_path):
    from spireagent.p1_curation import _scope

    owner, store, _, pool, _ = pool_fixture(tmp_path)
    parent = draft(owner, pool, model="parent")
    parent_snapshot = freeze(owner, store, parent)
    roles = parent["splits"]
    trained = next(g for g, r in roles.items() if r == "train")
    dev = next(g for g, r in roles.items() if r == "dev")
    child = draft(
        owner,
        pool,
        model="child",
        parent="parent",
        split_by_group={**roles, trained: "dev", dev: "train"},
    )
    child_snapshot = freeze(owner, store, child)
    # Simulate an already durable child exposure from a historical/interrupted
    # producer. The owner must reject a later parent use even in this order.
    with owner.ledger.operations.transaction() as db:
        db.execute("INSERT INTO curation_p1_model_uses VALUES(?,?)", ("3" * 32, child_snapshot))
    with pytest.raises(BoundaryError, match="ancestor_use_would_contaminate_descendant_exam"):
        record_p1_training_use(
            owner, store, parent_snapshot, "4" * 32, loss_weighting=weighting(parent)
        )
    _, scopes = _scope(store, parent_snapshot)
    with owner.ledger.operations.transaction() as db:
        assert not db.execute(
            "SELECT 1 FROM curation_p1_model_uses WHERE reference=?", ("4" * 32,)
        ).fetchone()
        for root in scopes["train"]["roots"]:
            assert not db.execute(
                "SELECT 1 FROM curation_uses WHERE run=? AND reference=?", (root, "4" * 32)
            ).fetchone()


def test_upgrade_grandfathers_existing_snapshots_once_only(tmp_path):
    from spireagent.research_curation import P1_EXPERIMENT_TABLES, CurationLedger

    owner, store, _, pool, _ = pool_fixture(tmp_path)
    plan = draft(owner, pool)
    snapshot = legacy_freeze(
        owner,
        store,
        PRODUCER,
        projection_ids=[s["projection_id"] for s in plan["sources"]],
        selected_decisions=plan["selection"],
        split_by_group=plan["splits"],
        split_salt="pre-upgrade",
        reserve=True,
    )["snapshot_id"]
    # Reproduce the pre-upgrade database, retaining its original purpose claims.
    with owner.ledger.operations.transaction() as db:
        for table in sorted(P1_EXPERIMENT_TABLES):
            db.execute("DROP TABLE " + table)
    migrated = CurationLedger(owner)
    with migrated.operations.transaction() as db:
        assert db.execute("SELECT snapshot FROM curation_p1_legacy_snapshots").fetchall() == [
            (snapshot,)
        ]
    migrated.claim("later-claim", "training", {"new-root"})
    migrated.bind("later-claim", "f" * 64)
    CurationLedger(owner)
    with migrated.operations.transaction() as db:
        assert db.execute("SELECT snapshot FROM curation_p1_legacy_snapshots").fetchall() == [
            (snapshot,)
        ]
