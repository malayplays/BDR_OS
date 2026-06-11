# Devin Session 0 — Repo Scaffold, Data Model, Adapters + Mocks, Config

Paste alongside: ARCHITECTURE.md, DATA_MODEL.md, ADAPTER_CONTRACTS.md (attach all three).

## Task

Create the `bdr-os` monorepo exactly per ARCHITECTURE.md §3/§7:

1. **Scaffold**: `backend/` (Python 3.12, FastAPI, SQLAlchemy, APScheduler, pytest, ruff), `frontend/` (empty placeholder + README pointing at frontend prompts), `docker-compose.yml` (api + sqlite volume), `.env.example` as the connection registry from ADAPTER_CONTRACTS.md, Makefile (`make dev`, `make test`, `make fixtures`).
2. **Models**: all six objects from DATA_MODEL.md as SQLAlchemy models + Pydantic schemas. SQLite now, Postgres-portable types only (no SQLite-specific features). Alembic from day one.
3. **Adapter layer**: the five abstract interfaces verbatim from ADAPTER_CONTRACTS.md; mock implementations for all five backed by `fixtures/*.json`; `registry.py` resolving from env; `AdapterError(retryable)`.
4. **Fixtures**: `fixtures/generate.py` producing the files in ADAPTER_CONTRACTS.md §6 with a seeded RNG (seed=42). The 90-day `event_timeline.json` must be generated from known ground-truth rates (use the seed benchmarks in DATA_MODEL.md Object 3) so later engine tests can assert recovered rates ≈ ground truth.
5. **Policy skeleton**: `policy/guardrails.py` with `check(action) -> Verdict` reading `policy.yaml` + env (`DRAFT_ONLY_UNTIL`, rate limits, `STRATEGIC_ACCOUNTS`). Implement: draft-only blanket rule, strategic-account block, daily new-outbound counter. Write methods on adapters must require a `Verdict` arg (type-enforced).
6. **Job lifecycle**: `Job` state machine with allowed transitions only (DATA_MODEL.md Object 6); invalid transition raises. Approval endpoints stubbed: `POST /api/jobs/{id}/approve|reject`.
7. **Voice profile placeholder**: `backend/app/agents/voice_profile.md` and `value_props.yaml` with `[CONFIRM WEEK 1]` placeholder content.

## Constraints

- `engine/` package created but EMPTY except `__init__.py` — Session 1 fills it. Do not pre-implement rate math.
- No real Gmail/GCal yet (Session 12 option) — mocks only this session.
- No LLM calls this session.

## Done = these pass

- `make dev` boots API; `GET /healthz` 200; `GET /api/jobs` returns fixture-seeded jobs.
- `make fixtures` regenerates byte-identical fixtures (seeded).
- `pytest`: ≥ the following tests, all green:
  - `test_models_roundtrip` — create/read all six objects
  - `test_job_lifecycle` — legal transitions pass, illegal raise
  - `test_adapter_registry` — env swap mock↔mock2 resolves without app-code change
  - `test_mock_crm_serves_fixtures`, `test_mock_email_drafts_inspectable`
  - `test_policy_draft_only` — customer-facing write w/o approval verdict → BLOCK
  - `test_policy_strategic_block` — write targeting strategic-tier account → BLOCK even with approval
  - `test_policy_rate_limit` — 41st new outbound of the day → REQUIRE_APPROVAL with policy_flag
- `ruff check` clean. Alembic migration applies to empty db.
