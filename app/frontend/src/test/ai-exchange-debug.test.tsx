import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AIExchangeDebug } from '../components/AIExchangeDebug';
import { runFixture } from './fixtures';
import { fetchFixtures, json, renderApp } from './helpers';

const path = '/api/v1/runs/run-1/ai-traces';
const detail = {
  id: 'trace-1', stage: 'independent_review_1', status: 'failed', source: 'captured',
  created_at: '2026-10-21', note: 'Public responses only. Images are hashed.',
  request: { system_prompt: 'Full canonical instructions', user_prompt: 'Full document sent',
    output_schema: { type: 'object' }, model: 'test-model' },
  responses: [{ type: 'result', structured_output: { company_claims: ['Software Engineer, AI Data role'] } }],
};

function open() {
  fireEvent.click(screen.getByText('AI prompts & responses (debug)'));
}

describe('AI exchange debug viewer', () => {
  it('fetches only when opened, shows full requests and raw results, and makes no AI call', async () => {
    const fetch = fetchFixtures({
      [path]: [detail],
      [`${path}/trace-1`]: detail,
    });
    renderApp(<AIExchangeDebug run={runFixture} />);
    expect(fetch).not.toHaveBeenCalled();
    open();
    expect(await screen.findByText(/Software Engineer, AI Data role/)).toBeVisible();
    fireEvent.click(screen.getByText('System prompt'));
    expect(screen.getByText('Full canonical instructions')).toBeVisible();
    fireEvent.click(screen.getByText('User prompt'));
    expect(screen.getByText('Full document sent')).toBeVisible();
    expect(screen.getByText(/Contains personal profile/)).toBeVisible();
    expect(fetch.mock.calls.every(([, init]) => init?.method === undefined || init.method === 'GET')).toBe(true);
    const copy = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: copy } });
    fireEvent.click(screen.getByRole('button', { name: 'Copy exchange' }));
    expect(await screen.findByRole('button', { name: 'Exchange copied' })).toBeVisible();
    expect(JSON.parse(copy.mock.calls[0][0])).toEqual(detail);
  });

  it('labels legacy snapshots and allows switching stages and refreshing', async () => {
    const legacy = { ...detail, id: 'old', source: 'legacy_snapshot', responses: [],
      note: 'Legacy snapshot, not a complete wire exchange.' };
    const fetch = fetchFixtures({
      [path]: [detail, legacy], [`${path}/old`]: legacy, [`${path}/trace-1`]: detail,
    });
    renderApp(<AIExchangeDebug run={runFixture} />);
    open();
    expect(await screen.findByText('Legacy snapshot, not a complete wire exchange.')).toBeVisible();
    expect(screen.getByText(/No response was recorded/)).toBeVisible();
    fireEvent.change(screen.getByRole('combobox', { name: 'AI stage' }), { target: { value: 'trace-1' } });
    expect(await screen.findByText(/Software Engineer, AI Data role/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh exchanges' }));
    await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url === path)).toHaveLength(2));
  });

  it('shows unavailable history without inventing an exchange', async () => {
    fetchFixtures({ [path]: [] });
    renderApp(<AIExchangeDebug run={runFixture} />);
    open();
    expect(await screen.findByText(/No recorded AI exchanges/)).toBeVisible();
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });

  it('surfaces load errors and allows retry', async () => {
    fetchFixtures({ [path]: () => json({ error: { code: 'not_found', message: 'Operation not found.' } }, 404) });
    renderApp(<AIExchangeDebug run={runFixture} />);
    open();
    expect(await screen.findByText('Operation not found.')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeVisible();
  });
});
