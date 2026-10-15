# LogSherlock — Features

Reference for what the software does, feature by feature, and where each piece
lives. For setup see the [README](../README.md); for the design rationale see
[SPEC.md](../SPEC.md).

---

## 1. Input

### 1.1 Paste or upload

`/analyze` accepts a pasted buffer or a `.log` / `.txt` / `.jsonl` file
(`POST /api/v1/investigations`, `POST /api/v1/investigations/upload`).

- `MAX_UPLOAD_MB` (default 5) → `413` when exceeded
- `MAX_LOG_LINES` (default 20000) → `422` when exceeded
- `MAX_LINE_LENGTH` (default 20000) → over-long lines rejected, not silently cut

### 1.2 Bundled sample

`/analyze?sample=1` fetches `/sample.log` from the site root and prefills the
editor. The sample deliberately contains a bearer token, a database password, an
email address, and a prompt-injection attempt, so every defensive path is
visible in one run.

**This file lives in `frontend/public/sample.log` and must stay tracked.** An
earlier blanket `*.log` gitignore rule silently dropped it and every test
fixture with it; see the handoff doc.

### 1.3 Input validation

`validate_input` is the first graph node. It enforces the size and shape limits
and is the only node that can reject an upload outright.

---

## 2. Parsing

`app/logengine/parsers/` — format is detected, then handed to a specific parser.

| Parser            | Handles                                                        |
| ----------------- | -------------------------------------------------------------- |
| `python_logs.py`  | Python `logging`, `Traceback`, exception chains, levels          |
| `nodejs_logs.py`  | Node/Bun single-line and multi-line errors, `Error:` prefixes   |
| `dotnet_logs.py`  | .NET `System.Exception`, HRESULTs, stack frames                  |
| `jsonl_logs.py`   | JSON / JSONL structured events                                   |
| `generic.py`      | Fallback — timestamp, level, service, message                    |

Unrecognised formats fall back to `generic` and the report **says so** under
"Processing notes". A silent fallback would make the parse quality invisible.

Redaction runs during parsing (`redaction.py`) — before anything is stored or
sent to a model:

- bearer tokens, JWTs, `sk-`/API-key shapes
- connection strings and password assignments
- session cookies, email addresses, IP addresses (`REDACT_IP_ADDRESSES`)
- each pattern is recorded in the report so you can see what was removed

**Best-effort by construction.** It cannot catch unusual formats or secrets
split across lines.

---

## 3. Normalisation, grouping, ranking

### 3.1 Normalisation (`normalize.py`)

Replaces what varies, preserves what diagnoses:

| Replaced                                    | Preserved                        |
| ------------------------------------------- | -------------------------------- |
| Timestamps, request/trace ids, UUIDs        | HTTP status codes                |
| IP addresses, durations, sizes, hex blobs   | SQLSTATE codes                   |
| Bare numeric ids, memory addresses          | `ORA-…` / DB error codes         |
|                                             | HRESULTs, exception types        |

The split is deliberate: an unknown error is never merged into a familiar one
just because they share the word `failed`.

### 3.2 Fingerprinting and clustering (`fingerprint.py`, `grouping.py`)

Each normalised line is fingerprinted; identical fingerprints cluster into one
**error group** carrying an occurrence count and a representative sample.

### 3.3 Severity (`severity.py`)

Deterministic, rule-based, and explains itself — each severity carries the
reasons that produced it, shown in the UI. The model **cannot** override it.

### 3.4 Evidence selection (`evidence.py`)

Selects a bounded, prioritised, redacted evidence set under
`MAX_EVIDENCE_CHARS` (default 16000), capped to `MAX_GROUPS_FOR_MODEL` groups.
Truncation is always disclosed in the report, never silent.

---

## 4. AI analysis

### 4.1 Provider abstraction

`BaseChatModel` (`app/models/base.py`) is one interface; the rest of the app
never learns which provider is in use.

| `MODEL_PROVIDER` | Class              | Requires                        |
| ---------------- | ------------------ | ------------------------------- |
| `ollama`         | `ChatOllama`       | Ollama running locally          |
| `google`         | `ChatGoogle`       | `MODEL_API_KEY` (AI Studio)     |
| `openai`         | `ChatOpenAI`       | `MODEL_API_KEY`                 |
| `openai_compat`  | `ChatOpenAICompat` | `MODEL_BASE_URL`                |

`MODEL_BASE_URL` defaults to `None` so each provider uses its own endpoint
rather than inheriting another's.

`GET /api/v1/model/status` reports provider, model and reachability. **Check it
before trusting a report** — if `available` is `false`, no AI analysis ran.

### 4.2 One call per error group

`investigate_with_model` makes **one focused call per error group**, against
that group's own citations. This is the single most important design decision
in the AI path.

The original design — one call for the whole report, asking for a summary plus
N findings of ten fields each — never produced an answer on Gemma: the model
spent its entire output budget reasoning and returned nothing (observed at both
2048 and 16384 tokens). Per-group calls return usable hypotheses with valid
citations, and one bad group cannot invalidate the rest.

Calls run **sequentially with a stagger** (`MODEL_CALL_STAGGER_SECONDS`,
default 1.5s, jittered) rather than concurrently. A burst of identical requests
on one key is what invites rate limits, and a run that gets limited still
consumed quota.

The overall summary is derived **deterministically** — not from the model. That
costs no call and is reproducible.

### 4.3 Validation — the part worth scrutinising

`app/graph/validation.py`, `validate_findings`:

- every cited id (`line-42`) is resolved against lines actually sent to the model
- a fabricated id is rejected
- a finding must cite **its own** group's lines, not a neighbour's
- a finding with no citations is discarded
- severity stays as the backend computed it
- a group the model never answered is **not** retried as though it had failed

When everything fails, findings derived deterministically are used and labelled
`source: deterministic`. Nothing implies the model spoke when it did not.

### 4.4 Bounded repair

`bounded_repair` retries once for findings that failed validation, then gives
up. One attempt, not a loop — an unbounded repair path is a quota leak.

### 4.5 Prompt injection

Logs are untrusted input and may contain instruction-shaped text; the sample
includes one. That text is carried as ordinary log data and can never become an
instruction.

The prompt states this, **but the prompt is not the defence**. Real enforcement
is structural: the model has no tools, cannot cite anything that does not
exist, and cannot change a deterministic fact. Prompt guidance is defence in
depth only.

---

## 5. Report and UI

Routes: `/`, `/analyze`, `/investigations/[id]`, `/settings`.

### 5.1 Live progress

`stream_investigation` uses `astream(stream_mode="values")` — **not** the
default `"updates"`, which yields `{node_name: delta}` and is neither
progress-reportable nor a usable final result.

Each node completion is persisted, so `GET .../status` reports `current_step`
live. This value is a real column on the `investigations` table; it was once
missing from the schema entirely and silently dropped on every write.

`AnalysisProgress.tsx` shows the current stage in plain language ("Analysing
with the model — this is the slow step"), the live step list, and a retry
button.

### 5.2 Evidence drawer

Citations are the signature element. Selecting one opens the real redacted log
line. The panel and its empty-state placeholder share one sticky container —
when they differed, the drawer rendered off-screen while the placeholder had
already left the viewport, which looked like the drawer never opened.

A citation whose line is not in the evidence bundle says so, rather than
silently doing nothing.

### 5.3 Retry

`POST /api/v1/investigations/{id}/retry-ai` re-runs **only** the AI stage.

Raw logs are not persisted, so a retry reuses the stored groups and evidence
rather than re-parsing, and attempts only the groups the previous run did not
explain. The previous report is preserved if the retry yields nothing that
validates. The report exposes `retry_count` and `can_retry`.

### 5.4 Other UI

Markdown export and per-finding copy; light/dark theme via plain CSS custom
property overrides (Tailwind constant-folds both `@theme` and `color-mix`).

---

## 6. Persistence

SQLAlchemy with Alembic. `InvestigationStore` is a seam with two
implementations: `InMemoryStore` and `SqlAlchemyStore`.

- raw logs are **not** written unless `PERSIST_RAW_LOGS=true`
- reports, groups, findings, evidence and processing steps persist
- deleting an investigation cascades to its children
- **SQLite does not survive a redeploy on Render.** Point `DATABASE_URL` at
  managed Postgres to keep history.

The Postgres path is dialect-neutral (SQLite appears only in connection
arguments and `PRAGMA`s) but **has never been run against a real instance**.

---

## 7. Reliability and safety

| Guarantee | Mechanism |
| --------- | --------- |
| Never calls a model under test | `Settings.is_test` — `APP_ENV=test` **or** running under pytest |
| Never returns an unvalidated citation | `validate_findings` rejects before the report is built |
| Never loses the deterministic report | Model failure degrades to `partial`, findings preserved |
| Never silently truncates evidence | Truncation disclosed in the report |
| Never invents a severity | Model output cannot override the deterministic value |
| Bounded spend | `MAX_GROUPS_FOR_MODEL`, staggered calls, one repair attempt |
| Bounded concurrency | `MAX_CONCURRENT_INVESTIGATIONS` → `429` |
| No silent provider fallback | Only the configured provider is ever contacted |

---

## 8. Test coverage

189 tests, no network, in ~4s.

| Area | Covers |
| ---- | ------ |
| Parsers | Per-format output, fallback behaviour |
| Normalisation / grouping / severity | Cluster correctness, severity reasons |
| Redaction | Secret removal, privacy guarantees |
| Graph | Full workflow, validation, bounded repair |
| API | Report quality, citation integrity, concurrency, failure paths |
| Persistence | Round trips, cascade delete, live progress fields, restart |

The provider is injected, so AI paths are covered by scripted responses
including invalid JSON, fabricated citations, timeouts and provider outages.

**These fixtures (`samples/*.log`) must stay tracked.** They were gitignored
once; the suite passed locally and could not run on a fresh clone.