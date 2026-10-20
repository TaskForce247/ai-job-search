import { act, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useActivity } from '../query';
import type { Run } from '../types';
import { runFixture, workspaceFixture } from './fixtures';
import { fetchFixtures, json, renderApp } from './helpers';

class FixtureEventSource {
  static instances: FixtureEventSource[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();

  constructor(public url: string, public options?: EventSourceInit) {
    FixtureEventSource.instances.push(this);
  }

  receive(event: unknown) {
    this.onmessage?.(new MessageEvent<string>('message', {
      data: typeof event === 'string' ? event : JSON.stringify(event),
    }));
  }
}

const activeRun: Run = { ...runFixture, status: 'running', approvals: [] };
function page(items: Run[]) { return { items, total: items.length, next_cursor: null }; }
function Probe() {
  const activity = useActivity();
  return <>
    <p>{activity.streamNotice ?? 'Live progress available'}</p>
    <p data-testid="run-status">{activity.allRuns[0]?.status}</p>
  </>;
}
function setup(items: Run[] = [activeRun]) {
  const fetch = fetchFixtures({
    '/api/v1/workspace': workspaceFixture, '/api/v1/runs': page(items),
  });
  return { fetch, ...renderApp(<Probe />, { activity: true }) };
}
function event(sequence: number, type = 'stage.progress') {
  return { run_id: activeRun.id, sequence, type };
}

beforeEach(() => {
  FixtureEventSource.instances = [];
  vi.stubGlobal('EventSource', FixtureEventSource);
});

describe('durable live activity', () => {
  it('opens only one credentialed stream for the running worker, not queued or historical work', async () => {
    setup([
      { ...runFixture, id: 'completed', status: 'succeeded', approvals: [] },
      { ...runFixture, id: 'queued', status: 'queued', approvals: [] },
      activeRun, runFixture,
    ]);
    await waitFor(() => expect(FixtureEventSource.instances).toHaveLength(1));
    const source = FixtureEventSource.instances[0];
    expect(source.url).toBe('/api/v1/runs/run-1/events?after=0');
    expect(source.options).toEqual({ withCredentials: true });
  });

  it('refreshes for increasing event sequences and ignores duplicates or older replay', async () => {
    const { client } = setup();
    await waitFor(() => expect(FixtureEventSource.instances).toHaveLength(1));
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const source = FixtureEventSource.instances[0];
    act(() => { source.receive(event(7)); });
    expect(invalidate).toHaveBeenCalledOnce();
    act(() => { source.receive(event(7)); source.receive(event(2)); });
    expect(invalidate).toHaveBeenCalledOnce();
    act(() => { source.receive(event(9)); });
    expect(invalidate).toHaveBeenCalledTimes(2);
  });

  it('does not lose a terminal event that arrives during an older status request', async () => {
    let requests = 0;
    let latest = activeRun;
    const pending: { release?: () => void } = {};
    fetchFixtures({
      '/api/v1/workspace': workspaceFixture,
      '/api/v1/runs': () => {
        requests++;
        if (requests === 2) {
          return new Promise<Response>((resolve) => {
            pending.release = () => resolve(json(page([activeRun])));
          });
        }
        return json(page([latest]));
      },
    });
    const { client } = renderApp(<Probe />, { activity: true });
    await waitFor(() => expect(FixtureEventSource.instances).toHaveLength(1));
    let inFlight: Promise<void> | undefined;
    act(() => { inFlight = client.refetchQueries({ queryKey: ['runs'] }); });
    await waitFor(() => expect(requests).toBe(2));
    latest = { ...activeRun, status: 'succeeded' };
    try {
      act(() => { FixtureEventSource.instances[0].receive(event(8, 'run.completed')); });
      await waitFor(() => expect(screen.getByTestId('run-status')).toHaveTextContent('succeeded'));
      expect(requests).toBe(3);
    } finally {
      await act(async () => { pending.release?.(); await inFlight; });
    }
  });

  it.each(['run.completed', 'run.failed', 'run.cancelled', 'run.interrupted', 'approval.required'])(
    'closes the source at %s while refreshing durable status',
    async (type) => {
      const { client } = setup();
      await waitFor(() => expect(FixtureEventSource.instances).toHaveLength(1));
      const invalidate = vi.spyOn(client, 'invalidateQueries');
      const source = FixtureEventSource.instances[0];
      act(() => { source.receive(event(3, type)); });
      expect(source.close).toHaveBeenCalled();
      expect(invalidate).toHaveBeenCalledOnce();
      expect(screen.getByText('Live progress available')).toBeVisible();
    },
  );

  it.each([
    null, 'not valid JSON', { ...event(1), run_id: 'unrelated-run' },
    { ...event(1), sequence: 1.5 }, { ...event(1), type: 123 },
  ])('rejects an invalid event without trusting its progress: %j', async (invalid) => {
    const { client } = setup();
    await waitFor(() => expect(FixtureEventSource.instances).toHaveLength(1));
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const source = FixtureEventSource.instances[0];
    act(() => { source.receive(invalid); });
    expect(source.close).toHaveBeenCalled();
    expect(invalidate).not.toHaveBeenCalled();
    expect(screen.getByText(/Progress continues to refresh automatically/)).toBeVisible();
  });

  it.each(['source error', 'unsupported browser'])('keeps bounded polling with a visible fallback after %s', async (failure) => {
    vi.useFakeTimers();
    if (failure === 'unsupported browser') vi.stubGlobal('EventSource', undefined);
    const { fetch, unmount } = setup();
    try {
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      if (failure === 'source error') act(() => { FixtureEventSource.instances[0].onerror?.(); });
      expect(screen.getByText(/Progress continues to refresh automatically/)).toBeVisible();
      const statusRequests = () => fetch.mock.calls.filter(([url]) => String(url).startsWith('/api/v1/runs')).length;
      expect(statusRequests()).toBe(1);
      await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
      expect(statusRequests()).toBe(2);
    } finally {
      unmount();
      vi.useRealTimers();
    }
  });

  it('replays from the last sequence, scopes cursors to the workspace, and removes old handlers', async () => {
    let workspace = workspaceFixture;
    fetchFixtures({
      '/api/v1/workspace': () => json(workspace), '/api/v1/runs': page([activeRun]),
    });
    const { client, unmount } = renderApp(<Probe />, { activity: true });
    await waitFor(() => expect(FixtureEventSource.instances).toHaveLength(1));
    const first = FixtureEventSource.instances[0];
    act(() => { first.receive(event(42)); });
    await act(async () => { await client.refetchQueries({ queryKey: ['runs'] }); });
    act(() => { client.setQueryData(['runs'], page([])); });
    await waitFor(() => expect(first.close).toHaveBeenCalled());
    expect(first.onmessage).toBeNull();
    act(() => { client.setQueryData(['runs'], page([activeRun])); });
    await waitFor(() => expect(FixtureEventSource.instances).toHaveLength(2));
    expect(FixtureEventSource.instances[1].url).toContain('after=42');
    workspace = { ...workspaceFixture, path: 'C:\\Another Workspace' };
    await act(async () => { await client.refetchQueries({ queryKey: ['workspace'] }); });
    await waitFor(() => expect(FixtureEventSource.instances).toHaveLength(3));
    expect(FixtureEventSource.instances[1].close).toHaveBeenCalled();
    expect(FixtureEventSource.instances[2].url).toContain('after=0');
    unmount();
    expect(FixtureEventSource.instances[2].close).toHaveBeenCalled();
    expect(FixtureEventSource.instances[2].onmessage).toBeNull();
  });
});
