"""Tests for tools/run.py - the full sweep loop, shadow mode, and dedup.

See tests/test_eventwedding_engine.py for the branch-by-branch rule tests and
tests/conftest.py for why these never read config/hotel.yaml or
config/agent.yaml.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core.config import load_settings
from core.store import Store

import run
import store_ext


def _seeded_store(tmp_path, name="run.db"):
    settings = load_settings(provider="mock", mode="shadow")
    store = Store(settings, path=tmp_path / name)
    store_ext.ensure_schema(store)
    store_ext.seed_spaces(store, settings.agent_get("spaces", []))
    store_ext.seed_rates(store, settings.agent_get("rates", {}))
    store_ext.create_event(
        store, id="EV-R1", name="Run-loop test wedding", type="wedding", stage="enquiry",
        event_day_offset=120, pax=40,
        stakeholders=[{"key": "c", "name": "A Client", "role": "client",
                      "email": "client@example.com"}])
    return settings, store


def test_one_sweep_drafts_and_never_sends_in_shadow_mode(tmp_path):
    settings, store = _seeded_store(tmp_path)
    code, stats = run.one_sweep(settings, store, provider="mock")
    assert code == 0
    assert stats["drafted"] > 0
    assert stats["sent"] == 0
    counts = store.counts()
    assert counts.get("sent", 0) == 0
    assert counts.get("auto_sent", 0) == 0
    store.close()


def test_rerun_on_the_same_day_does_not_duplicate_actions(tmp_path):
    settings, store = _seeded_store(tmp_path)
    run.one_sweep(settings, store, provider="mock")
    first_count = len(store.list_items(kind="event_message", limit=100))
    code, stats = run.one_sweep(settings, store, provider="mock")
    assert code == 0
    second_count = len(store.list_items(kind="event_message", limit=100))
    assert second_count == first_count
    assert stats["drafted"] == 0   # everything was already handled
    store.close()


def test_dry_run_writes_nothing_and_is_idempotent(tmp_path):
    settings, store = _seeded_store(tmp_path)
    settings.dry_run = True
    run.one_sweep(settings, store, provider="mock")
    counts_after_first = dict(store.counts())
    checklist_count = store.db.execute(
        "SELECT COUNT(*) c FROM event_checklist_items").fetchone()["c"]
    run.one_sweep(settings, store, provider="mock")
    assert dict(store.counts()) == counts_after_first
    assert checklist_count == store.db.execute(
        "SELECT COUNT(*) c FROM event_checklist_items").fetchone()["c"]
    assert checklist_count == 0   # dry-run really writes nothing
    store.close()


def test_autopilot_in_shadow_reports_held_never_sent(tmp_path, capsys):
    """Regression for SIMULATION.md Finding 5: with autopilot: true and
    mode: shadow, a routine action is ATTEMPTED but blocked by the write
    guard - the sweep headline and the final summary_line must agree, and
    neither may say "sent" for something that never left."""
    settings, store = _seeded_store(tmp_path)
    settings.agent["autopilot"] = True
    code, stats = run.one_sweep(settings, store, provider="mock")
    assert code == 0
    assert stats["sent"] == 0
    out = capsys.readouterr().out
    headline_line = next(l for l in out.splitlines() if "action(s) across" in l)
    assert "0 sent" in headline_line
    assert "held (shadow, approval kept)" in headline_line
    from core.log import summary_line
    # The headline's own "0 sent" and the run's final summary_line() must
    # agree - that agreement is the whole point of Finding 5.
    assert "0 sent (shadow)" in summary_line(stats, settings.mode)
    counts = store.counts()
    assert counts.get("sent", 0) == 0
    assert counts.get("auto_sent", 0) == 0
    store.close()


def test_approve_and_send_moves_an_item_to_sent(tmp_path):
    import review as review_tools
    from core.review import approve

    settings, store = _seeded_store(tmp_path)
    run.one_sweep(settings, store, provider="mock")
    pending = store.list_items(status="pending_review", kind="event_message", limit=10)
    assert pending
    approve(store, pending[0].id)
    settings.mode = "live"
    result = review_tools.cmd_send(store, settings, type("Args", (), {"limit": 20})())
    assert result == 0
    item = store.get_item(pending[0].id)
    assert item.review_status == "sent"
    store.close()
