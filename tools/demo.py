#!/usr/bin/env python3
"""tools/demo.py - the whole loop on the bundled fixtures, zero credentials.

    make demo
    python3 tools/demo.py

Forces `llm.provider=mock` and `mode=shadow` regardless of config/hotel.yaml
(ARCHITECTURE.md section 1). Seeds five events already in flight
(fixtures/events/*.json) plus the space calendar (fixtures/hotel/
space_calendar.json), then runs intake on the three sample inbound emails
(fixtures/inbound/*.json - one opens a brand new wedding enquiry, one files a
reply onto an existing conference, one needs a human), then runs the planner
sweep once. It uses its own database (data/demo/demo.db) and never touches
data/agent.db. `tools/run.py`'s own one_intake_pass()/one_sweep() are called
directly, so the demo exercises exactly the same code the real loop runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings, sub_data_dir  # noqa: E402
from core.log import summary_line  # noqa: E402
from core.store import Store  # noqa: E402

import store_ext  # noqa: E402
from run import one_intake_pass, one_sweep  # noqa: E402


def _seed_calendar_and_events(store: Store) -> int:
    cal_path = REPO_ROOT / "fixtures" / "hotel" / "space_calendar.json"
    if cal_path.exists():
        for row in json.loads(cal_path.read_text(encoding="utf-8")):
            store_ext.seed_booked_space_day(store, row["space"], row["day_offset"],
                                            row.get("label", ""))
    events_dir = REPO_ROOT / "fixtures" / "events"
    count = 0
    for path in sorted(events_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        checklist = data.pop("checklist", [])
        event = store_ext.create_event(store, **data)
        for item in checklist:
            store_ext.insert_checklist_item(
                store, event.id, item["item_key"], item["label"], item["owner"],
                item["status"], item["due_offset"], item.get("note"))
        count += 1
    return count


def main() -> int:
    try:
        settings = load_settings(demo=True)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    demo_db = sub_data_dir("demo") / "demo.db"
    if demo_db.exists():
        demo_db.unlink()   # every `make demo` is a clean, repeatable run
    store = Store(settings, path=demo_db)
    store_ext.ensure_schema(store)
    store_ext.seed_spaces(store, settings.agent_get("spaces", []))
    store_ext.seed_rates(store, settings.agent_get("rates", {}))
    n_events = _seed_calendar_and_events(store)

    print(f"Event & Wedding AI demo - {n_events} event(s) already in flight from "
         f"fixtures/events/, plus 3 sample inbound emails from fixtures/inbound/\n")

    print("-- Intake: reading the inbox --")
    _, intake_stats = one_intake_pass(settings, store, limit=50, provider="mock")

    print("\n-- Planner sweep --")
    _, sweep_stats = one_sweep(settings, store, provider="mock")

    stats = {
        "processed": intake_stats["processed"] + sweep_stats["processed"],
        "drafted": intake_stats["drafted"] + sweep_stats["drafted"],
        "sent": sweep_stats["sent"],
        "needs_human": intake_stats["needs_human"] + sweep_stats["needs_human"],
    }
    print(f"\n{stats['needs_human']} item(s) need a person to look first "
         f"(an ambiguous inbound message always does - see docs/safety.md).")
    print("Nothing was sent: mode is shadow, and a counter-offer is held in every mode.")
    print("Next: `make review` to see the drafts, or read workflows/10-planner-sweep.md.\n")

    print(f"DEMO OK — {summary_line(stats, settings.mode)}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
