"""Cheap real collector/reader checks; synthetic owner receipts are not full gates."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.project import RepositoryGateError, parse_pytest_shard, portable_commands
from tools.pytest_shard import normalized_nodeid, selected_ids

ROOT = Path(__file__).resolve().parents[2]


def test_default_inventory_and_explicit_top_level_only() -> None:
    full = portable_commands("python")
    for value in ("1/2", "2/2"):
        sharded = portable_commands("python", pytest_shard=parse_pytest_shard(value))
        assert len(full) == len(sharded)
        for original, partition in zip(full, sharded, strict=True):
            if original[:3] == ("python", "-m", "pytest"):
                assert partition[:len(original)] == original
                assert partition[len(original):] == (
                    "-p", "tools.pytest_shard", f"--portable-shard-index={value[0]}",
                    "--portable-shard-count=2",
                    "--portable-shard-manifest=.local/pytest-shard.json",
                )
            else:
                assert partition == original
    assert not any("tools.pytest_shard" in command for command in full)
    for value in ("0/2", "3/2", "1/3", "", "all"):
        with pytest.raises(RepositoryGateError):
            parse_pytest_shard(value)
    assert normalized_nodeid(r"tests\test_a.py::TestClass::test_x[one]") == (
        "tests/test_a.py::TestClass::test_x[one]"
    )
    for value in ("", "../a.py::x", "C:/a.py::x", "/a.py::x", "a.py::"):
        with pytest.raises(ValueError):
            normalized_nodeid(value)
    collection = [{"file": file, "nodeid": file + "::test_x[one]"} for file in (
        "tests/test_z.py", "deploy/hub/test_a.py", "tests/test_new.py",
    )]
    assert set(selected_ids(collection, 1, 2)).isdisjoint(selected_ids(collection, 2, 2))
    assert sorted(selected_ids(collection, 1, 2) + selected_ids(collection, 2, 2)) == sorted(
        item["nodeid"] for item in collection
    )


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True,
                                   encoding="utf-8", stderr=subprocess.DEVNULL).strip()


def golden_repo(root: Path, failure: str = "") -> Path:
    root.mkdir()
    files = {
        ".gitignore": ".local/\n__pycache__/\n.pytest_cache/\n",
        ".github/workflows/ci.yml": "name: fixture\n",
        "python/pytest.ini": "[pytest]\ntestpaths = tests deploy/hub\n",
        "python/deploy/hub/test_a.py": "def test_a():\n    assert True\n",
        "python/tests/test_b.py": '''import pytest
from pathlib import Path
@pytest.fixture(scope="module", autouse=True)
def once():
    path = Path(".local/fixture-count")
    assert not path.exists()
    path.write_text("once", encoding="utf-8")
@pytest.mark.parametrize("value", ["one", "two"])
def test_values(value):
    assert value
''',
        "python/tests/test_c.py": (
            "def test_c(subtests):\n    with subtests.test(value=1):\n        assert True\n"
        ),
        "python/tests/test_nested.py": '''import os
import subprocess
import sys
from pathlib import Path
def test_nested():
    command = [sys.executable, "-m", "pytest", "nested/test_inner.py", "-q"]
    child = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert child.returncode == 0, child.stdout + child.stderr
    assert Path(".local/nested-items").read_text().splitlines() == ["one", "two"]
''',
        "python/nested/test_inner.py": '''from pathlib import Path
def record(value):
    with Path(".local/nested-items").open("a") as stream:
        stream.write(value + "\\n")
def test_one():
    record("one")
def test_two():
    record("two")
''',
    }
    if failure:
        files["python/tests/test_c.py"] = {
            "subtest": (
                "def test_c(subtests):\n    with subtests.test(value=1):\n        assert False\n"
            ),
            "teardown": ("import pytest\n@pytest.fixture\ndef fixture():\n    yield\n"
                         "    assert False\ndef test_c(fixture):\n    pass\n"),
            "interrupt": "def test_c():\n    raise KeyboardInterrupt()\n",
            "collection": "raise RuntimeError('original collection failure')\n",
            "skip": ("import pytest\n@pytest.mark.skip(reason='original skip')\n"
                     "def test_c():\n    pass\n"),
        }[failure]
    files["python/tools/pytest_shard.py"] = (ROOT / "python/tools/pytest_shard.py").read_text(
        encoding="utf-8"
    )
    for name, text in files.items():
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
    git(root, "init")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "add", ".")
    git(root, "commit", "-m", "golden fixture")
    return root


def run_shard(root: Path, index: int) -> tuple[int, dict]:
    environment = dict(os.environ)
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("PYTEST_PLUGINS", None)
    environment.update({"PYTHONPATH": str(root / "python"), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                        "GITHUB_REPOSITORY": "fixture/repo", "GITHUB_RUN_ID": "123",
                        "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": {
                            "Linux": "linux-portability", "Windows": "windows-portability",
                        }.get(platform.system(), "fixture"), "CHECK_SCOPE": "full",
                        "RUNNER_OS": platform.system(),
                        "GITHUB_SHA": git(root, "rev-parse", "HEAD")})
    command = [sys.executable, "-m", "pytest", "-q", "-p", "tools.pytest_shard",
               f"--portable-shard-index={index}", "--portable-shard-count=2",
               "--portable-shard-manifest=.local/pytest-shard.json", "--junitxml=.local/pytest.xml"]
    started = datetime.now(UTC).isoformat()
    process = subprocess.Popen(command, cwd=root / "python", env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate(timeout=20)
    output = root / "python/.local"
    output.mkdir(exist_ok=True)
    (output / "original-stdout").write_bytes(stdout)
    (output / "original-stderr").write_bytes(stderr)
    (output / "author-invocation.json").write_text(json.dumps({
        "argv": command, "pid": process.pid, "exit_code": process.returncode, "started_at": started,
        "ended_at": datetime.now(UTC).isoformat(), "head": environment["GITHUB_SHA"],
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
    }), encoding="utf-8")
    manifest = output / "pytest-shard.json"
    assert manifest.is_file(), stdout.decode() + stderr.decode()
    return process.returncode, json.loads(manifest.read_text(encoding="utf-8"))


def test_real_collection_module_lifetime_nested_child_and_aggregate(tmp_path: Path) -> None:
    source = golden_repo(tmp_path / "source")
    manifests = []
    for index in (1, 2):
        clone = tmp_path / f"clone-{index}"
        git(tmp_path, "clone", "--no-hardlinks", str(source), str(clone))
        code, report = run_shard(clone, index)
        assert code == 0, report
        manifests.append(report)
        assert report["state"] == "completed" and report["invocation"]["exit_code"] == 0
        assert report["junit_sha256"] == hashlib.sha256(
            (clone / "python/.local/pytest.xml").read_bytes()
        ).hexdigest()
        assert all(item["finished"] for item in report["executed"])
    assert manifests[0]["collection"] == manifests[1]["collection"]
    selected = manifests[0]["selected_nodeids"] + manifests[1]["selected_nodeids"]
    assert len(selected) == len(set(selected)) == len(manifests[0]["collection"]) == 5
    assert (tmp_path / "clone-2/python/.local/fixture-count").read_text() == "once"
    assert (tmp_path / "clone-2/python/.local/nested-items").read_text().splitlines() == [
        "one", "two",
    ]
    # Real pytest originals enter the production verifier; broad owner stages are modeled here.
    script = '''import fs from 'node:fs'; import path from 'node:path';
import {verifyPytestShards} from './tools/verify-pytest-shards.mjs';
import {workspaceStages,pytestPartition} from './tools/check-workspace.mjs';
const base=process.argv[1], os=process.argv[2];
const artifact=path.join(base,'artifacts'); fs.mkdirSync(artifact);
let expected;
for(const index of [1,2]) {
 const original=path.join(base,`clone-${index}`);
 const dir=path.join(artifact,`pytest-${os}-shard-${index}-1`);
 fs.mkdirSync(path.join(dir,'python'),{recursive:true});
 fs.cpSync(path.join(original,'python','.local'),path.join(dir,'python','.local'),{recursive:true});
 const manifest=path.join(dir,'python','.local','pytest-shard.json');
 const report=JSON.parse(fs.readFileSync(manifest,'utf8'));
 expected={source:report.source_at_start, repository:'fixture/repo',
           run_id:'123',run_attempt:'1',scope:'full'};
 const identity={head:expected.source.head,tree:expected.source.tree,dirty:false};
 const workspace={schema:'spireagent/portable-stage-results-1',scope:'full',
   platform:{Darwin:'darwin',Linux:'linux',Windows:'win32'}[os],verdict:'passed',
   source_accepted:true,interrupted_signal:null,source_at_start:identity,source_at_end:identity,
   pytest_partition:pytestPartition(`${index}/2`),
   results:workspaceStages('full',{pytestShard:`${index}/2`}).map(stage=>
     ({id:stage.id,args:stage.args,status:'passed',exit_code:0,signal:null}))};
 fs.mkdirSync(path.join(dir,'.local','checks'),{recursive:true});
 fs.writeFileSync(path.join(dir,'.local','checks',`workspace-full-${workspace.platform}.json`),JSON.stringify(workspace));
}
console.log(JSON.stringify(verifyPytestShards(artifact,expected,{oses:[os]})));
'''
    node = shutil.which("node")
    assert node
    result = subprocess.run([node, "--input-type=module", "-e", script,
                             str(tmp_path), platform.system()],
                            cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["complete"] is True


@pytest.mark.parametrize("failure", ["subtest", "teardown", "interrupt", "collection", "skip"])
def test_real_negative_outcomes_remain_original(tmp_path: Path, failure: str) -> None:
    root = golden_repo(tmp_path / "source", failure)
    code, report = run_shard(root, 1)
    if failure == "skip":
        assert code == 0
        assert any(item["status"] == "skipped" for item in report["executed"])
    else:
        assert code != 0 and report["invocation"]["exit_code"] != 0
        if failure == "collection":
            assert report["collection_errors"]
        else:
            assert any(item["status"] in ("failed", "incomplete") for item in report["executed"])
