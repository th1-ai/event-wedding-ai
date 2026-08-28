# Workflow: shadow to live

Objective: decide, together with the hotel, whether Event & Wedding AI is
ready to send approved drafts on its own instead of only queuing them - and
make the change safely if so.

This is the hotel's decision, never the agent's. Do not suggest it until the
checklist below is genuinely true, and when you do raise it, say plainly
what changes.

## Checklist

- [ ] `make doctor` is clean (no `FAIL` lines). `warn` on `mode` is expected
      until you flip it.
- [ ] `config/hotel.yaml` has the real property name, address and contact
      details, and `config/agent.yaml: spaces:`/`rates:` are the property's
      own venue and prices, not the shipped samples.
- [ ] `knowledge/property.md`, `knowledge/event-pricing-policy.md` and
      `knowledge/signature.md` exist and are accurate.
- [ ] At least a few days of real `make run` passes (intake + the sweep)
      have gone through the review queue, not just the demo fixtures.
- [ ] The hotel has read and edited enough drafts to trust the reply and
      chase quality. A `counter_offer` never leaves this checklist behind -
      see below.
- [ ] The AI-disclosure line is in `knowledge/signature.md`
      (`docs/safety.md` has suggested wording and the EU AI Act Article 50
      context).
- [ ] A real mailbox is connected (`systems.email.adapter: imap` or
      `gmail`) and `make doctor` shows it healthy - going live on the
      `mock` adapter would only ever touch the fixtures.
- [ ] Run the go-live clear-out so nothing drafted during shadow goes out
      by surprise:
      ```bash
      python3 tools/review.py stale
      ```

## Making the change

1. Edit `config/hotel.yaml`:
   ```yaml
   mode: live
   ```
2. `review.require_approval_for` still lists `send_email` by default - it
   should. Going live means **approved drafts get sent**, not that anything
   starts sending unapproved. There is no config that changes this for a
   `counter_offer` - see step 4.
3. Run `make doctor` again to confirm.
4. Run one real pass and manually watch a send go through:
   ```bash
   make run ARGS="--limit 1"
   python3 tools/review.py list
   python3 tools/review.py approve <id>
   python3 tools/review.py send
   ```
5. Tell the hotel exactly what just changed: an approved draft now actually
   sends the next time someone (or a scheduled job) runs
   `python3 tools/review.py send` - still never automatic before that
   approval.

## If the hotel wants autopilot to actually auto-send

Setting `autopilot: true` in `config/agent.yaml` alone changes nothing about
what leaves the building - see `docs/how-it-works.md` "Modes". A routine
send (reply, chase, deposit reminder, site-visit offer) only really goes out
on its own once **both** of these are true:

```yaml
# config/hotel.yaml
mode: live
review:
  require_approval_for:
    - pms_write
    - payment
    - publish
    # send_email removed on purpose
```

This is a bigger step than the basic go-live above - every routine message
this agent drafts will now send itself, with no per-message review. Only
take it once the hotel has watched the review queue for a real stretch of
time and trusts the quality without checking every one. **A
`counter_offer` is unaffected either way** - `tools/engine.py` marks it
`gates=["pricing"]`, and that is checked in code before this list is even
consulted, so no config change here ever lets a price out the door on its
own.

## Going back to shadow

```yaml
mode: shadow
```
in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env` for one run. Either
stops every outbound action on the next pass, mid-schedule, with no other
change required - and if `send_email` was removed from
`review.require_approval_for`, put it back too.
