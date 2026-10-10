"""Synthetic CPU trusted-installation checks, never native/model qualification."""

from __future__ import annotations

import copy

import pytest
import test_structured_s0 as fixtures

from spireagent.json_boundary import BoundaryError, json_bytes
from stpd import structured_policy_installation as installation
from stpd.policy.structured_export import ROOT

cpu_threads = fixtures.cpu_threads
exported = fixtures.exported
manifest_for = fixtures.manifest_for


def bind(tmp_path, exported):
    directory, metadata, _model = exported
    facts = manifest_for(directory, metadata)
    config_path, manifest_path = tmp_path / "config.json", tmp_path / "policy.json"
    config, manifest = installation.bind_structured_export(
        ROOT, directory, config_path, manifest_path, manifest_id="fixture",
        policy=facts["policy"], requirements=facts["requirements"], support=facts["support"],
        binding_root=tmp_path,
    )
    return config_path, manifest_path, config, manifest


def test_binding_checks_package_and_fixed_module_args(tmp_path, exported):
    config_path, manifest_path, config, manifest = bind(tmp_path, exported)
    assert installation.validate(ROOT, config_path, manifest_path, binding_root=tmp_path) == (
        config, manifest)
    assert installation.arguments({"config": str(config_path), "manifest": str(manifest_path)}) == [
        "-m", "stpd.policy.structured_port", "--package", str(exported[0]),
        "--manifest", str(manifest_path),
    ]
    assert manifest["claims"] == installation.CLAIMS
    assert installation.inspect(ROOT, {"config": "config.json", "manifest": "policy.json"},
                                manifest, config, binding_root=tmp_path)["policy_identity"] == {
                                    "status": "pass"}


@pytest.mark.parametrize("mutation", [
    lambda value: value["claims"].update(full_run=True),
    lambda value: value["representation"].update(input_schema="native-flat"),
    lambda value: value["adapter"].update(id="downloaded-executable"),
    lambda value: value["requirements"].update(reads=[{"hidden": True}]),
    lambda value: value["policy"].update(architecture="future-model"),
])
def test_binding_refuses_unreviewed_scope_and_code(tmp_path, exported, mutation):
    config_path, manifest_path, _config, manifest = bind(tmp_path, exported)
    changed = copy.deepcopy(manifest)
    mutation(changed)
    manifest_path.write_bytes(json_bytes(changed))
    with pytest.raises(BoundaryError):
        installation.validate(ROOT, config_path, manifest_path, binding_root=tmp_path)


def test_weight_drift_and_package_symlink_fail(tmp_path, exported):
    config_path, manifest_path, _config, _manifest = bind(tmp_path, exported)
    weights = exported[0] / "weights.tensor-tree"
    weights.write_bytes(weights.read_bytes() + b"tampered")
    with pytest.raises(BoundaryError, match="weights_digest"):
        installation.validate(ROOT, config_path, manifest_path, binding_root=tmp_path)
    link = tmp_path / "link"
    link.symlink_to(exported[0], target_is_directory=True)
    facts = manifest_for(exported[0], exported[1])
    with pytest.raises(BoundaryError, match="directory_required"):
        installation.bind_structured_export(
            ROOT, link, tmp_path / "second-config", tmp_path / "second-policy",
            manifest_id="fixture", policy=facts["policy"],
            requirements=facts["requirements"], support=facts["support"], binding_root=tmp_path)
    assert not (tmp_path / "second-config").exists()


def test_invalid_requirements_rollback_and_stale_code_identity(tmp_path, exported):
    facts = manifest_for(exported[0], exported[1])
    facts["requirements"]["successor_required"] = False
    with pytest.raises(BoundaryError, match="requirements_identity"):
        installation.bind_structured_export(
            ROOT, exported[0], tmp_path / "bad-config", tmp_path / "bad-policy",
            manifest_id="fixture", policy=facts["policy"],
            requirements=facts["requirements"], support=facts["support"], binding_root=tmp_path)
    assert not (tmp_path / "bad-config").exists()
    assert not (tmp_path / "bad-policy").exists()
    metadata_path = exported[0] / "model.json"
    value = copy.deepcopy(exported[1])
    value["adapter_code_sha256"] = "0" * 64
    metadata_path.write_bytes(json_bytes(value))
    with pytest.raises(BoundaryError, match="unsupported_package_identity"):
        installation.bind_structured_export(
            ROOT, exported[0], tmp_path / "old-config", tmp_path / "old-policy",
            manifest_id="fixture", policy=facts["policy"],
            requirements=facts["requirements"], support=facts["support"], binding_root=tmp_path)


def test_launch_argument_config_pin_and_binding_symlink_fail(tmp_path, exported):
    config_path, manifest_path, config, _manifest = bind(tmp_path, exported)
    changed = {**config, "export_path": str(tmp_path / "different-package")}
    config_path.write_bytes(json_bytes(changed))
    with pytest.raises(BoundaryError, match="export_identity_drift"):
        installation.arguments({"config": str(config_path), "manifest": str(manifest_path)})
    config_path.write_bytes(json_bytes(config))
    alias = tmp_path / "config-alias.json"
    alias.symlink_to(config_path)
    with pytest.raises(BoundaryError, match="unsafe_binding_path"):
        installation.validate(ROOT, alias, manifest_path, binding_root=tmp_path)
