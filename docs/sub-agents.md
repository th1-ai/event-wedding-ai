# Sub-agents in this repo

## Event Outreach AI ("The Rainmaker") - `tools/outreach.py`

**Off by default** (`config/agent.yaml: subagents.event_outreach.enabled:
false`). The planner sweep is fully useful without it - it works whatever
enquiries already exist. Turn this on when you want the calendar filled
from the outside too. See `workflows/20-event-outreach.md`.

**Adds:** the outbound half of the roster promise - *"Fills the event
calendar by going outbound. It watches signal sources for buying triggers
..., finds verified contacts ... for cents per lead, then runs multi-day
sequences ... all inside safe per-account daily caps."* This agent only
ever works enquiries that already exist; the Rainmaker creates them.

**Won't:** *"Hands warm replies to a human or the Event & Wedding AI to
close, stops every sequence the moment someone answers, and respects
do-not-contact and anti-spam rules."*

### Where the repo boundary was decided

The behavioural spec this repo is built from (`specs/event-outreach-ai.md`)
flags that the roster calls this agent's parent "Event & Wedding AI" while
its original demo page and engine actually sat inside a different agent's
surface (the CRM/Lead Nurture desk). This repo's brief settled that
question: **the Rainmaker folds into this repo**, per the roster's parent
link. There is a real, working hand-off in both directions here - see
`docs/how-it-works.md` design decisions 2-3.

### What is simplified relative to the spec, and why

- **No avatar-suggestion clustering.** The spec's demo can suggest a target
  avatar by clustering past clients. Cut for scope - avatars are
  hand-defined in `fixtures/outreach/leads.json`'s `avatar` field
  (`mice`, `wedding`, `agency`). Worth adding if you use this for real.
- **No LinkedIn-acceptance branching.** The spec models whether a
  connection request was actually accepted before a follow-up message
  fires (`if_accepted`/`if_not_accepted`, `accepts_after`). This template
  walks the sequence linearly instead, gated only by `if_no_reply` (stop on
  any reply) and the daily caps. The safety-critical mechanics - weekend
  pause, stop-on-reply, DNC suppression, safe caps, source vetting - are
  all real and all tested (`tests/test_eventwedding_outreach.py`).
- **Campaign state is persisted, not replayed.** The original demo
  recomputes the whole campaign from scratch on every render, because
  nothing in a demo really sends. This template's own open question said
  the quiet part out loud: *"a real deployment ... almost certainly must"*
  store what actually happened. `outreach_events` records every real send;
  `tick()` advances from there.
- **LinkedIn and Instagram steps are logged, not sent** - there is no core
  adapter for either family in this repo template. `docs/integrations.md`
  says so plainly.
- **Enrichment cost is simulated**, not a live Hunter.io/Findymail call -
  see `docs/how-it-works.md` design decision 6.
- **No AI-drafted inbox reply.** `tools/outreach.py reply` is a human
  typing a message by hand. The spec's demo has an "AI draft reply" button;
  cut here to keep the one genuinely automated part of this sub-agent
  (the outbound sequence) small and auditable. A hotel that wants drafted
  replies can point `tools/review.py`-style tooling at
  `outreach_store.list_replies()` - the data is all there.

### Launch is the approval gate, not per-message review

Unlike the main planner sweep, a launched campaign's routine sends are
**not** queued item-by-item for a human to approve. `campaign launch`
itself is the human decision (mirroring the source demo's own "Launch
campaign" button), gated by a five-point pre-flight check. After launch,
`tick` sends within the configured safe caps automatically, in `mode: live`,
exactly the way the roster promise describes automated outreach working.
`mode: shadow` still blocks every real send regardless. This is a
deliberate difference in shape from the main loop, not an oversight - see
`docs/how-it-works.md`.

### Data model

`outreach_sources`, `outreach_signals`, `outreach_leads`,
`outreach_campaigns`, `outreach_steps`, `outreach_events` (what actually
sent, insert-or-ignore keyed on campaign/lead/step), `outreach_replies`.
Kept in their own module, `tools/outreach_store.py`, separate from the
events ledger in `tools/store_ext.py` - see that file's own docstring for
why.

### Try it

```bash
python3 tools/outreach.py seed-demo
python3 tools/outreach.py signals
python3 tools/outreach.py enrich
python3 tools/outreach.py campaign generate camp-01 --avatar mice
python3 tools/outreach.py campaign launch camp-01
python3 tools/outreach.py tick --days 8
python3 tools/outreach.py funnel camp-01
```

No credentials needed for any of this - `fixtures/outreach/*.json` is
enough to see every mechanic, including a scripted reply that hands off a
new event to the planner sweep.
