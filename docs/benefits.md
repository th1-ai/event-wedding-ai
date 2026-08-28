# Measuring the benefit

## The promise, verbatim

**Does.** Owns the whole event once someone wants to hold it here - weddings,
conferences, offsites, board meetings. It runs the long sales cycle in one
thread with every stakeholder (the couple, the planner, procurement,
vendors), works from a per-event-type checklist so nothing gets forgotten,
reads the function-space calendar and the seasonal price table before it
quotes, drafts and sends the proposal and the BEO, organises the site visit,
chases whoever went quiet, and negotiates inside the band you set. Runs in
co-pilot (every send waits for your OK) or autopilot (routine sends go out
on their own - pricing still comes to you).

**Won't.** Won't sign contracts, won't discount beyond the approved band,
and any custom price is held for human sign-off - in autopilot too.

**Why.** Events are huge revenue but enormous manual effort with slow
follow-up; most leads go cold.

**Output.** Faster proposals + relentless follow-up, and a checklist-driven
plan for every event type from first enquiry to the run-sheet; recover a
chunk of the ~50% of event inquiries that go stale.

**ROI.** +25% Event leads recovered (revenue).

## What to actually measure

| Metric | Where it comes from | What it tells you |
|---|---|---|
| Pipeline value | `tools/report.py`: sum of `est_value` across non-`done` events | The size of what is in flight right now. |
| Checklist completion | `tools/report.py`: done / total across every open event's checklist | Whether events are actually moving through their plan, not just sitting. |
| Chases sent vs. events with a `waiting` client item | `data/logs/*.jsonl`, filter `action=="chase"` | How much of the "relentless follow-up" promise is real work, not narrative - see the honest caveat below. |
| Review queue: waiting / edit rate | `make review`, `learnings` table | How much the drafts are trusted as-is vs. rewritten. Below ~10% edit rate is a reasonable bar before considering autopilot for routine sends. |
| Time from enquiry to first reply | Not measured yet - see caveat | The number the roster's "faster proposals" claim is really about. |
| Event Outreach: leads revealed / enriched / replied / handed off | `python3 tools/outreach.py funnel <id>` | Whether outbound prospecting is actually filling the pipeline, if you have turned it on. |

## Honest caveats

- **"Recover ~50% of event inquiries that go stale" rests on one narrow
  mechanism.** The only staleness rule this agent has is
  `rules.follow_up_5d`, and it only ever looks at checklist items with
  `owner: client` and `status: waiting` that already have a matching
  message to chase against. A fresh enquiry nobody has replied to, or a
  `todo` item nobody has started, does not age and is not chased. The
  `+25%` figure is a roster-level target, not something this template
  measures or guarantees on its own.
- **"Median first response" is not measured.** Nothing in this repo times
  how long a reply sat drafted before a human approved it. If that number
  matters to you, it is a small addition to `core.log` worth asking Claude
  to build.
- **A proposal document exists here that the demo this is ported from never
  built** (see `docs/how-it-works.md` design decision 9) - `does` promises
  a proposal and a BEO; only the BEO shipped in the source demo.
- **Checklist items only self-complete where the sweep itself does the
  work** (`qualify`, on `checklist_build`). Everything else needs a human
  action in the review queue or a real reply moving it forward - there is
  no invented "mark done automatically" beyond that.
- **Event Outreach AI's pipeline number has no quality score.** A hand-off
  is a real `event_bookings` row, but nothing here scores whether that lead
  was actually worth having - see `docs/sub-agents.md`.

## How this compares to doing it by hand

The honest case for this agent is not "it closes deals a person would have
missed" - it is that a checklist never gets forgotten, a diary hold never
gets left un-protected, a discount never gets given past the approved band
by an over-eager or overworked coordinator, and a client who has gone quiet
gets one polite nudge on a fixed schedule instead of whenever someone
remembers. Those are the failure modes this template actually closes.
