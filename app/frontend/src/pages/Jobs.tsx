import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { api, queryString } from '../api';
import { useAction, useApiQuery, useNotices } from '../query';
import type { ApiSchema, Job, Page, Portal, RunAccepted, SettingsView } from '../types';
import { AIReadinessNote } from '../components/SetupControls';
import { displayValue, GateView, StrengthsAndGaps } from '../components/proposals';
import { Alert, ApprovalLink, Badge, Button, Empty, ErrorPanel, ExternalLink, Field, formatDate, Loading, Modal, PageHeader, Pager, Panel } from '../components/ui';

function SearchForm({ settings, portals }: { settings: SettingsView; portals: Portal[] }) {
  const { register, handleSubmit, formState: { errors } } = useForm<{
    query: string; location: string; country: string; portals: string[];
  }>({ defaultValues: {
    query: settings.settings.search_query, location: settings.settings.search_location,
    country: settings.settings.search_country, portals: portals.filter((p) => p.effective_enabled && p.supported).map((p) => p.id),
  } });
  const { queued } = useNotices();
  const action = useAction((values: { query: string; location: string; country: string; portals: string[] }) =>
    api<RunAccepted>('/search-runs', { method: 'POST', body: { ...values, country: values.country.toUpperCase(), limit: 20 } satisfies ApiSchema<'SearchInput'> }),
  (run) => queued(run, 'Search queued for up to 20 results. New postings will appear here as the search finishes.'));
  return <form className="stack" onSubmit={handleSubmit((values) => action.mutate(values))}>
    <div className="search-fields">
      <Field id="search-query" label="Role or keywords" error={errors.query?.message}>
        <input id="search-query" placeholder="What would you like to do next?" maxLength={500} {...register('query', { required: 'Enter a role or keyword.' })} /></Field>
      <Field id="search-location" label="Location" hint="City, region, or Remote">
        <input id="search-location" maxLength={500} {...register('location')} /></Field>
      <Field id="search-country" label="Country code" error={errors.country?.message}>
        <input id="search-country" maxLength={2} {...register('country', { validate: (v) => !v || /^[a-z]{2}$/i.test(v) || 'Use two letters.' })} /></Field>
    </div>
    <fieldset className="source-picker"><legend>Search these sources</legend>
      {portals.filter((portal) => portal.supported).map((portal) => <label className="checkbox-row" key={portal.id}>
        <input type="checkbox" value={portal.id} disabled={!portal.effective_enabled}
          {...register('portals', { validate: (v) => v.length > 0 || 'Select an available job source. Enable sources in Settings if needed.' })} />
        <span>{portal.label}{!portal.effective_enabled && <small> · {portal.enabled ? 'finish setup' : 'enable'} in Settings</small>}</span></label>)}
      <Link to="/settings">Manage sources →</Link>
      {errors.portals && <p role="alert" className="field-error">{errors.portals.message}</p>}
    </fieldset>
    <ErrorPanel error={action.error} />
    <div className="actions"><Button type="submit" busy={action.isPending}>Find jobs</Button>
      <span className="hint">Up to 20 results per search · Ranking is a separate step.</span></div>
    {action.data && <ApprovalLink runId={action.data.run_id} />}
  </form>;
}

type PostingInput = Omit<ApiSchema<'PostingInput'>, 'deadline'> & { deadline: string };

export function JobImportForm({ onImported }: { onImported?: () => void }) {
  const { register, handleSubmit, getValues, formState: { errors } } = useForm<PostingInput>({
    defaultValues: { title: '', company: '', location: '', url: '', text: '', deadline: '' },
  });
  const { queued } = useNotices();
  const action = useAction((values: PostingInput) => api<RunAccepted>('/jobs/imports', {
    method: 'POST', body: { ...values, deadline: values.deadline || null } satisfies ApiSchema<'PostingInput'>,
  }), (run) => { queued(run, 'Posting import queued. Review any source or employer confirmation requested in Activity.'); onImported?.(); });
  return <form className="stack" onSubmit={handleSubmit((values) => action.mutate(values))}>
    <p>Paste a public job URL or the actual posting text. For text-only imports, employer and title are required.
      Providing metadata for a URL helps confirm that the right role was retrieved.</p>
    <Field id="import-job-url" label="Posting URL" error={errors.url?.message}>
      <input id="import-job-url" type="url" maxLength={2048} {...register('url', {
        validate: (v) => !v || /^https?:\/\//i.test(v) || 'Use an HTTP or HTTPS URL.',
      })} /></Field>
    <div className="form-grid">
      <Field id="import-job-title" label="Job title" error={errors.title?.message}>
        <input id="import-job-title" maxLength={500} {...register('title', {
          validate: (v) => !!getValues('url').trim() || !!v.trim() || 'A title is required without a URL.',
        })} /></Field>
      <Field id="import-job-company" label="Employer" error={errors.company?.message}>
        <input id="import-job-company" maxLength={500} {...register('company', {
          validate: (v) => !!getValues('url').trim() || !!v.trim() || 'An employer is required without a URL.',
        })} /></Field>
      <Field id="import-job-location" label="Job location"><input id="import-job-location" maxLength={500} {...register('location')} /></Field>
      <Field id="import-job-deadline" label="Deadline (if known)"><input id="import-job-deadline" type="date" {...register('deadline')} /></Field>
    </div>
    <Field id="import-job-text" label="Posting text" hint="Rendered only as plain text. Paste the original requirements, not an AI summary."
      error={errors.text?.message}>
      <textarea id="import-job-text" rows={9} maxLength={120_000} {...register('text', {
        validate: (v) => !!getValues('url').trim() || !!v.trim() || 'Paste the posting text or provide its URL.',
      })} /></Field>
    <ErrorPanel error={action.error} />
    <Button type="submit" busy={action.isPending}>Import posting for review</Button>
  </form>;
}

function SaveJobButton({ job }: { job: Job }) {
  const { notify } = useNotices();
  const action = useAction(() => api<Job>(`/jobs/${encodeURIComponent(job.id)}`, {
    method: 'PATCH', body: { saved: !job.saved, expected_revision: job.revision } satisfies ApiSchema<'JobChange'>,
  }), () => notify(job.saved ? 'Job removed from your saved list.' : 'Job saved for later.'));
  return <div><Button variant="secondary" aria-pressed={job.saved} busy={action.isPending} onClick={() => action.mutate()}>
    {job.saved ? 'Saved ✓' : 'Save job'}</Button><ErrorPanel error={action.error} /></div>;
}

function FitSignal({ job }: { job: Job }) {
  return <div className="fit-signal">
    <span>Quick fit <strong>{job.fit == null || job.fit === '' ? 'Not assessed' : displayValue(job.fit)}</strong></span>
    {job.rank_score != null ? <span>Ranked triage <strong>{job.rank_score}/100</strong>{job.rank_verdict && ` · ${job.rank_verdict}`}</span>
      : <span className="hint">Not ranked yet</span>}
  </div>;
}

export function JobCard({ job, selected, onSelect }: { job: Job; selected: boolean; onSelect: () => void }) {
  return <article className={`job-card ${selected ? 'job-card-selected' : ''}`}>
    <div className="job-card-top"><label className="checkbox-row"><input type="checkbox" checked={selected}
      onChange={onSelect} /><span className="sr-only">Select {job.title} for ranking</span></label>
      <Badge value={job.status} /><span className="hint">{job.portal || job.source}</span></div>
    <div className="job-title"><div className="company-monogram" aria-hidden="true">{job.company.slice(0, 1) || '·'}</div>
      <div><h3><Link to={`/jobs/${encodeURIComponent(job.id)}`}>{job.title}</Link></h3><p>{job.company}</p></div></div>
    <p className="job-location">{job.location || 'Location not provided'}</p>
    <FitSignal job={job} />
    <dl className="inline-meta"><div><dt>Posted</dt><dd>{formatDate(job.posted_date)}</dd></div>
      <div><dt>Deadline</dt><dd>{formatDate(job.deadline)}</dd></div></dl>
    {job.last_fetch_error && <p className="field-error">Could not verify this posting. Check its source manually.</p>}
    {job.warnings.length > 0 && <p className="hint">{job.warnings.length} source {job.warnings.length === 1 ? 'warning' : 'warnings'} · read details before applying</p>}
    <div className="job-card-bottom"><Link to={`/jobs/${encodeURIComponent(job.id)}`}>Read posting →</Link><SaveJobButton job={job} /></div>
  </article>;
}

export function JobsPage() {
  const [params, setParams] = useSearchParams();
  const [filterText, setFilterText] = useState(params.get('q') ?? '');
  const [filterStatus, setFilterStatus] = useState(params.get('status') ?? '');
  const [cursors, setCursors] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importOpen, setImportOpen] = useState(false);
  const settings = useApiQuery<SettingsView>(['settings'], '/settings');
  const portals = useApiQuery<{ items: Portal[] }>(['portals'], '/portals');
  const cursor = cursors.at(-1);
  const queryParams = { q: params.get('q'), status: params.get('status'), limit: 50, cursor };
  const jobs = useApiQuery<Page<Job>>(['jobs', queryParams], `/jobs${queryString(queryParams)}`);
  const { queued } = useNotices();
  const rank = useAction((ids: string[]) => api<RunAccepted>('/ranking-runs', {
    method: 'POST', body: { job_ids: ids.slice(0, 10), limit: 10, rerank: false } satisfies ApiSchema<'RankingInput'>,
  }), (run, ids) => {
    queued(run, `Ranking queued for ${Math.min(ids.length, 10)} selected jobs.${ids.length > 10 ? ' Other selected jobs are deferred; select them again for another batch.' : ''} This is triage, not a full evaluation.`);
    setSelected(new Set());
  });
  return <div className="stack">
    <PageHeader eyebrow="Find your fit" title="Opportunities worth exploring"
      description="Search deliberately, compare honestly, and keep the roles that matter."
      actions={<Button variant="secondary" onClick={() => setImportOpen(true)}>+ Add a posting</Button>} />
    <AIReadinessNote />
    <Panel title="Find something new">
      {(settings.isPending || portals.isPending) && <Loading label="Loading search preferences…" />}
      <ErrorPanel error={settings.error} retry={() => void settings.refetch()} /><ErrorPanel error={portals.error} retry={() => void portals.refetch()} />
      {settings.data && portals.data && <SearchForm settings={settings.data} portals={portals.data.items} />}
    </Panel>
    <section aria-labelledby="saved-results-heading" className="stack">
      <div className="section-heading"><h2 id="saved-results-heading">Your job collection</h2><span className="hint">Local records · not a live completeness guarantee</span></div>
      <form className="filter-bar" onSubmit={(event) => {
        event.preventDefault(); setParams({ ...(filterText ? { q: filterText } : {}), ...(filterStatus ? { status: filterStatus } : {}) });
        setCursors([]); setSelected(new Set());
      }}>
        <Field id="jobs-filter" label="Search saved records"><input id="jobs-filter" value={filterText} onChange={(event) => setFilterText(event.target.value)}
          placeholder="Title, company, or keywords" /></Field>
        <Field id="jobs-status" label="Posting status"><select id="jobs-status" value={filterStatus} onChange={(event) => setFilterStatus(event.target.value)}>
          <option value="">All statuses</option><option value="new">New</option><option value="ranked">Ranked</option><option value="expired">Expired / retired</option>
        </select></Field><Button type="submit" variant="secondary">Filter records</Button>
      </form>
      <div className="selection-toolbar"><span>{selected.size} selected · ranks up to 10 per batch</span>
        <div className="actions"><Button variant="ghost" disabled={!selected.size || rank.isPending} onClick={() => setSelected(new Set())}>Clear selection</Button>
          <Button busy={rank.isPending} disabled={!selected.size} onClick={() => rank.mutate([...selected])}>Rank selected</Button></div></div>
      <ErrorPanel error={rank.error} />
      {jobs.isPending && <Loading label="Loading job records…" />}
      <ErrorPanel error={jobs.error} retry={() => void jobs.refetch()} />
      {jobs.data && (jobs.data.items.length ? <div className="job-grid">{jobs.data.items.map((job) =>
        <JobCard key={job.id} job={job} selected={selected.has(job.id)} onSelect={() => setSelected((old) => {
          const next = new Set(old); if (next.has(job.id)) next.delete(job.id); else next.add(job.id); return next;
        })} />)}</div> : <Panel><Empty title="Your next opportunity is out there."
          action={<Button variant="secondary" onClick={() => setImportOpen(true)}>Paste a job posting</Button>}>
          No jobs match these filters yet. Run a search above, or add a posting you’ve already found.</Empty></Panel>)}
      {jobs.data && <Pager total={jobs.data.total} count={jobs.data.items.length} hasNext={!!jobs.data.next_cursor} hasPrevious={cursors.length > 0}
        next={() => { if (jobs.data.next_cursor) setCursors((old) => [...old, jobs.data.next_cursor!]); setSelected(new Set()); }}
        previous={() => { setCursors((old) => old.slice(0, -1)); setSelected(new Set()); }} />}
      <p className="hint">Quick fit and ranked triage are advisory. A separate, full evaluation always comes before document preparation.
        No score is a prediction of hiring success.</p>
    </section>
    <Modal open={importOpen} title="Add a job posting" onClose={() => setImportOpen(false)}>
      <JobImportForm onImported={() => setImportOpen(false)} />
    </Modal>
  </div>;
}

export function JobDetailPage() {
  const { id = '' } = useParams();
  const query = useApiQuery<Job>(['job', id], `/jobs/${encodeURIComponent(id)}`);
  const [confirm, setConfirm] = useState(false);
  const { queued } = useNotices();
  const evaluate = useAction(() => api<RunAccepted>('/evaluation-runs', { method: 'POST', body: { job_id: id } satisfies ApiSchema<'EvaluationInput'> }),
    (run) => { setConfirm(false); queued(run, 'Full evaluation queued. You will review the assessment before any documents are prepared.'); });
  if (query.isPending) return <Loading label="Opening the job posting…" />;
  if (query.error) return <ErrorPanel error={query.error} retry={() => void query.refetch()} />;
  if (!query.data) return null;
  const job = query.data;
  return <div className="stack">
    <Link to="/jobs" className="back-link">← All opportunities</Link>
    <PageHeader eyebrow={job.company} title={job.title} description={job.location || 'Location not provided'}
      actions={<><SaveJobButton job={job} /><Button onClick={() => setConfirm(true)}>Check this job</Button></>} />
    <AIReadinessNote />
    <div className="actions"><Badge value={job.status} /><FitSignal job={job} /></div>
    {job.warnings.map((warning, i) => <Alert tone="warning" key={i}>{warning}</Alert>)}
    {job.last_fetch_error && <Alert tone="warning" title="Retrieval failed, not proof of closure">
      {job.last_fetch_error} Check the employer’s posting manually before relying on older text.</Alert>}
    {job.expired_reason && <Alert tone="warning" title={job.expired_reason === 'unreachable' ? 'Could not verify; check manually' : 'This listing has been retired'}>
      {job.expired_reason === 'unreachable' ? 'An unreachable source does not establish that the employer closed the role.' : job.expired_reason}</Alert>}
    <div className="detail-grid">
      <Panel title="The posting"><div className="posting-text">{job.description || 'Posting text has not been retrieved. Check the source or import the original text.'}</div></Panel>
      <div className="stack">
        <Panel title="Source & freshness">
          <dl className="metadata"><dt>Employer</dt><dd>{job.company}</dd><dt>Source</dt><dd>{job.portal || job.source}</dd>
            <dt>Source host</dt><dd>{job.source_host || 'Not recorded'}</dd>
            <dt>First seen</dt><dd>{formatDate(job.first_seen, true)}</dd>
            <dt>Posted</dt><dd>{formatDate(job.posted_date)}</dd><dt>Deadline</dt><dd>{formatDate(job.deadline)}</dd></dl>
          <ExternalLink href={job.url}>Open original posting</ExternalLink>
          <p className="hint path-display">{job.url}</p>
        </Panel>
        <Panel title="Restrictions & uncertainty">
          <GateView name="Language" value={job.language_gate} note={job.language_note} />
          <GateView name="Location" value={job.location_verdict} />
          <GateView name="Eligibility" value={job.eligibility_gate} />
          {!job.language_gate && !job.location_verdict && !job.eligibility_gate && <p className="hint">No restrictions have been assessed yet.</p>}
        </Panel>
      </div>
    </div>
    <Panel title="Ranked triage · not a full evaluation">
      <StrengthsAndGaps strengths={job.strengths} gaps={job.gaps} />
      <p className="hint">Check the full job evaluation before preparing an application. <Link to="/profile">Review your confirmed profile →</Link></p>
    </Panel>
    <ErrorPanel error={evaluate.error} />
    {evaluate.data && <ApprovalLink runId={evaluate.data.run_id} />}
    <Modal open={confirm} title="Run a full fit evaluation?" onClose={() => { if (!evaluate.isPending) setConfirm(false); }}>
      <p>The app will compare this posting with your confirmed profile, check restrictions, and research the company.
        Selected context will be sent to your configured AI provider and may incur API charges.</p>
      <p><strong>No documents are prepared until you review the assessment and choose to proceed.</strong></p>
      <ErrorPanel error={evaluate.error} />
      <div className="actions"><Button variant="secondary" disabled={evaluate.isPending} onClick={() => setConfirm(false)}>Not now</Button>
        <Button busy={evaluate.isPending} onClick={() => evaluate.mutate()}>Evaluate fit</Button></div>
    </Modal>
  </div>;
}
