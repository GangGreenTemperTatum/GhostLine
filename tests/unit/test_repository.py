"""Tests for the async SQLite repository."""

from __future__ import annotations

import pytest

from ghostline.persistence.repository import (
    CallRecord,
    MessageRecord,
    ObjectionRecord,
    Repository,
)


@pytest.fixture
async def repo() -> Repository:
    r = await Repository.open(":memory:")
    try:
        yield r
    finally:
        await r.close()


class TestSchemaInitialization:
    async def test_schema_creates_all_tables(self, repo: Repository) -> None:
        async with repo.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) as cur:
            names = [r[0] for r in await cur.fetchall()]
        assert "calls" in names
        assert "messages" in names
        assert "objections" in names
        assert "customer_profiles" in names

    async def test_wal_mode_enabled(self, repo: Repository) -> None:
        async with repo.db.execute("PRAGMA journal_mode") as cur:
            mode = (await cur.fetchone())[0]
        assert str(mode).lower() in {"wal", "memory"}  # :memory: reports 'memory'

    async def test_foreign_keys_on(self, repo: Repository) -> None:
        async with repo.db.execute("PRAGMA foreign_keys") as cur:
            assert (await cur.fetchone())[0] == 1


class TestCalls:
    async def test_insert_call(self, repo: Repository) -> None:
        await repo.insert_call(
            CallRecord(
                call_sid="CA_test_1",
                voice_id="vid",
                campaign="demo",
                persona="professional",
                phone_number="+15551234567",
            )
        )
        assert await repo.call_count() == 1

    async def test_insert_call_idempotent(self, repo: Repository) -> None:
        rec = CallRecord(call_sid="CA_test_1", voice_id="vid")
        await repo.insert_call(rec)
        await repo.insert_call(rec)
        assert await repo.call_count() == 1

    async def test_update_outcome(self, repo: Repository) -> None:
        await repo.insert_call(CallRecord(call_sid="CA_test_1"))
        await repo.update_call_outcome("CA_test_1", "compromised", 0.92)
        async with repo.db.execute(
            "SELECT outcome, conversion_score, end_time FROM calls WHERE call_sid=?",
            ("CA_test_1",),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "compromised"
        assert row[1] == pytest.approx(0.92)
        assert row[2] is not None

    async def test_get_call_phone(self, repo: Repository) -> None:
        await repo.insert_call(CallRecord(call_sid="CA_test_1", phone_number="+15551234567"))
        assert await repo.get_call_phone("CA_test_1") == "+15551234567"
        assert await repo.get_call_phone("missing") is None


class TestMessages:
    async def test_insert_returns_id(self, repo: Repository) -> None:
        await repo.insert_call(CallRecord(call_sid="CA_test_1"))
        mid = await repo.insert_message(
            MessageRecord(call_sid="CA_test_1", role="user", content="hello")
        )
        assert mid is not None
        assert mid > 0

    async def test_recent_messages_returns_oldest_first(self, repo: Repository) -> None:
        await repo.insert_call(CallRecord(call_sid="CA_test_1"))
        for i, role in enumerate(["user", "assistant", "user", "assistant"]):
            await repo.insert_message(
                MessageRecord(
                    call_sid="CA_test_1",
                    role=role,
                    content=f"msg-{i}",
                    sales_stage="RAPPORT",
                )
            )
        msgs = await repo.recent_messages("CA_test_1", limit=10)
        assert [m.content for m in msgs] == ["msg-0", "msg-1", "msg-2", "msg-3"]

    async def test_recent_messages_respects_limit(self, repo: Repository) -> None:
        await repo.insert_call(CallRecord(call_sid="CA_test_1"))
        for i in range(5):
            await repo.insert_message(
                MessageRecord(call_sid="CA_test_1", role="user", content=f"m{i}")
            )
        msgs = await repo.recent_messages("CA_test_1", limit=2)
        assert len(msgs) == 2
        assert [m.content for m in msgs] == ["m3", "m4"]

    async def test_recent_messages_empty_for_unknown_call(self, repo: Repository) -> None:
        assert await repo.recent_messages("nope") == []


class TestObjections:
    async def test_insert_objection(self, repo: Repository) -> None:
        await repo.insert_call(CallRecord(call_sid="CA_test_1"))
        oid = await repo.insert_objection(
            ObjectionRecord(
                call_sid="CA_test_1",
                objection_text="I don't have time",
                objection_type="timing",
                response_used="acknowledged",
                resolved=False,
            )
        )
        assert oid is not None
        assert oid > 0


class TestCustomerProfiles:
    async def test_upsert_inserts_then_updates(self, repo: Repository) -> None:
        await repo.upsert_profile("+15551234567", "professional")
        await repo.upsert_profile("+15551234567", "urgent")
        async with repo.db.execute(
            "SELECT preferred_persona FROM customer_profiles WHERE phone_number=?",
            ("+15551234567",),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "urgent"


class TestStats:
    async def test_stage_counts_aggregates(self, repo: Repository) -> None:
        await repo.insert_call(CallRecord(call_sid="CA_test_1"))
        for stage in ["RAPPORT", "RAPPORT", "URGENCY"]:
            await repo.insert_message(
                MessageRecord(
                    call_sid="CA_test_1",
                    role="user",
                    content="x",
                    sales_stage=stage,
                )
            )
        counts = await repo.stage_counts()
        assert counts == {"RAPPORT": 2, "URGENCY": 1}

    async def test_stage_counts_ignores_null_stage(self, repo: Repository) -> None:
        await repo.insert_call(CallRecord(call_sid="CA_test_1"))
        await repo.insert_message(
            MessageRecord(call_sid="CA_test_1", role="user", content="x", sales_stage=None)
        )
        assert await repo.stage_counts() == {}


class TestConnectionLifecycle:
    async def test_db_property_raises_when_closed(self) -> None:
        repo = await Repository.open(":memory:")
        await repo.close()
        with pytest.raises(RuntimeError, match="not open"):
            _ = repo.db

    async def test_context_manager(self) -> None:
        async with Repository(":memory:") as repo:
            await repo.insert_call(CallRecord(call_sid="CA_x"))
            assert await repo.call_count() == 1
