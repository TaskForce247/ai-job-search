import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, artifactUrl, queryString } from '../api';
import { isActiveRun, useAction, useActivity, useApiQuery, useNotices } from '../query';
import type { ApiSchema, Application, DocumentKind, DocumentView, Page, ProfileView, Run, RunAccepted } from '../types';
import { outcomeStatuses } from '../types';
import { DocumentChecks, DocumentEditor, isVerifiedDocument } from '../components/DocumentEditor';
import { AIReadinessNote } from '../components/SetupControls';
import { RunFailure } from '../components/RunFailure';
import { DocumentIssues, EvaluationView } from '../components/proposals';
import { Alert, Badge, Button, Empty, ErrorPanel, ExternalLink, Field, formatDate, humanize, Loading, Modal, PageHeader, Pager, Panel, safeUrl, today } from '../components/ui';
import { ApprovalCard, RunActions } from './Activity';

type OutcomeStatus = typeof outcomeStatuses[number];

export function RegisterApplicationForm({ onDone }: { onDone?: () => void }) {
  const { register, handleSubmit, formState: { errors } } = useForm<{
    company: string; role: string; source: string; status: OutcomeStatus; applied_on: string;
  }>({ defaultValues: { company: '', role: '', source: '', status: 'applied', applied_on: today() } });
  const navigate = useNavigate();
  const { notify } = useNotices();
  const action = useAction((values: { company: string; role: string; source: string; status: OutcomeStatus; applied_on: string }) =>
    api<Application>('/applications', { method: 'POST', body: values satisfies ApiSchema<'ApplicationInput'> }),
  (result) => {
    onDone?.();
    notify('External application recorded. No files or messages were sent.');
    navigate(result.id ? `/applications/${encodeURIComponent(result.id)}` : '/applications');
  });
  return <form className="stack" onSubmit={handleSubmit((values) => action.mutate(values))}>
    <Alert title="Already applied elsewhere?">Register an application you submitted yourself. This creates a distinct record,
      including when you reapply to the same role. It does not prepare or send documents.</Alert>
    <div className="form-grid">
      <Field id="external-company" label="Employer" error={errors.company?.message}><input id="external-company" maxLength={500}
        {...register('company', { required: 'Enter the employer.' })} /></Field>
      <Field id="external-role" label="Role" error={errors.role?.message}><input id="external-role" maxLength={500}
        {...register('role', { required: 'Enter the role.' })} /></Field>
      <Field id="external-source" label="Posting URL or source"><input id="external-source" maxLength={2048} {...register('source')} /></Field>
      <Field id="external-status" label="Current status"><select id="external-status" {...register('status')}>
        {outcomeStatuses.map((status) => <option key={status} value={status}>{humanize(status)}</option>)}</select></Field>
      <Field id="external-date" label="Actual submission date" error={errors.applied_on?.message}><input id="external-date" type="date" max={today()}
        {...register('applied_on', { required: 'Enter the date you submitted.', validate: (v) => v <= today() || 'Submission cannot be in the future.' })} /></Field>
    </div>
    <ErrorPanel error={action.error} /><Button type="submit" busy={action.isPending}>Record external application</Button>
  </form>;
}

function ApplicationCard({ application }: { application: Application }) {
  return <article className="application-card">
    <div className="section-heading"><span className="company-monogram" aria-hidden="true">{application.company.slice(0, 1) || '·'}</span><Badge value={application.status} /></div>
    <h3><Link to={`/applications/${encodeURIComponent(application.id)}`}>{application.role}</Link></h3>
    <p>{application.company}</p><p className="hint">{application.applied_on ? `Applied ${formatDate(application.applied_on)}`
      : application.status === 'drafted' ? 'Drafted · not sent' : application.status === 'preparing'
        ? 'Draft workspace · not yet verified' : 'Submission date not recorded'}</p>
    {application.deadline && <p className="hint">Deadline {formatDate(application.deadline)}</p>}
    <Link to={`/applications/${encodeURIComponent(application.id)}${application.status === 'preparing' ? '' : '#tracking'}`}>
      {application.status === 'preparing' ? 'Open drafts & checks' : application.status === 'drafted' ? 'Review & record submission' : 'Record progress'} →</Link>
  </article>;
}

export function ApplicationsPage() {
  const [params, setParams] = useSearchParams();
  const [filterText, setFilterText] = useState(params.get('q') ?? '');
  const [filterStatus, setFilterStatus] = useState(params.get('status') ?? '');
  const [view, setView] = useState<'board' | 'table'>('board');
  const [registerOpen, setRegisterOpen] = useState(false);
  const [cursors, setCursors] = useState<string[]>([]);
  const filters = { q: params.get('q'), status: params.get('status'), limit: 50, cursor: cursors.at(-1) };
  const query = useApiQuery<Page<Application>>(['applications', filters], `/applications${queryString(filters)}`);
  const statusGroups = ['preparing', 'drafted', 'applied', 'interview', 'offer', 'hired', 'closed'];
  const closed = ['rejected', 'no_response', 'offer_declined', 'withdrawn'];
  const unknown = [...new Set((query.data?.items ?? []).map((application) => application.status)
    .filter((status) => !statusGroups.includes(status) && !closed.includes(status)))];
  const columns = [...statusGroups, ...unknown];
  return <div className="stack">
    <PageHeader eyebrow="Progress, not just paperwork" title="Your applications"
      description="Keep drafts, real submissions, and responses clearly separate."
      actions={<Button variant="secondary" onClick={() => setRegisterOpen(true)}>+ Record an external application</Button>} />
    <form className="filter-bar" onSubmit={(event) => {
      event.preventDefault(); setParams({ ...(filterText ? { q: filterText } : {}), ...(filterStatus ? { status: filterStatus } : {}) }); setCursors([]);
    }}>
      <Field id="applications-q" label="Search applications"><input id="applications-q" value={filterText} onChange={(event) => setFilterText(event.target.value)}
        placeholder="Employer or role" /></Field>
      <Field id="applications-status" label="Application status"><select id="applications-status" value={filterStatus} onChange={(event) => setFilterStatus(event.target.value)}>
        <option value="">All statuses</option><option value="preparing">Preparing · not yet verified</option><option value="drafted">Drafted · not sent</option>
        {outcomeStatuses.map((status) => <option key={status} value={status}>{humanize(status)}</option>)}
      </select></Field><Button type="submit" variant="secondary">Filter applications</Button>
    </form>
    <div className="section-heading"><p className="hint">Preparing a document never marks an application as sent.</p>
      <div className="segmented" role="group" aria-label="Application view">
        <button aria-pressed={view === 'board'} onClick={() => setView('board')}>Board</button>
        <button aria-pressed={view === 'table'} onClick={() => setView('table')}>Table</button>
      </div></div>
    {query.isPending && <Loading label="Loading your application pipeline…" />}
    <ErrorPanel error={query.error} retry={() => void query.refetch()} />
    {query.data && (query.data.items.length === 0 ?
      <Panel><Empty title="Every next chapter starts somewhere." action={<Link to="/jobs" className="button button-primary">Explore jobs →</Link>}>
        No applications match these filters. Prepare a role from Jobs, or record one you have already submitted.</Empty></Panel>
      : view === 'board' ? <div className="application-board" aria-label="Application pipeline board">{columns.map((status) => {
        const items = query.data.items.filter((application) => status === 'closed'
          ? closed.includes(application.status) || application.status === 'closed' : application.status === status);
        return <section key={status} className="board-column" aria-label={`${humanize(status)} applications`}>
          <div className="section-heading"><h2>{humanize(status)}</h2><span className="count-pill">{items.length}</span></div>
          {items.map((application) => <ApplicationCard key={application.id} application={application} />)}
          {!items.length && <p className="board-empty">Nothing here yet</p>}
        </section>;
      })}</div> : <Panel><div className="table-scroll"><table><caption className="sr-only">Your application records</caption><thead><tr>
        <th>Role & employer</th><th>Status</th><th>Submitted</th><th>Deadline</th><th>Fit rating</th><th>Next action</th>
      </tr></thead><tbody>{query.data.items.map((application) => <tr key={application.id}>
        <td><Link to={`/applications/${encodeURIComponent(application.id)}`}><strong>{application.role}</strong></Link><span className="cell-detail">{application.company}</span></td>
        <td><Badge value={application.status} /></td><td>{application.applied_on ? formatDate(application.applied_on)
          : ['preparing', 'drafted'].includes(application.status) ? 'Not sent' : 'Date not recorded'}</td>
        <td>{formatDate(application.deadline)}</td><td>{application.fit_rating ?? 'Not evaluated'}</td>
        <td><Link to={`/applications/${encodeURIComponent(application.id)}${application.status === 'preparing' ? '' : '#tracking'}`}>
          {application.status === 'preparing' ? 'Open drafts & checks' : 'Review / record progress'} →</Link></td>
      </tr>)}</tbody></table></div></Panel>)}
    {query.data && <Pager count={query.data.items.length} total={query.data.total} hasNext={!!query.data.next_cursor} hasPrevious={cursors.length > 0}
      next={() => query.data.next_cursor && setCursors((old) => [...old, query.data.next_cursor!])}
      previous={() => setCursors((old) => old.slice(0, -1))} />}
    <Modal open={registerOpen} title="Record an external application" onClose={() => setRegisterOpen(false)}>
      <RegisterApplicationForm onDone={() => setRegisterOpen(false)} />
    </Modal>
  </div>;
}

export function SubmissionForm({ application }: { application: Application }) {
  const { register, handleSubmit, reset, formState: { errors } } = useForm<{ submitted_on: string; notes: string; confirmed: boolean }>({
    defaultValues: { submitted_on: today(), notes: '', confirmed: false },
  });
  const { notify } = useNotices();
  const action = useAction((values: { submitted_on: string; notes: string; confirmed: boolean }) =>
    api<unknown>(`/applications/${encodeURIComponent(application.id)}/submissions`, {
      method: 'POST', body: { expected_revision: application.revision, submitted_on: values.submitted_on, notes: values.notes } satisfies ApiSchema<'SubmissionInput'>,
    }), () => { reset(); notify('Actual submission recorded. This app did not send files or contact the employer.'); });
  return <form className="stack" onSubmit={handleSubmit((values) => action.mutate(values))}>
    <Alert tone="warning" title="Drafted does not mean sent">
      Submit your final documents through the employer’s own process first. Then record the real submission here.
      This button never sends an application.</Alert>
    {safeUrl(application.source) && <ExternalLink href={application.source} className="button button-secondary">Open employer’s posting</ExternalLink>}
    <div className="form-grid">
      <Field id="submission-date" label="Date actually submitted" error={errors.submitted_on?.message}>
        <input id="submission-date" type="date" max={today()} {...register('submitted_on', {
          required: 'Enter your actual submission date.', validate: (v) => v <= today() || 'Submission cannot be in the future.',
        })} /></Field>
      <Field id="submission-notes" label="Submission notes (optional)" error={errors.notes?.message}
        hint="Use a single line and semicolons instead of commas or double quotes (legacy tracker format).">
        <input id="submission-notes" maxLength={20_000} {...register('notes', {
          validate: (value) => !/[,"\r\n]/.test(value) || 'Use a single line without commas or double quotes; semicolons are fine.',
        })} /></Field>
    </div>
    <label className="checkbox-row"><input type="checkbox" {...register('confirmed', { required: 'Confirm that you submitted the application yourself.' })} />
      <span>I submitted the application myself and want to record it as Applied.</span></label>
    {errors.confirmed && <p role="alert" className="field-error">{errors.confirmed.message}</p>}
    <ErrorPanel error={action.error} />
    <div><Button type="submit" busy={action.isPending}>Record actual submission</Button></div>
  </form>;
}

function OutcomeForm({ application }: { application: Application }) {
  const { register, handleSubmit, getValues, reset, formState: { errors } } = useForm<{
    status: OutcomeStatus; occurred_on: string; stage: string; notes: string; submitted_on: string;
  }>({ defaultValues: { status: 'interview', occurred_on: today(), stage: '', notes: '', submitted_on: '' } });
  const { notify } = useNotices();
  const action = useAction((values: { status: OutcomeStatus; occurred_on: string; stage: string; notes: string; submitted_on: string }) =>
    api<unknown>(`/applications/${encodeURIComponent(application.id)}/outcomes`, {
      method: 'POST', body: { ...values, expected_revision: application.revision, submitted_on: values.submitted_on || null } satisfies ApiSchema<'OutcomeInput'>,
    }), () => { reset(); notify('Application progress recorded in your local history.'); });
  return <form className="stack" onSubmit={handleSubmit((values) => action.mutate(values))}>
    <p>Record a real response or stage change. Your earlier timeline and submitted documents remain in the archive.</p>
    <div className="form-grid">
      <Field id="outcome-status" label="New status"><select id="outcome-status" {...register('status')}>
        {outcomeStatuses.map((status) => <option key={status} value={status}>{humanize(status)}</option>)}</select></Field>
      <Field id="outcome-date" label="When did it happen?" error={errors.occurred_on?.message}><input id="outcome-date" type="date" max={today()}
        {...register('occurred_on', { required: 'Choose the date.', validate: (v) => v <= today() || 'Use an actual event date, not a future date.' })} /></Field>
      <Field id="outcome-stage" label="Stage (optional)" hint="For example: screening, technical interview, final round.">
        <input id="outcome-stage" maxLength={500} {...register('stage')} /></Field>
      {!application.applied_on && <Field id="outcome-submitted" label="Actual submission date"
        hint="Required for submitted stages if not already recorded. Leave blank only if withdrawing an unsent draft."
        error={errors.submitted_on?.message}>
        <input id="outcome-submitted" type="date" max={today()} {...register('submitted_on', {
          validate: (v) => ((!v && getValues('status') === 'withdrawn') || (!!v && v <= today())) || 'Record the actual submission date first.',
        })} /></Field>}
    </div>
    <Field id="outcome-notes" label="What happened? (optional)" error={errors.notes?.message}
      hint="Use a single line and semicolons instead of commas or double quotes (legacy tracker format).">
      <input id="outcome-notes" maxLength={20_000} {...register('notes', {
        validate: (value) => !/[,"\r\n]/.test(value) || 'Use a single line without commas or double quotes; semicolons are fine.',
      })} /></Field>
    <ErrorPanel error={action.error} />
    <div><Button type="submit" variant="secondary" busy={action.isPending}>Record progress</Button></div>
  </form>;
}

export function ApplicationDetailPage() {
  const { id = '' } = useParams();
  const [params] = useSearchParams();
  const activity = useActivity();
  const applicationQuery = useApiQuery<Application>(['application', id], `/applications/${encodeURIComponent(id)}`);
  const [kind, setKind] = useState<DocumentKind>(params.get('document') === 'cover_letter' ? 'cover_letter' : 'cv');
  const [checksOpen, setChecksOpen] = useState(params.get('tab') === 'checks');
  const [editIssue, setEditIssue] = useState<{ kind: DocumentKind; blockId?: string; request: number }>();
  const [dirty, setDirty] = useState(false);
  const [switchTo, setSwitchTo] = useState<DocumentKind | null>(null);
  const [instruction, setInstruction] = useState('');
  const application = applicationQuery.data;
  const hasDocument = !!application?.documents[kind];
  const document = useApiQuery<DocumentView>(['document', id, kind], `/applications/${encodeURIComponent(id)}/documents/${kind}`, {
    enabled: hasDocument,
  });
  const profile = useApiQuery<ProfileView>(['profile'], '/profile', { enabled: !!application });
  const relatedRun = activity.allRuns.find((run) => run.kind === 'documents' && run.input.application_id === id && isActiveRun(run));
  const lastRunId = relatedRun?.id || application?.latest_run_id || application?.last_run_id || params.get('run');
  const lastRun = useApiQuery<Run>(['run', lastRunId], `/runs/${encodeURIComponent(lastRunId ?? '')}`, {
    enabled: !!lastRunId,
    refetchInterval: (query) => query.state.data && isActiveRun(query.state.data) ? 3_000 : false,
  });
  const { queued } = useNotices();
  const documentsAction = useAction((requestedInstruction: string) => {
    if (!application) throw new Error('Wait for the application to finish loading.');
    return api<RunAccepted>(`/applications/${encodeURIComponent(id)}/document-runs`, {
      method: 'POST', body: {
        expected_revision: application.revision, instruction: requestedInstruction, repair: false,
        ...(!requestedInstruction ? { resume_from: lastRunId ?? null } : {}),
      } satisfies ApiSchema<'DocumentRunInput'>,
    });
  }, (run, requestedInstruction) => {
      queued(run, requestedInstruction ? 'Targeted rewrite queued. Review its before-and-after proposal in Activity; no rewrite is accepted automatically.' :
        'Remaining checks queued. Saved drafts are retained; only unchanged, successful review checkpoints can be reused. PDFs are not ready until verification passes.');
      setInstruction('');
    });
  const pdfAction = useAction((documentKind: DocumentKind) => {
    if (!application) throw new Error('Wait for the application to finish loading.');
    return api<RunAccepted>(`/applications/${encodeURIComponent(id)}/documents/${documentKind}/pdf-runs`, {
      method: 'POST', body: { expected_revision: application.revision } satisfies ApiSchema<'DocumentPdfInput'>,
    });
  }, (run) => queued(run, 'Draft PDF queued locally. No AI call or content changes; the PDF will be marked unverified.'));
  const pdfRunId = (pdfAction.variables === kind ? pdfAction.data?.run_id : undefined) ??
    activity.allRuns.find((run) => run.kind === 'document_pdf' && run.input.application_id === id && run.input.kind === kind)?.id;
  const pdfRun = useApiQuery<Run>(['run', pdfRunId], `/runs/${encodeURIComponent(pdfRunId ?? '')}`, {
    enabled: !!pdfRunId,
    refetchInterval: (query) => query.state.data && isActiveRun(query.state.data) ? 2_000 : false,
  });
  const repairAction = useAction(() => {
    if (!application) throw new Error('Wait for the application to finish loading.');
    return api<RunAccepted>(`/applications/${encodeURIComponent(id)}/document-runs`, {
      method: 'POST', body: { expected_revision: application.revision, instruction: '', repair: true } satisfies ApiSchema<'DocumentRunInput'>,
    });
  }, (run) => {
    setChecksOpen(true);
    queued(run, 'AI edits queued for the failed checks. Review and accept the before-and-after proposal before your saved drafts change. API charges may apply.');
  });
  useEffect(() => {
    if (application && !application.documents.cv && application.documents.cover_letter && kind === 'cv' && !dirty) setKind('cover_letter');
  }, [application, kind, dirty]);
  if (applicationQuery.isPending) return <Loading label="Opening your application workspace…" />;
  if (applicationQuery.error) return <ErrorPanel error={applicationQuery.error} retry={() => void applicationQuery.refetch()} />;
  if (!application) return null;
  const view = document.data ?? application.documents[kind];
  const runBusy = ['queued', 'running'].includes(relatedRun?.status ?? lastRun.data?.status ?? '') ||
    activity.allRuns.some((run) => run.kind === 'document_pdf' && run.input.application_id === id && ['queued', 'running'].includes(run.status)) ||
    ['queued', 'running'].includes(pdfRun.data?.status ?? '') || pdfAction.isPending || repairAction.isPending || documentsAction.isPending;
  const pending = lastRun.data?.approvals.filter((approval) => approval.status === 'pending') ?? [];
  const hasPair = !!application.documents.cv && !!application.documents.cover_letter;
  const verified = hasPair && !dirty && Object.values(application.documents).every((value) => value && isVerifiedDocument(value));
  const issues = application.content_issues ?? [];
  const legacyContentFailure = ['grounding_failed', 'company_claims_unverified', 'evidence_repair_failed', 'repair_limit_reached']
    .includes(lastRun.data?.error_code ?? '');
  const needsReview = pending.length > 0 || issues.length > 0 || legacyContentFailure;
  const currentProposal = pending.some((approval) => approval.proposal.expected_revision === application.revision);
  const editProposalPending = pending.some((approval) => approval.kind === 'document_rewrite');
  const editsDisabled = dirty || !hasPair || runBusy || editProposalPending;
  function choose(next: DocumentKind) {
    setChecksOpen(false);
    if (next === kind) return;
    if (dirty) setSwitchTo(next); else { setKind(next); setDirty(false); }
  }
  function editFinding(documentKind?: DocumentKind, blockId?: string) {
    const next = documentKind ?? kind;
    choose(next);
    setEditIssue((previous) => ({ kind: next, blockId, request: (previous?.request ?? 0) + 1 }));
  }
  return <div className="stack">
    <Link to="/applications" className="back-link">← All applications</Link>
    <PageHeader eyebrow={application.company} title={application.role}
      description={application.applied_on ? `Submitted ${formatDate(application.applied_on)} · Changes now create a new draft, not a replacement of sent files.` :
        ['drafted', 'preparing'].includes(application.status) ? 'A prepared application is a draft. You submit it yourself, outside this app.' :
          'A submission date has not been recorded. Review the imported status and add the missing history.'}
      actions={<Badge value={application.status} />} />
    <Panel title="Application documents" className="application-documents">
      <div className="actions"><Badge value={verified ? 'verified' : needsReview ? 'needs_review' : runBusy ? 'running' : 'unverified'} />
        <p className="hint">Saved text stays available while checks run or need attention. Nothing is submitted automatically.</p></div>
      <div className="document-topbar">
        <div className="segmented" role="group" aria-label="Document selection">
          <button aria-pressed={!checksOpen && kind === 'cv'} onClick={() => choose('cv')}>CV</button>
          <button aria-pressed={!checksOpen && kind === 'cover_letter'} onClick={() => choose('cover_letter')}>Cover letter</button>
          <button aria-pressed={checksOpen} onClick={() => setChecksOpen(true)}>Checks{issues.length ? ` (${issues.length})` : ''}</button>
        </div>
        <Button busy={documentsAction.isPending} disabled={dirty || !hasPair || runBusy || currentProposal}
          onClick={() => { setChecksOpen(true); documentsAction.mutate(''); }}>Resume checks</Button>
      </div>
      <p className="hint">Resume checks may use your configured AI connection
        {typeof application.next_run_limit_usd === 'number' ? `, with your existing $${application.next_run_limit_usd.toFixed(2)} limit for the next operation` : ' and spending limit'}.
        It reuses completed reviews only when the document, profile, posting and policy still match.</p>
      <div className="actions">
        <Button variant="secondary" disabled={dirty || !hasDocument || runBusy} busy={pdfAction.isPending}
          onClick={() => pdfAction.mutate(kind)}>Generate PDF</Button>
        {!dirty && view?.draft_pdf_artifact_id && !view.draft_pdf_stale &&
          <a className="button button-secondary" href={artifactUrl(view.draft_pdf_artifact_id)} download>Download draft PDF (unverified)</a>}
      </div>
      <p className="hint">Generate PDF compiles the selected saved document locally without AI or changing its text.
        The download is marked UNVERIFIED DRAFT; failed checks do not block this preview or become passes.</p>
      <ErrorPanel error={pdfAction.error} />
      <ErrorPanel error={pdfRun.error} retry={() => void pdfRun.refetch()} />
      {pdfRun.data && <section aria-label="Draft PDF generation" className="stack">
        <div className="actions"><Badge value={pdfRun.data.status} /><span>{pdfRun.data.message}</span></div>
        <RunFailure run={pdfRun.data} />
        <RunActions run={pdfRun.data} />
      </section>}
      <ErrorPanel error={repairAction.error} />
      {typeof application.preparation_cost_usd === 'number' && <p className="hint">
        Recorded document API cost: <strong>${application.preparation_cost_usd.toFixed(2)}</strong>
        {typeof application.preparation_attempts === 'number' && ` across ${application.preparation_attempts} ${application.preparation_attempts === 1 ? 'attempt' : 'attempts'}`}.
        Interrupted requests may have additional provider charges.
      </p>}
      {needsReview && !checksOpen && <Alert tone="warning" title="Your draft needs review">
        Your generated text is available below. Open Checks to compare the proposed corrections, inspect supporting sources,
        or edit the wording yourself. A content finding does not automatically mean your Profile is wrong.
        <p><Button variant="secondary" onClick={() => setChecksOpen(true)}>Review issues & corrections</Button></p>
      </Alert>}
      <ErrorPanel error={documentsAction.error} />
      <ErrorPanel error={document.error} retry={() => void document.refetch()} />
      <ErrorPanel error={profile.error} retry={() => void profile.refetch()} />
      {document.isPending && hasDocument && <Loading label="Loading document blocks…" />}
      <div hidden={checksOpen}>
      {view ? <DocumentEditor key={`${id}-${kind}`} applicationId={id} kind={kind} view={view} initialPane="read"
        focusBlockId={editIssue?.kind === kind ? editIssue.blockId : undefined}
        editRequest={editIssue?.kind === kind ? editIssue.request : undefined}
        evidence={profile.data?.evidence ?? []} onDirtyChange={setDirty} /> : <Empty title={runBusy ? 'Your documents are being prepared.' : 'No document content yet.'}>
        {runBusy ? 'The first saved draft appears here before PDF verification finishes.' :
          'A full job evaluation and your explicit approval come before document preparation. Check Activity for a pending decision.'}
      </Empty>}
      {view?.pdf_artifact_id && <div className="actions">
        {isVerifiedDocument(view) && !dirty && <a href={artifactUrl(view.pdf_artifact_id)} download>Download {kind === 'cv' ? 'CV' : 'cover letter'} PDF</a>}
      </div>}
      </div>
      {checksOpen && <section id="checks" className="stack" aria-label="Application checks and corrections">
        <ul className="preparation-checklist" aria-label="Preparation checklist">
          <li><Badge value={hasPair ? 'pass' : 'pending'} /> Draft text saved</li>
          <li><Badge value={verified ? 'pass' : 'pending'} /> Independent factual & company review</li>
          <li><Badge value={application.documents.cv && isVerifiedDocument(application.documents.cv) && !dirty ? 'pass' : 'pending'} /> CV PDF checks</li>
          <li><Badge value={application.documents.cover_letter && isVerifiedDocument(application.documents.cover_letter) && !dirty ? 'pass' : 'pending'} /> Cover letter PDF checks</li>
        </ul>
        {application.review_checkpoint_available && <Alert title="A completed review is available">
          Next are compilation, mechanical PDF checks and rendered-page inspection if the saved review still matches.
          Changed wording or sources trigger the necessary review again; a cached review is not a verified PDF.
        </Alert>}
        {dirty && <Alert tone="warning">Your unsaved edits are preserved. Return to the document and save them before accepting corrections or resuming checks.</Alert>}
        {pending.map((approval) => <div key={approval.id}>
          {approval.proposal.expected_revision !== application.revision && <Alert tone="warning">
            The saved draft changed after this proposal. Its approval is disabled. Resume checks to use your saved edits instead.
          </Alert>}
          <ApprovalCard approval={approval} runKind="documents" disabled={dirty || approval.proposal.expected_revision !== application.revision}
            onEditIssue={editFinding}
            onGenerateEdits={() => repairAction.mutate()} generateEditsDisabled={editsDisabled} />
        </div>)}
        {!pending.length && <DocumentIssues issues={issues} evidence={profile.data?.evidence} onEditIssue={editFinding}
          onGenerateEdits={() => repairAction.mutate()} disabled={runBusy} generateEditsDisabled={editsDisabled} />}
        {Object.entries(application.documents).map(([name, value]) => value && <section key={name}>
          <h3>{name === 'cv' ? 'CV' : 'Cover letter'}</h3><DocumentChecks view={value} stale={value.stale || dirty}
            onGenerateEdits={() => repairAction.mutate()} disabled={editsDisabled} />
        </section>)}
        <p className="hint">AI edits use the failed checks and confirmed profile evidence. They may incur API charges and
          always require your acceptance before replacing saved text. Missing facts are never confirmed by AI.</p>
      </section>}
      <details className="rewrite-panel"><summary>Request a targeted rewrite</summary>
        <form className="stack" onSubmit={(event) => { event.preventDefault(); if (instruction.trim()) documentsAction.mutate(instruction); }}>
          <p>Describe a change in emphasis, structure, or tone. Review and explicitly approve the before-and-after proposal in Activity.
            If you need to correct a fact, <Link to="/profile">update and confirm it in your profile first</Link>.</p>
          <Field id="rewrite-instruction" label="What should change?">
            <textarea id="rewrite-instruction" value={instruction} required rows={3} maxLength={20_000}
              onChange={(event) => setInstruction(event.target.value)} /></Field>
          <div><Button variant="secondary" type="submit" busy={documentsAction.isPending}
            disabled={!instruction.trim() || dirty || !hasDocument || runBusy || editProposalPending}>Request targeted rewrite</Button></div>
        </form>
      </details>
    </Panel>
    <AIReadinessNote />
    {lastRun.isPending && lastRunId && <Loading label="Checking document operation…" />}
    <ErrorPanel error={lastRun.error} retry={() => void lastRun.refetch()} />
    {lastRun.data && <Panel title="Latest operation">
      <div className="actions"><Badge value={needsReview ? 'needs_review' : lastRun.data.status} />
        <strong>{humanize(lastRun.data.stage || lastRun.data.kind)}</strong></div>
      <p>{lastRun.data.message}</p>{lastRun.data.error && <Alert tone={legacyContentFailure ? 'warning' : 'danger'}>{lastRun.data.error}</Alert>}
      <p><Link to={`/activity#run-${encodeURIComponent(lastRun.data.id)}`}>Review operation & decisions →</Link></p>
      <RunActions run={lastRun.data} />
    </Panel>}
    {application.evaluation && <Panel title="Why this role"><details><summary>Full fit assessment</summary>
      <EvaluationView value={application.evaluation} /></details></Panel>}
    {application.review && <Panel title="Independent review">
      {!!application.review.assessment_warnings?.length && <Alert tone="warning" title="Assessment notes · separate from document checks">
        <ul>{application.review.assessment_warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
        The earlier assessment has not been silently rewritten or promoted to a Profile fact.
      </Alert>}
      {application.review.findings.length ? <ul className="review-findings">{application.review.findings.map((finding, index) =>
        <li key={index}><Badge value={finding.kind} /><p>{finding.message}</p>{finding.block_id && <span className="hint">Content reference: {finding.block_id}</span>}</li>)}</ul>
        : <p>No reviewer findings are recorded. This does not replace the verification results above.</p>}
      {!!application.review.sources.length && <details><summary>Company claims & independent sources</summary><ul>{application.review.sources.map((source, index) =>
        <li key={index}><p>{source.claim}</p><blockquote>{source.quote}</blockquote><ExternalLink href={source.url}>{source.url}</ExternalLink></li>)}</ul></details>}
    </Panel>}
    <section id="tracking" className="stack anchor-target">
      {!application.applied_on && <Panel title="Record your real submission"><SubmissionForm application={application} /></Panel>}
      <Panel title="Record a response or stage"><OutcomeForm application={application} /></Panel>
    </section>
    <Panel title="Application timeline">
      {application.timeline.length ? <ol className="timeline">{application.timeline.map((entry, index) =>
        <li key={index}><div className="timeline-dot" aria-hidden="true" /><div>
          <div className="actions"><Badge value={entry.status} /><span className="hint">{formatDate(entry.date)}</span></div>
          {entry.stage && <h3>{entry.stage}</h3>}{entry.notes && <p className="preserve-lines">{entry.notes}</p>}
        </div></li>)}</ol> : <p className="hint">Your first recorded stage will appear here.</p>}
      <details><summary>Local archive details</summary><p className="path-display">{application.archive_dir || 'No archive created yet.'}</p>
        <p className="hint">Application reference: {application.id}. Reapplications keep distinct records.</p></details>
    </Panel>
    <Modal open={!!switchTo} title="Switch documents without saving?" onClose={() => setSwitchTo(null)}>
      <p>Your unsaved edits will be discarded. Save the current document first if you want to keep them.</p>
      <div className="actions"><Button variant="secondary" onClick={() => setSwitchTo(null)}>Keep editing</Button>
        <Button onClick={() => { if (switchTo) { setKind(switchTo); setDirty(false); setSwitchTo(null); } }}>Discard edits & switch</Button></div>
    </Modal>
  </div>;
}
