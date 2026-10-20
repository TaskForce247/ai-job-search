import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { DependencyStatus, PortalControls, WorkspaceControls } from '../components/SetupControls';
import { fetchFixtures, renderApp } from './helpers';

describe('guided local setup and source permissions', () => {
  it('asks for a full workspace path and requires approval before switching', async () => {
    const user = userEvent.setup();
    const fetch = fetchFixtures({
      '/api/v1/workspace': (init: RequestInit | undefined) => {
        if (init?.method === 'POST') {
          expect(JSON.parse(String(init.body))).toEqual({ path: 'C:\\Private\\Careers', create: true });
        }
        return new Response(JSON.stringify({ path: 'C:\\Private\\Careers', initialized: true, revision: 'w1', public_repository_warning: false }));
      },
    });
    renderApp(<WorkspaceControls />);
    await screen.findByText('Workspace ready');
    expect(screen.getByRole('button', { name: 'Choose a new workspace folder' })).toBeDisabled();
    await user.type(screen.getByLabelText('Workspace folder path'), 'C:\\Private\\Careers');
    await user.click(screen.getByRole('button', { name: 'Choose a new workspace folder' }));
    await user.click(await screen.findByRole('button', { name: 'Create here' }));
    const dialog = screen.getByRole('dialog', { name: 'Create this workspace?' });
    expect(within(dialog).getByRole('button', { name: 'Confirm workspace' })).toBeDisabled();
    expect(fetch.mock.calls.filter((call) => call[0] === '/api/v1/workspace' && call[1]?.method === 'POST')).toHaveLength(0);
    await user.click(within(dialog).getByRole('checkbox'));
    await user.click(within(dialog).getByRole('button', { name: 'Confirm workspace' }));
    await waitFor(() => expect(fetch.mock.calls.filter((call) => call[0] === '/api/v1/workspace' && call[1]?.method === 'POST')).toHaveLength(1));
  });

  it('requires acknowledgement of LinkedIn automated-access risks before enabling it', async () => {
    const user = userEvent.setup();
    const fetch = fetchFixtures({
      '/api/v1/portals': { items: [{
        id: 'linkedin', label: 'LinkedIn', enabled: false, installed_enabled: true, effective_enabled: false,
        supported: true, warning: 'Automated access may conflict with site terms.', health: 'available', revision: 'portal-r1',
      }] },
      '/api/v1/portals/linkedin/settings': {},
    });

    renderApp(<PortalControls />);
    await user.click(await screen.findByRole('button', { name: 'Enable LinkedIn' }));
    const dialog = screen.getByRole('dialog', { name: 'Enable LinkedIn?' });
    const enable = within(dialog).getByRole('button', { name: 'Enable source' });
    expect(enable).toBeDisabled();
    await user.click(within(dialog).getByRole('checkbox'));
    await user.click(enable);
    await waitFor(() => expect(fetch.mock.calls.some((call) => call[0] === '/api/v1/portals/linkedin/settings')).toBe(true));
    const change = fetch.mock.calls.find((call) => call[0] === '/api/v1/portals/linkedin/settings');
    expect(JSON.parse(String(change?.[1]?.body))).toEqual({ enabled: true, acknowledged: true, expected_revision: 'portal-r1' });
  });

  it('provides a confirmed guided dependency repair rather than a terminal command', async () => {
    const user = userEvent.setup();
    const fetch = fetchFixtures({
      '/api/v1/system/readiness': {
        dependencies: [{ id: 'tex', label: 'PDF tools', status: 'setup_required', description: 'Document compilation needs local PDF tools.', can_install: true }],
        ai_connected: false, privacy_consent: false, workspace_path: '', active_runs: 0, version: 'test',
      },
      '/api/v1/system/dependencies/tex/repair': { run_id: 'repair-run' },
    });
    renderApp(<DependencyStatus />);
    await user.click(await screen.findByRole('button', { name: 'Set up PDF tools' }));
    const dialog = screen.getByRole('dialog', { name: 'Set up PDF tools?' });
    expect(within(dialog).getByRole('button', { name: 'Start guided setup' })).toBeDisabled();
    await user.click(within(dialog).getByRole('checkbox'));
    await user.click(within(dialog).getByRole('button', { name: 'Start guided setup' }));
    await waitFor(() => expect(fetch.mock.calls.some((call) => call[0] === '/api/v1/system/dependencies/tex/repair')).toBe(true));
    expect(screen.queryByText('npm install')).not.toBeInTheDocument();
  });

  it('can repair missing template packages even when compiler executables are already detected', async () => {
    const user = userEvent.setup();
    const fetch = fetchFixtures({
      '/api/v1/system/readiness': {
        dependencies: [{
          id: 'tex', label: 'Document tools', status: 'ready', description: 'Compiler executables detected.',
          repair_url: 'https://miktex.org/download', can_install: true,
        }],
        ai_connected: false, privacy_consent: false, workspace_path: '', active_runs: 0, version: 'test',
      },
      '/api/v1/system/dependencies/tex/repair': { run_id: 'repair-run' },
    });
    renderApp(<DependencyStatus />);
    await user.click(await screen.findByRole('button', { name: 'Repair Document tools' }));
    const dialog = screen.getByRole('dialog', { name: 'Set up Document tools?' });
    expect(within(dialog).getByText(/download large files/)).toBeVisible();
    expect(within(dialog).getByRole('link', { name: /license information/ })).toHaveAttribute('href', 'https://miktex.org/download');
    expect(within(dialog).getByRole('button', { name: 'Start guided setup' })).toBeDisabled();
    expect(fetch.mock.calls.some(([url]) => url === '/api/v1/system/dependencies/tex/repair')).toBe(false);
    await user.click(within(dialog).getByRole('checkbox', { name: /accept the provider's license/ }));
    await user.click(within(dialog).getByRole('button', { name: 'Start guided setup' }));
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === '/api/v1/system/dependencies/tex/repair')).toBe(true));
  });

  it('leaves the workspace unchanged when a typed folder selection is cancelled', async () => {
    const user = userEvent.setup();
    const fetch = fetchFixtures({
      '/api/v1/workspace': { path: 'C:\\Private\\Current', initialized: false, revision: 'w1', public_repository_warning: false },
    });
    renderApp(<WorkspaceControls />);
    await user.type(await screen.findByLabelText('Workspace folder path'), 'C:\\Private\\Other');
    await user.click(await screen.findByRole('button', { name: 'Choose an existing workspace' }));
    await user.click(screen.getByRole('button', { name: 'Cancel selection' }));
    expect(await screen.findByText('Folder selection cancelled. Your workspace is unchanged.')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Use this folder' })).not.toBeInTheDocument();
    expect(fetch.mock.calls.some((call) => call[0] === '/api/v1/workspace' && call[1]?.method === 'POST')).toBe(false);
  });

  it('inspects only the current workspace without a desktop folder picker', async () => {
    const user = userEvent.setup();
    const fetch = fetchFixtures({
      '/api/v1/workspace': { path: 'C:\\Private\\Current', initialized: true, revision: 'w1', public_repository_warning: false },
      '/api/v1/workspace/imports': { run_id: 'migration-run' },
    });
    renderApp(<WorkspaceControls />);
    await user.click(await screen.findByText('Import existing records from this workspace'));
    await user.click(screen.getByRole('button', { name: 'Inspect existing files' }));
    await waitFor(() => expect(fetch.mock.calls.some((call) => call[0] === '/api/v1/workspace/imports')).toBe(true));
    const request = fetch.mock.calls.find((call) => call[0] === '/api/v1/workspace/imports');
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({});
  });
});
