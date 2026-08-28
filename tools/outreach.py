#!/usr/bin/env python3
"""tools/outreach.py - Event Outreach AI ("The Rainmaker"), a folded-in
sub-agent. Off by default (config/agent.yaml: subagents.event_outreach.enabled).

    python3 tools/outreach.py signals                     # scan sources, reveal leads
    python3 tools/outreach.py enrich                       # find contacts, with a cost ticker
    python3 tools/outreach.py campaign generate <id> --avatar wedding
    python3 tools/outreach.py campaign launch <id>          # the human approval gate
    python3 tools/outreach.py tick [--days 1]               # advance the clock, send within caps
    python3 tools/outreach.py funnel <id>
    python3 tools/outreach.py inbox                         # replies waiting for a human
    python3 tools/outreach.py reply <lead_id> --body-file r.txt   # a human sends, by hand

DETERMINISTIC end to end - no model call anywhere in this file, matching the
spec (specs/event-outreach-ai.md section 3 header). `stop_on_reply` halts a
lead's sequence the moment they answer on any channel, and nothing in the
inbox sends itself - `reply` is a human typing, not an AI draft; see
docs/how-it-works.md design decisions 4-6 and docs/sub-agents.md for what is
simplified here relative to the demo (no LinkedIn-acceptance branching, no
avatar-suggestion clustering) and why.

Once launched, a campaign's routine sends are NOT gated through the review
queue item-by-item the way tools/run.py's are - launch itself is the human
approval, exactly like the demo's "Launch campaign" button. `mode: shadow`
still blocks every real send (email/whatsapp); LinkedIn/Instagram steps have
no core adapter at all and are logged only - see docs/integrations.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_email, get_messaging  # noqa: E402
from core.adapters.base import AdapterError  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.review import WriteBlocked  # noqa: E402
from core.store import Store  # noqa: E402

import outreach_store  # noqa: E402

CONNECT_NOTE_MAX = 300

CONNECT_NOTES = {
    "mice": ("Hi {first}, we host offsites and board days close to the hotel. What prompted "
            "me: {signal}. Worth connecting?"),
    "agency": ("Hi {first}, we work with local event partners on offsites and congress "
              "blocks. Would value being on {org}'s venue radar."),
    "wedding": ("Hi {first}, congratulations. We host a handful of coastal weddings a year "
               "and I would love to show you the terrace."),
}

BREAKUP_BODY = ("I will stop here - inboxes are sacred. If an offsite or board day comes up "
               "later in the year, this thread will find me and the terrace will still have "
               "your name on it.")


def hook_for(lead: outreach_store.Lead) -> str:
    """The per-lead opener. First match wins - see specs/event-outreach-ai.md
    section 7 'hookFor'."""
    signal = lead.signal_snapshot
    if not signal:
        return f"Your work at {lead.org} keeps coming up in our local events circle."
    if re.search(r"office", signal, re.I):
        return f"Saw that {signal} - congrats, a new office usually means a team worth celebrating."
    if re.search(r"hiring", signal, re.I):
        return (f"Noticed {signal} - teams that grow that fast usually need a day out of the "
               f"building to stay one team.")
    if re.search(r"raised|funding", signal, re.I):
        return f"Congratulations - {signal}. The next board meeting deserves a better view."
    if re.search(r"engaged", signal, re.I):
        return f"Congratulations on the engagement - the terrace at sunset was made for exactly this."
    return f"Saw that {signal} - that is what made me reach out."


def generate_ladder(avatar_kind: str) -> list[dict]:
    """The 9-step sequence, specs/event-outreach-ai.md section 4 table.
    Delays are relative to the PREVIOUS SENT step, not to launch."""
    note = CONNECT_NOTES.get(avatar_kind, CONNECT_NOTES["mice"])
    last_channel = "instagram_dm" if avatar_kind == "wedding" else "whatsapp"
    return [
        {"channel_kind": "linkedin_visit", "delay_days": 0, "condition": "always", "body": ""},
        {"channel_kind": "linkedin_like", "delay_days": 1, "condition": "always",
         "body": "Like their most recent company post"},
        {"channel_kind": "linkedin_connect", "delay_days": 2, "condition": "always",
         "body": note},
        {"channel_kind": "linkedin_message", "delay_days": 1, "condition": "if_no_reply",
         "body": "{hook}"},
        {"channel_kind": "email", "delay_days": 3, "condition": "if_no_reply",
         "subject": "A quick idea for {org}", "body": "{hook} Worth a short call?"},
        {"channel_kind": "email", "delay_days": 4, "condition": "if_no_reply",
         "subject": "One more thought", "body": "One of our clients said the write-up took "
                    "her 20 minutes because we ran the run-sheet - happy to send it over."},
        {"channel_kind": "withdraw_connect", "delay_days": 7, "condition": "if_no_reply",
         "body": "Withdraw the invite quietly - never leave stale requests hanging"},
        {"channel_kind": last_channel, "delay_days": 2, "condition": "if_no_reply",
         "body": "A message here is easier for some - happy to keep this quick."},
        {"channel_kind": "email", "delay_days": 5, "condition": "if_no_reply",
         "subject": "Closing the file for now", "body": BREAKUP_BODY},
    ]


def render_message(step: dict, lead: outreach_store.Lead, *, ai_personalization: bool) -> str:
    if not ai_personalization:
        hook, signal = "I'll keep this short.", "your events calendar"
    else:
        hook, signal = hook_for(lead), lead.signal_snapshot or "your events calendar"
    return (step["body"].replace("{hook}", hook).replace("{signal}", signal)
           .replace("{first}", lead.first_name or "there").replace("{org}", lead.org or "you"))


def preflight(store, campaign: outreach_store.Campaign, settings) -> list[str]:
    """Five-ish checks, all must pass before launch - specs/event-outreach-ai.md
    section 3 step 6, trimmed to what this template actually enforces."""
    problems = []
    leads = [l for l in outreach_store.list_leads(store, avatar=campaign.avatar, revealed=True)
            if not l.do_not_contact and (l.email_status == "found" or l.linkedin_url)]
    if not leads:
        problems.append("no reachable, non-DNC leads for this avatar - run `signals` and "
                        "`enrich` first")
    steps = outreach_store.list_steps(store, campaign.id)
    if not steps:
        problems.append("no sequence - run `campaign generate` first")
    for step in steps:
        if step["channel_kind"] == "linkedin_connect":
            for lead in leads:
                text = render_message(step, lead, ai_personalization=True)
                if len(text) > CONNECT_NOTE_MAX:
                    problems.append(f"connect note exceeds {CONNECT_NOTE_MAX} chars for "
                                    f"{lead.id}")
                    break
    return problems


def seed_demo_data(store) -> int:
    """Load fixtures/outreach/*.json (sources, signals, leads) so a hotel can
    try the sub-agent before connecting a real signal feed. Idempotent -
    sources/signals upsert, leads insert-or-ignore."""
    base = REPO_ROOT / "fixtures" / "outreach"
    outreach_store.seed_sources(store, json.loads((base / "sources.json").read_text()))
    outreach_store.seed_signals(store, json.loads((base / "signals.json").read_text()))
    return outreach_store.seed_leads(store, json.loads((base / "leads.json").read_text()))


# --------------------------------------------------------------------------
# signals -> enrich
# --------------------------------------------------------------------------
ENRICH_COST = {"hunter": 0.034, "findymail": 0.049}


def scan_signals(store, *, source_vetting: bool) -> dict:
    """Reveal every hidden lead whose signal_snapshot already carries an
    active signal's keyword, from a usable source. Returns a summary dict."""
    signals = store.db.execute("SELECT * FROM outreach_signals WHERE enabled=1").fetchall()
    sources = {r["id"]: dict(r) for r in store.db.execute("SELECT * FROM outreach_sources")}
    usable_ids = {sid for sid, s in sources.items()
                 if not source_vetting or s["status"] in ("approved", "testing")}
    blocked = sorted({sources[s["source_id"]]["name"] for s in signals
                      if s["source_id"] not in usable_ids and s["source_id"] in sources})

    revealed = []
    hidden = outreach_store.list_leads(store, revealed=False)
    for lead in hidden:
        for sig in signals:
            if sig["source_id"] not in usable_ids:
                continue
            if lead.signal_snapshot and re.search(re.escape(sig["keyword"]), lead.signal_snapshot,
                                                   re.I):
                outreach_store.reveal_lead(store, lead.id, lead.signal_snapshot)
                revealed.append(lead.id)
                break
    return {"revealed": revealed, "blocked_sources": blocked}


def enrich_leads(store, *, suppress_dnc: bool) -> dict:
    """Find contacts for revealed, missing-email, non-DNC leads. Costs are
    SIMULATED (no live Hunter.io/Findymail call - see docs/how-it-works.md
    design decision 6), but the arithmetic is real: a miss is still billed."""
    found, not_found, suppressed, total_cost = 0, 0, 0, 0.0
    leads = outreach_store.list_leads(store, revealed=True)
    for i, lead in enumerate(leads):
        if lead.email_status != "missing":
            continue
        if lead.do_not_contact:
            if suppress_dnc:
                suppressed += 1
                continue
        provider = "findymail" if lead.linkedin_url else "hunter"
        cost = ENRICH_COST[provider]
        if not lead.domain:
            outreach_store.set_enrichment(store, lead.id, email="", email_status="not_found",
                                         provider=provider, cost=cost)
            not_found += 1
            total_cost += cost
            continue
        local = f"{(lead.first_name or 'contact').lower()}.{(lead.last_name or i).__str__().lower()}"
        if i % 5 == 4:
            local = f"{(lead.first_name or 'c')[:1].lower()}.{(lead.last_name or i).__str__().lower()}"
        email = f"{local}@{lead.domain}"
        outreach_store.set_enrichment(store, lead.id, email=email, email_status="found",
                                      provider=provider, cost=cost)
        found += 1
        total_cost += cost
    return {"found": found, "not_found": not_found, "suppressed": suppressed,
           "total_cost": round(total_cost, 2)}


# --------------------------------------------------------------------------
# the daily tick - persisted, incremental (design decision 4)
# --------------------------------------------------------------------------
def _family(channel_kind: str) -> str:
    if channel_kind.startswith("linkedin") or channel_kind == "withdraw_connect":
        return "linkedin"
    if channel_kind == "email":
        return "email"
    if channel_kind == "whatsapp":
        return "whatsapp"
    if channel_kind == "instagram_dm":
        return "instagram"
    return channel_kind


def _next_due(store, campaign: outreach_store.Campaign, lead_id: str,
             steps: list[dict]) -> tuple[int, dict, int] | tuple[None, None, None]:
    sent = outreach_store.steps_sent_for_lead(store, campaign.id, lead_id)
    done_idx = {e["step_idx"] for e in sent}
    last_day = max((e["day"] for e in sent), default=0)
    for i, step in enumerate(steps):
        if i in done_idx:
            continue
        return i, step, last_day + int(step["delay_days"])
    return None, None, None


def tick(store, settings, campaign: outreach_store.Campaign, *, upto_day: int) -> dict:
    cfg = settings.agent_get("subagents.event_outreach", {}) or {}
    weekend_pause = bool(cfg.get("weekend_pause", True))
    stop_on_reply = bool(cfg.get("stop_on_reply", True))
    suppress_dnc = bool(cfg.get("suppress_dnc", True))
    caps = cfg.get("daily_caps", {}) or {}
    steps = outreach_store.list_steps(store, campaign.id)
    email = get_email(settings)
    messaging = get_messaging(settings)

    sent, queued, replied_today, withdrawn, skipped_dnc = 0, 0, 0, 0, 0
    for day in range(campaign.day + 1, upto_day + 1):
        if weekend_pause and day % 7 in (5, 6):
            continue
        for lead_id in campaign.lead_ids:
            lead = outreach_store.get_lead(store, lead_id)
            if lead is None:
                continue
            if lead.do_not_contact and suppress_dnc:
                skipped_dnc += 1
                continue
            if stop_on_reply and outreach_store.has_replied(store, lead_id):
                continue
            script = lead.script or {}
            reply_after = script.get("reply_after")
            if reply_after is not None and day >= int(reply_after) and script.get("reply_text"):
                if outreach_store.record_reply(store, lead_id, day, script.get("reply_channel",
                                               "email"), script["reply_text"]):
                    replied_today += 1
                if stop_on_reply:
                    continue
            idx, step, due = _next_due(store, campaign, lead_id, steps)
            if step is None or day < due:
                continue
            family = _family(step["channel_kind"])
            cap = int(caps.get(family, 0))
            if cap and outreach_store.day_spend(store, campaign.id, day, family) >= cap:
                queued += 1
                continue
            text = render_message(step, lead, ai_personalization=cfg.get("ai_personalization",
                                                                         True))
            ok = _send_step(email, messaging, step, lead, text)
            if not ok:
                continue   # blocked (shadow) - stays due, retried next tick
            outreach_store.record_step_sent(store, campaign.id, lead_id, idx, day,
                                            step["channel_kind"])
            if step["channel_kind"] == "withdraw_connect":
                withdrawn += 1
            else:
                sent += 1
        outreach_store.set_campaign_day(store, campaign.id, day)
    return {"sent": sent, "queued_by_caps": queued, "replies": replied_today,
           "withdrawn": withdrawn, "skipped_dnc": skipped_dnc}


def _send_step(email, messaging, step: dict, lead: outreach_store.Lead, text: str) -> bool:
    """LinkedIn/Instagram: logged only, no core adapter exists (design
    decision 5). Email/WhatsApp: a real guarded write. Returns False (never
    raises) when a WriteBlocked stops it - the caller leaves the step due."""
    kind = step["channel_kind"]
    if kind in ("linkedin_visit", "linkedin_like", "linkedin_connect", "linkedin_message",
               "withdraw_connect", "instagram_dm"):
        return True
    try:
        if kind == "email" and lead.email:
            email.send(lead.email, step.get("subject", "") or "", text)
            return True
        if kind == "whatsapp" and lead.phone:
            messaging.send(lead.phone, text)
            return True
    except WriteBlocked:
        return False
    return False   # no address/phone on file - never sent blind


# --------------------------------------------------------------------------
# funnel table + the hand-off
# --------------------------------------------------------------------------
def funnel_stats(store, campaign: outreach_store.Campaign) -> list[dict]:
    steps = outreach_store.list_steps(store, campaign.id)
    total = len(campaign.lead_ids)
    rows = []
    for step in steps:
        done = store.db.execute(
            "SELECT COUNT(*) c FROM outreach_events WHERE campaign_id=? AND step_idx=?",
            (campaign.id, step["idx"])).fetchone()["c"]
        rows.append({"idx": step["idx"], "channel": step["channel_kind"], "done": done,
                    "waiting": max(0, total - done)})
    return rows


def hand_off(settings, store, event_store_module, lead: outreach_store.Lead, *,
            value_eur: float) -> str:
    """A booked reply becomes an event_bookings row at stage 'enquiry' - the
    real fix for the spec's own open question (specs/event-outreach-ai.md
    section 11 point 2): 'a qualified outbound lead ought to arrive as an
    event_bookings row ... today nothing does that.' Idempotent: one hand-off
    per lead, keyed on a stable id."""
    event_id = f"EV-OR-{lead.id}"
    existing = event_store_module.get_event(store, event_id)
    if existing is not None:
        return existing.id
    event_type = "wedding" if lead.avatar == "wedding" else "conference"
    event_store_module.create_event(
        store, id=event_id, name=f"{lead.org or lead.full_name} - outreach enquiry",
        org=lead.org, type=event_type, stage="enquiry",
        event_day_offset=event_store_module.get_day_cursor(store) + 90,
        pax=0, est_value=value_eur,
        stakeholders=[{"key": "primary", "name": lead.full_name or lead.org,
                      "role": "client", "email": lead.email}],
        thread=[{"from": lead.full_name or lead.org, "role": "client",
                "body": "(handed off from Event Outreach AI - see the reply in "
                        "outreach_replies for the full exchange)",
                "at_offset": event_store_module.get_day_cursor(store), "ai": False, "re": None}],
        source="outreach")
    return event_id


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _print_problems(problems: list[str]) -> None:
    for p in problems:
        print(f"  - {p}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seed-demo", help="load fixtures/outreach/*.json to try this before "
                                     "connecting a real signal feed")
    sub.add_parser("signals", help="scan sources, reveal matching leads")
    sub.add_parser("enrich", help="find contacts for revealed leads")

    p_campaign = sub.add_parser("campaign")
    csub = p_campaign.add_subparsers(dest="campaign_command", required=True)
    p_gen = csub.add_parser("generate")
    p_gen.add_argument("id")
    p_gen.add_argument("--name", default=None)
    p_gen.add_argument("--avatar", required=True, choices=["mice", "wedding", "agency"])
    p_launch = csub.add_parser("launch")
    p_launch.add_argument("id")

    p_tick = sub.add_parser("tick", help="advance the clock and send what is due")
    p_tick.add_argument("--days", type=int, default=1)
    p_tick.add_argument("--campaign", default=None, help="default: every running campaign")

    p_funnel = sub.add_parser("funnel")
    p_funnel.add_argument("id")

    sub.add_parser("inbox", help="replies waiting for a human")

    p_reply = sub.add_parser("reply", help="a human sends a reply, by hand - never automated")
    p_reply.add_argument("lead_id")
    p_reply.add_argument("--body-file", required=True)

    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    if not settings.agent_get("subagents.event_outreach.enabled", False):
        print("subagents.event_outreach.enabled is false in config/agent.yaml - "
             "see docs/sub-agents.md before turning it on.", file=sys.stderr)
        return 1

    cfg = settings.agent_get("subagents.event_outreach", {}) or {}
    store = Store(settings)
    outreach_store.ensure_schema(store)
    import store_ext
    store_ext.ensure_schema(store)
    try:
        if args.command == "seed-demo":
            n = seed_demo_data(store)
            print(f"seeded {n} new lead(s), plus sources and signals, from fixtures/outreach/")
            return 0

        if args.command == "signals":
            result = scan_signals(store, source_vetting=bool(cfg.get("source_vetting", True)))
            print(f"revealed {len(result['revealed'])} lead(s): {', '.join(result['revealed']) or '(none)'}")
            if result["blocked_sources"]:
                print(f"blocked (not vetted): {', '.join(result['blocked_sources'])}")
            return 0

        if args.command == "enrich":
            result = enrich_leads(store, suppress_dnc=bool(cfg.get("suppress_dnc", True)))
            print(f"found {result['found']}, not found {result['not_found']} (still billed), "
                 f"suppressed {result['suppressed']} (DNC, never billed). "
                 f"Cost: {settings.hotel.currency} {result['total_cost']}")
            return 0

        if args.command == "campaign" and args.campaign_command == "generate":
            campaign = outreach_store.get_campaign(store, args.id)
            if campaign is None:
                campaign = outreach_store.create_campaign(store, args.id, args.name or args.id,
                                                           args.avatar)
            else:
                # Regenerating an existing campaign id with a different
                # --avatar must restamp it here, or `campaign launch`'s
                # pre-flight silently checks the WRONG avatar's lead pool -
                # see SIMULATION.md Finding 6.
                outreach_store.update_campaign(store, args.id, avatar=args.avatar,
                                               name=args.name or campaign.name)
                campaign = outreach_store.get_campaign(store, args.id)
            outreach_store.set_campaign_steps(store, campaign.id, generate_ladder(args.avatar))
            print(f"generated a {len(outreach_store.list_steps(store, campaign.id))}-step "
                 f"sequence for {campaign.id} ({args.avatar})")
            return 0

        if args.command == "campaign" and args.campaign_command == "launch":
            campaign = outreach_store.get_campaign(store, args.id)
            if campaign is None:
                print(f"error: no campaign {args.id}", file=sys.stderr)
                return 1
            problems = preflight(store, campaign, settings)
            if problems:
                print(f"pre-flight failed for {args.id}:")
                _print_problems(problems)
                return 1
            leads = [l.id for l in outreach_store.list_leads(store, avatar=campaign.avatar,
                                                              revealed=True)
                    if not l.do_not_contact and (l.email_status == "found" or l.linkedin_url)]
            outreach_store.launch_campaign(store, args.id, leads)
            print(f"launched {args.id} with {len(leads)} lead(s)")
            return 0

        if args.command == "tick":
            campaigns = ([outreach_store.get_campaign(store, args.campaign)]
                        if args.campaign else
                        [c for c in outreach_store.list_campaigns(store) if c.status == "running"])
            for campaign in campaigns:
                if campaign is None:
                    continue
                result = tick(store, settings, campaign, upto_day=campaign.day + args.days)
                print(f"{campaign.id}: {result['sent']} sent, {result['queued_by_caps']} "
                     f"queued by caps, {result['replies']} new repl(y/ies), "
                     f"{result['withdrawn']} withdrawn, {result['skipped_dnc']} DNC-skipped, "
                     f"now at day +{outreach_store.get_campaign(store, campaign.id).day}")
                for reply in outreach_store.list_replies(store, unhandled_only=True):
                    lead = outreach_store.get_lead(store, reply["lead_id"])
                    if lead and lead.script.get("wants_meeting"):
                        event_id = hand_off(settings, store, store_ext, lead,
                                           value_eur=float(cfg.get("meeting_value", 12500)))
                        print(f"  {lead.id} wants a meeting -> handed off as {event_id}")
            return 0

        if args.command == "funnel":
            campaign = outreach_store.get_campaign(store, args.id)
            if campaign is None:
                print(f"error: no campaign {args.id}", file=sys.stderr)
                return 1
            for row in funnel_stats(store, campaign):
                print(f"  {row['idx']}. {row['channel']:<18} done={row['done']:<3} "
                     f"waiting={row['waiting']}")
            return 0

        if args.command == "inbox":
            replies = outreach_store.list_replies(store, unhandled_only=True)
            if not replies:
                print("Nothing waiting in the outreach inbox.")
                return 0
            for r in replies:
                lead = outreach_store.get_lead(store, r["lead_id"])
                name = lead.full_name if lead else r["lead_id"]
                print(f"  {r['id']}  {name}  ({r['channel_kind']}, day +{r['day']}): "
                     f"{r['body'][:80]}")
            return 0

        if args.command == "reply":
            lead = outreach_store.get_lead(store, args.lead_id)
            if lead is None:
                print(f"error: no lead {args.lead_id}", file=sys.stderr)
                return 1
            body = Path(args.body_file).read_text(encoding="utf-8")
            email = get_email(settings)
            try:
                email.send(lead.email or "lead@example.com", f"Re: {lead.org}", body)
            except WriteBlocked as exc:
                print(f"blocked: {exc}")
                return 1
            for r in outreach_store.list_replies(store, unhandled_only=True):
                if r["lead_id"] == args.lead_id:
                    outreach_store.mark_reply_handled(store, r["id"])
            print(f"sent and marked handled for {args.lead_id}")
            return 0

        parser.error("unknown command")
        return 2
    except AdapterError as exc:
        print(f"integration error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
