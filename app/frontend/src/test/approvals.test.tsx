import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { ActivityPage, ApprovalCard } from '../pages/Activity';
import { fetchFixtures, renderApp } from './helpers';
import { approvalFixture, documentFixture, runFixture, workspaceFixture } from './fixtures';

describe('explicit approvals and durable progress', () => {
  it('shows a useful before/after and submits the specific approval revision', async () => {
    const user = userEvent.setup();
    const fetch = fetchFixtures({ '/api/v1/approvals/approval-1/decision': { run_id: 'run-1' } });
    renderApp(<ApprovalCard approval={approvalFixture} />);
    expect(screen.getByText('Current name')).toBeVisible();
    expect(screen.getByText('Proposed name')).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Confirm profile changes' }));
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({
      decision: 'approve', expected_revision: 'approval-r1', confirmation: '',
    });
  });

  it('requires the exact destructive confirmation but allows rejecting without it', async () => {
    const user = userEvent.setup();
    const fetch = fetchFixtures({ '/api/v1/approvals/approval-1/decision': { run_id: 'run-1' } });
    renderApp(<ApprovalCard approval={{ ...approvalFixture, kind: 'restore', title: 'Restore workspace?', proposal: { files: ['profile.md'], action: 'replace' } }} />);
    const approve = screen.getByRole('button', { name: 'Approve restore' });
    expect(approve).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reject proposal' })).toBeEnabled();
    await user.type(screen.getByLabelText('Type RESTORE to approve'), 'restore');
    expect(approve).toBeDisabled();
    await user.clear(screen.getByLabelText('Type RESTORE to approve'));
    await user.type(screen.getByLabelText('Type RESTORE to approve'), 'RESTORE');
    await user.click(approve);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body)).confirmation).toBe('RESTORE');
  });

  it('never offers preparation through a failed required gate', () => {
    renderApp(<ApprovalCard approval={{
      ...approvalFixture, kind: 'evaluation', choices: ['prepare', 'not_now'],
      proposal: { evaluation: { language: { verdict: 'FAIL', note: 'Required language is not in the profile.' }, strengths: ['Relevant skills'], gaps: ['Language'], scores: { technical: 80 } } },
    }} />);
    expect(screen.queryByRole('button', { name: 'Prepare application documents' })).not.toBeInTheDocument();
    expect(screen.getByText('Preparation is blocked')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Not now' })).toBeEnabled();
  });

  it('navigates to a distinct application when an evaluation is approved for preparation', async () => {
    const user = userEvent.setup();
    fetchFixtures({ '/api/v1/approvals/approval-1/decision': { run_id: 'draft-run', application_id: 'application-unique' } });
    renderApp(<Routes><Route path="/" element={<ApprovalCard approval={{
      ...approvalFixture, kind: 'evaluation', choices: ['prepare', 'not_now'], proposal: { strengths: ['Confirmed match'] },
    }} />} /><Route path="/applications/:id" element={<h1>Application workspace opened</h1>} /></Routes>);
    await user.click(screen.getByRole('button', { name: 'Prepare application documents' }));
    expect(await screen.findByRole('heading', { name: 'Application workspace opened' })).toBeVisible();
    expect(screen.getByText(/It is not yet drafted or sent/)).toBeVisible();
  });

  it('respects the server blocked flag even when no detailed failing gate is present', () => {
    renderApp(<ApprovalCard approval={{ ...approvalFixture, kind: 'evaluation', choices: ['prepare', 'not_now'],
      proposal: { blocked: true, score: 82, verdict: 'Blocked', evaluation: { strengths: ['Skills match'] } },
    }} />);
    expect(screen.queryByRole('button', { name: 'Prepare application documents' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Not now' })).toBeVisible();
  });

  it('makes inferred posting metadata reviewable and renders its original text safely', () => {
    const fetch = fetchFixtures({});
    renderApp(<ApprovalCard approval={{ ...approvalFixture, kind: 'posting_import', title: 'Confirm posting details',
      proposal: { record: {
        title: 'Inferred role', company: 'Inferred employer', location: 'Remote', description: '<img src=x onerror=bad()>Original requirements.',
        url: 'https://example.test/posting', warnings: ['Employer inferred from the posting.'],
      }, expected: { posting: 'source-hash' } },
    }} />);
    expect(screen.getByRole('heading', { name: 'Inferred role' })).toBeVisible();
    expect(screen.getByText('Inferred employer', { selector: 'p' })).toBeVisible();
    expect(screen.getByText('<img src=x onerror=bad()>Original requirements.', { selector: '.posting-text' })).toBeVisible();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Approve changes' })).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('renders CV and cover-letter rewrite comparisons and never accepts an AI rewrite automatically', async () => {
    const user = userEvent.setup();
    const before = { cv: structuredClone(documentFixture.content), cover_letter: structuredClone(documentFixture.content) };
    const after = structuredClone(before);
    after.cv.blocks[0].text = 'Proposed CV text.';
    after.cover_letter.blocks[0].text = 'Proposed cover letter text.';
    const fetch = fetchFixtures({ '/api/v1/approvals/approval-1/decision': { run_id: 'rewrite-run' } });
    renderApp(<ApprovalCard approval={{ ...approvalFixture, kind: 'document_rewrite', title: 'Review rewrite',
      proposal: { before, after },
    }} />);
    expect(screen.getByText('Original paragraph.')).toBeVisible();
    expect(screen.getByText('Proposed CV text.')).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Compare cover letter' }));
    expect(screen.getByText('Proposed cover letter text.')).toBeVisible();
    expect(screen.queryByText('Proposed CV text.')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Approve changes' }));
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toEqual({
      decision: 'approve', expected_revision: 'approval-r1', confirmation: '',
    });
  });

  it('announces new pending decisions after polling invalidation and does not label failed runs completed', async () => {
    let latest = { ...runFixture, status: 'running' as const, approvals: [] as typeof runFixture.approvals };
    fetchFixtures({
      '/api/v1/workspace': workspaceFixture,
      '/api/v1/runs': () => new Response(JSON.stringify({ items: [latest], total: 1, next_cursor: null }), { headers: { 'Content-Type': 'application/json' } }),
    });
    const { client } = renderApp(<ActivityPage />, { activity: true });
    expect(await screen.findByText('In progress')).toBeVisible();
    latest = { ...latest, approvals: [approvalFixture] };
    await act(async () => { await client.refetchQueries({ queryKey: ['runs'] }); });
    expect(await screen.findByText(/1 decisions need your attention/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm profile changes' })).toBeVisible();
    const failed = { ...runFixture, status: 'failed', error: 'The compiler is unavailable.', approvals: [] };
    client.setQueryData(['runs'], { items: [failed], total: 1, next_cursor: null });
    await waitFor(() => expect(screen.getByText('The compiler is unavailable.')).toBeVisible());
    expect(screen.getByText('Failed')).toBeVisible();
    expect(screen.queryByText('Completed')).not.toBeInTheDocument();
  });

  it('keeps pending decisions globally visible after they leave the most recent activity page', async () => {
    fetchFixtures({ '/api/v1/workspace': workspaceFixture, '/api/v1/runs': (_init: RequestInit | undefined, path: string) => {
      const isOlder = new URL(path, 'http://localhost').searchParams.has('cursor');
      return new Response(JSON.stringify({
        items: isOlder ? [runFixture] : [{ ...runFixture, id: 'recent-completed', status: 'succeeded', approvals: [] }],
        total: 2, next_cursor: isOlder ? null : 'older-page',
      }), { headers: { 'Content-Type': 'application/json' } });
    } });
    renderApp(<ActivityPage />, { activity: true });
    expect(await screen.findByRole('button', { name: 'Confirm profile changes' })).toBeVisible();
    expect(screen.getByText(/1 decisions need your attention/)).toBeInTheDocument();
  });
});
