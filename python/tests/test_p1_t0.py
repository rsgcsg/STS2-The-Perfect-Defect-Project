"""Faithful inexpensive checks for P1 masks, model input, resume and registration."""

from __future__ import annotations

import copy
import json

import pytest
import torch

from stpd.p1.data import digest, json_bytes, read_segments
from stpd.p1.export import export_model, load_model, register
from stpd.p1.input import fit_vocabulary, project
from stpd.p1.model import MStd
from stpd.p1.training import Trainer, maximum_difference
from stpd.workers.checkpoint_codec import decode_checkpoint


def observation():
    return {
        "schema": "sts2.p1/observation-1",
        "profile": "p1-global-r1",
        "revision": "r1.4",
        "envelope": {
            "source_snapshot_id": "private-binding",
            "required_information_complete": True,
        },
        "status": "decision",
        "scene": {
            "family": "event",
            "content": {
                "entities": [
                    {"entity_id": "e1", "definition_id": "A", "description": "Gain 5 Block."},
                    {"entity_id": "e2", "definition_id": "B", "description": "Deal 6 damage."},
                ]
            },
        },
        "catalog": [
            {
                "choice_id": "c7",
                "kind": "event.choose",
                "subject": "e1",
                "target": None,
                "label": "Gain 5 Block.",
                "mapped": True,
            },
            {
                "choice_id": "c9",
                "kind": "event.leave",
                "subject": "e2",
                "target": None,
                "label": "Deal 6 damage.",
                "mapped": True,
            },
        ],
        "terminal": None,
    }


def segments():
    obs = observation()
    terminal = {
        **copy.deepcopy(obs),
        "status": "terminal",
        "catalog": [],
        "terminal": {"outcome": "loss"},
    }
    branch = {"session_id": "session", "timeline_id": "timeline", "run_id": "run"}
    return {
        "schema": "sts2.p1/native-segments-1",
        "profile": "p1-global-r1",
        "revision": "r1.4",
        "branch": branch,
        "source_content_id": "source",
        "gaps": [],
        "discarded_choices": [],
        "segments": [
            {
                "segment_id": "s1",
                "branch": branch,
                "start_boundary": {
                    "complete_basis": True,
                    "unfinished_reward_commitment": False,
                    "pending_native_decisions": [],
                    "selector_continuation": False,
                },
                "end_target": {"branch": branch, "source_event_id": "terminal", "result": "loss"},
                "points": [
                    {
                        "index": 0,
                        "observation": obs,
                        "chosen": "c9",
                        "source_event_id": "decision",
                        "mask": {"N": True, "next": True, "end": True},
                    },
                    {
                        "index": 1,
                        "observation": terminal,
                        "chosen": None,
                        "source_event_id": "terminal",
                        "mask": {"N": False, "next": False, "end": True},
                    },
                ],
            }
        ],
    }


def read_fixture(tmp_path, value):
    path = tmp_path / "segments.json"
    path.write_bytes(json_bytes(value))
    return read_segments(path, digest(path.read_bytes()))


def test_current_segments_keep_target_masks_and_one_lineage(tmp_path):
    data = read_fixture(tmp_path, segments())
    assert (
        data.receipt["observations"],
        data.receipt["N"],
        data.receipt["next"],
        data.receipt["end"],
    ) == (2, 1, 1, 2)
    assert data.examples[0].chosen == "c9" and data.examples[1].next_index is None
    assert {a["split"] for a in data.receipt["splits"].values()} == {"train"}
    assert "chosen" not in vars(project(data.examples[0].observation))


@pytest.mark.parametrize(
    "defect", ["duplicate", "outside", "cross_gap", "end_branch", "old_version", "null_mask"]
)
def test_invalid_supervision_does_not_silently_enter_training(tmp_path, defect):
    data = segments()
    first = data["segments"][0]["points"][0]
    if defect == "duplicate":
        first["observation"]["catalog"].append(copy.deepcopy(first["observation"]["catalog"][1]))
    elif defect == "outside":
        first["chosen"] = "foreign"
    elif defect == "cross_gap":
        data["segments"][0]["points"][-1]["mask"]["next"] = True
    elif defect == "end_branch":
        data["segments"][0]["end_target"] = {"branch": {"run_id": "foreign"}, "result": "loss"}
    elif defect == "old_version":
        data["revision"] = "r1.3"
    else:
        first["mask"]["N"] = None
    with pytest.raises(ValueError):
        read_fixture(tmp_path, data)


def test_input_alias_renaming_target_isolation_and_untruncated_text():
    from dataclasses import asdict

    obs = observation()
    left = project(obs)
    renamed = json.loads(
        json.dumps(obs).replace('"e1"', '"renamed"').replace('"c7"', '"new-choice"')
    )
    right = project(renamed)
    assert [asdict(e) for e in left.entities] == [asdict(e) for e in right.entities]
    assert left.roles == right.roles
    assert asdict(project(json.loads(json.dumps(obs, sort_keys=True)))) == asdict(left)
    obs["scene"]["content"]["owner_entity_id"] = "owner-body-not-visible"
    assert project(obs).entities[-1].text == [("availability", "reference_only")]
    obs["scene"]["content"]["entities"][0]["description"] = "Block " * 400 + "987.5"
    frame = project(obs)
    vocab = fit_vocabulary([frame])
    model = MStd(vocab).eval()
    ids, nums = model.tokenize("Block " * 400 + "987.5")
    assert len(ids) > 400 and any(nums)


def test_id_mask_has_no_identifier_text_bypass(monkeypatch):
    from dataclasses import asdict

    first = project(observation())
    second = project(json.loads(json.dumps(observation()).replace('"A"', '"NEW_ID"')))
    assert "NEW_ID" not in str([e.text for e in second.entities])
    assert [asdict(e) for e in first.entities] != [asdict(e) for e in second.entities]
    model = MStd(fit_vocabulary([first, second])).train()
    monkeypatch.setattr(torch, "rand", lambda size, **kw: torch.zeros(size, **kw))
    torch.manual_seed(7)
    left = model(first)
    torch.manual_seed(7)
    right = model(second)
    assert torch.equal(left, right)


def test_resume_restores_dropout_sampler_adam_and_next_batch(tmp_path):
    torch.set_num_threads(2)
    frame = project(observation())
    vocab = fit_vocabulary([frame])
    trainer = Trainer([frame], [1], vocab, "source", 2)
    trainer.advance()
    midpoint = trainer.checkpoint()
    trainer.advance()
    reference = decode_checkpoint(trainer.checkpoint())
    replacement = Trainer([frame], [1], vocab, "source", 2)
    replacement.restore(midpoint)
    replacement.advance()
    assert maximum_difference(reference, decode_checkpoint(replacement.checkpoint())) == 0
    with pytest.raises(ValueError, match="identity"):
        Trainer([frame], [0], vocab, "source", 2).restore(midpoint)


def test_controlled_weights_score_legal_choices_and_exact_export_load(tmp_path, monkeypatch):
    frame = project(observation())
    model = MStd(fit_vocabulary([frame])).eval()
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.action_types.weight[model.index("actions", "event.choose"), 0] = 1
        model.action_types.weight[model.index("actions", "event.leave"), 0] = -1
        model.head[0].weight[0, 0] = 1
        model.head[2].weight[0, 0] = 1
        scores = model(frame)
        assert frame.choices[int(scores.argmax())]["choice_id"] == "c7"
        model.head[2].weight[0, 0] = -1
        flipped = model(frame)
        assert frame.choices[int(flipped.argmax())]["choice_id"] == "c9"
    checkpoint = tmp_path / "training-state"
    checkpoint.write_bytes(b"controlled test parameters only")
    desc = export_model(model, checkpoint, tmp_path / "export", {"scope": "controlled test"})
    loaded, _ = load_model(tmp_path / "export/model.tensor-tree", desc["package_sha256"])
    assert torch.equal(loaded(frame), flipped)
    with pytest.raises(ValueError, match="checksum"):
        load_model(tmp_path / "export/model.tensor-tree", "0" * 64)
    template = {
        "schema": "sts2.policy-runtime/agent-manifest-1",
        "input": {"profile": "p1-global-r1"},
    }
    register(tmp_path / "export", tmp_path / "private", template)
    import builtins

    from stpd.p1.agent import installation

    original_import = builtins.__import__

    def without_workbench(name, *args, **kwargs):
        if name == "spireagent.workbench" or name.startswith("spireagent.workbench."):
            raise AssertionError("P1 installation must not import Workbench")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_workbench)
    config, manifest, loaded, _ = installation(
        tmp_path / "private/config.json", tmp_path / "private/manifest.json", tmp_path / "private"
    )
    assert manifest["input"]["profile"] == "p1-global-r1"
    assert config["weights_sha256"] and torch.equal(loaded(frame), flipped)
    # Exercise the actual Agent implementation with known controlled weights.
    # ACK uses the returned completion, exactly as the Runtime client does.
    import io

    from stpd.p1.agent import SCHEMA, serve

    destination = io.StringIO()
    current = observation()
    current.pop("envelope")
    context = {"schema": SCHEMA, "session_id": "test-session", "recovery_epoch": 0}
    value = {
        "acquisition_id": "acq-1",
        "input_spec": manifest["input"]["input_spec"],
        "continuity_token": "continuity-1",
        "previous_consumption_id": None,
        "observation": current,
        "catalog": current["catalog"],
        "consumption_unit": {"id": "unit-1"},
    }

    def client_messages():
        yield json.dumps(
            {**context, "request_id": "consume-1", "message_type": "consume", "input": value}
        )
        completion = json.loads(destination.getvalue().splitlines()[-1])["completion"]
        yield json.dumps(
            {
                **context,
                "request_id": "consume-1",
                "message_type": "consume_ack",
                "completion": completion,
            }
        )
        yield json.dumps(
            {
                **context,
                "request_id": "next-1",
                "message_type": "next",
                "input": {"continuity_token": "continuity-1"},
            }
        )

    serve(
        tmp_path / "private/config.json",
        tmp_path / "private/manifest.json",
        tmp_path / "private",
        source=client_messages(),
        destination=destination,
    )
    directive = json.loads(destination.getvalue().splitlines()[-1])["output"]["directive"]
    assert directive["selection"]["action_id"] == "c9"
    assert directive["scores"]["values"] == flipped.tolist()
    monkeypatch.setattr(builtins, "__import__", original_import)
    from spireagent.workbench.developer import ProjectConfig, combination
    from spireagent.workbench.local_models import LocalModelService

    service = LocalModelService(ProjectConfig(tmp_path / "state", "", "", None, combination()))
    service.private_root = tmp_path / "private"
    try:
        composition = service.composition("t0-p1-mstd")
        assert composition["semantic_definition"]["state"]["kind"] == "stateless_current"
        assert (
            composition["semantic_definition"]["models"][0]["weights_sha256"]
            == config["weights_sha256"]
        )
        assert "stpd.p1.agent" in service.adapter_arguments(service.selection("t0-p1-mstd"))
    finally:
        service.close()
    from stpd.p1.installation import inspect

    (tmp_path / "private/model.tensor-tree").unlink()
    state = inspect(
        tmp_path / "private",
        {"config": "config.json", "manifest": "manifest.json"},
        manifest,
        config,
    )
    assert state["policy_identity"]["status"] == "blocked"
