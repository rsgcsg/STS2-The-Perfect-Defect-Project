"""Cached Input-v2 encoder: public observation JSON -> `stpd/rows-v2-row-1` row.

This is the fast_chain encoder (P-REWRITE WP3). It produces exactly the frame
`FrameCompiler.compile` produces (`compile_frame(project(observation))`), element
for element, and is checked against it (`verify_every`, tests, R7 double runs).
It adds no semantics: no new field, order, legality or hidden input. Until the
WP8 version step makes it the single production implementation, the reference
in `input.py` / `input_order.py` / `tensor_input.py` stays the frozen oracle.

Correspondence with the reference (one structure, read side by side):

    _Encoder._visit            == input.project.visit, v2 symmetric branch only
                                  (relation is never None for v2, so the v1 and
                                  sorted branches are unreachable and not copied)
    _Encoder._assemble, part 1 == project(): alias views, view extension, pending
                                  loop with reference-only nodes, roles
    _Encoder._order_positions  == input_order.order_frame(sorted_positions=False)
    _Encoder._assemble, part 2 == tensor_input.compile_frame; _reduction ==
                                  tensor_input.reduction
    _Encoder.hashes            == frame_compiler.observation_hashes
    identity_paths, unordered_domains, public_field_path, signed_log and the
    InputEncoding tokenizer are imported from the reference, not copied.

The only additions are caches:
* Subtree units. Every top-level value and every list element that itself
  contains a dict or list is visited once per distinct content and replayed
  afterwards. The key holds everything the reference visit of that subtree
  reads: its typed value in insertion order (marshal v2 bytes; declaration order
  within one entity follows dict insertion order), its kind path, and the identity
  relation scope of its source path (`_Encoder._scope`). Row numbers, the root's
  position and parent edge, alias views, pending references, ordering positions,
  text indices and reductions are always assembled per observation.
* Relation results, memoized per complete identity_paths input signature.
* Sorted JSON bytes of top-level values, so the two observation hashes are the
  sha256 of exactly `json_bytes(observation)` / `json_bytes(catalog)`, the same
  `observation_sha256` / `catalog_sha256` every other P1 record carries.
"""
from __future__ import annotations

import hashlib
import json
import marshal
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

from .data import json_bytes
from .frame_compiler import FrameCompiler, compiler_digest
from .input import is_v2_encoding, public_field_path, signed_log, validate_input_spec
from .input_identity import identity_paths
from .input_order import unordered_domains
from .rows_encoder_wire import FORMAT as ROW_FORMAT
from .rows_encoder_wire import encode_row
from .tensor_input import Reduction, TensorFrame

IMPLEMENTATION = "stpd/p1-rows-encoder-cached-1"
DEFAULT_CACHE_BYTES = 512 * 1024 * 1024
MAX_TEXTS = 2_000_000  # interned text table bound; reaching it clears the unit cache
MAX_RELATION_CONTEXTS = 256
_INDEX = re.compile(r"\[\d+\]")
_READ_INDEX = re.compile(r"^\$\.extra_reads\[(\d+)\]")
_CONSTANT = frozenset({"schema", "profile", "revision"})
_I64 = np.int64
_UNSET = object()


def encoder_digest() -> str:
    """Bind this implementation, the row codec and the reference compiler identity."""
    root = Path(__file__).parent
    sources = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
               for name in ("rows_encoder.py", "rows_encoder_wire.py")}
    body = json.dumps({"compiler_sha256": compiler_digest(), "implementation": IMPLEMENTATION,
                       "row_format": ROW_FORMAT, "sources": sources},
                      sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(body).hexdigest()


def _non_finite(token: str) -> Any:
    raise ValueError("non-finite JSON constant")


def _finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    return value


def _nested(item: Any) -> bool:
    """Cache unit policy for list elements: containers that themselves nest."""
    if isinstance(item, dict):
        return any(isinstance(value, (dict, list)) for value in item.values())
    if isinstance(item, list):
        return any(isinstance(value, (dict, list)) for value in item)
    return False


def _rows(values: list[Any], width: int) -> np.ndarray:
    return np.array(values, dtype=_I64).reshape(-1, width) if values else np.zeros(
        (0, width), dtype=_I64)


class _Unit:
    """One visited subtree in local rows (root = row 0); immutable once cached.

    Entry rows keep each entity's own list order (the reference appends an
    entity's identities/text/numeric/children only during that entity's visit).
    `decl` and `pend` keep the visit's time order, which fixes alias-view order
    and the creation order of reference-only nodes. Text values are ids into the
    owning encoder's interned text table.
    """

    __slots__ = ("n", "kinds", "kind_idx", "positions", "ids", "nums", "num_vals", "texts",
                 "tree", "decl", "pend", "nbytes", "json", "cost", "cached")

    def __init__(self, n: int, kinds: tuple[str, ...], kind_idx: tuple[int, ...],
                 positions: tuple[int, ...], ids: np.ndarray, nums: np.ndarray,
                 num_vals: np.ndarray, texts: np.ndarray, tree: np.ndarray,
                 decl: tuple[tuple[int, str], ...], pend: tuple[tuple[int, str], ...]) -> None:
        self.n, self.kinds, self.kind_idx, self.positions = n, kinds, kind_idx, positions
        self.ids = ids            # [k, 3] row, ids index, field index
        self.nums = nums          # [k, 3] row, field index, missing (0/1)
        self.num_vals = num_vals  # [k] float32 signed_log value, 0 where missing
        self.texts = texts        # [k, 3] row, field index, text id
        self.tree = tree          # [k, 2] parent row, child row
        self.decl, self.pend = decl, pend
        self.nbytes = 120 * n + 80 * (len(decl) + len(pend)) + sum(
            array.nbytes for array in (ids, nums, num_vals, texts, tree))
        self.json: bytes | None = None  # sorted JSON bytes, top-level units only
        self.cost = 0         # bytes charged to the cache while `cached`
        self.cached = False


class _Builder:
    """Mutable rows of one subtree being visited; child units are spliced in."""

    __slots__ = ("kinds", "kind_idx", "positions", "ids", "nums", "num_vals", "texts", "tree",
                 "decl", "pend", "frags")

    def __init__(self) -> None:
        self.kinds: list[str] = []
        self.kind_idx: list[int] = []
        self.positions: list[int] = []
        self.ids: list[tuple[int, int, int]] = []
        self.nums: list[tuple[int, int, int]] = []
        self.num_vals: list[float] = []
        self.texts: list[tuple[int, int, int]] = []
        self.tree: list[tuple[int, int]] = []
        self.decl: list[tuple[int, str]] = []
        self.pend: list[tuple[int, str]] = []
        self.frags: list[tuple[_Unit, int]] = []

    def replay(self, unit: _Unit, position: int, parent: int) -> None:
        offset = len(self.kinds)
        self.kinds.extend(unit.kinds)
        self.kind_idx.extend(unit.kind_idx)
        self.positions.extend(unit.positions)
        self.positions[offset] = position
        self.tree.append((parent, offset))
        if unit.decl:
            self.decl.extend((row + offset, alias) for row, alias in unit.decl)
        if unit.pend:
            self.pend.extend((row + offset, alias) for row, alias in unit.pend)
        self.frags.append((unit, offset))

    def finish(self) -> _Unit:
        ids, nums, texts = _rows(self.ids, 3), _rows(self.nums, 3), _rows(self.texts, 3)
        tree = _rows(self.tree, 2)
        num_vals = np.array(self.num_vals, dtype=np.float32)
        frags = self.frags
        if frags:
            offsets = np.array([0] + [offset for _, offset in frags], dtype=_I64)

            def joined(own: np.ndarray, name: str, columns: tuple[int, ...]) -> np.ndarray:
                parts = [own] + [getattr(unit, name) for unit, _ in frags]
                out = np.concatenate(parts)
                shift = np.repeat(offsets, [len(part) for part in parts])
                for column in columns:
                    out[:, column] += shift
                return out

            ids = joined(ids, "ids", (0,))
            nums = joined(nums, "nums", (0,))
            texts = joined(texts, "texts", (0,))
            tree = joined(tree, "tree", (0, 1))
            num_vals = np.concatenate([num_vals] + [unit.num_vals for unit, _ in frags])
        return _Unit(len(self.kinds), tuple(self.kinds), tuple(self.kind_idx),
                     tuple(self.positions), ids, nums, num_vals, texts, tree,
                     tuple(self.decl), tuple(self.pend))


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
        cost = unit.nbytes + len(key[-1]) + 240
        if cost > self.limit:
            return
        unit.cost, unit.cached = cost, True
        self.items[key] = unit
        self.size += cost
        self._evict()

    def charge(self, unit: _Unit, nbytes: int) -> None:
        """Account bytes attached to a unit later (its sorted JSON)."""
        if unit.cached:
            unit.cost += nbytes
            self.size += nbytes
            self._evict()

    def _evict(self) -> None:
        while self.size > self.limit and self.items:
            _, old = self.items.popitem(last=False)
            self.size -= old.cost
            old.cached = False

    def clear(self) -> None:
        for unit in self.items.values():
            unit.cached = False
        self.items.clear()
        self.size = 0


def _reduction(counts: np.ndarray, values: np.ndarray) -> Reduction:
    """tensor_input.reduction over CSR rows: owner i holds the next counts[i] values.

    Buckets follow the first appearance of each width; owners keep row order
    within a bucket and each bucket concatenates its owners' values in order.
    """
    owners_total = len(counts)
    if owners_total == 0:
        return Reduction((), torch.zeros(0, dtype=torch.long))
    widths, first = np.unique(counts, return_index=True)
    widths = widths[np.argsort(first, kind="stable")]
    rank = np.zeros(int(widths.max()) + 1, dtype=_I64)
    rank[widths] = np.arange(len(widths), dtype=_I64)
    owner_rank = rank[counts]
    order = np.argsort(owner_rank, kind="stable")
    lengths = counts[order]
    starts = (np.cumsum(counts) - counts)[order]
    flat = values[np.repeat(starts - (np.cumsum(lengths) - lengths), lengths)
                  + np.arange(int(lengths.sum()), dtype=_I64)]
    owners = np.bincount(owner_rank, minlength=len(widths))
    ends = np.cumsum(widths * owners)
    buckets = []
    for width, rows, end in zip(widths.tolist(), owners.tolist(), ends.tolist(), strict=True):
        buckets.append((width, rows, torch.from_numpy(flat[end - width * rows:end].copy())))
    inverse = np.empty(owners_total, dtype=_I64)
    inverse[order] = np.arange(owners_total, dtype=_I64)
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
        self._relations: OrderedDict[tuple[Any, ...], dict[str, Any]] = OrderedDict()
        self._text_ids: dict[str, int] = {}
        self._texts: list[str] = []
        self._tokens: list[tuple[np.ndarray, np.ndarray] | None] = []
        self._verify_every = verify_every
        self._count = 0
        self.identity = {**self._reference.identity,
                         "encoder": {"implementation": "python",
                                     "sha256": encoder_digest()},
                         "row_format": ROW_FORMAT}

    @property
    def cache_stats(self) -> dict[str, int]:
        cache = self._cache
        return {"hits": cache.hits, "misses": cache.misses, "units": len(cache.items),
                "bytes": cache.size, "texts": len(self._texts)}

    def encode(self, raw: bytes | bytearray | memoryview) -> EncodedRow:
        """Encode one observation given as its JSON bytes."""
        # FrameCompiler.encode hashes json_bytes(observation) (allow_nan=False)
        # before compiling, so any non-finite number is invalid_public_json there.
        try:
            observation = json.loads(bytes(raw), parse_constant=_non_finite,
                                     parse_float=_finite_float)
        except (UnicodeDecodeError, ValueError) as error:
            raise BoundaryError("p1_rows_encoder", "invalid_public_json") from error
        # FrameCompiler.encode checks the catalog binding (observation_hashes) first.
        if not isinstance(observation, dict) or not isinstance(observation.get("catalog"), list):
            raise BoundaryError("p1_frame_compiler", "public_observation_and_catalog_required")
        if any(not isinstance(choice, dict) or not isinstance(choice.get("choice_id"), str)
               for choice in observation["catalog"]):
            raise BoundaryError("p1_frame_compiler", "invalid_public_catalog")
        encoder = _Encoder(self, observation)
        frame = encoder.run()
        self._check(observation, frame)
        bindings = encoder.hashes()
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
        self._check(observation, frame)
        return frame

    def _check(self, observation: dict[str, Any], frame: TensorFrame) -> None:
        self._count += 1
        if self._verify_every and self._count % self._verify_every == 0:
            expected = self._reference.compile(observation)
            if encode_row(expected) != encode_row(frame):
                raise BoundaryError("p1_rows_encoder", "rows_encoder_reference_mismatch")

    def text_id(self, text: str) -> int:
        index = self._text_ids.get(text)
        if index is None:
            index = self._text_ids[text] = len(self._texts)
            self._texts.append(text)
            self._tokens.append(None)
        return index

    def tokens(self, index: int) -> tuple[np.ndarray, np.ndarray]:
        value = self._tokens[index]
        if value is None:
            ids, numbers = self._encoding.tokenize(self._texts[index])
            value = (np.array(ids, dtype=_I64), np.array(numbers, dtype=np.float32))
            self._tokens[index] = value
        return value

    def _bound(self) -> None:
        if len(self._texts) >= MAX_TEXTS:
            self._cache.clear()
            self._text_ids.clear()
            self._texts.clear()
            self._tokens.clear()


class _Encoder:
    """One observation: the reference visit with subtree replay, then assembly."""

    def __init__(self, owner: RowsEncoder, observation: dict[str, Any]) -> None:
        self.owner = owner
        self.observation = observation
        self.relation: Callable[[str], Any] | None = None
        self.top: dict[str, _Unit] = {}
        self.elements: dict[int, _Unit] = {}  # id(list element) -> its unit
        self._scene: tuple[Any, ...] | None = None
        self._reads: list[tuple[Any, bool]] | None = None

    # -- project() ---------------------------------------------------------
    def run(self) -> TensorFrame:
        observation, spec = self.observation, self.owner._projection
        if (observation.get("profile"), observation.get("revision")) != (
                spec["profile"], spec["revision"]):
            raise ValueError("P1 M-std observation differs from bound InputSpec revision")
        self.owner._bound()
        fresh = identity_paths(observation)
        # identity_paths reads only revision, scene native_kind, event tooltip
        # cards and each read's kind/content shape; equal inputs, equal results.
        context = (*self._scene_signature(), tuple(self._read_signature()))
        relations = self.owner._relations
        memo = relations.get(context)
        if memo is None:
            memo = relations[context] = {}
            if len(relations) > MAX_RELATION_CONTEXTS:
                relations.popitem(last=False)
        else:
            relations.move_to_end(context)

        def relation(path: str) -> Any:
            value = memo.get(path, _UNSET)
            if value is _UNSET:
                value = memo[path] = fresh(path)
            return value

        self.relation = relation
        root = _Builder()
        self._visit(root, observation, "$", 0, None, "$")
        return self._assemble(root.finish())

    def _scope(self, source_path: str) -> tuple[Any, ...]:
        """Everything identity_paths(observation)(p) reads for p under source_path.

        Derived from input_identity.identity_paths (tests pin its digest):
        * under $.extra_reads[i]: the path below read i (exact nested indices),
          read i's kind/content-is-dict (its exact typed card/shop roots are
          `extra_reads[i]...` and match only paths of read i, whatever i is) and
          whether any read is a surface card with dict content (adds the
          display_owner reference path);
        * under $.scene: the exact path, revision, native_kind and the exact event
          tooltip card roots (all native/revision-dependent paths are surface paths);
        * elsewhere: the index-normalized path only; no declared, reference or card
          root outside scene/extra_reads depends on the observation or on indices.
        """
        if source_path.startswith("$.extra_reads"):
            reads = self._read_signature()
            surface = any(kind == "surface_card" and is_dict for kind, is_dict in reads)
            match = _READ_INDEX.match(source_path)
            if match is None:
                return ("x", source_path, tuple(reads), surface)
            return ("x", source_path[match.end():], reads[int(match[1])], surface)
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

    def _unit(self, b: _Builder, item: Any, path: str, position: int, parent: int,
              source_path: str) -> _Unit:
        # marshal v2 has no back-references, so its bytes are a pure function of
        # the typed value in insertion order and decode back to it exactly.
        key = (path, self._scope(source_path), marshal.dumps(item, 2))
        cache = self.owner._cache
        unit = cache.get(key)
        if unit is None:
            sub = _Builder()
            self._visit(sub, item, path, 0, None, source_path)
            unit = sub.finish()
            cache.put(key, unit)
        b.replay(unit, position, parent)
        return unit

    def _visit(self, b: _Builder, value: Any, path: str, position: int,
               parent: int | None, source_path: str) -> None:
        # input.project.visit; v2: relation is not None and views are symmetric.
        relation = self.relation
        assert relation is not None
        owner = self.owner
        fields = owner._fields
        text_id = owner.text_id
        row = len(b.kinds)
        b.kinds.append(path)
        b.kind_idx.append(owner._kinds.get(path, 0))
        b.positions.append(position)
        if parent is not None:
            b.tree.append((parent, row))
        if isinstance(value, dict):
            field_roles = {key: relation(public_field_path(source_path, key))
                           for key, item in value.items() if isinstance(item, str)}
            for key, role in field_roles.items():
                if role == "declared":
                    b.decl.append((row, value[key]))
            top = path == "$"
            for key, item in sorted(value.items()):
                if top and (key == "envelope" or key in _CONSTANT):
                    continue
                if key == "choice_id" and path == "$.catalog[]":
                    continue
                role = field_roles.get(key)
                if role == "declared" and isinstance(item, str):
                    continue
                field = fields.get(key, 0)
                if role == "reference" and isinstance(item, str):
                    b.pend.append((row, item))
                    b.texts.append((row, field, text_id("<REF>")))
                    continue
                if isinstance(item, (dict, list)):
                    child_path = public_field_path(path, key)
                    child_source = public_field_path(source_path, key)
                    if top:
                        self.top[key] = self._unit(b, item, child_path, 0, row, child_source)
                    else:
                        self._visit(b, item, child_path, 0, row, child_source)
                elif item is None:
                    b.texts.append((row, field, text_id("<MISSING>")))
                    b.nums.append((row, field, 1))
                    b.num_vals.append(0.0)
                elif type(item) is bool:
                    b.texts.append((row, field, text_id("true" if item else "false")))
                elif type(item) in {int, float}:
                    if not math.isfinite(item):
                        raise ValueError("non-finite public numeric field")
                    b.nums.append((row, field, 0))
                    b.num_vals.append(signed_log(float(item)))
                elif isinstance(item, str):
                    if key.endswith("_id"):
                        b.ids.append((row, owner._ids.get(item, 0), field))
                    else:
                        b.texts.append((row, field, text_id(item)))
                else:
                    raise ValueError("unsupported public field")
        elif isinstance(value, list):
            item_path = path + "[]"
            for i, item in enumerate(value):
                if _nested(item):
                    self.elements[id(item)] = self._unit(b, item, item_path, i, row,
                                                         source_path + f"[{i}]")
                else:
                    self._visit(b, item, item_path, i, row, source_path + f"[{i}]")
        elif isinstance(value, str) and relation(source_path) == "reference":
            b.pend.append((row, value))
            b.texts.append((row, fields.get("value", 0), text_id("<REF>")))
        elif value is None:
            field = fields.get("value", 0)
            b.texts.append((row, field, text_id("<MISSING>")))
            b.nums.append((row, field, 1))
            b.num_vals.append(0.0)
        elif type(value) in {int, float}:
            b.nums.append((row, fields.get("value", 0), 0))
            b.num_vals.append(signed_log(float(value)))
        else:
            b.texts.append((row, fields.get("value", 0), text_id(str(value))))

    # -- project() tail, order_frame, compile_frame -------------------------
    def _assemble(self, root: _Unit) -> TensorFrame:
        owner, observation = self.owner, self.observation
        kinds = list(root.kinds)
        kind_idx = list(root.kind_idx)
        positions = list(root.positions)
        # project(): views in first-declaration order (aliases.setdefault + members).
        aliases: dict[str, int] = {}
        views: dict[str, list[int]] = {}
        for row, alias in root.decl:
            aliases.setdefault(alias, row)
            members = views.setdefault(alias, [])
            if row not in members:
                members.append(row)
        edges: list[tuple[int, int]] = []
        for members in views.values():
            if len(members) > 1:
                edges.extend((row, other) for row in members for other in members
                             if other != row)
        extra_texts: list[tuple[int, int, int]] = []
        for row, alias in root.pend:
            if alias not in aliases:
                # Reference-only node: public owner identity without an entity body.
                aliases[alias] = len(kinds)
                kinds.append("public_reference_only")
                kind_idx.append(owner._kinds.get("public_reference_only", 0))
                positions.append(0)
                extra_texts.append((aliases[alias], owner._fields.get("availability", 0),
                                    owner.text_id("reference_only")))
                views[alias] = [aliases[alias]]
            edges.extend((row, target) for target in views[alias])
        count = len(kinds)
        choices = observation["catalog"]
        sources = [views.get(choice.get("subject"), []) for choice in choices]
        targets = [views.get(choice.get("target"), []) for choice in choices]
        # Refs per entity: tree children, then view extensions, then pending views.
        refs = np.concatenate([root.tree, _rows(edges, 2)]) if edges else root.tree
        ref_order = np.argsort(refs[:, 0], kind="stable")
        ref_dst = refs[ref_order, 1]
        ref_counts = np.bincount(refs[:, 0], minlength=count).astype(_I64)
        ref_offsets = np.concatenate([[0], np.cumsum(ref_counts)])
        self._order_positions(kinds, positions, ref_dst, ref_offsets)

        # compile_frame: sorted distinct texts, identities/numbers/text by row.
        texts = np.concatenate([root.texts, _rows(extra_texts, 3)]) if extra_texts else root.texts
        texts = texts[np.argsort(texts[:, 0], kind="stable")]
        interned = np.unique(texts[:, 2])
        strings = owner._texts
        by_text = sorted(range(len(interned)), key=lambda i: strings[interned[i]])
        rank = np.empty(len(interned), dtype=_I64)
        rank[by_text] = np.arange(len(interned), dtype=_I64)
        text_index = rank[np.searchsorted(interned, texts[:, 2])]
        token_arrays = [owner.tokens(int(interned[i])) for i in by_text]
        lengths = np.array([len(ids) for ids, _ in token_arrays], dtype=_I64)
        width = int(lengths.max()) if len(lengths) else 0
        text_ids = np.zeros((len(lengths), width), dtype=_I64)
        text_numbers = np.zeros((len(lengths), width, 1), dtype=np.float32)
        if len(lengths):
            real = np.arange(width)[None, :] < lengths[:, None]
            text_ids[real] = np.concatenate([ids for ids, _ in token_arrays])
            text_numbers[..., 0][real] = np.concatenate([nums for _, nums in token_arrays])
        ids = root.ids[np.argsort(root.ids[:, 0], kind="stable")]
        num_order = np.argsort(root.nums[:, 0], kind="stable")
        nums = root.nums[num_order]
        missing = nums[:, 2].astype(bool)
        present = ~missing
        present_count = int(present.sum())
        number_index = np.where(present, np.cumsum(present) - 1,
                                present_count + np.cumsum(missing) - 1).astype(_I64)

        def column(values: np.ndarray) -> torch.Tensor:
            return torch.from_numpy(np.ascontiguousarray(values, dtype=_I64))

        tensors = {
            "text_ids": torch.from_numpy(text_ids),
            "text_numbers": torch.from_numpy(text_numbers),
            "text_lengths": torch.from_numpy(lengths),
            "identities": column(ids[:, 1]),
            "identity_fields": column(ids[:, 2]),
            "kinds": column(np.array(kind_idx, dtype=_I64)),
            "positions": column(np.array(positions, dtype=_I64)),
            "number_fields": column(nums[present, 1]),
            "missing_fields": column(nums[missing, 1]),
            "text_fields": column(texts[:, 1]),
            "text_indices": column(text_index),
            "actions": column(np.array([owner._encoding.index("actions", c["kind"])
                                        for c in choices], dtype=_I64).reshape(-1)),
            "numbers": torch.from_numpy(np.ascontiguousarray(
                root.num_vals[num_order][present], dtype=np.float32)),
        }

        def per_row(rows: np.ndarray) -> np.ndarray:
            return np.bincount(rows, minlength=count).astype(_I64)

        def roles(rows: list[list[int]]) -> Reduction:
            return _reduction(np.array([len(r) for r in rows], dtype=_I64),
                              np.array([v for r in rows for v in r], dtype=_I64))

        reductions = {
            "ids": _reduction(per_row(ids[:, 0]), np.arange(len(ids), dtype=_I64)),
            "numbers": _reduction(per_row(nums[:, 0]), number_index),
            "text": _reduction(per_row(texts[:, 0]), np.arange(len(texts), dtype=_I64)),
            "refs": _reduction(ref_counts, ref_dst),
            "source": roles(sources),
            "target": roles(targets),
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
            out: list[int] = ref_dst[ref_offsets[row]:ref_offsets[row + 1]].tolist()
            return out

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

    def hashes(self) -> dict[str, str]:
        """frame_compiler.observation_hashes from cached sorted top-level bytes."""
        observation = self.observation
        if not isinstance(observation.get("catalog"), list):
            raise BoundaryError("p1_frame_compiler", "public_observation_and_catalog_required")
        if any(not isinstance(choice, dict) or not isinstance(choice.get("choice_id"), str)
               for choice in observation["catalog"]):
            raise BoundaryError("p1_frame_compiler", "invalid_public_catalog")
        parts = []
        catalog = b""
        try:
            for key in sorted(observation):
                value = observation[key]
                unit = self.top.get(key)
                encoded = json_bytes(value) if unit is None else self._json(unit, value)
                if key == "catalog":
                    catalog = encoded
                parts.append(json_bytes(key) + b":" + encoded)
        except (TypeError, ValueError) as error:
            raise BoundaryError("p1_frame_compiler", "invalid_public_json") from error
        whole = b"{" + b",".join(parts) + b"}"  # json_bytes: sorted keys, compact
        return {"observation_sha256": hashlib.sha256(whole).hexdigest(),
                "catalog_sha256": hashlib.sha256(catalog).hexdigest()}

    def _json(self, unit: _Unit, value: Any) -> bytes:
        """json_bytes(value), kept on its unit; a list joins its elements' bytes."""
        if unit.json is None:
            if isinstance(value, list):
                parts = []
                for item in value:
                    element = self.elements.get(id(item))
                    parts.append(json_bytes(item) if element is None
                                 else self._json(element, item))
                unit.json = b"[" + b",".join(parts) + b"]"
            else:
                unit.json = json_bytes(value)
            self.owner._cache.charge(unit, len(unit.json))
        return unit.json
