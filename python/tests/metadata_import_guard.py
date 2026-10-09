"""Test-only guard for metadata/admission operations, independent of collection order."""

from __future__ import annotations

import builtins
import importlib
import importlib.util
from types import ModuleType
from typing import Any

import pytest


class TorchImportGuard:
    """Block backend import requests even when collection already loaded Torch."""

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._builtin_import = builtins.__import__
        self._module_import = importlib.import_module

    def _check(self, name: str) -> None:
        if name == "torch" or name.startswith("torch."):
            self.attempts.append(name)
            raise AssertionError("metadata/admission operation attempted a Torch import: " + name)

    def builtin_import(
        self, name: str, globals: Any = None, locals: Any = None,
        fromlist: Any = (), level: int = 0,
    ) -> Any:
        if level == 0:
            self._check(name)
        elif isinstance(globals, dict) and isinstance(globals.get("__package__"), str):
            self._check(importlib.util.resolve_name("." * level + name, globals["__package__"]))
        return self._builtin_import(name, globals, locals, fromlist, level)

    def module_import(self, name: str, package: str | None = None) -> ModuleType:
        resolved = importlib.util.resolve_name(name, package) if name.startswith(".") else name
        self._check(resolved)
        return self._module_import(name, package)


def install_torch_import_guard(monkeypatch: pytest.MonkeyPatch) -> TorchImportGuard:
    guard = TorchImportGuard()
    monkeypatch.setattr(builtins, "__import__", guard.builtin_import)
    monkeypatch.setattr(importlib, "import_module", guard.module_import)
    return guard


@pytest.fixture(autouse=True)
def no_torch_imports(monkeypatch):
    guard = install_torch_import_guard(monkeypatch)
    yield guard
    # An application may catch an exception during readiness. Even a swallowed
    # backend import attempt violates the metadata/admission operation boundary.
    assert not guard.attempts, "unexpected Torch import attempts: " + repr(guard.attempts)
