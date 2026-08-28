"""Tests for tools/engine.py - the planner sweep's decision rules.

Every test builds a minimal event directly in a temp store rather than going
through fixtures/, so each one isolates a single rule. See
tests/test_eventwedding_run.py for the full loop, and tests/conftest.py for
why these never read config/hotel.yaml or config/agent.yaml.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core.config import load_settings
from core.i18n import format_date
from core.store import Store

import engine
import store_ext


def _store(tmp_path, name="engine.db"):
    settings = load_settings(provider="mock", mode="shadow")
    store = Store(settings, path=tmp_path / name)
    store_ext.ensure_schema(store)
    store_ext.seed_spaces(store, settings.agent_get("spaces", []))
    store_ext.seed_rates(store, settings.agent_get("rates", {}))
    return settings, store


def test_skip_gates_today_tomorrow_past_and_done(tmp_path):
    settings, store = _store(tmp_path)
    cursor = 0
    cases = [("done_ev", "done", 50), ("past_ev", "planning", -1),
            ("today_ev", "planning", 0), ("tomorrow_ev", "planning", 1)]
    for eid, stage, offset in cases:
        store_ext.create_event(store, id=eid, name=eid, type="meeting", stage=stage,
                               event_day_offset=offset)
    result = engine.plan_sweep(store, settings)
    skipped_ids = {s.event_id for s in result.skips}
    assert skipped_ids == {"done_ev", "past_ev", "today_ev", "tomorrow_ev"}
    store.close()


def test_enquiry_branch_blocks_and_offers_two_free_alt_dates_with_holds(tmp_path):
    settings, store = _store(tmp_path)
    store_ext.seed_booked_space_day(store, "grand-ballroom", 100, "Another wedding")
    store_ext.seed_booked_space_day(store, "garden-terrace", 100, "Another wedding")
    store_ext.create_event(store, id="EV-T1", name="Test wedding", type="wedding",
                           stage="enquiry", event_day_offset=100, pax=50,
                           spaces=[{"space": "grand-ballroom"}, {"space": "garden-terrace"}])
    result = engine.plan_sweep(store, settings)
    reply = next(a for a in result.actions if a.event_id == "EV-T1" and a.kind == "reply")
    assert "already taken" in reply.message["body"]
    # Regression for SIMULATION.md Finding 3: a real calendar date, never a
    # bare "day +N" offset, in a guest-facing draft.
    lang = settings.hotel.default_language
    date_93 = format_date(store_ext.offset_to_date(store, 93), lang)
    date_107 = format_date(store_ext.offset_to_date(store, 107), lang)
    assert date_93 in reply.message["body"] and date_107 in reply.message["body"]
    assert "day +" not in reply.message["body"]
    holds = [a for a in result.actions if a.event_id == "EV-T1" and a.kind == "hold"]
    assert len(holds) == 1
    rows = store.db.execute(
        "SELECT day_offset FROM event_space_days WHERE event_ref='EV-T1'").fetchall()
    assert {r["day_offset"] for r in rows} == {93, 107}
    store.close()


def test_checklist_template_falls_back_to_generic_when_rule_is_off(tmp_path):
    settings, store = _store(tmp_path)
    raw = settings.raw["agent"]
    raw.setdefault("rules", {})["checklist_templates"] = False
    store_ext.create_event(store, id="EV-T2", name="Test offsite", type="offsite",
                           stage="enquiry", event_day_offset=100)
    engine.plan_sweep(store, settings)
    items = store_ext.list_checklist(store, "EV-T2")
    generic = settings.agent_get("checklist_templates", {})["generic"]
    assert len(items) == len(generic)
    store.close()


def test_negotiation_counter_never_falsely_claims_outside_peak_band(tmp_path):
    """Regression test for docs/how-it-works.md design decision 8: the demo
    this is ported from would say 'outside our peak band' even for an event
    seeded at season_band='peak'. This must never happen here."""
    settings, store = _store(tmp_path)
    store_ext.create_event(
        store, id="EV-T3", name="Test conference", type="conference", stage="negotiation",
        event_day_offset=45, pax=100, season_band="peak", package_id="day-delegate",
        spaces=[{"space": "grand-ballroom"}],
        thread=[{"from": "Client", "role": "client", "ai": False, "re": None,
                "body": "Can you do 15% off the total?"}])
    result = engine.plan_sweep(store, settings)
    counter = next(a for a in result.actions if a.event_id == "EV-T3")
    assert counter.kind == "counter_offer"
    assert counter.held is True
    assert "outside our peak band" not in counter.message["body"]
    assert "8%" in counter.message["body"]
    store.close()


def test_negotiation_band_off_produces_escalation_with_no_message(tmp_path):
    settings, store = _store(tmp_path)
    raw = settings.raw["agent"]
    raw.setdefault("rules", {})["negotiation_band"] = False
    store_ext.create_event(
        store, id="EV-T4", name="Test conference 2", type="conference", stage="negotiation",
        event_day_offset=45, package_id="day-delegate", spaces=[{"space": "grand-ballroom"}],
        thread=[{"from": "Client", "role": "client", "ai": False, "re": None,
                "body": "I need 20% off."}])
    result = engine.plan_sweep(store, settings)
    action = next(a for a in result.actions if a.event_id == "EV-T4")
    assert action.kind == "escalation"
    assert action.message is None
    store.close()


def test_question_branch_answers_dietary_questions_by_name(tmp_path):
    settings, store = _store(tmp_path)
    store_ext.create_event(
        store, id="EV-T5", name="Test wedding 2", type="wedding", stage="planning",
        event_day_offset=200, package_id="gold",
        thread=[{"from": "Client", "role": "client", "ai": False, "re": None,
                "body": "Is there a gluten-free option? My sister has coeliac disease."}])
    result = engine.plan_sweep(store, settings)
    reply = next(a for a in result.actions if a.event_id == "EV-T5")
    assert reply.kind == "reply"
    assert "gluten-free" in reply.message["body"]
    assert "Gold" in reply.message["body"]
    store.close()


def test_chase_skips_vendors_and_recently_pinged_items(tmp_path):
    settings, store = _store(tmp_path)
    store_ext.create_event(
        store, id="EV-T6", name="Test wedding 3", type="wedding", stage="planning",
        event_day_offset=200,
        stakeholders=[{"key": "vendor", "name": "Some Vendor", "role": "vendor",
                      "email": "vendor@example.com"},
                     {"key": "bride", "name": "The Bride", "role": "client",
                      "email": "bride@example.com"}],
        thread=[
            {"from": "Hotel", "role": "ai", "ai": True, "re": "photographer", "at_offset": -6,
             "body": "Any news?"},
            {"from": "Hotel", "role": "ai", "ai": True, "re": "florist", "at_offset": -1,
             "body": "Any news on florist?"},
        ])
    store_ext.insert_checklist_item(store, "EV-T6", "photographer", "Photographer confirmed",
                                    "client", "waiting", 180)
    store_ext.insert_checklist_item(store, "EV-T6", "florist", "Florist confirmed", "client",
                                    "waiting", 180)
    result = engine.plan_sweep(store, settings)
    chases = [a for a in result.actions if a.event_id == "EV-T6" and a.kind == "chase"]
    assert len(chases) == 1
    assert "photographer" in chases[0].message["subject"].lower()
    # Never the vendor, even though the vendor is also stale on "florist".
    assert chases[0].message["to"] == "bride@example.com"
    store.close()


def test_deposit_reminder_only_inside_the_configured_window(tmp_path):
    settings, store = _store(tmp_path)
    store_ext.create_event(
        store, id="EV-T7", name="Test meeting", type="meeting", stage="contracted",
        event_day_offset=30, deposit={"amount": 500, "due_offset": 20, "paid": False})
    store_ext.create_event(
        store, id="EV-T8", name="Test meeting 2", type="meeting", stage="contracted",
        event_day_offset=10, deposit={"amount": 500, "due_offset": 4, "paid": False})
    result = engine.plan_sweep(store, settings)
    ids = {a.event_id for a in result.actions if a.kind == "deposit_reminder"}
    assert ids == {"EV-T8"}
    store.close()


def test_negotiation_counter_and_deposit_reminder_use_the_hotels_own_currency(tmp_path):
    """Regression for SIMULATION.md Finding 1: a GBP property's negotiation
    counter and deposit reminder must show GBP throughout, never a hardcoded
    EUR default (tools/pricing.py:format_money, tools/engine.py callers)."""
    settings, store = _store(tmp_path)
    settings.hotel.currency = "GBP"
    store_ext.create_event(
        store, id="EV-T9", name="Test conference GBP", type="conference", stage="negotiation",
        event_day_offset=45, pax=100, season_band="peak", package_id="day-delegate",
        spaces=[{"space": "grand-ballroom"}],
        thread=[{"from": "Client", "role": "client", "ai": False, "re": None,
                "body": "Can you do 15% off the total?"}])
    store_ext.create_event(
        store, id="EV-T10", name="Test meeting GBP", type="meeting", stage="contracted",
        event_day_offset=10, deposit={"amount": 500, "due_offset": 4, "paid": False})
    result = engine.plan_sweep(store, settings)
    counter = next(a for a in result.actions if a.event_id == "EV-T9")
    deposit = next(a for a in result.actions if a.event_id == "EV-T10")
    assert "GBP" in counter.message["body"] and "EUR" not in counter.message["body"]
    assert "GBP" in deposit.message["body"] and "EUR" not in deposit.message["body"]
    store.close()


def test_site_visit_offer_and_enquiry_reply_never_show_a_bare_day_offset(tmp_path):
    """Regression for SIMULATION.md Finding 3, across every guest-facing
    message a fresh enquiry produces (reply + site-visit offer)."""
    settings, store = _store(tmp_path)
    store_ext.create_event(store, id="EV-T11", name="Test wedding 4", type="wedding",
                           stage="enquiry", event_day_offset=150, pax=60,
                           spaces=[{"space": "grand-ballroom"}])
    result = engine.plan_sweep(store, settings)
    drafts = [a for a in result.actions if a.event_id == "EV-T11" and a.message]
    assert drafts
    for action in drafts:
        assert "day +" not in action.message["body"]
        assert "day +" not in action.message["subject"]
    store.close()
