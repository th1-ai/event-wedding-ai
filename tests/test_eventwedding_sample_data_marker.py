"""A fresh clone must never let shipped fixtures pass for the hotel's own data.

Every `config/*.example.yaml` ships with `systems.email.adapter: mock`, so a
hotel that runs the real loop (not `make demo`) before connecting a mailbox
reads `fixtures/inbound/*.json` and nothing else. `core.store.Store.upsert_item`
tags those items `_sample: True` via `core.adapters.is_sample_source` - this
repo does not re-implement the tagging, it only consumes it - and
`tools/review.py` must say so in both `list` and `show`, where a human is one
keystroke from approving the draft.

Named `test_eventwedding_*` on purpose: `tests/conftest.py` only isolates
AGENT_CONFIG_DIR for that prefix, and these tests must read the shipped
example config, never the hotel's own filled-in `config/agent.yaml`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings
from core.store import Store
from tools.review import cmd_list, cmd_show

PAYLOAD = {"event_id": "evt-901", "kind": "reply", "title": "Wedding enquiry, 120 pax",
           "from_email": "sample@example.com", "subject": "Our wedding, next June"}


def _sample_item(tmp_path):
    """One real (non-demo) intake item on the shipped `mock` email default."""
    settings = load_settings()
    assert settings.systems.email.adapter == "mock"   # the shipped default
    assert settings.demo is False                     # the real path, not `make demo`
    store = Store(settings, path=tmp_path / "test.db")
    item = store.upsert_item("email", "email-99", kind="event_intake", payload=PAYLOAD)
    # `new` is not a state a human is asked about; needs_human is - that is the
    # queue tools/review.py lists (core.review.ACTIONABLE_STATES).
    item = store.transition(item.id, "needs_human", actor="agent")
    return store, item


def test_a_real_pass_on_the_mock_default_tags_its_item_sample(tmp_path):
    store, item = _sample_item(tmp_path)
    store.close()
    assert item.is_sample is True
    assert item.payload.get("_sample") is True


def test_review_list_marks_the_sample_item_and_says_why(tmp_path, capsys):
    store, item = _sample_item(tmp_path)
    capsys.readouterr()
    cmd_list(store, SimpleNamespace(status=None, kind=None, limit=50))
    store.close()
    out = capsys.readouterr().out
    assert "[SAMPLE DATA]" in out
    assert "systems.email.adapter is 'mock'" in out


def test_review_show_warns_before_the_json(tmp_path, capsys):
    store, item = _sample_item(tmp_path)
    capsys.readouterr()
    cmd_show(store, SimpleNamespace(id=item.id))
    store.close()
    out = capsys.readouterr().out
    assert out.startswith("[SAMPLE DATA]")
