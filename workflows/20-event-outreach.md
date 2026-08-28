# Workflow: Event Outreach AI ("The Rainmaker")

Objective: fill the calendar from the outside instead of only waiting for
enquiries. Off by default - the planner sweep (`workflows/10-planner-sweep.md`)
is fully useful without this. Turn it on when you actually want outbound
prospecting running. See `docs/sub-agents.md` for what this adds, what it
does not do, and how it differs in shape from the main loop.

## Turn it on

```yaml
# config/agent.yaml
subagents:
  event_outreach:
    enabled: true
```

Then:
```bash
make doctor
```
"event outreach (sub-agent)" should now say `ok enabled`.

## Try it on sample data first

```bash
python3 tools/outreach.py seed-demo
```
Loads `fixtures/outreach/*.json` - three signal-source-backed leads, one
pre-enriched lead, one wedding lead, one do-not-contact lead - so you can run
every step below before connecting a real signal feed.

## 1. Find leads

```bash
python3 tools/outreach.py signals
```
Reveals every lead whose signal snapshot matches an enabled signal from a
vetted source. A source still `pending` blocks its leads and is named, not
silently dropped - see `docs/integrations.md` for connecting a real one
(company registry filings, a conference exhibitor list, LinkedIn job posts).

## 2. Find contacts

```bash
python3 tools/outreach.py enrich
```
Costs are simulated (no live Hunter.io/Findymail integration ships here -
see `docs/how-it-works.md` design decision 6), but the arithmetic is honest:
a miss is still billed, a do-not-contact lead is never enriched and never
billed.

## 3. Build and launch a sequence

```bash
python3 tools/outreach.py campaign generate camp-01 --avatar mice
python3 tools/outreach.py campaign launch camp-01
```
`--avatar` is one of `mice` (offsites/conferences), `wedding`, or `agency`.
Launch is the pre-flight gate: it fails loudly if there is no reachable
audience, no sequence, or a LinkedIn connection note over 300 characters
after personalisation - fix what it names and try again. **Launch is itself
the human approval** for this sub-agent; routine sends do not queue for
per-message review the way the main loop's do (see docs/sub-agents.md for
why that is the right shape here).

## 4. Advance the clock

```bash
python3 tools/outreach.py tick --days 1
```
Sends whatever is due today, inside the configured daily caps
(`config/agent.yaml: subagents.event_outreach.daily_caps`), skipping
weekends (`weekend_pause`) and do-not-contact leads (`suppress_dnc`). The
moment any lead replies on any channel, that lead's sequence stops dead -
`stop_on_reply` - and the reply lands in the inbox. `mode: shadow` still
blocks every real email/WhatsApp send; LinkedIn and Instagram steps have no
adapter in this family at all and are logged only, never really sent - see
`docs/integrations.md`.

## 5. Work the inbox

```bash
python3 tools/outreach.py inbox
python3 tools/outreach.py reply <lead_id> --body-file reply.txt
```
Nothing here drafts or sends itself - `reply` is a human typing a message
and this tool sending it, still through the same guarded write as
everything else in this family. When a lead's scripted reply says
`wants_meeting`, `tick` hands it straight to the events pipeline:

```bash
python3 tools/outreach.py funnel camp-01
```
prints the per-step count so you can see where leads are queued by caps,
withdrawn, or waiting.

## The hand-off

A booked reply becomes a real `event_bookings` row at stage `enquiry`,
picked up by the next planner sweep exactly like an inbound enquiry - see
`docs/how-it-works.md` design decision 3. Check it landed:

```bash
python3 tools/review.py list
```
will show nothing new yet (Branch A has not run); the row itself is visible
via `python3 tools/report.py`.
