import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentChecks, DocumentEditor } from '../components/DocumentEditor';
import { PdfPreview } from '../components/PdfPreview';
import { ApplicationDetailPage } from '../pages/Applications';
import type { Run } from '../types';
import { applicationFixture, documentFixture, profileFixture, runFixture, workspaceFixture } from './fixtures';
import { fetchFixtures, json, renderApp } from './helpers';

const pdfMocks = vi.hoisted(() => ({ getDocument: vi.fn() }));
vi.mock('pdfjs-dist', () => ({ GlobalWorkerOptions: { workerSrc: '' }, getDocument: pdfMocks.getDocument }));

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as CanvasRenderingContext2D);
  pdfMocks.getDocument.mockImplementation(() => ({
    promise: Promise.resolve({
      numPages: 1,
      getPage: async () => ({
        getViewport: ({ scale }: { scale: number }) => ({ width: 600 * scale, height: 800 * scale }),
        render: () => ({ promise: Promise.resolve(), cancel: vi.fn() }),
      }),
    }),
    destroy: vi.fn().mockResolvedValue(undefined),
  }));
});

describe('local PDF preview and document revisions', () => {
  it('never loads or presents an old PDF as the current preview when stale', () => {
    const fetch = fetchFixtures({});
    renderApp(<PdfPreview artifactId="pdf-old" stale content={documentFixture.content} />);
    expect(screen.getByText('PDF is out of date')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Download previous PDF (out of date)' })).toBeVisible();
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
    expect(pdfMocks.getDocument).not.toHaveBeenCalled();
  });

  describe('latest document operation', () => {
    const draftWriter: Run = {
      ...runFixture, id: 'draft-writer', kind: 'documents', status: 'failed', approvals: [],
      input: { application_id: applicationFixture.id }, stage: 'independent_review_1',
      message: 'Original draft-writing operation.',
    };

    function setup(latest: Run, visibleRuns: Run[] = [draftWriter]) {
      vi.stubGlobal('EventSource', undefined);
      const state = { latest, visibleRuns };
      const fetch = fetchFixtures({
        '/api/v1/workspace': workspaceFixture,
        '/api/v1/runs': () => json({
          items: state.visibleRuns, total: state.visibleRuns.length, next_cursor: null,
        }),
        '/api/v1/applications/application-1': {
          ...applicationFixture, last_run_id: draftWriter.id, latest_run_id: latest.id,
        },
        '/api/v1/applications/application-1/documents/cv': documentFixture,
        '/api/v1/profile': profileFixture,
        '/api/v1/system/readiness': { ai_connected: true, privacy_consent: true },
        [`/api/v1/runs/${latest.id}`]: () => json(state.latest),
        [`/api/v1/runs/${draftWriter.id}`]: draftWriter,
      });
      const result = renderApp(<Routes>
        <Route path="/applications/:id" element={<ApplicationDetailPage />} />
      </Routes>, { route: '/applications/application-1', activity: true });
      return { ...result, fetch, state };
    }

    it.each(['failed', 'cancelled', 'interrupted', 'succeeded'] as const)(
      'shows the newest %s attempt even when Activity does not retain it',
      async (status) => {
        const latest: Run = {
          ...draftWriter, id: 'latest-attempt', status, stage: 'evidence_review_2',
          message: 'Most recent document attempt.',
          error: status === 'failed' ? 'Evidence links need repair.' : null,
        };
        const { fetch } = setup(latest);
        expect(await screen.findByText(latest.message)).toBeVisible();
        const panel = screen.getByRole('heading', { name: 'Latest operation' }).closest('section')!;
        expect(within(panel).getByRole('link', { name: /Review operation & decisions/ }))
          .toHaveAttribute('href', '/activity#run-latest-attempt');
        if (latest.error) expect(within(panel).getByText(latest.error)).toBeVisible();
        expect(screen.queryByText(draftWriter.message)).not.toBeInTheDocument();
        expect(fetch.mock.calls.some(([url]) => String(url).includes('/runs/draft-writer'))).toBe(false);
      },
    );

    it('keeps the same attempt visible after it stops instead of returning to the draft writer', async () => {
      const latest: Run = {
        ...draftWriter, id: 'latest-attempt', status: 'running', stage: 'independent_review_2',
        message: 'Reviewing the latest attempt.',
      };
      const unrelated: Run = { ...latest, id: 'other-kind', kind: 'evaluate', message: 'Unrelated evaluation.' };
      const { client, fetch, state } = setup(latest, [unrelated, latest, draftWriter]);
      expect(await screen.findByText(latest.message)).toBeVisible();
      state.latest = { ...latest, status: 'failed', error: 'The newest review stopped.' };
      state.visibleRuns = [draftWriter];
      await act(async () => {
        await client.refetchQueries({ queryKey: ['runs'] });
      });
      expect(await screen.findByText('The newest review stopped.')).toBeVisible();
      const panel = screen.getByRole('heading', { name: 'Latest operation' }).closest('section')!;
      expect(within(panel).getByRole('link', { name: 'Open draft & resume checks' })).toBeVisible();
      expect(within(panel).queryByRole('button', { name: 'Cancel operation' })).not.toBeInTheDocument();
      expect(screen.queryByText(draftWriter.message)).not.toBeInTheDocument();
      expect(fetch.mock.calls.some(([url]) => String(url).includes('/runs/draft-writer'))).toBe(false);
    });
  });

  it('renders PDF.js locally and immediately hides rendered pages when edits make them stale', async () => {
    fetchFixtures({ '/api/v1/artifacts/pdf-current': () => new Response('PDF bytes') });
    const { rerender } = renderApp(<PdfPreview artifactId="pdf-current" stale={false} content={documentFixture.content} />);
    expect(await screen.findByRole('img', { name: /PDF page 1 of 1/ })).toBeVisible();
    expect(pdfMocks.getDocument).toHaveBeenCalledWith(expect.objectContaining({ data: expect.any(Uint8Array), isEvalSupported: false }));
    rerender(<PdfPreview artifactId="pdf-current" stale content={documentFixture.content} />);
    expect(screen.queryByRole('img')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Download current PDF' })).not.toBeInTheDocument();
    expect(screen.getByText('PDF is out of date')).toBeVisible();
  });

  it('labels a local draft PDF as unverified even when its text matches the saved draft', async () => {
    fetchFixtures({ '/api/v1/artifacts/draft-pdf': () => new Response('PDF bytes') });
    renderApp(<PdfPreview artifactId="draft-pdf" stale={false} content={documentFixture.content} unverified />);
    expect(await screen.findByRole('img', { name: /PDF page 1 of 1/ })).toBeVisible();
    expect(screen.getByText('Unverified draft PDF')).toBeVisible();
    expect(screen.getByRole('link', { name: 'Download draft PDF (unverified)' })).toBeVisible();
    expect(screen.queryByRole('link', { name: 'Download current PDF' })).not.toBeInTheDocument();
  });

  it('offers AI edits for failed PDF checks but does not run them automatically', async () => {
    const edits = vi.fn();
    renderApp(<DocumentChecks view={{ ...documentFixture, checks: [{
      id: 'layout', label: 'Layout', status: 'fail', message: 'Signature overflows to page two.',
    }] }} stale={false} onGenerateEdits={edits} />);
    expect(edits).not.toHaveBeenCalled();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Generate AI edits for failed checks' }));
    expect(edits).toHaveBeenCalledOnce();
  });

  it('provides a text alternative when PDF rendering fails', async () => {
    fetchFixtures({ '/api/v1/artifacts/pdf-bad': () => new Response('Bad bytes') });
    pdfMocks.getDocument.mockImplementation(() => ({ promise: Promise.reject(new Error('Damaged PDF')), destroy: vi.fn().mockResolvedValue(undefined) }));
    renderApp(<PdfPreview artifactId="pdf-bad" stale={false} content={documentFixture.content} />);
    expect(await screen.findByText('PDF preview unavailable')).toBeVisible();
    expect(screen.getByText('Damaged PDF')).toBeVisible();
    await userEvent.setup().click(screen.getByText('Read document text (accessible alternative)'));
    expect(screen.getByText('Original paragraph.')).toBeVisible();
  });

  it('edits, reorders, undoes, links evidence, and saves the exact content revision', async () => {
    const user = userEvent.setup();
    let savedContent = documentFixture.content;
    const saved = { ...documentFixture, revision: 'document-r2' };
    const fetch = fetchFixtures({
      '/api/v1/applications/application-1/documents/cv': (init: RequestInit | undefined) => {
        if (init?.method === 'PATCH') { savedContent = JSON.parse(String(init.body)).content; return json({}); }
        return json({ ...saved, content: savedContent });
      },
    });
    const onDirty = vi.fn();
    renderApp(<DocumentEditor applicationId="application-1" kind="cv" view={documentFixture}
      evidence={profileFixture.evidence} onDirtyChange={onDirty} />);
    await user.type(screen.getAllByLabelText('Body text')[0], ' Updated.');
    expect(onDirty).toHaveBeenLastCalledWith(true);
    await user.click(screen.getByRole('button', { name: 'Move block 2 up' }));
    expect(screen.getAllByLabelText('Body text')[0]).toHaveValue('');
    await user.click(screen.getByRole('button', { name: 'Undo last edit' }));
    expect(screen.getAllByLabelText('Body text')[0]).toHaveValue('Original paragraph. Updated.');
    await user.click(screen.getAllByText('Supporting evidence (1)')[0]);
    expect(screen.getAllByText('Built a reporting tool.').length).toBeGreaterThan(0);
    await user.click(screen.getByRole('button', { name: 'Save document edits' }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    const patch = JSON.parse(String(fetch.mock.calls[0][1]?.body));
    expect(patch.expected_revision).toBe('document-r1');
    expect(patch.content.blocks[0].id).toBe('block-1');
    expect(patch.content.blocks[0].text).toBe('Original paragraph. Updated.');
    expect(patch.content.blocks[0].evidence_ids).toEqual(['fact-1']);
    expect(screen.getByText('PDF is out of date')).toBeInTheDocument();
  });

  it('invalidates passes and never styles skipped checks as passed', () => {
    const { rerender } = renderApp(<DocumentChecks view={documentFixture} stale={false} />);
    expect(screen.getByText('Passed')).toHaveClass('badge-positive');
    expect(screen.getByText('Not checked')).not.toHaveClass('badge-positive');
    rerender(<DocumentChecks view={documentFixture} stale />);
    expect(screen.queryByText('Passed')).not.toBeInTheDocument();
    expect(screen.getByText('Previously passed · outdated')).not.toHaveClass('badge-positive');
    expect(screen.getByText('Not checked for current text')).not.toHaveClass('badge-positive');
    expect(screen.getByText('Previous results · out of date')).toBeVisible();
    expect(screen.getByText('Exactly two pages.')).toBeVisible();
  });

  it('distinguishes never checked and current failed checks from outdated results', () => {
    const edits = vi.fn();
    const { rerender } = renderApp(<DocumentChecks view={{ ...documentFixture, checks: [] }} stale />);
    expect(screen.getByText(/Never checked/)).toBeVisible();
    const view = { ...documentFixture, checks_stale: true, checks_stale_reasons: ['The saved text changed after these checks.'],
      checks: [{ id: 'pages', label: 'Page count', status: 'fail' as const, message: 'Expected 2; found 3.' }] };
    rerender(<DocumentChecks view={view} stale onGenerateEdits={edits} />);
    expect(screen.getByText('Previously failed · outdated')).not.toHaveClass('badge-negative');
    expect(screen.getByText('Expected 2; found 3.')).toBeVisible();
    expect(screen.getByText('The saved text changed after these checks.')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Generate AI edits for failed checks' })).not.toBeInTheDocument();
    rerender(<DocumentChecks view={{ ...view, checks_stale: false }} stale={false} onGenerateEdits={edits} />);
    expect(screen.getByText('Failed')).toHaveClass('badge-negative');
    expect(screen.getByRole('button', { name: 'Generate AI edits for failed checks' })).toBeEnabled();
  });

  it('preserves dirty edits when a newer saved rewrite arrives', async () => {
    const { rerender } = renderApp(<DocumentEditor applicationId="application-1" kind="cv" view={documentFixture}
      evidence={[]} onDirtyChange={() => undefined} />);
    const user = userEvent.setup();
    await user.type(screen.getAllByLabelText('Body text')[0], ' Local edit.');
    const rewritten = structuredClone(documentFixture);
    rewritten.revision = 'document-rewrite';
    rewritten.content.blocks[0].text = 'New server rewrite.';
    rerender(<DocumentEditor applicationId="application-1" kind="cv" view={rewritten}
      evidence={[]} onDirtyChange={() => undefined} />);
    expect(screen.getByText('Unsaved edits')).toBeVisible();
    expect(screen.getByText('A newer document revision is available')).toBeVisible();
    expect(screen.getAllByLabelText('Body text')[0]).toHaveValue('Original paragraph. Local edit.');
    expect(screen.getByText('Compare current edits with saved content')).toBeVisible();
  });

  it('offers a readable before/after when a completed rewrite changes saved content', async () => {
    const onDirty = vi.fn();
    const { rerender } = renderApp(<DocumentEditor applicationId="application-1" kind="cv" view={documentFixture}
      evidence={[]} onDirtyChange={onDirty} />);
    const rewritten = structuredClone(documentFixture);
    rewritten.revision = 'document-rewrite';
    rewritten.content.blocks[0].text = 'New server rewrite.';
    rerender(<DocumentEditor applicationId="application-1" kind="cv" view={rewritten} evidence={[]} onDirtyChange={onDirty} />);
    await userEvent.setup().click(await screen.findByText('Compare the previous and current document'));
    expect(screen.getByText('Original paragraph.')).toBeVisible();
    const after = screen.getByRole('heading', { name: 'After' }).closest('section')!;
    expect(within(after).getByText('New server rewrite.')).toBeVisible();
  });
});
