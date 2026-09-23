# Sentinel IP AI

**AI-Powered IP Enforcement at Counterfeit Speed.**

A multi-agent platform that lets a small IP enforcement team protect a global
product catalogue from counterfeits and trademark-infringing listings. Four
agents find, verify, strategise and prepare enforcement — and stop at a human
approval gate that no autonomy tier can remove.

> **Two architectural principles govern this codebase:**
> **tenant isolation and human approval are security boundaries, not UI features.**
> Both are enforced in the query and service layers, and both have automated
> tests that fail loudly if they regress.

---

## Contents

1. [Quick start](#1-quick-start)
2. [Demo accounts](#2-demo-accounts)
3. [What to look at first](#3-what-to-look-at-first)
4. [Architecture](#4-architecture)
5. [The four agents](#5-the-four-agents)
6. [The human approval gate](#6-the-human-approval-gate)
7. [Autonomy tiers](#7-autonomy-tiers)
8. [Multi-tenancy](#8-multi-tenancy)
9. [Roles and permissions](#9-roles-and-permissions)
10. [Data model](#10-data-model)
11. [API reference](#11-api-reference)
12. [Security](#12-security)
13. [Tests](#13-tests)
14. [Configuration reference](#14-configuration-reference)
15. [Swapping in a real AI provider](#15-swapping-in-a-real-ai-provider)
16. [Deliberate design decisions](#16-deliberate-design-decisions)
17. [Known limitations](#17-known-limitations)
18. [Production hardening checklist](#18-production-hardening-checklist)
19. [Troubleshooting](#19-troubleshooting)

---

## 1. Quick start

### Prerequisites

| Requirement | Notes |
| ----------- | ----- |
| Python 3.10+ | 3.12 recommended; the code is tested on 3.11 and targets 3.10+ |
| MySQL 8.0+ or MariaDB 10.6+ | Running on `127.0.0.1:3306` by default |
| A MySQL user that can `CREATE DATABASE` | Or create `sentinel_ip` yourself and grant on it |

### Setup

```bash
# 1. Get the code and create a virtual environment
cd sentinel-ip-ai
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"   # paste into SECRET_KEY
#    then edit DB_USER / DB_PASSWORD in .env to match your MySQL

# 3. Create the database, then the schema
python scripts/init_db.py            # creates the database if it does not exist
alembic upgrade head                 # creates the tables

# 4. Load demo data (runs the real agent pipeline against the mock provider)
python scripts/seed.py

# 5. Run
python run.py
```

Open <http://localhost:8000>. Interactive API docs are at
<http://localhost:8000/api/docs> outside production.

### Which command creates what?

`alembic upgrade head` cannot create the database it needs to connect to, so the
two steps are split:

| Command | Creates |
| ------- | ------- |
| `python scripts/init_db.py` | The **database** (and the upload directory). Idempotent. |
| `alembic upgrade head` | The **tables**. Versioned, repeatable, reversible with `alembic downgrade base`. |

Alembic owns the schema. If you would rather not run migrations at all,
`python scripts/init_db.py --tables` creates the tables straight from the models
and stamps Alembic at head, so a later `upgrade head` is a no-op.

`python scripts/init_db.py --drop` destroys every table and the Alembic version
record. It asks for confirmation unless you pass `--yes`.

---

## 2. Demo accounts

`scripts/seed.py` creates these. **All share one password**, printed by the seed
script and configured by `SEED_DEFAULT_PASSWORD` in `.env`:

```
Sentinel!Demo2026
```

| Email | Role | Scope |
| ----- | ---- | ----- |
| `admin@sentinel.local` | Super Admin | Every vendor, every case, all configuration |
| `platform.admin@sentinel.local` | Admin (platform) | Manages users and vendors across the platform |
| `admin@acme.local` | Admin (tenant) | Administers **Acme Consumer Brands only** |
| `user@acme.local` | Vendor User | Acme Consumer Brands |
| `analyst@acme.local` | Vendor User | Acme Consumer Brands |
| `admin@global.local` | Admin (tenant) | Global Retail Group only |
| `user@global.local` | Vendor User | Global Retail Group |
| `user@txoutdoor.local` | Vendor User | Texas Outdoor Products |
| `user@demobrand.local` | Vendor User | Demo Brand Corporation |

> **These are development credentials.** They exist only because `scripts/seed.py`
> was run. Change `SEED_DEFAULT_PASSWORD`, and never run the seed script against
> anything that is not a local development database.

---

## 3. What to look at first

**Tenant isolation, in thirty seconds.** Sign in as `user@acme.local`. The case
list shows only Acme Consumer Brands. Copy the id of a Global Retail Group case
(visible as `admin@sentinel.local`) and put it in the URL — you get a 403 that
says nothing about whether that case exists. The block happens in the SQL query,
not in the template: see `scope_to_vendor()` in
[`app/auth/dependencies.py`](app/auth/dependencies.py).

**The approval gate.** Create a case as a vendor user. Watch the four agent
cards fill in. The fourth, Filing & Tracking, ends in **Awaiting approval** —
not Complete. Try to file it through the API before approving:

```bash
curl -X POST http://localhost:8000/api/cases/1/file -b cookies.txt
# 409 {"success": false, "error": {"code": "HUMAN_APPROVAL_REQUIRED", ...}}
```

Every refusal is written to the audit trail as a denied action.

**The audit trail.** `Administration → Audit Trail`. Filter by
`APPROVAL_GRANTED` and open a row: it holds the AI recommendation, the
confidence at the moment of the decision, the approver, their IP, the
correlation id, and a frozen snapshot of the evidence set.

---

## 4. Architecture

```
sentinel-ip-ai/
├── app/
│   ├── main.py                 FastAPI app, middleware, lifespan, startup checks
│   ├── config.py               Settings from .env (no secret is ever hard-coded)
│   ├── constants.py            The domain vocabulary: statuses, actions, tiers
│   ├── errors.py               One error envelope for the whole API
│   ├── logging_config.py       Correlation-id logging
│   │
│   ├── api/                    HTTP layer only - no business rules live here
│   │   ├── auth.py  users.py  vendors.py  cases.py  evidence.py
│   │   ├── enforcement.py  agents.py  audit.py  dashboard.py
│   │   ├── notifications.py  admin.py  meta.py  ws.py  pages.py
│   │
│   ├── auth/
│   │   ├── security.py         Hashing, JWT, CSRF, password policy
│   │   └── dependencies.py     Auth, RBAC and the tenant-scoping helpers
│   │
│   ├── models/                 SQLAlchemy 2.x models (17 tables)
│   ├── schemas/                Pydantic request/response contracts
│   │
│   ├── services/               Where the business rules actually live
│   │   ├── enforcement.py      *** the human approval gate ***
│   │   ├── audit.py            Append-only audit writer with secret scrubbing
│   │   ├── sla.py              SLA computation
│   │   ├── notifications.py    In-app notifications
│   │   ├── realtime.py         WebSocket fan-out
│   │   └── references.py       CASE-…, EV-…, ENF-… reference generation
│   │
│   ├── agents/
│   │   ├── base.py             BaseAgent: run bookkeeping, evidence, findings
│   │   ├── scout.py  verification.py
│   │   ├── enforcement_strategist.py  filing_tracking.py
│   │   └── pipeline.py         Orchestration + the four agent cards
│   │
│   ├── ai/provider.py          AIProvider interface, Mock/OpenAI/Gemini
│   └── middleware/             Security headers, correlation ids, rate limiting
│
├── frontend/
│   ├── templates/              Jinja2 page shells (no business logic)
│   └── static/css|js|          Vanilla JS calling the REST API; Chart.js vendored
│
├── migrations/                 Alembic
├── scripts/init_db.py seed.py
├── tests/                      103 tests
└── run.py  requirements.txt  .env.example  alembic.ini
```

**The layering rule:** routers validate and delegate; services own the rules;
models own the shape. The approval gate lives in `services/enforcement.py` and
nothing bypasses it — including the agents.

**The frontend is not a static prototype.** Every screen fetches from the REST
API, the API applies the same authorization a second time, and the templates
render only the chrome. Turning off JavaScript shows you an empty shell, not
somebody else's data.

---

## 5. The four agents

Each agent implements one interface (`app/agents/base.py`):

```python
class BaseAgent(abc.ABC):
    agent_name: str
    sequence: int
    charter: str                                   # what it is allowed to do

    def build_context(self, db, case) -> dict: ...  # gather facts, no side effects
    async def run(self, db, case, run, ctx) -> dict: ...  # do the work
    async def execute(self, db, case, ...): ...     # bookkeeping + audit + errors
```

`execute()` opens an `ai_agent_runs` row before anything happens, records the
provider, model, duration, confidence and evidence count, and writes an
`AI_AGENT_EXECUTED` audit record whether the run succeeds or fails.

| Agent | Does | Never does |
| ----- | ---- | ---------- |
| **1. Scout** | Captures the listing, seller identity and history, pricing against MSRP, trademark usage, imagery provenance, marketplace metadata, timestamps. Writes one evidence item per signal. | Conclude infringement |
| **2. Verification** | Weighs the signals both ways, produces a calibrated 0–100 confidence and a one-sentence finding, and records **contradictory** evidence as prominently as supporting evidence. | Decide an action |
| **3. Enforcement Strategist** | Picks exactly one action from the approved set, justifies it in plain language against the evidence, lists the risks of taking it and the alternatives it rejected. Creates the enforcement action in `AWAITING_APPROVAL`. | Execute anything |
| **4. Filing & Tracking** | Drafts the notice, assembles the evidence package, tracks submission, SLA, marketplace response, escalation. | **File without a recorded human approval** |

Confidence bands, exactly as specified:

| Score | Band |
| ----- | ---- |
| 0–39 | Low |
| 40–69 | Medium |
| 70–89 | High |
| 90–100 | Critical |

Displayed as **AI Confidence: 92% — Critical** with a ring indicator, and always
accompanied by:

> AI confidence is a calibrated model score, not a legal determination of
> infringement. All enforcement decisions require human review.

**Verification refuses to be confident on thin evidence.** With fewer than three
evidence items the score is capped at 58 and a "Limited evidence available"
finding is recorded. A model that is always sure is not an analyst.

### The pipeline

```
Create Case
     │
     ▼  Scout ─────────────► evidence + signals
     ▼  Verification ──────► confidence + supporting AND contradictory findings
     ▼  Enforcement Strategist ──► one recommendation + risks + alternatives
     │
   ══╪══════════ HUMAN APPROVAL GATE ═══════════
     │
     ▼  Filing & Tracking ─► notice draft, package, submission, tracking
```

Progress streams to the case page over a WebSocket (`/ws/cases/{id}`), with
polling as a fallback.

---

## 6. The human approval gate

This is the part worth reading the code for:
[`app/services/enforcement.py`](app/services/enforcement.py).

`submit_action()` is the **only** path to a `SUBMITTED` enforcement action
anywhere in the application. Before it does anything it requires an `approvals`
row that is:

* `decision == APPROVED`, **and**
* attached to *this* enforcement action, **and**
* matching its **`recommendation_version`**.

That last condition matters. If the AI re-runs and produces a new
recommendation, the version increments and the earlier approval no longer
authorises anything. A human approved a specific recommendation, not the case in
perpetuity. `test_10e_a_new_recommendation_invalidates_the_old_approval` covers it.

When the gate refuses, it writes an `ACCESS_DENIED` audit record and returns:

```json
{
  "success": false,
  "error": {
    "code": "HUMAN_APPROVAL_REQUIRED",
    "message": "This enforcement action requires a recorded human approval before it can be filed."
  }
}
```

**What an approval records** — frozen at the moment of decision, never
recalculated: the decision, the user (id, email and role), timestamp, comments,
the rejection reason, the AI recommendation and its confidence and risk level,
the recommendation version, the autonomy tier in force, the IP address, the
correlation id, and a **snapshot of every evidence item** with its checksum and
review state. Rejection requires a reason; requesting changes requires comments.

**Approving files the action.** An approval *is* the authorisation to file, so
Filing & Tracking picks the action up immediately (`AUTO_FILE_ON_APPROVAL=true`).
This does not weaken the gate: `submit_action()` still verifies the approval.
Set the flag to `false` if your process needs filing to be a separate click.

**All filings in this build are simulated** (`SIMULATED_FILING=true`) and are
labelled **DEMO / SIMULATED** in the UI, in the API payload and in the audit
record. No external system is contacted.

---

## 7. Autonomy tiers

| Tier | Name | Behaviour |
| ---- | ---- | --------- |
| 0 | Manual | AI assists only |
| 1 | AI Recommendation | AI investigates and recommends; a human must approve — **default for every new vendor** |
| 2 | AI Draft | AI also prepares the evidence package and notice; a human must approve before filing |
| 3 | Controlled Automation | Approved workflows may auto-execute predefined **non-legal** operational actions (`MONITOR`, `REQUEST_EVIDENCE`, `NO_ACTION`); everything is still audit logged |
| 4 | Autonomous | **Reserved.** Disabled by `MAX_AUTONOMY_TIER=3`; the API refuses to file at tier 4 |

No tier removes approval for a legal action. `LEGAL_ACTIONS` in
`app/constants.py` — marketplace complaint, trademark complaint, copyright
complaint, cease and desist, escalate to legal, contact seller — always requires
a recorded human decision, at every tier, with no configuration flag to change
that. Only a Super Admin can set tier 3 or above, and the change is audited.

---

## 8. Multi-tenancy

Every business record carries `vendor_id`. Isolation is applied **to the query**,
not to the response:

```python
def scope_to_vendor(stmt, model, user):
    if user.is_global:
        return stmt
    return stmt.where(model.vendor_id == user.vendor_id)
```

Every route that touches vendor-owned data goes through `scope_to_vendor()` or
`load_case()`, so another tenant's row is never selected in the first place —
there is no filtered-after-the-fact code path to forget. A vendor user who edits
the id in the URL receives `403 VENDOR_ACCESS_DENIED`, the response leaks nothing
about whether the record exists, and the attempt is audited.

A vendor user with no vendor assignment sees nothing at all (a defensive
`WHERE vendor_id = -1`), and a user whose vendor is suspended loses access on
their next request, not at their next login.

---

## 9. Roles and permissions

| Role | Vendor | Sees |
| ---- | ------ | ---- |
| **Super Admin** | Must have none | Everything, all vendors, all configuration |
| **Admin** | Optional | Without a vendor: platform-wide user/vendor administration. With a vendor: that tenant only, scoped exactly like a vendor user |
| **Vendor User** | **Required** | Their own vendor's cases, evidence, enforcement, analytics and audit trail |

The Vendor dropdown on the Create User form is populated from
`GET /api/vendors/options`, and the rule is enforced in three places: the
Pydantic schema, the API layer (`_validate_vendor_pairing`), and the tests. Pick
`Super Admin` and the dropdown disables itself; pick `Vendor User` and it becomes
required.

**Privilege escalation is blocked in both directions.** No one can create or
modify a role above their own; a platform Admin cannot mint a Super Admin; a
tenant-scoped Admin cannot create users outside their vendor or create vendors at
all; nobody can disable their own account.

> **A note on "Admin".** The specification lists Admin as a role that can create
> vendors and users but also "view cases according to assigned permissions",
> which reads two ways. Rather than pick one, an Admin here is platform-wide when
> unassigned and tenant-scoped when given a vendor. That covers both readings and
> is strictly safer than making every Admin global.

---

## 10. Data model

17 tables, normalised, with indexes on every filter and join column.

```
vendors ──┬── users ────── user_vendor_assignments
          ├── products
          ├── sla_policies
          └── cases ──┬── listings
                      ├── evidence ────────── (agent_run_id)
                      ├── ai_agent_runs ───── ai_findings
                      ├── case_assignments
                      ├── enforcement_actions ─── approvals
                      ├── notifications
                      └── audit_logs
roles          marketplaces
```

Notes worth knowing:

* **`audit_logs` is append-only.** The only writer is `services/audit.py:record()`.
  There is no update or delete anywhere in the application, and no API endpoint
  that would allow one — `test_audit_has_no_write_endpoints` proves it.
* **Evidence is never destroyed.** It can be *archived* with a mandatory reason,
  and archived items stay visible on the case and in the approval snapshot.
* **Four foreign-key pairs are circular** (vendor↔sla_policy, vendor↔creating
  user, case↔listing, enforcement_action↔approval). They are declared
  `use_alter=True` and added by `ALTER` in the migration after every table
  exists. The initial migration's `downgrade()` is hand-written for the same
  reason: MySQL will not drop an index a foreign key still needs.
* A constraint **naming convention** is set on the metadata, so index and
  constraint names are deterministic across MySQL and SQLite and Alembic can
  alter them later.

---

## 11. API reference

Every endpoint below requires authentication. Failures share one envelope:

```json
{"success": false, "error": {"code": "VENDOR_ACCESS_DENIED", "message": "You do not have permission to access this record."}}
```

| Method | Path | Notes |
| ------ | ---- | ----- |
| `POST` | `/api/auth/login` | Sets an HttpOnly session cookie and a CSRF cookie; also returns a bearer token |
| `POST` | `/api/auth/logout` | Increments `token_version`, invalidating every issued token |
| `GET` | `/api/auth/me` | Current user, role, vendor, permissions |
| `POST` | `/api/auth/change-password` | Signs out other sessions |
| `GET` | `/api/meta` | Enum vocabulary so the frontend hard-codes nothing |
| `GET` | `/api/dashboard` | Tenant-scoped analytics and all seven charts |
| `GET` | `/api/reports/summary` | Vendor and marketplace breakdowns |
| `GET/POST` | `/api/vendors` | List / create |
| `GET` | `/api/vendors/options` | Feeds the Vendor dropdown |
| `GET/PUT/DELETE` | `/api/vendors/{id}` | `DELETE` disables; it never destroys |
| `GET/POST` | `/api/users` | Create returns a one-time temporary password if the server generated it |
| `GET` | `/api/users/roles` | Roles the caller is allowed to assign |
| `GET/PUT/DELETE` | `/api/users/{id}` | Delete is refused for users with case history — they are disabled instead |
| `GET/POST` | `/api/cases` | Full filtering, search, sorting, pagination |
| `GET/PUT` | `/api/cases/{id}` | `GET` returns the whole investigation |
| `POST` | `/api/cases/{id}/analyze` | Runs the pipeline in the background |
| `GET` | `/api/cases/{id}/agents` · `/agents/{run_id}` | Agent cards; full auditable output |
| `GET/POST` | `/api/cases/{id}/evidence` · `/evidence/url` | List / upload / add a URL |
| `POST` | `/api/cases/{id}/approve` · `/reject` · `/request-changes` | The gate |
| `POST` | `/api/cases/{id}/file` | **409 `HUMAN_APPROVAL_REQUIRED`** without an approval |
| `POST` | `/api/cases/{id}/enforcement/response` | DEMO: record a marketplace outcome |
| `GET` | `/api/cases/{id}/enforcement/timeline` · `/approvals` · `/audit` | |
| `GET/PUT` | `/api/evidence` · `/{id}/review` · `/{id}/archive` · `/{id}/download` | |
| `GET` | `/api/enforcement` · `/enforcement/pipeline` | |
| `GET` | `/api/agents` · `/agents/health` · `/agents/runs` | AI Operations |
| `GET` | `/api/audit` · `/api/audit/actions` | Read-only, tenant-scoped |
| `GET/POST` | `/api/notifications` · `/count` · `/{id}/read` · `/read-all` | |
| `GET/POST/PUT` | `/api/admin/sla-policies` · `/roles` · `/permissions` · `/autonomy` · `/marketplaces` | |
| `WS` | `/ws/cases/{id}` | Live agent status; tenant-checked at connect |

Status codes are used as they should be: `200`, `201`, `302` (page auth
redirect), `400`, `401`, `403`, `404`, `409` (conflict / approval required),
`422` (validation), `423` (locked), `429` (rate limited), `500`.

---

## 12. Security

**Authentication.** JWT (HS256) issued into an `HttpOnly`, `SameSite=Lax` cookie
and also returned as a bearer token for API clients. Every token carries a
`token_version`; logout, a password change or disabling an account increments it
and invalidates every token already issued. Access tokens expire in 60 minutes,
or 7 days with "Remember me".

**Passwords.** bcrypt at 12 rounds (Argon2 available via `PASSWORD_HASH_SCHEME`),
with a per-hash salt. The policy requires 12+ characters with upper, lower, digit
and symbol, and rejects well-known passwords. Admin-created accounts get a
generated password shown exactly once and must change it at first sign-in.

**Login throttling.** Sliding window on both IP and email, so one attacker cannot
lock out every account and one account cannot be sprayed cheaply. Ten failures
locks the account for fifteen minutes. Unknown accounts are verified against a
real dummy hash so response timing does not reveal whether an address exists,
and the error message is identical either way.

**CSRF.** Double-submit cookie. Cookie-authenticated writes require a matching
`X-CSRF-Token` header. Bearer-authenticated requests are exempt because they are
not cookie-driven.

**Headers and CSP.** `X-Content-Type-Options`, `X-Frame-Options: DENY`,
`Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`,
`Cache-Control: no-store` on the API, HSTS when `COOKIE_SECURE=true`, and a CSP
of `script-src 'self'` — **no inline script anywhere and no third-party script
host**. Chart.js is vendored under `frontend/static/js/vendor/`.

**Injection.** All queries go through SQLAlchemy Core/ORM with bound parameters;
no string interpolation reaches the database. Every value rendered by the
frontend passes through `UI.esc()`, and URLs through `UI.safeUrl()`, which
accepts only `http(s)` and same-origin paths.

**File uploads** are validated three ways — extension allow-list, declared MIME
type, and magic-byte sniffing of the actual content — then capped at 25 MB,
stored outside the web root under a generated name with `0640` permissions, and
hashed (SHA-256). Downloads are tenant-scoped, re-check the resolved path against
the upload root, and are served `Content-Disposition: attachment` with
`nosniff`.

**Secrets** live only in environment variables. Passwords, hashes, tokens, API
keys and internal system prompts are never returned by any endpoint, and
`services/audit.py:scrub()` redacts them recursively before anything is written
to the audit table. Unhandled errors return an opaque correlation id; the stack
trace goes to the log.

**Startup refuses an insecure production configuration** — a weak `SECRET_KEY`,
`COOKIE_SECURE=false` or `DEBUG=true` with `APP_ENV=production` raises at boot
rather than serving.

---

## 13. Tests

```bash
pytest                       # 103 tests, ~35s
pytest -v tests/test_authorization.py
pytest -k approval
```

Tests run against an isolated temporary SQLite database, so they never touch
your MySQL instance. The models are portable, so the same code paths are
exercised.

| File | Covers |
| ---- | ------ |
| `test_authorization.py` | The ten mandated tests plus cross-tenant reads, writes, evidence, approvals, filters, privilege escalation, and the tenant-scoped Admin |
| `test_pipeline.py` | Agent sequencing, confidence banding, evidence and checksums, contradictory findings, determinism, upload validation (including a PE binary renamed `.png`), archive-not-delete, enforcement tracking |
| `test_security.py` | Password hashing and policy, session cookies, logout invalidation, CSRF, account lockout, security headers, SQL injection, XSS storage, SLA arithmetic, audit completeness and secret scrubbing |
| `test_admin_workflow.py` | The complete 16-step administrator workflow, end to end |

The specification's ten authorization tests map to `test_1` … `test_10` in
`test_authorization.py`.

---

## 14. Configuration reference

Everything lives in `.env`. See `.env.example` for the annotated full list.

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `APP_ENV` | `development` | `production` enables the startup security checks and disables `/api/docs` |
| `SECRET_KEY` | — | **Set this.** JWT signing key |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | `127.0.0.1:3306`, `root`, ``, `sentinel_ip` | Or override wholesale with `DATABASE_URL` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | |
| `COOKIE_SECURE` | `false` | **Must be `true` behind HTTPS** |
| `AI_PROVIDER` | `mock` | `mock` \| `openai` \| `gemini` |
| `AI_MOCK_SEED` | `1337` | Makes the demo reproducible |
| `DEFAULT_AUTONOMY_TIER` | `1` | New vendors |
| `MAX_AUTONOMY_TIER` | `3` | Tier 4 is reserved |
| `SIMULATED_FILING` | `true` | Filings are labelled DEMO / SIMULATED |
| `AUTO_FILE_ON_APPROVAL` | `true` | Approval hands the action to Filing & Tracking |
| `DEFAULT_SLA_HOURS` | `48` | |
| `MAX_UPLOAD_MB` | `25` | |

---

## 15. Swapping in a real AI provider

The agents never touch a vendor SDK. They call one interface:

```python
class AIProvider(abc.ABC):
    name: str
    model: str

    @abc.abstractmethod
    async def analyze(self, prompt: str, context: dict) -> AIResponse: ...
```

To use OpenAI or Gemini:

```bash
AI_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

To add another provider: implement `AIProvider`, register it in `_PROVIDERS` in
`app/ai/provider.py`, done. If a provider fails to initialise the factory logs
the error and falls back to the mock rather than taking the platform down.

**`MockAIProvider` is the default and needs no API key.** It is not a stub that
returns canned text — it is a transparent weighted-signal model over price ratio,
trademark usage, image similarity, seller age, rating, prior reports, shipping
origin and stock anomalies. Every number in the UI traces back to a named
finding, and it is seeded on the *observed listing facts* so the same listing
always scores the same. That is the property you want from the real model too:
an auditable decision, not an opaque verdict.

> **`OpenAIProvider` and `GeminiProvider` are wired but unverified.** No API key
> was available in this build, so those code paths have never executed. The
> request shapes and JSON parsing are correct as written, but treat them as
> templates and run them against a key before relying on them. This is flagged in
> the docstrings too.

---

## 16. Deliberate design decisions

Places where the specification was ambiguous or where the obvious approach was
worse, and the call that was made:

1. **MySQL, not PostgreSQL.** The specification said PostgreSQL; you run MySQL on
   `127.0.0.1:3306`. Ported: `PyMySQL`, portable column types, no `JSONB`, no
   `NULLS LAST` (MySQL has no such syntax — the dashboard sorts on the
   null-ness first instead), and explicit lengths on every indexed `VARCHAR`.
2. **The approval binds to a recommendation version, not to the case.** Without
   this, re-running the AI would silently inherit an approval for a different
   recommendation. This is the single most important line of defence in the gate.
3. **Approving files the action.** The specification's workflow step 15 has
   Filing & Tracking update the status right after the human decision. An
   approval *is* the authorisation, so the gate is satisfied and filing proceeds
   through the same guarded function. Set `AUTO_FILE_ON_APPROVAL=false` for a
   two-step process.
4. **Admin can optionally be tenant-scoped.** See the note in §9.
5. **Evidence is archived, never deleted**, and a reason is mandatory. The
   specification said "never allow evidence to be silently deleted"; a soft
   archive with an audited reason is the honest reading.
6. **Deleting a user with case history is refused** — the account is disabled
   instead, so the audit trail keeps its referents.
7. **Verification caps confidence on thin evidence** and always reports
   contradictory findings. A one-sided analyst is worse than no analyst.
8. **Chart.js is vendored, not loaded from a CDN.** It lets the CSP be
   `script-src 'self'` with no exceptions and the app work offline.
9. **`.local` demo email addresses** are reserved mDNS names that
   `email-validator` correctly rejects. The restriction is lifted in
   non-production environments only (`app/config.py`).
10. **Tests use SQLite, the app uses MySQL.** Tests stay fast and never touch
    your database. The trade-off is stated in §17.

---

## 17. Known limitations

Stated plainly, because a demo that hides these is not useful:

* **The tests run on SQLite, not MySQL.** The schema is portable and the
  migration has been applied, downgraded and re-applied against MySQL/MariaDB,
  but the *test suite* does not exercise MySQL-specific behaviour (collation,
  strict mode, lock semantics). Point `DATABASE_URL` at a scratch MySQL database
  and re-run `pytest` before you trust it in production.
* **The real AI providers are unexecuted code.** See §15.
* **Rate limiting is in-process.** Fine for one worker; it does not hold across
  multiple processes or hosts. Back it with Redis before scaling out — the
  `RateLimiter` interface is designed for that swap.
* **WebSocket fan-out is single-process** for the same reason.
* **The audit table is append-only by convention and code review**, not by
  database grant. See §18.
* **Marketplace filing is simulated.** There is no real marketplace integration;
  `simulate_marketplace_response` stands in for a webhook, and every filing is
  labelled DEMO / SIMULATED.
* **The background pipeline uses FastAPI `BackgroundTasks`.** It dies with the
  process. A real deployment wants Celery, RQ or ARQ with retries and a dead
  letter queue.
* **No email delivery.** Notifications are in-app only.
* **`X-Forwarded-For` is trusted when present.** Configure your reverse proxy to
  overwrite rather than append it, or the recorded IP can be spoofed.

---

## 18. Production hardening checklist

- [ ] `APP_ENV=production`, `DEBUG=false`, a 64-byte random `SECRET_KEY`
- [ ] `COOKIE_SECURE=true` behind TLS; set `CORS_ORIGINS` to your real origins
- [ ] A dedicated MySQL user with only the grants it needs — **not** `root`
- [ ] **Harden the audit trail at the database level:**
      `REVOKE UPDATE, DELETE ON sentinel_ip.audit_logs FROM 'app_user'@'%';`
      and ship the rows to append-only storage (or a WORM bucket) on a schedule
- [ ] Redis-backed rate limiting and WebSocket fan-out
- [ ] A real task queue for the agent pipeline
- [ ] Object storage for evidence with server-side encryption, plus backups
- [ ] Log aggregation keyed on the correlation id
- [ ] `SIMULATED_FILING=false` only once a real marketplace integration exists
      **and** counsel has signed off on the notice templates
- [ ] Penetration test focused on the two boundaries: tenant isolation and the
      approval gate

---

## 19. Troubleshooting

**`Can't connect to MySQL server on '127.0.0.1'`** — MySQL is not running, or
`DB_USER`/`DB_PASSWORD` in `.env` are wrong. Check with
`mysql -u root -p -e "SELECT 1"`.

**`Access denied for user ... to database 'sentinel_ip'`** — the user cannot
create databases. Create it yourself and grant:

```sql
CREATE DATABASE sentinel_ip CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'sentinel'@'localhost' IDENTIFIED BY 'a-strong-password';
GRANT ALL PRIVILEGES ON sentinel_ip.* TO 'sentinel'@'localhost';
```

**`RuntimeError: cryptography is required for sha256_password`** — MySQL 8's
default auth plugin. `cryptography` is in `requirements.txt`; make sure the
virtual environment is active.

**`SettingsError: error parsing value for field ... from source "DotEnvSettingsSource"`**
— a comma-separated variable is being typed as a list somewhere. `CORS_ORIGINS`
and `ALLOWED_UPLOAD_EXTENSIONS` are deliberately bound as plain strings and split
by `Settings._split_csv`, because pydantic-settings JSON-decodes list-typed
fields inside the dotenv source before any validator can run. If you add a new
list-shaped setting, follow the same pattern — `tests/test_config.py` guards it.

**`error reading bcrypt version` / `module 'bcrypt' has no attribute '__about__'`**
— that came from passlib, which this project no longer uses. Reinstall from
`requirements.txt`; if the message persists, a stale `passlib` is still on the
path and can be removed (`pip uninstall passlib`).

**`The database already contains cases`** — the seed script refuses to run over
existing data. Use `python scripts/seed.py --reset`.

**Charts are blank** — hard-refresh; `frontend/static/js/vendor/chart.umd.js`
must be served. Nothing is fetched from a CDN.

**Port 8000 is in use** — edit the port in `run.py`, or run
`uvicorn app.main:app --port 8080 --reload`.

**A vendor user sees no cases** — that is usually correct. Their vendor has none.
Sign in as `admin@sentinel.local` to see the whole platform.

---

Built as a working demonstration of the Sentinel IP AI concept: a small team,
four agents, and a human who stays in the loop by construction.
