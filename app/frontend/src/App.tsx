import { Component, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { createBrowserRouter, createRoutesFromElements, Link, Navigate, NavLink, Outlet, Route, RouterProvider, useLocation } from 'react-router-dom';
import { ApiError, initializeSession, retrySession } from './api';
import { ActivityProvider, createQueryClient, NoticesProvider, useActivity, useApiQuery } from './query';
import type { SettingsView, Workspace } from './types';
import { Alert, Button, Empty, ErrorPanel, Loading, Panel } from './components/ui';
import { WelcomePage } from './pages/Welcome';
import { DashboardPage } from './pages/Dashboard';
import { ProfilePage } from './pages/Profile';
import { JobDetailPage, JobsPage } from './pages/Jobs';
import { ApplicationDetailPage, ApplicationsPage } from './pages/Applications';
import { ActivityPage } from './pages/Activity';
import { SettingsPage } from './pages/Settings';

const client = createQueryClient();

export class AppErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return <div className="startup-screen"><Panel>
      <h1>This screen could not be displayed.</h1><p>Your saved workspace has not been deleted.
        Reopen the app to restore its connection, or reload this screen.</p>
      <Button onClick={() => window.location.reload()}>Reload the app</Button>
    </Panel></div>;
    return this.props.children;
  }
}

export function SessionGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<'pending' | 'ready' | 'expired' | 'error'>('pending');
  const [error, setError] = useState<unknown>();
  useEffect(() => {
    let active = true;
    const unauthorized = () => { if (active) setState('expired'); };
    window.addEventListener('app:unauthorized', unauthorized);
    void initializeSession().then(() => { if (active) setState('ready'); })
      .catch((cause: unknown) => {
        if (active) { setError(cause); setState(cause instanceof ApiError && cause.status === 401 ? 'expired' : 'error'); }
      });
    return () => { active = false; window.removeEventListener('app:unauthorized', unauthorized); };
  }, []);
  if (state === 'ready') return children;
  return <div className="startup-screen"><div className="startup-brand"><Brand /></div><Panel>
    {state === 'pending' ? <Loading label="Opening your secure local workspace…" /> : state === 'expired' ? <>
      <p className="eyebrow">Local session required</p><h1>Open the login link from your backend terminal.</h1>
      <p>This browser session is not connected. Open the one-use link printed when the backend started.
        If it expired or was already used, restart the backend for a fresh link.</p>
      <Alert>No account or password is needed. The terminal link securely connects this browser to your local backend.
        Restarting stops active work and preserves saved checkpoints; an interrupted AI call may still incur charges.</Alert>
    </> : <>
      <h1>We couldn’t reach the local app.</h1><ErrorPanel error={error} />
      <p>Make sure the backend is running in its terminal. If it stopped, start it again and open its fresh login link.</p>
      <Button onClick={() => {
        setState('pending');
        void retrySession().then(() => setState('ready')).catch((cause: unknown) => {
          setError(cause); setState(cause instanceof ApiError && cause.status === 401 ? 'expired' : 'error');
        });
      }}>Try connecting again</Button>
    </>}
  </Panel></div>;
}

function Brand() {
  return <div className="brand"><span className="brand-mark" aria-hidden="true">
    <svg viewBox="0 0 32 32" fill="none"><path d="M8 24V13c0-5 5-8 16-8 0 10-3 16-9 16H8" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      <path d="m8 24 12-13M8 17h7" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" /></svg>
  </span><span>job search<span className="brand-subtitle">A little more possibility.</span></span></div>;
}

function NavIcon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    home: 'M3 10 12 3l9 7v10H3V10Zm6 10v-7h6v7',
    profile: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-8 9v-2a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v2',
    jobs: 'M8 7V4h8v3M3 7h18v14H3V7Zm0 5 9 4 9-4M10 15h4',
    applications: 'M4 3h13l3 3v15H4V3Zm4 6h8M8 13h8M8 17h5',
    activity: 'M3 12h4l3-8 4 16 3-8h4',
    settings: 'M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M9 15v6',
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.65"
    strokeLinecap="round" strokeLinejoin="round"><path d={paths[name]} /></svg>;
}

function Layout() {
  const location = useLocation();
  const main = useRef<HTMLElement>(null);
  const workspace = useApiQuery<Workspace>(['workspace'], '/workspace');
  const activity = useActivity();
  const pending = activity.allRuns.flatMap((run) => run.approvals).filter((approval) => approval.status === 'pending').length;
  const running = activity.allRuns.filter((run) => ['queued', 'running'].includes(run.status)).length;
  const navigation = [
    ['/', 'Overview', 'home'], ['/profile', 'My profile', 'profile'], ['/jobs', 'Find jobs', 'jobs'],
    ['/applications', 'Applications', 'applications'], ['/activity', 'Activity', 'activity'], ['/settings', 'Settings', 'settings'],
  ];
  useEffect(() => {
    const name = navigation.find(([path]) => path === '/' ? location.pathname === '/' : location.pathname.startsWith(path))?.[1] ?? 'Welcome';
    document.title = `${name} · AI Job Search`;
    main.current?.focus({ preventScroll: true });
    if (!location.hash) window.scrollTo?.({ top: 0, behavior: 'instant' });
  // A route transition should move focus, not a polling update.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.pathname]);
  return <div className="app-shell">
    <a href="#main-content" className="skip-link">Skip to main content</a>
    <aside className="sidebar">
      <Link className="brand-link" to="/"><Brand /></Link>
      <p className="nav-eyebrow">YOUR NEXT CHAPTER</p>
      <nav aria-label="Main navigation"><ul className="nav-list">{navigation.map(([to, label, icon]) =>
        <li key={to}><NavLink to={to} end={to === '/'}><NavIcon name={icon} /><span>{label}</span>
          {to === '/activity' && pending > 0 && <span className="nav-count" aria-label={`${pending} pending decisions`}>{pending}</span>}
        </NavLink></li>)}</ul></nav>
      <div className="sidebar-bottom"><div className="local-indicator"><span aria-hidden="true" />Local workspace</div>
        <p>Your information. Your decisions.<br />Cloud AI only with your consent.</p><Link to="/welcome">Guided setup →</Link></div>
    </aside>
    <div className="main-column">
      <header className="topbar"><span className="workspace-heading">Personal workspace</span>
        <span className="workspace-path" title={workspace.data?.path}>{workspace.data?.path || 'On this computer'}</span>
        <Link to="/activity" className="activity-indicator"><span className={running ? 'pulse-dot' : 'idle-dot'} aria-hidden="true" />
          {running ? `${running} in progress` : pending ? `${pending} decisions needed` : 'Activity'}</Link></header>
      <main id="main-content" ref={main} tabIndex={-1} className="main-content">
        <ErrorPanel error={workspace.error} retry={() => void workspace.refetch()} />
        {activity.error && location.pathname !== '/activity' && <Alert tone="warning" title="Activity updates are unavailable">
          <ErrorPanel error={activity.error} retry={() => void activity.refetch()} />
          <Link to="/activity">Review activity →</Link>
        </Alert>}
        {workspace.data?.public_repository_warning && location.pathname !== '/settings' && <Alert tone="warning" title="Workspace privacy reminder">
          This folder may track personal files in Git. Review its privacy before publishing anything.
          The app never commits or pushes. <Link to="/settings">Workspace settings →</Link>
        </Alert>}
        <Outlet />
      </main>
      <footer className="app-footer"><span>Built around your next move.</span><span>Drafts stay drafts until you record a real submission.</span></footer>
    </div>
  </div>;
}

function Home() {
  const settings = useApiQuery<SettingsView>(['settings'], '/settings');
  if (settings.isPending) return <Loading label="Opening your workspace…" />;
  if (settings.error) return <ErrorPanel error={settings.error} retry={() => void settings.refetch()} />;
  if (settings.data && !settings.data.settings.onboarding_complete) return <Navigate to="/welcome" replace />;
  return <DashboardPage />;
}

export function App() {
  const [router] = useState(() => createBrowserRouter(createRoutesFromElements(
    <Route element={<NoticesProvider><ActivityProvider><Outlet /></ActivityProvider></NoticesProvider>}>
      <Route element={<Layout />}>
        <Route index element={<Home />} /><Route path="/welcome" element={<WelcomePage />} />
        <Route path="/profile" element={<ProfilePage />} /><Route path="/jobs" element={<JobsPage />} />
        <Route path="/jobs/:id" element={<JobDetailPage />} /><Route path="/applications" element={<ApplicationsPage />} />
        <Route path="/applications/:id" element={<ApplicationDetailPage />} /><Route path="/activity" element={<ActivityPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Panel><Empty title="This page isn’t in your workspace."
          action={<Link to="/" className="button button-primary">Go to overview</Link>}>Use the navigation to continue your search.</Empty></Panel>} />
      </Route>
    </Route>,
  )));
  return <AppErrorBoundary><SessionGate><QueryClientProvider client={client}>
    <RouterProvider router={router} />
  </QueryClientProvider></SessionGate></AppErrorBoundary>;
}
