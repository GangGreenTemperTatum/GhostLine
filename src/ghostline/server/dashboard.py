"""HTML dashboard for GhostLine.

Replaces the legacy stub that returned ``<h1>Dashboard</h1>``. Renders
live stage counts + call count + playbook summary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ghostline.persistence.repository import Repository

__all__ = ("render_dashboard_html",)


def render_dashboard_html(
    *,
    call_count: int,
    stage_counts: dict[str, int],
    playbook_name: str | None = None,
) -> str:
    """Render the dashboard HTML as a string.

    Args:
        call_count: Total number of calls in the DB.
        stage_counts: ``{stage_name: message_count}`` from the repository.
        playbook_name: Optional name of the active playbook.

    Returns:
        A complete HTML document string.
    """
    stage_rows = (
        "\n".join(
            f"<tr><td>{stage}</td><td>{count}</td></tr>"
            for stage, count in sorted(stage_counts.items())
        )
        or '<tr><td colspan="2"><em>no data yet</em></td></tr>'
    )
    pb_line = f"<p>Active playbook: <strong>{playbook_name}</strong></p>" if playbook_name else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>GhostLine Dashboard</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0d1117; color: #c9d1d9; margin: 2rem auto; max-width: 800px; }}
    h1 {{ color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: .5rem; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
    th, td {{ border: 1px solid #30363d; padding: .5rem .8rem; text-align: left; }}
    th {{ background: #161b22; color: #8b949e; font-weight: 600; }}
    .stat {{ display: inline-block; background: #161b22; padding: 1rem 1.5rem;
             border-radius: 6px; margin: .5rem 1rem .5rem 0; }}
    .stat .num {{ font-size: 1.8rem; font-weight: 700; color: #58a6ff; display: block; }}
    .stat .label {{ font-size: .8rem; color: #8b949e; text-transform: uppercase; }}
    footer {{ margin-top: 2rem; color: #6e7681; font-size: .8rem; }}
  </style>
</head>
<body>
  <h1>👻 GhostLine Dashboard</h1>
  {pb_line}
  <div>
    <div class="stat"><span class="num">{call_count}</span><span class="label">Total calls</span></div>
    <div class="stat"><span class="num">{sum(stage_counts.values())}</span><span class="label">Messages logged</span></div>
  </div>
  <h2>Stage distribution</h2>
  <table>
    <thead><tr><th>Stage</th><th>Messages</th></tr></thead>
    <tbody>
      {stage_rows}
    </tbody>
  </table>
  <footer>GhostLine — for authorized security assessments only.</footer>
</body>
</html>"""


async def render_dashboard_from_repo(repo: Repository, playbook_name: str | None = None) -> str:
    """Convenience: pull stats from ``repo`` and render the dashboard."""
    return render_dashboard_html(
        call_count=await repo.call_count(),
        stage_counts=await repo.stage_counts(),
        playbook_name=playbook_name,
    )
