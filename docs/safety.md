# Guardrails and safety

This agent talks to your guests and touches your systems. Everything below is
built in, not optional, and this page explains what it does and what is left for
you to decide.

## The two modes

| Mode | What happens |
|---|---|
| `shadow` (default) | The agent reads, thinks, drafts and queues. It **never** sends a message and **never** writes to your PMS. Approving, editing or rejecting a draft records your decision (and teaches the agent) but sends nothing. At go-live, `python3 tools/review.py stale` clears that shadow-era queue so nothing old goes out by surprise. |
| `live` | Items you approved are really sent. Everything else still waits. |

`mode` lives in `config/hotel.yaml`. It is a global kill switch: flipping it back
to `shadow` stops every outbound action immediately, mid-schedule, with no other
change. `config/agent.yaml` can be stricter than `hotel.yaml`, never looser.

Two more brakes:

- `make run ARGS="--dry-run"` computes everything and writes nothing, even in
  live mode. Use it when you change a prompt.
- `review.require_approval_for` in `config/hotel.yaml` lists the actions that
  need a human even in live mode. The defaults are `send_email`, `send_message`,
  `pms_write`, `payment`, `publish`. Shortening that list is how you hand the
  agent more rope, one action at a time.

Every outbound action in the codebase goes through one function,
`core/review.py:assert_write_allowed`. There is no second path.

## The review queue

Nothing reaches a guest without passing through the queue.

```bash
make review                       # what is waiting
python3 tools/review.py show <id>  # the full draft and how it got there
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --body-file my-version.txt
python3 tools/review.py reject <id> --reason "wrong tone"
```

An item moves `new -> classified -> drafted -> pending_review` and then waits.
Only `tools/review.py` can write `approved`, `edited` or `rejected`; only
`tools/run.py` can write `sent`. A crash between "about to send" and "sent" is
picked up on the next pass and shown to you as failed rather than silently
retried.

**Your edits teach it.** When you rewrite a draft, the before and after are
stored. Over time that is what makes the drafts sound like your hotel instead of
like a machine.

## What the agent will not do

- Send anything while `mode: shadow`.
- Send an item a human has not approved, when the action needs approval.
- Take a payment, issue a refund, or move money. Payment adapters are read-only
  by design.
- Invent a fact that is not in `knowledge/` or in the data it was given. When it
  is not sure, it queues the item as `needs_human` instead of guessing.
- Argue. Complaints, refund requests, legal or medical topics, and anything that
  reads as distressed go straight to a person.

## Data handling

**What leaves your machine.** With `llm.provider: anthropic` or `claude-code`,
the prompt goes to Anthropic. That prompt contains the guest message and the
relevant property facts. With `llm.provider: mock` or `interactive`, nothing
leaves the machine at all.

**What is stored, and where.** Everything lives in `data/` inside this folder:
`agent.db` (SQLite), `logs/*.jsonl`, `exports/`. `data/` is gitignored. There is
no cloud service behind this repo and no telemetry.

**Card numbers are redacted on the way in.** Every inbound message passes through
`core/redact.py` before it is stored, logged or put into a prompt. A payment card
number is replaced with `[CARD REDACTED ****1234]`, and labelled CVC and expiry
values in the same message go with it. Detection requires a real card prefix and
a valid Luhn checksum, so booking references and door codes survive. IBANs are
masked the same way. Nothing you can do in config turns this off.

**Retention.** `privacy.retention_days` (default 365) is how long processed items
stay in the database. Deleting `data/agent.db` deletes everything the agent knows.

## GDPR, in practice

If you are in the EU or handle EU guests' data, the short version:

- **You are the controller.** This software runs on your machine, under your
  control, on your data. TH1 does not receive it.
- **Your model provider is a processor.** If you use the `anthropic` or
  `claude-code` provider, Anthropic processes guest data on your behalf. Check
  their data processing terms and record them in your processing register.
- **Purpose and minimisation.** The agent sees the message and the property facts
  it needs. Do not put staff phone numbers, card data or full guest histories in
  `knowledge/`.
- **Right to erasure.** A guest asking to be deleted means removing their rows
  from `data/agent.db` and any exported CSVs. Ask your Claude session:
  *"Delete every item in data/agent.db whose payload mentions this email address,
  and tell me how many rows you removed."*
- **Retention.** Set `privacy.retention_days` to what your own policy says, not
  to the default.

This is a practical summary, not legal advice.

## Telling guests they are talking to AI

The EU AI Act (Article 50) requires that a person is told when they are
interacting with an AI system, unless it is obvious. Whether it applies to you
depends on where you and your guests are, but it is good practice everywhere and
guests react well to it.

Add a line like this to the signature of any message the agent sends
(`knowledge/signature.md`):

> This reply was prepared with AI assistance and reviewed by our team. Reply to
> this message any time to reach a person directly.

If you run in live mode with auto-send for some intents, say so plainly:

> This reply was written by our AI assistant. If you would rather speak to a
> person, just say so and we will take over.

Keep the escape hatch in the sentence. A guest who wants a human should never
have to work out how to get one.

## Subscription or API: an honest note

Two ways to pay for the reasoning:

**Your Claude Code subscription** (`llm.provider: claude-code` or `interactive`).
Flat monthly cost, no per-message billing. This is genuinely the cheapest way to
run a small hotel's agent.

The caveat, plainly: a personal Pro or Max subscription is intended for
interactive use, and Anthropic's usage policy and rate limits apply to automated
use of it. A handful of scheduled runs a day is a normal way to work. Pointing
a busy inbox at it around the clock is not, and you will hit rate limits at the
worst moment. Read the terms and decide for yourself.

**The Anthropic API** (`llm.provider: anthropic`). Pay per token, no ambiguity
about automated use, proper rate limits, and usage you can attribute. This is
the right answer for production volume. `make report` shows what you are
spending.

Start on the subscription while you are learning what the agent does. Move to the
API when it becomes part of how the hotel runs.

## If something goes wrong

1. `mode: shadow` in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env`. Every
   outbound action stops on the next pass.
2. Remove the schedule (`crontab -e`, `launchctl unload`, or
   `systemctl disable --now <slug>.timer`).
3. `make doctor` to see what the agent thinks its state is.
4. `data/logs/*.jsonl` has every decision, with the run id, in order.

## Event & Wedding AI specifics

The generic rules above (shadow/live, the review queue, redaction, GDPR, the
AI-disclosure line) apply unchanged. This section adds what is specific to
running events and weddings.

### What this agent will never do

- **Sign a contract.** No signature path exists anywhere in this repo. The
  BEO (`tools/beo.py`) prints a blank signature line and a re-issue policy;
  nothing here ever counts as acceptance.
- **Discount beyond the approved band.** `negotiation_band_pct`
  (`config/agent.yaml`, 8% by default) is a hard ceiling. A bigger ask is
  answered with the band and the reason, never split, never rounded up "to
  be nice", and the counter is **always** queued for a human -
  `tools/engine.py` marks it `gates=["pricing"]` before autopilot is even
  consulted. No config flip changes this; it is checked in code.
- **Chase a vendor directly.** A chase is always addressed to the first
  non-vendor stakeholder on the event - see `tools/engine.py:VENDOR_RE`.
- **Offer a date it has not protected.** Every alternate date named in a
  reply gets a diary hold in the same sweep.
- **Quote off memory.** `tools/pricing.py:build_quote()` is the only place a
  price is computed; the BEO and the sendable price list both call it, so
  they cannot disagree with what a guest was told.
- **Send in a language the hotel cannot check.** `tools/intake.py` uses one
  model call per new thread specifically because free text needs judgement;
  if it cannot tell whether a message is a new enquiry or which event it
  belongs to, the item is queued `needs_human` with the reason recorded
  rather than guessed.

### Escalation, beyond "needs_human"

- With `rules.negotiation_band` off, a discount ask produces an
  `escalation` with **no drafted message at all** - the thread, the quote
  and the ask are left entirely for a person, and a staff notification is
  sent (`systems.messaging`) so it is not missed. See
  `docs/how-it-works.md` "Guardrails".
- An `event_intake` item at `needs_human` means the model could not
  confidently classify an inbound message. Read the original message
  yourself before doing anything with it.

### Autopilot is not a bigger autonomy dial

`autopilot: true` (`config/agent.yaml`) only changes whether a **routine**
action (reply, chase, deposit reminder, site-visit offer) is attempted as a
direct send instead of queued. It does not, and cannot, change what happens
to a `counter_offer` - see above - and it still goes through the exact same
guarded write as everything else, so `mode: shadow` blocks it exactly like
co-pilot. See `workflows/90-go-live.md` for what it actually takes to make
autopilot auto-send for real.

### Event Outreach AI ("The Rainmaker") - additional guardrails

Off by default (`subagents.event_outreach.enabled`). When it is on:

- **`stop_on_reply` halts a lead's entire sequence the moment they answer,
  on any channel.** Never turn this off outside a deliberate test.
- **`suppress_dnc` excludes a do-not-contact lead from every campaign and
  from enrichment** - never enriched, never billed, never touched.
- **`source_vetting` blocks a signal source that is not `approved` or
  `testing`**, and names it rather than silently dropping its leads.
- **Daily caps and `weekend_pause`** keep sending inside safe, published
  ranges for LinkedIn/email/WhatsApp/Instagram - see
  `workflows/20-event-outreach.md`.
- **Nothing in the outreach inbox sends itself.** `tools/outreach.py reply`
  is a human typing a message and this tool sending it - there is no
  AI-drafted reply in this template.
- **A booked reply hands off to the events pipeline, and never negotiates
  or quotes.** The Rainmaker's job ends the moment `hand_off()` creates the
  `event_bookings` row at stage `enquiry` - the planner sweep and a human
  take it from there.
