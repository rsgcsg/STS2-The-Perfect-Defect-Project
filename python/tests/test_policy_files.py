"""Shared binding path constraints keep caller-owned failure semantics."""

import pytest

from spireagent.json_boundary import BoundaryError
from spireagent.policy_files import bound_path


def test_binding_path_allows_new_destination_but_not_outside_owner(tmp_path):
    owner = tmp_path / "owner"
    owner.mkdir()
    destination = owner / "new" / "config.json"
    assert bound_path(owner, destination, stage="native_agent_support") == destination
    with pytest.raises(ValueError):
        bound_path(owner, tmp_path / "outside", stage="native_agent_support")
    with pytest.raises(BoundaryError, match="invalid_registry_path") as error:
        bound_path(owner, owner / ".." / "outside", stage="native_agent_support")
    assert error.value.stage == "local_model"


@pytest.mark.parametrize("directory", [False, True])
@pytest.mark.parametrize("stage", ["native_agent_support", "caller_stage"])
def test_binding_rejects_internal_symlink_at_any_segment(tmp_path, directory, stage):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    path = link / "config.json" if directory else link
    with pytest.raises(BoundaryError, match="unsafe_binding_path") as error:
        bound_path(tmp_path, path, stage=stage)
    assert error.value.stage == stage


def test_binding_symlink_escape_retains_existing_inside_error(tmp_path):
    owner = tmp_path / "owner"
    owner.mkdir()
    link = owner / "link"
    link.symlink_to(tmp_path / "outside")
    with pytest.raises(BoundaryError, match="invalid_registry_path") as error:
        bound_path(owner, link, stage="native_agent_support")
    assert error.value.stage == "local_model"
