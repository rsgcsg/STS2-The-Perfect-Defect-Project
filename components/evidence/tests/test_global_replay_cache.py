"""Real inventory/context checks; only the expensive Node semantic seam is mocked."""
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sts2_platform_evidence.agent_run_evidence import AgentRunEvidenceError, _canonical_json
from sts2_platform_evidence import global_agent_session_run_evidence as owner


def encoded(value):
    return (_canonical_json(value) + "\n").encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class GlobalReplayCacheTests(unittest.TestCase):
    def setUp(self):
        owner._SEMANTIC_REPLAY_CACHE.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / "original"
        self.directory.mkdir()
        runtime = self.root / "trusted-runtime"
        runtime.mkdir()
        (runtime / "global-replay.js").write_bytes(b"// mocked semantic seam\n")
        code = sha(b"global-replay.js\0// mocked semantic seam\n\0")
        agent = {"input": {"profile": "p1-global-r1", "global": {"core_sha256": "b" * 64}}}
        run = {"schema": "sts2.policy-runtime/agent-session-run-1", "run_id": "run-cache-test",
               "manifest_id": "fixture", "agent_manifest_sha256": sha(_canonical_json(agent).encode()),
               "agent_id": "fixture", "agent_version": "1", "agent_artifact_sha256": "a" * 64,
               "runtime_version": "fixture", "runtime_code_sha256": code,
               "started_at": "2026-10-24T09:00:00Z", "ended_at": "2026-10-24T09:01:00Z",
               "status": "stopped", "mode": "human", "tainted": False, "append_only": True}
        (self.directory / "manifest.json").write_bytes(encoded(run))
        (self.directory / "agent-manifest.json").write_bytes(encoded(agent))
        (self.directory / "adapter-attestation.json").write_bytes(encoded({}))
        (self.directory / "events.jsonl").write_bytes(encoded({
            "schema": "sts2.policy-runtime/agent-session-event-1", "kind": "stopped", "payload": {},
        }))
        files = [{"path": p.name, "bytes": p.stat().st_size, "sha256": sha(p.read_bytes())}
                 for p in sorted(self.directory.iterdir())]
        seal = {"schema": "sts2.policy-runtime/immutable-evidence-manifest-1", "run_id": run["run_id"],
                "complete": True, "append_only": True, "files": files,
                "manifest_sha256": sha(_canonical_json({"run_id": run["run_id"], "files": files}).encode())}
        (self.directory / "evidence-manifest.json").write_bytes(encoded(seal))
        (self.directory / "checksums.sha256").write_text("".join(
            f"{sha(p.read_bytes())}  {p.name}\n" for p in sorted(self.directory.iterdir())
        ))
        self.expected = {"global_runtime_directory": str(runtime)}
        report = {"schema": "sts2.policy-runtime/global-replay-1", "run_id": run["run_id"],
                  "verdict": "verified", "native_qualification": False,
                  "human_origin": False, "research_admission": False}
        self.completed = SimpleNamespace(returncode=0, stdout=json.dumps(report))
        self.identity = {"schema": "sts2.policy-runtime/global-replay-code-1",
                         "runtime_code_sha256": code,
                         "semantic_dependencies_sha256": "c" * 64,
                         "core": {"module_digest": "b" * 64}}
        self.semantic_calls = 0

    def execute(self, command, **kwargs):
        if "globalReplaySemanticIdentity" in command[3]:
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.identity))
        self.semantic_calls += 1
        return self.completed

    def test_identical_restored_copy_reuses_semantics_but_not_integrity_or_context_checks(self):
        restored = self.root / "restored"
        shutil.copytree(self.directory, restored)
        with patch.object(owner.subprocess, "run", side_effect=self.execute) as replay:
            first = owner.verify_global_session(self.directory, self.expected)
            second = owner.verify_global_session(restored, self.expected)
            self.assertEqual(first.content_id, second.content_id)
            self.assertEqual(self.semantic_calls, 1)
            self.assertEqual(next(c.kwargs["timeout"] for c in replay.call_args_list
                                  if "globalReplaySemanticIdentity" not in c.args[0][3]), 120)
            with self.assertRaises(AgentRunEvidenceError):
                owner.verify_global_session(restored, {**self.expected, "run_id": "foreign-run"})
            (restored / "events.jsonl").write_bytes(b"changed after cached proof\n")
            with self.assertRaises(AgentRunEvidenceError):
                owner.verify_global_session(restored, self.expected)
            self.assertEqual(self.semantic_calls, 1)

    def test_failed_semantic_verdict_is_never_cached(self):
        completed = self.completed
        self.completed = SimpleNamespace(returncode=1, stdout="")
        with patch.object(owner.subprocess, "run", side_effect=self.execute):
            with self.assertRaises(AgentRunEvidenceError):
                owner.verify_global_session(self.directory, self.expected)
            self.completed = completed
            owner.verify_global_session(self.directory, self.expected)
            self.assertEqual(self.semantic_calls, 2)

    def test_changed_sdk_dependency_or_current_core_cannot_reuse_old_success(self):
        with patch.object(owner.subprocess, "run", side_effect=self.execute):
            owner.verify_global_session(self.directory, self.expected)
            self.identity["core"]["module_digest"] = "d" * 64
            with self.assertRaises(AgentRunEvidenceError):
                owner.verify_global_session(self.directory, self.expected)
            self.assertEqual(self.semantic_calls, 1)
            self.identity["core"]["module_digest"] = "b" * 64
            self.identity["semantic_dependencies_sha256"] = "e" * 64
            self.completed = SimpleNamespace(returncode=1, stdout="")
            with self.assertRaises(AgentRunEvidenceError):
                owner.verify_global_session(self.directory, self.expected)
            self.assertEqual(self.semantic_calls, 2)

    def test_legacy_runtime_without_dependency_identity_always_replays(self):
        self.identity = None
        with patch.object(owner.subprocess, "run", side_effect=self.execute):
            owner.verify_global_session(self.directory, self.expected)
            owner.verify_global_session(self.directory, self.expected)
            self.assertEqual(self.semantic_calls, 2)
