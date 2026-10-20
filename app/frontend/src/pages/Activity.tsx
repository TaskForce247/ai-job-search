import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { api, artifactUrl, queryString } from '../api';
import { isActiveRun, useAction, useActivity, useApiQuery, useNotices } from '../query';
import type { ApiSchema, Approval, DocumentKind, Page, Run, RunAccepted } from '../types';
import { hasBlockingGate, ProposalView, StructuredDetails } from '../components/proposals';
import { Alert, Badge, Button, Empty, ErrorPanel, Field, formatDate, humanize, Loading, Modal, PageHeader, Pager, Panel } from '../components/ui';

function choiceLabel(choice: string, kind: string) {
  if (kind.includes('document_correction')) return choice === 'approve' ? 'Accept corrections & continue' : 'Keep saved draft';
  if (kind.includes('document_review') && choice === 'reject') return 'Keep saved draft';
  if (choice === 'prepare') return 'Prepare application documents';
  if (choice === 'not_now') return 'Not now';
  if (choice === 'reject') return 'Reject proposal';
  if (choice === 'approve') {
    if (kind.includes('reset')) return 'Approve reset';
    if (kind.includes('restore')) return 'Approve restore';
    if (kind.includes('profile')) return 'Confirm profile changes';
    return 'Approve changes';
  }
  return humanize(choice);
}

export function ApprovalCard({ approval, runKind = '', disabled = false, onEditIssue }: {
  approval: Approval; runKind?: string; disabled?: boolean;
  onEditIssue?: (kind?: DocumentKind, blockId?: string) => void;
}) {
  const [confirmation, setConfirmation] = useState('');
  const navigate = useNavigate();
  const { queued } = useNotices();
  const kind = `${approval.kind} ${runKind}`.toLowerCase();
  const requiredConfirmation = kind.includes('restore') ? 'RESTORE' : kind.includes('reset') ? 'RESET' : '';
  const blocked = hasBlockingGate(approval.proposal);
  const action = useAction((decision: string) => api<RunAccepted>(`/approvals/${encodeURIComponent(approval.id)}/decision`, {
    method: 'POST', body: {
      decision, expected_revision: approval.revision,
      confirmation: ['reject', 'not_now', 'cancel'].includes(decision) ? '' : confirmation,
    } satisfies ApiSchema<'Decision'>,
  }), (result, decision) => {
    queued({ ...result, run_id: result.preparation_run_id || result.run_id }, approval.kind === 'document_correction' && decision === 'approve'
      ? 'Corrections accepted. Remaining checks queued; unchanged verified review checkpoints can be reused. No Profile facts were changed.'
      : decision === 'prepare'
      ? 'Document preparation queued. Read saved drafts in the application workspace; wait for verification before using PDFs. Nothing is sent.' :
      ['reject', 'not_now'].includes(decision) ? 'Decision recorded. The proposal was not applied.' :
        'Decision recorded. Follow Activity to confirm the operation completes successfully.');
    setConfirmation('');
    if (result.application_id) navigate(`/applications/${encodeURIComponent(result.application_id)}${
      result.preparation_run_id ? `?run=${encodeURIComponent(result.preparation_run_id)}` : ''}`);
  });
  return <section className="approval-card" aria-labelledby={`approval-${approval.id}`}>
    <div className="section-heading"><h3 id={`approval-${approval.id}`}>{approval.title}</h3><Badge value={approval.status}>
      {approval.status === 'pending' ? 'Your decision needed' : humanize(approval.status)}</Badge></div>
    <ProposalView proposal={approval.proposal} kind={approval.kind} onEditIssue={onEditIssue} />
    {approval.status === 'pending' ? <div className="stack approval-actions">
      {requiredConfirmation && <Field id={`confirmation-${approval.id}`} label={`Type ${requiredConfirmation} to approve`}
        hint="This changes local files. Review the proposed changes and make a backup first.">
        <input id={`confirmation-${approval.id}`} autoComplete="off" spellCheck={false} value={confirmation}
          onChange={(event) => setConfirmation(event.target.value)} />
      </Field>}
      <ErrorPanel error={action.error} />
      <div className="actions">{approval.choices.filter((choice) => !(blocked && choice === 'prepare')).map((choice) => {
        const negative = ['reject', 'not_now', 'cancel'].includes(choice);
        return <Button key={choice} type="button"
          variant={negative ? 'secondary' : requiredConfirmation ? 'danger' : 'primary'}
          busy={action.isPending}
          disabled={!negative && (disabled || (!!requiredConfirmation && confirmation !== requiredConfirmation))}
          onClick={() => action.mutate(choice)}>{choiceLabel(choice, kind)}</Button>;
      })}</div>
      {blocked && <p className="field-error">Document preparation is unavailable because a required gate failed.
        Correct the facts and evaluate again.</p>}
      <p className="hint">This decision applies only to the revision shown. Changed inputs require a new proposal.</p>
    </div> : approval.status === 'invalidated' && <Alert tone="warning">This proposal is out of date.
      Its inputs changed and it can no longer be approved. Retry the operation using the latest facts.</Alert>}
  </section>;
}

export function RunActions({ run }: { run: Run }) {
  const [confirmCancel, setConfirmCancel] = useState(false);
  const { notify, queued } = useNotices();
  const cancel = useAction(() => api<unknown>(`/runs/${encodeURIComponent(run.id)}/cancel`, { method: 'POST', body: {} }),
    () => { setConfirmCancel(false); notify('Cancellation requested. Activity will show when the operation has stopped.'); });
  const retry = useAction(() => api<RunAccepted>(`/runs/${encodeURIComponent(run.id)}/retry`, { method: 'POST', body: {} }),
    (result) => queued(result, 'Retry queued using the server’s recovery checks. Review the new operation’s result in Activity.'));
  const applicationId = typeof run.input.application_id === 'string' ? run.input.application_id :
    typeof run.result?.application_id === 'string' ? run.result.application_id : undefined;
  return <div className="stack">
    <div className="actions">
      {isActiveRun(run) && <Button variant="secondary" disabled={retry.isPending}
        onClick={() => setConfirmCancel(true)}>Cancel operation</Button>}
      {['failed', 'interrupted', 'cancelled'].includes(run.status) && (run.kind === 'documents' && applicationId ?
        <Link className="button button-secondary" to={`/applications/${encodeURIComponent(applicationId)}?tab=checks#checks`}>Open draft & resume checks</Link> :
        <Button variant="secondary" busy={retry.isPending} onClick={() => retry.mutate()}>Retry operation</Button>)}
    </div>
    <ErrorPanel error={retry.error} />
    <Modal open={confirmCancel} title="Cancel this operation?" onClose={() => { if (!cancel.isPending) setConfirmCancel(false); }}>
      <p>Work will stop at a safe checkpoint. Already saved records remain available. No application will be submitted.</p>
      <ErrorPanel error={cancel.error} />
      <div className="actions"><Button variant="secondary" disabled={cancel.isPending} onClick={() => setConfirmCancel(false)}>Keep running</Button>
        <Button variant="danger" busy={cancel.isPending} onClick={() => cancel.mutate()}>Cancel operation</Button></div>
    </Modal>
  </div>;
}

function RunCard({ run }: { run: Run }) {
  const pending = run.approvals.filter((approval) => approval.status === 'pending');
  const otherApprovals = run.approvals.filter((approval) => approval.status !== 'pending');
  const applicationId = typeof run.result?.application_id === 'string' ? run.result.application_id :
    typeof run.input.application_id === 'string' ? run.input.application_id : undefined;
  return <Panel className={`run-card run-${run.status}`}>
    <div id={`run-${run.id}`} className="anchor-target" />
    <div className="section-heading"><div><p className="eyebrow">{humanize(run.kind)}</p>
      <h2>{humanize(run.stage || run.status)}</h2></div><Badge value={run.status} /></div>
    <p className="preserve-lines">{run.message || 'The operation has been recorded. Waiting for its next stage.'}</p>
    <div className="run-meta"><span>Started {formatDate(run.created_at, true)}</span>
      <span>Updated {formatDate(run.updated_at, true)}</span><span>API cost ${run.cost_usd.toFixed(2)}</span></div>
    {run.error && <Alert tone="danger" title={run.status === 'interrupted' ? 'Work was interrupted' : 'The operation did not complete'}>
      <p className="preserve-lines">{run.error}</p>{run.error_code && <p className="hint">Reference: {run.error_code}</p>}
      <p>Saved drafts are preserved. Fix the reported cause before retrying. A queued request is not a successful result.</p>
    </Alert>}
    {run.status === 'awaiting_user' && !pending.length && <Alert tone="warning">
      This operation is waiting for input but has no current decision available. Refresh Activity or retry after resolving changed inputs.</Alert>}
    {pending.map((approval) => <ApprovalCard key={approval.id} approval={approval} runKind={run.kind} />)}
    {applicationId && <p><Link to={`/applications/${encodeURIComponent(applicationId)}`}>Open the application workspace →</Link></p>}
    {applicationId && run.kind === 'documents' && <div className="actions">
      <Link to={`/applications/${encodeURIComponent(applicationId)}?document=cv`}>Open CV draft</Link>
      <Link to={`/applications/${encodeURIComponent(applicationId)}?document=cover_letter`}>Open cover letter draft</Link>
    </div>}
    {run.artifacts.length > 0 && <div className="artifact-list"><h3>Saved artifacts</h3>
      {run.artifacts.map((artifact) => <a key={artifact.id} href={artifactUrl(artifact.id)} download>
        Download {artifact.label || artifact.name || humanize(artifact.kind || 'file')}</a>)}</div>}
    <RunActions run={run} />
    <details><summary>Operation details & previous decisions</summary>
      <p className="hint">Operation reference: {run.id}</p>
      <h3>Approved inputs</h3><StructuredDetails value={run.input} />
      {run.result && <><h3>Result</h3><StructuredDetails value={run.result} /></>}
      {otherApprovals.map((approval) => <ApprovalCard key={approval.id} approval={approval} runKind={run.kind} />)}
    </details>
  </Panel>;
}

export function ActivityPage() {
  const current = useActivity();
  const [filter, setFilter] = useState('all');
  const [cursors, setCursors] = useState<string[]>([]);
  const cursor = cursors.at(-1);
  const older = useApiQuery<Page<Run>>(['runs-page', cursor], `/runs${queryString({ limit: 50, cursor })}`, {
    enabled: !!cursor, refetchInterval: cursor ? 5_000 : false,
  });
  const query = cursor ? older : current;
  const location = useLocation();
  useEffect(() => {
    if (!location.hash || !query.data) return;
    const id = decodeURIComponent(location.hash.slice(1));
    document.getElementById(id)?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  }, [location.hash, query.data]);
  const runs = (cursor ? query.data?.items ?? [] : current.allRuns).filter((run) => {
    if (filter === 'pending') return run.approvals.some((approval) => approval.status === 'pending');
    if (filter === 'active') return isActiveRun(run);
    if (filter === 'failed') return ['failed', 'interrupted'].includes(run.status);
    return true;
  }).sort((a, b) => Number(b.approvals.some((approval) => approval.status === 'pending')) -
    Number(a.approvals.some((approval) => approval.status === 'pending')));
  return <div className="stack">
    <PageHeader eyebrow="Nothing happens behind your back" title="Activity & decisions"
      description="Follow real stages, review proposals, and recover work that needs attention."
      actions={<Button variant="secondary" busy={query.isFetching} onClick={() => void query.refetch()}>Refresh activity</Button>} />
    <Alert>Operations keep running when you close a browser tab. A queued or running action is not complete;
      failed or interrupted work stays visible here. Decisions are never approved automatically.</Alert>
    {current.streamNotice && <Alert tone="warning">{current.streamNotice}</Alert>}
    <div className="filter-bar"><Field id="activity-filter" label="Show operations">
      <select id="activity-filter" value={filter} onChange={(event) => setFilter(event.target.value)}>
        <option value="all">All activity</option><option value="pending">Decisions needed</option>
        <option value="active">Active operations</option><option value="failed">Failed or interrupted</option>
      </select></Field><p className="hint">Updates automatically. Unfinished work from older pages stays visible.</p></div>
    {query.isPending && <Loading label="Loading operation history…" />}
    <ErrorPanel error={query.error} retry={() => void query.refetch()} />
    {query.data && (runs.length ? runs.map((run) => <RunCard key={run.id} run={run} />) :
      <Panel><Empty title={filter === 'pending' ? 'You’re all caught up.' : 'A clear activity log.'}>
        {filter === 'pending' ? 'There are no decisions waiting on this page.' :
          'Searches, imports, evaluations, document checks, and data changes appear here when you start them.'}
      </Empty></Panel>)}
    {query.data && <Pager total={query.data.total} count={runs.length} hasNext={!!query.data.next_cursor}
      hasPrevious={cursors.length > 0} next={() => query.data.next_cursor && setCursors((old) => [...old, query.data.next_cursor!])}
      previous={() => setCursors((old) => old.slice(0, -1))} />}
  </div>;
}
