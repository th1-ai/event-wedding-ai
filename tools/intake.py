"""tools/intake.py - turn a raw inbound email into an event row or a thread entry.

The ONE addition this template makes beyond the demo engine it is built from:
specs/event-wedding-ai.md's runPlannerSweep takes fully-formed event rows as
input. A real mailbox does not arrive that way, so this module reads unread
mail and, with one small classification model call, decides "new enquiry" vs
"reply on an existing event" - see docs/how-it-works.md design decision 1.

Idempotent the same way the reference agent's triage is: `store.upsert_item`
dedups by (source, external_id), and the item's cached classification
survives a re-run so an `interactive` pend does not force re-classifying.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.adapters.base import EmailMessage
from core.config import Settings
from core.llm import LLMResult, LLMSchemaError, complete
from core.store import Item, Store
from core.templates import build_prompt

import store_ext

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "schemas"
INTAKE_SCHEMA = json.loads((SCHEMAS_DIR / "intake.json").read_text(encoding="utf-8"))


def email_to_dict(msg: EmailMessage) -> dict:
    return {"id": msg.id, "from": msg.from_email, "from_name": msg.from_name,
           "subject": msg.subject, "body": msg.body_text, "received_at": msg.received_at}


def _open_events_block(store: Store) -> str:
    events = store_ext.list_events(store)
    if not events:
        return "(none yet - this must be a new enquiry)"
    return "\n".join(f"- {e.id}: \"{e.name}\" ({e.org or 'no org given'}), "
                     f"day +{e.event_day_offset}" for e in events)


def classify_message(settings: Settings, store: Store, item: Item, msg: EmailMessage,
                     *, provider: str | None = None) -> dict:
    space_slugs = ", ".join(s["slug"] for s in store_ext.list_spaces(store))
    prompt = build_prompt("intake", settings=settings, item=email_to_dict(msg),
                          fixture_id=msg.id, open_events=_open_events_block(store),
                          space_slugs=space_slugs)
    result: LLMResult = complete("intake", prompt, INTAKE_SCHEMA, settings=settings,
                                 provider=provider, store=store, item_id=item.id,
                                 fixture_id=msg.id)
    data = result.data or {}
    store.set_fields(item.id, payload={**(item.payload or {}), "_intake_cache": data})
    return data


def process_message(settings: Settings, store: Store, msg: EmailMessage, *,
                    provider: str | None = None) -> tuple[Item, str]:
    """Classify one inbound email and either open a new event or file it onto
    an existing one. Returns (item, outcome) where outcome is one of
    'new_event' | 'filed' | 'needs_human' | 'unrelated' | 'already_done'."""
    item = store.upsert_item("email", msg.id, kind="event_intake", payload=email_to_dict(msg))
    if item.review_status != "new":
        return item, "already_done"

    cached = (item.payload or {}).get("_intake_cache")
    data = cached or classify_message(settings, store, item, msg, provider=provider)

    if data.get("needs_human"):
        updated = store.transition(item.id, "needs_human", actor="agent",
                                   detail={"reason": data.get("reason", "")})
        return updated, "needs_human"

    kind = data.get("kind")
    if kind == "new_enquiry":
        # Every guest-facing draft needs a real date, not a bare day-offset
        # (SIMULATION.md Finding 3). The message itself carries no calendar
        # date yet (see prompts/schemas/intake.json), so anchor day 0 to
        # today the first time it is needed - store_ext.offset_to_date()
        # then turns every event_day_offset into a real date from here on.
        store_ext.get_anchor_date(store)
        event_type = data.get("event_type") or "meeting"
        event = store_ext.create_event(
            store, name=msg.subject or f"Enquiry from {msg.from_name or msg.from_email}",
            org=msg.from_name or "", type=event_type,
            event_day_offset=store_ext.get_day_cursor(store) + 60,   # placeholder until qualified
            pax=int(data.get("pax") or 0),
            spaces=[{"space": s} for s in (data.get("requested_spaces") or [])],
            stakeholders=[{"key": "primary", "name": msg.from_name or msg.from_email,
                          "role": "client"}],
            thread=[{"from": msg.from_name or msg.from_email, "role": "client",
                    "body": msg.body_text, "at_offset": store_ext.get_day_cursor(store),
                    "ai": False, "re": None}],
            source="inbound")
        store.record_event(item.id, "agent", "opened_event", {"event_id": event.id})
        updated = store.transition(item.id, "skipped", actor="agent",
                                   detail={"opened_event": event.id})
        return updated, "new_event"

    if kind == "reply":
        event_id = data.get("matched_event_id")
        if event_id and store_ext.get_event(store, event_id):
            store_ext.append_thread_message(store, event_id, {
                "from": msg.from_name or msg.from_email, "role": "client",
                "body": msg.body_text, "at_offset": store_ext.get_day_cursor(store),
                "ai": False, "re": None})
            store.record_event(item.id, "agent", "filed_message", {"event_id": event_id})
            updated = store.transition(item.id, "skipped", actor="agent",
                                       detail={"filed_to": event_id})
            return updated, "filed"
        updated = store.transition(item.id, "needs_human", actor="agent",
                                   detail={"reason": "no matching event id returned"})
        return updated, "needs_human"

    updated = store.transition(item.id, "skipped", actor="agent",
                               detail={"reason": "not an event enquiry"})
    return updated, "unrelated"
