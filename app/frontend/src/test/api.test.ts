import { describe, expect, it, vi } from 'vitest';
import { api, ApiError, initializeSession, resetSessionForTests, setCsrfToken } from '../api';
import { fetchFixtures, json } from './helpers';

describe('local API authentication and transport', () => {
  it('removes a bootstrap fragment immediately and keeps CSRF only in memory', async () => {
    window.history.replaceState(null, '', '/welcome?step=2#token=opaque-bootstrap-token-example');
    const storage = vi.spyOn(Storage.prototype, 'setItem');
    const fetch = fetchFixtures({
      '/api/v1/session/bootstrap': (init: RequestInit | undefined) => {
        expect(window.location.hash).toBe('');
        expect(window.location.pathname + window.location.search).toBe('/welcome?step=2');
        expect(JSON.parse(String(init?.body))).toEqual({ token: 'opaque-bootstrap-token-example' });
        expect(init?.credentials).toBe('same-origin');
        return json({ csrf_token: 'csrf-from-bootstrap' });
      },
      '/api/v1/jobs/job-1': {},
    });
    await initializeSession();
    await api('/jobs/job-1', { method: 'PATCH', body: { saved: true, expected_revision: 'r1' } });
    const headers = fetch.mock.calls[1][1]?.headers as Headers;
    expect(headers.get('X-CSRF-Token')).toBe('csrf-from-bootstrap');
    expect(headers.get('Idempotency-Key')).toMatch(/^[a-f0-9-]{36}$/);
    expect(storage).not.toHaveBeenCalled();
  });

  it('resumes a reload with the cookie session and never resends the bootstrap', async () => {
    const fetch = fetchFixtures({ '/api/v1/session': { csrf_token: 'resumed-csrf' } });
    await Promise.all([initializeSession(), initializeSession()]);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/session');
    expect(fetch.mock.calls[0][1]?.method).toBe('GET');
  });

  it('uses a distinct idempotency UUID per user intent and does not retry mutations', async () => {
    setCsrfToken('csrf');
    const fetch = fetchFixtures({ '/api/v1/search-runs': { run_id: 'queued-run' } });
    await api('/search-runs', { method: 'POST', body: { query: 'developer' } });
    await api('/search-runs', { method: 'POST', body: { query: 'developer' } });
    const keys = fetch.mock.calls.map((call) => (call[1]?.headers as Headers).get('Idempotency-Key'));
    expect(keys[0]).not.toBe(keys[1]);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it('sends multipart uploads without breaking their browser-generated boundary', async () => {
    setCsrfToken('csrf');
    const fetch = fetchFixtures({ '/api/v1/documents/imports': { run_id: 'import-1' } });
    const body = new FormData(); body.append('file', new File(['CV text'], 'cv.txt'));
    await api('/documents/imports', { method: 'POST', body });
    const options = fetch.mock.calls[0][1];
    expect(options?.body).toBe(body);
    expect((options?.headers as Headers).has('Content-Type')).toBe(false);
    expect((options?.headers as Headers).get('X-CSRF-Token')).toBe('csrf');
  });

  it('preserves the error envelope and announces an expired session', async () => {
    const listener = vi.fn(); window.addEventListener('app:unauthorized', listener);
    fetchFixtures({
      '/api/v1/profile': () => json({ error: { code: 'SESSION_REQUIRED', message: 'Session expired.', detail: 'Reopen.' } }, 401),
    });
    await expect(api('/profile')).rejects.toMatchObject({ status: 401, code: 'SESSION_REQUIRED', message: 'Session expired.', detail: 'Reopen.' });
    expect(listener).toHaveBeenCalledOnce();
    window.removeEventListener('app:unauthorized', listener);
  });

  it('reports malformed server and connection errors instead of returning mock records', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response('Server failed', { status: 502 }))
      .mockRejectedValueOnce(new TypeError('Failed to fetch')));
    await expect(api('/jobs')).rejects.toMatchObject({ status: 502, code: 'REQUEST_FAILED' });
    await expect(api('/jobs')).rejects.toBeInstanceOf(ApiError);
    await expect(api('https://elsewhere.test')).rejects.toThrow('Only local application API paths');
  });

  it('can reset initialization between independent sessions', async () => {
    const fetch = fetchFixtures({ '/api/v1/session': { csrf_token: 'csrf' } });
    await initializeSession(); resetSessionForTests(); await initializeSession();
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
