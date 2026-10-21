import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { DocumentEditor } from '../components/DocumentEditor';
import { ApplicationDetailPage } from '../pages/Applications';
import type { Application, Approval, DocumentKind, Run } from '../types';
import { applicationFixture, approvalFixture, documentFixture, profileFixture, runFixture, workspaceFixture } from './fixtures';
import { fetchFixtures, json, renderApp } from './helpers';

const savedWording = 'I owned the entire reporting platform.';
const correctedWording = 'Built a reporting tool.';

function setup(options: { manual?: boolean; stopped?: boolean; preparing?: boolean } = {}) {
  vi.stubGlobal('EventSource', undefined);
  const cv = structuredClone(documentFixture);
  cv.content.blocks[0].text = savedWording;
  cv.pdf_artifact_id = null;
  cv.checks = [];
  const letter = structuredClone(cv);
  letter.content.blocks[0].text = 'A generated cover letter is available before PDF compilation.';
  const before = { cv: structuredClone(cv.content), cover_letter: structuredClone(letter.content) };
  const after = structuredClone(before);
  after.cv.blocks[0].text = correctedWording;
  const issue = {
    kind: 'grounding' as const, document: 'cv' as const, block_id: 'block-1', claim_id: '',
    quote: savedWording, message: 'Ownership exceeds the confirmed source.', evidence_ids: ['fact-1'],
    suggestion: correctedWording, needs_confirmation: !!options.manual,
  };
  const approval: Approval = {
    ...approvalFixture, run_id: 'review-run',
    kind: options.manual ? 'document_review' : 'document_correction',
    title: options.manual ? 'Your draft needs content review' : 'Accept checked wording corrections?',
    choices: options.manual ? ['reject'] : ['approve', 'reject'],
    proposal: { application_id: applicationFixture.id, before, after, issues: [issue], evidence: profileFixture.evidence,
      expected_revision: applicationFixture.revision, checked: options.manual ? null : { grounding: { supported: true } } },
  };
  const run: Run = {
    ...runFixture, id: 'review-run', kind: 'documents', status: options.preparing ? 'running' : options.stopped ? 'failed' : 'awaiting_user',
    stage: options.preparing ? 'draft' : options.stopped ? 'grounding_2' : approval.kind, input: { application_id: applicationFixture.id },
    result: options.preparing ? null : options.stopped ? { findings: [issue.message] } : approval.proposal,
    error: options.stopped ? 'A generated statement needs correction.' : null,
    error_code: options.stopped ? 'grounding_failed' : null, approvals: options.stopped || options.preparing ? [] : [approval],
  };
  const state: { application: Application; runs: Record<string, Run> } = {
    application: {
      ...applicationFixture, status: 'preparing', published: false, documents: options.preparing ? {} : { cv, cover_letter: letter },
      latest_run_id: run.id, last_run_id: 'original-writer', content_issues: options.preparing ? [] : [issue],
      preparation_cost_usd: 0.06, preparation_attempts: 2, next_run_limit_usd: 3,
    },
    runs: { [run.id]: run },
  };
  function document(kind: DocumentKind, init?: RequestInit) {
    const current = state.application.documents[kind]!;
    if (init?.method === 'PATCH') {
      const request = JSON.parse(String(init.body));
      state.application.documents[kind] = { ...current, content: request.content, revision: 'document-r2', stale: true };
      state.application.revision = 'application-r2';
      return json({});
    }
    return json(current);
  }
  function continueChecks(accepted = false) {
    state.runs[run.id] = {
      ...run, status: 'succeeded', approvals: [{ ...approval, status: accepted ? 'approved' : 'rejected' }], result: { application_id: applicationFixture.id },
    };
    state.runs['continued-run'] = { ...run, id: 'continued-run', status: 'queued', stage: 'queued', error: null, error_code: null, approvals: [] };
    state.application.latest_run_id = 'continued-run';
    state.application.content_issues = [];
    return json({ run_id: 'continued-run', application_id: applicationFixture.id }, 202);
  }
  const fetch = fetchFixtures({
    '/api/v1/workspace': workspaceFixture,
    '/api/v1/runs': () => json({ items: Object.values(state.runs), total: Object.keys(state.runs).length, next_cursor: null }),
    '/api/v1/applications/application-1': () => json(state.application),
    '/api/v1/applications/application-1/documents/cv': (init?: RequestInit) => document('cv', init),
    '/api/v1/applications/application-1/documents/cover_letter': (init?: RequestInit) => document('cover_letter', init),
    '/api/v1/profile': profileFixture,
    '/api/v1/system/readiness': { ai_connected: true, privacy_consent: true },
    '/api/v1/runs/review-run': () => json(state.runs['review-run']),
    '/api/v1/runs/continued-run': () => json(state.runs['continued-run']),
    '/api/v1/runs/pdf-run': () => json(state.runs['pdf-run']),
    ...Object.fromEntries((['cv', 'cover_letter'] as const).map((kind) => [
      `/api/v1/applications/application-1/documents/${kind}/pdf-runs`,
      () => {
        state.runs['pdf-run'] = {
          ...runFixture, id: 'pdf-run', kind: 'document_pdf', status: 'queued', approvals: [],
          input: { application_id: applicationFixture.id, kind }, result: null, error: null, error_code: null,
        };
        return json({ run_id: 'pdf-run', application_id: applicationFixture.id }, 202);
      },
    ])),
    '/api/v1/applications/application-1/document-runs': () => continueChecks(),
    '/api/v1/approvals/approval-1/decision': (init?: RequestInit) => {
      const decision = JSON.parse(String(init?.body));
      if (decision.decision === 'approve') {
        state.application.documents.cv = { ...cv, content: structuredClone(after.cv), revision: 'document-r2' };
        state.application.revision = 'application-r2';
        state.application.review_checkpoint_available = true;
        continueChecks(true);
        return json({ run_id: run.id, application_id: applicationFixture.id, preparation_run_id: 'continued-run' });
      }
      return json({ run_id: run.id, application_id: applicationFixture.id });
    },
  });
  const result = renderApp(<Routes><Route path="/applications/:id" element={<ApplicationDetailPage />} /></Routes>, {
    route: '/applications/application-1', activity: true,
  });
  return { ...result, state, fetch, drafts: { cv, cover_letter: letter } };
}

describe('document-first application workspace', () => {
  it('reveals the first saved draft while independent review is still running', async () => {
    const { client, state, fetch, drafts } = setup({ preparing: true });
    await screen.findByText('Your documents are being prepared.');
    expect(screen.queryByRole('region', { name: 'Readable saved draft' })).not.toBeInTheDocument();
    state.application.documents = drafts;
    state.runs['review-run'] = { ...state.runs['review-run'], stage: 'independent_review_1' };
    await act(async () => { await client.refetchQueries({ queryKey: ['runs'] }); });
    const readable = await screen.findByRole('region', { name: 'Readable saved draft' });
    expect(within(readable).getByText(savedWording)).toBeVisible();
    expect(within(readable).getByText('Unverified draft')).toBeVisible();
    expect(state.runs['review-run'].status).toBe('running');
    expect(screen.getByRole('button', { name: 'Resume checks' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Generate PDF' })).toBeDisabled();
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
    expect(fetch.mock.calls.some(([url]) => String(url).includes('/artifacts/'))).toBe(false);
  });

  it('shows readable drafts before operational details and without compiling or calling AI', async () => {
    const { fetch } = setup({ stopped: true });
    const draft = await screen.findByRole('region', { name: 'Readable saved draft' });
    expect(within(draft).getByText(savedWording)).toBeVisible();
    expect(within(draft).getByText('Unverified draft')).toBeVisible();
    const documents = screen.getByRole('heading', { name: 'Application documents' });
    const operation = await screen.findByRole('heading', { name: 'Latest operation' });
    expect(documents.compareDocumentPosition(operation) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Cover letter' }));
    await waitFor(() => expect(within(screen.getByRole('region', { name: 'Readable saved draft' }))
      .getByText('A generated cover letter is available before PDF compilation.')).toBeVisible());
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
    expect(fetch.mock.calls.some(([url]) => String(url).includes('/artifacts/'))).toBe(false);
  });

  it('shows supporting sources and applies corrections only after explicit acceptance', async () => {
    const { fetch, state } = setup();
    const user = userEvent.setup();
    await screen.findByRole('region', { name: 'Readable saved draft' });
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
    await user.click(screen.getByRole('button', { name: 'Checks (1)' }));
    const issues = screen.getByRole('region', { name: 'Document issues' });
    expect(within(issues).getByText(savedWording)).toBeVisible();
    expect(within(issues).getByRole('heading', { name: 'Supporting source' })).toBeVisible();
    expect(within(issues).getByRole('heading', { name: 'Suggested correction' })).toBeVisible();
    expect(within(issues).getAllByText(correctedWording).length).toBe(2);
    expect(screen.getByText(/Recorded document API cost/)).toHaveTextContent('$0.06');
    expect(screen.getByText(/limit for the next operation/)).toHaveTextContent('$3.00');
    await user.click(screen.getByRole('button', { name: 'Accept corrections & continue' }));
    await waitFor(() => expect(state.application.documents.cv!.content.blocks[0].text).toBe(correctedWording));
    const decision = fetch.mock.calls.find(([url, init]) => String(url).includes('/approvals/') && init?.method === 'POST');
    expect(JSON.parse(String(decision?.[1]?.body))).toEqual({ decision: 'approve', expected_revision: 'approval-r1', confirmation: '' });
    expect(state.application.published).toBe(false);
    expect(state.application.applied_on).toBeNull();
    expect(screen.queryByText('Verified draft', { exact: true })).not.toBeInTheDocument();
  });

  it('preserves unsaved edits across Checks and resumes the manually saved revision', async () => {
    const { fetch, state } = setup();
    const user = userEvent.setup();
    await screen.findByRole('region', { name: 'Readable saved draft' });
    await user.click(screen.getByRole('button', { name: 'Checks (1)' }));
    await user.click(screen.getByRole('button', { name: 'Edit this sentence' }));
    const body = screen.getAllByLabelText('Body text')[0];
    expect(body).toHaveFocus();
    await user.clear(body);
    await user.type(body, 'My manually corrected reporting experience.');
    await user.click(screen.getByRole('button', { name: 'Checks (1)' }));
    expect(screen.getByRole('button', { name: 'Accept corrections & continue' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Resume checks' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Generate PDF' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Generate AI edits' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Edit this sentence' }));
    expect(body).toHaveFocus();
    expect(body).toHaveValue('My manually corrected reporting experience.');
    await user.click(screen.getByRole('button', { name: 'Checks (1)' }));
    await user.click(screen.getByRole('button', { name: 'CV' }));
    expect(screen.getAllByLabelText('Body text')[0]).toHaveValue('My manually corrected reporting experience.');
    await user.click(screen.getByRole('button', { name: 'Save document edits' }));
    await waitFor(() => expect(state.application.revision).toBe('application-r2'));
    await user.click(screen.getByRole('button', { name: 'Checks (1)' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resume checks' })).toBeEnabled());
    expect(screen.getByRole('button', { name: 'Accept corrections & continue' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Resume checks' }));
    await waitFor(() => expect(fetch.mock.calls.some(([url, init]) =>
      String(url).endsWith('/document-runs') && init?.method === 'POST')).toBe(true));
    const request = fetch.mock.calls.find(([url, init]) => String(url).endsWith('/document-runs') && init?.method === 'POST');
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({
      expected_revision: 'application-r2', instruction: '', resume_from: 'review-run', repair: false,
    });
    expect(state.application.documents.cv!.content.blocks[0].text).toBe('My manually corrected reporting experience.');
    expect(fetch.mock.calls.some(([url, init]) => String(url).includes('/approvals/') && init?.method === 'POST')).toBe(false);
  });

  it('asks for genuine fact clarification without offering unchecked acceptance', async () => {
    setup({ manual: true });
    await screen.findByRole('region', { name: 'Readable saved draft' });
    await userEvent.setup().click(screen.getByRole('button', { name: 'Checks (1)' }));
    expect(screen.getByText(/A genuine fact needs your confirmation/)).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Accept corrections & continue' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Edit this sentence' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Resume checks' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Generate AI edits' })).toBeEnabled();
  });

  it('generates a local draft PDF during pending review and exposes a clearly unverified download', async () => {
    const { fetch, client, state } = setup({ manual: true });
    const user = userEvent.setup();
    await screen.findByRole('region', { name: 'Readable saved draft' });
    await user.click(screen.getByRole('button', { name: 'Cover letter' }));
    await user.click(screen.getByRole('button', { name: 'Generate PDF' }));
    await screen.findByRole('region', { name: 'Draft PDF generation' });
    const request = fetch.mock.calls.find(([url, init]) =>
      String(url).endsWith('/documents/cover_letter/pdf-runs') && init?.method === 'POST');
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({ expected_revision: applicationFixture.revision });
    expect(screen.getByRole('button', { name: 'Generate PDF' })).toBeDisabled();
    state.runs['pdf-run'] = {
      ...state.runs['pdf-run'], status: 'succeeded',
      message: 'Draft PDF compiled locally.', result: { verified: false },
    };
    state.application.documents.cover_letter = {
      ...state.application.documents.cover_letter!, draft_pdf_artifact_id: 'draft-pdf', draft_pdf_stale: false,
    };
    await act(async () => { await client.refetchQueries({ queryKey: ['runs'] }); });
    const download = await screen.findByRole('link', { name: 'Download draft PDF (unverified)' });
    expect(download).toHaveAttribute('href', '/api/v1/artifacts/draft-pdf');
    expect(screen.queryByRole('link', { name: 'Download cover letter PDF' })).not.toBeInTheDocument();
    expect(state.runs['review-run'].status).toBe('awaiting_user');
    expect(state.application.published).toBe(false);
    expect(fetch.mock.calls.some(([url, init]) =>
      (String(url).endsWith('/document-runs') || String(url).includes('/approvals/')) && init?.method === 'POST')).toBe(false);
    state.application.documents.cover_letter.draft_pdf_stale = true;
    await act(async () => {
      await client.refetchQueries({ queryKey: ['application'] });
      await client.refetchQueries({ queryKey: ['document'] });
    });
    await waitFor(() => expect(screen.queryByRole('link', { name: 'Download draft PDF (unverified)' })).not.toBeInTheDocument());
  });

  it('reports PDF compilation failure without offering a success-shaped download', async () => {
    const { client, state } = setup({ manual: true });
    await screen.findByRole('region', { name: 'Readable saved draft' });
    await userEvent.setup().click(screen.getByRole('button', { name: 'Generate PDF' }));
    await screen.findByRole('region', { name: 'Draft PDF generation' });
    state.runs['pdf-run'] = {
      ...state.runs['pdf-run'], status: 'failed', error: 'The document compiler could not finish.', error_code: 'tool_failed',
    };
    await act(async () => {
      await client.refetchQueries({ queryKey: ['runs'] });
      await client.refetchQueries({ queryKey: ['run', 'pdf-run'] });
    });
    expect(await screen.findByText('The document compiler could not finish.')).toBeVisible();
    expect(screen.queryByRole('link', { name: 'Download draft PDF (unverified)' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Generate PDF' })).toBeEnabled();
  });

  it('requests AI edits directly from a failed check without auto-accepting a correction', async () => {
    const { fetch, state } = setup({ manual: true });
    const user = userEvent.setup();
    await screen.findByRole('region', { name: 'Readable saved draft' });
    await user.click(screen.getByRole('button', { name: 'Checks (1)' }));
    await user.click(screen.getByRole('button', { name: 'Generate AI edits' }));
    await waitFor(() => expect(fetch.mock.calls.some(([url, init]) =>
      String(url).endsWith('/document-runs') && init?.method === 'POST')).toBe(true));
    const request = fetch.mock.calls.find(([url, init]) => String(url).endsWith('/document-runs') && init?.method === 'POST');
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({
      expected_revision: applicationFixture.revision, instruction: '', repair: true,
    });
    expect(state.application.documents.cv!.content.blocks[0].text).toBe(savedWording);
    expect(fetch.mock.calls.some(([url, init]) => String(url).includes('/approvals/') && init?.method === 'POST')).toBe(false);
  });

  it('allows a targeted rewrite to resolve a pending content review', async () => {
    const { fetch } = setup({ manual: true });
    const user = userEvent.setup();
    await screen.findByRole('region', { name: 'Readable saved draft' });
    await user.click(screen.getByText('Request a targeted rewrite'));
    await user.type(screen.getByLabelText('What should change?'), 'Restore the full supported application.');
    await user.click(screen.getByRole('button', { name: 'Request targeted rewrite' }));
    await waitFor(() => expect(fetch.mock.calls.some(([url, init]) =>
      String(url).endsWith('/document-runs') && init?.method === 'POST')).toBe(true));
    const request = fetch.mock.calls.find(([url, init]) => String(url).endsWith('/document-runs') && init?.method === 'POST');
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({
      expected_revision: applicationFixture.revision, instruction: 'Restore the full supported application.', repair: false,
    });
  });

  it('never labels a draft verified when a required check is skipped', () => {
    fetchFixtures({});
    renderApp(<DocumentEditor applicationId="application-1" kind="cv"
      view={{ ...documentFixture, stale: false, pdf_artifact_id: null }} evidence={[]} onDirtyChange={() => undefined} initialPane="read" />);
    expect(screen.getByText('Unverified draft')).toBeVisible();
    expect(screen.queryByText('Verified draft', { exact: true })).not.toBeInTheDocument();
  });
});
