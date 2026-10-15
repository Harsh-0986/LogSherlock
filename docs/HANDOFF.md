# Handoff

For whoever picks up LogSherlock next. Current state, what is actually
verified, and the things that will bite you.

Last updated: 15 October 2026.

---

## 1. What this is

Paste a log file, get a debugging report where every AI claim is tied to the
log line supporting it. Deterministic grouping runs first; the model is
strictly an enhancement.

Repo: `github.com/Harsh-0986/LogSherlock` · Licence MIT · Python 3.12+,
Node 20+, `uv`.

## 2. Running it

```bash
cd backend && cp .env.example .env && uv sync --extra dev
uv run alembic upgrade head && uv run uvicorn app.main:app --reload

cd ../frontend && npm install && npm run dev
```

Works with **no model configured** — you get the full deterministic report and
`status: partial`. That is a supported workflow, not an error state.

## 3. Architecture in one diagram

```
paste log
   │
   ├─ validate_input        limits
   ├─ parse_logs            detect → parse → redact
   ├─ normalize_and_group   strip variables → fingerprint → cluster
   ├─ rank_errors           deterministic severity
   ├─ select_evidence       bounded, prioritised, redacted
   ├─ investigate_with_model   ONE CALL PER GROUP, staggered
   ├─ validate_findings     every citation resolved against real lines
   │    └─ (invalid) bounded_repair    one attempt only
   ├─ persist_report
   └─ report → JSON → UI
```

`app/store/base.py` is a seam: swap `InMemoryStore` for `SqlAlchemyStore`
without touching the graph.

## 4. Design decisions you must not undo casually

**Deterministic first, AI second.** If the model is unreachable you still get a
correct report. This is the product, not a degraded mode.

**One model call per error group.** A single whole-report call never worked on
Gemma — it spent the entire output budget reasoning and returned nothing. This
was verified by failure, not theory.

**Sequential calls with a stagger.** Concurrent fan-out on one API key invites
rate limits, and a limited run still burns quota.

**The summary is computed, not generated.** Costs no call, reproducible.

**The prompt is not the injection defence.** Structure is: no tools, no
citations that don't exist, no ability to change a deterministic fact. Keep the
prompt guidance as-is and do not treat it as security.

**Citations are validated, never trusted.** This is the whole point. If you
relax `validate_findings`, the product is worthless.

---

## 5. Verified vs not

Be precise about this; it has been misreported before.

**Verified**

- 189 tests, no network, ~4s (`cd backend && uv run pytest`)
- ruff, `tsc --noEmit`, `eslint --max-warnings=0` clean; production build green
- SQLite round trips, cascade delete, live `current_step` persistence
- AI path confirmed working end to end **once** — 3 groups, real findings with
  valid citations, `status: completed`
- Deployed: frontend on Vercel, backend blueprint in `render.yaml`

**Not verified — treat as untested**

- **Postgres.** No real instance has ever been run. Dialect-neutral in theory.
- **The AI path beyond that one run.** Intermittent Google `500 INTERNAL` was
  seen on 9 of 11 calls in one full run. Per-group design degrades gracefully,
  so this is survivable, but it is flaky.
- **The evidence drawer in a browser.** The sticky-container bug was diagnosed
  by reading CSS, not by clicking. Please click a citation before trusting it.
- **A fresh clone.** Never actually run end to end; see §6.
- **No browser/E2E suite.**

## 6. The gitignore trap — please read this

The root `.gitignore` has a blanket `*.log`, meant for runtime logs. It
silently swallowed three files:

- `frontend/public/sample.log` → `/analyze?sample=1` 404s
- `samples/sample-node-api.log` → `tests/conftest.py` reads it
- `samples/sample-python-worker.log` → same

**The consequence was that "189 tests passing" was a property of one machine,
not of the repository.** Anyone cloning it, and any CI run, got a suite that
could not even collect.

Fixed with scoped negations. **If you add a fixture to `samples/`, verify it
is tracked:**

```bash
git check-ignore samples/your-new-file.log && echo "BROKEN"
```

## 7. Test safety — do not undo

`Settings.is_test` returns True when `APP_ENV=test` **or** running under
pytest, and `JobManager._resolve_model` returns a no-op model when it is True.

This exists because a test that sets `APP_ENV=development` (to reach the
SQLAlchemy store) will otherwise build a real provider client and **call a paid
API**. That happened once during this build.

If you need SQLAlchemy in a test, do **not** flip `APP_ENV`. Inject
`model_factory=lambda _s: _NoModel()` into `JobManager` instead.

`APP_ENV=test` also selects `InMemoryStore`, so the two concerns are coupled —
that coupling is the trap.

## 8. Known open issues

1. **SQLite is ephemeral on Render.** Investigations 404 after a restart or
   redeploy. Needs a managed Postgres `DATABASE_URL`. *(User is aware.)*
2. **Retry re-attempts groups that keep failing.** Worth tracking per-group
   failures and skipping after N.
3. **No CI.** Nothing runs on push. The suite is fast — worth wiring up.
4. **`MODEL_MAX_OUTPUT_TOKENS` conflates context length with output budget.**
   The user set it to 262144, which gives a thinking model's reasoning phase an
   unbounded runway. Gemma is a thinking model and `thoughts_token_count` draws
   from the same allowance. 256k *context* governs input size only.
5. **`npm audit` reports 5 high-severity advisories**, all dev-only, in the
   ESLint toolchain. `--force` breaks the build.
6. **In-process job manager.** A restart loses running investigations; multiple
   workers each get their own queue. `InvestigationStore` exists so a durable
   queue can replace it.

## 9. Where to start

**To add a parser:** `app/logengine/parsers/`, register in `__init__.py`,
add a fixture to `samples/`, check it is tracked.

**To change AI behaviour:** `app/graph/nodes.py` →
`investigate_with_model`, and `app/models/prompts.py`. Keep the per-group
structure. Check `MAX_GROUPS_FOR_MODEL` and the stagger still bound your change.

**To add an endpoint:** `app/api/v1/investigations.py` + `app/api/schemas.py`.
One error envelope: `{"error": {"code", "message", "details"}}`.

**To change the UI:** `frontend/src/app/investigations/[id]/page.tsx` holds the
report; components are in `frontend/src/components/`.

## 10. Before you call anything done

```bash
cd backend
uv run ruff check app tests
uv run pytest              # must be 189, no network
cd ../frontend
npx tsc --noEmit && npx eslint src --max-warnings=0 && npm run build
```

Then actually click a citation and confirm the evidence drawer opens.