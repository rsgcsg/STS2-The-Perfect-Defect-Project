"""How one Managed game begins: a seeded new run or a verified map save (RL5 S4 hook).

One owner for both chains: the per-game executor (``managed_evaluation``) and the
fast-chain game manager (``fast_chain_episodes``). In this batch a save start always
runs in a fresh native process (in-process load, U1, is not built), and the Host itself
refuses a load after a mount (``managed_map_continue_fresh_process_required``).

A native map-continue load does not replay ``initial_progress``: the Host identity of a
loaded world reports ``initial_progress: null``. The proof for a loaded game is the save
lineage instead: the save start carries the progress digest its parent game proved at
reset, and the loaded world must name the same save (Host origin, or the compact first
frame ``start``) and the same seed/act/floor. Nothing here replays, guesses or relaxes a
Host check; any difference is a typed rejection.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, NoReturn

from spireagent.json_boundary import BoundaryError

SAVE_START_SCHEMA = "spireagent/managed-save-start-1"
HOST_SAVE_SCHEMA = "sts2.host-runtime/managed-map-continue-1"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAVE_BOUNDARIES = frozenset({"map_room", "map_presentation"})


def reject(code: str) -> NoReturn:
    raise BoundaryError("episode_start", code)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _count(value: Any, minimum: int) -> bool:
    return type(value) is int and value >= minimum


def _host_save(value: Any) -> dict[str, Any]:
    """The Host's own descriptor, checked with the Host's rules before any process starts."""
    if not isinstance(value, Mapping) or value.get("schema") != HOST_SAVE_SCHEMA:
        reject("save_descriptor_invalid")
    candidate = value.get("candidate")
    if (not isinstance(value.get("sha256"), str) or not _SHA256.fullmatch(value["sha256"])
            or not _count(value.get("bytes"), 1)
            or not _text(value.get("seed"))
            or not _text(value.get("parent_runtime_instance_id"))
            or not _text(value.get("parent_game_continuity_id"))
            or not _count(value.get("act"), 1) or not _count(value.get("floor"), 0)
            or not _text(value.get("path")) or not str(value["path"]).startswith("/")
            or not isinstance(candidate, Mapping)
            or any(not isinstance(candidate.get(key), str)
                   or not _SHA256.fullmatch(candidate[key])
                   for key in ("artifact_sha256", "runtime_sts2_sha256"))):
        reject("save_descriptor_invalid")
    if value.get("save_boundary") not in _SAVE_BOUNDARIES:
        # pre_room_rollback is refused by the Host; never offered from here either.
        reject("save_boundary_not_map")
    return dict(value)


def _lineage(value: Any) -> dict[str, Any]:
    if (not isinstance(value, Mapping)
            or set(value) - {"initial_progress_sha256", "character", "ascension",
                             "parent_intent_id", "parent_run_id"}
            or not _text(value.get("initial_progress_sha256"))
            or not _text(value.get("character"))
            or not _count(value.get("ascension"), 0)
            or not _text(value.get("parent_intent_id"))
            or not (value.get("parent_run_id") is None or _text(value.get("parent_run_id")))):
        reject("save_lineage_invalid")
    return dict(value)


def _parent(value: Any, *, save: Mapping[str, Any], lineage: Mapping[str, Any]) -> None:
    """The parent game's own Host ``episode_identity`` must back the save's lineage.

    The parent must be the runtime the save names, a passed seeded provenance of the
    same seed, and a replayed progress proof equal to the lineage digest. A parent that
    was itself loaded (no replayed proof) is refused: chained loads are not proved yet.
    """
    if not isinstance(value, Mapping):
        reject("save_parent_identity_invalid")
    provenance = value.get("episode_provenance")
    if (value.get("adapter_runtime_instance_id") != save["parent_runtime_instance_id"]
            or not isinstance(provenance, Mapping)
            or provenance.get("verdict") != "provenance_pass"
            or provenance.get("actual_seed") != save["seed"]):
        reject("save_parent_identity_changed")
    progress = value.get("initial_progress")
    if (not isinstance(progress, Mapping) or progress.get("run_unlock_state_matches") is not True
            or progress.get("progress_sha256") != lineage["initial_progress_sha256"]):
        reject("save_parent_progress_unproved")


@dataclass(frozen=True)
class EpisodeStart:
    """``new_run`` (seeded reset) or ``from_save`` (fresh-process map load)."""

    kind: str
    seed: str
    save: dict[str, Any] | None = field(default=None, compare=False)
    lineage: dict[str, Any] | None = field(default=None, compare=False)

    @classmethod
    def new_run(cls, seed: str) -> EpisodeStart:
        if not _text(seed):
            reject("start_seed_required")
        return cls("new_run", seed)

    @classmethod
    def decode(cls, value: Any, *, seed: str) -> EpisodeStart:
        """``None`` is today's seeded run; anything else must be a complete save start."""
        if value is None:
            return cls.new_run(seed)
        if (not isinstance(value, Mapping) or value.get("schema") != SAVE_START_SCHEMA
                or set(value) != {"schema", "save", "lineage", "parent_identity"}):
            reject("save_start_invalid")
        save = _host_save(value["save"])
        if save["seed"] != seed:
            # A save's descendants keep its seed (held-out and pairing bookkeeping).
            reject("save_start_seed_changed")
        lineage = _lineage(value["lineage"])
        _parent(value["parent_identity"], save=save, lineage=lineage)
        return cls("from_save", seed, save, lineage)

    @property
    def fresh_native_required(self) -> bool:
        return self.kind == "from_save"

    @property
    def save_sha256(self) -> str | None:
        return self.save["sha256"] if self.save is not None else None

    def record(self) -> dict[str, Any]:
        """The row's durable record: which world was offered and from what."""
        if self.save is None:
            return {"kind": "new_run", "seed": self.seed}
        return {"kind": "from_save", "seed": self.seed, "save_sha256": self.save["sha256"],
                "save_bytes": self.save["bytes"], "act": self.save["act"],
                "floor": self.save["floor"], "save_boundary": self.save["save_boundary"],
                "candidate": dict(self.save["candidate"]),
                "parent_runtime_instance_id": self.save["parent_runtime_instance_id"],
                "parent_game_continuity_id": self.save["parent_game_continuity_id"],
                "lineage": dict(self.lineage or {})}

    def native_start(self) -> dict[str, Any]:
        """``managed-native-compact-1`` first-frame ``start``."""
        if self.save is None:
            return {"kind": "new_run"}
        return {"kind": "from_save", "save_sha256": self.save["sha256"]}

    def env_start(self) -> dict[str, Any]:
        """``fast-env-command-1`` ``open.start``: the Host descriptor itself for a save."""
        if self.save is None:
            return {"kind": "new_run"}
        return {"kind": "from_save", "save": dict(self.save)}

    def inference_start(self, origin_game_instance_id: str | None = None) -> dict[str, Any]:
        """``stpd/rl-inference-3`` ``episode_open.start``: never the seed or a world key."""
        if self.save is None:
            return {"kind": "new_run"}
        value = {"kind": "from_save", "save_sha256": self.save["sha256"],
                 "act": self.save["act"], "floor": self.save["floor"]}
        if origin_game_instance_id is not None:
            value["origin_game_instance_id"] = origin_game_instance_id
        return value

    def require_scenario(self, *, character: str, ascension: int) -> None:
        """The save's recorded scenario must be this manifest's; the Host does not check it."""
        if self.lineage is not None and (self.lineage["character"] != character
                                         or self.lineage["ascension"] != ascension):
            reject("save_start_scenario_changed")

    def load_request(self, *, expected_runtime_instance_id: str,
                     expected_game_continuity_id: str | None) -> dict[str, Any]:
        """The exact ``/v1/admin/load-map`` body (``request_id`` added by the client)."""
        if self.save is None:
            reject("load_requires_save_start")
        return {"save": dict(self.save),
                "expected_runtime_instance_id": expected_runtime_instance_id,
                "expected_game_continuity_id": expected_game_continuity_id}


def require_start_progress(start: EpisodeStart,
                           initial_progress: Mapping[str, Any] | None) -> None:
    """Before any offer, both chains: a save's lineage names this run's progress."""
    if start.lineage is None:
        return
    if (not isinstance(initial_progress, Mapping)
            or start.lineage["initial_progress_sha256"] != initial_progress.get(
                "progress_sha256")):
        reject("loaded_initial_progress_lineage_changed")


def decode_episode_starts(value: Any, *,
                          initial_progress: Mapping[str, Any] | None) -> dict[str, EpisodeStart]:
    """The frozen ``episode_starts`` (slot seed -> save start), checked once, up front."""
    if not isinstance(value, Mapping) or any(not _text(seed) for seed in value):
        reject("invalid_episode_starts")
    starts = {seed: EpisodeStart.decode(item, seed=seed) for seed, item in value.items()}
    for start in starts.values():
        require_start_progress(start, initial_progress)
    return starts


def lineage_progress(start: EpisodeStart, initial_progress: Mapping[str, Any],
                     reported: Any) -> dict[str, Any]:
    """Progress proof of a loaded world: the save lineage, never a guessed Host value.

    ``reported`` is what the loaded world's Host identity says; the native load path
    does not replay progress, so it is ``None`` today. A non-null report must still be
    a matching proof: a loaded world never claims a different progress.
    """
    assert start.lineage is not None and start.save is not None
    require_start_progress(start, initial_progress)
    if reported is not None and (
            not isinstance(reported, Mapping)
            or reported.get("run_unlock_state_matches") is not True
            or reported.get("progress_sha256") != initial_progress["progress_sha256"]):
        reject("loaded_initial_progress_changed")
    return {"source": "save_lineage", "progress_sha256": initial_progress["progress_sha256"],
            "save_sha256": start.save["sha256"],
            "parent_intent_id": start.lineage["parent_intent_id"],
            "native_replayed": reported is not None}


def require_loaded_identity(identity: object, *, start: EpisodeStart,
                            initial_progress: Mapping[str, Any]) -> dict[str, Any]:
    """Old-chain gate for a ``from_save`` world, from the Host's ``episode_identity``.

    The Host origin must name exactly this save and its parents; seed, act and floor
    must match the descriptor. Returns the lineage progress proof for the row.
    """
    if start.save is None:
        reject("loaded_identity_requires_save_start")
    if not isinstance(identity, Mapping):
        reject("prepared_identity_invalid")
    provenance = identity.get("episode_provenance")
    if not isinstance(provenance, Mapping):
        reject("prepared_provenance_not_passed")
    if provenance.get("actual_seed") != start.seed:
        reject("prepared_seed_changed")
    if provenance.get("verdict") != "provenance_pass":
        reject("prepared_provenance_not_passed")
    origin = provenance.get("origin")
    save = start.save
    if (not isinstance(origin, Mapping) or origin.get("kind") != "map_continue_save"
            or origin.get("save_sha256") != save["sha256"]
            or origin.get("act") != save["act"] or origin.get("floor") != save["floor"]
            or origin.get("candidate") != save["candidate"]
            or origin.get("parent_runtime_instance_id") != save["parent_runtime_instance_id"]
            or origin.get("parent_game_continuity_id") != save["parent_game_continuity_id"]):
        reject("loaded_origin_changed")
    return lineage_progress(start, initial_progress, identity.get("initial_progress"))


def require_first_frame_start(observed: Any, *, start: EpisodeStart) -> None:
    """Fast-chain gate: the compact first frame says how the native instance began.

    The environment may echo either the compact ``start`` or its own ``open.start``
    (the descriptor); both must name exactly this kind and save digest.
    """
    if (isinstance(observed, Mapping) and observed.get("kind") == "from_save"
            and isinstance(observed.get("save"), Mapping) and set(observed) == {"kind", "save"}):
        observed = {"kind": "from_save", "save_sha256": observed["save"].get("sha256")}
    if observed != start.native_start():
        reject("first_frame_start_changed")
