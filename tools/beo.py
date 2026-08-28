"""tools/beo.py - document generation: the BEO and the proposal.

Both are DETERMINISTIC - plain data assembly over the event, its checklist and
tools/pricing.py:build_quote(). No model call. Regenerating either one INSERTS
a new version (store_ext.insert_document) rather than editing in place, so the
run sheet ops works from is always the version that was actually issued - see
docs/how-it-works.md "Never quote off memory" and design decision 9 (the
proposal is new: the spec's own open question #1 flags that `does` promises a
proposal the demo engine never actually builds).
"""

from __future__ import annotations

from typing import Any

import pricing
import store_ext
import textmatch


def _capacity_for(store, space_slug: str, layout: str) -> int | None:
    for s in store_ext.list_spaces(store):
        if s["slug"] == space_slug:
            return s["capacities"].get(layout)
    return None


def _headroom_line(capacity: int | None, pax: int) -> str:
    if capacity is None:
        return "Capacity not on file for this layout - confirm before print."
    spare = capacity - pax
    if spare >= 0:
        return f"Headroom: {spare} covers spare"
    return f"Over capacity by {-spare} - ops to confirm"


def build_beo(store, event: store_ext.Event, checklist: list[store_ext.ChecklistItem],
             quote: pricing.Quote, *, currency: str = "EUR") -> dict:
    """Sections, in the spec's own order (specs/event-wedding-ai.md section 3,
    step 19): heading, KPI row, run sheet, space setups, F&B (with the dietary
    warn callout), AV, pricing summary, deposit and payment, still-open list,
    sign-off."""
    run_sheet = []
    for use in event.spaces or []:
        slug = use.get("space") if isinstance(use, dict) else use
        layout = use.get("layout", "") if isinstance(use, dict) else ""
        when = use.get("when", "") if isinstance(use, dict) else ""
        run_sheet.append({"when": when, "space": pricing.space_name(store, slug),
                          "layout": layout, "set_for": event.pax})

    space_setups = []
    for use in event.spaces or []:
        slug = use.get("space") if isinstance(use, dict) else use
        layout = use.get("layout", "") if isinstance(use, dict) else ""
        capacity = _capacity_for(store, slug, layout)
        space_setups.append({
            "space": pricing.space_name(store, slug), "layout": layout,
            "capacity": capacity, "headroom": _headroom_line(capacity, event.pax)})

    thread_texts = [m.get("body", "") for m in (event.thread or []) if not m.get("ai")]
    note_texts = [c.note for c in checklist if c.note]
    dietary_notes = textmatch.scan_dietary_notes(thread_texts + note_texts)

    av_extras = [e for e in (event.extras or [])
                if isinstance(e, dict) and textmatch.matches_av(e.get("label", ""))]

    deposit = event.deposit or {}
    balance_due_offset = event.event_day_offset - 7
    still_open = [c for c in checklist if c.status != "done"]

    version = store_ext.insert_document(store, event.id, "beo", None)
    return {
        "kind": "beo", "version": version,
        "heading": f"Banquet Event Order - {event.name}",
        "kpi": {"event_day_offset": event.event_day_offset, "guests": event.pax,
                "spaces": len(event.spaces or []),
                "contracted_value": pricing.format_money(quote.total, currency),
                "season_band": event.season_band},
        "run_sheet": run_sheet,
        "space_setups": space_setups,
        "food_and_beverage": {
            "summary": f"{event.pax} covers across {len(event.spaces or [])} space(s).",
            "dietary_warn": dietary_notes},
        "audio_visual": [{"label": e.get("label"), "amount": e.get("amount")}
                         for e in av_extras],
        "pricing_summary": {"lines": [{"label": l.label, "amount": l.amount}
                                      for l in quote.lines],
                            "total": pricing.format_money(quote.total, currency)},
        "deposit_and_payment": {
            "deposit": pricing.format_money(deposit.get("amount", 0), currency)
                       if deposit else "not set",
            "paid": bool(deposit.get("paid")) if deposit else False,
            "balance_due_offset": balance_due_offset},
        "still_open": [{"label": c.label, "owner": c.owner} for c in still_open],
        "still_open_count": f"{len(still_open)} of {len(checklist)}",
        "sign_off": "Signature: ______________________  (blank on purpose - "
                    "this agent never signs a contract)",
        "reissue_note": "Anything that moves after this is signed comes back through "
                        "the event thread and re-issues this document at the next "
                        "version - ops always works from the one that was actually sent.",
    }


def build_proposal(store, event: store_ext.Event, quote: pricing.Quote, *,
                   attachments: list[str], currency: str = "EUR") -> dict:
    """A plainer sibling of build_beo(): what actually goes to the client
    before contract, not what ops works from after. New in this template -
    see docs/how-it-works.md design decision 9."""
    version = store_ext.insert_document(store, event.id, "proposal", None)
    return {
        "kind": "proposal", "version": version,
        "heading": f"Proposal - {event.name}",
        "event_type": event.type, "pax": event.pax,
        "spaces": [pricing.space_name(store, (u.get("space") if isinstance(u, dict) else u))
                  for u in (event.spaces or [])],
        "quote_lines": [{"label": l.label, "amount": pricing.format_money(l.amount, currency)}
                        for l in quote.lines],
        "total": pricing.format_money(quote.total, currency),
        "attachments": list(attachments),
    }


def render_beo_markdown(beo: dict) -> str:
    """A plain-text rendering good enough to attach to an email or print."""
    lines = [f"# {beo['heading']}", "", f"Version {beo['version']}", ""]
    kpi = beo["kpi"]
    lines.append(f"Guests: {kpi['guests']} | Spaces: {kpi['spaces']} | "
                 f"Value: {kpi['contracted_value']} ({kpi['season_band']})")
    lines.append("")
    lines.append("## Run sheet")
    for row in beo["run_sheet"]:
        lines.append(f"- {row['when']}: {row['space']} ({row['layout']}), "
                     f"set for {row['set_for']}")
    lines.append("")
    lines.append("## Food and beverage")
    lines.append(beo["food_and_beverage"]["summary"])
    if beo["food_and_beverage"]["dietary_warn"]:
        lines.append("WARNING - dietary notes on file:")
        for note in beo["food_and_beverage"]["dietary_warn"]:
            lines.append(f"  - {note}")
    lines.append("")
    lines.append("## Pricing")
    for row in beo["pricing_summary"]["lines"]:
        lines.append(f"- {row['label']}: {row['amount']:.0f}" if isinstance(row['amount'], float)
                     else f"- {row['label']}: {row['amount']}")
    lines.append(f"Total: {beo['pricing_summary']['total']}")
    lines.append("")
    lines.append(f"Still open: {beo['still_open_count']}")
    lines.append("")
    lines.append(beo["sign_off"])
    return "\n".join(lines)
