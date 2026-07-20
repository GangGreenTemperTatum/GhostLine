"""Async SQLite repository for GhostLine.

Replaces the legacy ``database.py`` which held a module-level ``DB_CONN``.
Connections are created via :meth:`Repository.open` and injected by the
composition root. The hot path (per-frame WS handler) never touches sync I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = (
    "CallRecord",
    "MessageRecord",
    "ObjectionRecord",
    "Repository",
)

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


@dataclass(slots=True, frozen=True)
class CallRecord:
    """A row in the ``calls`` table."""

    call_sid: str
    start_time: str | None = None
    end_time: str | None = None
    voice_id: str | None = None
    campaign: str | None = None
    persona: str | None = None
    phone_number: str | None = None
    outcome: str | None = None
    conversion_score: float | None = None
    notes: str | None = None


@dataclass(slots=True, frozen=True)
class MessageRecord:
    """A row in the ``messages`` table."""

    call_sid: str
    role: str
    content: str
    sales_stage: str | None = None
    sentiment_score: float | None = None
    interest_level: float | None = None
    objection_type: str | None = None
    trigger_used: str | None = None
    timestamp: str | None = None
    id: int | None = None


@dataclass(slots=True, frozen=True)
class ObjectionRecord:
    """A row in the ``objections`` table."""

    call_sid: str
    objection_text: str
    objection_type: str | None = None
    response_used: str | None = None
    resolved: bool = False
    timestamp: str | None = None
    id: int | None = None


def _utcnow_iso() -> str:
    """Return ISO-8601 UTC timestamp with timezone designator."""
    return datetime.now(UTC).isoformat()


class Repository:
    """Async wrapper around :mod:`aiosqlite`.

    Use as an async context manager or call :meth:`open` / :meth:`close`
    explicitly. Methods are safe to call concurrently from multiple
    coroutines on the same connection (aiosqlite serializes writes).
    """

    def __init__(self, db_path: Path | str = "sales_tracking.db") -> None:
        self._db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    @classmethod
    async def open(cls, db_path: Path | str = ":memory:") -> Repository:
        """Open a connection, run the schema, and return a ready repository.

        Convenience for non-context-manager usage::

            repo = await Repository.open(":memory:")
            ...
            await repo.close()
        """
        repo = cls(db_path)
        await repo._open()
        return repo

    async def _open(self) -> None:
        if self._db is not None:
            return  # idempotent
        self._db = await aiosqlite.connect(str(self._db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA_PATH.read_text())
        await self._db.commit()

    async def close(self) -> None:
        """Close the underlying sqlite connection (no-op if already closed)."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        """The underlying aiosqlite connection; raises if not open."""
        if self._db is None:
            raise RuntimeError("Repository is not open; call Repository.open() first")
        return self._db

    async def __aenter__(self) -> Repository:
        await self._open()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    # ── Calls ───────────────────────────────────────────────────────
    async def insert_call(self, record: CallRecord) -> None:
        """Insert a call row, ignoring if the call_sid already exists."""
        await self.db.execute(
            "INSERT OR IGNORE INTO calls"
            " (call_sid, start_time, end_time, voice_id, campaign, persona,"
            "  phone_number, outcome, conversion_score, notes)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.call_sid,
                record.start_time,
                record.end_time,
                record.voice_id,
                record.campaign,
                record.persona,
                record.phone_number,
                record.outcome,
                record.conversion_score,
                record.notes,
            ),
        )
        await self.db.commit()

    async def update_call_outcome(
        self, call_sid: str, outcome: str, conversion_score: float | None = None
    ) -> None:
        """Record final outcome and (optional) score for a call."""
        await self.db.execute(
            "UPDATE calls SET outcome=?, conversion_score=?, end_time=? WHERE call_sid=?",
            (outcome, conversion_score, _utcnow_iso(), call_sid),
        )
        await self.db.commit()

    async def get_call_phone(self, call_sid: str) -> str | None:
        """Return the target phone number for ``call_sid`` (or ``None``)."""
        async with self.db.execute(
            "SELECT phone_number FROM calls WHERE call_sid=?", (call_sid,)
        ) as cur:
            row = await cur.fetchone()
            return str(row[0]) if row and row[0] else None

    # ── Messages ────────────────────────────────────────────────────
    async def insert_message(self, record: MessageRecord) -> int | None:
        """Insert a message row; return its autoincrement id."""
        ts = record.timestamp or _utcnow_iso()
        async with self.db.execute(
            "INSERT INTO messages"
            " (call_sid, role, content, timestamp, sales_stage,"
            "  sentiment_score, interest_level, objection_type, trigger_used)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.call_sid,
                record.role,
                record.content,
                ts,
                record.sales_stage,
                record.sentiment_score,
                record.interest_level,
                record.objection_type,
                record.trigger_used,
            ),
        ) as cur:
            await self.db.commit()
            return cur.lastrowid if cur.lastrowid is not None else None

    async def recent_messages(self, call_sid: str, limit: int = 10) -> Sequence[MessageRecord]:
        """Return the most recent ``limit`` messages for a call, oldest-first."""
        async with self.db.execute(
            "SELECT id, call_sid, role, content, timestamp, sales_stage,"
            " sentiment_score, interest_level, objection_type, trigger_used"
            " FROM messages WHERE call_sid=? ORDER BY id DESC LIMIT ?",
            (call_sid, limit),
        ) as cur:
            rows = list(await cur.fetchall())
        rows.reverse()
        return [
            MessageRecord(
                id=int(r["id"]),
                call_sid=str(r["call_sid"]),
                role=str(r["role"]),
                content=str(r["content"]),
                timestamp=str(r["timestamp"]),
                sales_stage=r["sales_stage"],
                sentiment_score=r["sentiment_score"],
                interest_level=r["interest_level"],
                objection_type=r["objection_type"],
                trigger_used=r["trigger_used"],
            )
            for r in rows
        ]

    # ── Objections ──────────────────────────────────────────────────
    async def insert_objection(self, record: ObjectionRecord) -> int | None:
        """Insert an objection row; return its autoincrement id."""
        ts = record.timestamp or _utcnow_iso()
        async with self.db.execute(
            "INSERT INTO objections"
            " (call_sid, objection_text, objection_type, response_used, resolved, timestamp)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                record.call_sid,
                record.objection_text,
                record.objection_type,
                record.response_used,
                record.resolved,
                ts,
            ),
        ) as cur:
            await self.db.commit()
            return cur.lastrowid if cur.lastrowid is not None else None

    # ── Customer profiles ───────────────────────────────────────────
    async def upsert_profile(self, phone_number: str, preferred_persona: str | None) -> None:
        """Insert or update the preferred persona for a phone number."""
        await self.db.execute(
            "INSERT INTO customer_profiles (phone_number, preferred_persona, last_updated)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT(phone_number) DO UPDATE SET"
            "   preferred_persona=excluded.preferred_persona,"
            "   last_updated=excluded.last_updated",
            (phone_number, preferred_persona, _utcnow_iso()),
        )
        await self.db.commit()

    # ── Dashboard / stats ───────────────────────────────────────────
    async def stage_counts(self) -> dict[str, int]:
        """Return ``{stage_name: count}`` aggregated across all messages."""
        async with self.db.execute(
            "SELECT sales_stage, COUNT(*) FROM messages"
            " WHERE sales_stage IS NOT NULL GROUP BY sales_stage"
        ) as cur:
            rows = await cur.fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    async def call_count(self) -> int:
        """Total number of call rows in the database."""
        async with self.db.execute("SELECT COUNT(*) FROM calls") as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0
