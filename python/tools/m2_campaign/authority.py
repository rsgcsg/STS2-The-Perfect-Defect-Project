"""File-backed operator authorization with immutable grants and fresh observations.

Files are supplied by the external operator; this module never issues a grant or
collects credentials. Hash pins detect accidental drift, not hostile same-user edits.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .config import Settings, bounded_file, canonical, digest, fields, sha
from .core import Attempt, PendingApproval
from .journal import CampaignJournal


def money(value: object) -> Decimal:
    if type(value) is not str:
        raise ValueError("decimal_money_string_required")
    try:
        amount = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("invalid_money") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError("invalid_money")
    return amount


def timestamp(value: object) -> dt.datetime:
    if type(value) is not str:
        raise ValueError("utc_timestamp_required")
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp_timezone_required")
    return result.astimezone(dt.UTC)


class FileAuthority:
    def __init__(self, settings: Settings, journal: CampaignJournal):
        self.settings = settings
        self.journal = journal
        self.root = Path(settings.value["approval_dir"])
        limits = settings.value["limits"]
        self.batch_limit = money(limits["batch_raw_usd"])
        self.workspace_limit = money(limits["workspace_cap_usd"])
        self.external = money(limits["external_exposure_usd"])
        if not 0 < self.batch_limit <= self.workspace_limit or self.external >= self.batch_limit:
            raise ValueError("invalid_budget_envelope")

    def _grant(self, attempt: Attempt, request_sha256: str, raw: bytes) -> dict[str, Any]:
        value = fields(json.loads(raw), {
            "schema", "config_sha256", "attempt", "request_sha256", "producer",
            "provider_adapter_sha256", "raw_ceiling_usd", "expires_at_utc", "provider",
        })
        if (value["schema"] != "stpd/m2-campaign-attempt-grant-v1"
                or value["config_sha256"] != self.settings.identity
                or value["attempt"] != asdict(attempt)
                or value["request_sha256"] != digest(request_sha256)
                or value["producer"] != self.settings.value["producer"]
                or value["provider_adapter_sha256"]
                != self.settings.value["provider_adapter"]["sha256"]
                or type(value["provider"]) is not dict):
            raise ValueError("approval_binding_mismatch")
        ceiling = money(value["raw_ceiling_usd"])
        if ceiling <= 0:
            raise ValueError("positive_raw_reservation_required")
        return value

    def _read_operator_grant(self, attempt: Attempt) -> bytes:
        grant_path = self.root / f"{attempt.attempt_id}.grant.json"
        pin_path = self.root / f"{attempt.attempt_id}.approved-sha256"
        if not grant_path.exists() or not pin_path.exists():
            raise PendingApproval("exact_operator_grant_required")
        raw = bounded_file(grant_path, 1024 * 1024)
        pin = bounded_file(pin_path, 128).decode().strip()
        if sha(raw) != digest(pin):
            raise ValueError("operator_grant_digest_mismatch")
        return raw

    def _all_reserved(self) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for record in self.journal.records():
            if record["approval"] is None:
                continue
            grant = json.loads(self.journal.read(record["approval"], limit=1024 * 1024))
            version_raw = self.journal.settings_for(record)
            version = self.settings if version_raw is None else Settings.decode(version_raw)
            attempt = {key: record[key] for key in ("attempt_id", "run_id", "ordinal", "slice")}
            if (grant["config_sha256"] != version.identity
                    or grant["attempt"] != attempt
                    or grant["producer"] != version.value["producer"]
                    or grant["provider_adapter_sha256"]
                    != version.value["provider_adapter"]["sha256"]
                    or grant["request_sha256"] != record["request"]["sha256"]):
                raise ValueError("durable_reservation_binding_mismatch")
            result[digest(record["attempt_id"], 32)] = money(grant["raw_ceiling_usd"])
        return result

    def authorize(self, attempt: Attempt, request_sha256: str) -> bytes:
        raw = self._read_operator_grant(attempt)
        value = self._grant(attempt, request_sha256, raw)
        self._fresh(attempt, request_sha256, raw)
        reservations = self._all_reserved()
        ceiling = money(value["raw_ceiling_usd"])
        previous = reservations.get(attempt.attempt_id)
        if previous is not None and previous != ceiling:
            raise ValueError("reservation_changed")
        reservations[attempt.attempt_id] = ceiling
        if self.external + sum(reservations.values(), Decimal(0)) > self.batch_limit:
            raise ValueError("campaign_raw_budget_exceeded")
        return raw

    def _fresh(self, attempt: Attempt, request_sha256: str, approval: bytes) -> None:
        now = dt.datetime.now(dt.UTC)
        grant = self._grant(attempt, request_sha256, approval)
        if now > timestamp(grant["expires_at_utc"]):
            raise ValueError("operator_grant_expired")
        location = self.root / f"{attempt.attempt_id}.fresh.json"
        if not location.exists():
            raise PendingApproval("fresh_operator_observation_required")
        value = fields(json.loads(bounded_file(location, 1024 * 1024)), {
            "schema", "config_sha256", "attempt_id", "request_sha256", "grant_sha256",
            "observed_at_utc", "external_active_gpu",
            "workspace_raw_exposure_excluding_this_attempt_usd",
            "account_receipt", "billing_receipt",
        })
        if (value["schema"] != "stpd/m2-campaign-fresh-observation-v1"
                or value["config_sha256"] != self.settings.identity
                or value["attempt_id"] != attempt.attempt_id
                or value["request_sha256"] != request_sha256
                or value["grant_sha256"] != sha(approval)
                or type(value["external_active_gpu"]) is not int
                or value["external_active_gpu"] != 0):
            raise ValueError("fresh_observation_binding_mismatch")
        age = (now - timestamp(value["observed_at_utc"])).total_seconds()
        if not 0 <= age <= 300:
            raise PendingApproval("fresh_operator_observation_expired")
        for key in ("account_receipt", "billing_receipt"):
            reference = fields(value[key], {"path", "sha256"})
            location = Path(reference["path"])
            if not location.is_absolute() or location.parent.resolve() != self.root.resolve():
                raise ValueError("external_observation_reference_invalid")
            if sha(bounded_file(location, 4 * 1024 * 1024)) != digest(reference["sha256"]):
                raise ValueError("external_observation_digest_mismatch")
        if (money(value["workspace_raw_exposure_excluding_this_attempt_usd"])
                + money(grant["raw_ceiling_usd"])
                > self.workspace_limit):
            raise ValueError("workspace_raw_budget_exceeded")

    def validate_before_submit(self, attempt: Attempt, request_sha256: str,
                               approval: bytes) -> None:
        if self._read_operator_grant(attempt) != approval:
            raise ValueError("immutable_operator_grant_changed")
        self._fresh(attempt, request_sha256, approval)

    def request_notice(self, attempt: Attempt, request_sha256: str) -> bytes:
        return canonical({"schema": "stpd/m2-campaign-approval-request-v1",
                          "config_sha256": self.settings.identity,
                          "attempt": asdict(attempt), "request_sha256": request_sha256,
                          "producer": self.settings.value["producer"],
                          "provider_adapter_sha256":
                          self.settings.value["provider_adapter"]["sha256"]})
