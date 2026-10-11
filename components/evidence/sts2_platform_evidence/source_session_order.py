"""Closed native input-prefix order supplement, independent of producer code."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from .source_session_bundle import _object, _require


def _input_is_paused(value: dict[str, Any], boundaries: list[dict[str, Any]],
                     index: Callable[[Any], int]) -> bool:
    point = value["pre_position"]
    position = index(point["publication_index"])
    for boundary in boundaries:
        for interval in boundary["paused_intervals"]:
            if (interval["epoch_id"] != point["epoch_id"]
                    or not index(interval["after_index"]) < position <= index(interval["through_index"])):
                continue
            after_resume = (boundary["kind"] == "resume" and boundary["position"] == point
                            and index(value["input_prefix_ordinal"]) > index(boundary["after_input_ordinal"]))
            if not after_resume:
                return True
    return False


def _verify_order(epochs: list[dict[str, Any]], segments: list[dict[str, Any]],
                  boundaries: list[dict[str, Any]], inputs: list[dict[str, Any]],
                  drains: list[dict[str, Any]], final_ordinal: Any,
                  index: Callable[[Any], int]) -> None:
    final = index(final_ordinal)
    _require(final == len(inputs), "source_input_ordinal_accounting_incomplete")
    epoch_map = {value["epoch_id"]: (offset, value) for offset, value in enumerate(epochs)}
    segment_map = {value["segment_id"]: offset for offset, value in enumerate(segments)}
    seals = {value["epoch_id"]: value for value in boundaries[-1]["sealed_epochs"]}
    def validate_actor_boundaries() -> None:
        actor = 0
        pause_point = None
        pause_cut = 0
        def native_point(value: dict[str, Any]) -> tuple[int, int]:
            return epoch_map[value["epoch_id"]][0], index(value["publication_index"])
        for boundary in boundaries:
            next_actor = segment_map[boundary["segment_id"]]
            _require(next_actor >= actor, "source_boundary_actor_regressed")
            if pause_point is None:
                _require(next_actor == actor, "source_boundary_actor_changed_outside_pause")
            else:
                _require(index(boundary["after_input_ordinal"]) == pause_cut, "source_input_admitted_while_paused")
                for offset in range(actor + 1, next_actor + 1):
                    segment = segments[offset]
                    point, through = native_point(segment["boundary_position"]), native_point(boundary["position"])
                    _require(index(segment["after_input_ordinal"]) == pause_cut and pause_point <= point
                             and (point < through if boundary["kind"] == "epoch_transition" else point <= through),
                             "source_actor_pause_input_fence_mismatch")
                actor = next_actor
            if boundary["kind"] == "pause":
                pause_point, pause_cut = native_point(boundary["position"]), index(boundary["after_input_ordinal"])
            elif boundary["kind"] in {"resume", "close"}:
                pause_point = None
            elif boundary["kind"] == "epoch_transition" and pause_point is not None:
                pause_point = native_point(boundary["position"])
        _require(actor == len(segments) - 1, "source_actor_boundary_accounting_incomplete")
    previous_point = None
    for expected, value in enumerate(sorted(inputs, key=lambda row: index(row["input_prefix_ordinal"])), 1):
        ordinal = index(value["input_prefix_ordinal"])
        _require(ordinal == expected, "source_input_ordinal_not_contiguous")
        order = _object(value["basis_order"])
        _require(set(order) == {"status", "reason_code"} and order["status"] in {"native_prefix_frozen", "unproven"}
                 and order["reason_code"] == ("source_prefix_capture_order_unproven" if order["status"] == "unproven" else None),
                 "source_input_order_invalid")
        epoch_offset, epoch = epoch_map[value["epoch_id"]]
        _require(index(value["pre_position"]["publication_index"]) >= index(epoch["initial_position"]["publication_index"]),
                 "source_input_before_initial_reservation")
        _require(index(epoch["after_input_ordinal"]) < ordinal <= index(seals[value["epoch_id"]]["after_input_ordinal"]),
                 "source_input_epoch_fence_mismatch")
        segment_offset = segment_map[value["segment_id"]]
        end = final if segment_offset + 1 == len(segments) else index(segments[segment_offset + 1]["after_input_ordinal"])
        _require(index(segments[segment_offset]["after_input_ordinal"]) < ordinal <= end,
                 "source_input_actor_fence_mismatch")
        point = epoch_offset, index(value["pre_position"]["publication_index"])
        _require(previous_point is None or previous_point <= point, "source_input_native_prefix_order_regressed")
        previous_point = point
        for boundary in boundaries:
            boundary_point = epoch_map[boundary["position"]["epoch_id"]][0], index(boundary["position"]["publication_index"])
            fence = index(boundary["after_input_ordinal"])
            _require(point == boundary_point or (ordinal <= fence if point < boundary_point else ordinal > fence),
                     "source_input_boundary_fence_mismatch")
        if order["status"] == "unproven":
            _require(value["pre_capture"] is None and value["catalog"] is None
                     and value["outcome"]["mapping_status"] == "capture_missing"
                     and value["outcome"]["selected_action"] is None and value["outcome"]["match_count"] == 0,
                     "source_unproven_input_has_capture")
    validate_actor_boundaries()
    _require(index(epochs[0]["after_input_ordinal"]) == index(segments[0]["after_input_ordinal"]) == 0,
             "source_initial_input_fence_invalid")
    previous = 0
    for value in segments:
        cut = index(value["after_input_ordinal"])
        _require(previous <= cut <= final, "source_actor_input_fence_invalid")
        previous = cut
    previous = 0
    for value in epochs:
        cut = index(value["after_input_ordinal"])
        _require(previous <= cut <= final, "source_epoch_input_fence_invalid")
        if value["predecessor_seal"] is not None:
            _require(index(value["predecessor_seal"]["after_input_ordinal"]) == cut,
                     "source_epoch_predecessor_input_fence_mismatch")
        _require(cut <= index(seals[value["epoch_id"]]["after_input_ordinal"]) <= final,
                 "source_seal_input_fence_invalid")
        previous = cut
    previous = 0
    pause = None
    for value in boundaries:
        cut = index(value["after_input_ordinal"])
        _require(previous <= cut <= final, "source_boundary_input_fence_invalid")
        if value["kind"] == "pause":
            pause = cut
        elif pause is not None:
            _require(cut == pause, "source_input_admitted_while_paused")
            if value["kind"] in {"resume", "close"}:
                pause = None
        for interval in value["paused_intervals"]:
            _require(index(interval["after_input_ordinal"]) == index(interval["through_input_ordinal"]) == cut,
                     "source_paused_input_fence_invalid")
        if value["kind"] == "epoch_transition":
            _require(index(epoch_map[value["position"]["epoch_id"]][1]["after_input_ordinal"]) == cut,
                     "source_epoch_boundary_input_fence_mismatch")
        previous = cut
    _require(index(boundaries[-1]["after_input_ordinal"]) == index(seals[epochs[-1]["epoch_id"]]["after_input_ordinal"]) == final,
             "source_final_input_fence_mismatch")
    for value in drains:
        _require(index(value["after_input_ordinal"]) == index(seals[value["epoch_id"]]["after_input_ordinal"]),
                 "source_final_drain_input_fence_mismatch")
