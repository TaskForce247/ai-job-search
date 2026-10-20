# Local browser application

AI Job Search now has a **single-user website that runs on your own computer**.
React supplies the interface; a loopback-only FastAPI service manages your local
workspace and background work. The small Windows launcher only starts the website,
opens your browser, and provides Reopen and Quit controls. It is not a separate
Windows-native job-search interface, hosted service, or account system.

**Status: development preview.** Deterministic browser journeys use the real local
backend. A passing fixture journey is not evidence of live Claude, real LaTeX,
clean-machine installation, or completed novice-usability acceptance. The release
checks below distinguish these.

## Use the website

With a built Windows x64 portable package, extract the **entire** `AIJobSearch`
folder to a local disk and double-click `AIJobSearch.exe`. Do not separate it from
`_internal`. Normal use needs no Python, Node, terminal, or always-on service.
This repository does not yet publish a signed installer; developers can build the
portable folder using the instructions below.

Follow the browser's guided setup:

1. Use the private default workspace or select an existing framework workspace.
   Importing existing records presents a migration proposal rather than silently
   rewriting their identity links.
2. Build your profile or import a text PDF/Markdown/text CV. Check uncertain facts
   and approve the proposal. Scanned/image-only PDFs need conversion first.
3. In Settings, enter an Anthropic API key and explicitly consent to cloud
   processing. API billing is separate from a Claude subscription. Selected
   profile evidence, posting text, drafts, and rendered document pages can be sent
   to Anthropic when you request AI assistance. There is no subscription-login
   integration and no automatic paid call on startup.
4. Use Settings' guided dependency setup when needed. Bun runs the bundled
   Freehire and opt-in LinkedIn adapters. MiKTeX, the stock-template packages, and
   Poppler support verified PDFs. Setup requires confirmation, can download large
   files, and accepts the displayed provider/source agreements only after consent.
   Repair remains available for missing MiKTeX packages even when its compilers
   are already detected.
5. Search an available source or paste the **complete** job description. Pasted
   postings do not require any portal runtime. Read the assessment and explicitly
   choose whether to prepare documents.
6. Review and edit the CV and cover letter. An edit makes the previous PDF/checks
   stale until recompilation. A skipped or failed check is not a passing document.
7. Submit through the employer's own site, then record the real submission date.
   Preparation is not submission. Submitted document revisions are immutable;
   later edits or outcomes do not rewrite what was sent.

Activity preserves decisions, progress, cancellation, retryable interruptions, and
local artifacts across refreshes. Live events replay by sequence; bounded polling
continues with a visible explanation if streaming is unavailable. Closing a tab
does not stop work. Quit from Settings or the launcher to stop the local service.

## Data and supported scope

The default workspace is `%LOCALAPPDATA%\AIJobSearch\workspace`; its selection is
saved beside it in `workspace.json`. Keep it outside a public Git checkout.
Canonical Markdown, JSON, CSV, LaTeX, and PDFs remain authoritative. `.app` contains
operational SQLite, durable approvals/events, snapshots, preparation artifacts, and
publication/recovery journals. It is not a replacement hosted database.

API keys use Windows Credential Manager, not browser storage or workspace files.
Backup exports exclude credentials. Backups still contain career information:
choose an optional passphrase of at least 12 characters for encryption, and retain
it yourself. Restore previews changes and requires `RESTORE`; reset requires
`RESET`. Both create a safety backup before approved destructive changes.
Replacing/removing the application folder does not remove the separate workspace.

Offline profile access, stored postings, tracking, and existing PDF previews do
not need AI. Network access is necessary for searches, URL retrieval, downloads,
and cloud assistance. There is no UI telemetry or third-party asset CDN.
The local service protects sessions, origins, CSRF, and artifacts, but does not
claim to defend against malware already running as your OS user.

The preview includes guided onboarding/profile editing, search/import/ranking,
full assessments and approvals, reviewed stock CV/cover-letter preparation,
typed editing and PDF.js previews, submission/outcome tracking, dashboard,
workspace migration, backup/restore/reset, and tool readiness/repair.

The portable package includes Freehire and LinkedIn source adapters; LinkedIn
requires a separate terms/access acknowledgement. The four optional Danish
adapters remain available in a source checkout with their declared dependencies,
but are not distributed or advertised as available in the portable package.
Live portal availability is best-effort; a fetch failure is not proof of closure.

Interview preparation UI, upskilling, Gmail/Notion, arbitrary custom templates,
unreviewed portal execution, additional AI providers, macOS/Linux distribution,
and automatic updates are not part of this preview. Existing CLI workflows remain
in place. Unsupported personalized rubric/template changes stop with an
explanation instead of silently substituting stock policy.

## Developer setup

Supported development/build target: Windows x64, Python 3.12, Node 22.12 or newer
compatible Node 22, PowerShell 7, and Microsoft Edge for browser tests.
Run from the repository root:

```powershell
python -m venv app\backend\.venv
& app\backend\.venv\Scripts\python.exe -m pip install -c app\backend\constraints.txt -e ".\app\backend[dev]"
npm.cmd --prefix app\frontend ci --no-fund --no-audit
npm.cmd --prefix app\frontend run build
& app\backend\.venv\Scripts\python.exe -m ai_job_search
```

The source launcher opens the built browser UI on an available loopback port,
using the same one-use bootstrap handshake as the package. It never binds a LAN
interface. `--workspace <local-folder>` and `--port <port>` are developer overrides,
not required setup for end users.

For frontend hot reload, start the source launcher with `--port 8765`, then
`npm.cmd --prefix app\frontend run dev`. The development proxy is loopback-only
and keeps session/CSRF checks. See [frontend development](frontend/README.md) for
the launch-token detail. Business policy still loads from `.claude`; do not add a
second prompt tree to the frontend or backend.

### Build the optional portable launcher

```powershell
& app\packaging\build.ps1
```

This verifies the reviewed resource manifest, builds the frontend, packages the
Python backend/launcher with PyInstaller, exercises the **frozen** server/session/
worker/PDF-renderer/credential-backend/SDK-runtime path without paid AI, then
creates `app\packaging\dist\AIJobSearch-0.1.0-windows-x64.zip` and its SHA-256 file.
`-SkipFrontend` reuses a frontend you have already built. Local evidence is saved
as `app\packaging\work\packaged-smoke.json`.

Only explicitly allowlisted, hash-reviewed framework/template files enter the
package. Modified candidate templates fail the build; personal trackers,
applications, secrets, runtime state, and arbitrary workspace code are excluded.
After a deliberate framework/template update, a maintainer must review that the
resources are pristine and non-personal **before** running:

```powershell
& app\backend\.venv\Scripts\python.exe app\packaging\bundle_resources.py --record-reviewed-templates
```

Never use that command merely to bypass a personalized-template build failure.
Dependency pins are in `pyproject.toml`, `constraints.txt`, and the frontend
lockfile. The package retains unmodified SDK/native PDF libraries, Python/Tcl/Tk,
font notices, and dependency licenses under `_internal\licenses`. Bun, MiKTeX,
and Poppler are separately downloaded, not silently redistributed by the build.
Review provider terms and redistribution requirements before publishing.

## Verification

From the repository root, with the environment and frontend dependencies installed:

```powershell
& app\backend\.venv\Scripts\python.exe -m ruff check app\backend\src app\backend\tests app\backend\export_openapi.py app\packaging\bundle_resources.py app\packaging\local-app.spec
& app\backend\.venv\Scripts\python.exe -m pytest app\backend\tests -q
npm.cmd --prefix app\frontend test
npm.cmd --prefix app\frontend run typecheck
npm.cmd --prefix app\frontend run test:e2e
npm.cmd --prefix app\frontend run test:integration
& app\backend\.venv\Scripts\python.exe -m ai_job_search --smoke-test
```

If the machine's shared pytest temp directory is inaccessible, add `--basetemp`
pointing to a new, task-owned temporary directory; do not change other users'
directory permissions.

Unit/API/storage tests use synthetic data. Browser fixture tests exercise real
PDF.js. The separate integration journey uses real FastAPI, HTTP security,
SQLite/worker claims, approvals, canonical writes, editing, submission/outcomes,
and reload persistence; only AI, credentials, and document compilation/checks are
explicitly injected. It defaults to a unique child of Playwright test output.
`AI_JOB_SEARCH_INTEGRATION_ROOT` can select a private artifact parent; no Copilot
installation or session-specific path is required. No paid provider, OS credential
write, employer, or live portal is contacted.

Regenerate API declarations after endpoint/schema changes:

```powershell
& app\backend\.venv\Scripts\python.exe app\backend\export_openapi.py
npm.cmd --prefix app\frontend run api:generate
```

The separate `Local browser application` workflow runs on Windows, without secrets.
Keep the original framework checks as well:

```powershell
& app\backend\.venv\Scripts\python.exe tools\lint_skills.py
& app\backend\.venv\Scripts\python.exe tools\security_guards.py
& app\backend\.venv\Scripts\python.exe tools\check_framework_version.py
& app\backend\.venv\Scripts\python.exe -m unittest discover -s tests -t . -q
```

### Recorded preview verification

The current Windows x64 preview passed 125 backend tests (one skipped), 61
frontend tests, typechecking, two fixture browser journeys, and the separate real
FastAPI browser journey. The existing framework completed 647 tests with 29
skipped; its skills, security, and version guards passed. Generated API contracts
were unchanged after regeneration.

Both source and frozen launch checks passed, including the actual worker,
approval pause, PDFium page rendering, OS credential-backend availability, native
workspace monitor, bundled SDK executable, and shutdown. Measured startup was
1.890 seconds from source and 2.109 seconds frozen. The 158.9 MiB portable ZIP's
inventory and SHA-256 were checked. The pinned Bun download/checksum and both
included adapters' help commands passed in isolated storage, without installing
tools globally or making live job searches.

With 10,000 synthetic jobs and 1,000 application archives containing two drafts
each, 20 post-initial-read samples through the authenticated ASGI API measured
p95 reads of 106 ms for jobs, 94 ms for applications, and 92 ms for the dashboard;
queue acknowledgement was 205 ms. The initial application index took 4.6 seconds
and its first notification reconciliation took 0.94 seconds: not every individual
request was below 300 ms. These are measurements on this development machine,
not clean-machine or reference-hardware certification.

### Release checks that require a real environment

Passing deterministic checks does **not** complete these specification gates:

- Connect/test Claude through the local settings UI using your own locally stored
  API key, cloud consent, and agreed spending limits. Do not put keys in tests,
  source files, command history, or chat.
- Approve and exercise the real MiKTeX/Poppler installation/repair path. Compile,
  extract text from, and inspect the actual two-page CV and one-page cover letter.
- Install/run the package on a clean supported Windows machine without developer
  tools or a terminal; check native folder selection, browser reopen/quit, and
  document dependency setup. An unsigned build is not a signed installer.
- Measure the specified large-workspace latency and cancellation/recovery bounds
  on the reference hardware; do not infer them from small fixtures.
- Complete manual accessibility checks and the five-person novice usability test
  in the [specification](../docs/local-app-technical-specification.md).

Do not advertise release readiness or universal lossless legacy import until
those checks have evidence.
