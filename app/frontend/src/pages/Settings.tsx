import { useRef, useState } from 'react';
import { api, artifactUrl } from '../api';
import { useAction, useActivity, useApiQuery, useNotices } from '../query';
import type { ApiSchema, RunAccepted, SettingsView } from '../types';
import { AIConnection, DependencyStatus, PortalControls, PreferencesForm, WorkspaceControls } from '../components/SetupControls';
import { Alert, ApprovalLink, Button, ErrorPanel, Field, Loading, Modal, PageHeader, Panel } from '../components/ui';

function DataControls() {
  const [backupPassphrase, setBackupPassphrase] = useState('');
  const [restorePassphrase, setRestorePassphrase] = useState('');
  const [restoreError, setRestoreError] = useState<string>();
  const [scope, setScope] = useState<'profile' | 'documents' | 'all'>('documents');
  const fileInput = useRef<HTMLInputElement>(null);
  const { queued, notify } = useNotices();
  const backup = useAction((passphrase: string) => api<{ artifact_id: string }>('/backups', { method: 'POST', body: { passphrase } satisfies ApiSchema<'BackupInput'> }),
    () => { setBackupPassphrase(''); notify('Backup created locally. Download and store it somewhere safe.'); });
  const restore = useAction((file: File) => {
    const body = new FormData(); body.append('file', file); body.append('passphrase', restorePassphrase);
    return api<RunAccepted>('/restores', { method: 'POST', body });
  }, (run) => {
    setRestorePassphrase(''); if (fileInput.current) fileInput.current.value = '';
    queued(run, 'Restore inspection queued. Review the proposal and type RESTORE in Activity before changes are made.');
  });
  const reset = useAction(() => api<RunAccepted>('/resets', { method: 'POST', body: { scope } satisfies ApiSchema<'ResetInput'> }),
    (run) => queued(run, 'Reset proposal created. Review exactly what will change and type RESET in Activity to approve.'));
  return <div className="stack">
    <Panel title="Back up your workspace">
      <p>Create a portable backup of local records. API credentials are not a reason to share a backup publicly;
        it still contains personal career information.</p>
      <form className="stack" onSubmit={(event) => { event.preventDefault(); backup.mutate(backupPassphrase); }}>
        <Field id="backup-passphrase" label="Backup passphrase (optional)" hint="Leave blank for an unencrypted ZIP. Keep a chosen passphrase somewhere safe; it cannot be recovered.">
          <input id="backup-passphrase" type="password" autoComplete="new-password" maxLength={500}
            value={backupPassphrase} onChange={(event) => setBackupPassphrase(event.target.value)} />
        </Field>
        <ErrorPanel error={backup.error} /><div><Button busy={backup.isPending} type="submit">Create backup</Button></div>
        {backup.data && <a className="button button-secondary" href={artifactUrl(backup.data.artifact_id)} download>Download backup</a>}
      </form>
    </Panel>
    <Panel title="Restore a backup">
      <p>Inspect a backup before restoring. Current files are not changed until you review and approve the restore in Activity.</p>
      <form className="stack" onSubmit={(event) => {
        event.preventDefault(); const file = fileInput.current?.files?.[0];
        if (!file || !file.size) { setRestoreError('Choose a non-empty workspace backup.'); return; }
        setRestoreError(undefined); restore.mutate(file);
      }}>
        <Field id="restore-file" label="Backup file" error={restoreError}>
          <input id="restore-file" type="file" ref={fileInput} aria-invalid={!!restoreError} onChange={() => { setRestoreError(undefined); restore.reset(); }} />
        </Field>
        <Field id="restore-passphrase" label="Backup passphrase" hint="Required only if the backup is encrypted.">
          <input id="restore-passphrase" type="password" autoComplete="off" maxLength={500}
            value={restorePassphrase} onChange={(event) => setRestorePassphrase(event.target.value)} />
        </Field>
        <ErrorPanel error={restore.error} /><div><Button variant="secondary" busy={restore.isPending} type="submit">Inspect restore</Button></div>
        {restore.data && <ApprovalLink runId={restore.data.run_id} />}
      </form>
    </Panel>
    <Panel title="Reset selected data" className="danger-panel">
      <Alert tone="warning">Reset is destructive. Make a backup first. This step only creates a proposal;
        approving it requires the exact word RESET in Activity.</Alert>
      <form className="stack" onSubmit={(event) => { event.preventDefault(); reset.mutate(); }}>
        <Field id="reset-scope" label="Data to reset">
          <select id="reset-scope" value={scope} onChange={(event) => setScope(event.target.value as typeof scope)}>
            <option value="documents">Generated documents</option><option value="profile">Candidate profile</option>
            <option value="all">All workspace data</option>
          </select>
        </Field>
        <ErrorPanel error={reset.error} />
        <div><Button variant="danger" type="submit" busy={reset.isPending}>Review reset proposal</Button></div>
        {reset.data && <ApprovalLink runId={reset.data.run_id} />}
      </form>
    </Panel>
  </div>;
}

function ShutdownControl() {
  const [open, setOpen] = useState(false);
  const [closed, setClosed] = useState(false);
  const runs = useActivity();
  const active = runs.allRuns.filter((run) => ['running', 'queued', 'awaiting_user'].includes(run.status)).length;
  const action = useAction(() => api<unknown>('/system/shutdown', { method: 'POST', body: {} }),
    () => { setClosed(true); setOpen(false); });
  return <Panel title="Stop the backend">
    <p>Closing this tab does not stop work. Stop here or press Ctrl+C in the backend terminal.
      Stop a separate frontend server with Ctrl+C in its own terminal.</p>
    {closed ? <Alert title="The backend is stopping">Run the backend command again and open its fresh login link to continue.</Alert>
      : <Button variant="secondary" onClick={() => setOpen(true)}>Stop backend</Button>}
    <Modal open={open} title="Stop the local backend?" onClose={() => { if (!action.isPending) setOpen(false); }}>
      <p>{active ? `${active} operations are active or waiting for a decision. They will stop safely and retain recovery checkpoints.` :
        'The local service will stop. Your saved workspace will remain on this computer.'}</p>
      <ErrorPanel error={action.error} />
      <div className="actions"><Button variant="secondary" disabled={action.isPending} onClick={() => setOpen(false)}>Keep open</Button>
        <Button variant="danger" busy={action.isPending} onClick={() => action.mutate()}>Stop safely</Button></div>
    </Modal>
  </Panel>;
}

export function SettingsPage() {
  const query = useApiQuery<SettingsView>(['settings'], '/settings');
  return <div className="stack">
    <PageHeader eyebrow="Your workspace, your control" title="Settings"
      description="Manage cloud assistance, job sources, local tools, and your data." />
    {query.isPending && <Loading label="Loading settings…" />}
    <ErrorPanel error={query.error} retry={() => void query.refetch()} />
    {query.data && <>
      <Panel title="AI connection"><AIConnection connected={query.data.ai_connected} /></Panel>
      <Panel title="Privacy, limits & search preferences"><PreferencesForm view={query.data} /></Panel>
    </>}
    <Panel title="Job sources"><PortalControls /></Panel>
    <Panel title="Workspace"><WorkspaceControls /></Panel>
    <Panel title="Local tools & diagnostics"><DependencyStatus /></Panel>
    <DataControls />
    <ShutdownControl />
  </div>;
}
