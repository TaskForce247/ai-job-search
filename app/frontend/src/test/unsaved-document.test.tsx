import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { DocumentEditor } from '../components/DocumentEditor';
import { documentFixture } from './fixtures';
import { fetchFixtures, json, renderApp } from './helpers';

function setup() {
  const fetch = fetchFixtures({
    '/api/v1/applications/application-1/documents/cv': (init?: RequestInit) => init?.method === 'PATCH' ? json({})
      : json({ ...documentFixture, revision: 'saved-new', content: documentFixture.content }),
  });
  const result = renderApp(<Routes>
    <Route path="/edit" element={<><Link to="/jobs">Go to jobs</Link>
      <DocumentEditor applicationId="application-1" kind="cv" view={documentFixture}
        evidence={[]} onDirtyChange={vi.fn()} /></>} />
    <Route path="/jobs" element={<h1>Job collection</h1>} />
  </Routes>, { route: '/edit' });
  return { ...result, fetch };
}

describe('unsaved document navigation protection', () => {
  it('blocks navigation, preserves edits when staying, and requires explicit discard to leave', async () => {
    setup();
    const user = userEvent.setup();
    await user.type(screen.getAllByLabelText('Body text')[0], ' Unsaved.');
    await user.click(screen.getByRole('link', { name: 'Go to jobs' }));
    expect(screen.getByRole('dialog', { name: 'Leave without saving your document?' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Stay & keep editing' }));
    expect(screen.getAllByLabelText('Body text')[0]).toHaveValue('Original paragraph. Unsaved.');
    await user.click(screen.getByRole('link', { name: 'Go to jobs' }));
    await user.click(screen.getByRole('button', { name: 'Discard edits & leave' }));
    expect(await screen.findByRole('heading', { name: 'Job collection' })).toBeVisible();
  });

  it('protects browser back navigation as well as links', async () => {
    const { router } = setup();
    await act(async () => { await router.navigate('/jobs'); await router.navigate('/edit'); });
    await userEvent.setup().type(screen.getAllByLabelText('Body text')[0], ' Unsaved.');
    await act(async () => { await router.navigate(-1); });
    expect(screen.getByRole('dialog', { name: 'Leave without saving your document?' })).toBeVisible();
    await userEvent.setup().click(screen.getByRole('button', { name: 'Stay & keep editing' }));
    expect(router.state.location.pathname).toBe('/edit');
  });

  it('warns on browser close/reload only while edits are unsaved and cleans up on unmount', async () => {
    const { unmount } = setup();
    const clean = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);
    await userEvent.setup().type(screen.getAllByLabelText('Body text')[0], ' Unsaved.');
    const dirty = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
    unmount();
    const after = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(after);
    expect(after.defaultPrevented).toBe(false);
  });

  it('allows navigation after saving edits', async () => {
    setup();
    const user = userEvent.setup();
    await user.type(screen.getAllByLabelText('Body text')[0], ' Save me.');
    await user.click(screen.getByRole('button', { name: 'Save document edits' }));
    await screen.findByText('Document edits saved. Compile and check them before using a PDF.');
    await user.click(screen.getByRole('link', { name: 'Go to jobs' }));
    expect(await screen.findByRole('heading', { name: 'Job collection' })).toBeVisible();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
