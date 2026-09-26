"""Portable manifest path selection, including Windows multi-drive layouts."""

from __future__ import annotations

import ntpath
from pathlib import PureWindowsPath

import pytest

from stpd.token_policy_installation import _manifest_artifact_path


def test_artifact_manifest_path_is_relative_on_same_windows_drive():
    artifact = PureWindowsPath("C:/exports/model.json")
    manifest_directory = PureWindowsPath("c:/project/python/.local/policy")
    assert _manifest_artifact_path(artifact, manifest_directory, ntpath.relpath) == (
        ntpath.relpath(str(artifact), str(manifest_directory))
    )


def test_artifact_manifest_path_is_absolute_across_windows_drives():
    artifact = PureWindowsPath("C:/exports/model.json")
    manifest_directory = PureWindowsPath("D:/project/python/.local/policy")
    assert _manifest_artifact_path(artifact, manifest_directory, ntpath.relpath) == str(artifact)
    with pytest.raises(ValueError, match="path is on mount"):
        ntpath.relpath(str(artifact), str(manifest_directory))


def test_same_drive_path_errors_are_not_masked():
    def invalid_relative_path(_artifact: str, _directory: str) -> str:
        raise ValueError("unrelated path error")

    with pytest.raises(ValueError, match="unrelated path error"):
        _manifest_artifact_path(
            PureWindowsPath("C:/exports/model.json"),
            PureWindowsPath("C:/project/python/.local/policy"),
            invalid_relative_path,
        )
