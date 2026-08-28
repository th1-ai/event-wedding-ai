# Workflow: troubleshooting

Read the whole error before doing anything - every tool here is written to
say what broke and what to do about it. If you fix something not covered
below, add it here.

## `make doctor` shows a FAIL

Each `FAIL` line has a `->` fix hint right under it. Common ones:

- **`hotel identity`: name is still 'Hotel Aurora'.** Expected on a fresh
  clone. Edit `config/hotel.yaml`.
- **`event spaces`: no spaces configured.** `config/agent.yaml: spaces:` is
  empty or missing - copy from `config/agent.example.yaml` and edit it.
- **`event rates`: rates.venue or rates.package is empty.** Both tables need
  at least one row, and every space in `spaces:` needs a matching row under
  `rates.venue` (a `WARN`, not a `FAIL`, if only some are missing).
- **`checklist templates`: missing: ...** All five keys (`wedding`,
  `conference`, `offsite`, `meeting`, `generic`) must be present -
  `generic` is what a new enquiry falls back to when
  `rules.checklist_templates` is off.
- **`llm provider`: claude-code selected but `claude` is not on PATH.**
  Install Claude Code, or switch `llm.provider` to `interactive` or
  `anthropic` in `config/hotel.yaml`.
- **`llm provider`: ANTHROPIC_API_KEY is not set.** Add it to `.env`, or
  switch `llm.provider` to `claude-code` or `interactive`.
- **An adapter shows FAIL, not warn.** `universal`/`built` adapters fail
  loud when misconfigured (a `warn` is reserved for stubs). Read the
  `detail` column - it names the missing file or variable.

## `make demo` does not print `DEMO OK`

- Make sure `make setup` ran first (`.venv` must exist).
- `tools/demo.py` forces `llm.provider=mock`, seeds `fixtures/events/*.json`
  and `fixtures/hotel/space_calendar.json`, and reads
  `fixtures/inbound/*.json` - if you deleted or renamed any of those,
  restore them from git.
- Read the traceback if there is one; nothing here swallows an error on
  purpose, so a fixture problem shows up immediately.

## `make run` exits with code 3

Not an error. `llm.provider: interactive` parked a prompt (from intake's
classification, or the cosmetic desk note). Read `data/pending/*.prompt.md`,
write your answer to the matching `*.answer.json` (JSON only, matching the
schema shown, no prose, no code fence), and run the same command again.

## A `counter_offer` is missing, or a `reply` appeared where you expected one

The engine reads only the **newest** thread message per event, every sweep -
see `docs/how-it-works.md` "Guardrails". If a new message arrived after the
one that asked for a discount, the sweep now answers *that* message instead
- this is correct behaviour, not a bug, but it means a discount ask can be
"buried" by a later, unrelated message. Check
`python3 tools/review.py show <id>` for the event's full thread before
assuming something was missed.

## An item is stuck at `sending`

A process died between claiming an item and finishing the send.
`tools/run.py` calls `store.reap_stuck_sending()` on every sweep, which
moves anything stuck for more than 30 minutes to `failed` so you see it in
the queue instead of it vanishing. Use `python3 tools/review.py retry <id>`
once the cause is fixed.

## Event Outreach AI: `campaign launch` fails pre-flight

The failure list names exactly what is wrong - usually "no reachable,
non-DNC leads" (run `signals` then `enrich` first) or a connection note over
300 characters after personalisation (shorten
`tools/outreach.py:CONNECT_NOTES`). See `workflows/20-event-outreach.md`.

## Event Outreach AI: `tick` shows 0 sent every time

Check `mode` in `config/hotel.yaml` - shadow blocks every real email/
WhatsApp send from a launched campaign exactly like everywhere else in this
family. LinkedIn and Instagram steps are logged only, always, in every mode
- see `docs/integrations.md`.

## `python3 tools/*.py` says `ModuleNotFoundError: No module named 'core'`

You ran it with a Python that is not the repo's virtualenv, or from outside
the repo root. Use `make run` / `make doctor` / etc. (they call
`.venv/bin/python` for you), or run `.venv/bin/python tools/run.py` directly
from the repo root.

## Still stuck

`data/logs/*.jsonl` has every decision the agent made, in order, with a run
id. `python3 tools/review.py show <id>` has the full event trail for one
item. If neither explains it, that is a real bug - describe exactly what you
ran and what you expected, and ask.
