"""Cached Input-v2 encoder: public observation JSON -> `stpd/rows-v2-row-1` row.

This is the fast_chain encoder (P-REWRITE WP3). It produces exactly the frame
`FrameCompiler.compile` produces (`compile_frame(project(observation))`), element
for element, and is checked against it (`verify_every`, tests, R7 double runs).
It adds no semantics: no new field, order, legality or hidden input. Until the
WP8 version step makes it the single production implementation, the reference
in `input.py` / `input_order.py` / `tensor_input.py` stays the oracle.

Correspondence with the reference (one structure, read side by side):

    _Encoder._visit            == input.project.visit, v2 symmetric branch only
                                  (relation is never None, so the v1/sorted
                                  branches are not reachable and not copied)
    _Assembly views/pending    == project(): views extension, pending loop,
                                  reference-only nodes, roles
    _Assembly positions        == input_order.order_frame(sorted_positions=False)
    _Assembly tensors          == tensor_input.compile_frame + reduction
    identity_paths, unordered_domains, public_field_path, signed_log and the
    InputEncoding tokenizer cache are imported from the reference, not copied.

The only addition is caching. A subtree (every top-level value and every list
element that is a dict or list) is visited once per distinct content and replayed
afterwards. Its cache key holds everything the reference visit of that subtree
reads: the subtree's own JSON bytes in insertion order (declaration order within
one entity follows dict insertion order), its kind path, and the identity
relation scope of its source path (see `_Encoder._scope`). Row numbers,
positions of the subtree root, parent edges, alias views, pending references,
ordering positions and text indices are always assembled per observation.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch

from spireagent.json_boundary import BoundaryError

from .frame_compiler import FrameCompiler, compiler_digest, observation_hashes
from .input import is_v2_encoding, public_field_path, signed_log, validate_input_spec
from .input_identity import identity_paths
from .input_order import unordered_domains
from .rows_encoder_wire import FORMAT as ROW_FORMAT
from .rows_encoder_wire import encode_row
from .tensor_input import Reduction, TensorFrame

IMPLEMENTATION = "stpd/p1-rows-encoder-cached-1"
DEFAULT_CACHE_BYTES = 256 * 1024 * 1024
_INDEX = re.compile(r"\[\d+\]")
_READ_INDEX = re.compile(r"^\$\.extra_reads\[(\d+)\]")
_CONSTANT = frozenset({"schema", "profile", "revision"})
_I64 = np.int64
_EMPTY_I = np.zeros(0, dtype=_I64)
_EMPTY_F = np.zeros(0, dtype=np.float32)
_EMPTY_B = np.zeros(0, dtype=bool)


def encoder_digest() -> str:
    """Bind this implementation and the reference compiler it must equal."""
    root = Path(__file__).parent
    sources = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
               for name in ("rows_encoder.py", "rows_encoder_wire.py")}
    body = json.dumps({"compiler_sha256": compiler_digest(), "implementation": IMPLEMENTATION,
                       "row_format": ROW_FORMAT, "sources": sources},
                      sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def _array(values: list[Any], dtype: Any) -> np.ndarray:
    return np.array(values, dtype=dtype) if values else np.zeros(0, dtype=dtype)


@dataclass(frozen=True, slots=True)
class _Unit:
    """One visited subtree in local rows (root = row 0); immutable once cached.

    Entry arrays keep each entity's own list order (the reference appends an
    entity's identities/text/numeric/children only during that entity's visit).
    `decl` and `pend` keep the visit's time order, which fixes alias-view order
    and the creation order of reference-only nodes.
    """
    n: int
    kinds: tuple[str, ...]
    kind_idx: tuple[int, ...]
    positions: tuple[int, ...]
    id_row: np.ndarray
    id_val: np.ndarray
    id_field: np.ndarray
    num_row: np.ndarray
    num_field: np.ndarray
    num_val: np.ndarray  # float32 signed_log value; 0 where missing
    num_missing: np.ndarray
    txt_row: np.ndarray
    txt_field: np.ndarray
    txt_local: np.ndarray  # index into txt_uniq
    txt_uniq: tuple[str, ...]  # sorted unique text values of this subtree
    tree_row: np.ndarray
    tree_dst: np.ndarray
    decl: tuple[tuple[int, str], ...]
    pend: tuple[tuple[int, str], ...]
    nbytes: int


class _Builder:
    """Mutable rows of one subtree being visited; child units are spliced in."""

    __slots__ = ("kinds", "kind_idx", "positions", "ids", "nums", "texts", "tree",
                 "decl", "pend", "frags")

    def __init__(self) -> None:
        self.kinds: list[str] = []
        self.kind_idx: list[int] = []
        self.positions: list[int] = []
        self.ids: list[tuple[int, int, int]] = []
        self.nums: list[tuple[int, int, float, bool]] = []
        self.texts: list[tuple[int, int, str]] = []
        self.tree: list[tuple[int, int]] = []
        self.decl: list[tuple[int, str]] = []
        self.pend: list[tuple[int, str]] = []
        self.frags: list[tuple[_Unit, int]] = []

    def replay(self, unit: _Unit, position: int, parent: int | None) -> None:
        offset = len(self.kinds)
        self.kinds.extend(unit.kinds)
        self.kind_idx.extend(unit.kind_idx)
        self.positions.extend(unit.positions)
        self.positions[offset] = position
        if parent is not None:
            self.tree.append((parent, offset))
        if unit.decl:
            self.decl.extend((row + offset, alias) for row, alias in unit.decl)
        if unit.pend:
            self.pend.extend((row + offset, alias) for row, alias in unit.pend)
        self.frags.append((unit, offset))

    def finish(self) -> _Unit:
        frags = self.frags
        lengths: dict[str, list[int]] = {}

        def joined(name: str, own: np.ndarray, shift: bool) -> np.ndarray:
            parts = [own] + [getattr(unit, name) for unit, _ in frags]
            if not frags:
                return own
            out = np.concatenate(parts)
            if shift:
                key = name.split("_")[0]
                if key not in lengths:
                    lengths[key] = [len(own)] + [len(getattr(unit, name)) for unit, _ in frags]
                out += np.repeat(np.array([0] + [offset for _, offset in frags], dtype=_I64),
                                 lengths[key])
            return out

        ids = np.array(self.ids, dtype=_I64).reshape(-1, 3)
        nums = self.nums
        tree = np.array(self.tree, dtype=_I64).reshape(-1, 2)
        texts = self.texts
        uniq_set = {text for _, _, text in texts}
        for unit, _ in frags:
            uniq_set.update(unit.txt_uniq)
        uniq = tuple(sorted(uniq_set))
        lookup = {text: i for i, text in enumerate(uniq)}
        local_parts = [_array([lookup[text] for _, _, text in texts], _I64)]
        for unit, _ in frags:
            if len(unit.txt_local):
                local_parts.append(np.array([lookup[text] for text in unit.txt_uniq],
                                            dtype=_I64)[unit.txt_local])
            else:
                local_parts.append(_EMPTY_I)
        txt_local = np.concatenate(local_parts) if frags else local_parts[0]
        tree_dst = joined("tree_dst", tree[:, 1].copy(), False)
        if frags:
            # Child edges shift by the owning fragment offset, exactly like rows.
            counts = [len(tree)] + [len(unit.tree_dst) for unit, _ in frags]
            tree_dst += np.repeat(np.array([0] + [o for _, o in frags], dtype=_I64), counts)
        unit = _Unit(
            n=len(self.kinds), kinds=tuple(self.kinds), kind_idx=tuple(self.kind_idx),
            positions=tuple(self.positions),
            id_row=joined("id_row", ids[:, 0].copy(), True),
            id_val=joined("id_val", ids[:, 1].copy(), False),
            id_field=joined("id_field", ids[:, 2].copy(), False),
            num_row=joined("num_row", _array([e[0] for e in nums], _I64), True),
            num_field=joined("num_field", _array([e[1] for e in nums], _I64), False),
            num_val=joined("num_val", _array([e[2] for e in nums], np.float32), False),
            num_missing=joined("num_missing", _array([e[3] for e in nums], bool), False),
            txt_row=joined("txt_row", _array([e[0] for e in texts], _I64), True),
            txt_field=joined("txt_field", _array([e[1] for e in texts], _I64), False),
            txt_local=txt_local, txt_uniq=uniq,
            tree_row=joined("tree_row", tree[:, 0].copy(), True), tree_dst=tree_dst,
            decl=tuple(self.decl), pend=tuple(self.pend), nbytes=0)
        size = 64 * unit.n + sum(len(text) + 56 for text in uniq) + 64 * (
            len(unit.decl) + len(unit.pend)) + sum(
            getattr(unit, name).nbytes for name in (
                "id_row", "id_val", "id_field", "num_row", "num_field", "num_val",
                "num_missing", "txt_row", "txt_field", "txt_local", "tree_row", "tree_dst"))
        object.__setattr__(unit, "nbytes", size)
        return unit


class _Cache:
    """Process-local LRU bounded by an estimate of retained bytes."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.size = 0
        self.items: OrderedDict[tuple[Any, ...], _Unit] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: tuple[Any, ...]) -> _Unit | None:
        unit = self.items.get(key)
        if unit is None:
            self.misses += 1
            return None
        self.hits += 1
        self.items.move_to_end(key)
        return unit

    def put(self, key: tuple[Any, ...], unit: _Unit) -> None:
        cost = unit.nbytes + len(key[-1]) + 200
        if cost > self.limit:
            return
        self.items[key] = unit
        self.size += cost
        while self.size > self.limit:
            old_key, old = self.items.popitem(last=False)
            self.size -= old.nbytes + len(old_key[-1]) + 200


def _reduction(counts: np.ndarray, values: np.ndarray) -> Reduction:
    """tensor_input.reduction over CSR rows (values grouped by owner, in order)."""
    owners_total = len(counts)
    if owners_total == 0:
        return Reduction((), torch.zeros(0, dtype=torch.long))
    widths, first = np.unique(counts, return_index=True)
    widths = widths[np.argsort(first, kind="stable")]
    entry_width = np.repeat(counts, counts)
    buckets = []
    order = []
    for width in widths.tolist():
        owners = np.flatnonzero(counts == width)
        buckets.append((width, len(owners), torch.from_numpy(
            np.ascontiguousarray(values[entry_width == width], dtype=_I64))))
        order.append(owners)
    inverse = np.empty(owners_total, dtype=_I64)
    inverse[np.concatenate(order)] = np.arange(owners_total, dtype=_I64)
    return Reduction(tuple(buckets), torch.from_numpy(inverse))


@dataclass(frozen=True, slots=True)
class EncodedRow:
    """One row plus the public bindings the inference request carries."""
    row: bytes
    row_sha256: str
    observation_sha256: str
    catalog_sha256: str
    catalog_len: int
    in_combat: bool
    floor: int | None  # public run.content.floor; the caller keeps the per-game max


class RowsEncoder:
    """One process-local cached encoder for one bound v2 vocabulary.

    `verify_every=k` re-runs the reference compiler on every k-th observation and
    raises `rows_encoder_reference_mismatch` on any byte difference (R7 double
    run). It never falls back: a mismatch is an error, not a slower path.
    """

    def __init__(self, vocabulary: dict[str, Any], *, cache_bytes: int = DEFAULT_CACHE_BYTES,
                 verify_every: int = 0) -> None:
        self._reference = FrameCompiler(vocabulary)  # validates and copies vocabulary
        self._encoding = self._reference._encoding
        self._projection = validate_input_spec(self._encoding.vocabulary["input_spec"])
        if not is_v2_encoding(self._projection):
            raise BoundaryError("p1_rows_encoder", "v2_input_required")
        tables = self._encoding.tables
        self._kinds, self._fields, self._ids = tables["kinds"], tables["fields"], tables["ids"]
        self._cache = _Cache(cache_bytes)
        self._tokens: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._verify_every = verify_every
        self._count = 0
        self.identity = {**self._reference.identity,
                         "encoder": {"implementation": IMPLEMENTATION,
                                     "sha256": encoder_digest()},
                         "row_format": ROW_FORMAT}

    @property
    def cache_stats(self) -> dict[str, int]:
        cache = self._cache
        return {"hits": cache.hits, "misses": cache.misses, "units": len(cache.items),
                "bytes": cache.size}

    def encode(self, raw: bytes | bytearray | memoryview) -> EncodedRow:
        """Encode one observation given as its JSON bytes (JSON types only)."""
        try:
            observation = json.loads(bytes(raw))
        except (UnicodeDecodeError, ValueError) as error:
            raise BoundaryError("p1_rows_encoder", "invalid_public_json") from error
        bindings = observation_hashes(observation)
        frame = self.frame(observation)
        row = encode_row(frame)
        run = observation.get("run")
        floor = ((run or {}).get("content") or {}).get("floor") if isinstance(run, dict) else None
        combat = observation.get("combat")
        return EncodedRow(
            row=row, row_sha256=hashlib.sha256(row).hexdigest(),
            observation_sha256=bindings["observation_sha256"],
            catalog_sha256=bindings["catalog_sha256"],
            catalog_len=len(observation["catalog"]),
            in_combat=isinstance(combat, dict) and combat.get("availability") == "available",
            floor=floor if type(floor) is int else None)

    def frame(self, observation: dict[str, Any]) -> TensorFrame:
        """The reference TensorFrame for a json.loads observation (JSON types only)."""
        frame = _Encoder(self, observation).run()
        self._count += 1
        if self._verify_every and self._count % self._verify_every == 0:
            expected = self._reference.compile(observation)
            if encode_row(expected) != encode_row(frame):
                raise BoundaryError("p1_rows_encoder", "rows_encoder_reference_mismatch")
        return frame

    def tokens(self, text: str) -> tuple[np.ndarray, np.ndarray]:
        value = self._tokens.get(text)
        if value is None:
            ids, numbers = self._encoding.tokenize(text)
            value = (np.array(ids, dtype=_I64), np.array(numbers, dtype=np.float32))
            self._tokens[text] = value
        return value


class _Encoder:
    """One observation: the reference visit with subtree replay, then assembly."""

    def __init__(self, owner: RowsEncoder, observation: dict[str, Any]) -> None:
        self.owner = owner
        self.observation = observation
        self.relation: Callable[[str], str | None] | None = None
        self._scene: tuple[Any, ...] | None = None
        self._reads: list[tuple[Any, bool]] | None = None

    # -- project() ---------------------------------------------------------
    def run(self) -> TensorFrame:
        observation, spec = self.observation, self.owner._projection
        if (observation.get("profile"), observation.get("revision")) != (
                spec["profile"], spec["revision"]):
            raise ValueError("P1 M-std observation differs from bound InputSpec revision")
        self.relation = identity_paths(observation)
        root = _Builder()
        self._visit(root, observation, "$", 0, None, "$")
        return self._assemble(root.finish())

    def _scope(self, source_path: str) -> tuple[Any, ...]:
        """Everything identity_paths(observation)(p) reads for p under source_path.

        Derived from input_identity.identity_paths (tests pin its digest):
        * under $.extra_reads[i]: the exact path, read i's kind/content-is-dict
          (exact typed card/shop roots are per index) and whether any read is a
          surface card with dict content (adds the display_owner reference path);
        * under $.scene: the exact path, revision, native_kind and the exact event
          tooltip card roots (all native/revision-dependent paths are surface paths);
        * elsewhere: the index-normalized path only; no declared, reference or card
          root outside scene/extra_reads depends on the observation or on indices.
        """
        if source_path.startswith("$.extra_reads"):
            reads = self._read_signature()
            match = _READ_INDEX.match(source_path)
            detail: Any = reads[int(match[1])] if match else tuple(reads)
            return ("x", source_path, detail,
                    any(kind == "surface_card" and is_dict for kind, is_dict in reads))
        if source_path.startswith("$.scene"):
            return ("s", source_path, *self._scene_signature())
        return ("n", _INDEX.sub("[]", source_path))

    def _read_signature(self) -> list[tuple[Any, bool]]:
        if self._reads is None:
            self._reads = [(read.get("kind"), isinstance(read.get("content"), dict))
                           for read in self.observation.get("extra_reads", [])]
        return self._reads

    def _scene_signature(self) -> tuple[Any, ...]:
        if self._scene is None:
            observation = self.observation
            native = observation.get("scene", {}).get("native_kind")
            tips = []
            if native in {"event_option", "event_dialogue"}:
                body = observation.get("scene", {}).get("content", {}).get("surface", {})
                for i, option in enumerate(body.get("options", [])):
                    for j, tip in enumerate(option.get("tooltips", [])):
                        if tip.get("kind") == "card" and isinstance(tip.get("card"), dict):
                            tips.append((i, j))
            self._scene = (observation.get("revision"), native, tuple(tips))
        return self._scene

    def _child(self, b: _Builder, item: Any, path: str, position: int, parent: int,
               source_path: str) -> None:
        if not isinstance(item, (dict, list)):
            self._visit(b, item, path, position, parent, source_path)
            return
        raw = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        key = (path, self._scope(source_path), raw)
        cache = self.owner._cache
        unit = cache.get(key)
        if unit is None:
            sub = _Builder()
            self._visit(sub, item, path, 0, None, source_path)
            unit = sub.finish()
            cache.put(key, unit)
        b.replay(unit, position, parent)

    def _visit(self, b: _Builder, value: Any, path: str, position: int,
               parent: int | None, source_path: str) -> None:
        # input.project.visit; v2: relation is not None and views are symmetric.
        relation = self.relation
        assert relation is not None
        fields = self.owner._fields
        row = len(b.kinds)
        b.kinds.append(path)
        b.kind_idx.append(self.owner._kinds.get(path, 0))
        b.positions.append(position)
        if parent is not None:
            b.tree.append((parent, row))
        if isinstance(value, dict):
            field_roles = {key: relation(public_field_path(source_path, key))
                           for key, item in value.items() if isinstance(item, str)}
            for key, role in field_roles.items():
                if role == "declared":
                    b.decl.append((row, value[key]))
            for key, item in sorted(value.items()):
                if key == "envelope" and path == "$":
                    continue
                if (key in _CONSTANT and path == "$") or (
                        key == "choice_id" and path == "$.catalog[]"):
                    continue
                role = field_roles.get(key)
                if role == "declared" and isinstance(item, str):
                    continue
                field = fields.get(key, 0)
                if role == "reference" and isinstance(item, str):
                    b.pend.append((row, item))
                    b.texts.append((row, field, "<REF>"))
                    continue
                if isinstance(item, (dict, list)):
                    self._child(b, item, public_field_path(path, key), 0, row,
                                public_field_path(source_path, key))
                elif item is None:
                    b.texts.append((row, field, "<MISSING>"))
                    b.nums.append((row, field, 0.0, True))
                elif type(item) is bool:
                    b.texts.append((row, field, "true" if item else "false"))
                elif type(item) in {int, float}:
                    if not math.isfinite(item):
                        raise ValueError("non-finite public numeric field")
                    b.nums.append((row, field, signed_log(float(item)), False))
                elif isinstance(item, str):
                    if key.endswith("_id"):
                        b.ids.append((row, self.owner._ids.get(item, 0), field))
                    else:
                        b.texts.append((row, field, item))
                else:
                    raise ValueError("unsupported public field")
        elif isinstance(value, list):
            item_path = path + "[]"
            for i, item in enumerate(value):
                self._child(b, item, item_path, i, row, source_path + f"[{i}]")
        elif isinstance(value, str) and relation(source_path) == "reference":
            b.pend.append((row, value))
            b.texts.append((row, fields.get("value", 0), "<REF>"))
        elif value is None:
            field = fields.get("value", 0)
            b.texts.append((row, field, "<MISSING>"))
            b.nums.append((row, field, 0.0, True))
        elif type(value) in {int, float}:
            b.nums.append((row, fields.get("value", 0), signed_log(float(value)), False))
        else:
            b.texts.append((row, fields.get("value", 0), str(value)))

    # -- project() tail, order_frame, compile_frame -------------------------
    def _assemble(self, root: _Unit) -> TensorFrame:
        owner, observation = self.owner, self.observation
        kinds = list(root.kinds)
        kind_idx = list(root.kind_idx)
        positions = list(root.positions)
        # Alias views in first-declaration order (aliases.setdefault + members).
        aliases: dict[str, int] = {}
        views: dict[str, list[int]] = {}
        for row, alias in root.decl:
            aliases.setdefault(alias, row)
            members = views.setdefault(alias, [])
            if row not in members:
                members.append(row)
        view_src: list[int] = []
        view_dst: list[int] = []
        for members in views.values():
            if len(members) > 1:
                for row in members:
                    for other in members:
                        if other != row:
                            view_src.append(row)
                            view_dst.append(other)
        extra_text: list[tuple[int, int, str]] = []
        pend_src: list[int] = []
        pend_dst: list[int] = []
        for row, alias in root.pend:
            if alias not in aliases:
                # Reference-only node: public owner identity without an entity body.
                aliases[alias] = len(kinds)
                kinds.append("public_reference_only")
                kind_idx.append(owner._kinds.get("public_reference_only", 0))
                positions.append(0)
                extra_text.append((aliases[alias], owner._fields.get("availability", 0),
                                   "reference_only"))
                views[alias] = [aliases[alias]]
            for target in views[alias]:
                pend_src.append(row)
                pend_dst.append(target)
        count = len(kinds)
        choices = observation["catalog"]
        sources: list[list[int]] = []
        targets: list[list[int]] = []
        for choice in choices:
            sources.append(list(views.get(choice.get("subject"), [])))
            targets.append(list(views.get(choice.get("target"), [])))
        ref_src = np.concatenate([root.tree_row, _array(view_src, _I64), _array(pend_src, _I64)])
        ref_dst = np.concatenate([root.tree_dst, _array(view_dst, _I64), _array(pend_dst, _I64)])
        order = np.argsort(ref_src, kind="stable")
        ref_dst = ref_dst[order]
        ref_counts = np.bincount(ref_src, minlength=count).astype(_I64)
        ref_offsets = np.concatenate([[0], np.cumsum(ref_counts)])
        self._order_positions(kinds, positions, ref_dst, ref_offsets)

        # compile_frame
        texts_set = set(root.txt_uniq)
        texts_set.update(text for _, _, text in extra_text)
        texts = sorted(texts_set)
        token_arrays = [owner.tokens(text) for text in texts]
        lengths = np.array([len(ids) for ids, _ in token_arrays], dtype=_I64)
        width = int(lengths.max()) if len(lengths) else 0
        text_ids = np.zeros((len(texts), width), dtype=_I64)
        text_numbers = np.zeros((len(texts), width, 1), dtype=np.float32)
        if len(texts):
            real = np.arange(width)[None, :] < lengths[:, None]
            text_ids[real] = np.concatenate([ids for ids, _ in token_arrays])
            text_numbers[..., 0][real] = np.concatenate([nums for _, nums in token_arrays])

        id_order = np.argsort(root.id_row, kind="stable")
        id_rows = root.id_row[id_order]
        num_order = np.argsort(root.num_row, kind="stable")
        num_rows = root.num_row[num_order]
        num_missing = root.num_missing[num_order]
        num_field = root.num_field[num_order]
        present = ~num_missing
        present_count = int(present.sum())
        number_index = np.where(present, np.cumsum(present) - 1,
                                present_count + np.cumsum(num_missing) - 1).astype(_I64)

        lookup = {text: i for i, text in enumerate(texts)}
        txt_global = np.array([lookup[text] for text in root.txt_uniq], dtype=_I64)
        txt_rows = np.concatenate([root.txt_row, _array([e[0] for e in extra_text], _I64)])
        txt_fields = np.concatenate([root.txt_field, _array([e[1] for e in extra_text], _I64)])
        txt_index = np.concatenate([
            txt_global[root.txt_local] if len(root.txt_local) else _EMPTY_I,
            _array([lookup[e[2]] for e in extra_text], _I64)])
        txt_order = np.argsort(txt_rows, kind="stable")
        txt_rows = txt_rows[txt_order]

        def tensor(values: np.ndarray) -> torch.Tensor:
            return torch.from_numpy(np.ascontiguousarray(values, dtype=_I64))

        tensors = {
            "text_ids": torch.from_numpy(text_ids),
            "text_numbers": torch.from_numpy(text_numbers),
            "text_lengths": torch.from_numpy(lengths),
            "identities": tensor(root.id_val[id_order]),
            "identity_fields": tensor(root.id_field[id_order]),
            "kinds": tensor(np.array(kind_idx, dtype=_I64)),
            "positions": tensor(np.array(positions, dtype=_I64)),
            "number_fields": tensor(num_field[present]),
            "missing_fields": tensor(num_field[num_missing]),
            "text_fields": tensor(txt_fields[txt_order]),
            "text_indices": tensor(txt_index[txt_order]),
            "actions": tensor(np.array([owner._encoding.index("actions", c["kind"])
                                        for c in choices], dtype=_I64).reshape(-1)),
            "numbers": torch.from_numpy(np.ascontiguousarray(
                root.num_val[num_order][present], dtype=np.float32)),
        }

        def counts_of(rows: np.ndarray) -> np.ndarray:
            return np.bincount(rows, minlength=count).astype(_I64)

        def role_reduction(rows: list[list[int]]) -> Reduction:
            role_counts = np.array([len(r) for r in rows], dtype=_I64)
            flat = np.array([v for r in rows for v in r], dtype=_I64)
            return _reduction(role_counts, flat)

        reductions = {
            "ids": _reduction(counts_of(id_rows), np.arange(len(id_rows), dtype=_I64)),
            "numbers": _reduction(counts_of(num_rows), number_index),
            "text": _reduction(counts_of(txt_rows), np.arange(len(txt_rows), dtype=_I64)),
            "refs": _reduction(ref_counts, ref_dst),
            "source": role_reduction(sources),
            "target": role_reduction(targets),
        }
        return TensorFrame(tensors, reductions, tuple(c["choice_id"] for c in choices), count)

    def _order_positions(self, kinds: list[str], positions: list[int], ref_dst: np.ndarray,
                         ref_offsets: np.ndarray) -> None:
        """input_order.order_frame with sorted_positions=False: only shared positions."""
        observation = self.observation
        paths = unordered_domains(observation)
        zones = observation.get("piles", {}).get("content") or {}
        unordered_zones = {i for i, zone in enumerate(zones.get("zones", []))
                           if zone.get("ordering") == "unordered_multiset"}
        special = {"$.extra_reads", "$.piles.content.zones[].cards"}
        zone_rows = None

        def refs(row: int) -> list[int]:
            return ref_dst[ref_offsets[row]:ref_offsets[row + 1]].tolist()

        for index, kind in enumerate(kinds):
            if kind not in paths and kind not in special:
                continue  # the reference loop changes nothing for such a node
            member_kind = kind + "[]"
            members = [r for r in refs(index) if kinds[r] == member_kind]
            is_set = kind in paths
            reads = observation.get("extra_reads", [])
            if (kind == "$.extra_reads" and reads and reads[0].get("kind") == "shop_catalog"
                    and all(read.get("kind") == "surface_card" for read in reads[1:])):
                members = members[1:]
                is_set = True
            if kind == "$.piles.content.zones[].cards":
                if zone_rows is None:
                    zone_rows = [i for i, k in enumerate(kinds) if k == "$.piles.content.zones[]"]
                owners = [p for p in zone_rows if index in refs(p)]
                is_set = bool(owners and positions[owners[0]] in unordered_zones)
            if is_set and members:
                for member in members:
                    positions[member] = 0
