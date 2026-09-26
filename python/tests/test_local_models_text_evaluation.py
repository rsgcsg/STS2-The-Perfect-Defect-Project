"""Operational counts from real, sealed synthetic text Runtime evidence."""

import json
import shutil

import pytest
from test_local_models import service as service
from test_text_menu_runtime_import import verified_fixture as verified_fixture

from spireagent.live_evaluation import EXPECTED


@pytest.mark.parametrize("outcome", ["navigation", "delivered", "not_delivered", "unknown"])
def test_evaluation_counts_verified_native_results(service, verified_fixture, outcome):
    directory = verified_fixture._text_evidence("text-evaluation", native=outcome != "navigation")
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    if outcome in {"unknown", "not_delivered"}:
        events[5]["kind"] = (
            "text_native_unknown" if outcome == "unknown" else "text_menu_not_applied"
        )
        events[5]["payload"]["result"].update(
            status="unknown" if outcome == "unknown" else "not_applied",
            native_delivery=outcome, successor=None,
            retry="never" if outcome == "unknown" else "reobserve",
        )
        events.pop(6)  # Neither outcome has a delivered action's observed successor.
        for index, event in enumerate(events, 1):
            event["sequence"] = index
        if outcome == "unknown":
            path = directory / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest.update(status="tainted", tainted=True)
            path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        verified_fixture._rewrite_events(directory, events)
    manifest = json.loads((directory / "manifest.json").read_text())
    shutil.copytree(directory, service.directory / "agent-runs" / manifest["run_id"])
    service.state.update(
        startup={key: manifest[key] for key in EXPECTED}, selection_id="synthetic-text",
    )
    service._evaluation_handoff()
    report = service.state["evaluation"]
    assert report["evidence_verification"] == "pass", report["findings"]
    assert report["delivery_counts"] == ({} if outcome == "navigation" else {outcome: 1})
    assert report["game_outcome"] == "not_measured"
    assert report["training_admission"] == "not_claimed"
