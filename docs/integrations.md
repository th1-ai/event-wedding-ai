# Connecting your systems

Every connector in this repo is one of three things, and the table says which.
We will not tell you an integration exists when it does not.

| Badge | Means |
|---|---|
| **built** | Written against the real API and tested against it. |
| **universal** | Works with any system through a common protocol: IMAP/SMTP, CSV, a webhook. |
| **stub** | Interface only. Calling it raises a clear error with a recipe for adding it. |
| **simulated** | Not a live third-party call at all - the arithmetic and the rules are real, the network call is not. Used only where the brief names no system to port. |

Check what is actually working on your machine at any time:

```bash
make doctor
```

## What this agent actually uses

| System | Adapter family | Status | Used for |
|---|---|---|---|
| Email | `systems.email.adapter` | universal (`mock`/`imap`), built (`gmail`) | Every event thread: replies, checklist/site-visit offers, negotiation counters, chases, deposit reminders. |
| Messaging | `systems.messaging.adapter` | universal (`mock`/`webhook`), built (`unipile`) | A staff nudge when a negotiation escalates with `rules.negotiation_band` off (nothing else uses this in the main loop - see below). Event Outreach AI also uses it for WhatsApp steps. |
| Sheets | `systems.sheets.adapter` | universal (`csv`), built (`google`) | Not wired into a tool yet in this template - `tools/report.py` prints to the terminal. Available for a future CSV/Sheet export of the pipeline. |
| PMS | `systems.pms.adapter` | universal (`mock`/`csv`), built (`cloudbeds`) | **Not used by the core loop.** The function-space diary and rate card are this agent's own tables (`event_space_days`, `event_rates` in `data/agent.db`), seeded from `config/agent.yaml`, not a PMS call - see `docs/how-it-works.md` design decision 7. Kept configured so a future room-block write (checklist item `room-block`) has somewhere to go. |
| POS, Accounting, Reviews, Calendar, Payments, Procurement, Locks, Courier | stub families | stub | Not used by this agent. |

**LinkedIn and Instagram have no adapter in this family at all** - not built,
not stubbed by name. Event Outreach AI's LinkedIn/Instagram steps
(visit, like, connect, message, withdraw) are logged for the funnel table
only; nothing is really sent. See `docs/sub-agents.md` and
`docs/how-it-works.md` design decision 5.

**Hunter.io and Findymail (contact enrichment) are simulated**, not a live
API call - see `docs/how-it-works.md` design decision 6. `tools/outreach.py:
enrich_leads()` reproduces the real cost arithmetic (per-provider EUR/lead,
billed even on a miss) so the numbers are honest about what a real
integration would cost.

## Email - `systems.email.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Reads `fixtures/inbound/*.json`. What `make demo` and `make test` use. |
| `imap` | universal | mailbox + app password | Any provider. **Start here.** |
| `gmail` | built | Google OAuth desktop client | Adds Gmail labels and threads. |

**`imap`.** In `.env`:

```
EMAIL_ADDRESS=events@example.com
EMAIL_PASSWORD=            # an APP password, never your login password
IMAP_HOST=imap.example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587              # 587 STARTTLS, 465 implicit TLS
```

Replies carry `In-Reply-To` and `References`, so they land inside the
existing conversation rather than starting a new one. Test:

```bash
make doctor
```

**`gmail`.** Google Cloud Console: enable the Gmail API, configure the
consent screen, create an OAuth client of type **Desktop app**, download the
JSON to `credentials.json`. Then
`pip install google-api-python-client google-auth-oauthlib` and run
`make doctor`; a browser opens once and writes `token.json`.

## Messaging - `systems.messaging.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Logs to `data/exports/sent_messages.jsonl`. |
| `unipile` | built | your own UniPile account | WhatsApp on your own number. |
| `webhook` | universal | any URL | POST to Zapier, Make, n8n, or your own endpoint. |

**`unipile`.** You create the account, you connect your own number by QR
code, you own the credentials: `UNIPILE_DSN`, `UNIPILE_API_KEY`,
`UNIPILE_ACCOUNT_ID`, `UNIPILE_STAFF_CHAT_ID`.

## Sheets - `systems.sheets.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `csv` | universal | nothing | Writes `data/exports/<sheet>.csv`. |
| `google` | built | service account JSON | A live shared spreadsheet. |

Not called by any tool in this template yet - ask Claude to wire
`core.adapters.get_sheets()` into `tools/report.py` if you want a scheduled
pipeline export.

## PMS - `systems.pms.adapter` (optional, not used by the core loop)

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Fixture reservations, unused here. |
| `csv` | universal | a CSV export | Works with any PMS. |
| `cloudbeds` | built | OAuth app + refresh token | Live reads and writes. |
| `cli` | universal | a JSON-speaking CLI | Advanced. |

If you want the `room-block` checklist item to actually push a room block
into your PMS instead of sitting as a checklist tick, ask Claude:

> Read `docs/integrations.md` and `core/adapters/base.py`. Wire
> `core.adapters.get_pms()` into `tools/engine.py`'s `branch_enquiry` so the
> `room-block` checklist item, once marked done, calls
> `pms.update_reservation()` or `pms.add_note()` with the block details.

## Implement your own

<a id="implement-your-own"></a>

The interface is small on purpose. Open `claude` in this folder and paste:

> Read `docs/integrations.md#implement-your-own` and `core/adapters/base.py`.
> I need a **<system>** adapter for **<your provider>**. Its API docs are at
> **<url>** and I have credentials in `.env` as `<VAR names>`. Copy the
> closest existing adapter as the shape, implement `ping`, `capabilities`
> and the read methods first, register it in `core/adapters/__init__.py`,
> and stop before the write methods so I can check the reads with
> `make doctor`.

### The five steps

**1. Copy the closest existing adapter** - `email_imap.py` for a mailbox,
`messaging_webhook.py` for a chat channel, `pms_csv.py` for a PMS.

**2. Implement `ping()` and `capabilities()` first** - `make doctor` reads
both, so getting these right gives the rest of the work a feedback loop.

**3. Implement the reads.** Map the vendor's fields onto the dataclasses in
`core/adapters/base.py`. Dates are ISO `YYYY-MM-DD`. Money is a float in the
hotel's currency.

**4. Implement the writes, each with the guard:**

```python
from core.adapters.base import guarded_write

@guarded_write("send_email")
def send(self, to, subject, body_md, **kwargs) -> dict:
    ...
```

Not optional - without it your adapter can write while the agent is in
shadow mode, which defeats the whole safety model.

**5. Register it** in `core/adapters/__init__.py`'s `REGISTRY`, set the
adapter name in `config/hotel.yaml`, and run `make doctor`.

### Rules that matter

- **`ping()` never raises.** Returns `HealthCheck(ok=False, ...)` with a hint.
- **Every write is decorated.** No exceptions.
- **Never log a credential.** `core/log.py` masks anything whose key looks
  like a secret, but do not rely on it.
- **Redact on ingestion.** Guest-written text goes through
  `core.redact.redact()` before it is stored or shown to a model - the
  mock/imap/gmail adapters already do this for you.
- **Write a test.** Copy `tests/test_core_adapters_mock_csv.py`. No network:
  feed your parser a fixture, check the dataclass that comes out.

### `core/` is shared

`core/` is identical in every repo in this family. A `core/` change belongs
upstream in the factory, not here - a hotel-specific tweak belongs in
`tools/` or in your own adapter file.
