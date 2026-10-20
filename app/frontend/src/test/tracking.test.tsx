import { screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ApplicationsPage } from '../pages/Applications';
import { WelcomePage } from '../pages/Welcome';
import { fetchFixtures, renderApp } from './helpers';
import { applicationFixture, profileFixture, settingsFixture } from './fixtures';

describe('preparation and imported-record semantics', () => {
  it('keeps preparing, closed and unknown imported statuses visible without claiming a submission', async () => {
    fetchFixtures({ '/api/v1/applications': {
      items: [
        { ...applicationFixture, status: 'preparing', id: 'preparing', role: 'Preparing role', documents: {} },
        { ...applicationFixture, status: 'closed', id: 'closed', role: 'Closed legacy role', documents: {} },
        { ...applicationFixture, status: 'needs_review', id: 'unknown', role: 'Unmapped imported role', documents: {} },
      ], total: 3, next_cursor: null,
    } });
    renderApp(<ApplicationsPage />);
    const column = await screen.findByRole('region', { name: 'Preparing · not yet drafted applications' });
    expect(within(column).getByRole('link', { name: 'Preparing role' })).toBeVisible();
    expect(within(column).getByText('Document preparation in progress · not sent')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Closed legacy role' })).toBeVisible();
    expect(screen.getByRole('link', { name: 'Unmapped imported role' })).toBeVisible();
    expect(screen.queryByText('Drafted · not sent', { selector: '.application-card p' })).not.toBeInTheDocument();
  });

  it('does not trap a new empty workspace before the profile step', async () => {
    fetchFixtures({
      '/api/v1/settings': settingsFixture, '/api/v1/profile': profileFixture,
      '/api/v1/workspace': { path: 'C:\\Private\\New', initialized: false, revision: 'w1', public_repository_warning: false },
      '/api/v1/system/readiness': { dependencies: [], ai_connected: false, privacy_consent: false, active_runs: 0, version: 'test', workspace_path: 'C:\\Private\\New' },
    });
    renderApp(<WelcomePage />, { route: '/welcome' });
    await waitFor(() => expect(screen.getByRole('button', { name: /Continue to AI & privacy/ })).toBeEnabled());
  });
});
