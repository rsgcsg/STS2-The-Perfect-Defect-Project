"""T2 auxiliary outcome supervision; never inference input or an RL reward."""
from __future__ import annotations

from typing import Any

from .spec import TARGET_SPEC

VALUE_TARGET_SPEC: dict[str, Any] = {
    **TARGET_SPEC, "id": "p1-current-catalog-N-outcome-progress-2", "version": "2",
    "end": "trusted native natural outcome only; unknown/technical/censored masked",
    "auxiliary": {"schema": "stpd/p1-value-targets-1",
                  "win": "native natural win=1, loss=0; otherwise masked",
                  "progress": "final recorded run floor / frozen finish floor; win=1",
                  "finish_floors": {"0": 48, "10": 49},
                  "mask": "both require unconflicted native natural terminal",
                  "loss": "0.01 BCEWithLogits(win) + 0.01 MSE(sigmoid(progress))",
                  "inference": "ignored; full legal catalog action logits only"},
}


def target_spec(enabled: bool) -> dict[str, Any]:
    return VALUE_TARGET_SPEC if enabled else TARGET_SPEC


def from_run_facts(facts: dict[str, Any]) -> tuple[float | None, float | None]:
    # A public terminal frame alone is insufficient: recording/technical stops
    # must not become negative labels. Native OnEnded is the positive witness.
    if (facts.get("native_end") is not True or facts.get("fact_conflict")
            or facts.get("result") not in {"win", "loss"}
            or facts.get("technical_failure") is True or facts.get("abandoned") is True):
        return None, None
    win = float(facts["result"] == "win")
    level, floor = facts.get("ascension"), facts.get("floor_reached")
    finish = VALUE_TARGET_SPEC["auxiliary"]["finish_floors"].get(str(level))
    progress = (1.0 if win else min(floor / finish, 1.0)
                if type(floor) is int and floor >= 0 and finish else None)
    return win, progress


def supervision_facts(projection: Any) -> dict[str, Any]:
    """Read native abandonment witness without relabelling cohort outcomes."""
    from .cohort import projection_facts

    facts = projection_facts(projection)
    facts["abandoned"] = any(
        e.get("run_id") == projection.value["branch"]["run_id"]
        and e.get("kind") == "run_abandoned_native"
        for e in projection.source.parameters.value().get("run_journal", [])
    )
    return facts
