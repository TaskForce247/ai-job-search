import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { ApprovalCard } from '../pages/Activity';
import { RunOutcome } from '../components/RunOutcome';
import type { Approval } from '../types';
import { applicationFixture, approvalFixture, runFixture } from './fixtures';
import { fetchFixtures, json, renderApp } from './helpers';

function review(overrides: Partial<Approval> = {}): Approval {
  return {
    ...approvalFixture, kind: 'document_review', choices: ['reject'],
    proposal: {
      application_id: applicationFixture.id, expected_revision: 'old-application-revision',
      issues: [{ document: 'cv', message: 'Substantive experience is missing.',
        suggestion: 'Restore the supported experience and role-specific body from the full draft.' }],
    },
    ...overrides,
  };
}

describe('Activity content review recovery', () => {
  it.each([
    { outcome: 'review_dismissed' },
    { declined: true },
  ])('labels dismissed document operations separately from successful preparation: %j', (result) => {
    renderApp(<RunOutcome run={{ ...runFixture, kind: 'documents', status: 'succeeded', result }} />);
    expect(screen.getByText('Review dismissed · documents unchanged')).not.toHaveClass('badge-positive');
    expect(screen.queryByText('Completed')).not.toBeInTheDocument();
  });

  it('offers AI edits and manual editing instead of only Keep saved draft', async () => {
    const fetch = fetchFixtures({
      '/api/v1/applications/application-1': { ...applicationFixture, revision: 'current-application-revision' },
      '/api/v1/applications/application-1/document-runs': () => json({
        run_id: 'new-edits', application_id: applicationFixture.id,
      }, 202),
    });
    renderApp(<ApprovalCard approval={review()} runKind="documents" />);
    expect(screen.getByRole('button', { name: 'Dismiss review · keep current text' })).toBeVisible();
    expect(screen.getByRole('link', { name: 'Edit draft manually' }))
      .toHaveAttribute('href', '/applications/application-1?tab=checks');
    expect(screen.getByText(/There is no checked correction to accept yet/)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Approve changes' })).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Generate AI edits' }));
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(true));
    const request = fetch.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(request?.[0]).toBe('/api/v1/applications/application-1/document-runs');
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({
      expected_revision: 'current-application-revision', instruction: '', repair: true, resume_from: 'run-1',
    });
    expect(fetch.mock.calls.some(([url]) => String(url).includes('/decision'))).toBe(false);
  });

  it('also offers recovery when an older review has no structured issue list', () => {
    fetchFixtures({});
    renderApp(<ApprovalCard approval={review({ proposal: { application_id: applicationFixture.id } })} />);
    expect(screen.getByRole('button', { name: 'Generate AI edits' })).toBeEnabled();
  });

  it('shows a repair error without accepting or dismissing the pending review', async () => {
    fetchFixtures({
      '/api/v1/applications/application-1': applicationFixture,
      '/api/v1/applications/application-1/document-runs': () => json({
        error: { code: 'operation_in_progress', message: 'Document work is already in progress.' },
      }, 409),
    });
    renderApp(<ApprovalCard approval={review()} />);
    await userEvent.setup().click(screen.getByRole('button', { name: 'Generate AI edits' }));
    expect(await screen.findByText('Document work is already in progress.')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Dismiss review · keep current text' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Generate AI edits' })).toBeEnabled();
  });

  it('does not expose new edit actions for a historical decision', () => {
    fetchFixtures({});
    renderApp(<ApprovalCard approval={review({ status: 'rejected' })} />);
    expect(screen.queryByRole('button', { name: 'Generate AI edits' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Dismiss review · keep current text' })).not.toBeInTheDocument();
  });

  it('dismisses review without claiming that preparation or verification completed', async () => {
    const fetch = fetchFixtures({
      '/api/v1/approvals/approval-1/decision': () => json({
        run_id: 'run-1', application_id: 'application-1', outcome: 'review_dismissed',
      }),
    });
    renderApp(<ApprovalCard approval={review()} runKind="documents" />);
    await userEvent.setup().click(screen.getByRole('button', { name: 'Dismiss review · keep current text' }));
    expect(await screen.findByText(/Review dismissed. Saved documents are unchanged; verification has not advanced/)).toBeVisible();
    expect(screen.queryByRole('link', { name: 'View progress →' })).not.toBeInTheDocument();
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/approvals/approval-1/decision');
  });
});
