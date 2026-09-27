"""Private disk-backed decision rows; never an admission or public artifact format."""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import weakref
from collections.abc import Iterator, MutableMapping, Sequence
from pathlib import Path
from typing import Any, overload

from spireagent.json_boundary import BoundaryError
from stpd.canonical import canonical_json

from .contracts import ResearchTransitionV2
from .representation import decision_fingerprint


def _close_spool(db: sqlite3.Connection, directory: Path) -> None:
    """One owner orders SQLite handle release before private-file removal."""
    db.close()
    shutil.rmtree(directory)


class DecisionSpool(MutableMapping[str, ResearchTransitionV2]):
    """Keep large state/action/Read payloads off the aggregate selection heap.

    The returned selection retains this owner until consumers finish publishing.
    Temporary files are private and removed with the owner; no evidence is stored here.
    """

    def __init__(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="stpd-decision-rows-"))
        try:
            self.db = sqlite3.connect(self.directory / "rows.sqlite")
        except BaseException:
            shutil.rmtree(self.directory)
            raise
        try:
            self._finalizer = weakref.finalize(self, _close_spool, self.db, self.directory)
        except BaseException:
            _close_spool(self.db, self.directory)
            raise
        try:
            self.db.execute("PRAGMA cache_size=-2048")
            self.db.execute("PRAGMA temp_store=FILE")
            self.db.execute("CREATE TABLE records(id TEXT PRIMARY KEY,run TEXT,sequence INTEGER,"
                            "body TEXT NOT NULL,summary TEXT NOT NULL,"
                            "selected INTEGER NOT NULL DEFAULT 0)")
        except BaseException:
            self.close()
            raise

    def __getitem__(self, key: str) -> ResearchTransitionV2:
        row = self.db.execute("SELECT body FROM records WHERE id=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return ResearchTransitionV2.decode(json.loads(row[0]))

    def __setitem__(self, key: str, value: ResearchTransitionV2) -> None:
        self.db.execute("INSERT OR REPLACE INTO records(id,run,sequence,body,summary) "
                        "VALUES(?,?,?,?,?)",
                        (key, value.run_id, value.source_evidence.value()["action_sequence"],
                         canonical_json(value.to_dict()), json.dumps(row_summary(key, value))))

    def summaries(self, *, selected: bool = False) -> Iterator[dict[str, Any]]:
        clause = " WHERE selected=1" if selected else ""
        for row in self.db.execute("SELECT summary FROM records" + clause +
                                   " ORDER BY run,sequence,id"):
            yield json.loads(row[0])

    def copy_selected(self, source: SpoolSelection, keys: set[str]) -> None:
        """Copy already validated private rows without decoding their full game states."""
        for row in source.owner.db.execute(
            "SELECT id,run,sequence,body,summary FROM records WHERE selected=1"
        ):
            if row[0] in keys:
                self.db.execute("INSERT INTO records VALUES(?,?,?,?,?,1)", row)

    def __delitem__(self, key: str) -> None:
        if not self.db.execute("DELETE FROM records WHERE id=?", (key,)).rowcount:
            raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        for row in self.db.execute("SELECT id FROM records ORDER BY id"):
            yield row[0]

    def __len__(self) -> int:
        return int(self.db.execute("SELECT count(*) FROM records").fetchone()[0])

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self.db.execute(
            "SELECT 1 FROM records WHERE id=?", (key,),
        ).fetchone() is not None

    def select(self, key: str) -> None:
        self.db.execute("UPDATE records SET selected=1 WHERE id=?", (key,))

    def selected(self) -> SpoolSelection:
        self.db.commit()
        return SpoolSelection(self)

    def close(self) -> None:
        self._finalizer()


class SpoolSelection(Sequence[ResearchTransitionV2]):
    def __init__(self, owner: DecisionSpool) -> None:
        self.owner = owner
        self.size = int(owner.db.execute(
            "SELECT count(*) FROM records WHERE selected=1").fetchone()[0])

    def __len__(self) -> int:
        return self.size

    def summaries(self) -> Iterator[dict[str, Any]]:
        return self.owner.summaries(selected=True)

    def canonical_rows(self) -> Iterator[bytes]:
        for row in self.owner.db.execute(
            "SELECT body FROM records WHERE selected=1 ORDER BY run,sequence,id"
        ):
            yield row[0].encode("utf-8")

    def __iter__(self) -> Iterator[ResearchTransitionV2]:
        for row in self.owner.db.execute(
            "SELECT body FROM records WHERE selected=1 ORDER BY run,sequence,id"
        ):
            yield ResearchTransitionV2.decode(json.loads(row[0]))

    @overload
    def __getitem__(self, index: int) -> ResearchTransitionV2: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[ResearchTransitionV2, ...]: ...

    def __getitem__(
        self, index: int | slice,
    ) -> ResearchTransitionV2 | tuple[ResearchTransitionV2, ...]:
        if isinstance(index, slice):
            return tuple(self[i] for i in range(*index.indices(len(self))))
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        row = self.owner.db.execute(
            "SELECT body FROM records WHERE selected=1 ORDER BY run,sequence,id LIMIT 1 OFFSET ?",
            (index,),
        ).fetchone()
        if row is None:
            raise BoundaryError("decision_spool", "selection_changed")
        return ResearchTransitionV2.decode(json.loads(row[0]))

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, Sequence) and len(self) == len(other)
                and all(a == b for a, b in zip(self, other, strict=True)))


def row_summary(key: str, record: ResearchTransitionV2) -> dict[str, Any]:
    """Derived only from a typed row, never accepted from a request or artifact."""
    evidence = record.source_evidence.value()
    return {
        "id": key, "run_id": record.run_id, "transition_id": record.transition_id,
        "source": record.provenance.bundle_sha256,
        "session_id": evidence["session_id"], "native_run_id": evidence["native_run_id"],
        "fingerprint": decision_fingerprint(record),
        "metadata": {
            "environment_identity": record.provenance.environment_identity,
            "character": record.state.run.value().get("character"),
            "difficulty": record.state.run.value().get("ascension"),
            "family": record.family, "surface": record.surface,
            "decision_kind": record.occurrence.value()["decision_kind"],
        },
    }
