# RUNBOOK — BDR OS Operations Guide

## 1. Starting the System

### Quick start (local dev)
```bash
# Install dependencies
cd backend && pip install -e ".[dev]"

# Generate fixtures (deterministic, seed=42)
make fixtures

# Start the API server
make dev
# → http://localhost:8000  (healthz: /healthz, docs: /docs)
```

### Docker
```bash
# Create .env with required vars (see §5)
cp .env.example .env  # edit as needed

docker-compose up --build
# → http://localhost:8000
```

### Verify
```bash
curl http://localhost:8000/healthz
# {"status": "ok"}
```

## 2. Daily Operation

### The rhythm (ARCHITECTURE.md §4.3)

| Time | What happens | Your action |
|------|-------------|-------------|
| 02:00 | Nightly rates recompute + pipeline hygiene sweep | None (automatic) |
| 07:30 | Dispatcher morning plan → Today screen refreshes | Glance at Today for the day's priorities |
| 11:30 | *Check-in 1* | Open Review Queue → batch approve/reject/edit |
| 15:30 | *Check-in 2* | Review Queue again — book_response SLA jobs surface here |
| 17:30 | *Check-in 3* | Final sweep — approve remaining, snooze if needed |
| Sunday 21:00 | Weekly cascade → new Plan | Review next week's allocation on Pace |
| Friday 15:00 | Reporting agent → personal recap + manager draft | Approve manager update in Review Queue |

### Speed-to-Book SLA
Positive replies trigger `book_response` jobs with a **4-hour SLA**. These jump to the top of the Review Queue with a countdown timer. Approve fast.

### Show-Rate Machine
Meetings progress through: `BOOKED → INVITE_SENT → ACCEPTED → CONFIRMED_24H → CONFIRMED_AM → HELD`. Each transition creates a customer-facing job that needs approval. The machine also watches for:
- Invite not accepted in 24h → `reconfirm` job
- OOO autoreply → `reschedule` job
- Meeting >4 days out → `pull_in_offer` job
- No-show +10min → hands off to `no_show_recovery`

## 3. Approving via Review Queue

### API endpoints
```
GET  /api/jobs                       # list all jobs
GET  /api/jobs/{id}                  # job detail with output
POST /api/jobs/{id}/approve          # approve a job
POST /api/jobs/{id}/reject           # reject with reason
```

### Approval body (optional)
```json
{
  "decided_by": "malay",
  "edit_diff": {"body": "edited text here"},
  "rejection_reason": "Tone too aggressive"
}
```

### What to look for when approving
1. **Copy drafts**: voice matches? CTA clear? ≤90 words? No generic filler?
2. **Book responses**: slots ≤4 days out? In-thread? Acknowledges their words?
3. **Confirmations**: content-bearing, not naked "looking forward to it"?
4. **Recovery**: zero-guilt tone? Two concrete one-click times?
5. **Manager drafts**: outcome-first, no activity theater?

### Policy flags
Jobs may have `policy_flags` set when guardrails intervene:
- `strategic_account_block`: manual-only, no automated touches
- `rate_limit_exceeded`: daily outbound cap hit (40/day default)
- `draft_only_block`: customer-facing send blocked during ramp period

## 4. Reading the Pace Screen

### Pace API
```
GET /api/pace
```
Returns:
- Goal cascade tree (annual → monthly → weekly → daily)
- Funnel rates with confidence badges (LOW/MEDIUM/HIGH)
- Pace gap by stage
- Points: **credited** (AD-accepted), **pending** (held, awaiting AD), **projected** (booked × show_rate × accept_rate)
- Active catch-up levers with accept/dismiss

### Key rule: projected ≠ real
Pace runs on `credited + pending`, never on `projected`. If the UI shows you "on track" but credited+pending is behind, something's wrong.

### Earnings widget
Shows: month-to-date $, projected month $, annualized vs $135k goal, and **"value of one more VP meeting this month"** (marginal-$ motivation).

## 5. Environment Configuration

### Required environment variables
```bash
# Database
DATABASE_URL=sqlite:///./bdr_os.db

# Adapters (mock for dev, swap for live)
ADAPTER_CRM=mock          # mock | salesforce | hubspot
ADAPTER_EMAIL=mock         # mock | gmail
ADAPTER_CALENDAR=mock      # mock | gcal
ADAPTER_ENRICHMENT=mock    # mock | apollo | clearbit
ADAPTER_CALLRECORDING=mock # mock | gong

# Policy
DRAFT_ONLY_UNTIL=2026-09-15        # start + 60 days
MAX_NEW_OUTBOUND_PER_DAY=40
STRATEGIC_ACCOUNTS=["acct-001","acct-002","acct-003"]

# LLM (for agents)
ANTHROPIC_API_KEY=sk-ant-...

# Live adapters (only when swapping from mock)
# GOOGLE_CREDENTIALS_JSON=/path/to/creds.json
# GOOGLE_TEST_CALENDAR_ID=test-cal-id
```

## 6. Swapping an Adapter to Live

### Example: Email mock → Gmail

1. Set credentials:
   ```bash
   export ADAPTER_EMAIL=gmail
   export GOOGLE_CREDENTIALS_JSON=/path/to/service-account.json
   ```

2. Restart the server:
   ```bash
   make dev  # or docker-compose restart api
   ```

3. Verify with a test draft:
   ```bash
   RUN_LIVE=1 python -m pytest tests/e2e/test_live_leg.py -v
   ```

4. The Gmail adapter uses the same interface as mock — `create_draft()`, `send()`, `watch_replies()`. All policy guardrails still apply. During draft-only period, only `create_draft` is allowed.

### Example: Calendar mock → Google Calendar

```bash
export ADAPTER_CALENDAR=gcal
export GOOGLE_CREDENTIALS_JSON=/path/to/creds.json
```

Same pattern — the adapter registry resolves the implementation from env vars. Zero app-code changes required.

## 7. Running Tests

```bash
# All unit tests
make test

# E2E scenario suite
cd backend && python -m pytest tests/e2e/ -v

# Demo mode (happy_week narrative)
make demo

# Live adapter tests (requires credentials)
RUN_LIVE=1 python -m pytest tests/e2e/test_live_leg.py -v

# Lint
make lint
```

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Job stuck in `awaiting_approval` | Needs human check-in | Approve via API or Review Queue |
| `BLOCKED: strategic_account_block` | Account is in STRATEGIC_ACCOUNTS list | Manual-only — use the agent's prepared materials, execute manually |
| `rate_limit_exceeded` | >40 outbound in one day | Wait for next day, or increase MAX_NEW_OUTBOUND_PER_DAY |
| Show rate dropping | Confirmation cadence insufficient | Check show-rate machine jobs are being approved promptly |
| `needs_human_because` set on agent output | Agent low-confidence or edge case | Review the output, edit if needed, approve with edits |
| Book response SLA breach | Check-in too late | Set up more frequent check-in reminders |
