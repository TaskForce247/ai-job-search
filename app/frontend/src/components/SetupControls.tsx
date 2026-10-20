import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useAction, useApiQuery, useNotices } from '../query';
import type { ApiSchema, Dependency, Portal, Readiness, RunAccepted, Settings, SettingsView, Workspace } from '../types';
import { Alert, ApprovalLink, Badge, Button, ErrorPanel, ExternalLink, Field, Loading, Modal, Panel } from './ui';
import { StructuredDetails } from './proposals';

export function CloudPrivacyExplanation() {
  return <div className="privacy-copy">
    <p><strong>Local workspace. Explicit cloud assistance.</strong> Your profile, saved postings, and application
      history are stored on this computer. Searching job boards makes requests to those sites.</p>
    <p>When you use AI, selected profile excerpts, job postings, drafts, research context, and rendered
      document pages used for checking may be sent to Anthropic. The entire workspace is not sent by default.</p>
    <p><strong>API usage is billed separately.</strong> A Claude Pro or Max consumer subscription does not pay
      for this app’s API calls. You control the API account and per-run spending limits.</p>
  </div>;
}

export function AIConnection({ connected }: { connected: boolean }) {
  const { register, handleSubmit, reset, formState: { errors } } = useForm<{ api_key: string }>({ defaultValues: { api_key: '' } });
  const { notify, queued } = useNotices();
  const save = useAction((values: ApiSchema<'AIConnection'>) =>
    api<unknown>('/settings/ai-connection', { method: 'POST', body: values }),
  () => { reset({ api_key: '' }); notify('API key saved to the operating system credential store. Run a connection test to verify it.'); });
  const test = useAction(() => api<RunAccepted>('/settings/ai-connection/test', { method: 'POST', body: {} }),
    (run) => queued(run, 'Connection test queued. Check Activity for the actual result.'));
  return <div className="stack">
    <div className="section-heading"><h3>Claude API connection</h3><Badge value={connected ? 'ready' : 'setup_required'}>
      {connected ? 'Key configured' : 'Not connected'}</Badge></div>
    <CloudPrivacyExplanation />
    <form className="stack" onSubmit={handleSubmit((values) => save.mutate(values))}>
      <Field id="api-key" label={connected ? 'Replace API key' : 'API key'}
        hint="Stored only in your OS credential store, not in browser storage. The field is cleared after saving."
        error={errors.api_key?.message}>
        <input id="api-key" type="password" autoComplete="off" spellCheck={false} maxLength={500}
          aria-invalid={!!errors.api_key} aria-describedby="api-key-hint"
          {...register('api_key', { required: 'Enter an API key.', minLength: { value: 10, message: 'The API key is too short.' } })} />
      </Field>
      <ErrorPanel error={save.error} /><ErrorPanel error={test.error} />
      <div className="actions">
        <Button type="submit" busy={save.isPending} disabled={test.isPending}>Save API connection</Button>
        <Button type="button" variant="secondary" disabled={!connected || save.isPending}
          busy={test.isPending} onClick={() => test.mutate()}>Test connection</Button>
      </div>
      {test.data && <ApprovalLink runId={test.data.run_id} />}
    </form>
  </div>;
}

export function PrivacyConsentControl({ view }: { view: SettingsView }) {
  const [consent, setConsent] = useState(view.settings.privacy_consent);
  const { notify } = useNotices();
  useEffect(() => setConsent(view.settings.privacy_consent), [view.settings.privacy_consent]);
  const action = useAction(() => api<unknown>('/settings', {
    method: 'PATCH', body: { settings: { ...view.settings, privacy_consent: consent }, expected_revision: view.revision } satisfies ApiSchema<'SettingsChange'>,
  }), () => notify('Data-sharing preference saved.'));
  return <form className="stack" onSubmit={(event) => { event.preventDefault(); action.mutate(); }}>
    <label className="checkbox-row"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
      <span>I understand the cloud data sharing and separate API billing, and consent to AI processing when I request it.</span></label>
    <ErrorPanel error={action.error} />
    <div><Button type="submit" variant="secondary" busy={action.isPending}
      disabled={consent === view.settings.privacy_consent}>Save data-sharing choice</Button></div>
  </form>;
}

export function DependencyStatus({ compact = false }: { compact?: boolean }) {
  const query = useApiQuery<Readiness>(['readiness'], '/system/readiness', { refetchInterval: 15_000 });
  const [repair, setRepair] = useState<Dependency | null>(null);
  const [accepted, setAccepted] = useState(false);
  const { queued } = useNotices();
  const action = useAction((dependency: Dependency) =>
    api<RunAccepted>(`/system/dependencies/${encodeURIComponent(dependency.id)}/repair`, {
      method: 'POST', body: { accepted: true } satisfies ApiSchema<'AcceptedInput'>,
    }), (run) => { queued(run, 'Dependency setup queued. Follow the guided progress in Activity.'); setRepair(null); setAccepted(false); });
  if (query.isPending) return <Loading label="Checking local tools…" />;
  if (query.error) return <ErrorPanel error={query.error} retry={() => void query.refetch()} />;
  if (!query.data) return null;
  return <div className="stack">
    {!compact && <p className="hint">Install local tools using the application README. Guided repair is available where supported.</p>}
    <div className="dependency-list">{query.data.dependencies.map((dependency) =>
      <div key={dependency.id} className="dependency-row">
        <div><div className="actions"><strong>{dependency.label}</strong><Badge value={dependency.status} /></div>
          <p className="hint">{dependency.description}</p></div>
        {dependency.can_install && (dependency.status !== 'ready' || dependency.id === 'tex') &&
          <Button variant="secondary" busy={action.isPending}
            onClick={() => { setRepair(dependency); setAccepted(false); action.reset(); }}>
            {dependency.status === 'ready' ? 'Repair' : 'Set up'} {dependency.label}
          </Button>}
        {dependency.status !== 'ready' && !dependency.can_install && dependency.repair_url &&
          <ExternalLink href={dependency.repair_url}>Setup instructions</ExternalLink>}
      </div>)}</div>
    <p className="hint">App version {query.data.version} · {query.data.active_runs} active operations</p>
    <Modal open={repair !== null} title={`Set up ${repair?.label ?? 'dependency'}?`} onClose={() => { if (!action.isPending) setRepair(null); }}>
      <p>{repair?.description}</p><p>The app will run its approved installation or repair process on this computer.
        This may download large files and accept the dependency's license and package-source agreements.
        Progress and any errors will appear in Activity.</p>
      {repair?.repair_url && <ExternalLink href={repair.repair_url}>Review the provider's setup and license information</ExternalLink>}
      <label className="checkbox-row"><input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} />
        <span>I approve this installation or repair and accept the provider's license and source agreements.</span></label>
      <ErrorPanel error={action.error} />
      <div className="actions"><Button variant="secondary" disabled={action.isPending} onClick={() => setRepair(null)}>Cancel</Button>
        <Button busy={action.isPending} disabled={!accepted || !repair} onClick={() => repair && action.mutate(repair)}>Start guided setup</Button></div>
    </Modal>
  </div>;
}

export function WorkspaceControls() {
  const query = useApiQuery<Workspace>(['workspace'], '/workspace');
  const client = useQueryClient();
  const [path, setPath] = useState('');
  const [selected, setSelected] = useState<ApiSchema<'WorkspaceInput'> | null>(null);
  const [inspectOpen, setInspectOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const { notify, queued } = useNotices();
  const change = useAction((selection: ApiSchema<'WorkspaceInput'>) =>
    api<Workspace>('/workspace', { method: 'POST', body: selection }),
  async (_workspace, selection) => {
    await client.cancelQueries();
    await client.resetQueries();
    setInspectOpen(!selection.create);
    setPath(''); setSelected(null); setConfirmOpen(false);
    notify(selection.create ? 'Local workspace created.' : 'Workspace selected. Inspect existing files to review a proposed import.');
  });
  const importFiles = useAction(() => api<RunAccepted>('/workspace/imports', { method: 'POST', body: {} }),
    (run) => queued(run, 'Existing-workspace inspection queued. Review discovered records before any import changes are applied.'));
  return <div className="stack">
    {query.isPending && <Loading label="Opening workspace details…" />}
    <ErrorPanel error={query.error} retry={() => void query.refetch()} />
    {query.data && <div><p className="hint">Current local folder</p><p className="path-display">{query.data.path}</p>
      <Badge value={query.data.initialized ? 'ready' : 'setup_required'}>
        {query.data.initialized ? 'Workspace ready' : 'Setup not completed'}</Badge></div>}
    {query.data?.public_repository_warning && <Alert tone="warning" title="Personal files may be tracked in Git">
      {typeof query.data.public_repository_warning === 'string' ? query.data.public_repository_warning :
        'This folder may be part of a public repository. Profile changes could later be published by a Git push. The app never commits or pushes your data.'}
    </Alert>}
    <p>Your workspace contains career information and document history. Choose a private folder you control.
      Existing files are inspected and proposed changes require approval.</p>
    <Field id="workspace-path" label="Workspace folder path" hint="Enter the full absolute path on this computer. A new workspace needs an empty folder.">
      <input id="workspace-path" value={path} maxLength={4096} aria-describedby="workspace-path-hint"
        onChange={(event) => { setPath(event.target.value); setSelected(null); }} />
    </Field>
    <div className="actions">
      <Button variant="secondary" disabled={!path.trim() || change.isPending}
        onClick={() => { setSelected({ path, create: true }); setConfirmed(false); }}>Choose a new workspace folder</Button>
      <Button variant="secondary" disabled={!path.trim() || change.isPending}
        onClick={() => { setSelected({ path, create: false }); setConfirmed(false); }}>Choose an existing workspace</Button>
    </div>
    <ErrorPanel error={change.error} />
    {selected && <div className="selected-folder"><p className="eyebrow">{selected.create ? 'New workspace' : 'Existing workspace'}</p>
      <p className="path-display">{selected.path}</p>
      <div className="actions"><Button onClick={() => setConfirmOpen(true)}>
        {selected.create ? 'Create here' : 'Use this folder'}</Button>
        <Button variant="ghost" onClick={() => { setSelected(null); notify('Folder selection cancelled. Your workspace is unchanged.'); }}>Cancel selection</Button></div>
    </div>}
    {query.data?.path && <details open={inspectOpen} onToggle={(event) => setInspectOpen(event.currentTarget.open)}>
      <summary>Import existing records from this workspace</summary>
      <p>Selecting a folder does not approve an import. Inspect profile and application records, including proposed identity migrations,
        before accepting any changes. This inspects only the current workspace.</p>
      <Button variant="secondary" busy={importFiles.isPending} onClick={() => importFiles.mutate()}>Inspect existing files</Button>
      <ErrorPanel error={importFiles.error} />
      {importFiles.data && <p><ApprovalLink runId={importFiles.data.run_id} /></p>}
    </details>}
    <Modal open={confirmOpen} title={selected?.create ? 'Create this workspace?' : 'Switch to this workspace?'}
      onClose={() => { if (!change.isPending) setConfirmOpen(false); }}>
      <p className="path-display">{selected?.path}</p>
      <p>Unsaved edits on other screens are not moved to the selected workspace.
        Existing records are never silently overwritten. Active operations may prevent switching.</p>
      <label className="checkbox-row"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
        <span>I have saved my edits and want to use this private local folder.</span></label>
      <ErrorPanel error={change.error} />
      <div className="actions"><Button variant="secondary" disabled={change.isPending} onClick={() => setConfirmOpen(false)}>Cancel</Button>
        <Button busy={change.isPending} disabled={!confirmed || !selected}
          onClick={() => selected && change.mutate(selected)}>Confirm workspace</Button></div>
    </Modal>
  </div>;
}

export function PortalControls() {
  const query = useApiQuery<{ items: Portal[] }>(['portals'], '/portals');
  const [choice, setChoice] = useState<Portal | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const { notify } = useNotices();
  const action = useAction((input: { portal: Portal; enabled: boolean; acknowledged: boolean }) =>
    api<unknown>(`/portals/${encodeURIComponent(input.portal.id)}/settings`, {
      method: 'PATCH', body: { enabled: input.enabled, acknowledged: input.acknowledged, expected_revision: input.portal.revision } satisfies ApiSchema<'PortalChange'>,
    }), (_result, input) => { setChoice(null); notify(`${input.portal.label} ${input.enabled ? 'enabled' : 'disabled'}.`); });
  if (query.isPending) return <Loading label="Checking job sources…" />;
  if (query.error) return <ErrorPanel error={query.error} retry={() => void query.refetch()} />;
  return <div className="stack">
    <p>Sources can differ in coverage and availability. Danish boards are opt-in.
      freehire focuses on technical roles. Enable only sources appropriate to your search.</p>
    <p className="hint">Ready to search means the local tools are installed, not that the source is currently reachable.</p>
    <div><Button variant="secondary" busy={query.isFetching} onClick={() => void query.refetch()}>Refresh sources</Button></div>
    <ErrorPanel error={action.error} />
    {(query.data?.items ?? []).map((portal) => <div key={portal.id} className="portal-row">
      <div><div className="actions"><h3>{portal.label}</h3><Badge value={portal.effective_enabled ? 'ready' : portal.enabled ? 'setup_required' : 'optional'}>
        {portal.effective_enabled ? 'Enabled · ready to search' : portal.enabled ? 'Enabled · setup required' : 'Disabled'}</Badge></div>
      {portal.warning && <p className="hint">{portal.warning}</p>}
      {!portal.supported && <p className="field-error">This source is not supported by the current app.</p>}
      {portal.supported && portal.runtime_ready === false && <p className="hint">Local setup is incomplete.
        Follow "Job search and PDF tools" in the application README to install Bun and the portal dependencies, then refresh sources.</p>}
      <details><summary>Source health</summary>
        {(typeof portal.health === 'string' ? portal.health : portal.health.status) === 'not_checked'
          ? <p className="hint">Not searched yet. Live availability is checked when you run a search.</p>
          : <StructuredDetails value={portal.health} />}
      </details></div>
      <div className="actions">
      {portal.enabled && !portal.effective_enabled && portal.id.toLowerCase().includes('linkedin') &&
        <Button variant="secondary" busy={action.isPending} onClick={() => { setChoice(portal); setAcknowledged(false); }}>
          Review access warning
        </Button>}
      <Button variant="secondary" busy={action.isPending} disabled={!portal.supported}
        onClick={() => {
          if (!portal.enabled && (portal.warning || portal.id.toLowerCase().includes('linkedin'))) {
            setChoice(portal); setAcknowledged(false);
          } else action.mutate({ portal, enabled: !portal.enabled, acknowledged: false });
        }}>{portal.enabled ? 'Disable' : 'Enable'} {portal.label}</Button>
      </div>
    </div>)}
    <Modal open={!!choice} title={`Enable ${choice?.label ?? 'source'}?`} onClose={() => { if (!action.isPending) setChoice(null); }}>
      <Alert tone="warning">{choice?.warning || 'Automated access to LinkedIn may conflict with its terms and may be blocked. Do not bypass access restrictions.'}</Alert>
      <label className="checkbox-row"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />
        <span>I have read the source warning and choose to enable this source.</span></label>
      <ErrorPanel error={action.error} />
      <div className="actions"><Button variant="secondary" disabled={action.isPending} onClick={() => setChoice(null)}>Keep disabled</Button>
        <Button busy={action.isPending} disabled={!acknowledged} onClick={() => choice && action.mutate({ portal: choice, enabled: true, acknowledged })}>Enable source</Button></div>
    </Modal>
  </div>;
}

export function PreferencesForm({ view, onComplete, searchOnly = false }: {
  view: SettingsView; onComplete?: () => void; searchOnly?: boolean;
}) {
  const { register, handleSubmit, reset, formState: { errors, isDirty } } = useForm<Settings>({ defaultValues: view.settings });
  const [revision, setRevision] = useState(view.revision);
  const { notify } = useNotices();
  const save = useAction((settings: Settings) => api<unknown>('/settings', {
    method: 'PATCH', body: {
      settings: { ...settings, search_country: settings.search_country.toUpperCase(), ...(onComplete ? { onboarding_complete: true } : {}) },
      expected_revision: revision,
    } satisfies ApiSchema<'SettingsChange'>,
  }), (_result, values) => { reset(values); notify('Preferences saved.'); onComplete?.(); });
  useEffect(() => {
    if (view.revision !== revision && !isDirty) { reset(view.settings); setRevision(view.revision); }
  }, [view, revision, isDirty, reset]);
  return <form className="stack" onSubmit={handleSubmit((values) => save.mutate(values))}>
    {view.revision !== revision && isDirty && <Alert tone="warning">Settings changed elsewhere.
      <Button type="button" variant="ghost" onClick={() => { reset(view.settings); setRevision(view.revision); }}>Discard edits and reload settings</Button></Alert>}
    {!searchOnly && <>
      <CloudPrivacyExplanation />
      <label className="checkbox-row"><input type="checkbox" {...register('privacy_consent')} />
        <span>I consent to sending selected context to Anthropic when I request AI assistance, and understand separate API billing.</span></label>
      <div className="form-grid">
        <Field id="settings-model" label="Claude API model" hint="A supported Anthropic model identifier." error={errors.model?.message}>
          <input id="settings-model" maxLength={500} {...register('model', { required: 'Enter a model identifier.' })} /></Field>
        <Field id="settings-cost" label="Maximum cost per operation (USD)" error={errors.max_run_cost_usd?.message}>
          <input id="settings-cost" type="number" step="0.05" min="0.05" max="100"
            {...register('max_run_cost_usd', { valueAsNumber: true, required: 'Enter a spending limit.', min: { value: 0.05, message: 'Minimum is $0.05.' }, max: { value: 100, message: 'Maximum is $100.' } })} /></Field>
        <Field id="settings-turns" label="Maximum AI turns" error={errors.max_turns?.message}>
          <input id="settings-turns" type="number" min="1" max="30" step="1"
            {...register('max_turns', { valueAsNumber: true, required: 'Enter a turn limit.', min: { value: 1, message: 'Use 1–30 turns.' }, max: { value: 30, message: 'Use 1–30 turns.' }, validate: (v) => Number.isInteger(v) || 'Use a whole number.' })} /></Field>
        <Field id="settings-timeout" label="Stage timeout (seconds)" error={errors.stage_timeout_seconds?.message}>
          <input id="settings-timeout" type="number" min="30" max="600" step="1"
            {...register('stage_timeout_seconds', { valueAsNumber: true, required: 'Enter a timeout.', min: { value: 30, message: 'Use 30–600 seconds.' }, max: { value: 600, message: 'Use 30–600 seconds.' }, validate: (v) => Number.isInteger(v) || 'Use a whole number.' })} /></Field>
      </div>
    </>}
    <div className="form-grid">
      <Field id="settings-query" label="Default job search" hint="Role or keywords, not a prompt.">
        <input id="settings-query" maxLength={500} {...register('search_query')} /></Field>
      <Field id="settings-location" label="Search location" hint="Enter your city, region, or Remote.">
        <input id="settings-location" maxLength={500} {...register('search_location')} /></Field>
      <Field id="settings-country" label="Country code" hint="Optional two-letter code, such as DK or GB." error={errors.search_country?.message}>
        <input id="settings-country" maxLength={2} autoCapitalize="characters" {...register('search_country', {
          validate: (v) => !v || /^[a-z]{2}$/i.test(v) || 'Use a two-letter country code.',
        })} /></Field>
    </div>
    <ErrorPanel error={save.error} />
    <div className="actions"><Button busy={save.isPending} type="submit">{onComplete ? 'Finish setup & open dashboard' : 'Save preferences'}</Button>
      {!searchOnly && <Link to="/welcome">Revisit guided setup</Link>}</div>
  </form>;
}

export function AIReadinessNote() {
  const query = useApiQuery<Readiness>(['readiness'], '/system/readiness');
  if (query.error) return <ErrorPanel error={query.error} retry={() => void query.refetch()} />;
  if (!query.data || (query.data.ai_connected && query.data.privacy_consent)) return null;
  return <Alert tone="warning" title="AI setup is incomplete">
    You can read and edit local records. To request AI extraction, evaluation, or document preparation,
    configure your API connection and data-sharing consent in <Link to="/settings">Settings</Link>.
  </Alert>;
}

export function SetupPanel() {
  return <Panel title="Local tools & readiness"><DependencyStatus /></Panel>;
}
