"""Native full-reference M2 state: stage on Consume, commit only exact consume_ack."""

from __future__ import annotations

import copy
import math
import uuid
from dataclasses import asdict
from typing import Any

import torch

from spireagent.json_boundary import BoundaryError, object_fields

from ..fullrun.native_structured_inputs import INPUT_SPEC
from ..fullrun.native_structured_sequences import NativeUnit, native_advance, qualify_native
from ..fullrun.structured_inputs import StructuredFrame
from .structured_m2 import SLOTS, WIDTH, StructuredM2

MAX_RETIRED_SEGMENTS = 1024
MAX_STATE_VERSION = 2**53 - 1


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024:
        raise BoundaryError("native_model", "binding_text_required")
    return value


def checked_prefix(value: object, token: str) -> dict[str, Any]:
    prefix = object_fields(
        value,
        {
            "continuity_token",
            "history_mode",
            "consumption_mode",
            "received_cursor",
            "consumed_publication_index",
            "omissions",
        },
        "native_model.prefix",
    )
    omissions = object_fields(
        prefix["omissions"],
        {"received_unconsumed_count", "missing_scopes", "gap"},
        "native_model.omissions",
    )
    if (
        prefix["continuity_token"] != token
        or prefix["history_mode"] != "full_reference"
        or prefix["consumption_mode"] != "once_per_occurrence"
        or omissions["gap"] is not None
        or omissions["missing_scopes"] != []
        or type(omissions["received_unconsumed_count"]) is not int
        or omissions["received_unconsumed_count"] < 0
    ):
        raise BoundaryError("native_model", "qualified_ack_prefix_required")
    if prefix["received_cursor"] is not None:
        _text(prefix["received_cursor"])
    index = prefix["consumed_publication_index"]
    if index is not None and (
        not isinstance(index, str)
        or not index.isascii()
        or not index.isdigit()
        or str(int(index)) != index
        or not 0 <= int(index) < 2**64
    ):
        raise BoundaryError("native_model", "publication_index_binding")
    return prefix


class NativeStructuredScorer:
    def __init__(self, model: StructuredM2, model_id: str, weights_sha256: str) -> None:
        self.model = model.eval()
        self.model.validate_parameters()
        self.model_id, self.weights_sha256 = model_id, weights_sha256
        self.memory = model.initial_memory()
        self.unit: NativeUnit | None = None
        self.frame: StructuredFrame | None = None
        self.input: dict[str, Any] | None = None
        self.continuity: str | None = None
        self.consumption_id: str | None = None
        self.state_version = 0
        self.acquisition_id: str | None = None
        self.prefix: dict[str, Any] | None = None
        self.retired: set[str] = set()
        self.pending: dict[str, Any] | None = None

    @property
    def model_bindings(self) -> list[dict[str, str]]:
        return [{"model_id": self.model_id, "weights_sha256": self.weights_sha256}]

    def propose_consume(self, value: dict[str, Any]) -> dict[str, Any]:
        if self.pending is not None:
            raise BoundaryError("native_model", "pending_consume_ack_required")
        value = object_fields(
            value,
            {
                "acquisition_id",
                "input_spec",
                "continuity_token",
                "previous_consumption_id",
                "observation",
                "catalog",
            },
            "native_model.consume",
        )
        acquisition, token = _text(value["acquisition_id"]), _text(value["continuity_token"])
        reset = token != self.continuity
        if (
            value["input_spec"] != INPUT_SPEC
            or token in self.retired
            or value["previous_consumption_id"] != (None if reset else self.consumption_id)
            or reset
            and len(self.retired) >= MAX_RETIRED_SEGMENTS
        ):
            raise BoundaryError("native_model", "input_spec_or_continuity_binding")
        frame, unit = qualify_native(value["observation"], value["catalog"])
        advance = native_advance(None if reset else self.unit, unit)
        version = (0 if reset else self.state_version) + int(advance)
        if version > MAX_STATE_VERSION:
            raise BoundaryError("native_model", "state_version_overflow")
        old = self.model.initial_memory() if reset else self.memory
        with torch.inference_mode():
            entities = self.model.encode(frame)
            memory = self.model.advance(entities, old) if advance else old
        report = {
            "acquisition_id": acquisition,
            "input_spec": INPUT_SPEC,
            "continuity_token": token,
            "previous_consumption_id": value["previous_consumption_id"],
            "consumption_id": uuid.uuid4().hex if advance else self.consumption_id,
            "state_version": version,
            "advanced": advance,
        }
        self.pending = {
            "report": report,
            "memory": memory.clone(),
            "unit": unit,
            "frame": frame,
            "input": copy.deepcopy(value),
            "reset": reset,
        }
        return copy.deepcopy(report)

    def acknowledge(self, value: dict[str, Any]) -> None:
        if self.pending is None:
            raise BoundaryError("native_model", "unexpected_consume_ack")
        value = object_fields(
            value,
            {"consumption_id", "acquisition_id", "state_version", "advanced", "prefix"},
            "native_model.ack",
        )
        report = self.pending["report"]
        if any(
            value[key] != report[key] or type(value[key]) is not type(report[key])
            for key in ("consumption_id", "acquisition_id", "state_version", "advanced")
        ):
            raise BoundaryError("native_model", "consume_ack_binding")
        prefix = checked_prefix(value["prefix"], report["continuity_token"])
        if self.pending["reset"] and self.continuity is not None:
            self.retired.add(self.continuity)
        self.memory, self.unit, self.frame = (
            self.pending[key] for key in ("memory", "unit", "frame")
        )
        self.input, self.prefix = self.pending["input"], copy.deepcopy(prefix)
        self.continuity, self.acquisition_id = report["continuity_token"], report["acquisition_id"]
        self.consumption_id, self.state_version = report["consumption_id"], report["state_version"]
        self.pending = None

    def scores(self) -> tuple[float, ...]:
        if self.pending is not None or self.frame is None:
            raise BoundaryError("native_model", "acknowledged_basis_required")
        if not self.frame.candidates:
            return ()
        with torch.inference_mode():
            entities = self.model.encode(self.frame)
            values = tuple(
                float(x) for x in self.model.score(self.frame, entities, self.memory).tolist()
            )
        if len(values) != len(self.frame.candidates) or any(not math.isfinite(x) for x in values):
            raise BoundaryError("native_model", "finite_complete_scores_required")
        return values

    def state(self) -> dict[str, Any]:
        if (
            self.pending is not None
            or self.input is None
            or self.unit is None
            or self.prefix is None
        ):
            raise BoundaryError("native_model", "acknowledged_state_required")
        return {
            "memory": self.memory.clone(),
            "unit": asdict(self.unit),
            "input": copy.deepcopy(self.input),
            "continuity_token": self.continuity,
            "consumption_id": self.consumption_id,
            "state_version": self.state_version,
            "prefix": copy.deepcopy(self.prefix),
            "retired": sorted(self.retired),
        }

    def restore(self, value: dict[str, Any]) -> None:
        if self.unit is not None or self.pending is not None:
            raise BoundaryError("native_model", "fresh_state_restore_required")
        value = object_fields(
            value,
            {
                "memory",
                "unit",
                "input",
                "continuity_token",
                "consumption_id",
                "state_version",
                "prefix",
                "retired",
            },
            "native_model.state",
        )
        source = object_fields(
            value["input"],
            {
                "acquisition_id",
                "input_spec",
                "continuity_token",
                "previous_consumption_id",
                "observation",
                "catalog",
            },
            "native_model.state_input",
        )
        prefix = checked_prefix(value["prefix"], value["continuity_token"])
        frame, unit = qualify_native(source["observation"], source["catalog"])
        memory = value["memory"]
        retired = value["retired"]
        if (
            value["unit"] != asdict(unit)
            or not isinstance(memory, torch.Tensor)
            or memory.shape != (SLOTS, WIDTH)
            or memory.dtype != torch.float32
            or memory.device.type != "cpu"
            or not bool(torch.isfinite(memory).all())
            or type(value["state_version"]) is not int
            or not 1 <= value["state_version"] <= MAX_STATE_VERSION
            or not isinstance(retired, list)
            or len(retired) > MAX_RETIRED_SEGMENTS
            or len(set(retired)) != len(retired)
            or value["continuity_token"] in retired
            or value["input"]["input_spec"] != INPUT_SPEC
            or value["input"]["continuity_token"] != value["continuity_token"]
            or value["prefix"]["continuity_token"] != value["continuity_token"]
        ):
            raise BoundaryError("native_model", "state_shape_or_binding")
        for token in retired:
            _text(token)
        _text(value["continuity_token"])
        _text(value["consumption_id"])
        acquisition = _text(value["input"]["acquisition_id"])
        self.memory, self.unit, self.frame = memory.detach().clone(), unit, frame
        self.input, self.prefix = copy.deepcopy(value["input"]), copy.deepcopy(prefix)
        self.continuity, self.consumption_id = value["continuity_token"], value["consumption_id"]
        self.state_version, self.acquisition_id = value["state_version"], acquisition
        self.retired = set(retired)
