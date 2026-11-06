"""Background sampled replay of already preserved originals; no gameplay authority."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from spireagent.json_boundary import json_bytes
from spireagent.storage.local import LocalBlobStore

# Operational inability to finish replay is not a semantic counterexample.
INCONCLUSIVE = frozenset(
    {"global_shared_replay_unavailable", "global_shared_replay_identity_unavailable"}
)


class SamplingAudits:
    def __init__(
        self, exchange: Any, actor: str, budget_id: str | None, fail: Callable[[str], None]
    ) -> None:
        self.exchange, self.actor, self.budget_id, self.fail = exchange, actor, budget_id, fail
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sampled-audit")
        self.futures: list[Any] = []

    def submit(
        self,
        row: dict[str, Any],
        artifacts: Any,
        run: Path,
        expected: dict[str, Any],
        content_id: str,
        release: Callable[[], None],
    ) -> None:
        if row.get("sampling_audit"):
            return  # Preserve the original pending/result identity on save-only recovery.
        result_name = "sampling-audit-result.json"
        row["sampling_audit"] = artifacts.put(
            "sampling-audit-request.json",
            json_bytes({"status": "pending", "run_id": row["run_id"], "result_path": result_name}),
        )
        self.exchange.publish_event(
            "sampling_audit_scheduled",
            actor=self.actor,
            budget_id=self.budget_id,
            run_id=row["run_id"],
        )
        self.futures.append(
            self.pool.submit(
                self._run,
                artifacts.root,
                run,
                dict(expected),
                row["run_id"],
                content_id,
                release,
                result_name,
            )
        )

    def _run(
        self,
        root: Path,
        run: Path,
        expected: dict[str, Any],
        run_id: str,
        content_id: str,
        release: Callable[[], None],
        result_name: str,
    ) -> None:
        from sts2_platform_evidence.global_agent_session_run_evidence import audit_global_session

        began = time.monotonic()
        try:
            audited = audit_global_session(run, expected)
            findings = [{"code": f.code, "detail": f.detail} for f in audited.findings]
            codes = {f.code for f in audited.findings}
            if codes and codes <= INCONCLUSIVE:
                status = "audit_inconclusive"
            elif audited.passed and audited.value is not None:
                status = "pass" if audited.value.content_id == content_id else "fail"
                if status == "fail":
                    findings.append(
                        {
                            "code": "audit_content_id_changed",
                            "detail": "audited content differs from typed verification",
                        }
                    )
            else:
                status = "fail"
        # An execution failure is not a semantic counterexample.
        except Exception as error:
            status = "audit_inconclusive"
            findings = [{"code": type(error).__name__, "detail": "audit worker unavailable"}]
        result = {
            "status": status,
            "run_id": run_id,
            "findings": findings,
            "seconds": time.monotonic() - began,
            "typed_content_id": content_id,
        }
        raw = json_bytes(result)
        LocalBlobStore(root).put_if_absent(result_name, raw)
        self.exchange.publish_event(
            "sampling_audit",
            actor=self.actor,
            budget_id=self.budget_id,
            run_id=run_id,
            status=status,
            result_sha256=hashlib.sha256(raw).hexdigest(),
        )
        if status == "fail":
            self.fail("sampled_original_audit_failed")
        elif status == "pass":
            release()
        # Inconclusive and failed originals remain available for offline diagnosis.

    def close(self) -> None:
        self.pool.shutdown(wait=True)
        for future in self.futures:
            future.result()
