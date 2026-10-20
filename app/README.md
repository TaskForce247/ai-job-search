# Run the local browser app

AI Job Search is a **single-user website running on your computer**: a Python
FastAPI backend and a React frontend. Start them from terminals, then use the
browser for your job search. No Windows desktop app, Docker, or interactive AI
CLI is required.

**Status: development preview.** Source startup is documented for Windows,
macOS, and Linux. Automated verification has been performed on Windows; the
other operating systems still need real-environment acceptance.

## Prerequisites

- A checkout or extracted copy of this repository.
- **Python 3.12** with `venv` and `pip`.
- **Node.js 22.12 or a newer compatible Node 22**, including npm.
- A modern browser.
- An unlocked operating-system credential store for AI: Windows Credential
  Manager, macOS Keychain, or a Linux Secret Service keyring in your desktop
  login session. Plaintext credential backends are rejected.

You do not need to install or operate Claude Code separately. The backend's
Claude Agent SDK dependency supplies its runtime. AI features require an
Anthropic API key, entered **in the browser Settings**, and explicit cloud
processing consent. API billing is separate from a Claude subscription.

Bun and document tools are optional for initial startup; see
[Job search and PDF tools](#job-search-and-pdf-tools).

## 1. Install dependencies once

Open a terminal in the **repository root**. There is no need to activate the
virtual environment when using the explicit Python paths below.

### Windows PowerShell

```powershell
python -m venv app\backend\.venv
& .\app\backend\.venv\Scripts\python.exe -m pip install -c .\app\backend\constraints.txt -e ".\app\backend"
npm.cmd --prefix app\frontend ci --no-fund --no-audit
```

### macOS / Linux

```bash
python3.12 -m venv app/backend/.venv
app/backend/.venv/bin/python -m pip install -c app/backend/constraints.txt -e ./app/backend
npm --prefix app/frontend ci --no-fund --no-audit
```

Keep the checkout: it contains the canonical workflow specifications, templates,
fonts, and portal adapters used by the editable backend installation.

## 2. Start the frontend

In **terminal 1**, from the repository root:

Windows PowerShell:

```powershell
npm.cmd --prefix app\frontend run dev
```

macOS / Linux:

```bash
npm --prefix app/frontend run dev
```

Leave it running. Vite listens at **http://127.0.0.1:5173** and proxies API
requests to the backend at **http://127.0.0.1:8765**.

## 3. Start the backend

In **terminal 2**, also from the repository root:

Windows PowerShell:

```powershell
& .\app\backend\.venv\Scripts\python.exe -m ai_job_search --frontend-port 5173
```

macOS / Linux:

```bash
app/backend/.venv/bin/python -m ai_job_search --frontend-port 5173
```

The backend stays in this terminal; it does not open a desktop window. It binds
only to `127.0.0.1:8765` and prints a browser link like:

```text
http://127.0.0.1:5173/#token=<one-use-login-token>
```

**Open the actual link printed in terminal 2 within 90 seconds.** Opening the
bare frontend URL does not establish the initial session. The frontend removes
the token from the URL and exchanges it for an HttpOnly local session cookie.
Keep the link private; it is not an API key or application account.

Start the frontend first so it is ready before the backend issues the link.
Use **127.0.0.1**, not `localhost`, in both services and the browser: session
cookies are host-specific. Reloading or reopening the plain frontend URL works
in the same browser while the session remains valid.

If the link expires, was already used in another browser, or the session expires,
restart the backend and use its fresh link. Stopping interrupts active work and
preserves saved checkpoints; an interrupted AI request may still incur charges.

## Stop and restart

- Press **Ctrl+C in the backend terminal** to stop its server and owned workers.
  Settings also has a confirmed **Stop backend** action.
- Press **Ctrl+C in the frontend terminal** to stop Vite.
- Closing the browser alone stops neither service.
- To resume, start the two services again and open the new terminal-issued link.
  Your saved workspace is not deleted by stopping either service.

## Drafts, review and recovery

For stopped document preparation, **Resume checks** reuses saved drafts and
binds the new operation to the application's current saved version. It does not
reuse the version from before the drafts were saved. Later edits still stop
preparation rather than being overwritten. Retries may incur additional API
charges, and all required review and verification must still pass.

Evidence links are checked locally before each independent review. Missing or
invalid links enter the existing three-cycle repair limit, within your configured
AI limits. Evidence repairs are saved as unverified before review, and rejected
reviews are not replayed as successful retry checkpoints.

The application workspace opens readable **CV** and **Cover letter** drafts
before a PDF exists. **Checks** shows outstanding issues, supporting source text,
proposed corrections, and actual PDF check results. Content findings pause as
**Needs review** rather than a technical failure. Conservative wording
corrections are independently rechecked and shown as a before/after proposal;
only **Accept corrections & continue** replaces the saved draft. Missing facts
are never added to Profile automatically. You can instead edit and save the
draft yourself, then choose **Resume checks**.

Resume binds to the current saved version and reuses a completed factual review
only when its documents, profile, posting, assessment, policy and verification
date still match. A changed input requires the relevant checks again. Company
claims are independently verified before the final factual gate; assessment
wording warnings remain separate from the actual documents. Compilation and
rendered-page inspection must still pass before PDFs are considered verified.

The workspace shows the recorded API cost across document attempts and the
configured limit for the next operation. Acceptance and continuation can still
use the AI connection; interrupted requests may have unrecorded provider charges.
**Latest operation** includes stopped attempts, and Activity links to both drafts
even if the operation failed. None of these actions submits an application.

## Alternative: one server with a built frontend

For ordinary use without hot reload, build the frontend once, then run only
the backend. Rebuild after changing frontend code.

Windows PowerShell:

```powershell
npm.cmd --prefix app\frontend run build
& .\app\backend\.venv\Scripts\python.exe -m ai_job_search
```

macOS / Linux:

```bash
npm --prefix app/frontend run build
app/backend/.venv/bin/python -m ai_job_search
```

Open the backend's printed login link, now at **http://127.0.0.1:8765**. No Vite
server is needed in this mode.

## Workspace and first use

The default workspace is separate from the code:

| OS | Default location |
|---|---|
| Windows | `%LOCALAPPDATA%\AIJobSearch\workspace` |
| macOS / Linux | `~/.local/share/AIJobSearch/workspace` |

In the browser, follow guided setup to confirm your profile and preferences.
To switch workspaces, enter a **full absolute folder path** in Setup or Settings
and confirm the change. New workspaces require an empty folder. Existing records
are inspected separately and changes require approval. There is no native folder
picker.

You can also select a workspace when starting the backend:

```powershell
& .\app\backend\.venv\Scripts\python.exe -m ai_job_search --frontend-port 5173 --workspace "C:\Users\YourName\JobSearch"
```

On macOS/Linux, use the same `--workspace` option with an absolute local path,
such as `"$HOME/JobSearch"`. A browser-selected workspace is remembered in
`workspace.json` beside the default workspace; `--workspace` overrides that
selection for the current launch. Never use a public checkout for personal data.

Canonical Markdown, JSON, CSV, LaTeX, and PDFs remain the business records.
`.app` contains private operational SQLite, approvals, events, artifacts, and
publication/recovery journals. Backups exclude API keys but still contain career
information; encryption is optional and the passphrase must be retained by you.

Profile access, stored postings, tracking, and existing PDF previews work without
AI. Searches, URL imports, and cloud assistance need a network connection.
Preparing documents never records submission: submit on the employer's website,
then record the actual submission date. Submitted document revisions are frozen.

## Job search and PDF tools

| Capability | Additional dependencies |
|---|---|
| Paste a posting, edit your profile, track applications | None beyond the app prerequisites |
| AI assessments and drafting | Anthropic API key, cloud consent, and spending limits in Settings |
| Search bundled job sources | [Bun](https://bun.sh/); Danish adapters also need their npm dependencies below. LinkedIn requires its explicit terms/access acknowledgement |
| Compile stock CV and cover letter | LuaLaTeX and XeLaTeX from [MiKTeX](https://miktex.org/), [TeX Live](https://tug.org/texlive/), or [MacTeX](https://tug.org/mactex/) |
| Full PDF layout verification | Poppler `pdftotext` and `pdfinfo` |

Install tools on `PATH` **before starting the backend**, or restart it after
installation. Stock templates need the packages listed in
[minimal TeX setup](../SETUP.md#minimal-tex-install-tinytexbasictex), including
`moderncv`, `fontawesome5`, `fontspec`, and `needspace`. Fonts for the cover
letter are already in the repository. Compilation never enables shell escape
or opens hidden package-install dialogs.

Settings shows actual tool readiness. Existing consent-based Windows dependency
repair is still available where supported; on macOS/Linux use the providers'
installation instructions. Finding compiler executables is not proof that a
document passed compilation or verification. Failed and skipped checks remain
visible.

Freehire and LinkedIn have zero runtime npm dependencies. Other bundled portal
adapters need their declared dependencies installed and their source enabled;
install them once from the repository root:

Windows PowerShell:

```powershell
# Also find Bun if it was installed through the app's Settings.
$env:PATH = "$env:LOCALAPPDATA\AIJobSearch\runtimes\bun;$env:PATH"
& .\app\backend\.venv\Scripts\python.exe .\tools\install_portal_clis.py
```

macOS / Linux, with Bun on `PATH`:

```bash
app/backend/.venv/bin/python tools/install_portal_clis.py
```

The installer discovers the portal manifests and skips unsafe lifecycle hooks.
After installation, choose **Settings -> Refresh sources** (or reload the page).
**Enabled - setup required** means a source is selected but its local tools or
access acknowledgement are incomplete. **Enabled - ready to search** means
local setup is complete, not that a live request has succeeded. Source health
starts as **Not searched yet** and records the result when you run a search.

## Troubleshooting

| Symptom | Action |
|---|---|
| Browser asks for a local session | Open the fresh login link from the backend terminal, not just the bare website URL |
| `localhost` opens but authentication fails | Use `127.0.0.1` consistently |
| Backend says its port is occupied | Stop your previous backend, or use `--port` and update the Vite proxy target with `AI_JOB_SEARCH_API_URL` |
| Frontend port 5173 is occupied | Stop your previous Vite server, or use `run dev -- --port <port>` and the matching backend `--frontend-port` |
| Backend says the UI is not built | Start Vite and use `--frontend-port 5173`, or build the frontend and omit that option |
| AI settings report an unavailable credential store | Unlock your OS keyring; on Linux run from your desktop login session with a Secret Service provider |
| A dependency remains missing | Check `PATH`, restart the backend, and refresh readiness in Settings |

For a non-default backend port, set `AI_JOB_SEARCH_API_URL` in the frontend
terminal **before** starting Vite. The proxy accepts loopback targets only.
Do not run bare Uvicorn as a substitute for the documented backend command:
the app entrypoint handles startup, the login token, workspace ownership, and
graceful shutdown.

## Development checks

Install the backend's test tools with the same `pip install` command above,
replacing `-e ".\app\backend"` with `-e ".\app\backend[dev]"` (or
`-e './app/backend[dev]'` on macOS/Linux).

From the repository root on Windows:

```powershell
& .\app\backend\.venv\Scripts\python.exe -m ruff check app\backend\src app\backend\tests app\backend\export_openapi.py
& .\app\backend\.venv\Scripts\python.exe -m pytest app\backend\tests -q
npm.cmd --prefix app\frontend test
npm.cmd --prefix app\frontend run typecheck
npm.cmd --prefix app\frontend run test:e2e
npm.cmd --prefix app\frontend run test:integration
npm.cmd --prefix app\frontend run build
& .\app\backend\.venv\Scripts\python.exe -m ai_job_search --smoke-test
```

For an inaccessible shared pytest temp directory, add `--basetemp` with a new
task-owned temporary path; never change another user's permissions. Regenerate
API declarations after schema changes:

```powershell
& .\app\backend\.venv\Scripts\python.exe app\backend\export_openapi.py
npm.cmd --prefix app\frontend run api:generate
```

Backend and browser tests use synthetic data. The real FastAPI browser journey
exercises sessions, queue claims, approvals, canonical writes, editing, and
tracking; AI and compilation/checks are explicitly synthetic. See
[frontend development](frontend/README.md) for test prerequisites. Keep the
original framework's skills, security, version, and Python checks as well.

Passing these checks is not evidence of live paid Claude, real stock-template
compilation/visual verification, clean macOS/Linux setup, accessibility, or
completed usability acceptance. Those remain separate release checks in the
[technical specification](../docs/local-app-technical-specification.md).
