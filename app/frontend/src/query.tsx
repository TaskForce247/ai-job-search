import { createContext, useContext, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { QueryClient, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { UseQueryOptions } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, ApiError, queryString } from './api';
import type { Page, Run, RunAccepted, Workspace } from './types';
import { Button } from './components/ui';

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        refetchOnWindowFocus: true,
        retry: (count, error) => count < 1 && error instanceof ApiError && error.status >= 500,
      },
      mutations: { retry: false },
    },
  });
}

export function useApiQuery<T>(
  key: readonly unknown[],
  path: string,
  options: Omit<UseQueryOptions<T, Error, T, readonly unknown[]>, 'queryKey' | 'queryFn'> = {},
) {
  return useQuery<T, Error, T, readonly unknown[]>({
    queryKey: key, queryFn: ({ signal }) => api<T>(path, { signal }), ...options,
  });
}

export function useAction<T, V = void>(
  action: (values: V) => Promise<T>,
  onSuccess?: (data: T, values: V) => void | Promise<void>,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: action,
    onSuccess: async (data, values) => {
      await client.invalidateQueries();
      await onSuccess?.(data, values);
    },
  });
}

interface Notice { id: number; text: string; runId?: string }
interface NoticesContextValue {
  notify: (text: string) => void;
  queued: (run: RunAccepted, text?: string) => void;
}
const NoticesContext = createContext<NoticesContextValue | null>(null);

export function NoticesProvider({ children }: { children: ReactNode }) {
  const [notices, setNotices] = useState<Notice[]>([]);
  const counter = useRef(0);
  function add(text: string, runId?: string) {
    setNotices((previous) => [...previous.slice(-3), { id: ++counter.current, text, runId }]);
  }
  return <NoticesContext.Provider value={{
    notify: (text) => add(text),
    queued: (run, text = 'Operation queued. Its progress and any decisions will appear in Activity.') => add(text, run.run_id),
  }}>
    {children}
    <aside className="notice-stack" aria-label="Notifications">
      {notices.map((notice) => <div className="notice" key={notice.id}>
        <div role="status"><p>{notice.text}</p>{notice.runId &&
          <Link to={`/activity#run-${encodeURIComponent(notice.runId)}`}>View progress →</Link>}</div>
        <Button variant="ghost" aria-label="Dismiss notification"
          onClick={() => setNotices((items) => items.filter((item) => item.id !== notice.id))}>×</Button>
      </div>)}
    </aside>
  </NoticesContext.Provider>;
}

export function useNotices() {
  const value = useContext(NoticesContext);
  if (!value) throw new Error('NoticesProvider is required.');
  return value;
}

export function isActiveRun(run: Run) {
  return ['queued', 'running', 'awaiting_user'].includes(run.status);
}

function useRunsQuery() {
  return useQuery<Page<Run> & { older_active?: Run[] }>({
    queryKey: ['runs'],
    queryFn: async ({ signal }) => {
      const first = await api<Page<Run>>('/runs?limit=50', { signal });
      const olderActive: Run[] = [];
      const cursors = new Set<string>();
      const ids = new Set(first.items.map((run) => run.id));
      let cursor = first.next_cursor;
      // Decisions and in-flight work remain global even after they leave the latest page.
      while (cursor) {
        if (cursors.has(cursor)) throw new ApiError(0, 'ACTIVITY_PAGINATION', 'Activity history could not be paged. Refresh and try again.');
        cursors.add(cursor);
        const page = await api<Page<Run>>(`/runs${queryString({ limit: 50, cursor })}`, { signal });
        for (const run of page.items) {
          if (!ids.has(run.id) && (isActiveRun(run) || run.approvals.some((approval) => approval.status === 'pending'))) olderActive.push(run);
          ids.add(run.id);
        }
        cursor = page.next_cursor;
      }
      return { ...first, older_active: olderActive };
    },
    refetchInterval: (query) => query.state.data &&
      [...query.state.data.items, ...(query.state.data.older_active ?? [])].some(isActiveRun) ? 3_000 : 10_000,
    refetchIntervalInBackground: true,
  });
}

type ActivityValue = ReturnType<typeof useRunsQuery> & { streamNotice?: string };
const ActivityContext = createContext<ActivityValue | null>(null);

export function ActivityProvider({ children }: { children: ReactNode }) {
  const runs = useRunsQuery();
  const client = useQueryClient();
  const workspace = useApiQuery<Workspace>(['workspace'], '/workspace');
  const [streamNotice, setStreamNotice] = useState<string>();
  const sequences = useRef(new Map<string, number>());
  const previous = useRef<string | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const allRuns = [...(runs.data?.items ?? []), ...(runs.data?.older_active ?? [])];
  const streamRun = allRuns.find((run) => run.status === 'running') ?? allRuns.find((run) => run.status === 'queued');
  const streamId = streamRun?.id;
  useEffect(() => {
    if (!streamId || !workspace.data?.path) { setStreamNotice(undefined); return; }
    if (typeof EventSource === 'undefined') {
      setStreamNotice('Live events are unavailable in this browser. Progress continues to refresh automatically.');
      return;
    }
    const key = `${workspace.data.path}:${streamId}`;
    // Only stream the worker's current run, avoiding the browser's connection limit.
    const source = new EventSource(`/api/v1/runs/${encodeURIComponent(streamId)}/events?after=${sequences.current.get(key) ?? 0}`,
      { withCredentials: true });
    const fallback = () => setStreamNotice('Live updates are reconnecting. Progress continues to refresh automatically.');
    source.onopen = () => setStreamNotice(undefined);
    source.onerror = fallback;
    source.onmessage = (message) => {
      let event: unknown;
      try { event = JSON.parse(message.data); }
      catch (error) {
        if (!(error instanceof SyntaxError)) throw error;
        fallback(); source.close(); return;
      }
      if (!event || typeof event !== 'object' || !('run_id' in event) || event.run_id !== streamId ||
          !('sequence' in event) || typeof event.sequence !== 'number' || !Number.isSafeInteger(event.sequence) ||
          !('type' in event) || typeof event.type !== 'string') {
        fallback(); source.close(); return;
      }
      if (event.sequence <= (sequences.current.get(key) ?? 0)) return;
      sequences.current.set(key, event.sequence);
      void client.invalidateQueries({ queryKey: ['runs'] });
      if (['run.completed', 'run.failed', 'run.cancelled', 'run.interrupted', 'approval.required'].includes(event.type)) {
        source.close(); setStreamNotice(undefined);
      }
    };
    return () => {
      source.onopen = null; source.onmessage = null; source.onerror = null;
      source.close();
    };
  }, [streamId, workspace.data?.path, client]);
  const signature = runs.data ? allRuns.map((run) =>
    `${run.id}:${run.status}:${run.stage}:${run.updated_at}:${run.approvals.map((a) => `${a.id}:${a.status}`).join(',')}`).join('|')
    : undefined;
  useEffect(() => {
    if (signature === undefined || signature === previous.current) return;
    if (previous.current !== null) {
      void client.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'runs' });
    }
    previous.current = signature;
    const items = [...(runs.data?.items ?? []), ...(runs.data?.older_active ?? [])];
    const active = items.filter((run) => ['queued', 'running'].includes(run.status)).length;
    const approvals = items.flatMap((run) => run.approvals).filter((approval) => approval.status === 'pending').length;
    const failed = items.filter((run) => run.status === 'failed' || run.status === 'interrupted').length;
    setAnnouncement(`${active} operations in progress. ${approvals} decisions need your attention.${failed ? ` ${failed} failed or interrupted operations. See Activity for details.` : ''}`);
  }, [signature, client, runs.data]);
  return <ActivityContext.Provider value={{ ...runs, streamNotice }}>
    <div role="status" aria-live="polite" aria-atomic="true" className="sr-only">{announcement}</div>
    {children}
  </ActivityContext.Provider>;
}

export function useActivity() {
  const value = useContext(ActivityContext);
  if (!value) throw new Error('ActivityProvider is required.');
  return { ...value, allRuns: [...(value.data?.items ?? []), ...(value.data?.older_active ?? [])] };
}
