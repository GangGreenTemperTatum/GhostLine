"""Tests for the dashboard HTML renderer."""

from __future__ import annotations

from ghostline.persistence.repository import (
    CallRecord,
    MessageRecord,
    Repository,
)
from ghostline.server.dashboard import render_dashboard_from_repo, render_dashboard_html


class TestRenderDashboardHtml:
    def test_contains_title(self) -> None:
        html = render_dashboard_html(call_count=0, stage_counts={})
        assert "GhostLine Dashboard" in html

    def test_shows_call_count(self) -> None:
        html = render_dashboard_html(call_count=42, stage_counts={})
        assert "42" in html

    def test_shows_stage_counts(self) -> None:
        html = render_dashboard_html(call_count=5, stage_counts={"RAPPORT": 3, "CLOSE": 2})
        assert "RAPPORT" in html
        assert "CLOSE" in html
        assert ">3<" in html
        assert ">2<" in html

    def test_empty_stage_counts_shows_placeholder(self) -> None:
        html = render_dashboard_html(call_count=0, stage_counts={})
        assert "no data yet" in html

    def test_playbook_name_displayed(self) -> None:
        html = render_dashboard_html(call_count=0, stage_counts={}, playbook_name="Vendor Swap")
        assert "Vendor Swap" in html

    def test_no_playbook_hides_playbook_line(self) -> None:
        html = render_dashboard_html(call_count=0, stage_counts={}, playbook_name=None)
        assert "Active playbook" not in html

    def test_total_messages_summed(self) -> None:
        html = render_dashboard_html(call_count=10, stage_counts={"RAPPORT": 4, "CLOSE": 6})
        # The total messages stat should be 4 + 6 = 10
        assert ">10<" in html


class TestRenderDashboardFromRepo:
    async def test_pulls_stats_from_repo(self) -> None:
        async with Repository(":memory:") as repo:
            await repo.insert_call(CallRecord(call_sid="CA_dash_1"))
            await repo.insert_message(
                MessageRecord(
                    call_sid="CA_dash_1",
                    role="user",
                    content="hi",
                    sales_stage="RAPPORT",
                )
            )
            html = await render_dashboard_from_repo(repo, playbook_name="test")
            assert "GhostLine Dashboard" in html
            assert "test" in html
            assert "RAPPORT" in html
            assert "1" in html  # one call, one message
