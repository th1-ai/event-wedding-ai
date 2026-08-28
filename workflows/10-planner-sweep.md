# Workflow: the planner sweep

Objective: run Event & Wedding AI's main job - read every open event, decide
what needs to happen next, queue every draft for a human, and read back a
plain-language desk note. See `docs/how-it-works.md` for the mermaid flow and
the branch-by-branch rules.

This workflow covers two loops that run on different schedules
(`config/agent.yaml: schedule:`): **intake** (reads mail, opens or files
events) and the **sweep** itself (decides what to do with what is already on
file). Run intake first if you have not run it recently.

## 1. Intake - read the inbox

```bash
python3 tools/run.py --once --intake
```

For each unread message this either opens a new event at stage `enquiry`, or
files it onto an existing event's thread, or - if the model genuinely cannot
tell which - queues it `needs_human`. Check those:

```bash
python3 tools/review.py list --status needs_human --kind event_intake
```

If `llm.provider` is `interactive`, this step will stop with exit code 3 and
park a prompt in `data/pending/`. Answer it (see `CLAUDE.md`) and re-run the
same command.

## 2. Run the sweep

```bash
make run                        # one pass
make run ARGS="--limit 5"       # not meaningful here - --limit only affects intake
make run ARGS="--dry-run"       # compute and print, write nothing
```

Read the printed headline and desk note. Every action is deterministic and
explainable - see `docs/how-it-works.md` for the exact rule behind each of:
a fresh-enquiry reply with alternate dates and a diary hold, a checklist
built from the right template, a site-visit offer, a negotiation counter
(always held), an answer to an open question, a chase on a silent client
item, and a deposit reminder. Events that are today, tomorrow, already past,
or already `done` are named in "left alone" and touched not at all.

## 3. Show what is waiting

```bash
make review
python3 tools/review.py show <id>
```

Summarise each one for the hotel in plain language: which event, what kind
of action, why now, and what it says. Never paste raw JSON at them. A
`counter_offer` item is always here, in co-pilot and in autopilot alike -
say so plainly when you show one.

## 4. Act on their decision

```bash
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --body-file <path>
python3 tools/review.py reject <id> --reason "<why>"
```

Read the draft back before approving. An edit's before/after is recorded,
even though there is no coach layer in this repo to learn from it yet.

## 5. Report

```bash
make report                # the full picture
python3 tools/report.py --digest   # one line, for a daily message
```

## What "autopilot" changes here

With `autopilot: true` in `config/agent.yaml`, a routine action (reply,
chase, deposit reminder, site-visit offer) is attempted as a real send
instead of queued for approval. That attempt still goes through the same
guard everything else does: in `mode: shadow` it is blocked and falls back
to the review queue exactly like co-pilot. It only really auto-sends once
`mode: live` **and** `send_email` has been removed from
`review.require_approval_for` in `config/hotel.yaml` - a second, deliberate
decision covered in `workflows/90-go-live.md`. A `counter_offer` is never
part of this - see `docs/safety.md`.
