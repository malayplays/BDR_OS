# ADAPTER_CONTRACTS.md — Interfaces, Mocks, Fixtures, Connect-Later Registry

Every external system sits behind an abstract interface. The app imports interfaces only; `registry.py` resolves implementations from `.env`. **Going live = changing one env var + filling keys. Zero app-code changes.**

```python
# adapters/registry.py — resolution pattern
ADAPTER_CRM=mock          # [CONNECT LATER] → salesforce | hubspot | tbd
ADAPTER_EMAIL=gmail       # REAL NOW (personal acct) | mock; [CONNECT LATER] → work Gmail/Outlook
ADAPTER_CALENDAR=gcal     # REAL NOW (personal acct) | mock; [CONNECT LATER] → work calendar
ADAPTER_ENRICHMENT=mock   # [CONNECT LATER] → clay
ADAPTER_CALLRECORDING=mock# [CONNECT LATER] → gong
ANTHROPIC_API_KEY=...     # REAL NOW (personal key)
```

Common conventions: all methods async; all return typed Pydantic models; all raise `AdapterError(retryable: bool)`; every write method is **only callable from the write-back executor after `policy.check()`** — enforced by requiring a signed `Verdict` argument. Reads are free.

---

## 1. CRMAdapter `[CONNECT LATER — Salesforce confirmed as system of record per comp plan; API access policy still [CONFIRM WEEK 1]]`

Salesforce-specific requirements when built: meeting records must carry outbound-touch provenance (clawback protection), Named Target validation status, opp stage (S0→S2) sync → `s1_reached`/`s2_reached` events, AD acceptance field → `ad_accepted` event, contact last-activity date (drives 120-day dormancy timer). Interface stays vendor-generic; these land in `custom: dict` + `pull_events` mapping.

```python
class CRMAdapter(ABC):
    # reads
    async def get_account(self, account_ref: str) -> Account
    async def search_accounts(self, query: AccountQuery) -> list[Account]   # filters: owner, tier, last_touched_before, status
    async def get_contacts(self, account_ref: str) -> list[Contact]
    async def get_open_tasks(self, owner: str) -> list[CRMTask]
    async def get_activity(self, account_ref: str, since: datetime) -> list[Activity]
    async def pull_events(self, since: datetime) -> list[NormalizedEvent]   # → EventLog ingestion
    # writes (policy-gated)
    async def log_activity(self, v: Verdict, a: ActivityWrite) -> str       # returns external id
    async def create_task(self, v: Verdict, t: TaskWrite) -> str
    async def update_task(self, v: Verdict, task_ref: str, patch: dict) -> None
```
Models: `Account{ref, name, domain, tier(strategic|target|standard), owner, custom: dict}`, `Contact{ref, account_ref, name, title, email, phone, linkedin_url}`, `NormalizedEvent` mirrors EventLog fields.
**Mock behavior:** serves `fixtures/crm.json`; writes append to an in-memory log inspectable in tests (`mock.written[]`); `pull_events` replays a scripted timeline (see §6).

## 2. EmailAdapter — **REAL NOW vs personal Gmail** (validates the whole pattern)

```python
class EmailAdapter(ABC):
    async def list_threads(self, query: ThreadQuery) -> list[ThreadSummary]  # unreplied, since, label
    async def get_thread(self, thread_ref: str) -> Thread                    # full messages
    async def watch_replies(self, since: datetime) -> list[InboundMessage]   # poll; webhook later
    async def detect_autoreply(self, msg: InboundMessage) -> AutoReplyInfo   # OOO/bounce classification
    # writes (policy-gated)
    async def create_draft(self, v: Verdict, d: DraftEmail) -> str           # THE workhorse during draft-only
    async def send(self, v: Verdict, d: DraftEmail) -> str                   # BLOCKED by policy until day 60+, then per-job-type graduation
```
**Gmail impl now:** OAuth2 against personal account, `gmail.readonly + gmail.compose` scopes only (deliberately **not** `gmail.send`) — scope itself enforces draft-only in v1.
**Mock:** fixture threads; `create_draft` returns fake ids; scripted inbound replies for triage testing.
`[CONNECT LATER]` work mailbox: same impl, new OAuth client + org consent — check Cognition policy first.

## 3. CalendarAdapter — **REAL NOW vs personal Google Calendar**

```python
class CalendarAdapter(ABC):
    async def list_events(self, start: date, end: date) -> list[CalEvent]
    async def get_invite_status(self, event_ref: str) -> InviteStatus        # accepted/declined/needsAction per attendee
    async def find_slots(self, c: SlotCriteria) -> list[Slot]                # prefer <3–4 days out (slot science)
    async def get_capacity(self, start: date, end: date) -> Capacity         # business days, PTO, blocked hours → feeds cascade
    # writes (policy-gated)
    async def create_event(self, v: Verdict, e: EventWrite) -> str           # invite w/ value-framed agenda body
    async def update_event(self, v: Verdict, event_ref: str, patch: dict) -> str
```
`CalEvent{ref, title, start, end, attendees[{email, response_status}], body, meeting_link}`. `Slot{start, end, days_out}` — `find_slots` sorts by `days_out` ascending and flags `days_out > 4` as `pull_in_candidate`.
**Mock:** fixture calendar with scripted accept/decline/no-show timelines for show-rate-machine tests.

## 4. EnrichmentAdapter (Clay) `[CONNECT LATER — seat/table access unknown]`

```python
class EnrichmentAdapter(ABC):
    async def enrich_company(self, domain: str) -> CompanyProfile      # size, funding, stack, eng headcount trend
    async def enrich_contact(self, email_or_linkedin: str) -> PersonProfile
    async def get_signals(self, since: datetime) -> list[Signal]       # THE trigger feed for signal-based jobs
```
`Signal{kind: hiring_surge|eng_leadership_change|dev_velocity_pain|funding|tech_adoption, account_domain, strength: 0–1, evidence: str, detected_at}`. Kinds chosen to match Cognition's buyer (eng orgs in pain about velocity); extendable enum.
**Mock:** `fixtures/signals.json` with a drip-feed script — N new signals per simulated day so the create-stage pipeline has realistic flow.

## 5. CallRecordingAdapter (Gong) `[CONNECT LATER — likely needs admin grant]`

```python
class CallRecordingAdapter(ABC):
    async def list_calls(self, since: datetime) -> list[CallMeta]
    async def get_transcript(self, call_ref: str) -> Transcript        # speaker-labeled segments
```
Consumed by CRM scribe (transcript → notes) and call prep (past-call context). **Mock:** 3 fixture transcripts (good discovery call, objection-heavy call, no-show-rescheduled call).

---

## 6. Fixture data shape (`backend/fixtures/`)

```
crm.json          # 25 accounts (3 strategic tier — guardrail tests), 60 contacts, 20 open tasks
threads.json      # 30 email threads: 6 positive replies, 8 objections, 4 OOO, 2 bounces, rest cold
calendar.json     # 2 weeks of events incl. 5 booked meetings at varying days_out, 1 scripted no-show
signals.json      # 15 signals across all kinds, varying strength
transcripts.json  # 3 calls
event_timeline.json  # 90 days of synthetic EventLog history → lets rates math compute real numbers on day one of dev
```
Synthetic timeline is generated by `fixtures/generate.py` (seeded RNG, rates ≈ the benchmark priors, ±noise) so engine tests assert against known ground-truth rates.

## 7. [CONNECT LATER] registry (detail in CONNECT_LATER_CHECKLIST.md)

| Adapter | Now | Live target | Blocking unknowns |
|---|---|---|---|
| CRM | mock | **Salesforce (confirmed)** | API access policy [CONFIRM WEEK 1], field map incl. AD-acceptance + opp stage + provenance fields |
| Email (work) | gmail-personal | work mailbox | provider, OAuth/org consent, sequencer overlap [CONFIRM WEEK 1] |
| Calendar (work) | gcal-personal | work calendar | provider, scheduling-link tool in use |
| Enrichment | mock | Clay | seat, table/webhook access |
| Call recording | mock | Gong | seat, API key policy |
| Sequencer | — (not an adapter yet) | TBD [CONFIRM WEEK 1] | if Outreach/Salesloft/Apollo exists, sequence-management jobs write THERE, not raw email — add `SequencerAdapter` interface in week 2, don't build now |
| Dialer | — | TBD [CONFIRM WEEK 1] | call logging may flow via CRM; decide after stack known |
```
