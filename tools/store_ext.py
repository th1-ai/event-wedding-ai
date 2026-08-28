"""tools/store_ext.py - Event & Wedding AI's own tables, layered on core.store.Store.

The generic `items` table (core/store.py) is the review queue: one row per
message-bearing action waiting on a human or a send. It is not the events
ledger. This module adds the tables the spec's own data model calls for -
events, their checklists, the function-space diary, the rate card, BEO
versions and the day cursor - plus the pure helper functions tools/engine.py,
tools/pricing.py, tools/beo.py and tools/intake.py all share.

Call :func:`ensure_schema` once per `Store`, right after constructing it -
every tool in this repo does. Nothing here replaces `core.store`: same
connection (`store.db`), same `utcnow()` convention, same JSON-column style.

Idempotency, spelled out here because several callers rely on it:
- `event_checklist_items`: `UNIQUE(event_ref, item_key)` - `insert_checklist_item`
  is insert-or-ignore, so a sweep that runs twice never double-inserts.
- `event_space_days`: `UNIQUE(space_slug, day_offset)` - `hold_space_day` is
  the same insert-or-ignore ("a sweep can run twice").
- `event_spaces` / `event_rates`: re-seeded from config on every run
  (upsert by slug) - config/agent.yaml is the source of truth, not the table.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core.store import Store, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS event_bookings (
  id                TEXT PRIMARY KEY,
  name              TEXT NOT NULL,
  org               TEXT,
  type              TEXT NOT NULL,
  stage             TEXT NOT NULL DEFAULT 'enquiry',
  event_day_offset  INTEGER NOT NULL,
  pax               INTEGER NOT NULL DEFAULT 0,
  est_value         REAL NOT NULL DEFAULT 0,
  season_band       TEXT NOT NULL DEFAULT 'shoulder',
  package_id        TEXT,
  spaces_json       TEXT NOT NULL DEFAULT '[]',
  extras_json       TEXT NOT NULL DEFAULT '[]',
  stakeholders_json TEXT NOT NULL DEFAULT '[]',
  thread_json       TEXT NOT NULL DEFAULT '[]',
  deposit_json      TEXT,
  source            TEXT NOT NULL DEFAULT 'inbound',
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_stage ON event_bookings (stage, event_day_offset);

CREATE TABLE IF NOT EXISTS event_checklist_items (
  id          TEXT PRIMARY KEY,
  event_ref   TEXT NOT NULL,
  item_key    TEXT NOT NULL,
  label       TEXT NOT NULL,
  owner       TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'todo',
  due_offset  INTEGER,
  note        TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  UNIQUE(event_ref, item_key)
);
CREATE INDEX IF NOT EXISTS idx_checklist_event ON event_checklist_items (event_ref);

CREATE TABLE IF NOT EXISTS event_spaces (
  slug        TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  capacities_json TEXT NOT NULL DEFAULT '{}',
  sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS event_space_days (
  id                TEXT PRIMARY KEY,
  space_slug        TEXT NOT NULL,
  day_offset        INTEGER NOT NULL,
  status            TEXT NOT NULL,
  event_ref         TEXT,
  label             TEXT,
  held_since_offset INTEGER,
  UNIQUE(space_slug, day_offset)
);
CREATE INDEX IF NOT EXISTS idx_space_days_lookup ON event_space_days (space_slug, day_offset);

CREATE TABLE IF NOT EXISTS event_rates (
  id          TEXT PRIMARY KEY,
  kind        TEXT NOT NULL,
  slug        TEXT NOT NULL,
  unit        TEXT NOT NULL,
  low         REAL NOT NULL,
  shoulder    REAL NOT NULL,
  peak        REAL NOT NULL,
  UNIQUE(kind, slug)
);

CREATE TABLE IF NOT EXISTS event_documents (
  id          TEXT PRIMARY KEY,
  event_ref   TEXT NOT NULL,
  kind        TEXT NOT NULL,
  version     INTEGER NOT NULL,
  sections_json TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_event ON event_documents (event_ref, kind, version);

CREATE TABLE IF NOT EXISTS event_state (
  id          TEXT PRIMARY KEY DEFAULT 'state',
  day_cursor  INTEGER NOT NULL DEFAULT 0,
  anchor_date TEXT
);

CREATE TABLE IF NOT EXISTS event_runs (
  id          TEXT PRIMARY KEY,
  created_at  TEXT NOT NULL,
  headline    TEXT,
  thinking_json TEXT,
  summary_json  TEXT,
  narrative   TEXT
);
"""


def ensure_schema(store: Store) -> None:
    """Create every table above if it does not already exist. Idempotent."""
    store.db.executescript(SCHEMA)
    # Migration for a database created before anchor_date existed (SIMULATION.md
    # Finding 3) - CREATE TABLE IF NOT EXISTS above never adds a column to an
    # already-existing table, so an older data/agent.db needs this once.
    cols = {r["name"] for r in store.db.execute("PRAGMA table_info(event_state)").fetchall()}
    if "anchor_date" not in cols:
        store.db.execute("ALTER TABLE event_state ADD COLUMN anchor_date TEXT")


# --------------------------------------------------------------------------
# reference data: spaces + rates, re-seeded from config/agent.yaml every run
# --------------------------------------------------------------------------
def seed_spaces(store: Store, spaces_cfg: list[dict]) -> None:
    for i, s in enumerate(spaces_cfg):
        store.db.execute(
            "INSERT INTO event_spaces (slug, name, capacities_json, sort_order) VALUES (?,?,?,?) "
            "ON CONFLICT(slug) DO UPDATE SET name=excluded.name, "
            "capacities_json=excluded.capacities_json, sort_order=excluded.sort_order",
            (s["slug"], s["name"], json.dumps(s.get("capacities") or {}), i))


def seed_rates(store: Store, rates_cfg: dict) -> None:
    unit = {"venue": "per day", "package": "per person"}
    for kind, rows in (rates_cfg or {}).items():
        for slug, bands in rows.items():
            rate_id = f"{kind}:{slug}"
            store.db.execute(
                "INSERT INTO event_rates (id, kind, slug, unit, low, shoulder, peak) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(kind, slug) DO UPDATE SET "
                "unit=excluded.unit, low=excluded.low, shoulder=excluded.shoulder, "
                "peak=excluded.peak",
                (rate_id, kind, slug, unit.get(kind, "per unit"),
                 float(bands["low"]), float(bands["shoulder"]), float(bands["peak"])))


def list_spaces(store: Store) -> list[dict]:
    rows = store.db.execute(
        "SELECT * FROM event_spaces ORDER BY sort_order ASC").fetchall()
    return [{"slug": r["slug"], "name": r["name"],
             "capacities": json.loads(r["capacities_json"] or "{}")} for r in rows]


def get_rate(store: Store, kind: str, slug: str) -> dict | None:
    row = store.db.execute(
        "SELECT * FROM event_rates WHERE kind=? AND slug=?", (kind, slug)).fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------
# state: the single day-cursor row
# --------------------------------------------------------------------------
def ensure_state(store: Store) -> dict:
    row = store.db.execute("SELECT * FROM event_state WHERE id='state'").fetchone()
    if row is None:
        store.db.execute("INSERT INTO event_state (id, day_cursor) VALUES ('state', 0)")
        return {"day_cursor": 0}
    return {"day_cursor": row["day_cursor"]}


def get_day_cursor(store: Store) -> int:
    return int(ensure_state(store)["day_cursor"])


def set_day_cursor(store: Store, value: int) -> None:
    ensure_state(store)
    store.db.execute("UPDATE event_state SET day_cursor=? WHERE id='state'", (int(value),))


def get_anchor_date(store: Store) -> str:
    """The real calendar date that ``day_offset`` 0 maps to (ISO ``YYYY-MM-DD``).

    Every guest-facing draft needs a real date, not the internal day-offset
    integer (SIMULATION.md Finding 3: "day +60" meant nothing to the hotelier
    reading it). Set once, lazily, the first time anything needs to convert an
    offset to a date - from today's real date, since intake does not yet
    extract a real calendar date from an inbound message. Never changes once
    set, so a date already quoted to a guest never moves under them."""
    ensure_state(store)
    row = store.db.execute("SELECT anchor_date FROM event_state WHERE id='state'").fetchone()
    if row and row["anchor_date"]:
        return row["anchor_date"]
    from datetime import date
    today = date.today().isoformat()
    store.db.execute("UPDATE event_state SET anchor_date=? WHERE id='state'", (today,))
    return today


def offset_to_date(store: Store, day_offset: int) -> str:
    """ISO calendar date for one ``day_offset``, anchored at :func:`get_anchor_date`.
    Feed the result to ``core.i18n.format_date`` for a guest-facing string."""
    from datetime import date, timedelta
    anchor = date.fromisoformat(get_anchor_date(store))
    return (anchor + timedelta(days=int(day_offset))).isoformat()


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------
@dataclass
class Event:
    id: str
    name: str
    org: str
    type: str
    stage: str
    event_day_offset: int
    pax: int
    est_value: float
    season_band: str
    package_id: str | None
    spaces: list = field(default_factory=list)
    extras: list = field(default_factory=list)
    stakeholders: list = field(default_factory=list)
    thread: list = field(default_factory=list)
    deposit: dict | None = None
    source: str = "inbound"

    @classmethod
    def from_row(cls, row: Any) -> "Event":
        return cls(
            id=row["id"], name=row["name"], org=row["org"] or "", type=row["type"],
            stage=row["stage"], event_day_offset=row["event_day_offset"], pax=row["pax"],
            est_value=row["est_value"], season_band=row["season_band"],
            package_id=row["package_id"],
            spaces=json.loads(row["spaces_json"] or "[]"),
            extras=json.loads(row["extras_json"] or "[]"),
            stakeholders=json.loads(row["stakeholders_json"] or "[]"),
            thread=json.loads(row["thread_json"] or "[]"),
            deposit=json.loads(row["deposit_json"]) if row["deposit_json"] else None,
            source=row["source"])


def next_event_id(store: Store) -> str:
    n = store.next_sequence("event_id")
    return f"EV-{n:03d}"


def create_event(store: Store, *, id: str | None = None, name: str, org: str = "",
                 type: str, stage: str = "enquiry", event_day_offset: int, pax: int = 0,
                 est_value: float = 0.0, season_band: str = "shoulder",
                 package_id: str | None = None, spaces: list | None = None,
                 extras: list | None = None, stakeholders: list | None = None,
                 thread: list | None = None, deposit: dict | None = None,
                 source: str = "inbound") -> Event:
    event_id = id or next_event_id(store)
    now = utcnow()
    store.db.execute(
        "INSERT INTO event_bookings (id, name, org, type, stage, event_day_offset, pax, "
        "est_value, season_band, package_id, spaces_json, extras_json, stakeholders_json, "
        "thread_json, deposit_json, source, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (event_id, name, org, type, stage, int(event_day_offset), int(pax), float(est_value),
         season_band, package_id, json.dumps(spaces or []), json.dumps(extras or []),
         json.dumps(stakeholders or []), json.dumps(thread or []),
         json.dumps(deposit) if deposit else None, source, now, now))
    return get_event(store, event_id)  # type: ignore[return-value]


def get_event(store: Store, event_id: str) -> Event | None:
    row = store.db.execute("SELECT * FROM event_bookings WHERE id=?", (event_id,)).fetchone()
    return Event.from_row(row) if row else None


def list_events(store: Store, *, stage: str | None = None) -> list[Event]:
    if stage:
        rows = store.db.execute(
            "SELECT * FROM event_bookings WHERE stage=? ORDER BY event_day_offset ASC",
            (stage,)).fetchall()
    else:
        rows = store.db.execute(
            "SELECT * FROM event_bookings ORDER BY event_day_offset ASC").fetchall()
    return [Event.from_row(r) for r in rows]


def update_event(store: Store, event_id: str, **fields: Any) -> None:
    """Update plain columns and/or JSON columns (spaces/extras/stakeholders/thread/deposit)."""
    json_cols = {"spaces", "extras", "stakeholders", "thread", "deposit"}
    cols, params = [], []
    for key, value in fields.items():
        col = f"{key}_json" if key in json_cols else key
        cols.append(f"{col}=?")
        params.append(json.dumps(value) if key in json_cols else value)
    if not cols:
        return
    params += [utcnow(), event_id]
    store.db.execute(f"UPDATE event_bookings SET {', '.join(cols)}, updated_at=? WHERE id=?",
                     params)


def append_thread_message(store: Store, event_id: str, message: dict) -> None:
    """Append one message to an event's thread. Always a full rewrite - see
    docs/how-it-works.md: the demo's own PostgREST rule ("a thread write always
    sends the FULL array") carries over even though this is SQLite."""
    ev = get_event(store, event_id)
    if ev is None:
        raise KeyError(f"no event {event_id}")
    thread = ev.thread + [message]
    update_event(store, event_id, thread=thread)


# --------------------------------------------------------------------------
# checklist
# --------------------------------------------------------------------------
@dataclass
class ChecklistItem:
    id: str
    event_ref: str
    item_key: str
    label: str
    owner: str
    status: str
    due_offset: int | None
    note: str | None

    @classmethod
    def from_row(cls, row: Any) -> "ChecklistItem":
        return cls(id=row["id"], event_ref=row["event_ref"], item_key=row["item_key"],
                   label=row["label"], owner=row["owner"], status=row["status"],
                   due_offset=row["due_offset"], note=row["note"])


def insert_checklist_item(store: Store, event_id: str, item_key: str, label: str,
                          owner: str, status: str, due_offset: int,
                          note: str | None = None, *, dry_run: bool = False) -> bool:
    """Insert-or-ignore. Returns True if a new row was actually created (or,
    on dry_run, would have been - see core.store.next_sequence for the same
    "peek, do not write" pattern)."""
    row_id = f"{event_id}-{item_key}"
    exists = store.db.execute(
        "SELECT 1 FROM event_checklist_items WHERE id=?", (row_id,)).fetchone() is not None
    if dry_run:
        return not exists
    now = utcnow()
    cur = store.db.execute(
        "INSERT OR IGNORE INTO event_checklist_items (id, event_ref, item_key, label, owner, "
        "status, due_offset, note, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (row_id, event_id, item_key, label, owner, status, int(due_offset), note, now, now))
    return cur.rowcount == 1


def list_checklist(store: Store, event_id: str) -> list[ChecklistItem]:
    rows = store.db.execute(
        "SELECT * FROM event_checklist_items WHERE event_ref=? ORDER BY due_offset ASC",
        (event_id,)).fetchall()
    return [ChecklistItem.from_row(r) for r in rows]


def set_checklist_status(store: Store, event_id: str, item_key: str, status: str,
                         note: str | None = None) -> None:
    store.db.execute(
        "UPDATE event_checklist_items SET status=?, note=COALESCE(?, note), updated_at=? "
        "WHERE event_ref=? AND item_key=?",
        (status, note, utcnow(), event_id, item_key))


# --------------------------------------------------------------------------
# space diary - sparse: no row means free. A "lapsed" row also means free.
# --------------------------------------------------------------------------
FREE_STATUSES = ("lapsed",)


def space_status(store: Store, space_slug: str, day_offset: int) -> tuple[str, dict | None]:
    """Returns (status, row) where status is 'free', 'held' or 'booked'.
    A lapsed row still exists (for history) but counts as free."""
    row = store.db.execute(
        "SELECT * FROM event_space_days WHERE space_slug=? AND day_offset=?",
        (space_slug, int(day_offset))).fetchone()
    if row is None or row["status"] in FREE_STATUSES:
        return "free", (dict(row) if row else None)
    return row["status"], dict(row)


def hold_space_day(store: Store, space_slug: str, day_offset: int, event_id: str,
                   label: str, held_since_offset: int, *, dry_run: bool = False) -> bool:
    """Insert-or-ignore a 'held' row. Returns True if newly created (or, on
    dry_run, would have been)."""
    row_id = f"{space_slug}:{day_offset}"
    exists = store.db.execute(
        "SELECT 1 FROM event_space_days WHERE id=?", (row_id,)).fetchone() is not None
    if dry_run:
        return not exists
    cur = store.db.execute(
        "INSERT OR IGNORE INTO event_space_days (id, space_slug, day_offset, status, "
        "event_ref, label, held_since_offset) VALUES (?,?,?,?,?,?,?)",
        (row_id, space_slug, int(day_offset), "held", event_id, label, int(held_since_offset)))
    return cur.rowcount == 1


def lapse_expired_holds(store: Store, day_cursor: int, hold_expiry_days: int, *,
                        dry_run: bool = False) -> list[str]:
    """Flip every 'held' row older than hold_expiry_days to 'lapsed' (free again).
    Returns the ids that were (or, on dry_run, would be) flipped."""
    rows = store.db.execute(
        "SELECT id FROM event_space_days WHERE status='held' AND ? - held_since_offset >= ?",
        (int(day_cursor), int(hold_expiry_days))).fetchall()
    ids = [r["id"] for r in rows]
    if ids and not dry_run:
        store.db.executemany(
            "UPDATE event_space_days SET status='lapsed' WHERE id=?", [(i,) for i in ids])
    return ids


# --------------------------------------------------------------------------
# documents (BEO / proposal versions)
# --------------------------------------------------------------------------
def insert_document(store: Store, event_id: str, kind: str, sections: dict | None) -> int:
    row = store.db.execute(
        "SELECT MAX(version) AS v FROM event_documents WHERE event_ref=? AND kind=?",
        (event_id, kind)).fetchone()
    version = (row["v"] or 0) + 1
    doc_id = f"{kind}-{event_id}-v{version}"
    store.db.execute(
        "INSERT INTO event_documents (id, event_ref, kind, version, sections_json, "
        "created_at) VALUES (?,?,?,?,?,?)",
        (doc_id, event_id, kind, version, json.dumps(sections) if sections else None, utcnow()))
    return version


def latest_document(store: Store, event_id: str, kind: str) -> dict | None:
    row = store.db.execute(
        "SELECT * FROM event_documents WHERE event_ref=? AND kind=? ORDER BY version DESC LIMIT 1",
        (event_id, kind)).fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------
# runs (for tools/report.py)
# --------------------------------------------------------------------------
def record_sweep_run(store: Store, run_id: str, headline: str, thinking: list,
                     summary: dict, narrative: str | None) -> None:
    store.db.execute(
        "INSERT INTO event_runs (id, created_at, headline, thinking_json, summary_json, "
        "narrative) VALUES (?,?,?,?,?,?)",
        (run_id, utcnow(), headline, json.dumps(thinking), json.dumps(summary), narrative))


def mark_thread_message_sent(store: Store, event_id: str, item_id: str) -> None:
    """Flip the thread entry that carries `item_id` to held=False once a
    human has actually approved and sent it - mirrors the demo's
    useApproveMessage. Safe to call on an event with no matching entry."""
    ev = get_event(store, event_id)
    if ev is None:
        return
    changed = False
    thread = []
    for msg in ev.thread:
        if msg.get("item_id") == item_id and msg.get("held"):
            msg = {**msg, "held": False, "hold_reason": None}
            changed = True
        thread.append(msg)
    if changed:
        update_event(store, event_id, thread=thread)


def seed_booked_space_day(store: Store, space_slug: str, day_offset: int, label: str) -> bool:
    """An externally-booked day (not an agent hold) - fixtures/demo only.
    Insert-or-ignore, same idempotency as hold_space_day."""
    row_id = f"{space_slug}:{day_offset}"
    cur = store.db.execute(
        "INSERT OR IGNORE INTO event_space_days (id, space_slug, day_offset, status, "
        "event_ref, label, held_since_offset) VALUES (?,?,?,?,?,?,?)",
        (row_id, space_slug, int(day_offset), "booked", None, label, None))
    return cur.rowcount == 1
