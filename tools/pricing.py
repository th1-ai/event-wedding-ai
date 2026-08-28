"""tools/pricing.py - the ONE place a price is computed.

build_quote() is called by tools/engine.py (to quote a fresh enquiry and to
compute a negotiation counter), by tools/beo.py (the BEO's pricing section)
and by build_pricing_sheet() (the sendable price list) - so the number a
guest is quoted, the number on the BEO and the number on the price list can
never disagree. See docs/how-it-works.md "Never quote off memory".

Pure functions over the reference data in store_ext (event_spaces,
event_rates) plus plain values - no I/O beyond reading those two tables, no
model call, nothing guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import store_ext

SEASON_BANDS = ("low", "shoulder", "peak")


@dataclass
class QuoteLine:
    label: str
    kind: str          # venue | package | uplift | extra
    amount: float
    detail: str = ""


@dataclass
class Quote:
    lines: list[QuoteLine] = field(default_factory=list)
    total: float = 0.0


def venue_rate(store, space_slug: str, season_band: str) -> float:
    row = store_ext.get_rate(store, "venue", space_slug)
    if row is None:
        return 0.0
    return float(row[season_band if season_band in SEASON_BANDS else "shoulder"])


def package_rate(store, package_slug: str, season_band: str) -> float:
    row = store_ext.get_rate(store, "package", package_slug)
    if row is None:
        return 0.0
    return float(row[season_band if season_band in SEASON_BANDS else "shoulder"])


def space_name(store, slug: str) -> str:
    for s in store_ext.list_spaces(store):
        if s["slug"] == slug:
            return s["name"]
    return slug


def build_quote(store, event: store_ext.Event, *, uplift_spaces: list[str],
                wedding_saturday_uplift: float, currency: str = "EUR") -> Quote:
    """Sum venue[space][season_band] per space-use entry, + the wedding
    Saturday uplift per qualifying entry (weddings only, uplift_spaces only),
    + package[season_band] x pax, + every extra. Mirrors buildQuote() in the
    spec exactly - see specs/event-wedding-ai.md section 3, step 18."""
    quote = Quote()
    for use in event.spaces or []:
        slug = use.get("space") if isinstance(use, dict) else use
        rate = venue_rate(store, slug, event.season_band)
        quote.lines.append(QuoteLine(
            label=f"{space_name(store, slug)} - venue hire",
            kind="venue", amount=rate,
            detail=f"{event.season_band} band, per day"))
        quote.total += rate
        if event.type == "wedding" and slug in (uplift_spaces or []):
            quote.lines.append(QuoteLine(
                label=f"{space_name(store, slug)} - Saturday uplift",
                kind="uplift", amount=float(wedding_saturday_uplift)))
            quote.total += float(wedding_saturday_uplift)
    if event.package_id:
        per_head = package_rate(store, event.package_id, event.season_band)
        line_total = per_head * (event.pax or 0)
        quote.lines.append(QuoteLine(
            label=f"{event.package_id.title()} package",
            kind="package", amount=line_total,
            detail=f"{currency} {per_head:.0f} x {event.pax} guests, {event.season_band} band"))
        quote.total += line_total
    for extra in event.extras or []:
        amount = float(extra.get("amount", 0)) if isinstance(extra, dict) else 0.0
        label = extra.get("label", "Extra") if isinstance(extra, dict) else str(extra)
        quote.lines.append(QuoteLine(label=label, kind="extra", amount=amount))
        quote.total += amount
    return quote


def build_pricing_sheet(store) -> dict:
    """The sendable price list, rendered straight from event_rates - the same
    table build_quote() reads, so the PDF a client gets and the number the
    agent quotes cannot disagree."""
    spaces = store_ext.list_spaces(store)
    venue_rows = []
    for s in spaces:
        row = store_ext.get_rate(store, "venue", s["slug"])
        if row:
            venue_rows.append({"name": s["name"], "unit": row["unit"], "low": row["low"],
                               "shoulder": row["shoulder"], "peak": row["peak"]})
    package_rows = []
    for pkg in ("silver", "gold", "platinum", "day-delegate"):
        row = store_ext.get_rate(store, "package", pkg)
        if row:
            package_rows.append({"name": pkg.replace("-", " ").title(), "unit": row["unit"],
                                 "low": row["low"], "shoulder": row["shoulder"],
                                 "peak": row["peak"]})
    return {"venue": venue_rows, "package": package_rows}


def format_money(amount: float, currency: str = "EUR") -> str:
    return f"{currency} {amount:,.0f}"
