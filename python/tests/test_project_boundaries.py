"""Dependency-direction checks protect the extracted application/research seam."""

import ast
from pathlib import Path

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.policies import policy_support

ROOT = Path(__file__).resolve().parents[1]


def imports(path: Path) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def test_shared_primitives_have_no_application_or_research_dependency():
    paths = [ROOT / "spireagent" / name for name in (
        "encoding.py", "json_boundary.py", "source.py", "artifact_contracts.py",
        "package_identity.py", "policy_files.py",
        "task_outcome.py", "managed_task_outcome.py",
    )]
    paths.extend((ROOT / "spireagent/storage").rglob("*.py"))
    for path in paths:
        assert not any(name.startswith(("stpd", "spireagent.hub", "spireagent.workbench",
                                        "spireagent.console")) for name in imports(path)), path


def test_research_never_imports_project_ui_or_operations():
    for path in (ROOT / "stpd").rglob("*.py"):
        assert not any(name.startswith(("spireagent.hub", "spireagent.workbench",
                                        "spireagent.console")) for name in imports(path)), path


def test_scheduler_and_model_supervisor_do_not_own_vendor_backends():
    assert "stpd.cloud_jobs.modal" not in imports(ROOT / "spireagent/hub/scheduler.py")
    assert not any(name.startswith(("stpd", "torch", "transformers"))
                   for name in imports(ROOT / "spireagent/workbench/local_models.py"))


def test_manifest_cannot_select_an_arbitrary_python_module():
    assert policy_support("s1-v1").__name__ == "stpd.policy.installation"
    with pytest.raises(BoundaryError):
        policy_support("os.system")
