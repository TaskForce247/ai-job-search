const BASE = '/api/v1';
let csrfToken: string | null = null;
let initialization: Promise<void> | undefined;

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public detail?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

export function intentKey() {
  return crypto.randomUUID();
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
  idempotencyKey?: string;
}

async function request(path: string, options: RequestOptions = {}) {
  if (!path.startsWith('/') || path.startsWith('//')) {
    throw new Error('Only local application API paths are allowed.');
  }
  const method = options.method ?? 'GET';
  const headers = new Headers({ Accept: 'application/json' });
  const isForm = options.body instanceof FormData;
  if (options.body !== undefined && !isForm) headers.set('Content-Type', 'application/json');
  if (method !== 'GET') {
    headers.set('Idempotency-Key', options.idempotencyKey ?? intentKey());
    if (csrfToken) headers.set('X-CSRF-Token', csrfToken);
  }
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method, headers, credentials: 'same-origin', cache: 'no-store',
      signal: options.signal,
      body: options.body === undefined ? undefined
        : isForm ? options.body as FormData : JSON.stringify(options.body),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, 'CONNECTION_UNAVAILABLE',
      'The local application is not responding. Check that it is still open, then try again.');
  }
  if (!response.ok) {
    let envelope: { error?: { code?: string; message?: string; detail?: unknown } } = {};
    try { envelope = await response.json(); } catch { /* Non-JSON failures still have a visible message. */ }
    const error = new ApiError(response.status, envelope.error?.code ?? 'REQUEST_FAILED',
      envelope.error?.message ?? `The request could not be completed (${response.status}).`,
      envelope.error?.detail);
    if (response.status === 401) {
      csrfToken = null;
      window.dispatchEvent(new CustomEvent('app:unauthorized'));
    }
    throw error;
  }
  return response;
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await request(path, options);
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return text ? JSON.parse(text) as T : undefined as T;
}

export async function artifactBytes(id: string, signal?: AbortSignal) {
  const response = await request(`/artifacts/${encodeURIComponent(id)}`, { signal });
  return new Uint8Array(await response.arrayBuffer());
}

export function artifactUrl(id: string) {
  return `${BASE}/artifacts/${encodeURIComponent(id)}`;
}

export function initializeSession(): Promise<void> {
  if (initialization) return initialization;
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const token = fragment.get('token');
  if (token !== null) {
    // Remove the bearer credential before the first network request or React render.
    window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search);
  }
  initialization = api<{ csrf_token: string }>(
    token !== null ? '/session/bootstrap' : '/session',
    token !== null ? { method: 'POST', body: { token } satisfies ApiSchema<'BootstrapInput'> } : {},
  ).then((session) => {
    if (!session.csrf_token) throw new ApiError(401, 'SESSION_REQUIRED', 'The local session is unavailable.');
    setCsrfToken(session.csrf_token);
  });
  return initialization;
}

export function resetSessionForTests() {
  initialization = undefined;
  csrfToken = null;
}

export function retrySession() {
  initialization = undefined;
  return initializeSession();
}

export function queryString(values: Record<string, string | number | null | undefined>) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  });
  const result = params.toString();
  return result ? `?${result}` : '';
}
import type { ApiSchema } from './types';
