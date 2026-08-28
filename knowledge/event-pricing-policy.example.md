# Event pricing policy - Hotel Aurora

<!--
Copy this to knowledge/event-pricing-policy.md. This is prose for a human
reading the file; the numbers the agent actually quotes from live in
config/agent.yaml (`rates:`, `spaces:`) - edit THOSE to change a price. This
file explains the policy behind the numbers so a new events coordinator
understands the reasoning, and it is loaded into the negotiation prompt
context so the agent can explain itself the same way a person would.
-->

## How we quote

- Venue hire is charged **per space-use entry**: a two-day conference in the
  ballroom is two day-hire lines, because our venue rate is "per day", not
  "per event".
- Package rates are **per person**, three season bands (low / shoulder /
  peak), set per event when it is qualified. We do not re-price an event
  automatically when a date changes - re-quote it by hand if the couple or
  company moves to a different season.
- The **Saturday wedding uplift** (see `config/agent.yaml:
  wedding_saturday_uplift`) applies only to weddings, only on the Grand
  Ballroom and the Garden Terrace, per qualifying space-use. It is not a
  general premium - do not add it to a conference or an offsite.

## The negotiation band

- We hold a **maximum 8% discount** the agent may put in writing on its own
  (`negotiation_band_pct`). A bigger ask is answered with 8% and the reason,
  never split down the middle and never rounded up "to be nice".
- Every counter is held for a human. Autopilot does not change this - a
  price never leaves the building without someone here approving it first.
- If a company or couple pushes past 8% and we genuinely want to go further,
  that is a human call made in the review queue, not something to raise the
  config number for. Raising `negotiation_band_pct` changes the ceiling for
  every future negotiation, not just this one.
- When we counter, we look for something to include instead of discounting
  further: comping AV, an extra hour, a tasting for two. The agent already
  does this when the requested extras look like AV/production - see
  `tools/engine.py: AV_RE`.

## What we never do

- We do not quote a price the agent invented. Every number traces back to
  `config/agent.yaml: rates:` and the season band on the event.
- We do not hold a date without protecting it - any alternate date offered
  in a reply gets a diary hold in the same sweep.
- We do not let a hold sit forever. It lapses after
  `hold_expiry_days` and the space is free again.
