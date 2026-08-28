"""Tests for tools/outreach.py - Event Outreach AI ("The Rainmaker"), the
folded-in sub-agent. See docs/sub-agents.md and tests/conftest.py for why
these never read config/hotel.yaml or config/agent.yaml.
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

import outreach
import outreach_store
import store_ext


def _store(tmp_path, name="outreach.db"):
    settings = load_settings(provider="mock", mode="shadow")
    store = Store(settings, path=tmp_path / name)
    outreach_store.ensure_schema(store)
    store_ext.ensure_schema(store)
    outreach.seed_demo_data(store)
    return settings, store


def test_signals_reveal_only_leads_behind_vetted_sources(tmp_path):
    _, store = _store(tmp_path)
    result = outreach.scan_signals(store, source_vetting=True)
    assert "le-01" in result["revealed"]
    assert "le-02" in result["revealed"]
    assert "le-03" not in result["revealed"]   # behind the pending source
    assert any("Summit" in s for s in result["blocked_sources"])
    store.close()


def test_signals_reveal_everything_when_source_vetting_is_off(tmp_path):
    _, store = _store(tmp_path)
    result = outreach.scan_signals(store, source_vetting=False)
    assert "le-03" in result["revealed"]
    store.close()


def test_enrich_suppresses_do_not_contact_leads_and_still_bills_a_miss(tmp_path):
    _, store = _store(tmp_path)
    # A lead with no domain at all (not part of the fixture any more - see
    # SIMULATION.md Finding 7, le-05 now has one so `campaign launch --avatar
    # wedding` works out of the box) still has to be a real, billed miss.
    outreach_store.seed_leads(store, [{"id": "le-99", "avatar": "mice", "org": "No Domain Co",
                                       "first_name": "No", "last_name": "Domain",
                                       "domain": "", "revealed": True}])
    outreach.scan_signals(store, source_vetting=False)
    result = outreach.enrich_leads(store, suppress_dnc=True)
    assert result["suppressed"] == 1          # le-06
    assert result["not_found"] == 1           # le-99 has no domain
    assert result["total_cost"] > 0           # the miss was still billed
    le06 = outreach_store.get_lead(store, "le-06")
    assert le06.email_status == "missing"     # never touched
    le05 = outreach_store.get_lead(store, "le-05")
    assert le05.email_status == "found"       # Finding 7: now enrichable
    store.close()


def test_enrich_cost_line_uses_the_hotels_own_currency(tmp_path, capsys, monkeypatch):
    """Regression for SIMULATION.md Finding 1: `enrich`'s cost line must
    never hardcode EUR on a non-euro property."""
    import os
    cfg_dir = Path(os.environ["AGENT_CONFIG_DIR"])
    hotel_yaml = cfg_dir / "hotel.yaml"
    hotel_yaml.write_text(hotel_yaml.read_text(encoding="utf-8")
                          .replace('currency: "EUR"', 'currency: "GBP"'), encoding="utf-8")
    agent_yaml = cfg_dir / "agent.yaml"
    agent_yaml.write_text(agent_yaml.read_text(encoding="utf-8")
                          .replace("enabled: false", "enabled: true"), encoding="utf-8")
    monkeypatch.setenv("AGENT_REPO_ROOT", str(tmp_path))   # keep data/agent.db out of the repo
    assert outreach.main(["seed-demo"]) == 0
    assert outreach.main(["signals"]) == 0
    capsys.readouterr()
    assert outreach.main(["enrich"]) == 0
    out = capsys.readouterr().out
    assert "GBP" in out
    assert "EUR" not in out


def test_campaign_generate_updates_avatar_on_an_existing_campaign_id(tmp_path):
    """Regression for SIMULATION.md Finding 6: regenerating camp-01 with a
    different --avatar must restamp the campaign, or `launch`'s pre-flight
    keeps checking the wrong avatar's lead pool forever."""
    _, store = _store(tmp_path)
    outreach.scan_signals(store, source_vetting=True)
    outreach.enrich_leads(store, suppress_dnc=True)

    campaign = outreach_store.create_campaign(store, "camp-01", "camp-01", "wedding")
    outreach_store.set_campaign_steps(store, campaign.id, outreach.generate_ladder("wedding"))
    assert outreach_store.get_campaign(store, "camp-01").avatar == "wedding"

    outreach_store.update_campaign(store, "camp-01", avatar="mice", name="camp-01")
    outreach_store.set_campaign_steps(store, "camp-01", outreach.generate_ladder("mice"))
    reloaded = outreach_store.get_campaign(store, "camp-01")
    assert reloaded.avatar == "mice"

    problems = outreach.preflight(store, reloaded, load_settings(provider="mock", mode="shadow"))
    assert problems == []   # 3 reachable, non-DNC mice leads exist
    store.close()


def test_wedding_avatar_launches_on_the_bundled_fixtures(tmp_path):
    """Regression for SIMULATION.md Finding 7: `workflows/20-event-outreach.md`
    promises every avatar works on sample data, including this agent's own
    core business."""
    settings, store = _store(tmp_path)
    outreach.scan_signals(store, source_vetting=True)
    outreach.enrich_leads(store, suppress_dnc=True)
    campaign = outreach_store.create_campaign(store, "camp-wed", "wedding test", "wedding")
    outreach_store.set_campaign_steps(store, campaign.id, outreach.generate_ladder("wedding"))
    problems = outreach.preflight(store, campaign, settings)
    assert problems == []
    store.close()


def test_generate_ladder_has_nine_steps_and_a_connect_note_under_300_chars(tmp_path):
    ladder = outreach.generate_ladder("mice")
    assert len(ladder) == 9
    connect = next(s for s in ladder if s["channel_kind"] == "linkedin_connect")
    assert len(connect["body"]) <= 300


def test_tick_stops_a_lead_dead_on_reply_and_hands_off_a_meeting(tmp_path):
    settings, store = _store(tmp_path)
    outreach.scan_signals(store, source_vetting=True)
    outreach.enrich_leads(store, suppress_dnc=True)
    campaign = outreach_store.create_campaign(store, "camp-t1", "test campaign", "mice")
    outreach_store.set_campaign_steps(store, campaign.id, outreach.generate_ladder("mice"))
    leads = [l.id for l in outreach_store.list_leads(store, avatar="mice", revealed=True)
            if not l.do_not_contact and (l.email_status == "found" or l.linkedin_url)]
    assert "le-04" in leads   # pre-enriched in the fixture
    outreach_store.launch_campaign(store, campaign.id, leads)

    outreach.tick(store, settings, outreach_store.get_campaign(store, campaign.id), upto_day=8)

    assert outreach_store.has_replied(store, "le-04")
    lead = outreach_store.get_lead(store, "le-04")
    event_id = outreach.hand_off(settings, store, store_ext, lead, value_eur=12500)
    event = store_ext.get_event(store, event_id)
    assert event is not None
    assert event.stage == "enquiry"
    assert event.est_value == 12500
    # idempotent - calling it again must not create a second row
    again = outreach.hand_off(settings, store, store_ext, lead, value_eur=12500)
    assert again == event_id
    assert len(store_ext.list_events(store)) == 1
    store.close()


def test_dnc_leads_are_never_ticked(tmp_path):
    settings, store = _store(tmp_path)
    campaign = outreach_store.create_campaign(store, "camp-t2", "dnc test", "mice")
    outreach_store.set_campaign_steps(store, campaign.id, outreach.generate_ladder("mice"))
    outreach_store.launch_campaign(store, campaign.id, ["le-06"])
    result = outreach.tick(store, settings, outreach_store.get_campaign(store, campaign.id),
                           upto_day=3)
    assert result["sent"] == 0
    assert result["skipped_dnc"] > 0
    store.close()
