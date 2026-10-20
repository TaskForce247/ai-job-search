import { Link } from 'react-router-dom';
import { useApiQuery } from '../query';
import type { Application, Dashboard, SettingsView } from '../types';
import { Alert, Badge, Empty, ErrorPanel, formatDate, humanize, Loading, PageHeader, Panel } from '../components/ui';

function ApplicationRow({ application }: { application: Application }) {
  return <Link to={`/applications/${encodeURIComponent(application.id)}`} className="record-row">
    <div className="company-monogram" aria-hidden="true">{application.company.slice(0, 1) || '·'}</div>
    <div className="record-title"><strong>{application.role}</strong><span>{application.company}</span></div>
    <Badge value={application.status} />
    <span className="hint">{application.applied_on ? formatDate(application.applied_on)
      : ['drafted', 'preparing'].includes(application.status) ? 'Not sent' : 'Date not recorded'}</span>
    <span aria-hidden="true">→</span>
  </Link>;
}

export function DashboardPage() {
  const query = useApiQuery<Dashboard>(['dashboard'], '/dashboard');
  const settings = useApiQuery<SettingsView>(['settings'], '/settings');
  const data = query.data;
  const metrics = [
    ['drafted', 'Drafted', 'Prepared, never automatically sent'],
    ['submitted', 'Submitted', 'You recorded an actual submission'],
    ['active', 'Active', 'Applications still in progress'],
    ['interview', 'Interviews', 'Interview stage reached'],
  ] as const;
  const maxFunnel = Math.max(1, ...(data?.funnel.map((item) => item.count) ?? []));
  return <div className="stack">
    <PageHeader eyebrow="One step closer" title="Your search, at a glance."
      description="Make space for the right opportunity. Here’s what needs your attention."
      actions={<Link className="button button-primary" to="/jobs">Find jobs <span aria-hidden="true">↗</span></Link>} />
    {settings.data && !settings.data.settings.onboarding_complete && <Alert title="Let’s finish setting up">
      Choose your workspace, confirm your profile, and set your preferences. <Link to="/welcome">Resume guided setup →</Link>
    </Alert>}
    <ErrorPanel error={settings.error} retry={() => void settings.refetch()} />
    {query.isPending && <Loading label="Gathering your search activity…" />}
    <ErrorPanel error={query.error} retry={() => void query.refetch()} />
    {data && <>
      <div className="metric-grid">{metrics.map(([key, label, explanation]) =>
        <div className="metric-card" key={key}><span>{label}</span><strong>{data.counts[key]}</strong><p>{explanation}</p></div>)}</div>
      {data.pending_approvals > 0 && <Link to="/activity" className="attention-card">
        <span className="attention-icon" aria-hidden="true">✓</span><div><strong>{data.pending_approvals} decisions waiting for you</strong>
          <p>Review proposed changes before anything is applied.</p></div><span aria-hidden="true">→</span>
      </Link>}
      <div className="dashboard-grid">
        <Panel title="Your application pipeline" actions={<Link to="/applications">View all →</Link>}>
          {data.recent_applications.length ? <div className="record-list">{data.recent_applications.map((application) =>
            <ApplicationRow key={application.id} application={application} />)}</div>
            : <Empty title="A fresh start">Find a role that fits, or register an application you’ve already sent.
              Your drafts and submissions will appear here.</Empty>}
        </Panel>
        <Panel title="Next deadlines">
          {data.deadlines.length ? <ul className="deadline-list">{data.deadlines.map((application) => <li key={application.id}>
            <span className="date-chip">{formatDate(application.deadline)}</span>
            <Link to={`/applications/${encodeURIComponent(application.id)}`}><strong>{application.role}</strong><span>{application.company}</span></Link>
          </li>)}</ul> : <div className="quiet-empty"><h3>Room to plan.</h3><p>No upcoming application deadlines are recorded.</p></div>}
        </Panel>
      </div>
      <div className="dashboard-grid">
        <Panel title="The bigger picture">
          {data.funnel.length ? <div className="funnel">{data.funnel.map((item) => <div key={item.stage}>
            <div className="section-heading"><span>{humanize(item.stage)}</span><strong>{item.count}</strong></div>
            <div className="funnel-track" aria-hidden="true"><div style={{ width: `${item.count / maxFunnel * 100}%` }} /></div>
          </div>)}</div> : <p className="hint">Your funnel will grow as you record real application progress.</p>}
          <p className="hint">Historical stages are retained. Drafts are not submitted applications.</p>
          <div className="mini-metrics"><span>Offers <strong>{data.counts.offer}</strong></span><span>Hired <strong>{data.counts.hired}</strong></span>
            <span>Closed <strong>{data.counts.closed}</strong></span></div>
        </Panel>
        <Panel title="A thoughtful next move" className="next-move">
          <h3>Quality over quantity.</h3><p>A confirmed profile makes every evaluation and draft more useful.
            Add the experience, evidence, and preferences that make your story yours.</p>
          <Link className="button button-secondary" to="/profile">Review my profile →</Link>
          <p className="hint">You decide what to prepare. You submit every application yourself.</p>
        </Panel>
      </div>
    </>}
  </div>;
}
