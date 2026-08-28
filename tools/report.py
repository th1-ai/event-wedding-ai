#!/usr/bin/env python3
"""tools/report.py - what the agent did, and what it cost.

    python3 tools/report.py               # the full picture
    python3 tools/report.py --digest      # short form for a daily digest email/chat
    make report

Reads data/agent.db only - never fixtures, never the demo database. See
docs/benefits.md for what each number means and why, and
docs/how-it-works.md for the shape of the numbers underneath.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings  # noqa: E402
from core.review import queue_summary  # noqa: E402
from core.store import Store  # noqa: E402

import store_ext  # noqa: E402


def pipeline_value(store: Store) -> float:
    return sum(e.est_value for e in store_ext.list_events(store) if e.stage != "done")


def checklist_completion(store: Store) -> tuple[int, int]:
    done, total = 0, 0
    for e in store_ext.list_events(store):
        for c in store_ext.list_checklist(store, e.id):
            total += 1
            if c.status == "done":
                done += 1
    return done, total


def stage_counts(store: Store) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in store_ext.list_events(store):
        counts[e.stage] = counts.get(e.stage, 0) + 1
    return counts


def digest(store: Store, settings) -> str:
    q = queue_summary(store)
    done, total = checklist_completion(store)
    pct = round(100 * done / total) if total else 0
    return (f"Events waiting on you: {q['waiting_on_human']}. Pipeline value: "
           f"{settings.hotel.currency} {pipeline_value(store):,.0f}. Checklist completion: "
           f"{pct}% ({done}/{total}). Mode: {settings.mode}.")


def full_report(store: Store, settings) -> str:
    lines = []
    q = queue_summary(store)
    lines.append("Event & Wedding AI - report")
    lines.append("=" * 40)
    lines.append(f"Mode: {settings.mode}  |  Autopilot: {settings.agent_get('autopilot', False)}")
    lines.append("")
    lines.append(f"Pipeline value (non-done events): "
                f"{settings.hotel.currency} {pipeline_value(store):,.0f}")
    stages = stage_counts(store)
    lines.append("Events by stage: " + ", ".join(f"{k}={v}" for k, v in sorted(stages.items()))
                or "Events by stage: (none yet)")
    done, total = checklist_completion(store)
    pct = round(100 * done / total) if total else 0
    lines.append(f"Checklist completion: {pct}% ({done}/{total})")
    lines.append("")
    lines.append(f"Review queue: {q['waiting_on_human']} waiting on you, "
                 f"{q['in_send_queue']} queued to send, {q['sent']} sent so far.")
    lines.append("By status: " + ", ".join(f"{k}={v}" for k, v in sorted(q["by_status"].items()))
                or "By status: (none yet)")
    lines.append("")
    usage = store.usage_totals()
    lines.append(f"LLM calls: {usage['calls']}, "
                 f"{usage['input_tokens'] + usage['output_tokens']} tokens, "
                 f"${usage['cost_usd']:.4f} (only non-zero on claude-code/anthropic providers)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--digest", action="store_true", help="one line, for a daily digest")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    store_ext.ensure_schema(store)
    try:
        print(digest(store, settings) if args.digest else full_report(store, settings))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
