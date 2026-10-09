"""Test-only guard for metadata/admission operations, independent of collection order."""

from __future__ import annotations

import builtins
import importlib
import importlib.util
import operator
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

    @staticmethod
    def _relative_package(globals: Any) -> str | None:
        if not isinstance(globals, dict):
            return None
        package = dict.get(globals, "__package__")
        spec = dict.get(globals, "__spec__")
        if package is None:
            if spec is not None:
                try:
                    package = spec.parent
                except (AttributeError, TypeError):
                    return None
            else:
                package = dict.get(globals, "__name__")
                if isinstance(package, str) and not dict.__contains__(globals, "__path__"):
                    package = package.rpartition(".")[0]
        return package if isinstance(package, str) and package else None

    def builtin_import(
        self, name: str, globals: Any = None, locals: Any = None,
        fromlist: Any = (), level: int = 0,
    ) -> Any:
        if not isinstance(name, str):
            return self._builtin_import(name, globals, locals, fromlist, level)
        try:
            resolved_level = operator.index(level)
        except TypeError:
            return self._builtin_import(name, globals, locals, fromlist, level)
        # CPython's builtin level argument is a C int. Keep invalid argument
        # errors owned by the actual builtin, without allocating relative dots.
        if not 0 <= resolved_level <= 2**31 - 1:
            return self._builtin_import(name, globals, locals, fromlist, level)
        if resolved_level == 0:
            self._check(name)
        else:
            package = self._relative_package(globals)
            if package is None:
                return self._builtin_import(name, globals, locals, fromlist, resolved_level)
            parts = package.rsplit(".", resolved_level - 1)
            if len(parts) < resolved_level:
                return self._builtin_import(name, globals, locals, fromlist, resolved_level)
            self._check(parts[0] + ("." + name if name else ""))
        return self._builtin_import(name, globals, locals, fromlist, resolved_level)

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
