import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { AIConnection } from '../components/SetupControls';
import { JobDetailPage, JobImportForm } from '../pages/Jobs';
import { SubmissionForm } from '../pages/Applications';
import { ExternalLink } from '../components/ui';
import { Route, Routes } from 'react-router-dom';
import { applicationFixture, jobFixture } from './fixtures';
import { fetchFixtures, json, renderApp } from './helpers';

describe('real user actions and untrusted input', () => {
  it('requires title, employer and posting text for text-only imports', async () => {
    const user = userEvent.setup();
    const fetch = fetchFixtures({ '/api/v1/jobs/imports': { run_id: 'posting-run' } });
    renderApp(<JobImportForm />);
    await user.click(screen.getByRole('button', { name: 'Import posting for review' }));
    expect(await screen.findByText('A title is required without a URL.')).toBeVisible();
    expect(screen.getByText('An employer is required without a URL.')).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText('Job title'), 'Engineer');
    await user.type(screen.getByLabelText('Employer'), 'Example');
    await user.type(screen.getByLabelText('Posting text'), 'Original role requirements.');
    await user.click(screen.getByRole('button', { name: 'Import posting for review' }));
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toMatchObject({
      title: 'Engineer', company: 'Example', text: 'Original role requirements.', deadline: null,
    });
  });

  it('records Applied only after explicit acknowledgement of a real submission', async () => {
    const user = userEvent.setup();
    const fetch = fetchFixtures({ '/api/v1/applications/application-1/submissions': {} });
    renderApp(<SubmissionForm application={applicationFixture} />);
    await user.click(screen.getByRole('button', { name: 'Record actual submission' }));
    expect(await screen.findByText('Confirm that you submitted the application yourself.')).toBeVisible();
    expect(fetch).not.toHaveBeenCalled();
    await user.click(screen.getByLabelText('I submitted the application myself and want to record it as Applied.'));
    await user.click(screen.getByRole('button', { name: 'Record actual submission' }));
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    const payload = JSON.parse(String(fetch.mock.calls[0][1]?.body));
    expect(payload.expected_revision).toBe('application-r1');
    expect(payload).not.toHaveProperty('confirmed');
    expect(payload.submitted_on).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(await screen.findByText(/This app did not send files or contact the employer/)).toBeVisible();
  });

  it('clears a saved API key and blocks double clicks during a pending mutation', async () => {
    let finish!: (response: Response) => void;
    const fetch = fetchFixtures({
      '/api/v1/settings/ai-connection': () => new Promise<Response>((resolve) => { finish = resolve; }),
    });
    const user = userEvent.setup();
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    renderApp(<AIConnection connected={false} />);
    await user.type(screen.getByLabelText('API key'), 'sk-ant-test-only-example');
    await user.dblClick(screen.getByRole('button', { name: 'Save API connection' }));
    expect(fetch).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Save API connection' })).toBeDisabled();
    expect(storage).not.toHaveBeenCalled();
    finish(json({ connected: true }));
    await waitFor(() => expect(screen.getByLabelText('API key')).toHaveValue(''));
  });

  it('displays connection failures without claiming that a key has been saved', async () => {
    fetchFixtures({ '/api/v1/settings/ai-connection': () => json({
      error: { code: 'KEYRING_UNAVAILABLE', message: 'The OS credential store is locked.' },
    }, 503) });
    const user = userEvent.setup();
    renderApp(<AIConnection connected={false} />);
    await user.type(screen.getByLabelText('API key'), 'sk-ant-test-only-example');
    await user.click(screen.getByRole('button', { name: 'Save API connection' }));
    expect(await screen.findByText('The OS credential store is locked.')).toBeVisible();
    expect(screen.queryByText(/API key saved to/)).not.toBeInTheDocument();
  });

  it('renders hostile posting text literally and never creates injected elements', async () => {
    fetchFixtures({
      '/api/v1/jobs/job-1': jobFixture,
      '/api/v1/system/readiness': { ai_connected: true, privacy_consent: true, dependencies: [], active_runs: 0, version: 'test', workspace_path: '' },
    });
    const { container } = renderApp(<Routes><Route path="/jobs/:id" element={<JobDetailPage />} /></Routes>, { route: '/jobs/job-1' });
    expect(await screen.findByText(/<script>alert\("untrusted"\)<\/script>/)).toBeVisible();
    expect(container.querySelector('script')).toBeNull();
    expect(screen.getByText('Not ranked yet')).toBeVisible();
  });

  it('does not turn untrusted javascript or data URLs into links', () => {
    renderApp(<><ExternalLink href="javascript:alert(1)">Unsafe</ExternalLink>
      <ExternalLink href="data:text/html,hello">Also unsafe</ExternalLink>
      <ExternalLink href="https://example.test">Safe source</ExternalLink></>);
    expect(screen.queryByRole('link', { name: 'Unsafe' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Also unsafe' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Safe source/ })).toHaveAttribute('rel', 'noopener noreferrer');
  });
});
