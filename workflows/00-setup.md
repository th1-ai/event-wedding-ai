# Workflow: first-run setup

Objective: get Event & Wedding AI from a fresh clone to a working demo, then
to real config, in one sitting.

## Steps

1. **Install and check.**
   ```bash
   make setup
   make doctor
   ```
   `make setup` creates the virtualenv, installs `requirements.txt`, and
   copies `.env.example` -> `.env` and every `config/*.example.yaml` ->
   `config/*.yaml` (only if those files do not exist yet). `make doctor`
   will show a `FAIL` on "hotel identity" right after setup - expected, it
   means the property name is still the shipped placeholder ("Hotel
   Aurora"). Everything else should be `ok` or `warn`.

2. **Run the demo.** No credentials needed.
   ```bash
   make demo
   ```
   Expect to see the inbox read (one new wedding enquiry opened, one reply
   filed onto an existing dinner event, one flagged `needs_human`), then a
   planner sweep across five events already in flight, ending
   `DEMO OK — 9 items processed, 7 drafted, 0 sent (shadow)`. If you do not
   see that, stop and read `workflows/99-troubleshooting.md` before going
   further.

3. **Fill in the property.**
   ```bash
   cp knowledge/property.example.md            knowledge/property.md
   cp knowledge/faq.example.md                 knowledge/faq.md
   cp knowledge/signature.example.md           knowledge/signature.md
   cp knowledge/event-pricing-policy.example.md knowledge/event-pricing-policy.md
   cp knowledge/event-spaces.example.md        knowledge/event-spaces.md
   ```
   Edit `config/hotel.yaml` (name, address, contact, languages) and replace
   every Hotel Aurora fact with the real property's. See `knowledge/README.md`.
   Also, optionally but worth doing before you go live:
   ```bash
   cp knowledge/disclosure.example.md knowledge/disclosure.md
   ```
   and put the EU AI Act disclosure line in your own guest language(s) -
   every chat message the agent sends carries a generic English version of
   this line automatically even if you skip this step, but it will read oddly
   to a client who does not read English (`docs/safety.md`).

4. **Set your own venue and rate card.** `config/agent.yaml: spaces:` and
   `rates:` are the actual data the agent quotes from - not a fixture, the
   thing itself. Replace the five sample spaces with your own function
   spaces and capacities, and the sample rate card with your own low /
   shoulder / peak prices. `docs/integrations.md` and
   `knowledge/event-spaces.md` cover a restaurant-shaped install (a private
   dining room and a whole-venue row instead of a ballroom).

5. **Check the seven rules and the checklist templates.**
   `config/agent.yaml: rules:` are on by default - read
   `docs/safety.md` for what each one proves when it is off.
   `checklist_templates:` needs all five keys (wedding, conference, offsite,
   meeting, generic); `make doctor` checks this.

6. **Pick how the agent thinks.** `config/hotel.yaml`'s `llm.provider` starts
   as `interactive` - it asks you, in this Claude Code session, instead of
   calling a model, for two things only: intake's new-enquiry-vs-reply
   classification and the cosmetic desk note. Neither the planner sweep nor
   the negotiation, chase or deposit logic ever calls a model - see
   `docs/how-it-works.md`.

7. **Decide on co-pilot or autopilot, and on Event Outreach AI.**
   `config/agent.yaml: autopilot: false` (co-pilot) is the shipped default -
   every send waits for you. `subagents.event_outreach.enabled: false` is
   also the shipped default - the planner sweep is fully useful without it.
   Leave both off for now; `workflows/90-go-live.md` and
   `workflows/20-event-outreach.md` cover turning them on later.

8. **Connect a real mailbox (optional for now).** `systems.email.adapter` in
   `config/hotel.yaml` starts as `mock`, which only ever sees
   `fixtures/inbound/*.json`. `docs/integrations.md` covers `imap` (works
   with any provider) and `gmail`. Run `make doctor` after changing it.

9. **Re-check.**
   ```bash
   make doctor
   ```
   Once the property name is real, `spaces`/`rates` are your own, and
   `knowledge/property.md` exists, the failing lines turn green. Move on to
   `workflows/10-planner-sweep.md` to run the loop for real.
