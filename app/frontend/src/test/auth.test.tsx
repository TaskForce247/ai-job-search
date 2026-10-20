import { act, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SessionGate } from '../App';
import { ApiError } from '../api';
import { ErrorPanel } from '../components/ui';
import { fetchFixtures, json, renderApp } from './helpers';

describe('authentication and actionable errors', () => {
  it('shows shortcut guidance rather than an account login on a 401', async () => {
    fetchFixtures({ '/api/v1/session': () => json({ error: { code: 'SESSION_REQUIRED', message: 'Unauthorized' } }, 401) });
    renderApp(<SessionGate><p>Private workspace</p></SessionGate>);
    expect(await screen.findByRole('heading', { name: 'Reopen from your app shortcut.' })).toBeVisible();
    expect(screen.queryByText('Private workspace')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('removes the authenticated UI if a later request loses its session', async () => {
    fetchFixtures({ '/api/v1/session': { csrf_token: 'csrf' } });
    renderApp(<SessionGate><p>Private workspace</p></SessionGate>);
    expect(await screen.findByText('Private workspace')).toBeVisible();
    act(() => window.dispatchEvent(new CustomEvent('app:unauthorized')));
    expect(screen.getByRole('heading', { name: 'Reopen from your app shortcut.' })).toBeVisible();
    expect(screen.queryByText('Private workspace')).not.toBeInTheDocument();
  });

  it('makes revision conflicts and denied requests visible and actionable', () => {
    const { rerender } = renderApp(<ErrorPanel error={new ApiError(409, 'REVISION_CONFLICT', 'File changed.')} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Your edits have not been overwritten');
    expect(screen.getByText('Reference: REVISION_CONFLICT')).toBeVisible();
    rerender(<ErrorPanel error={new ApiError(403, 'CSRF_INVALID', 'Permission denied.')} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Reopen the app from its shortcut');
  });

  it('does not misdescribe a cancelled or expired folder grant as lost document edits', () => {
    renderApp(<ErrorPanel error={new ApiError(409, 'directory_grant_expired', 'Choose the directory again using the native folder picker.')} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Choose the directory again');
    expect(screen.queryByText(/Your files changed since/)).not.toBeInTheDocument();
  });
});
