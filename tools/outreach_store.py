"""tools/outreach_store.py - tables for Event Outreach AI ("The Rainmaker"),
the folded-in sub-agent. Off by default - config/agent.yaml:
subagents.event_outreach.enabled. See docs/sub-agents.md.

Kept separate from tools/store_ext.py because this is a genuinely different
domain (outbound prospecting, not the events ledger) that only a minority of
installs will ever turn on - see specs/event-outreach-ai.md and
docs/how-it-works.md design decisions 4-6 for what is simplified here and why.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core.store import Store, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS outreach_sources (
  id      TEXT PRIMARY KEY,
  name    TEXT NOT NULL,
  status  TEXT NOT NULL DEFAULT 'approved'
);

CREATE TABLE IF NOT EXISTS outreach_signals (
  id         TEXT PRIMARY KEY,
  avatar     TEXT NOT NULL,
  keyword    TEXT NOT NULL,
  label      TEXT NOT NULL,
  source_id  TEXT NOT NULL,
  enabled    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS outreach_leads (
  id               TEXT PRIMARY KEY,
  avatar           TEXT NOT NULL,
  org              TEXT,
  first_name       TEXT,
  last_name        TEXT,
  role             TEXT,
  domain           TEXT,
  city             TEXT,
  linkedin_url     TEXT,
  phone            TEXT,
  email            TEXT,
  email_status     TEXT NOT NULL DEFAULT 'missing',
  enrich_provider  TEXT,
  enrich_cost      REAL,
  signal_snapshot  TEXT,
  do_not_contact   INTEGER NOT NULL DEFAULT 0,
  revealed         INTEGER NOT NULL DEFAULT 0,
  script_json      TEXT NOT NULL DEFAULT '{}',
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outreach_campaigns (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  avatar        TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'draft',
  day           INTEGER NOT NULL DEFAULT 0,
  lead_ids_json TEXT NOT NULL DEFAULT '[]',
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outreach_steps (
  id           TEXT PRIMARY KEY,
  campaign_id  TEXT NOT NULL,
  idx          INTEGER NOT NULL,
  channel_kind TEXT NOT NULL,
  delay_days   INTEGER NOT NULL,
  condition    TEXT NOT NULL,
  subject      TEXT,
  body         TEXT NOT NULL,
  UNIQUE(campaign_id, idx)
);

CREATE TABLE IF NOT EXISTS outreach_events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id  TEXT NOT NULL,
  lead_id      TEXT NOT NULL,
  step_idx     INTEGER NOT NULL,
  day          INTEGER NOT NULL,
  channel_kind TEXT NOT NULL,
  UNIQUE(campaign_id, lead_id, step_idx)
);

CREATE TABLE IF NOT EXISTS outreach_replies (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id      TEXT NOT NULL,
  day          INTEGER NOT NULL,
  channel_kind TEXT NOT NULL,
  body         TEXT NOT NULL,
  handled      INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL
);
"""


def ensure_schema(store: Store) -> None:
    store.db.executescript(SCHEMA)


@dataclass
class Lead:
    id: str
    avatar: str
    org: str
    first_name: str
    last_name: str
    role: str
    domain: str
    city: str
    linkedin_url: str
    phone: str
    email: str
    email_status: str
    enrich_provider: str | None
    enrich_cost: float | None
    signal_snapshot: str
    do_not_contact: bool
    revealed: bool
    script: dict = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Any) -> "Lead":
        return cls(
            id=row["id"], avatar=row["avatar"], org=row["org"] or "",
            first_name=row["first_name"] or "", last_name=row["last_name"] or "",
            role=row["role"] or "", domain=row["domain"] or "", city=row["city"] or "Lisbon",
            linkedin_url=row["linkedin_url"] or "", phone=row["phone"] or "",
            email=row["email"] or "", email_status=row["email_status"],
            enrich_provider=row["enrich_provider"], enrich_cost=row["enrich_cost"],
            signal_snapshot=row["signal_snapshot"] or "",
            do_not_contact=bool(row["do_not_contact"]), revealed=bool(row["revealed"]),
            script=json.loads(row["script_json"] or "{}"))

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p)


def seed_leads(store: Store, leads: list[dict]) -> int:
    """Insert-or-ignore from fixtures. Returns how many were newly inserted."""
    inserted = 0
    now = utcnow()
    for row in leads:
        exists = store.db.execute("SELECT 1 FROM outreach_leads WHERE id=?",
                                  (row["id"],)).fetchone()
        if exists:
            continue
        store.db.execute(
            "INSERT INTO outreach_leads (id, avatar, org, first_name, last_name, role, domain, "
            "city, linkedin_url, phone, email, email_status, signal_snapshot, do_not_contact, "
            "revealed, script_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], row["avatar"], row.get("org", ""), row.get("first_name", ""),
             row.get("last_name", ""), row.get("role", ""), row.get("domain", ""),
             row.get("city", "Lisbon"), row.get("linkedin_url", ""), row.get("phone", ""),
             row.get("email", ""), row.get("email_status", "missing"),
             row.get("signal_snapshot", ""), int(row.get("do_not_contact", False)),
             int(row.get("revealed", False)), json.dumps(row.get("script", {})), now, now))
        inserted += 1
    return inserted


def seed_sources(store: Store, sources: list[dict]) -> None:
    for s in sources:
        store.db.execute(
            "INSERT INTO outreach_sources (id, name, status) VALUES (?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, status=excluded.status",
            (s["id"], s["name"], s.get("status", "approved")))


def seed_signals(store: Store, signals: list[dict]) -> None:
    for s in signals:
        store.db.execute(
            "INSERT INTO outreach_signals (id, avatar, keyword, label, source_id, enabled) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET keyword=excluded.keyword, "
            "label=excluded.label, source_id=excluded.source_id, enabled=excluded.enabled",
            (s["id"], s["avatar"], s["keyword"], s["label"], s["source_id"],
             int(s.get("enabled", True))))


def list_leads(store: Store, *, avatar: str | None = None,
              revealed: bool | None = None) -> list[Lead]:
    sql = "SELECT * FROM outreach_leads"
    where, params = [], []
    if avatar:
        where.append("avatar=?")
        params.append(avatar)
    if revealed is not None:
        where.append("revealed=?")
        params.append(int(revealed))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id ASC"
    return [Lead.from_row(r) for r in store.db.execute(sql, params).fetchall()]


def get_lead(store: Store, lead_id: str) -> Lead | None:
    row = store.db.execute("SELECT * FROM outreach_leads WHERE id=?", (lead_id,)).fetchone()
    return Lead.from_row(row) if row else None


def reveal_lead(store: Store, lead_id: str, signal_label: str) -> None:
    store.db.execute("UPDATE outreach_leads SET revealed=1, signal_snapshot=?, updated_at=? "
                     "WHERE id=?", (signal_label, utcnow(), lead_id))


def set_enrichment(store: Store, lead_id: str, *, email: str, email_status: str,
                   provider: str, cost: float) -> None:
    store.db.execute(
        "UPDATE outreach_leads SET email=?, email_status=?, enrich_provider=?, enrich_cost=?, "
        "updated_at=? WHERE id=?", (email, email_status, provider, cost, utcnow(), lead_id))


# --------------------------------------------------------------------------
# campaigns, steps, sent events, replies
# --------------------------------------------------------------------------
@dataclass
class Campaign:
    id: str
    name: str
    avatar: str
    status: str
    day: int
    lead_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: Any) -> "Campaign":
        return cls(id=row["id"], name=row["name"], avatar=row["avatar"], status=row["status"],
                   day=row["day"], lead_ids=json.loads(row["lead_ids_json"] or "[]"))


def create_campaign(store: Store, campaign_id: str, name: str, avatar: str) -> Campaign:
    now = utcnow()
    store.db.execute(
        "INSERT INTO outreach_campaigns (id, name, avatar, status, day, lead_ids_json, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (campaign_id, name, avatar, "draft", 0, "[]", now, now))
    return get_campaign(store, campaign_id)  # type: ignore[return-value]


def get_campaign(store: Store, campaign_id: str) -> Campaign | None:
    row = store.db.execute("SELECT * FROM outreach_campaigns WHERE id=?",
                           (campaign_id,)).fetchone()
    return Campaign.from_row(row) if row else None


def list_campaigns(store: Store) -> list[Campaign]:
    rows = store.db.execute("SELECT * FROM outreach_campaigns ORDER BY created_at ASC").fetchall()
    return [Campaign.from_row(r) for r in rows]


def update_campaign(store: Store, campaign_id: str, *, avatar: str | None = None,
                    name: str | None = None) -> None:
    """Restamp an existing campaign's avatar/name. `campaign generate` on the
    same id with a different --avatar must call this, or `campaign launch`'s
    pre-flight keeps checking the OLD avatar's lead pool forever - see
    SIMULATION.md Finding 6."""
    cols, params = [], []
    if avatar is not None:
        cols.append("avatar=?")
        params.append(avatar)
    if name is not None:
        cols.append("name=?")
        params.append(name)
    if not cols:
        return
    params += [utcnow(), campaign_id]
    store.db.execute(f"UPDATE outreach_campaigns SET {', '.join(cols)}, updated_at=? WHERE id=?",
                     params)


def set_campaign_steps(store: Store, campaign_id: str, steps: list[dict]) -> None:
    store.db.execute("DELETE FROM outreach_steps WHERE campaign_id=?", (campaign_id,))
    for i, s in enumerate(steps):
        store.db.execute(
            "INSERT INTO outreach_steps (id, campaign_id, idx, channel_kind, delay_days, "
            "condition, subject, body) VALUES (?,?,?,?,?,?,?,?)",
            (f"{campaign_id}-s{i}", campaign_id, i, s["channel_kind"], s["delay_days"],
             s["condition"], s.get("subject"), s["body"]))
    store.db.execute("UPDATE outreach_campaigns SET updated_at=? WHERE id=?",
                     (utcnow(), campaign_id))


def list_steps(store: Store, campaign_id: str) -> list[dict]:
    rows = store.db.execute(
        "SELECT * FROM outreach_steps WHERE campaign_id=? ORDER BY idx ASC",
        (campaign_id,)).fetchall()
    return [dict(r) for r in rows]


def launch_campaign(store: Store, campaign_id: str, lead_ids: list[str]) -> None:
    store.db.execute(
        "UPDATE outreach_campaigns SET status='running', day=0, lead_ids_json=?, updated_at=? "
        "WHERE id=?", (json.dumps(lead_ids), utcnow(), campaign_id))


def set_campaign_day(store: Store, campaign_id: str, day: int) -> None:
    store.db.execute("UPDATE outreach_campaigns SET day=?, updated_at=? WHERE id=?",
                     (int(day), utcnow(), campaign_id))


def record_step_sent(store: Store, campaign_id: str, lead_id: str, step_idx: int,
                     day: int, channel_kind: str) -> bool:
    """Insert-or-ignore. Returns True if this is a genuinely new send (the
    persisted-state model - see docs/how-it-works.md design decision 4)."""
    cur = store.db.execute(
        "INSERT OR IGNORE INTO outreach_events (campaign_id, lead_id, step_idx, day, "
        "channel_kind) VALUES (?,?,?,?,?)", (campaign_id, lead_id, step_idx, day, channel_kind))
    return cur.rowcount == 1


def steps_sent_for_lead(store: Store, campaign_id: str, lead_id: str) -> list[dict]:
    rows = store.db.execute(
        "SELECT * FROM outreach_events WHERE campaign_id=? AND lead_id=? ORDER BY step_idx ASC",
        (campaign_id, lead_id)).fetchall()
    return [dict(r) for r in rows]


def day_spend(store: Store, campaign_id: str, day: int, channel_kind: str) -> int:
    row = store.db.execute(
        "SELECT COUNT(*) c FROM outreach_events WHERE campaign_id=? AND day=? AND channel_kind=?",
        (campaign_id, day, channel_kind)).fetchone()
    return row["c"]


def record_reply(store: Store, lead_id: str, day: int, channel_kind: str, body: str) -> bool:
    exists = store.db.execute(
        "SELECT 1 FROM outreach_replies WHERE lead_id=?", (lead_id,)).fetchone()
    if exists:
        return False
    store.db.execute(
        "INSERT INTO outreach_replies (lead_id, day, channel_kind, body, handled, created_at) "
        "VALUES (?,?,?,?,0,?)", (lead_id, day, channel_kind, body, utcnow()))
    return True


def has_replied(store: Store, lead_id: str) -> bool:
    return store.db.execute("SELECT 1 FROM outreach_replies WHERE lead_id=?",
                            (lead_id,)).fetchone() is not None


def list_replies(store: Store, *, unhandled_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM outreach_replies"
    if unhandled_only:
        sql += " WHERE handled=0"
    sql += " ORDER BY day ASC"
    return [dict(r) for r in store.db.execute(sql).fetchall()]


def mark_reply_handled(store: Store, reply_id: int) -> None:
    store.db.execute("UPDATE outreach_replies SET handled=1 WHERE id=?", (reply_id,))
