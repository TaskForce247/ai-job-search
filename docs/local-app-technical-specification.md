# Technical Specification: Local AI Job Search Application

**Status:** Implementation specification v0.2; [source startup and release gates](../app/README.md)

**Date:** 2026-10-20

**Basis:** Analysis of this repository and its canonical workflow specifications

**Confirmed deployment:** One user, on their own computer, using a browser interface after basic command-line setup. No desktop app or Docker distribution.

## 1. Product definition

Build a local website that makes the existing job-search framework usable without knowing AI prompts, slash commands, or how to operate an interactive AI CLI. Users can install documented dependencies and start local servers from a terminal.

The user follows the README to install Python/Node dependencies, starts a headless backend and a separate Vite frontend, and opens the backend's terminal-issued login link. Alternatively, the backend serves a built frontend with no Vite process. All job-search workflows use guided browser screens. A Windows executable, native desktop launcher, Docker, hosted application server, and application account are not required.

The 2026-10-20 deployment decision supersedes the original no-terminal/Windows-launcher requirement. Docker was considered and explicitly deferred; it must not become a hidden setup prerequisite.

**This is not a SaaS specification.** There are no tenants, organizations, cloud application databases, shared job queues, or subscription-management features.

### 1.1 What "local" means

| Concern | Required behavior |
|---|---|
| Interface | Runs on the user's computer and is served only over loopback. |
| Workflow execution | Runs in local application processes. |
| Profile, documents, and application history | Stored in a user-controlled local workspace. |
| Job-board access | Makes outbound requests when the user searches or imports a posting. |
| AI inference | Initial recommendation: an explicitly configured Claude API connection, matching the existing Claude-based methodology. Selected context is sent to that provider. |
| Offline use | Existing profiles, postings, documents, and application records remain accessible. Network-dependent actions explain that a connection is required. |
| Fully local AI models | A possible later runtime adapter, not an assumed capability of the current repository or the first release. |

Local execution must never be described as "your information never leaves your computer" when a cloud AI provider is enabled. The interface must explain the distinction before the first AI request.

### 1.2 Primary user journey

1. Follow the README to start the website, check dependency readiness, and configure the AI connection.
2. Import a CV or existing workspace, or build a profile through a questionnaire.
3. Confirm extracted facts and choose job preferences.
4. Search supported sources or paste a job URL or description.
5. Read an understandable fit assessment, including weaknesses and restrictions.
6. Explicitly choose whether to prepare application documents.
7. Review and edit the tailored CV and cover letter, with live PDF previews and verification results.
8. Open the employer's application page and submit outside the application.
9. Record submission, interview stages, offers, and outcomes.

The product assists the applicant; it does not submit applications or contact employers automatically.

## 2. Repository analysis

### 2.1 Existing assets and their application role

| Existing asset | What is already present | How the application should use it |
|---|---|---|
| `AGENTS.md` and `CLAUDE.md` | Thin-pointer architecture, candidate context, workflow expectations, verification requirements | Preserve the canonical-source model; expose candidate information through forms rather than file editing. |
| `.claude\commands\setup.md` | Document import, single-CV import, guided interview, conflict resolution, profile population | Onboarding wizard and profile-update workflows. |
| `.claude\skills\job-application-assistant\01-*.md` through `09-*.md` | Candidate facts, behavioral preferences, writing style, fit rubric, templates, interview guidance, form answers, research rules | Versioned policy and candidate context for the runtime bridge. |
| `.claude\skills\job-scraper\SKILL.md` and `search-queries.md` | Portal discovery, search strategy, deduplication, provenance, freshness and health reporting | Jobs screen, search configuration, source-status reporting. |
| `.agents\skills\*\cli\` | Six Bun/TypeScript portal CLIs with structured search/detail interfaces and fixture tests | Reuse through validated local subprocess adapters. |
| `.claude\commands\rank.md` | Batch triage, explanations, gates, expiry and fetch-failure handling | Ranked job list; clearly distinguish triage from a full application evaluation. |
| `.claude\commands\apply.md` | Fit approval, drafting, independent review, revision, compilation, verification, recording | Application workspace and its approval-driven progress interface. |
| `.claude\commands\outcome.md` and `documents\README.md` | Tracker status vocabulary, follow-up rules, outcome archive format | Application pipeline, timeline, follow-up drafts, submission recording. |
| `.claude\commands\interview.md` | Stage-specific preparation using submitted materials and earlier feedback | Interview preparation screen. |
| `.claude\skills\upskill\SKILL.md` | Gap analysis and learning reports | Learning screen in a subsequent release. |
| `.claude\commands\expand.md` | Source-backed profile enrichment with confirmation | Optional profile-enrichment action. |
| `.claude\commands\html-report.md` | Offline dashboard requirements and application-funnel semantics | Reuse metric definitions; retain the portable HTML export. This is a generation specification, not an existing interactive frontend. |
| `.claude\commands\gmail-sync.md` and `notion-sync.md` | Approval-based email classification and one-way pipeline publishing | Optional integrations after the local core is complete. |
| `tools\job_key.py` | Stable job keys and state auditing | Reuse identity and deduplication behavior rather than implementing a second slug function. |
| `tools\rank_state.py` | Candidate selection, weighted aggregation, atomic JSON replacement, expiry and retry state | Reuse deterministic ranking/state behavior behind the application service. |
| `tools\verify_pdf.py` and `verify_layout.py` | Page counts, text extraction, date checks, measurable layout checks | Verification pipeline and user-readable results. |
| `tools\robots_check.py` | Conservative robots-policy gate for retrieval retries | Retrieval service; never replace a blocked source with aggressive scraping. |
| `salary_lookup.py` | Optional lookup against a user-provided local dataset | Optional salary panel; not a mandatory dependency. |
| `cv\` and `cover_letters\` | Stock moderncv CV, cover-letter class, font assets | Initial supported document templates. |
| `tests\` and `.github\workflows\ci.yml` | Python tests, specification-contract tests, portal checks, template smoke builds | Preserve these checks and add application-level coverage. |

### 2.2 Baseline at the initial repository analysis

At the initial 2026-10-19 analysis, there was no web application, HTTP API, browser UI, application launcher, durable UI-facing workflow service, or frontend dependency manifest. The implementation now lives under `app\`; its current scope and verification limits are documented in the application guide above.

Most of the application intelligence is expressed as Markdown instructions interpreted by an interactive AI runtime. These files are not ordinary Python functions or callable API endpoints. The executable pieces are primarily portal CLIs and deterministic Python utilities.

The checked-out candidate profile still contains placeholders. The root `job_scraper\` directory contains only `.gitkeep`, and there is no root `job_search_tracker.csv`. There is no populated business state in those inspected locations to use as an application sample. Personal state is intentionally excluded from version control in several locations.

Consequently, the required work is **a frontend plus a thin local application/runtime layer**, not merely a visual skin over an already implemented backend.

### 2.3 Constraints that affect the design

- Preserve `.claude\` as the canonical workflow and methodology source. Do not copy the workflow prompts into frontend components or maintain an independent prompt tree.
- Current business records are Markdown, JSON, CSV, LaTeX, and PDFs. A database migration is not necessary merely to add a single-user UI.
- Some helpers resolve data paths from their own file location, not the current working directory. Installed application resources and a user's workspace must therefore be separated deliberately.
- The tracker distinguishes `drafted` from `applied`. Generating documents must never imply that an application was sent.
- Job status, tracker status, archive outcome status, and workflow execution status are different concepts. They must not share one generic status field.
- Existing archive naming can collide when applying again to the same company and role. The application needs stable record identity and an explicit compatibility plan.
- Four Danish sources ship disabled; LinkedIn and freehire ship enabled. freehire is tech-focused and best-effort. LinkedIn carries an explicit automated-access/terms warning.
- PDF creation requires real toolchains and fonts. Installation, non-interactive compilation, and preview are product requirements, not instructions to leave to the user.
- Existing Markdown/contract tests do not establish that a programmatic, UI-driven agent workflow works end to end.
- `CONTRIBUTING.md` excludes duplicate orchestration layers from the original upstream project's scope. Treat this application as a product layer in this fork, while preserving canonical references and an upgrade path.

## 3. Scope and priorities

### 3.1 MVP

| Capability | Required outcome |
|---|---|
| Launch and readiness | Document separate backend/frontend commands and the one-server alternative; detect and explain missing dependencies. |
| Workspace onboarding | Create a local workspace or import an existing one without overwriting it silently. |
| Profile | Guided import/questionnaire, editable career information, preferences, languages, and confirmed facts. |
| Jobs | Search supported portals, paste a posting, filter results, view provenance, deduplicate, rank, and save interest. |
| Application preparation | Evaluate first; obtain approval; draft, independently review, revise, compile, and verify documents. |
| Review | Edit document content without LaTeX knowledge; preview PDFs; inspect reasons, gaps, and validation findings. |
| Tracking | Record actual submission, stages, notes, deadlines, offers, and outcomes. |
| Reliability | Durable progress, approval cards, cancellation, retry, crash recovery, conflict detection. |
| Local data controls | Open the workspace, export data, back up, restore, and reset selected data with explicit confirmation. |

### 3.2 Subsequent releases

- Interview preparation and text-based mock interviews.
- Follow-up and thank-you drafts, respecting existing limits and approval rules.
- Profile enrichment, learning plans, and calibration from outcomes.
- Portable HTML dashboard export.
- Gmail and Notion integrations with explicit connection and data-sharing consent.
- Vetted custom templates and additional portal adapters.
- A separately evaluated local-model adapter.

### 3.3 Explicit non-goals

- Hosted services, shared accounts, team collaboration, payments, or multi-user administration.
- Autonomous application submission, employer messaging, or email sending.
- CAPTCHA solving, login-wall circumvention, or mass scraping.
- An unrestricted "run any command" chat box.
- Automatically installing or executing arbitrary portal plugins or document toolchains.
- Guaranteed job coverage, an interview probability, or an "ATS acceptance" guarantee.
- OCR and DOCX ingestion in the initial release.
- Native desktop applications, executable installers, and Docker/container distribution.

## 4. User experience and frontend

### 4.1 Interaction principles

The primary interaction model is **screens, forms, previews, and explicit actions**, not a blank chat window.

Use labels such as "Find jobs," "Check this job," "Prepare documents," and "Record a response." Do not expose `/scrape`, `/rank`, or `/apply` as concepts the user must learn.

Contextual conversation can supplement a screen, for example "Make this paragraph more direct" or "I managed three people, not ten." It must not be the only way to navigate, correct data, or resume work.

Every expensive or long-running action must show its current stage, completed work, any required decision, and a cancel action. Show real stage progress rather than an invented percentage or completion time.

### 4.2 Navigation and routes

| Screen | Proposed route | Main functions |
|---|---|---|
| First-run setup | `/welcome` | Workspace selection, readiness checks, AI connection and data-sharing explanation. |
| Dashboard | `/` | Next actions, drafts awaiting review, upcoming deadlines/interviews, pipeline summary. |
| Profile | `/profile` | Career history, evidence, skills, languages, goals, constraints, writing preferences. |
| Jobs | `/jobs` | Search form, results, shortlist, filtering, source health. |
| Job detail | `/jobs/:jobId` | Full posting, source, freshness, fit explanations, restrictions, application action. |
| Application workspace | `/applications/:applicationId` | Evaluation, documents, verification, submission, timeline. |
| Application pipeline | `/applications` | Table and board views; status and deadline filters. |
| Activity | `/activity` | Running, queued, interrupted, and completed operations; pending decisions. |
| Settings | `/settings` | AI connection, sources, workspace, backups, usage limits, diagnostics. |
| Interview preparation | `/applications/:applicationId/interview` | Later release: stage-specific preparation and practice. |
| Learning | `/learning` | Later release: evidence-backed gaps and learning plans. |

Routes are browser routes, not filesystem paths. Internal filesystem paths remain hidden behind document and record identifiers.

### 4.3 Onboarding

The wizard must:

1. Offer "Start a new workspace" and "Use an existing workspace."
2. Explain which data is stored locally and which selected content may be sent to AI services.
3. Run readiness checks with plain-language states: ready, optional, setup required, or unavailable.
4. Offer CV/document upload or a guided questionnaire. Existing workspace imports show discovered data before changing anything.
5. Extract proposed profile facts and show conflicts side by side with their source.
6. Require confirmation of facts before they become drafting evidence.
7. Collect target roles, location/remote preferences, commute constraints, working languages and levels, CV language, and hard restrictions.
8. Save progress locally so setup can be paused and resumed.

Initial import formats: text-readable PDF, Markdown, and plain text. Existing LaTeX sources may be read as data when importing a workspace; uploaded LaTeX is not executed. Image-only PDFs receive an actionable warning and an option to paste text.

Limit each uploaded file to 20 MiB and each PDF to 50 pages initially. Limits are application safeguards, not an excuse to truncate extracted facts silently.

Behavioral information and references are optional. Work-rights questions are requested only when relevant, and the UI should not collect unnecessary sensitive information.

### 4.4 Jobs and fit assessment

Each job card must show title, employer, location, source, posting date when known, deadline when known, and the distinction between a quick match signal and a ranked score.

Detail views must show:

- Retrieved posting text and the URL from which it was actually retrieved.
- Retrieval time, original discovery source, and whether a fallback was used.
- Strengths and genuine gaps, with links to the relevant profile evidence.
- Location, language, and eligibility restrictions with quoted supporting text.
- Unverified source-host warnings, missing information, and stale-posting signals.
- A clear indication when a job could not be retrieved rather than evidence that it closed.

Searching and ranking are separate operations. Users may inspect search results immediately and then choose how many jobs to rank. Ranking limits and deferred results must remain visible.

A triage score must be labeled as an advisory comparison with the profile, not a prediction of hiring success. The full application evaluation always runs before drafting.

### 4.5 Document review workspace

Use a split layout: editable document sections on one side and the compiled PDF on the other. On narrow windows, switch between content and preview.

Required controls:

- Switch between CV and cover letter.
- Edit ordinary text blocks, reorder supported blocks, and undo changes.
- Request a targeted rewrite with visible before/after comparison.
- See evidence behind factual claims and add a corrected fact to the profile.
- View reviewer findings and the changes made in response.
- See page counts, layout findings, contact extraction, and keyword coverage.
- Download PDFs; show the local workspace path in Settings rather than requiring native folder controls.
- Open the employer's application page, then separately record submission.

Raw LaTeX is an optional advanced view, not the normal editor. Any document edit invalidates the affected compile and verification results until the new revision is checked.

The ready state means "prepared and checked for your review," never "submitted." A failed or skipped check must not receive a green pass indicator.

### 4.6 Tracking and dashboard semantics

Use the canonical tracker vocabulary from `.claude\commands\outcome.md`, with friendly display labels.

Dragging a card or choosing a status opens a short confirmation form when a date, outcome, or interview stage is needed. A drafted application becomes applied only after the user records actual submission.

Dashboard calculations must follow `.claude\commands\html-report.md`: drafted rows are not submitted applications; historical interview stages contribute to funnel progression even after rejection; candidate-declined offers are not employer rejections.

Unknown imported statuses remain visible for review. Never silently discard records or silently rewrite their meaning.

### 4.7 Accessibility and interface quality

- Target WCAG 2.2 AA for the application UI.
- Full keyboard navigation, visible focus, correctly labeled controls, and announced progress/approval changes.
- Never convey fit, status, or errors through color alone.
- Support browser zoom to 200%, responsive layout, and an accessible text alternative to PDF-only content.
- Include useful empty, loading, partial-success, unavailable, conflict, and error states for every primary screen.
- Prepare interface strings for localization; do not couple the UI language, posting language, and chosen CV language.

## 5. Recommended technical architecture

### 5.1 Stack

| Layer | Recommendation | Reason |
|---|---|---|
| Frontend | React, TypeScript, Vite | An authenticated public website and server-side rendering are unnecessary; production assets can be served locally. |
| Frontend state | TanStack Query plus local component state | Server-backed files and operations remain authoritative; avoid a second global application database in the browser. |
| Forms and components | React Hook Form, schema validation, accessible component primitives, shared design tokens | Consistent guided editing and accessible interactions. |
| PDF preview | Locally bundled PDF.js | Preview without external viewers, CDNs, or uploads. |
| Local HTTP service | FastAPI and Pydantic | Natural integration with the existing Python utilities and typed API contracts. |
| AI runtime bridge | Python Claude Agent SDK behind an application-owned interface | Reuse a supported programmatic Claude runtime instead of screen-scraping an interactive terminal. |
| Operational persistence | SQLite | Durable runs, approvals, events, identities, and read-model caches without a separate database server. |
| Business persistence | Existing workspace files | Preserve the current source-of-truth model and user ownership. |
| Portal execution | Existing Bun CLIs | Avoid rewriting six existing integrations. |
| Document rendering | Stock template adapters, LuaLaTeX/XeLaTeX, existing verification utilities | Preserve document quality while hiding the toolchain. |
| Distribution | Source checkout, Python virtual environment, npm-installed frontend | Basic command-line setup with no desktop launcher, executable installer, or Docker. |

Pin library versions. Document Windows PowerShell and macOS/Linux startup, while distinguishing documented portability from actual platform acceptance. Keep OS credentials and process-management adapters behind the shared backend; they are not a native user interface.

Do not introduce PostgreSQL, Redis, Celery, Kubernetes, or independent microservices for a single-user local workload.

### 5.2 Process topology

```text
Terminal: python -m ai_job_search
              |
              +-- starts and owns loopback backend
              +-- prints one-use browser login link
              +-- Ctrl+C performs graceful shutdown

Optional second terminal: Vite frontend
              +-- proxies /api to the loopback backend

Browser: React interface (Vite or built assets)
              |
              | same-origin HTTP + server-sent events
              v
Loopback-only FastAPI service
              |
              +-- workspace/file service
              +-- workflow coordinator and approval service
              +-- SQLite operational store
              |
              +-- managed worker process
                       |
                       +-- AI runtime adapter
                       +-- portal CLI adapters
                       +-- document rendering and verification
                       +-- optional integration adapters
```

The API process remains responsive while a worker performs model calls, network retrieval, or compilation. One durable local worker is sufficient initially; it may run bounded parallel portal requests internally.

### 5.3 Service boundaries

- **WorkspaceService:** paths, file import, supported profile sections, hashes, backups, safe publication, conflict handling.
- **WorkflowService:** operation lifecycle, checkpoints, approvals, cancellation, retry, and recovery.
- **AgentRuntime:** structured model requests, streamed events, usage, tool policy, isolated reviewer context.
- **PortalService:** source discovery, capability mapping, validated arguments, result normalization, provenance, health.
- **ApplicationService:** evaluation records, document revisions, submission records, status changes, archive links.
- **DocumentService:** typed content, template rendering, compilation, PDF preview assets, verification.

These are modules in one local backend, not separately deployed services.

## 6. Canonical workflows and AI execution

### 6.1 Reuse model

The bridge must load the applicable canonical command/skill and its referenced methodology files. It supplies UI-approved inputs and translates runtime outputs into typed application events.

It must not maintain a rewritten copy of `/apply`, `/setup`, or the evaluation rubric in application code. Where a canonical command requires transport-neutral output or question metadata, add that contract to the canonical specification and preserve its CLI behavior.

Application code may enforce mechanical invariants such as schemas, allowed paths, confirmation requirements, page counts, and transaction boundaries. It must not independently invent scoring weights, fit bands, language rules, or writing policy.

Pin each run to a policy-bundle hash and record the relevant `framework_version` values. An update to the canonical files requires adapter compatibility checks; it must not change the policy of an operation already awaiting approval.

### 6.2 Runtime contract

The internal runtime interface should support:

```text
start_stage(stage, approved_inputs, policy_bundle, output_schema, limits)
    -> async stream of typed events, followed by a validated stage result

cancel_stage(run_id, step_id)
    -> cancellation acknowledgement
```

A result contains data, provenance, proposed mutations, and artifact references. A successful model call is not sufficient to mark an application operation successful: schema, grounding, file, and document checks must also succeed.

Use structured output schemas validated again by Pydantic. Do not parse prose, terminal ANSI output, or arbitrary Markdown tables to control the application.

Business approvals occur between completed stages. Do not leave an expensive agent process waiting indefinitely while the user considers a fit assessment.

### 6.3 Human questions and permissions

Represent a question as structured data:

```json
{
  "approval_id": "uuid",
  "run_id": "uuid",
  "kind": "proceed_to_drafting",
  "title": "Prepare documents for this role?",
  "input_revision": "sha256-of-relevant-inputs",
  "choices": ["prepare", "not_now"],
  "status": "pending"
}
```

Support typed fields, validation, single/multiple choices, and free text. The UI displays these as forms and approval cards rather than as terminal prompts.

AI tools cannot approve their own proposals. An approval is valid only for the recorded inputs, proposed changes, and policy version. Changes to those inputs invalidate it and require a new decision.

Use explicit SDK tool restrictions and pre-tool validation, not an assumption that the SDK permission callback sees every call. Official documentation notes that previously auto-approved calls can bypass that callback.

Never use global permission bypass. Do not automatically load arbitrary global hooks, plugins, MCP servers, or executable configuration from an imported workspace.

The model must not write directly to authoritative files. Give it the required snapshot/evidence and narrowly scoped application tools; keep unrestricted shell and file-write tools disabled. Validated changes are published by the workspace service. SDK permission settings are not a substitute for an OS sandbox.

### 6.4 Provider connection and privacy

The first supported AI connection should use an API key supplied in the settings UI, stored in the OS credential store and injected only into the AI worker's environment.

Do not implement a third-party "Sign in with Claude Pro/Max" flow or promise that the user's subscription covers this product. Current Agent SDK guidance requires API-key authentication for third-party products unless Anthropic has approved otherwise.

Show a connection test, actionable authentication errors, usage when available, and user-configurable spending limits. API charges are distinct from any consumer AI subscription.

Before first use, explain that profile excerpts, posting text, draft content, and rendered document pages used for verification may be sent to the selected AI provider. Do not send the complete workspace by default.

The runtime adapter boundary permits a later provider or local-model implementation, but equivalent output, tool restrictions, review independence, and quality gates must be demonstrated before calling it supported.

### 6.5 Application operation

The application bridge exposes these checkpoints while preserving the canonical `/apply` ordering:

| Checkpoint | System work | User-visible result or decision |
|---|---|---|
| Input | Retrieve or accept the actual posting; preserve its content and source | Confirm employer/role when uncertain; resolve retrieval or source warnings. |
| Evaluation | Apply the canonical fit framework and relevant gates | Strengths, gaps, restrictions, recommendation; approve drafting or stop. |
| Draft | Generate supported document content using confirmed profile facts | Draft sections and claim/evidence links. |
| Independent review | Fresh reviewer context; research and critique under the canonical rules | Findings and proposed improvements. |
| Revision | Apply justified changes; independently verify company claims used in final material | Updated drafts; unresolved concerns remain visible. |
| Render and verify | Compile, inspect actual rendered pages, check text and keyword coverage | PDFs and per-check pass/fail/skipped results. |
| Publish | Publish validated artifacts, record the drafted tracker entry, archive the exact posting | Application ready for personal review, not submitted. |
| Submission | User submits externally and supplies the submission date | Record applied status and preserve the exact submitted revision. |

Failed eligibility or language gates cannot be bypassed by a hidden UI override. The user can correct the profile or posting and re-evaluate. A flagged uncertainty remains visible for human judgment; it is not silently converted into a fail.

Confirming a new factual detail during editing must write it back to the canonical profile before drafting continues. It cannot remain only in a chat transcript or tailored document.

### 6.6 Limits and failure handling

Set explicit per-stage time, tool-call, token, and cost limits. Initial automatic document-repair limit: three compile/repair cycles, within the run's overall budget. Exhausting the limit leaves an actionable failed step and preserved drafts; it never skips a quality gate.

Persist queued work before dispatch. The worker claims a step transactionally with an ownership lease and heartbeat. A lost worker produces an interrupted step; recovery checks the file journal and committed operation IDs before retrying. Do not use request-scoped tasks or FastAPI's in-memory background-task mechanism as the durable queue.

Retry transient network/provider errors with bounded backoff and jitter. Do not blindly retry authentication failures, invalid inputs, denied permissions, or a site that disallows access.

Preserve the existing distinction between an unreachable posting and positive evidence of closure. If canonical rank state eventually retires an unreachable entry, display its `expired_reason: unreachable` as "Could not verify; check manually," not "Employer closed this role."

Closing the browser must not terminate work. Ctrl+C or the browser's confirmed Stop backend action must stop owned work safely and preserve recoverable checkpoints. Explain that interrupted AI calls may still incur charges.

## 7. Persistence, identity, and compatibility

### 7.1 Workspace ownership

Default new-workspace location on Windows:

```text
%LOCALAPPDATA%\AIJobSearch\workspace\
```

On macOS/Linux, the default is `~/.local/share/AIJobSearch/workspace`. Allow a user-selected absolute local path through a confirmed browser form or the backend's `--workspace` option. Keep source code and framework resources separate from writable career data. Do not create a Git repository or push profile data anywhere.

An existing repository can be selected as a workspace, but show that profile files may be tracked and warn before modifying personal data in a public-fork workflow. Never commit, push, or publish workspace contents automatically.

### 7.2 Sources of truth

| Data | Authoritative storage |
|---|---|
| Methodology and workflow policy | Canonical `.claude\` files and root guidance in the workspace's supported framework version. |
| Confirmed candidate facts | Existing candidate-profile sources used by the grounding audit; preserve their consistency when a fact is corrected. |
| Search strategy | `.claude\skills\job-scraper\search-queries.md`. |
| Discovered/ranked jobs | `job_scraper\seen_jobs.json`. |
| Application business status | `job_search_tracker.csv`. |
| Submitted material and outcome history | The linked archive under `documents\applications\`. |
| Company research cache | `company_research\`, following the canonical cache schema and freshness policy. |
| Runs, approvals, events, record links, settings references | `.app\state.sqlite3`. |
| Staging, verification evidence, recoverable file transactions | `.app\runs\` and `.app\transactions\`. |
| Rebuildable profile/job/application projections | SQLite read-model cache, linked to source hashes. |
| Credentials | OS credential store; SQLite contains references only. |

The frontend does not write files directly. SQLite must not become an unsynchronized second source of truth for profile facts or tracker status.

### 7.3 Structured views over existing files

Expose typed API models for profile, jobs, applications, and evidence. A rebuildable read model links each field to a source file, section or record locator, and content hash.

Use lossless, section-aware Markdown editing for supported profile fields. Preserve unknown sections and methodology text. When an existing personalized document cannot be mapped unambiguously, present an import/change preview rather than regenerating the entire file.

Do not invoke a model every time a profile screen loads. Model-assisted extraction is an explicit operation producing proposed changes; ordinary reads, filters, metrics, and known-field edits are deterministic.

Confirmed corrections must update all contradictory canonical fact sources involved in the current grounding audit. Updating only a browser cache does not satisfy the write-back rule.

### 7.4 Safe file changes

- All mutating operations use one workspace write coordinator and a cross-process workspace lock.
- Perform model/network work against a snapshot; do not hold a write lock while waiting for the user.
- Before publication, recheck input hashes. External changes produce a conflict, not an automatic overwrite.
- Stage outputs and validate them before replacing authoritative files.
- Use same-filesystem atomic replacement for individual files and a durable write journal with backups for related multi-file changes.
- Recover or roll back an interrupted publication before exposing the workspace as writable again. A multi-file publication must not be claimed to be filesystem-atomic.
- Watch for external edits, but treat filesystem notifications as hints and verify hashes at commit time.
- Retain unknown JSON fields and CSV columns during writes.
- Detect concurrent use by another app instance; explain that external CLI writers cannot be assumed to honor the app lock.

The application does not depend on users avoiding all manual edits, but conflicting concurrent writes must be detected and resolved.

### 7.5 Stable application identity

Job discovery keys are not application identifiers. A user can apply to the same role again after a previous application closes.

Use a UUID for every application. For new app-managed records, add `application_id` and `archive_dir` as additive tracker columns, retaining the existing standard columns and tolerating legacy rows without these additions.

New archives should use the canonical sanitized company/role prefix plus a stable application-ID suffix. The exact rule must be added once to `documents\README.md`, and canonical writers/readers must follow it; do not create an app-only naming rule.

Compatibility work required before enabling reapplications:

1. Update the canonical apply/outcome/interview and related archive consumers to prefer the explicit archive link and fall back to legacy derivation.
2. Preserve old folders and records. An import assigns missing IDs only after presenting the proposed additive migration.
3. Detect multiple plausible legacy archive matches and require user selection. Never attach one company's sibling-role documents or overwrite an earlier submission.
4. Store each submitted document revision immutably within its application's archive; later edits create a new draft, not a replacement of submitted evidence.
5. Check canonical report/sync/setup consumers and their contract tests for the added identity fields.

Until this compatibility change is complete, an ambiguous reapplication must stop with an explanation rather than silently sharing an archive.

### 7.6 CSV and legacy-state compatibility

The current standard tracker header remains defined by the canonical command files. Read legacy rows without a deadline, accept the documented space-separated status aliases, and preserve unknown additive columns.

Use the repository's existing score aggregation and rounding behavior. The UI must not calculate a different fit score in JavaScript.

`tools\rank_state.py` currently embeds weights and fit bands that mirror the canonical rubric. Compatibility checks must compare them with the active policy before use. A personalized rubric that differs requires a supported policy adapter or an explicit unsupported-configuration error, not silently incorrect aggregation.

The current tracker writers impose CSV-safe restrictions on appended notes. Preserve that behavior for the first adapter release. Moving to general quoted CSV requires updating every affected canonical reader/writer and compatibility test together, not only changing the frontend writer.

Normalize filesystem paths through the backend's workspace abstraction. In particular, `tools\rank_state.py` already supports explicit state/tracker paths; `salary_lookup.py` currently resolves its data relative to its own source. Changing `cwd` alone does not relocate all data access.

### 7.7 Operational SQLite schema

| Table | Essential fields |
|---|---|
| `runs` | ID, operation type, state, current stage, input revision, policy hash, runtime version, limits, usage, timestamps. |
| `steps` | ID, run ID, stage, attempt, state, input hash, validated output, checkpoint/session reference. |
| `events` | Run ID, monotonic sequence, event type, structured payload, timestamp. |
| `approvals` | ID, run/step ID, kind, input/proposal hash, form schema, decision, state, timestamps. |
| `artifacts` | ID, application/run ID, revision, workspace-relative path, hash, media type, validation results. |
| `file_transactions` | ID, state, affected paths, expected hashes, staged/backup paths, timestamps. |
| `record_index` | Stable record ID, authoritative source locator, source hash, rebuildable typed projection. |
| `settings` | Non-secret preferences, credential references, schema version. |

Enable foreign keys and use transactions. Use WAL only in supported local-filesystem locations; warn against network-share workspaces. Schema migrations must back up operational state and leave business files unchanged unless a separately approved migration requires otherwise.

## 8. Local API and event contracts

### 8.1 API conventions

Serve the frontend and API from the same loopback origin. Prefix endpoints with `/api/v1`.

- Generate the TypeScript client/types from the backend's OpenAPI contract.
- Use UUIDs for records and artifacts. Workspace selection is the sole explicit absolute-path input; validate it as a local root and require user confirmation, never take paths from posting content.
- Return `202 Accepted` with a run ID for long-running operations.
- Require an idempotency key for operation creation and non-repeatable mutations.
- Require an expected revision for profile, document, approval, and status changes.
- Use cursor pagination for job and activity lists; default page size 50, maximum 100.
- Return typed errors with a safe message, suggested action, and correlation/run ID.
- Do not expose raw secrets, unrestricted filesystem browsing, or a generic command-execution endpoint.

### 8.2 Initial endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/session/bootstrap` | Exchange the terminal-issued one-use token for a local session. |
| GET | `/system/readiness` | Runtime, workspace, source, and document-tool readiness. |
| POST | `/system/shutdown` | Confirmed local shutdown; stop/recover active work safely. |
| GET | `/workspace` | Selected workspace and revision summary. |
| POST | `/workspace` | Select/create a validated absolute local workspace after user confirmation. |
| POST | `/workspace/imports` | Inspect a selected workspace and produce an import proposal. |
| POST | `/documents/imports` | Validate/copy uploaded documents and create an extraction run. |
| GET | `/profile` | Typed profile with evidence and source revision. |
| POST | `/profile/changes` | Submit a previewable profile change set. |
| GET | `/portals` | Discovered sources, effective enablement, capabilities, warnings, health. |
| PATCH | `/portals/{id}/settings` | Change reviewed source options/enablement with required acknowledgement. |
| GET | `/jobs` | Search/filter local discovered jobs. |
| POST | `/jobs/imports` | Import a user-supplied URL or posting text. |
| POST | `/search-runs` | Search configured sources. |
| POST | `/ranking-runs` | Rank a selected/bounded group of jobs. |
| POST | `/evaluation-runs` | Fully evaluate a posting before drafting. |
| GET | `/applications` | Pipeline records. |
| POST | `/applications` | Allocate an application ID from an approved evaluation, or register an external application; never infer submission. |
| GET | `/applications/{id}` | Evaluation, documents, archive, timeline, and current state. |
| GET | `/applications/{id}/documents/{kind}` | Read typed CV/cover-letter content and its revision. |
| PATCH | `/applications/{id}/documents/{kind}` | Save supported content edits and invalidate affected verification results. |
| POST | `/applications/{id}/document-runs` | Start approved document preparation/revision. |
| POST | `/applications/{id}/submissions` | Record actual submission and freeze the submitted revision. |
| POST | `/applications/{id}/outcomes` | Record a confirmed response, stage, or resolution. |
| GET | `/runs/{id}` | Durable operation status and completed checkpoints. |
| GET | `/runs/{id}/events` | Server-sent event stream and replay. |
| POST | `/runs/{id}/cancel` | Request cancellation of managed work. |
| POST | `/runs/{id}/retry` | Resume/retry from a valid checkpoint with fresh checks. |
| POST | `/approvals/{id}/decision` | Submit a decision for the exact proposal revision. |
| GET | `/artifacts/{id}` | Authorized local preview/download. |
| POST | `/backups` | Create an explicitly requested local backup. |
| POST | `/restores` | Validate and preview a backup restore. |
| POST | `/resets` | Produce a scoped deletion proposal; execute only through its confirmation. |
| GET | `/settings` | Read non-secret configuration and credential-connection status. |
| PATCH | `/settings` | Change non-secret preferences and usage limits with revision checks. |
| POST | `/settings/ai-connection` | Store/test a credential without returning its value. |

Import proposals, restores, and destructive resets require a separate approval before publication. An endpoint that creates a proposal is not permission to apply it.

Application IDs may be allocated before preparation succeeds; keep those preparatory records in operational state. Only a completed publication creates the canonical drafted tracker row. An explicitly registered external application supplies its actual status/date instead of going through document generation.

### 8.3 Event envelope

```json
{
  "run_id": "uuid",
  "sequence": 42,
  "type": "approval.required",
  "stage": "evaluate",
  "occurred_at": "2026-10-19T11:00:00Z",
  "payload": {
    "approval_id": "uuid",
    "message": "Review the fit assessment before preparing documents."
  }
}
```

Minimum event types: `run.queued`, `stage.started`, `stage.progress`, `stage.completed`, `approval.required`, `artifact.created`, `warning`, `run.failed`, `run.cancelled`, `run.completed`.

Persist events before delivery. Support `Last-Event-ID` replay and client-side sequence deduplication. A browser refresh must reconstruct the same state from the API without restarting the operation. SSE delivers notifications at least once; it does not make business mutations exactly once.

If streaming is unavailable, fall back to bounded status polling without starting a second operation.

### 8.4 Separate state machines

Operation states:

```text
queued -> running -> awaiting_user -> running -> succeeded
                    \-> cancelled
          \-> failed
          \-> interrupted -> queued or failed
```

An awaiting-user run may also be cancelled. Terminal runs are not silently reopened; retry creates a linked attempt and rechecks relevant inputs.

Job states remain defined by the scraper/ranker. Tracker states remain defined by `/outcome`. Archive outcome values remain defined by `documents\README.md`. The frontend may group or label them, but it must not write one enum into another field.

Useful error codes include `DEPENDENCY_MISSING`, `AI_AUTH_REQUIRED`, `INPUT_CONFLICT`, `SOURCE_BLOCKED`, `SOURCE_RATE_LIMITED`, `MODEL_OUTPUT_INVALID`, `PDF_VERIFICATION_FAILED`, `ARCHIVE_AMBIGUOUS`, and `USAGE_LIMIT_REACHED`.

## 9. Portal and research integration

Discover installed portal skills and read their enablement metadata, but execute only reviewed adapters with known argument mappings. Listing a new skill must not automatically grant arbitrary local code execution.

The portable output contract is reusable; the input flags are not uniform. For example, LinkedIn requires an explicit location string, while freehire uses geographic facets. Implement a capability adapter per portal rather than guessing flags.

Validate every search result before ingestion: required identity/title/URL fields, optional company/location/date values, valid dates where present, and preserved source metadata. A successful process with degraded or empty output is not automatically a healthy source.

The effective enablement is the conjunction of the skill setting, supported adapter, relevant market, and any required user consent. Preserve Danish opt-in behavior. Show LinkedIn's documented automated-access warning and require acknowledgement before its first use; local execution does not remove the source's terms restrictions.

freehire must be described as a tech-oriented source with no guaranteed availability. Do not market the initial portal set as comprehensive coverage of every profession or country.

Search behavior must preserve canonical freshness, deduplication, mass-posting consolidation, health checks, and fallback provenance. Reuse full descriptions already returned by a source instead of performing unnecessary detail requests.

Use bounded per-source concurrency and budgets. Respect rate-limit responses and robots-policy decisions. Do not retry through proxies, authenticated browser sessions, or alternate identities to defeat a source's refusal.

External postings, research notes, and caches are untrusted data. Fetch the user-provided posting URL under the retrieval policy, but do not automatically follow instructions or links embedded in posting text. Company research starts from independently established company identity.

If all sources fail, the UI must still support pasting the full posting. Never evaluate or draft from a title plus assumptions.

## 10. Document generation and quality gates

### 10.1 Supported rendering path

For the initial application renderer, have the model return typed document content with source references. A template adapter escapes content and renders the supported stock LaTeX structures. This makes the visual editor practical and avoids treating model-written shell commands or arbitrary template code as trusted execution.

Reuse the stock examples, class, fonts, and canonical template guidance. Do not replace the reference CV/cover-letter styling incidentally while adding the UI.

Custom template import remains advanced/later functionality. User-supplied compile commands and arbitrary `.cls`/`.sty` code must not be executable simply because a template was uploaded.

### 10.2 Verification pipeline

1. Validate required content and claim/evidence references.
2. Render document sources from the approved content revision.
3. Compile with the active supported template's engine.
4. Run the existing PDF page/text and layout utilities.
5. Inspect the actual rendered pages through the AI runtime, not just their source text.
6. Produce the canonical keyword-coverage report against the extracted CV text.
7. Publish only after required checks pass and remaining uncertainty is explicit.

For the stock templates, acceptance includes an exactly two-page CV and exactly one-page cover letter. Custom templates, when supported, supply their declared limits.

Keep the existing distinction between layout-check success, layout failure, and a skipped/unavailable extractor. `verify_layout.py` exit 2 is not a pass. The UI must identify the actual extractor and any degraded verification.

The README must explain installation of the required PDF tooling, and readiness must identify missing tools before generation. When a documented optional check is unavailable, follow canonical fallback rules and show "Needs manual check," not "Verified."

Grounding checks must compare dates, titles, employers, metrics, and other material claims with the canonical profile evidence. A source link generated by a model is not by itself proof of semantic truth; uncertain claims require correction or human review.

Company-specific claims used in final documents require independent verification under the canonical research rules. Cached research is a lead, not final verification.

### 10.3 Revisions and provenance

Each artifact records its content revision, source/template/policy hashes, compilation result, verification findings, and creation time.

Preview and download always refer to a specific revision. If an edit is still being compiled, retain the last successful preview but clearly label it as older; never present it as the preview of current text.

Submitted revisions are immutable. Interview preparation reads those versions, not the latest edited draft.

### 10.4 Process restrictions

Compile in a dedicated run directory with only the required sources/assets, a minimal environment, no provider credentials, disabled shell escape, restricted file access where the engine supports it, and explicit time/output limits.

Do not claim these settings constitute a complete OS sandbox. Arbitrary templates remain unsupported until an appropriate isolation mechanism is available and tested on each supported platform. Lua-capable engines and template assets must be treated as executable code.

## 11. Local security and data protection

Single-user does not mean the local HTTP service can be unauthenticated to every website opened in the browser.

### 11.1 Loopback service

- Bind to loopback only (default `127.0.0.1:8765`), never `0.0.0.0` or a LAN interface. An explicit zero port may select an available port.
- Restrict accepted Host and Origin values to the launched local origin; reject unexpected hosts to reduce DNS-rebinding risk.
- Print a cryptographically random, 90-second, one-use bootstrap token in a terminal-issued URL fragment. `--frontend-port` targets a separately running loopback frontend; it never changes API origin enforcement.
- Exchange it for an HttpOnly, SameSite-protected local session, then remove the fragment from browser history.
- Require CSRF protection for state changes; do not depend on CORS alone.
- Disable permissive CORS, protect event/artifact endpoints, and reject unauthorized requests before performing work.
- Apply a restrictive content-security policy. Bundle scripts, fonts, PDF assets, and other UI resources locally.

The local session is an anti-cross-site protection mechanism, not a user-account system. The app does not defend against malware already running as the same OS user.

### 11.2 Files and processes

Resolve and validate paths against the approved workspace, including symlinks and Windows junctions. Use artifact IDs for documents and downloads. The authenticated workspace form accepts only an explicit absolute local directory path, with confirmation; no unrestricted filesystem browser or native folder dialog is required.

Posting/research retrieval accepts only HTTP(S), rejects URL credentials and private/loopback/link-local destinations, and revalidates DNS resolution and every redirect. An explicitly configured local freehire endpoint is a separate, trusted setting, not an exception a posting can request.

Spawn fixed executable paths with argument arrays, not shell-interpolated commands. Restrict inherited environment variables. Cancel only process IDs/process groups owned by the operation; on Windows use managed Job Objects or an equivalent tracked process-tree mechanism.

Do not automatically execute code imported with a workspace or portal skill. Do not allow the AI runtime to edit installed application code, permissions, or credential stores.

### 11.3 Content and credentials

Render untrusted Markdown/HTML safely; posting content must not inject scripts, load tracking pixels, or make automatic requests from the UI. External navigation requires a deliberate user action and appropriate link protections.

Store secrets in Windows Credential Manager or the equivalent OS facility. Never put them in Markdown, browser storage, exported state, or logs.

Keep diagnostics local by default, with content/secret redaction and retention controls. Any telemetry or support upload requires opt-in and a preview of what will be shared.

Backups contain sensitive career information. Exclude credentials, offer encryption, and explain that local files are not automatically encrypted unless that feature or OS disk encryption is enabled.

## 12. Source setup, startup, updates, and recovery

### 12.1 Launch contract

Basic terminal setup and startup are supported; job-search workflows must not require AI CLI commands.

The backend entrypoint must:

1. Enforce one backend instance per workspace.
2. Start the loopback backend and verify readiness.
3. Print a one-use browser login link, targeting Vite when `--frontend-port` is supplied.
4. Stay in the terminal and handle Ctrl+C and supported termination signals gracefully.
5. Report startup failures and occupied ports in the terminal with a nonzero exit code.

The README must give one-time dependency setup, separate frontend/backend commands, the built-frontend alternative, workspace selection, stopping/restarting, and session troubleshooting. Start Vite before the backend so the browser can use its 90-second login link immediately.

Do not install an always-on system service or require administrator rights for normal operation. Work resumes only when the app is running; scheduled scraping while the machine/app is off is not implied.

### 12.2 Dependency handling

Use a Python virtual environment and npm lockfile for documented source setup.

Python and Node are user-installed prerequisites. Bun is needed only for portal searches; LuaLaTeX, XeLaTeX, template packages, fonts, and Poppler are needed for fully checked documents. Keep them optional for profile access, pasted postings, tracking, and existing previews.

The Agent SDK bundles a native runtime for supported wheels/platforms; verify its presence without requiring a separate interactive Claude Code installation. Do not assume every wheel/CPU combination is supported.

Validate a supported TeX installation with all required packages/fonts. Document TeX Live, MacTeX, or MiKTeX setup; compilation must not trigger hidden interactive package-install dialogs.

The readiness screen must provide official setup links and actionable missing-tool states. Existing consent-based Windows dependency repair may remain as an optional backend integration; it is not a Windows desktop app or a cross-platform installer guarantee.

A release cannot claim a supported operating system until the documented source setup and core browser journey pass on a clean machine for that platform.

### 12.3 Updates

Application updates replace code/resources, not personal files. Framework updates require compatibility checks, a diff of affected personalized sections, a backup, and explicit approval before merging.

Pin dependencies and resource versions for reproducible setup. Verify any downloaded installers/resources.

Preserve the user's active template and profile changes. Never reapply a pristine upstream template over personalized files as an update mechanism.

### 12.4 Backup and recovery

Back up business files together with SQLite operational state and identity links. Use a consistent snapshot or SQLite backup API, not a blind copy of an actively written database file.

On startup, recover incomplete file transactions before accepting writes. Mark lost worker executions interrupted and offer a safe retry from the last completed checkpoint. A model call may have incurred cost before a crash; do not promise it can always be resumed without another charge.

Restore first validates archive paths, sizes, manifests, and schema compatibility, then shows a change preview and requires confirmation. Prevent path traversal and never execute files from a backup.

Reset actions list exact affected data and require typed confirmation for destructive scopes. They must not delete the selected workspace root, installed application, or unrelated files.

## 13. Proposed repository additions

```text
app\
  frontend\
    src\
      features\          screen-oriented UI modules
      components\        shared accessible components
      api\               generated client and query helpers
    package.json
  backend\
    src\ai_job_search\
      api\
      workflows\         lifecycle/transport adapters, not copied prompts
      services\
      adapters\          agent, portals, documents, OS credentials
      storage\
    tests\
    pyproject.toml
docs\
  local-app-technical-specification.md
```

Existing `.claude\`, `.agents\`, `tools\`, document templates, and tests remain in place. Add `.app\` operational/staging data to ignore rules when implementation begins.

Use one frontend and one backend dependency manifest. Do not reorganize or rewrite the portal CLIs merely to introduce the application.

## 14. Non-functional requirements

Reference performance environment: Windows x64 laptop, four CPU cores, 16 GB RAM, SSD, no dedicated GPU. Measurements exclude first-time downloads and external-provider latency unless stated otherwise.

| Area | Initial acceptance target |
|---|---|
| Startup | Usable local interface within 15 seconds with dependencies installed. |
| Local reads | p95 under 300 ms for paginated reads with 10,000 discovered jobs and 1,000 applications. |
| Action acknowledgement | Queue acknowledgement or an actionable error within 500 ms for locally valid operations. |
| Progress | A received stage/status change reaches an attached UI within one second; no fabricated progress. |
| Cancellation | Running work acknowledges cancellation and stops owned processes within 10 seconds, except an explicitly reported safe-publication boundary. |
| Recovery | A forced process termination loses no committed profile/status/submission record and cannot expose half-published state as complete. |
| Idempotency | Retrying the same client operation key cannot create duplicate application records, duplicate approvals, or duplicate committed file changes. |
| Accessibility | Primary journeys keyboard-complete, with no critical automated accessibility findings and a manual accessibility pass. |
| Offline behavior | Profile, stored postings, tracking, and existing document previews work with outbound network access blocked. |
| Privacy | No application-controlled outbound traffic on an idle offline/local-read journey; no third-party UI assets or telemetry by default. |
| Installation | Documented source dependency installation, startup, and core browser journey on each claimed platform; no Docker or desktop executable. |

Model-dependent turnaround times must be measured in the runtime spike. Do not promise a fixed application-generation time before collecting that evidence.

## 15. Validation and acceptance

### 15.1 Preserve the existing framework checks

Keep the repository's established checks, running relevant selectors during development:

```powershell
python tools\lint_skills.py
python tools\security_guards.py
python -m unittest discover -s tests -t . -v
```

When canonical framework files change, apply the repository's `framework_version` policy and its documented version check. For affected portal CLIs, run `bun run typecheck` and `bun test` from the relevant CLI directory. Keep live portal requests out of normal CI.

Retain template compilation and PDF verification on supported TeX configurations. These are requirements for the future implementation, not evidence that an application backend already exists.

### 15.2 New test layers

| Layer | Coverage |
|---|---|
| Unit | Profile mapping, status projections, portal capability mapping, revision checks, canonical policy compatibility. |
| Storage | File publication interruption, external-edit conflicts, unknown-column preservation, migrations, backup/restore. |
| API | Session/CSRF enforcement, schemas, idempotency, approval binding, path boundaries, artifact access. |
| Runtime contract | Typed model outputs, invalid result handling, permission denial, independent reviewer input, usage limits. |
| Integration | Fixture portals, deterministic fake AI runtime, real template compilation, preview and verification artifacts. |
| Browser end to end | Onboarding, posting import, approval, document revision, submission, outcome, reload/reconnect. |
| Startup | Separate backend/frontend and built-frontend modes, secure terminal login links, spaces/non-ASCII paths, graceful terminal shutdown, and no native launcher dependency. |
| Adversarial fixtures | Posting instructions, malicious Markdown, unsafe URLs, path traversal, untrusted templates, poisoned cache text. |

Use synthetic candidate data and saved portal fixtures. Real credentials, personal profiles, live job searches, and paid model calls must not be prerequisites for routine CI. Separate opt-in live runtime validation from the deterministic suite.

### 15.3 Required end-to-end acceptance scenarios

1. A new user imports a text PDF, corrects a date, and confirms it. The corrected fact is persisted in the canonical sources and used in later drafts after an application restart.
2. A user pastes a complete posting while every portal is unavailable. Evaluation and preparation remain available with the configured AI connection.
3. A required undeclared language blocks drafting with the quoted requirement. A higher required level in a declared language is flagged for judgment rather than auto-rejected.
4. A fetch failure, including HTTP 404, is not presented as proof of closure. Retired unreachable entries retain the documented manual-check explanation.
5. A fit assessment pauses for approval. Refreshing or restarting the app preserves the decision card, and no drafting occurs before approval.
6. Editing the profile after an assessment invalidates the stale approval. A repeated click cannot start duplicate preparation runs.
7. A draft passes through an independent reviewer and real compilation. For stock templates, the exported CV has two pages and the cover letter one page.
8. A failed layout or text check remains visible, cannot become a false pass, and stops automatic repair at the configured limit without losing drafts.
9. Editing document text marks the previous preview stale until recompilation. Submission freezes the revision actually approved by the user.
10. Generated documents create a drafted record, not an applied record. Recording submission uses the real submission date and starts follow-up timing.
11. Reapplying to the same company/role preserves the previous application, posting, submitted documents, and outcome as a separate record.
12. An external file edit during a running operation causes a reviewable conflict, not an overwrite.
13. Forced termination during multi-file publication recovers to a consistent state before further writes are accepted.
14. A legacy workspace without deadlines, with status aliases and extra CSV columns, imports without dropping records or unknown fields.
15. An unrelated website cannot read local career data or trigger a search, write, reset, or export through the loopback API.
16. A user with basic command-line skills follows the README on a clean supported computer, starts the backend/frontend, opens the terminal-issued login link, completes the browser journey, and stops both services without a desktop app or Docker.

Before MVP release, run a usability check with at least five people comfortable with basic terminal setup but unfamiliar with AI CLIs. At least four should follow the README and complete profile confirmation, posting evaluation, document review, and submission recording without learning AI commands or needing facilitator intervention. Record the specific points of confusion, not only task completion.

## 16. Delivery plan and architectural gates

| Phase | Deliverable | Exit criterion |
|---|---|---|
| 0: Runtime and startup spike | Headless backend, separate/built frontend startup, SDK connection, one structured evaluation, one UI question, restricted tools, real stock-template compile/preview | Demonstrate documented terminal startup, supported API authentication, event handling, cancellation, and viable TeX installation. |
| 1: Functional vertical slice | Profile import/confirmation -> pasted posting -> evaluation approval -> reviewed PDFs -> drafted record -> submission/outcome | One complete browser-driven application journey, persisted to canonical files and recoverable after restart. |
| 2: MVP search and hardening | Portal adapters, dedup/ranking, job list, dashboard, durable approvals, backup/restore, stable application identity | All MVP acceptance scenarios, clean-machine source setup, and usability target pass. |
| 3: Complete lifecycle UI | Interview prep, follow-ups, enrichment, learning, calibration, HTML export | Each new screen uses its canonical workflow and submitted-document/evidence boundaries. |
| 4: Optional extensions | Gmail, Notion, vetted templates/portals, local-model investigation | Explicit permissions, verified adapter compatibility, and no regression to the browser-driven core journey. |

Do not estimate the full implementation from frontend screen count alone. The critical uncertainties are runtime permission/question handling, lossless editing of personalized files, stable archive identity, document toolchain distribution, and end-to-end verification.

### 16.1 Decisions resolved by this draft

- Single-user local application, not hosted SaaS.
- Browser interface backed by a terminal-started, headless local backend; separate Vite or built-frontend modes.
- Basic command-line setup is acceptable; Windows desktop packaging and Docker are out of scope.
- Canonical workspace files retained as business records.
- SQLite limited to operational state, identities, settings references, and read models.
- Supported programmatic AI integration rather than a terminal transcript wrapper.
- Human approval before drafting and other consequential changes.
- External application submission remains manual.

### 16.2 Decisions to validate in Phase 0

- Supported OS/CPU combinations for source setup, the SDK runtime, OS keyring, fonts, and PDF tools.
- The initial supported TeX distribution and guided installation path.
- The exact SDK version and configuration needed for restricted tools, source loading, streaming, structured results, and UI questions.
- Whether the proposed section-aware profile writer covers representative existing personalized workspaces without loss.
- Practical model latency/cost and the default usage limits exposed in setup.
- Whether fully local inference is a separately required future product capability; it is not provided merely by running the orchestration locally.

## 17. External integration references

The following official documentation was consulted for this draft. Recheck it when pinning implementation dependencies and before distributing the application:

- [Claude Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview): local programmatic runtime, project-feature support, authentication restrictions, terms, and product branding.
- [Agent SDK quickstart](https://code.claude.com/docs/en/agent-sdk/quickstart): Python installation, bundled native-runtime caveats, and API-key setup.
- [Agent SDK permissions](https://code.claude.com/docs/en/agent-sdk/permissions): permission evaluation order and the limits of approval callbacks.
- [Agent SDK structured outputs](https://code.claude.com/docs/en/agent-sdk/structured-outputs): validated schemas and explicit failure when structured output cannot be produced.

Use the application's own branding. Do not present it as an official Anthropic product or use a provider's restricted product branding as the application's identity.
