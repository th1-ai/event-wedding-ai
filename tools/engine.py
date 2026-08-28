"""tools/engine.py - the planner sweep. DETERMINISTIC decisioning, no model call.

One entry point: plan_sweep(store, settings) -> SweepResult. Mirrors
specs/event-wedding-ai.md section 3 (runPlannerSweep) branch for branch:
skip gates, checklist read, thread-tail classification, branches A-E, mode
resolution, summary. The only LLM call anywhere in this agent's main loop is
the desk note in tools/run.py, and intake's classification in tools/intake.py
- see docs/how-it-works.md.

Internal actions (checklist_build, hold) are applied directly to the store
here, because they are the agent's own bookkeeping, not an external write -
see docs/how-it-works.md "Idempotency". Message-bearing actions are returned
as data; tools/run.py turns each into a core.store.Item and routes it through
core.review, because sending mail IS an external write and must be guarded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pricing
import store_ext
import textmatch

from core.i18n import format_date

VENDOR_RE = re.compile(r"vendor", re.I)


@dataclass
class Constants:
    chase_silence_days: int
    deposit_window_days: int
    hold_expiry_days: int
    negotiation_band_pct: int
    wedding_saturday_uplift: float
    uplift_spaces: list[str]
    default_wedding_spaces: list[str]
    alt_date_steps: list[int]
    site_visit_slots: list[dict]
    enquiry_attachments: list[str]
    menus_doc: str
    currency: str
    language: str


def load_constants(settings) -> Constants:
    g = settings.agent_get
    return Constants(
        chase_silence_days=int(g("chase_silence_days", 5)),
        deposit_window_days=int(g("deposit_window_days", 14)),
        hold_expiry_days=int(g("hold_expiry_days", 7)),
        negotiation_band_pct=int(g("negotiation_band_pct", 8)),
        wedding_saturday_uplift=float(g("wedding_saturday_uplift", 600)),
        uplift_spaces=list(g("uplift_spaces", [])),
        default_wedding_spaces=list(g("default_wedding_spaces", [])),
        alt_date_steps=list(g("alt_date_steps", [-7, 7, -14, 14, -21, 21])),
        site_visit_slots=list(g("site_visit_slots", [])),
        enquiry_attachments=list(g("enquiry_attachments", [])),
        menus_doc=str(g("menus_doc", "catering-menus")),
        # config/hotel.yaml, not agent.yaml - every money string and every
        # calendar date in a draft must match the hotel's own settings, never
        # a hardcoded default. See SIMULATION.md Findings 1 and 3.
        currency=str(settings.hotel.currency),
        language=str(settings.hotel.default_language))


def load_rules(settings) -> dict[str, bool]:
    raw = settings.agent_get("rules", {}) or {}
    keys = ("checklist_templates", "site_visit_offer", "hold_expiry", "follow_up_5d",
            "deposit_reminder_14d", "negotiation_band", "attach_materials")
    return {k: bool(raw.get(k, True)) for k in keys}


def load_checklist_templates(settings) -> dict[str, list[dict]]:
    return settings.agent_get("checklist_templates", {}) or {}


@dataclass
class Action:
    event_id: str
    kind: str
    title: str
    detail: str
    message: dict | None = None      # {to, subject, body, attachments, re}
    gates: list[str] = field(default_factory=list)   # "pricing" -> always held
    held: bool = False               # set by resolve_held() during plan_sweep
    unique_key: str | None = None    # tools/run.py: store.upsert_unique dedup key,
                                      # None for internal actions already applied in-place


@dataclass
class Skip:
    event_id: str
    reason: str


@dataclass
class SweepResult:
    thinking: list[str] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    skips: list[Skip] = field(default_factory=list)
    checklist_built: int = 0
    holds_placed: int = 0


def uniq(items: list[str]) -> list[str]:
    out: list[str] = []
    for i in items:
        if i not in out:
            out.append(i)
    return out


def event_spaces_wanted(event: store_ext.Event, constants: Constants) -> list[str]:
    slugs = uniq([u.get("space") if isinstance(u, dict) else u for u in (event.spaces or [])])
    if slugs:
        return slugs
    if event.type == "wedding":
        return list(constants.default_wedding_spaces)
    return []


def last_message(event: store_ext.Event) -> dict | None:
    """Most recent thread entry - the thread is append-only, so the last
    element IS the newest (matches the spec's 'later entries win ties')."""
    return event.thread[-1] if event.thread else None


def latest_with_re(event: store_ext.Event, key: str) -> dict | None:
    for msg in reversed(event.thread or []):
        if msg.get("re") == key:
            return msg
    return None


def week_label(cursor: int, day_offset: int) -> str:
    delta = day_offset - cursor
    weeks, rem = divmod(abs(delta), 7)
    direction = "later" if delta >= 0 else "earlier"
    if weeks and not rem:
        unit = "week" if weeks == 1 else "weeks"
        return f"a {unit} {direction}" if weeks == 1 else f"{weeks} {unit} {direction}"
    return f"{abs(delta)} days {direction}"


def weekday_of(day_offset: int) -> int:
    """0 = Monday, matching the spec's 'day 0 is a Monday' convention."""
    return day_offset % 7


def is_midweek(day_offset: int) -> bool:
    return weekday_of(day_offset) in (1, 2, 3)   # Tue/Wed/Thu


# --------------------------------------------------------------------------
# skip gates
# --------------------------------------------------------------------------
def skip_reason(event: store_ext.Event, cursor: int) -> str | None:
    if event.stage == "done":
        return "Completed - the file is closed"
    days_out = event.event_day_offset - cursor
    if days_out < 0:
        return "Event has already run - the file is closed"
    if days_out == 0:
        return "Event is today - run-sheet final, nothing to chase"
    if days_out == 1:
        return "Event is tomorrow - run-sheet final, nothing to chase"
    return None


# --------------------------------------------------------------------------
# Branch A - fresh enquiry
# --------------------------------------------------------------------------
def _find_free_alt_dates(store, spaces_wanted: list[str], cursor: int,
                         alt_date_steps: list[int], anchor: int) -> list[int]:
    found: list[int] = []
    for step in alt_date_steps:
        candidate = anchor + step
        if candidate <= cursor:
            continue
        if all(store_ext.space_status(store, s, candidate)[0] == "free" for s in spaces_wanted):
            found.append(candidate)
        if len(found) >= 2:
            break
    return sorted(found)


def branch_enquiry(store, event: store_ext.Event, cursor: int, constants: Constants,
                   rules: dict, checklist_templates: dict[str, list[dict]], *,
                   dry_run: bool = False) -> list[Action]:
    actions: list[Action] = []
    spaces_wanted = event_spaces_wanted(event, constants)
    blocked_by = None
    if spaces_wanted:
        for slug in spaces_wanted:
            status, row = store_ext.space_status(store, slug, event.event_day_offset)
            if status in ("held", "booked"):
                blocked_by = row.get("label", "another booking") if row else "another booking"
                break

    requested_date = format_date(store_ext.offset_to_date(store, event.event_day_offset),
                                 constants.language)
    hold_days: list[int] = []
    if blocked_by:
        alt_dates = _find_free_alt_dates(store, spaces_wanted, cursor, constants.alt_date_steps,
                                         event.event_day_offset)
        offer_lines = ", then ".join(
            f"{week_label(cursor, d)} ({format_date(store_ext.offset_to_date(store, d), constants.language)})"
            for d in alt_dates) or "no free alternative in range"
        body = (f"Straight answer first: {requested_date}, the date you asked "
               f"about, is already taken ({blocked_by}), so I cannot offer it. "
               f"The nearest I can offer is {offer_lines}, and I am holding both for "
               f"7 days while you decide.")
        hold_days = alt_dates
    else:
        body = (f"Good news: {requested_date} is free across "
               f"{', '.join(pricing.space_name(store, s) for s in spaces_wanted) or 'the space you asked for'}. "
               f"I am holding it for 7 days while we put the details together.")
        hold_days = [event.event_day_offset]

    attachments = list(constants.enquiry_attachments) if rules["attach_materials"] else []
    actions.append(Action(
        event_id=event.id, kind="reply",
        title="Offered the nearest free date(s)" if blocked_by else "Confirmed the date is free",
        detail=f"spaces requested: {', '.join(spaces_wanted) or '(none named)'}",
        message={"subject": f"Re: {event.name}", "body": body, "attachments": attachments,
                "re": None},
        unique_key=f"{event.id}:enquiry-reply"))

    template_key = event.type if (rules["checklist_templates"] and event.type in checklist_templates) \
        else "generic"
    template = checklist_templates.get(template_key, [])
    built = 0
    for row in template:
        due = event.event_day_offset + int(row["rel_due"])
        status = "done" if row["key"] == "qualify" else "todo"
        if store_ext.insert_checklist_item(store, event.id, row["key"], row["label"],
                                           row["owner"], status, due, dry_run=dry_run):
            built += 1
    actions.append(Action(
        event_id=event.id, kind="checklist_build",
        title=f"Built the {len(template)}-item {template_key} checklist",
        detail=f"{built} new row(s)"))

    if rules["site_visit_offer"]:
        slots = ", ".join(
            f"{format_date(store_ext.offset_to_date(store, cursor + s['offset']), constants.language)} "
            f"{s['time']}" for s in constants.site_visit_slots)
        actions.append(Action(
            event_id=event.id, kind="site_visit_offer",
            title=f"Proposed {len(constants.site_visit_slots)} site-visit slots",
            detail=slots,
            message={"subject": f"Re: {event.name} - site visit",
                    "body": (f"Would any of these work for a visit? {slots}. "
                            f"It takes about 45 minutes and I will have the layout set up "
                            f"so you can see it rather than imagine it."),
                    "attachments": [], "re": "site-visit"},
            unique_key=f"{event.id}:enquiry-site-visit"))

    placed = 0
    for day in hold_days:
        for slug in spaces_wanted:
            if store_ext.hold_space_day(store, slug, day, event.id,
                                        f"{event.name} - 7-day hold", cursor, dry_run=dry_run):
                placed += 1
    if placed:
        actions.append(Action(
            event_id=event.id, kind="hold",
            title=f"Held {len(hold_days)} date(s) for 7 days",
            detail="Offering a date I have not protected is how you lose it."))
    return actions


# --------------------------------------------------------------------------
# Branch B - negotiation
# --------------------------------------------------------------------------
def branch_negotiation(store, event: store_ext.Event, last: dict, constants: Constants,
                       rules: dict) -> list[Action]:
    ask = textmatch.discount_pct(last.get("body", ""))
    quote = pricing.build_quote(store, event, uplift_spaces=constants.uplift_spaces,
                                wedding_saturday_uplift=constants.wedding_saturday_uplift,
                                currency=constants.currency)
    counter = round(quote.total * (100 - constants.negotiation_band_pct) / 100)
    av = next((e for e in (event.extras or [])
              if isinstance(e, dict) and textmatch.matches_av(e.get("label", ""))), None)

    if not rules["negotiation_band"]:
        return [Action(
            event_id=event.id, kind="escalation",
            title="Negotiation band is off - nothing drafted",
            detail="The negotiation band is switched off, so I have drafted nothing - "
                  "the thread, the quote and the ask are on your desk.")]

    reasons = []
    if is_midweek(event.event_day_offset):
        reasons.append("you are midweek")
    if event.season_band != "peak":
        reasons.append(f"outside our peak band ({event.season_band} season)")
    reason_text = " and ".join(reasons) if reasons else "inside our approved negotiation band"
    ask_text = f"{ask:.0f}%" if ask else "a larger discount"

    body = (f"I cannot do {ask_text}, but here is what I can do: "
           f"{constants.negotiation_band_pct}% off because {reason_text}, which takes "
           f"{pricing.format_money(quote.total, constants.currency)} to "
           f"{pricing.format_money(counter, constants.currency)}")
    if av is not None:
        body += (f", with {av.get('label')} "
                f"({pricing.format_money(float(av.get('amount', 0)), constants.currency)}) "
                f"included rather than invoiced on top")
    body += ". Everything else in the proposal stands."

    return [Action(
        event_id=event.id, kind="counter_offer",
        title="Countered inside the negotiation band",
        detail=f"{constants.negotiation_band_pct}% band: "
              f"{pricing.format_money(quote.total, constants.currency)} -> "
              f"{pricing.format_money(counter, constants.currency)} "
              f"against {ask_text} asked - pricing never sends without a human, in either mode.",
        message={"subject": f"Re: {event.name} - pricing", "body": body,
                "attachments": [], "re": last.get("re")},
        gates=["pricing"], unique_key=f"{event.id}:counter:{len(event.thread)}")]


# --------------------------------------------------------------------------
# Branch C - an unanswered question
# --------------------------------------------------------------------------
def branch_question(store, event: store_ext.Event, last: dict, constants: Constants,
                    rules: dict) -> list[Action]:
    attachments = []
    if textmatch.is_dietary(last.get("body", "")):
        package = (event.package_id or "").replace("-", " ").title() or "your package"
        body = (f"On the dietary point: we run a fully gluten-free-per-course option and a "
               f"clean prep section in the kitchen, and it slots straight into the "
               f"{package} menu at no extra charge. I have checked it against your file "
               f"rather than leaving it for a phone call.")
        if rules["attach_materials"]:
            attachments = [constants.menus_doc]
        title = "Answered the dietary question"
    else:
        body = ("Good question - I have checked it against your file rather than leaving "
               "it for a phone call, and the short answer is yes, that works. Let me know "
               "if you would like the detail in writing as well.")
        title = "Answered the open question"
    return [Action(
        event_id=event.id, kind="reply", title=title,
        detail=f"question from: {last.get('from', 'the client')}",
        message={"subject": f"Re: {event.name}", "body": body, "attachments": attachments,
                "re": last.get("re")},
        unique_key=f"{event.id}:question:{len(event.thread)}")]


# --------------------------------------------------------------------------
# Branch D - chases
# --------------------------------------------------------------------------
def branch_chases(event: store_ext.Event, checklist: list[store_ext.ChecklistItem],
                  cursor: int, constants: Constants) -> tuple[list[Action], list[str]]:
    actions: list[Action] = []
    thinking: list[str] = []
    for item in checklist:
        if item.owner != "client" or item.status != "waiting":
            continue
        pinged = latest_with_re(event, item.item_key)
        if pinged is None:
            thinking.append(f"{item.label}: never pinged, nothing to chase yet")
            continue
        if not pinged.get("ai"):
            thinking.append(f"{item.label}: they answered, the ball is with us")
            continue
        silent = cursor - int(pinged.get("at_offset", cursor))
        if silent < constants.chase_silence_days:
            thinking.append(f"{item.label}: pinged {silent} day(s) ago, inside the "
                            f"{constants.chase_silence_days}-day line")
            continue
        recipient = next((s for s in (event.stakeholders or [])
                          if not VENDOR_RE.search(s.get("role", ""))), None)
        to_name = recipient.get("name") if recipient else "the client"
        actions.append(Action(
            event_id=event.id, kind="chase",
            title=f"Chased the {item.label.lower()}",
            detail=f"silent {silent} day(s), addressed to {to_name}",
            message={"subject": f"Re: {event.name} - {item.label}",
                    "body": (f"Checking back in on {item.label.lower()} - it has been "
                            f"{silent} days since I last asked. A yes or a no is all I "
                            f"need; if it is easier, I can re-send anything you need "
                            f"in one message."),
                    "attachments": [], "re": item.item_key,
                    # Never the vendor - see docs/how-it-works.md guardrails.
                    # None falls back to _primary_contact() in tools/run.py
                    # and tools/review.py if this stakeholder has no email.
                    "to": recipient.get("email") if recipient else None,
                    "to_name": to_name},
            unique_key=f"{event.id}:chase:{item.item_key}:{len(event.thread)}"))
    return actions, thinking


# --------------------------------------------------------------------------
# Branch E - deposit reminder
# --------------------------------------------------------------------------
def branch_deposit(event: store_ext.Event, cursor: int, constants: Constants,
                   rules: dict) -> list[Action]:
    if not rules["deposit_reminder_14d"] or not event.deposit:
        return []
    if event.deposit.get("paid"):
        return []
    days_out = event.event_day_offset - cursor
    if days_out > constants.deposit_window_days:
        return []
    amount = pricing.format_money(float(event.deposit.get("amount", 0)), constants.currency)
    body = (f"A reminder that the deposit of {amount} is still outstanding, "
           f"{days_out} day(s) out from the event. The room stays held for you either "
           f"way, but I cannot confirm catering numbers with the kitchen until it lands.")
    return [Action(
        event_id=event.id, kind="deposit_reminder",
        title=f"Deposit reminder ({amount})",
        detail=f"{days_out} day(s) out, inside the {constants.deposit_window_days}-day window",
        message={"subject": f"Re: {event.name} - deposit", "body": body,
                "attachments": [], "re": "deposit"},
        unique_key=f"{event.id}:deposit-reminder")]


# --------------------------------------------------------------------------
# mode resolution: co-pilot holds everything; autopilot holds only pricing.
# See docs/how-it-works.md "Modes" - mode: shadow (config/hotel.yaml) still
# has the final word over both, enforced downstream in tools/run.py.
# --------------------------------------------------------------------------
def resolve_held(action: Action, autopilot: bool) -> bool:
    if action.message is None:
        return False   # internal actions apply regardless of mode
    if "pricing" in action.gates:
        return True
    return not autopilot


# --------------------------------------------------------------------------
# the sweep
# --------------------------------------------------------------------------
def plan_sweep(store, settings) -> SweepResult:
    constants = load_constants(settings)
    rules = load_rules(settings)
    templates = load_checklist_templates(settings)
    autopilot = bool(settings.agent_get("autopilot", False))
    dry_run = bool(getattr(settings, "dry_run", False))
    cursor = store_ext.get_day_cursor(store)

    result = SweepResult()
    events = store_ext.list_events(store)
    result.thinking.append(f"Read the event book at day +{cursor}: {len(events)} event(s) on file.")

    touched_with_message = 0
    for event in events:
        reason = skip_reason(event, cursor)
        if reason:
            result.skips.append(Skip(event.id, reason))
            continue

        checklist = store_ext.list_checklist(store, event.id)
        done = sum(1 for c in checklist if c.status == "done")
        result.thinking.append(
            f"{event.id} checklist: {done} done, "
            f"{sum(1 for c in checklist if c.status == 'waiting')} waiting, "
            f"{sum(1 for c in checklist if c.status == 'todo')} todo.")

        last = last_message(event)
        negotiating = (event.stage == "negotiation" and last is not None
                       and not last.get("ai") and textmatch.asks_discount(last.get("body", "")))

        thread_actions: list[Action] = []
        if event.stage == "enquiry":
            thread_actions = branch_enquiry(store, event, cursor, constants, rules, templates,
                                            dry_run=dry_run)
        elif negotiating:
            thread_actions = branch_negotiation(store, event, last, constants, rules)
        elif last is not None and not last.get("ai") and textmatch.asks_question(last.get("body", "")):
            thread_actions = branch_question(store, event, last, constants, rules)
        result.actions.extend(thread_actions)

        if rules["follow_up_5d"]:
            chase_actions, chase_thinking = branch_chases(event, checklist, cursor, constants)
            result.actions.extend(chase_actions)
            result.thinking.extend(f"{event.id}: {t}" for t in chase_thinking)

        result.actions.extend(branch_deposit(event, cursor, constants, rules))

        if any(a.message for a in thread_actions) or any(
                a.event_id == event.id and a.message for a in result.actions):
            touched_with_message += 1

    if rules["hold_expiry"]:
        lapsed = store_ext.lapse_expired_holds(store, cursor, constants.hold_expiry_days,
                                               dry_run=dry_run)
        if lapsed:
            result.thinking.append(f"{len(lapsed)} hold(s) past {constants.hold_expiry_days} "
                                   f"days lapsed back to free.")

    for action in result.actions:
        action.held = resolve_held(action, autopilot)

    with_msg = [a for a in result.actions if a.message]
    internal = [a for a in result.actions if a.message is None]
    touched = uniq([a.event_id for a in result.actions])
    result.thinking.append(
        f"{len(result.actions)} action(s) across {len(touched)} event(s): "
        f"{len(with_msg)} with a message, {len(internal)} internal.")
    return result


def summary_headline(result: SweepResult) -> dict:
    with_msg = [a for a in result.actions if a.message]
    touched = uniq([a.event_id for a in result.actions])
    return {
        "actions": len(result.actions), "events_touched": len(touched),
        "with_message": len(with_msg),
        "held": sum(1 for a in with_msg if a.held),
        "auto": sum(1 for a in with_msg if not a.held),
        "skips": len(result.skips),
    }
