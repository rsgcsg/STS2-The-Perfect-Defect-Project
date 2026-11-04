"""Original ACKs are immutable; new CP-only generations never replay hot inputs."""

from __future__ import annotations

import dataclasses

import pytest
from test_rl41_w1 import configuration, deliver

from spireagent.json_boundary import BoundaryError
from stpd.p1.local_inference import SegmentConnection
from stpd.rl.segment_learner import SegmentLearner


@pytest.mark.parametrize("operation", ["commit", "episode_end"])
def test_lost_accepted_TCP_reply_is_resolved_from_original_receipt(
    tmp_path, monkeypatch, operation
):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    actual = owner.pool.control
    seen = []

    def observed(request):
        seen.append(request["operation"])
        return actual(request)

    owner.pool.control = observed
    for name, token in owner.pool.environment().items():
        monkeypatch.setenv(name, token)

    class LostReply(SegmentConnection):
        injected = False

        def _request_once(self, kind, fields):
            result = super()._request_once(kind, fields)
            if kind == operation and not self.injected:
                self.injected = True
                raise ConnectionResetError("injected accepted reply loss")
            return result

    connection = LostReply(owner.pool.attachment())
    try:
        deliver(owner, actor="a", connection=connection)
        assert seen.count(operation) == 1 and "receipt" in seen
        assert owner.queue.rows == 2 and owner.input_sequence == 4
        records = owner.exchange.input_records(0, training_generation=owner.generation)
        fact = next(
            owner.exchange.read_input(record)
            for record in records
            if owner.exchange.read_input(record)["request"]["operation"] == operation
        )
        assert owner.control(fact["request"]) == fact["response"]
        assert owner.input_sequence == 4 and owner.queue.rows == 2
    finally:
        connection.close()
        owner.close()


@pytest.mark.parametrize("fault", ["uncertain_write", "partial_apply"])
def test_ambiguous_input_fences_before_more_control_or_checkpoint(tmp_path, monkeypatch, fault):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    try:
        owner.checkpoint(final=True)
        if fault == "uncertain_write":
            actual = owner.exchange.append_input

            def broken(*args, **kwargs):
                actual(*args, **kwargs)
                raise OSError("injected uncertain input write")

            monkeypatch.setattr(owner.exchange, "append_input", broken)
        else:
            actual = owner._accept

            def broken(request, response, apply):
                def partial():
                    apply()
                    raise OSError("injected partial apply")

                return actual(request, response, partial)

            monkeypatch.setattr(owner, "_accept", broken)
        request = {
            "operation": "episode_open",
            "run": config.run,
            "training_generation": owner.generation,
            "actor": "a",
            "episode": "e",
        }
        with pytest.raises(OSError, match="injected"):
            owner.control(request)
        assert owner.input_fenced and owner.input_sequence == 0
        with pytest.raises(BoundaryError, match="input_owner_fenced"):
            owner.control({**request, "actor": "b"})
        with pytest.raises(BoundaryError, match="input_owner_fenced"):
            owner.checkpoint(final=True)
        assert len(owner.exchange.input_records(0, training_generation=owner.generation)) == 1
    finally:
        owner.close()


@pytest.mark.parametrize("changed", ["terminal", "outcome", "typed_original"])
def test_late_terminal_label_is_bound_to_exact_verified_source_before_pool_apply(
    tmp_path, monkeypatch, changed
):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)

    def alter(end):
        if changed == "terminal":
            from stpd.rl.heads import class_reward_values

            end["terminal"] = {
                **end["terminal"],
                "class": 4,
                "reward": float(class_reward_values()[4]),
            }
            for row in end["outcome_states"]:
                row["outcome"] = 4
        elif changed == "outcome":
            end["outcome_states"][0]["outcome"] = 4
        else:
            end["typed_original"] = "b" * 64

    try:
        with pytest.raises(ValueError, match="terminal|typed_original"):
            deliver(owner, actor="a", terminal=True, end_transform=alter)
        assert not owner.terminal.groups and not owner.hp.groups and not owner.input_fenced
        assert owner.input_sequence == 3 and owner.queue.rows == 2
    finally:
        owner.close()


def test_same_store_restore_returns_old_ACK_without_replaying_queue_or_old_mu(
    tmp_path, monkeypatch
):
    config, io = configuration(tmp_path, monkeypatch)
    owner = SegmentLearner(config, tmp_path / "owner", io)
    owner.checkpoint(final=True)
    cp = owner.exchange.latest_checkpoint(training_generation=owner.generation)[
        "checkpoint_artifact_id"
    ]
    deliver(owner, actor="a")  # Every accepted input occurs AFTER this CP.
    record = next(
        record
        for record in owner.exchange.input_records(0, training_generation=owner.generation)
        if owner.exchange.read_input(record)["request"]["operation"] == "commit"
    )
    fact = owner.exchange.read_input(record)
    owner.close()
    from spireagent.rl_exchange import Exchange

    def no_replay(*args, **kwargs):
        raise AssertionError("CP-only restore traversed the old input log")

    monkeypatch.setattr(Exchange, "input_records", no_replay)
    restored = SegmentLearner(
        dataclasses.replace(
            config,
            start_package=None,
            resume_checkpoint=cp,
            segments=dataclasses.replace(config.segments, training_generation="g-replacement"),
        ),
        tmp_path / "empty-work",
        io,
    )
    try:
        assert (
            restored.queue.rows
            == restored.input_sequence
            == restored.first_use
            == restored.updates
            == 0
        )
        assert restored.control(fact["request"]) == fact["response"]
        assert restored.queue.rows == restored.input_sequence == 0
        missing = {**fact["request"], "segment_id": "a/999"}
        assert restored.control(missing)["reason"] == "generation_changed"
        assert restored.queue.rows == restored.input_sequence == 0
        assert owner.exchange.read_input(record) == fact
    finally:
        restored.close()
