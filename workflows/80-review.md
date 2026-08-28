# Workflow: working the review queue

Objective: turn a queued draft into a decision - approve, edit, or reject -
and, once approved, actually send it.

Nothing reaches a couple, a company, or a vendor without going through this.
`mode: shadow` blocks every guarded write for everything except an item you
have approved or edited, and even then a `counter_offer` needs approval
regardless of mode or autopilot - see `docs/safety.md`.

## Steps

1. **See what is waiting.**
   ```bash
   make review
   ```
   Each line shows the item id, its status (`pending_review` or
   `needs_human`), the event id and action kind, and a short title.

2. **Read one in full.**
   ```bash
   python3 tools/review.py show <id>
   ```
   Prints the draft (subject, body, attachments), the payload (which event,
   which action kind, and for a chase, who it is addressed to and why), and
   the full event history for that item. Summarise it in plain language for
   the hotel - which event, what kind of message, why now - never paste the
   raw JSON at them.

3. **Decide.**
   ```bash
   python3 tools/review.py approve <id>
   python3 tools/review.py edit <id> --body-file my-version.txt [--subject "New subject"]
   python3 tools/review.py reject <id> --reason "wrong tone"
   ```
   `edit` records the before/after pair as a `learnings` row (there is no
   coach layer in this repo to act on it yet - see `docs/how-it-works.md`).

4. **Send what was approved.**
   ```bash
   python3 tools/review.py send
   ```
   Claims everything `approved`/`edited`, sends it to the event's primary
   contact (or, for a chase, to the specific stakeholder `tools/engine.py`
   picked - never a vendor), and flips the matching thread entry's `held`
   flag off so the event sheet stays accurate. In `mode: shadow` this only
   works for an item you have approved (the one case shadow lets through);
   nothing else can ever be sent while shadow is on.

5. **A failed send.** `send` marks the item `failed` with the error attached.
   ```bash
   python3 tools/review.py retry <id>
   ```
   re-queues it once the cause is fixed (usually a mailbox credential -
   `make doctor` says which).

## Rules

- Only `tools/review.py` writes `approved` / `edited` / `rejected`.
- A `counter_offer` is always here, whatever the mode and whatever autopilot
  says. Read it in full - it names the band, the reason, and the exact
  before/after price - before approving.
- An `event_intake` item at `needs_human` means intake could not tell which
  event a message belonged to, or whether it was a new enquiry at all. Read
  the original message and either open the event yourself or file the
  message manually, then move on - there is nothing to approve/edit/reject
  here, just a judgement call.
- Confirm with the hotel before sending anything, even an approved item, the
  first few times. `workflows/90-go-live.md` covers when to stop doing that.
