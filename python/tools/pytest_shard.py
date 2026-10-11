"""Explicit top-level pytest partition and original execution diagnostics."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest


def normalized_nodeid(nodeid: str) -> str:
    file, separator, name = nodeid.partition("::")
    file = file.replace("\\", "/")
    if (not separator or not name or file.startswith("/") or ":" in file
            or any(part in ("", ".", "..") for part in file.split("/"))):
        raise ValueError(f"invalid relative pytest node ID: {nodeid!r}")
    return file + separator + name


def selected_ids(collection: list[dict[str, str]], index: int, count: int) -> list[str]:
    if count != 2 or index not in (1, 2):
        raise ValueError("portable pytest requires shard 1/2 or 2/2")
    files = sorted({item["file"] for item in collection})
    selected = {file for rank, file in enumerate(files) if rank % count == index - 1}
    return sorted(item["nodeid"] for item in collection if item["file"] in selected)


def source_identity(root: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=root, text=True,
                                       encoding="utf-8").strip()

    return {"head": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
            "workflow": git("rev-parse", "HEAD:.github/workflows/ci.yml"),
            "selector": git("rev-parse", "HEAD:python/tools/pytest_shard.py"),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=normal"))}


def now() -> str:
    return datetime.now(UTC).isoformat()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("portable shard")
    group.addoption("--portable-shard-index", type=int)
    group.addoption("--portable-shard-count", type=int)
    group.addoption("--portable-shard-manifest")


def pytest_configure(config: pytest.Config) -> None:
    index = config.getoption("--portable-shard-index")
    count = config.getoption("--portable-shard-count")
    manifest = config.getoption("--portable-shard-manifest")
    if count != 2 or index not in (1, 2) or not manifest:
        raise pytest.UsageError("explicit shard index/count/manifest are required")
    config.pluginmanager.register(ShardRecorder(config, index, count, Path(manifest)),
                                  "portable-shard-recorder")


class ShardRecorder:
    def __init__(self, config: pytest.Config, index: int, count: int, output: Path):
        self.root = config.rootpath.resolve()
        self.output = output.resolve()
        self.records: dict[str, dict[str, Any]] = {}
        self.errors: list[str] = []
        source = source_identity(self.root)
        if source["dirty"]:
            raise pytest.UsageError("portable shard requires a clean exact checkout")
        if os.environ.get("GITHUB_SHA") != source["head"]:
            raise pytest.UsageError("portable shard checkout differs from GITHUB_SHA")
        if os.environ.get("RUNNER_OS") != platform.system():
            raise pytest.UsageError("portable shard OS differs from actual process OS")
        metadata = {key: os.environ.get(variable, "") for key, variable in (
            ("repository", "GITHUB_REPOSITORY"), ("run_id", "GITHUB_RUN_ID"),
            ("run_attempt", "GITHUB_RUN_ATTEMPT"), ("job", "GITHUB_JOB"),
            ("os", "RUNNER_OS"), ("scope", "CHECK_SCOPE"))}
        if (not all(metadata.values()) or metadata["scope"] not in ("full", "python")
                or not metadata["run_id"].isdigit() or not metadata["run_attempt"].isdigit()):
            raise pytest.UsageError("portable shard is missing exact CI metadata")
        if (config.args != ["tests", "deploy/hub"]
                or config.getini("testpaths") != ["tests", "deploy/hub"]
                or config.option.keyword or config.option.markexpr or config.option.collectonly):
            raise pytest.UsageError("portable shard must collect unchanged default testpaths")
        self.data: dict[str, Any] = {
            "schema": "spireagent/pytest-shard-1", "run": metadata,
            "shard": {"index": index, "count": count}, "source_at_start": source,
            "invocation": {"argv": sys.orig_argv, "pid": os.getpid(), "started_at": now()},
            "collection": [], "selected_nodeids": [], "executed": [],
            "collection_errors": [], "errors": self.errors, "state": "running",
        }
        self.save()

    def save(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        temporary.replace(self.output)

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_collection_modifyitems(self, config: pytest.Config,
                                      items: list[pytest.Item]) -> Generator[None, Any, Any]:
        collection = []
        for item in items:
            nodeid = normalized_nodeid(item.nodeid)
            file = item.path.resolve().relative_to(self.root).as_posix()
            if file != nodeid.partition("::")[0]:
                raise pytest.UsageError("pytest file/node ID mismatch")
            collection.append({"nodeid": nodeid, "file": file})
        collection.sort(key=lambda item: item["nodeid"])
        if len({item["nodeid"] for item in collection}) != len(collection):
            raise pytest.UsageError("duplicate collected pytest node IDs")
        self.data["collection"] = collection
        canonical = json.dumps(collection, ensure_ascii=False, separators=(",", ":"))
        self.data["collection_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        result = yield
        if sorted(normalized_nodeid(item.nodeid) for item in items) != [
            item["nodeid"] for item in collection
        ]:
            raise pytest.UsageError("another selector changed the complete pytest collection")
        selected = selected_ids(collection, **self.data["shard"])
        if not selected:
            raise pytest.UsageError("empty portable pytest shard")
        self.data["selected_nodeids"] = selected
        chosen = set(selected)
        deselected = [item for item in items if normalized_nodeid(item.nodeid) not in chosen]
        items[:] = [item for item in items if normalized_nodeid(item.nodeid) in chosen]
        config.hook.pytest_deselected(items=deselected)
        self.save()
        return result

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            self.data["collection_errors"].append(str(report.nodeid))

    def pytest_runtest_logstart(self, nodeid: str) -> None:
        nodeid = normalized_nodeid(nodeid)
        if nodeid in self.records:
            self.errors.append(f"duplicate execution: {nodeid}")
        self.records[nodeid] = {"nodeid": nodeid, "phases": [], "finished": False}

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        nodeid = normalized_nodeid(report.nodeid)
        if nodeid not in self.records:
            self.errors.append(f"report without execution: {nodeid}")
            return
        phase = {"when": report.when, "outcome": report.outcome,
                 "subtest": hasattr(report, "context"),
                 "wasxfail": getattr(report, "wasxfail", None)}
        self.records[nodeid]["phases"].append(phase)

    def pytest_runtest_logfinish(self, nodeid: str) -> None:
        record = self.records[normalized_nodeid(nodeid)]
        if record["finished"]:
            self.errors.append(f"duplicate finish: {nodeid}")
        record["finished"] = True

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_sessionfinish(self, session: pytest.Session) -> Generator[None, Any, Any]:
        result = yield
        end = source_identity(self.root)
        self.data["source_at_end"] = end
        if end != self.data["source_at_start"] or end["dirty"]:
            self.errors.append("source changed during pytest execution")
        for nodeid, record in sorted(self.records.items()):
            phases = record["phases"]
            ordinary = [phase for phase in phases if not phase["subtest"]]
            setup = [phase for phase in ordinary if phase["when"] == "setup"]
            expected = ["setup", "call", "teardown"] if (
                len(setup) == 1 and setup[0]["outcome"] == "passed"
            ) else ["setup", "teardown"]
            complete = record["finished"] and [phase["when"] for phase in ordinary] == expected
            outcomes = {phase["outcome"] for phase in phases}
            record["status"] = ("incomplete" if not complete else "failed" if "failed" in outcomes
                                else "skipped" if any(phase["outcome"] == "skipped"
                                                      for phase in ordinary) else "passed")
            if nodeid not in self.data["selected_nodeids"]:
                self.errors.append(f"foreign executed item: {nodeid}")
        self.data["executed"] = list(self.records.values())
        if sorted(self.records) != self.data["selected_nodeids"]:
            self.errors.append("terminal execution does not match selected items")
        if any(item["status"] in ("failed", "incomplete") for item in self.records.values()):
            self.errors.append("failed or incomplete item execution")
        if self.errors and session.exitstatus == 0:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        self.data["invocation"].update({"ended_at": now(), "exit_code": int(session.exitstatus)})
        junit = session.config.getoption("xmlpath")
        if junit and Path(junit).is_file():
            self.data["junit_sha256"] = hashlib.sha256(Path(junit).read_bytes()).hexdigest()
        self.data["state"] = "completed"
        self.save()
        return result
