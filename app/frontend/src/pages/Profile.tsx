import { Link, useSearchParams } from 'react-router-dom';
import { useApiQuery } from '../query';
import type { ProfileView } from '../types';
import { ProfileEditor } from '../components/ProfileEditor';
import { ProfileImport } from '../components/ProfileImport';
import { Alert, ErrorPanel, Loading, PageHeader, Panel } from '../components/ui';

export function ProfilePage() {
  const query = useApiQuery<ProfileView>(['profile'], '/profile');
  const [params] = useSearchParams();
  return <div className="stack">
    <PageHeader eyebrow="The source of your story" title="Your profile"
      description="Real experience, confirmed facts, and a clear picture of what comes next."
      actions={params.has('setup') && <Link className="button button-secondary" to="/welcome?step=3">Return to setup →</Link>} />
    <Panel title="Import an existing document"><ProfileImport /></Panel>
    {query.isPending && <Loading label="Loading your confirmed profile…" />}
    <ErrorPanel error={query.error} retry={() => void query.refetch()} />
    {query.data && <>
      {query.data.warnings.map((warning, index) => <Alert key={index} tone="warning">{warning}</Alert>)}
      <ProfileEditor view={query.data} />
      <Panel title="Confirmed evidence">
        <p className="hint">These sources support claims in your application documents. A draft is never a source for a new fact.</p>
        {query.data.evidence.length ? <div className="evidence-list">{query.data.evidence.map((evidence) =>
          <details key={evidence.id} id={`evidence-${evidence.id}`}>
            <summary>{evidence.section || 'Profile fact'} <span className="hint">· {evidence.source}</span></summary>
            <p className="preserve-lines">{evidence.text}</p><p className="hint">Evidence reference: {evidence.id}</p>
          </details>)}</div> : <p>No confirmed evidence yet. Save a profile proposal, then approve the facts in Activity.</p>}
      </Panel>
    </>}
  </div>;
}
