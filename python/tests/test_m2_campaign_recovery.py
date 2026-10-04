"""Metadata/protocol recovery tests; no owner, Torch, weights or cloud imports."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from m2_recovery_fixture import packet, reference, replacement_settings, synthetic_settings
from test_m2_campaign_authority import _operator_files
from test_m2_campaign_core import SyntheticBackend, SyntheticProvider

from tools.m2_campaign.authority import FileAuthority
from tools.m2_campaign.config import Settings, canonical, sha
from tools.m2_campaign.core import Campaign, CampaignConfig, RunSpec
from tools.m2_campaign.journal import CampaignJournal, JournalError
from tools.m2_campaign.recovery import core_config, recover


def failed_prepare(root: Path, *, runtime_pin: bool = True):
    before = synthetic_settings(root)
    journal = CampaignJournal(Path(before.value["journal_root"]))
    backend = SyntheticBackend()
    provider = SyntheticProvider(backend)
    provider.prepare_unknown = True
    campaign = Campaign(
        CampaignConfig((RunSpec(before.runs[0].run_id, 6),)), journal,
        backend, provider, FileAuthority(before, journal), execution_settings=before.raw,
    )
    with campaign.session():
        waiting = campaign.tick()
        row = journal.records()[0]
        assert waiting.status == "waiting_approval"
        evidence_dir = root / "runtime-evidence"
        evidence_dir.mkdir()
        runtime = reference(evidence_dir, "runtime.json", before.value["runtime"])
        provider_settings: dict[str, object] = {"synthetic": True}
        if runtime_pin:
            provider_settings["runtime_receipt"] = runtime
        _operator_files(before, waiting.attempt, row["request"]["sha256"], ceiling="0.50",
                        provider=provider_settings)
        assert campaign.tick().status == "blocked"
    after = replacement_settings(before)
    location, receipt = packet(after, root / "repair")
    return before, after, journal, backend, provider, location, receipt


def apply(after, journal, location):
    raw = location.read_bytes()
    return recover(after, journal, raw, sha(raw))


def test_transition_replacement_restart_progress_and_all_holds(tmp_path: Path):
    before, after, journal, backend, provider, location, receipt = failed_prepare(tmp_path)
    originals = {name: (journal.root / name).read_bytes()
                 for name in ("settings.json", "state.json", "config.json")}
    history = {item.name: item.read_bytes() for item in (journal.root / "history").iterdir()}
    event = apply(after, journal, location)
    assert event["historical_cost"] == "unknown" and event["hold_retained"]
    assert apply(after, CampaignJournal(journal.root), location) == event
    assert len(list((journal.root / "recoveries").iterdir())) == 1
    for name, raw in originals.items():
        assert (journal.root / name).read_bytes() == raw
    for name, raw in history.items():
        assert (journal.root / "history" / name).read_bytes() == raw
    with (pytest.raises(JournalError, match="execution_settings_not_current"),
          journal.session(core_config(before), settings_bytes=before.raw)):
        pass
    provider.prepare_unknown = False
    restarted = CampaignJournal(journal.root)
    authority = FileAuthority(after, restarted)
    campaign = Campaign(CampaignConfig((RunSpec(after.runs[0].run_id, 6),)), restarted,
                        backend, provider, authority, execution_settings=after.raw)
    with campaign.session():
        notice = campaign.tick()
        assert notice.attempt.ordinal == 2
        rows = restarted.records()
        assert rows[1]["replacement_for"] == rows[0]["attempt_id"]
        assert rows[1]["slice"] == rows[0]["slice"]
        assert rows[1]["attempt_id"] != rows[0]["attempt_id"]
        assert authority._all_reserved() == {rows[0]["attempt_id"]: Decimal(".50")}
    # A process restart after consuming the permit retains that same new UUID.
    with campaign.session():
        assert campaign.tick().attempt == notice.attempt
        for _ in range(6):
            pending = campaign.tick()
            assert pending.status == "waiting_approval"
            row = restarted.records()[-1]
            _operator_files(after, pending.attempt, row["request"]["sha256"], ceiling="0.50")
            accepted = campaign.tick()
            assert accepted.status == "accepted"
        assert campaign.tick().status == "completed"
        rows = restarted.records()
        assert len(rows) == 7 and [row["ordinal"] for row in rows] == list(range(1, 8))
        assert len(authority._all_reserved()) == 7
        assert str(sum(authority._all_reserved().values())) == "3.50"
        assert restarted.accepted(rows[0]) is None
        assert restarted.accepted(rows[-1])["completed_epochs"] == 5
        assert rows[0] == event["failed_attempt"]
    assert provider.prepares == 7 and provider.submits == 6


@pytest.mark.parametrize("change", [
    "run", "input", "producer", "seed_config", "budget_scope", "limit", "runtime",
])
def test_transition_cannot_change_numerical_or_budget_identity(tmp_path: Path, change: str):
    _, after, journal, _, _, location, _ = failed_prepare(tmp_path)
    value = json.loads(after.raw)
    if change == "run":
        value["runs"][0]["run_id"] = "f" * 64
    elif change == "input":
        value["runs"][0]["input_identity"] = "f" * 64
    elif change == "producer":
        value["producer"]["source_revision"] = "f" * 40
    elif change == "seed_config":
        value["runs"][0]["config_sha256"] = "f" * 64
    elif change == "budget_scope":
        value["budget_scope_id"] = "f" * 32
    elif change == "limit":
        value["limits"]["batch_raw_usd"] = "21.00"
    else:
        value["runtime"]["cpu_threads"] = 2
    changed = Settings.decode(canonical(value))
    with pytest.raises((ValueError, JournalError)):
        apply(changed, journal, location)
    assert not (journal.root / "recoveries").exists()


@pytest.mark.parametrize("change", [
    "missing_hold", "wrong_hold", "request", "snapshot", "root_pin", "expired",
    "closure_missing", "closure_wrong", "incomplete", "active", "identity", "unknown",
    "local_mutation", "local_target", "local_unlisted", "local_diagnostic",
    "local_failure", "submit_history",
    "runtime_mismatch", "runtime_missing",
])
def test_recovery_negative_evidence_boundaries(tmp_path: Path, change: str):
    _, after, journal, _, _, location, receipt = failed_prepare(
        tmp_path, runtime_pin=change != "runtime_missing",
    )
    if change == "missing_hold":
        receipt["reservations"] = []
    elif change == "wrong_hold":
        receipt["retained_raw_ceiling_usd"] = "0.00"
    elif change == "request":
        receipt["request_sha256"] = "0" * 64
    elif change == "snapshot":
        receipt["before_state_sha256"] = "0" * 64
    elif change == "expired":
        receipt["expires_at_utc"] = "2000-01-01T00:00:00Z"
    elif change == "runtime_mismatch":
        value = json.loads(Path(receipt["local_prepare_evidence"]["path"]).read_bytes())
        directory = Path(value["directory"])
        raw = canonical({"wrong_runtime": True})
        (directory / "runtime.json").write_bytes(raw)
        for row in value["files"]:
            if row["name"] == "runtime.json":
                row.update(sha256=sha(raw), size=len(raw))
        receipt["local_prepare_evidence"] = reference(location.parent, "local-new.json", value)
    elif change.startswith("closure"):
        value = json.loads(Path(receipt["implementation_closure"]["path"]).read_bytes())
        if change == "closure_missing":
            value["files"] = value["files"][:-1]
        else:
            value["files"][0]["sha256"] = "0" * 64
        receipt["implementation_closure"] = reference(location.parent, "closure-new.json", value)
    elif change in {"incomplete", "active", "identity", "unknown"}:
        value = json.loads(Path(receipt["provider_observation"]["path"]).read_bytes())
        if change == "incomplete":
            value["complete"] = False
        elif change == "active":
            value["active_gpu"] = 1
        elif change == "identity":
            value["provider"] = {"wrong_workspace": True}
        else:
            value["exact_name_absent"] = None
        receipt["provider_observation"] = reference(location.parent, "observation-new.json", value)
    elif change.startswith("local"):
        directory = journal.root / "provider" / receipt["attempt"]["attempt_id"]
        name = {
            "local_mutation": "provider-mutation-intent.json", "local_target": "target.json",
            "local_unlisted": "prepare-phase-0001.json",
            "local_diagnostic": "prepare-phase-0001.json",
            "local_failure": "prepare-failure-0001.json",
        }[change]
        content = ({"phase": "provider_mutation_boundary"} if change == "local_diagnostic"
                   else {"provider_mutation_possible": True} if change == "local_failure"
                   else {"synthetic": True})
        (directory / name).write_bytes(canonical(content))
        if change != "local_unlisted":
            value = json.loads(Path(receipt["local_prepare_evidence"]["path"]).read_bytes())
            value["files"].append({"name": name, "sha256": sha((directory / name).read_bytes()),
                                   "size": (directory / name).stat().st_size})
            receipt["local_prepare_evidence"] = reference(location.parent, "local-new.json", value)
    elif change == "submit_history":
        # A contradictory historical intent is never hidden by a later rollback.
        history = sorted((journal.root / "history").iterdir())[0]
        old = json.loads(history.read_bytes())
        old["attempts"] = [json.loads((journal.root / "state.json").read_bytes())["attempts"][0]]
        old["attempts"][0]["phase"] = "submit_intent"
        history.write_bytes(canonical(old))
    location.write_bytes(canonical(receipt))
    with pytest.raises((ValueError, JournalError)):
        if change == "root_pin":
            recover(after, journal, location.read_bytes(), "0" * 64)
        else:
            apply(after, journal, location)
    assert not (journal.root / "recoveries").exists()


def test_crash_before_commit_has_no_permit_and_retry_is_idempotent(tmp_path: Path):
    _, after, journal, _, _, location, _ = failed_prepare(tmp_path)
    from tools.m2_campaign import recovery
    original = recovery._write
    def interrupted(path, raw, **options):
        if path.parent.name == "recoveries":
            raise OSError("synthetic_commit_interruption")
        return original(path, raw, **options)
    with (patch.object(recovery, "_write", interrupted),
          pytest.raises(OSError, match="synthetic_commit_interruption")):
        apply(after, journal, location)
    with journal.session(core_config(after), settings_bytes=after.raw, recovery=True):
        assert journal.recoveries() == []
        assert journal.replacement_for(after.runs[0].run_id, journal.records()) is None
    apply(after, journal, location)


def test_different_reconcile_and_duplicate_or_unlinked_replacement_rejected(tmp_path: Path):
    _, after, journal, _, _, location, receipt = failed_prepare(tmp_path)
    apply(after, journal, location)
    receipt["issued_at_utc"] = "2000-01-01T00:00:00Z"
    location.write_bytes(canonical(receipt))
    with pytest.raises(ValueError):
        apply(after, journal, location)
    with journal.session(core_config(after), settings_bytes=after.raw):
        rows = journal.records()
        old = rows[0]
        new = {**old, "attempt_id": "a" * 32, "ordinal": 2,
               "phase": "waiting_approval", "approval": None}
        journal.save(rows + [new])
        with pytest.raises(JournalError, match="replacement_permit_required"):
            journal.records()
        new["replacement_for"] = old["attempt_id"]
        duplicate = {**new, "attempt_id": "b" * 32, "ordinal": 3}
        journal.save(rows + [new, duplicate])
        with pytest.raises(JournalError, match="replacement_permit_invalid_or_used"):
            journal.records()


def test_corrupt_request_blocks_session_and_cli_before_owner_bootstrap_or_provider(tmp_path: Path):
    _, after, journal, backend, provider, location, receipt = failed_prepare(tmp_path)
    failed = receipt["attempt"]
    state = json.loads((journal.root / "state.json").read_bytes())
    request = journal.root / "objects" / state["attempts"][0]["request"]["sha256"]
    raw = request.read_bytes()
    request.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    # Metadata-only reconciliation never reads or decodes the request payload.
    apply(after, journal, location)
    authority = FileAuthority(after, journal)
    campaign = Campaign(CampaignConfig((RunSpec(after.runs[0].run_id, 6),)), journal,
                        backend, provider, authority, execution_settings=after.raw)
    sessions, prepares = backend.sessions, provider.prepares
    with (pytest.raises(JournalError, match="object_corrupt"), campaign.session()):
        pass
    assert backend.sessions == sessions and provider.prepares == prepares
    from tools.m2_campaign import cli
    config_path = tmp_path / "new-config.json"
    config_path.write_bytes(after.raw)
    with patch.object(cli, "_bootstrap") as bootstrap, patch.object(cli, "_provider") as factory:
        assert cli.main(["--config", str(config_path), "execute"]) == 1
        bootstrap.assert_not_called()
        factory.assert_not_called()
    assert state["attempts"][0]["attempt_id"] == failed["attempt_id"]
