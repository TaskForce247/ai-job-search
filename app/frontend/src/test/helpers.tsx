import type { ReactNode } from 'react';
import { render } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { ActivityProvider, createQueryClient, NoticesProvider } from '../query';
import { setCsrfToken } from '../api';

export function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

type Handler = unknown | ((init: RequestInit | undefined, path: string) => Response | Promise<Response>);

export function fetchFixtures(handlers: Record<string, Handler>) {
  const mock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const path = String(input);
    const handler = handlers[path] ?? handlers[path.split('?')[0]];
    if (handler === undefined) throw new Error(`Unexpected fetch: ${path}`);
    return typeof handler === 'function' ? (handler as (init?: RequestInit, path?: string) => Response)(init, path) : json(handler);
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}

export function renderApp(ui: ReactNode, options: { route?: string; activity?: boolean } = {}) {
  setCsrfToken('test-csrf-token');
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false, gcTime: 0 }, mutations: { retry: false } });
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}><MemoryRouter initialEntries={[options.route ?? '/']}>
      <NoticesProvider>{options.activity ? <ActivityProvider>{children}</ActivityProvider> : children}</NoticesProvider>
    </MemoryRouter></QueryClientProvider>;
  }
  const result = render(ui, { wrapper: Wrapper });
  return { ...result, client };
}
