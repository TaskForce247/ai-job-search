import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DocumentChecks, DocumentEditor } from '../components/DocumentEditor';
import { PdfPreview } from '../components/PdfPreview';
import { documentFixture, profileFixture } from './fixtures';
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
    expect(screen.getAllByText('Needs checking').length).toBeGreaterThan(1);
    expect(screen.getByText('Checks are out of date until the current edits are compiled and verified.')).toBeVisible();
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
