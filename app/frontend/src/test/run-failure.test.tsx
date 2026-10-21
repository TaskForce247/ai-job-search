import { act, fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { RunFailure } from '../components/RunFailure';
import type { Run } from '../types';
import { runFixture } from './fixtures';
import { renderApp } from './helpers';

const aiFailure = {
  kind: 'output_limit', title: 'The AI response hit its output limit',
  explanation: 'The response stopped before the draft was complete.',
  remedy: 'Reduce the requested scope and try again.',
  action: { label: 'Open Settings', href: '/settings' },
  stage: 'draft_documents', model: 'example-model', output_tokens: 48_000,
  thinking_tokens: 41_280, output_token_cap: 12_000, api_error_status: 429,
  num_turns: 0, stop_reason: null, errors: ['Provider stopped the response.'],
};
const failedRun: Run = {
  ...runFixture, status: 'failed', error: 'The provider response was incomplete.',
  error_code: 'ai_output_limit', result: { ai_failure: aiFailure },
};

function setup(run: Run = failedRun) {
  return renderApp(<RunFailure run={run} />);
}

describe('run failure diagnostics', () => {
  it('renders actionable diagnostic sections and compact usage facts', () => {
    setup();
    const alert = screen.getByRole('alert');
    expect(within(alert).getByText(aiFailure.title, { selector: 'strong' })).toBeVisible();
    expect(within(alert).getByRole('heading', { name: 'What happened' })).toBeVisible();
    expect(within(alert).getByText(aiFailure.explanation, { selector: 'p' })).toBeVisible();
    expect(within(alert).getByRole('heading', { name: 'What to do' })).toBeVisible();
    expect(within(alert).getByText(aiFailure.remedy, { selector: 'p' })).toBeVisible();
    expect(screen.getByRole('link', { name: 'Open Settings' })).toHaveAttribute('href', '/settings');
    expect(screen.getByText(new RegExp(`Used ${(48_000).toLocaleString()} output tokens \\(86% thinking\\) of a ${(12_000).toLocaleString()}-token cap per attempt`))).toBeVisible();
    expect(screen.getByText(/Draft documents · example-model/)).toBeVisible();
    expect(screen.getByText(/HTTP 429/)).toBeVisible();
    expect(screen.getByText(failedRun.error!)).toBeVisible();
    expect(screen.getByText('Saved drafts are preserved.')).toBeVisible();
    expect(screen.queryByText(/A queued request is not a successful result/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Technical details'));
    const details = alert.querySelector('dl')!;
    expect(within(details).getByText('Reference')).toBeVisible();
    expect(within(details).getByText('ai_output_limit')).toBeVisible();
    expect(within(details).getByText('Run ID')).toBeVisible();
    expect(within(details).getByText(failedRun.id)).toBeVisible();
    expect(within(details).getByText('Provider stopped the response.')).toBeVisible();
    expect(within(details).getByText('0')).toBeVisible();
    expect(within(details).queryByText('Stop reason')).not.toBeInTheDocument();
  });

  it.each([null, { category: 'OSError', errno: 2, winerror: 2 }, { ai_failure: { kind: 'broken' } }])(
    'preserves the generic failure and reference for legacy or malformed results: %j', (result) => {
      setup({ ...failedRun, result });
      expect(screen.getByText('The operation did not complete')).toBeVisible();
      expect(screen.getByText(failedRun.error!)).toBeVisible();
      expect(screen.getByText('Reference: ai_output_limit')).toBeVisible();
      expect(screen.queryByRole('heading', { name: 'What happened' })).not.toBeInTheDocument();
    },
  );

  it('keeps the interrupted title for failures without structured diagnostics', () => {
    setup({ ...failedRun, status: 'interrupted', result: null });
    expect(screen.getByText('Work was interrupted')).toBeVisible();
  });

  it('renders structured diagnostics even without a legacy error string', () => {
    setup({ ...failedRun, error: null });
    expect(screen.getByText(aiFailure.title, { selector: 'strong' })).toBeVisible();
  });

  it('copies the diagnostic JSON and briefly confirms success', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    vi.useFakeTimers();
    try {
      setup();
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Copy diagnostics' })); });
      expect(writeText).toHaveBeenCalledOnce();
      expect(JSON.parse(writeText.mock.calls[0][0] as string)).toEqual({
        run_id: failedRun.id, kind: failedRun.kind, stage: failedRun.stage,
        error_code: failedRun.error_code, error: failedRun.error, ai_failure: aiFailure,
      });
      expect(screen.getByRole('status')).toHaveTextContent('Copied');
      act(() => { vi.advanceTimersByTime(2500); });
      expect(screen.getByRole('status')).toBeEmptyDOMElement();
    } finally {
      vi.useRealTimers();
    }
  });

  it.each(['unavailable', 'denied'])('handles an %s clipboard without an unhandled rejection', async (state) => {
    vi.stubGlobal('navigator', { clipboard: state === 'unavailable' ? undefined : {
      writeText: vi.fn().mockRejectedValue(new Error('Permission denied')),
    } });
    setup();
    fireEvent.click(screen.getByRole('button', { name: 'Copy diagnostics' }));
    expect(await screen.findByText(/Copy the technical details manually/)).toBeVisible();
  });

  it('handles zero tokens without division by zero or missing optional facts', () => {
    setup({ ...failedRun, result: { ai_failure: {
      kind: 'no_result', title: 'No result', explanation: 'Nothing returned.', remedy: 'Try again.',
      output_tokens: 0, thinking_tokens: 0,
    } } });
    expect(screen.getByText('Used 0 output tokens')).toBeVisible();
    expect(screen.queryByText(/NaN|Infinity/)).not.toBeInTheDocument();
  });

  it('does not render unsafe action URLs', () => {
    setup({ ...failedRun, result: { ai_failure: {
      ...aiFailure, action: { label: 'Unsafe action', href: 'javascript:alert(1)' },
    } } });
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });
});
