"""tools/textmatch.py - small deterministic text scans shared by engine.py and
beo.py. No model call anywhere in this file - these are plain regexes over
English prose, exactly as faithful (and exactly as limited) as the spec they
come from. See docs/how-it-works.md and docs/safety.md: a guest who writes in
a language your regexes were not built for still gets `needs_human` from
tools/intake.py, which is model-driven and language-aware.
"""

from __future__ import annotations

import re

DISCOUNT_RE = re.compile(r"(%|discount|off the total|better price|come down|budget is tight|cheaper)",
                         re.I)
DISCOUNT_PCT_RE = re.compile(r"(\d{1,2}(?:\.\d)?)\s*%")
DIETARY_RE = re.compile(
    r"(coeliac|celiac|gluten|allerg|vegan|vegetarian|dietary|nut[- ]free|halal|kosher)", re.I)
AV_RE = re.compile(r"(\bav\b|audio|sound|light|screen|stage|production|\bdj\b|micro)", re.I)


def asks_question(body: str) -> bool:
    """Real people bury the ask mid-paragraph and sign off with a sentence -
    an ends-with-'?' rule misses that, so this is a bare substring search."""
    return "?" in (body or "")


def asks_discount(body: str) -> bool:
    return bool(DISCOUNT_RE.search(body or ""))


def discount_pct(body: str) -> float | None:
    m = DISCOUNT_PCT_RE.search(body or "")
    return float(m.group(1)) if m else None


def is_dietary(body: str) -> bool:
    return bool(DIETARY_RE.search(body or ""))


def matches_av(label: str) -> bool:
    return bool(AV_RE.search(label or ""))


def scan_dietary_notes(texts: list[str], max_notes: int = 4) -> list[str]:
    """Every sentence across `texts` that trips DIETARY_RE, deduplicated,
    capped at max_notes - what the BEO's dietary callout is built from."""
    notes: list[str] = []
    for text in texts:
        for sentence in re.split(r"(?<=[.!?])\s+", text or ""):
            sentence = sentence.strip()
            if sentence and DIETARY_RE.search(sentence) and sentence not in notes:
                notes.append(sentence)
            if len(notes) >= max_notes:
                return notes
    return notes
