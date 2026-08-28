#!/usr/bin/env python3
"""tools/doctor.py - is Event & Wedding AI configured and reachable right now?

    python3 tools/doctor.py
    make doctor

Runs the generic core.doctor checks (python, deps, config, .env, hotel
identity, mode, llm provider, every adapter, the store, knowledge) plus this
agent's own: the rate card and space inventory in config/agent.yaml, the
checklist templates, and prompts/. Exits 0 when everything passed, 1 when a
FAIL line needs fixing. Never a traceback.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.doctor import Check, FAIL, PASS, WARN, print_table, run_checks  # noqa: E402


def check_spaces_and_rates(settings: Settings) -> list[Check]:
    spaces = settings.agent_get("spaces", [])
    rates = settings.agent_get("rates", {})
    out = []
    if not spaces:
        out.append(Check("event spaces", FAIL, "no spaces configured in config/agent.yaml",
                         "Copy config/agent.example.yaml to config/agent.yaml and edit "
                         "spaces: to match your own venue."))
    else:
        out.append(Check("event spaces", PASS, f"{len(spaces)}: "
                         f"{', '.join(s.get('slug', '?') for s in spaces)}"))
    venue_rows = (rates.get("venue") or {}) if isinstance(rates, dict) else {}
    package_rows = (rates.get("package") or {}) if isinstance(rates, dict) else {}
    missing_venue = [s.get("slug") for s in spaces if s.get("slug") not in venue_rows]
    if missing_venue:
        out.append(Check("event rates", WARN, f"no venue rate for: {', '.join(missing_venue)}",
                         "Add a low/shoulder/peak row under rates.venue for each space."))
    elif not venue_rows or not package_rows:
        out.append(Check("event rates", FAIL, "rates.venue or rates.package is empty",
                         "config/agent.yaml needs both a venue and a package rate table."))
    else:
        out.append(Check("event rates", PASS,
                         f"{len(venue_rows)} venue row(s), {len(package_rows)} package row(s)"))
    return out


def check_checklist_templates(settings: Settings) -> Check:
    templates = settings.agent_get("checklist_templates", {})
    required = {"wedding", "conference", "offsite", "meeting", "generic"}
    missing = required - set(templates)
    if missing:
        return Check("checklist templates", FAIL, f"missing: {', '.join(sorted(missing))}",
                     "config/agent.yaml: checklist_templates: needs all five - "
                     "generic is what rules.checklist_templates: false falls back to.")
    return Check("checklist templates", PASS, f"{len(templates)} template(s)")


def check_prompts() -> Check:
    missing = [p for p in ("prompts/intake.md", "prompts/desk_note.md",
                           "prompts/schemas/intake.json", "prompts/schemas/desk_note.json")
              if not (REPO_ROOT / p).is_file()]
    if missing:
        return Check("prompts", FAIL, f"missing {', '.join(missing)}",
                     "These ship with the repo - restore them from git.")
    return Check("prompts", PASS, "intake.md + desk_note.md + schemas present")


def check_outreach(settings: Settings) -> Check:
    enabled = bool(settings.agent_get("subagents.event_outreach.enabled", False))
    if not enabled:
        return Check("event outreach (sub-agent)", WARN, "off by default",
                     "Turn on subagents.event_outreach.enabled in config/agent.yaml once "
                     "you want the calendar filled from the outside too - see "
                     "docs/sub-agents.md.")
    return Check("event outreach (sub-agent)", PASS, "enabled")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        checks = run_checks(None) + [Check("config", FAIL, str(exc),
                                           "Fix config/hotel.yaml or config/agent.yaml.")]
        return print_table(checks, title="Event & Wedding AI - doctor")

    checks = run_checks(settings)
    checks += check_spaces_and_rates(settings)
    checks.append(check_checklist_templates(settings))
    checks.append(check_prompts())
    checks.append(check_outreach(settings))
    return print_table(checks, title="Event & Wedding AI - doctor")


if __name__ == "__main__":
    raise SystemExit(main())
