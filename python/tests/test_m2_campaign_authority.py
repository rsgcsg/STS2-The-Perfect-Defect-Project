"""Fast file-authority boundary tests; synthetic bytes only, no worker or provider."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tools.m2_campaign.authority import FileAuthority, PendingApproval
from tools.m2_campaign.config import Settings
from tools.m2_campaign.core import Attempt, NextSlice
from tools.m2_campaign.journal import CampaignJournal


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


def _settings(root: Path, *, batch_limit: str = "1.00") -> Settings:
    value = {
        "producer": {
            "repository": "synthetic/test",
            "source_revision": "a" * 40,
            "uv_lock_sha256": "b" * 64,
        },
        "provider_adapter": {"path": str(root / "adapter.py"), "sha256": "c" * 64},
        "limits": {
            "batch_raw_usd": batch_limit,
            "workspace_cap_usd": "10.00",
            "external_exposure_usd": "0.00",
        },
        "approval_dir": str(root / "approval"),
    }
    raw = _canonical(value)
    value["approval_dir"] = str(root / "approval")
    return Settings(raw, value, ())


def _operator_files(
    settings: Settings, attempt: Attempt, request_sha256: str, *,
    ceiling: str = "0.60", expires_at: datetime | None = None,
    observed_at: datetime | None = None,
    provider: dict | None = None,
) -> bytes:
    root = Path(settings.value["approval_dir"])
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    grant = {
        "schema": "stpd/m2-campaign-attempt-grant-v1",
        "config_sha256": settings.identity,
        "attempt": asdict(attempt),
        "request_sha256": request_sha256,
        "producer": settings.value["producer"],
        "provider_adapter_sha256": settings.value["provider_adapter"]["sha256"],
        "raw_ceiling_usd": ceiling,
        "expires_at_utc": (expires_at or now + timedelta(hours=1)).isoformat().replace(
            "+00:00", "Z"),
        "provider": {"synthetic": True} if provider is None else provider,
    }
    raw = _canonical(grant)
    (root / f"{attempt.attempt_id}.grant.json").write_bytes(raw)
    (root / f"{attempt.attempt_id}.approved-sha256").write_text(
        hashlib.sha256(raw).hexdigest() + "\n", encoding="ascii",
    )
    account_raw = _canonical({"schema": "synthetic-account-v1", "active_gpu": 0})
    billing_raw = _canonical({"schema": "synthetic-billing-v1", "exposure_usd": "0.00"})
    account = root / f"{attempt.attempt_id}.account.json"
    billing = root / f"{attempt.attempt_id}.billing.json"
    account.write_bytes(account_raw)
    billing.write_bytes(billing_raw)
    fresh = {
        "schema": "stpd/m2-campaign-fresh-observation-v1",
        "config_sha256": settings.identity,
        "attempt_id": attempt.attempt_id,
        "request_sha256": request_sha256,
        "grant_sha256": hashlib.sha256(raw).hexdigest(),
        "observed_at_utc": (observed_at or now).isoformat().replace("+00:00", "Z"),
        "external_active_gpu": 0,
        "workspace_raw_exposure_excluding_this_attempt_usd": "0.00",
        "account_receipt": {
            "path": str(account), "sha256": hashlib.sha256(account_raw).hexdigest(),
        },
        "billing_receipt": {
            "path": str(billing), "sha256": hashlib.sha256(billing_raw).hexdigest(),
        },
    }
    (root / f"{attempt.attempt_id}.fresh.json").write_bytes(_canonical(fresh))
    return raw


def _attempt(ordinal: int) -> Attempt:
    return Attempt(
        f"{ordinal:032x}", "d" * 64, ordinal,
        NextSlice(None if ordinal == 1 else "e" * 64, 2, ordinal, False),
    )


def test_file_authority_rejects_expired_grant_and_stale_fresh_observation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    journal = CampaignJournal(tmp_path / "journal")
    attempt = _attempt(1)
    request_sha = hashlib.sha256(b"synthetic-request").hexdigest()
    with journal.session({"authority-test": True}):
        authority = FileAuthority(settings, journal)
        _operator_files(
            settings, attempt, request_sha,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        with pytest.raises(ValueError, match="operator_grant_expired"):
            authority.authorize(attempt, request_sha)

        _operator_files(
            settings, attempt, request_sha,
            observed_at=datetime.now(UTC) - timedelta(seconds=301),
        )
        with pytest.raises(PendingApproval, match="fresh_operator_observation_expired"):
            authority.authorize(attempt, request_sha)


def test_file_authority_reloads_prior_budget_reservation_from_journal(tmp_path: Path) -> None:
    settings = _settings(tmp_path, batch_limit="1.00")
    journal = CampaignJournal(tmp_path / "journal")
    first, second = _attempt(1), _attempt(2)
    first_request, second_request = b"synthetic-first", b"synthetic-second"
    first_sha = hashlib.sha256(first_request).hexdigest()
    second_sha = hashlib.sha256(second_request).hexdigest()
    with journal.session({"authority-test": True}):
        first_authority = FileAuthority(settings, journal)
        first_grant = _operator_files(settings, first, first_sha, ceiling="0.60")
        assert first_authority.authorize(first, first_sha) == first_grant

        record = {
            **asdict(first),
            "phase": "deploy_intent",
            "request": journal.put(first_request),
            "approval": journal.put(first_grant),
            "target": None,
            "handle": None,
            "result": None,
            "stop": None,
        }
        journal.save([record])
        restarted_authority = FileAuthority(settings, journal)
        _operator_files(settings, second, second_sha, ceiling="0.60")
        with pytest.raises(ValueError, match="campaign_raw_budget_exceeded"):
            restarted_authority.authorize(second, second_sha)
