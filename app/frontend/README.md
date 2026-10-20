# Local application frontend

React, TypeScript, Vite, TanStack Query, React Hook Form, and a locally bundled
PDF.js worker. All production requests use the same-origin `/api/v1` service.
There are no mock records, external assets, browser-stored credentials, or
automatic application submissions in the production app.

## Development

Run these commands from `app\frontend`:

```powershell
npm ci
npm run dev
```

The development server binds to `127.0.0.1:5173` and proxies `/api` to the local
backend at `http://127.0.0.1:8765`. `AI_JOB_SEARCH_API_URL` can select another
**loopback-only** backend. The proxy rewrites Host and an explicitly matching
local development Origin; foreign origins are left for the backend to reject.
Session cookies, CSRF, and idempotency checks remain required. Open the
launcher-issued bootstrap fragment on the development origin to connect.

For normal use, build the app and let the local launcher/backend serve `dist`.
The end user does not need Node, a development server, or terminal commands.

```powershell
npm run api:generate
npm run typecheck
npm test
npm run test:e2e
npm run build
```

`api:generate` reads `..\backend\openapi.json` and writes the generated API
declarations in `src\api\schema.d.ts`. Regenerate after backend schema changes.
UI models refine open-ended presentation fields and normalize known-empty
profile/document fields; server calculations remain authoritative.

Unit/component tests use isolated fetch fixtures. Playwright tests use local
API fixtures and real PDF.js rendering, including its bundled worker. They
start and stop their own loopback Vite server and require Microsoft Edge
(`channel: msedge`). No live provider, job board, or candidate data is used.

## Real FastAPI integration journey

The separate integration test serves the **built** frontend from the real
FastAPI application. It does not intercept or mock HTTP. It exercises the
native, single-use launch-token artifact, cookie bootstrap/reload, real CSRF and
idempotency enforcement, SQLite queue claims and approval checkpoints, canonical
profile/job/archive/tracker writes, full evaluation, document editing and local
PDF.js rendering, submission recording, outcomes, and reload persistence.

From `app\frontend`:

```powershell
npm run test:integration
```

This requires `..\backend\.venv\Scripts\python.exe` with the backend's test
dependencies installed, plus Microsoft Edge. The command builds first, starts
its own loopback server on an available port, runs one bounded journey, stops the
server, and removes its uniquely named synthetic workspace/resource bundle.
By default data lives under Playwright's test output; optionally set
`AI_JOB_SEARCH_INTEGRATION_ROOT` to a private artifact parent. No Copilot-specific
path is required. `AI_JOB_SEARCH_INTEGRATION_PORT` can choose a fixed port.
The fixture refuses to reuse an existing data directory and cleans only the
unique directory it created.

Only the **test-only** Python server in `integration\server.py` injects the
backend's deterministic `FixtureRuntime`, in-memory `Credentials`, and synthetic
document compilation/checks. Live AI and external posting fetches are explicitly
blocked. The PDFs are real and rendered by the actual bundled PDF.js worker,
but their checks are labeled **Integration-fixture**: this is **not** proof of
real LaTeX compilation, live Claude, genuine factual/visual review, OS keyring
storage, or contact with a real employer. No repository candidate file is read
or changed; candidate-owned resource files start as synthetic blank skeletons.
The server log and a queue/check-fixture report are attached to Playwright
results before cleanup. Existing HTTP-fixture browser tests are unchanged.

## Safety and interaction rules

- A 401 shows “reopen from your app shortcut,” not an account-login form.
- Every mutation has a fresh idempotency UUID and in-memory CSRF token;
  mutations are not automatically retried.
- Imports, profile changes, rewrites, resets, and restores are reviewable
  proposals. Destructive approvals require `RESET` or `RESTORE`.
- Preparing, drafted/not sent, and applied are distinct states.
- Editing content hides stale PDFs and invalidates checks until recompilation.
- Posting content is escaped plain text. External links permit HTTP(S) only.
- Read-only records remain usable when AI is disconnected. Cloud processing and
  separate API billing are explained before consent.
