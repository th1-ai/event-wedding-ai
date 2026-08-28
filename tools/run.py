#!/usr/bin/env python3
"""tools/run.py - Event & Wedding AI's main loop: the planner sweep.

    python3 tools/run.py --once                 # the planner sweep (default)
    python3 tools/run.py --once --intake         # read mail, open/file events only
    python3 tools/run.py --watch
    python3 tools/run.py --once --dry-run
    python3 tools/run.py --once --provider mock

The sweep itself (tools/engine.py) is 100% deterministic - see
docs/how-it-works.md. This file's job is to seed the reference data, run the
sweep, turn every message-bearing action into a core.store.Item routed
through core.review, attempt the send when the action is not held, and write
the one cosmetic desk note. Exit codes: 0 ok, 3 waiting on an `interactive`
answer, 1 a real error.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_email, get_messaging  # noqa: E402
from core.adapters.base import AdapterError  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.llm import LLMError, LLMPendingInteractive, complete  # noqa: E402
from core.log import Run, get_logger, summary_line  # noqa: E402
from core.review import WriteBlocked  # noqa: E402
from core.store import Store  # noqa: E402
from core.templates import build_prompt  # noqa: E402

import engine  # noqa: E402
import intake  # noqa: E402
import store_ext  # noqa: E402

log = get_logger("run")
SCHEMAS_DIR = REPO_ROOT / "prompts" / "schemas"
DESK_NOTE_SCHEMA = json.loads((SCHEMAS_DIR / "desk_note.json").read_text(encoding="utf-8"))


def _seed_reference_data(store, settings) -> None:
    store_ext.ensure_schema(store)
    store_ext.seed_spaces(store, settings.agent_get("spaces", []))
    store_ext.seed_rates(store, settings.agent_get("rates", {}))


def _primary_contact(event: store_ext.Event) -> tuple[str, str]:
    for role_wanted in ("client", None):
        for s in event.stakeholders or []:
            if role_wanted is None or s.get("role") == role_wanted:
                if s.get("email"):
                    return s["email"], s.get("name", "the client")
    return "", "the client"


def _dispatch_message_action(settings, store, email_adapter, action: "engine.Action",
                             cursor: int) -> str:
    """Turn one message-bearing action into an item, file it onto the event
    thread, and attempt a send when it is not held. Returns one of:
    'skipped_existing' | 'pending' | 'auto_sent' | 'blocked' | 'dry_run_preview'.

    On --dry-run this computes and prints but writes nothing at all - not the
    item, not the thread entry, not even a 'new' row - mirroring
    core.store.next_sequence()'s own peek-only dry-run behaviour."""
    if settings.dry_run:
        exists = store.db.execute(
            "SELECT 1 FROM items WHERE kind=? AND unique_key=?",
            ("event_message", action.unique_key)).fetchone() is not None
        return "skipped_existing" if exists else "dry_run_preview"

    item, created = store.upsert_unique(
        "event_message", action.unique_key,
        payload={"event_id": action.event_id, "kind": action.kind, "title": action.title,
                 "detail": action.detail, **action.message})
    if not created:
        return "skipped_existing"

    store.set_fields(item.id, draft={"subject": action.message["subject"],
                                     "body": action.message["body"],
                                     "attachments": action.message.get("attachments", [])})
    store_ext.append_thread_message(store, action.event_id, {
        "from": "Hotel Aurora Events Team", "role": "ai", "ai": True,
        "body": action.message["body"], "at_offset": cursor, "held": action.held,
        "hold_reason": "pricing" if "pricing" in action.gates
                      else ("copilot" if action.held else None),
        "attachments": action.message.get("attachments", []),
        "re": action.message.get("re"), "item_id": item.id})

    if action.held:
        store.transition(item.id, "pending_review", actor="agent",
                         detail={"kind": action.kind, "title": action.title})
        return "pending"

    store.transition(item.id, "dispatched", actor="agent", detail={"kind": action.kind})
    event = store_ext.get_event(store, action.event_id)
    to_email = action.message.get("to") or (_primary_contact(event)[0] if event else "")
    try:
        result = email_adapter.send(
            to_email or "events@example.com", action.message["subject"],
            action.message["body"], attachments=action.message.get("attachments"), item=item)
    except WriteBlocked as exc:
        # autopilot decided this could go straight out, but mode: shadow (or
        # an approval gate still on send_email) says otherwise - fall back to
        # the review queue rather than pretend it sent. See
        # docs/how-it-works.md "Modes": shadow beats autopilot, always.
        store.transition(item.id, "pending_review", actor="agent",
                         detail={"blocked": str(exc)[:300]})
        return "blocked"
    store.set_fields(item.id, sent_message_id=result.get("message_id"))
    store.transition(item.id, "auto_sent", actor="agent", detail={"kind": action.kind})
    return "auto_sent"


def _notify_staff(settings, store, escalations: list["engine.Action"]) -> None:
    """A negotiation-band-off escalation has no message and no review-queue
    item at all (see docs/how-it-works.md), so it needs its own nudge or a
    human might never see it. Best-effort: a blocked (shadow) or failed
    notify never breaks the sweep - the escalation is still fully visible
    in data/logs/*.jsonl and in the desk note either way."""
    lines = [f"- {a.event_id}: {a.title} - {a.detail}" for a in escalations]
    text = "Event & Wedding AI needs you: negotiation band is off for " \
          f"{len(escalations)} event(s):\n" + "\n".join(lines)
    try:
        get_messaging(settings).notify_staff(text)
    except Exception as exc:  # noqa: BLE001 - a missed nudge must never fail the sweep
        log.warn("staff notify failed (escalation is still in the logs)", error=str(exc))


def desk_note(settings, store, summary: dict, headline: str, *,
             provider: str | None = None) -> str:
    """The one cosmetic LLM call in this agent. Its own route contract in the
    spec is 'always succeed, note: null on failure' - nothing on the sweep
    depends on it, so this is the single, narrow, documented place in this
    repo that catches LLMError broadly instead of letting it propagate to
    needs_human. LLMPendingInteractive is NOT an LLMError and still
    propagates normally - see docs/how-it-works.md design decisions and
    core/llm.py's own docstring on that distinction."""
    prompt = build_prompt("desk_note", settings=settings, item=summary, fixture_id="sweep-01")
    try:
        result = complete("desk_note", prompt, DESK_NOTE_SCHEMA, settings=settings,
                          provider=provider, store=store)
        return (result.data or {}).get("note") or headline
    except LLMPendingInteractive:
        raise
    except LLMError as exc:
        log.warn("desk note unavailable (cosmetic only, sweep unaffected)", error=str(exc))
        return headline


def one_sweep(settings, store, *, provider: str | None = None) -> tuple[int, dict]:
    stats = {"processed": 0, "drafted": 0, "sent": 0, "needs_human": 0, "skipped": 0}
    # Real outcomes, tallied as each message action is actually dispatched -
    # NOT engine.Action.held, which is only autopilot's pre-dispatch decision
    # to attempt a send. With autopilot on, an attempt can still be blocked by
    # mode: shadow (or a live-mode approval gate); the headline below must say
    # so, never "sent" - see SIMULATION.md Finding 5.
    outcomes = {"auto_sent": 0, "blocked": 0, "pending": 0}
    with Run("planner-sweep", settings, store) as run:
        _seed_reference_data(store, settings)
        reaped = store.reap_stuck_sending()
        if reaped:
            log.warn("reaped stuck sends", count=len(reaped))
        cursor = store_ext.get_day_cursor(store)
        result = engine.plan_sweep(store, settings)
        email = get_email(settings)

        for action in result.actions:
            if action.message is None:
                continue   # internal - already applied inside tools/engine.py
            outcome = _dispatch_message_action(settings, store, email, action, cursor)
            if outcome == "skipped_existing":
                continue
            stats["processed"] += 1
            stats["drafted"] += 1
            if outcome == "auto_sent":
                stats["sent"] += 1
            if outcome in outcomes:
                outcomes[outcome] += 1
            log.info("action", event_id=action.event_id, kind=action.kind,
                     outcome=outcome, held=action.held)

        for skip in result.skips:
            stats["skipped"] += 1

        escalations = [a for a in result.actions if a.kind == "escalation"]
        if escalations:
            _notify_staff(settings, store, escalations)

        headline = engine.summary_headline(result)
        if settings.dry_run:
            # Nothing was actually dispatched (core.review blocks every write
            # on --dry-run before it ever attempts one), so there is no real
            # outcome to report - fall back to what autopilot WOULD attempt.
            sent, held_blocked, awaiting = headline["auto"], 0, headline["held"]
        else:
            sent, held_blocked, awaiting = (outcomes["auto_sent"], outcomes["blocked"],
                                            outcomes["pending"])
        if held_blocked:
            block_label = ("held (shadow, approval kept)" if settings.mode == "shadow"
                          else "held (blocked, approval kept)")
            headline_text = (f"{headline['actions']} action(s) across {headline['events_touched']} "
                            f"event(s) - {sent} sent, {held_blocked} {block_label}, "
                            f"{awaiting} awaiting approval, {headline['skips']} left alone.")
        else:
            headline_text = (f"{headline['actions']} action(s) across {headline['events_touched']} "
                            f"event(s) - {sent} sent, {awaiting} awaiting "
                            f"approval, {headline['skips']} left alone.")
        # Only the TRUE, post-dispatch numbers go to the desk note - never
        # headline['auto']/['held'], which are autopilot's pre-dispatch plan
        # and can disagree with what mode: shadow actually allowed through.
        summary = {"headline": headline_text, "actions": headline["actions"],
                  "events_touched": headline["events_touched"],
                  "with_message": headline["with_message"], "skips": headline["skips"],
                  "sent": sent, "held_blocked": held_blocked, "awaiting_approval": awaiting,
                  "highlights": [a.title for a in result.actions if a.message][:12],
                  "skip_reasons": [f"{s.event_id}: {s.reason}" for s in result.skips]}
        try:
            note = desk_note(settings, store, summary, headline_text, provider=provider)
        except LLMPendingInteractive as exc:
            run.stats = dict(stats)
            print(str(exc))
            return 3, stats
        store_ext.record_sweep_run(store, run.id or "sweep", headline_text, result.thinking,
                                   headline, note)
        print(headline_text)
        print(f"Desk note: {note}")
        run.stats = dict(stats)
    return 0, stats


def one_intake_pass(settings, store, *, limit: int, provider: str | None = None) -> tuple[int, dict]:
    stats = {"processed": 0, "drafted": 0, "sent": 0, "needs_human": 0, "skipped": 0}
    with Run("intake", settings, store) as run:
        # Same seed one_sweep() does (SIMULATION.md Finding 4, the
        # contributing cause): without it, {{space_slugs}} is empty in the
        # intake prompt on a brand-new database, so the model has nothing
        # real to match a guest's own wording ("the ballroom") against.
        _seed_reference_data(store, settings)
        email = get_email(settings)
        messages = email.fetch_unread(limit=limit)
        seen = store.already_processed("email", [m.id for m in messages])
        for msg in messages:
            if msg.id in seen:
                stats["skipped"] += 1
                continue
            try:
                item, outcome = intake.process_message(settings, store, msg, provider=provider)
            except LLMPendingInteractive as exc:
                run.stats = dict(stats)
                print(str(exc))
                return 3, stats
            if outcome == "already_done":
                stats["skipped"] += 1
                continue
            stats["processed"] += 1
            if outcome == "new_event":
                stats["drafted"] += 1
            if outcome == "needs_human":
                stats["needs_human"] += 1
            print(f"  {msg.id}: \"{msg.subject}\" -> {outcome}")
        run.stats = dict(stats)
    return 0, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--once", action="store_true", help="run a single pass (default)")
    mode_group.add_argument("--watch", action="store_true",
                            help="keep running on the configured interval")
    parser.add_argument("--intake", action="store_true",
                        help="read mail and open/file events, instead of the planner sweep")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute everything, write nothing, even in live mode")
    parser.add_argument("--limit", type=int, default=50, help="max messages per intake pass")
    parser.add_argument("--provider", default=None, help="override llm.provider for this run")
    parser.add_argument("--poll-seconds", type=int, default=None,
                        help="override the --watch interval (default: agent.yaml or 900)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(provider=args.provider, dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    if not settings.dry_run and not settings.db_path().exists():
        # data/agent.db is disposable (see CLAUDE.md), but a silent empty
        # queue reads as "nothing is happening" rather than "your queue was
        # just wiped" - see SIMULATION.md Finding 11.
        print(f"Starting from an empty database: {settings.db_path()} did not exist yet.")
    store = Store(settings)
    store_ext.ensure_schema(store)
    runner = one_intake_pass if args.intake else one_sweep
    kwargs = {"limit": args.limit, "provider": args.provider} if args.intake \
        else {"provider": args.provider}
    try:
        if args.watch:
            poll_seconds = args.poll_seconds or int(settings.agent_get("poll_seconds", 900))
            while True:
                code, stats = runner(settings, store, **kwargs)
                print(summary_line(stats, settings.mode))
                if code != 0:
                    return code
                time.sleep(poll_seconds)
        code, stats = runner(settings, store, **kwargs)
        print(summary_line(stats, settings.mode))
        return code
    except AdapterError as exc:
        print(f"integration error: {exc}", file=sys.stderr)
        print("Run `make doctor` to see what is missing and how to fix it.", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
