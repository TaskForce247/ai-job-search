import { act, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SessionGate } from '../App';
import { ApiError } from '../api';
import { ErrorPanel } from '../components/ui';
import { fetchFixtures, json, renderApp } from './helpers';

describe('authentication and actionable errors', () => {
  it('shows terminal login-link guidance rather than an account login on a 401', async () => {
    fetchFixtures({ '/api/v1/session': () => json({ error: { code: 'SESSION_REQUIRED', message: 'Unauthorized' } }, 401) });
    renderApp(<SessionGate><p>Private workspace</p></SessionGate>);
    expect(await screen.findByRole('heading', { name: 'Open the login link from your backend terminal.' })).toBeVisible();
    expect(screen.queryByText('Private workspace')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('removes the authenticated UI if a later request loses its session', async () => {
    fetchFixtures({ '/api/v1/session': { csrf_token: 'csrf' } });
    renderApp(<SessionGate><p>Private workspace</p></SessionGate>);
    expect(await screen.findByText('Private workspace')).toBeVisible();
    act(() => window.dispatchEvent(new CustomEvent('app:unauthorized')));
    expect(screen.getByRole('heading', { name: 'Open the login link from your backend terminal.' })).toBeVisible();
    expect(screen.queryByText('Private workspace')).not.toBeInTheDocument();
  });

  it('makes revision conflicts and denied requests visible and actionable', () => {
    const { rerender } = renderApp(<ErrorPanel error={new ApiError(409, 'REVISION_CONFLICT', 'File changed.')} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Your edits have not been overwritten');
    expect(screen.getByText('Reference: REVISION_CONFLICT')).toBeVisible();
    rerender(<ErrorPanel error={new ApiError(403, 'CSRF_INVALID', 'Permission denied.')} />);
    expect(screen.getByRole('alert')).toHaveTextContent('fresh login link from the backend terminal');
  });

  it('does not misdescribe an unavailable workspace as lost document edits', () => {
    renderApp(<ErrorPanel error={new ApiError(409, 'workspace_missing', 'The selected workspace no longer exists.')} />);
    expect(screen.getByRole('alert')).toHaveTextContent('The selected workspace no longer exists');
    expect(screen.queryByText(/Your files changed since/)).not.toBeInTheDocument();
  });
});
