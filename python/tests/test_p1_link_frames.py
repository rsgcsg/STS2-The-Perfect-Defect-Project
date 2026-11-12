"""fast-env-link-2 frames (P-REWRITE v3 E7): delta frames encode exactly like full lines."""
from __future__ import annotations

import copy
import glob
import json
import marshal
import os
import re
import struct
from typing import Any

import pytest
from link2_writer import FrameWriter, link2_line, text
from test_p1_input_v2 import public_state

from spireagent.json_boundary import BoundaryError
from stpd.p1.data import digest, json_bytes
from stpd.p1.export import input_spec
from stpd.p1.fast_aggregator import encode_link_decision, link_lane_stats, release_link_lane
from stpd.p1.input import V2_INPUT_SPEC
from stpd.p1.link_frames import FrameRefused, Lanes, rebuild, rebuild_lines, split_line
from stpd.p1.rows_encoder import RowsEncoder
from stpd.p1.vocabulary import public_vocabulary


@pytest.fixture(scope="module")
def vocabulary() -> dict[str, Any]:
    return public_vocabulary(V2_INPUT_SPEC)


def _views(count: int) -> list[dict[str, Any]]:
    """One game's observations: some values change, lists grow, shrink and reorder."""
    first = public_state()
    out = []
    for step in range(count):
        view = copy.deepcopy(first)
        cards = [{"id": i, "x": [1]} for i in range(step % 3 + 1)]
        view["status"] = {"hp": 70 - step, "cards": cards}
        catalog = view["catalog"]
        view["catalog"] = catalog[step % len(catalog):] + catalog[:step % len(catalog)]
        out.append(view)
    return out


def _head(row_id: int, game: str, step: int, encoded: Any) -> str:
    return (f'{{"schema":"sts2.policy-runtime/fast-env-link-2","type":"decision","row_id":{row_id},'
            f'"game_instance_id":"{game}","step_index":{step},"consumption_id":"c-{game}-{step}",'
            f'"catalog_len":{encoded.catalog_len},"catalog_sha256":"{encoded.catalog_sha256}",'
            f'"observation_sha256":"{encoded.observation_sha256}",'
            f'"in_combat":{json.dumps(encoded.in_combat)},'
            f'"floor":{3 if encoded.floor is None else encoded.floor},')


def _game(vocabulary: dict[str, Any], game: str, views: list[dict[str, Any]], first_row: int = 1):
    """(link-2 lines, full encodings) of one game, full encodings by the link-1 path."""
    full = RowsEncoder(vocabulary)
    spec = input_spec(vocabulary)
    writer, lines, encodings = FrameWriter(), [], []
    for step, view in enumerate(views):
        encoded = full.encode(json.dumps(view).encode())
        frame, parts = writer.frame(view, first_row + step)
        lines.append(link2_line(_head(first_row + step, game, step, encoded), spec, frame, parts))
        encodings.append(encoded)
    return lines, encodings


def test_delta_frames_encode_exactly_like_full_observations(vocabulary):
    views = _views(12)
    lines, full = _game(vocabulary, "g1", views)
    encoder = RowsEncoder(vocabulary)
    for line, expected, view in zip(lines, full, views, strict=True):
        meta, row = encode_link_decision(encoder, memoryview(line))
        assert meta["frame"] is None and meta["binding"] is None
        assert row == expected.row and meta["row_sha256"] == expected.row_sha256
        assert meta["observation_sha256"] == digest(json_bytes(view)) == expected.observation_sha256
        assert meta["catalog_sha256"] == expected.catalog_sha256
    stats = link_lane_stats(encoder)
    assert stats["frames"] == 12 and stats["full_frames"] == 1 and stats["refused"] == 0
    assert sum(len(split_line(line)[1]) for line in lines[1:]) < sum(
        len(split_line(line)[1]) for line in lines[:1]) * 11
    assert [json.dumps(v) for v in rebuild_lines(lines)] == [json.dumps(v) for v in views]
    release_link_lane(encoder, "g1")
    assert link_lane_stats(encoder)["lanes"] == 0


def _mutate_part(line: bytes) -> bytes:
    """Change one byte inside a part, keep it valid JSON with the same length."""
    header, parts = split_line(line)
    for part in parts:
        match = re.search(rb"\d", part)
        if match:
            digit = part[match.start():match.start() + 1]
            changed = part[:match.start()] + (b"1" if digit != b"1" else b"2") + part[match.end():]
            return line.replace(part, changed, 1)
    raise AssertionError("no digit in any part")


def _mutate_frame(line: bytes, edit) -> bytes:
    header, parts = split_line(line)
    frame = header["input"]["frame"]
    edit(frame)
    head = line[:line.find(b'"input":')].decode()
    return link2_line(head, header["input"]["input_spec"], frame, [p.decode() for p in parts])


def _first_ref(frame: dict[str, Any]) -> None:
    for entry in frame["layout"]:
        if isinstance(entry, list) and any(k >= 0 for k in entry):
            at = next(i for i, k in enumerate(entry) if k >= 0)
            entry[at] ^= 1  # another element of the base list (or past its end)
            return
    raise AssertionError("no list reference")


def _claim_same(frame: dict[str, Any]) -> None:
    frame["layout"][frame["layout"].index("n")] = "s"


def _set(name: str, value: Any):
    return lambda frame: frame.__setitem__(name, value)


def _longer(frame: dict[str, Any]) -> None:
    frame["lengths"][0] += 1


BREAKS = {
    "part byte": (_mutate_part, {"frame_sha256"}),
    "layout ref": (_first_ref, {"frame_sha256", "frame_layout"}),
    "same value": (_claim_same, {"frame_parts", "frame_lengths", "frame_sha256"}),
    "base row": (_set("base_row_id", 99), {"frame_base"}),
    "restart": (_set("base_row_id", None), {"frame_restart"}),
    "lengths": (_longer, {"frame_lengths"}),
    "frame digest": (_set("frame_sha256", "0" * 64), {"frame_sha256"}),
}


@pytest.mark.parametrize("name", sorted(BREAKS))
def test_one_broken_frame_is_refused_and_the_game_cannot_continue(vocabulary, name):
    lines, _ = _game(vocabulary, "g1", _views(6))
    edit, codes = BREAKS[name]
    broken = edit(lines[3]) if edit is _mutate_part else _mutate_frame(lines[3], edit)
    assert broken != lines[3]
    encoder = RowsEncoder(vocabulary)
    for line in lines[:3]:
        assert encode_link_decision(encoder, memoryview(line))[0]["frame"] is None
    try:
        meta, row = encode_link_decision(encoder, memoryview(broken))
        code = meta["frame"]
        assert row == b""
    except FrameRefused as error:  # the parts cannot even be sliced
        code = error.code
    assert code in codes
    # The game's frame is gone: the next (valid) line refers to a base nobody has.
    meta, row = encode_link_decision(encoder, memoryview(lines[4]))
    assert meta["frame"] == "frame_base" and row == b""


def test_lost_line_and_wrong_observation_sha256_are_refused(vocabulary):
    views = _views(4)
    lines, full = _game(vocabulary, "g1", views)
    encoder = RowsEncoder(vocabulary)
    encode_link_decision(encoder, memoryview(lines[0]))
    assert encode_link_decision(encoder, memoryview(lines[2]))[0]["frame"] == "frame_base"
    # A frame that rebuilds but whose line claims another observation_sha256.
    encoder = RowsEncoder(vocabulary)
    encode_link_decision(encoder, memoryview(lines[0]))
    wrong = lines[1].replace(full[1].observation_sha256.encode(), b"f" * 64, 1)
    meta, row = encode_link_decision(encoder, memoryview(wrong))
    assert meta["frame"] == "frame_observation_sha256" and row == b""
    assert link_lane_stats(encoder)["refused"] == 1 and link_lane_stats(encoder)["lanes"] == 0


def test_games_are_separate_lanes(vocabulary):
    a, full_a = _game(vocabulary, "ga", _views(5), first_row=1)
    b, full_b = _game(vocabulary, "gb", list(reversed(_views(5))), first_row=100)
    encoder = RowsEncoder(vocabulary)
    for x, y, ex, ey in zip(a, b, full_a, full_b, strict=True):
        assert encode_link_decision(encoder, memoryview(x))[1] == ex.row
        assert encode_link_decision(encoder, memoryview(y))[1] == ey.row
    assert link_lane_stats(encoder)["lanes"] == 2


def test_frame_validation_refuses_malformed_frames():
    writer = FrameWriter()
    frame, parts = writer.frame({"a": 1, "b": [1, 2]}, 1)
    raw = [p.encode() for p in parts]
    values, state = rebuild(None, 1, frame, raw)
    assert values == {"a": 1, "b": [1, 2]}
    for edit in ({"keys": ["a", "a"]}, {"keys": ["a-b", "b"]}, {"layout": ["n"]},
                 {"layout": ["x", "n"]},
                 {"base_row_id": 0}, {"frame_sha256": "X" * 64}, {"extra": 1}):
        with pytest.raises(FrameRefused, match="frame_fields"):
            rebuild(None, 1, {**frame, **edit}, raw)
    with pytest.raises(FrameRefused, match="frame_fields"):
        rebuild(None, 0, frame, raw)
    with pytest.raises(FrameRefused, match="frame_layout"):  # a list sent as one value
        rebuild(None, 1, {**frame, "layout": ["n", "n"], "lengths": [1, 5]}, [b"1", b"[1,2]"])
    with pytest.raises(FrameRefused, match="frame_parts"):
        rebuild(None, 1, frame, raw + [b"3"])
    with pytest.raises(FrameRefused, match="frame_line_layout"):
        split_line(b'{"input":{}}')
    with pytest.raises(BoundaryError):  # parts are public JSON, parsed strictly
        rebuild(None, 1, {**frame, "lengths": [3, 1, 1]}, [b"NaN", b"1", b"2"])
    lanes = Lanes(max_lanes=2)
    for name in "xyz":
        lanes.store(name, state)
    assert list(lanes.frames) == ["y", "z"] and lanes.stats["evicted"] == 1


def test_composed_list_marshal_is_marshal_v2():
    for value in ([], [1, "a", None, True, 1.5, {"k": [1, {"z": None}]}], [[1, [2]], []]):
        elements = b"".join(marshal.dumps(v, 2) for v in value)
        composed = b"[" + struct.pack("<i", len(value)) + elements
        assert composed == marshal.dumps(value, 2)


REPLAY = os.environ.get("STPD_LINK2_REPLAY")


@pytest.mark.skipif(not REPLAY, reason="STPD_LINK2_REPLAY (frame-delta-replay.mjs --out) not set")
def test_real_frames_replay_identically():
    """Real recorded games: each link-2 row equals the link-1 row of the same decision."""
    from stpd.p1.input import V2_R19_INPUT_SPEC
    vocabulary = public_vocabulary(V2_R19_INPUT_SPEC)
    full, delta = RowsEncoder(vocabulary), RowsEncoder(vocabulary)
    rows = 0
    for link1 in sorted(glob.glob(os.path.join(REPLAY, "*.link1.jsonl"))):
        link2 = link1.replace(".link1.", ".link2.")
        with open(link1, "rb") as a, open(link2, "rb") as b:
            for x, y in zip(a.read().splitlines(), b.read().splitlines(), strict=True):
                message = json.loads(x)
                expected = full.encode(text(message["input"]["observation"]).encode())
                meta, row = encode_link_decision(delta, memoryview(y))
                assert meta["frame"] is None and row == expected.row
                assert meta["observation_sha256"] == message["observation_sha256"]
                assert meta["observation_sha256"] == expected.observation_sha256
                rows += 1
    assert rows > 0 and link_lane_stats(delta)["refused"] == 0
