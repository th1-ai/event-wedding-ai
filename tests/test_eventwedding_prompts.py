"""Tests for prompts/*.md themselves - see tests/conftest.py for why these
never read config/hotel.yaml or config/agent.yaml.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core.templates import load_prompt


def test_intake_task_section_keeps_its_open_events_list():
    """Regression for the BLOCKER core/templates.py fixed upstream: a prompt
    that nests `## Open events` inside its own `## Task` section must not have
    that content silently truncated by _section()'s heading search. See
    SIMULATION.md Finding 4."""
    prompt = load_prompt("intake")
    assert prompt.body != ""
    assert "Open events" in prompt.body
    assert "{{open_events}}" in prompt.body
    assert "read the Item block" in prompt.body.lower() or "Item" in prompt.body


def test_desk_note_prompt_never_hardcodes_a_currency():
    """Regression for SIMULATION.md Finding 8: the desk note's own
    instructions must use {{hotel_currency}}, never a literal EUR example
    that misleads the model on a non-euro property."""
    text = (REPO_ROOT / "prompts" / "desk_note.md").read_text(encoding="utf-8")
    assert "{{hotel_currency}}" in text
    assert "EUR" not in text
