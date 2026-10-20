import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useApiQuery } from '../query';
import type { ProfileView, SettingsView, Workspace } from '../types';
import { AIConnection, DependencyStatus, PreferencesForm, PrivacyConsentControl, WorkspaceControls } from '../components/SetupControls';
import { ProfileImport } from '../components/ProfileImport';
import { Alert, Button, ErrorPanel, Loading, PageHeader, Panel } from '../components/ui';

const steps = ['Your workspace', 'AI & privacy', 'Your profile', 'Search preferences'];

export function WelcomePage() {
  const [params, setParams] = useSearchParams();
  const raw = Number(params.get('step') ?? '1');
  const step = Number.isInteger(raw) && raw >= 1 && raw <= steps.length ? raw : 1;
  const settings = useApiQuery<SettingsView>(['settings'], '/settings');
  const workspace = useApiQuery<Workspace>(['workspace'], '/workspace');
  const profile = useApiQuery<ProfileView>(['profile'], '/profile');
  const navigate = useNavigate();
  const go = (next: number) => setParams({ step: String(next) });
  return <div className="stack welcome-page">
    <PageHeader eyebrow="A more intentional job search" title="Your next chapter starts here."
      description="A private local workspace, clear next steps, and a little assistance when you need it. You stay in control." />
    <ol className="stepper" aria-label="Setup progress">{steps.map((name, index) =>
      <li key={name} aria-current={index + 1 === step ? 'step' : undefined}>
        <button onClick={() => go(index + 1)}><span>{index + 1}</span>{name}</button>
      </li>)}</ol>
    {settings.isPending && <Loading label="Resuming your setup…" />}
    <ErrorPanel error={settings.error} retry={() => void settings.refetch()} />
    {step === 1 && <>
      <Panel title="1. Choose where your information lives"><WorkspaceControls /></Panel>
      <Panel title="Check the tools we’ll use"><DependencyStatus compact /></Panel>
      <ErrorPanel error={workspace.error} retry={() => void workspace.refetch()} />
      <div className="wizard-footer"><p className="hint">Saved workspace and settings persist when you close this tab.</p>
        <Button disabled={!workspace.data?.path} onClick={() => go(2)}>Continue to AI & privacy →</Button></div>
    </>}
    {step === 2 && settings.data && <>
      <Panel title="2. Connect AI only if you choose">
        <AIConnection connected={settings.data.ai_connected} />
        <hr /><PrivacyConsentControl view={settings.data} />
      </Panel>
      <Alert>You can continue without AI and browse or edit local records. Searching supported job boards is separate.
        Extraction, evaluation, and document preparation need a working API connection and your consent.</Alert>
      <div className="wizard-footer"><Button variant="secondary" onClick={() => go(1)}>← Back</Button>
        <Button onClick={() => go(3)}>{settings.data.ai_connected && settings.data.settings.privacy_consent
          ? 'Continue to your profile →' : 'Continue with local-only setup →'}</Button></div>
    </>}
    {step === 3 && <>
      <Panel title="3. Bring your experience">
        <ProfileImport />
        <div className="alternative"><span>or start with a blank page</span></div>
        <h3>Tell your story, one section at a time</h3>
        <p>Enter career history, projects, skills, languages, and what you want next. Leave unknown fields blank.</p>
        <Link to="/profile?setup=1" className="button button-secondary">Build my profile manually</Link>
      </Panel>
      {profile.data?.data.name ? <Alert tone="success">A confirmed profile exists for {profile.data.data.name}.
        You can still add or correct facts at any time.</Alert> :
        <Alert tone="warning">An imported or edited profile is not confirmed until you approve it in <Link to="/activity">Activity</Link>.
          You can finish setup now and come back to the profile later.</Alert>}
      <div className="wizard-footer"><Button variant="secondary" onClick={() => go(2)}>← Back</Button>
        <Button onClick={() => go(4)}>Continue to search preferences →</Button></div>
    </>}
    {step === 4 && settings.data && <>
      <Panel title="4. What would you like to find?">
        <p>Set a starting point. These preferences can be changed for every search. Your profile’s restrictions and languages
          are checked separately during evaluation.</p>
        <PreferencesForm view={settings.data} searchOnly onComplete={() => navigate('/')} />
      </Panel>
      <Panel title="How your search will work">
        <ol className="journey-list"><li><strong>Find & compare.</strong> Search supported sources or paste a posting. Rank selected results for triage.</li>
          <li><strong>Decide & prepare.</strong> Read the full evaluation and explicitly choose whether to prepare documents.</li>
          <li><strong>Review & submit yourself.</strong> Edit and check PDFs, apply on the employer’s site, then record what you actually sent.</li></ol>
      </Panel>
      <div><Button variant="secondary" onClick={() => go(3)}>← Back</Button></div>
    </>}
  </div>;
}
