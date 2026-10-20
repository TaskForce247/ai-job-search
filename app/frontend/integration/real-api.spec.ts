import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import type { APIRequestContext, Page, Request } from '@playwright/test';
import type { Application, Page as ApiPage, ProfileView, Run, RunAccepted } from '../src/types';
import { expect, test } from './fixtures';

const candidate = 'Synthetic Candidate';
const employer = 'Example Hiring';
const role = 'Software Developer';
const changedBullet = 'Built reliable Python services for an internal reporting team.';
const posting = [
  'SYNTHETIC INTEGRATION POSTING - not a real vacancy.',
  'Example Hiring is seeking a Software Developer in Example City.',
  'Build and maintain Python services for an internal reporting team. SQL and TypeScript are useful.',
  'Professional English is required. This local role does not require relocation.',
  'Apply through the employer process yourself; this application never sends documents.',
  '<script>throw new Error("untrusted posting must remain plain text")</script>',
].join('\n');

async function getJson<T>(request: APIRequestContext, url: string): Promise<T> {
  const response = await request.get(url);
  expect(response.status(), `${url}: ${await response.text()}`).toBe(200);
  return await response.json() as T;
}

async function waitForRun(request: APIRequestContext, origin: string, id: string, status: Run['status']) {
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    const run = await getJson<Run>(request, `${origin}/api/v1/runs/${encodeURIComponent(id)}`);
    if (run.status === status) return run;
    if (['failed', 'cancelled', 'interrupted', 'succeeded'].includes(run.status)) {
      throw new Error(`${run.kind} stopped at ${run.stage}: ${run.status}; ${run.error_code}; ${run.error}`);
    }
    await new Promise((done) => setTimeout(done, 100));
  }
  throw new Error(`Run ${id} did not reach ${status} within 15 seconds.`);
}

async function navigate(page: Page, label: RegExp) {
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: label }).click();
}

test('real FastAPI: confirm profile, evaluate, approve, edit PDFs, submit, record outcome and reload', async ({ page, integration }) => {
  const { origin, workspace_path: workspace, data_directory: directory } = integration;
  const errors: string[] = [];
  const mutations: Request[] = [];
  const requests: Request[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('request', (request) => {
    requests.push(request);
    if (request.url().startsWith(`${origin}/api/v1/`) && request.method() !== 'GET') mutations.push(request);
  });
  const unauthorized = await page.request.get(`${origin}/api/v1/profile`);
  expect(unauthorized.status()).toBe(401);
  const bootstrapResponse = page.waitForResponse((response) => response.url().endsWith('/session/bootstrap'));
  await page.goto(integration.url);
  expect((await bootstrapResponse).status()).toBe(200);
  await expect(page.getByRole('heading', { name: 'Your next chapter starts here.' })).toBeVisible();
  expect(new URL(page.url()).hash).toBe('');
  const cookies = await page.context().cookies(`${origin}/api/v1`);
  const cookie = cookies.find((item) => item.name.startsWith('aijs_'));
  expect(cookie).toMatchObject({ httpOnly: true, sameSite: 'Strict', path: '/api/v1' });
  expect(await page.evaluate(() => document.cookie)).not.toContain(cookie!.name);
  const session = await getJson<{ csrf_token: string }>(page.request, `${origin}/api/v1/session`);

  await page.getByRole('button', { name: 'Continue to AI & privacy' }).click();
  await page.getByLabel('API key', { exact: true }).fill('sk-ant-integration-fixture-not-a-real-key');
  await page.getByRole('button', { name: 'Save API connection' }).click();
  await expect(page.getByText('Key configured', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Replace API key', { exact: true })).toHaveValue('');
  await page.getByLabel('I understand the cloud data sharing and separate API billing, and consent to AI processing when I request it.').check();
  await page.getByRole('button', { name: 'Save data-sharing choice' }).click();
  await expect(page.getByRole('button', { name: 'Save data-sharing choice' })).toBeDisabled();
  await page.getByRole('button', { name: 'Continue to your profile' }).click();
  await page.getByRole('link', { name: 'Build my profile manually' }).click();
  await page.getByLabel('Full name', { exact: true }).fill(candidate);
  await page.getByLabel('Professional headline', { exact: true }).fill('Software developer');
  await page.getByLabel('Email', { exact: true }).fill('candidate@example.invalid');
  await page.getByLabel('Phone', { exact: true }).fill('+45 12345678');
  await page.getByLabel('Location', { exact: true }).fill('Example City');
  await page.getByLabel('Professional summary', { exact: true }).fill('Builds reliable Python services.');
  await page.getByRole('button', { name: '+ Add experience', exact: true }).click();
  await page.getByLabel('Job title', { exact: true }).fill('Software Developer');
  await page.getByLabel('Employer', { exact: true }).fill('Example Systems');
  await page.getByLabel('Work location', { exact: true }).fill('Example City');
  await page.getByLabel('Start date or year', { exact: true }).fill('2020');
  await page.getByLabel('End date or Present', { exact: true }).fill('2025');
  await page.getByLabel('Responsibilities and achievements', { exact: true }).fill('Built Python services for an internal reporting team.');
  await page.getByRole('button', { name: '+ Add language', exact: true }).click();
  await page.getByLabel('Language', { exact: true }).fill('English');
  await page.getByLabel('Proficiency', { exact: true }).fill('C2');
  await page.getByLabel('Skills', { exact: true }).fill('Python\nSQL\nTypeScript');
  const profileResponse = page.waitForResponse((response) => response.url().endsWith('/profile/changes') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Review profile changes' }).click();
  const proposed = await profileResponse;
  expect(proposed.status()).toBe(202);
  const profileRun = await proposed.json() as RunAccepted;
  const originalRequest = proposed.request();
  const headers = {
    Origin: origin, 'X-CSRF-Token': session.csrf_token,
    'Idempotency-Key': (await originalRequest.allHeaders())['idempotency-key'],
  };
  const requestBody = originalRequest.postDataJSON() as Record<string, unknown>;
  const replay = await page.request.post(`${origin}/api/v1/profile/changes`, { headers, data: requestBody });
  expect(replay.status()).toBe(202);
  expect(await replay.json()).toEqual(profileRun);
  const conflict = await page.request.post(`${origin}/api/v1/profile/changes`, {
    headers, data: { ...requestBody, expected_revision: 'different-intent' },
  });
  expect(conflict.status()).toBe(409);
  expect((await conflict.json()).error.code).toBe('idempotency_conflict');
  const noCsrf = await page.request.post(`${origin}/api/v1/profile/changes`, {
    headers: { Origin: origin, 'Idempotency-Key': randomUUID() }, data: requestBody,
  });
  expect(noCsrf.status()).toBe(403);
  expect((await noCsrf.json()).error.code).toBe('csrf_required');
  const noIntent = await page.request.post(`${origin}/api/v1/profile/changes`, {
    headers: { Origin: origin, 'X-CSRF-Token': session.csrf_token }, data: requestBody,
  });
  expect(noIntent.status()).toBe(400);
  expect((await noIntent.json()).error.code).toBe('idempotency_key_required');
  const pendingProfile = await waitForRun(page.request, origin, profileRun.run_id, 'awaiting_user');
  expect(pendingProfile.approvals).toHaveLength(1);
  const profilePath = join(workspace, '.claude\\skills\\job-application-assistant\\01-candidate-profile.md');
  expect(await readFile(profilePath, 'utf8')).not.toContain(candidate);
  expect((await getJson<ProfileView>(page.request, `${origin}/api/v1/profile`)).data.name).toBe('');
  await navigate(page, /^Activity/);
  await expect(page.getByRole('heading', { name: 'Confirm your profile changes' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Confirm your profile changes' })).toBeVisible();
  await page.getByRole('button', { name: 'Confirm profile changes', exact: true }).click();
  await waitForRun(page.request, origin, profileRun.run_id, 'succeeded');
  await expect.poll(() => readFile(profilePath, 'utf8')).toContain(candidate);
  expect(await readFile(join(workspace, 'CLAUDE.md'), 'utf8')).toContain('candidate@example.invalid');
  expect(await readFile(join(workspace, 'cv\\main_example.tex'), 'utf8')).toContain('\\name{Synthetic}{Candidate}');

  await page.goto(`${origin}/welcome?step=4`);
  await page.getByLabel('Default job search', { exact: true }).fill(role);
  await page.getByLabel('Search location', { exact: true }).fill('Example City');
  await page.getByRole('button', { name: 'Finish setup & open dashboard' }).click();
  await expect(page.getByRole('heading', { name: 'Your search, at a glance.' })).toBeVisible();
  await navigate(page, /^Find jobs$/);
  await page.getByRole('button', { name: '+ Add a posting', exact: true }).click();
  await page.getByLabel('Job title', { exact: true }).fill(role);
  await page.getByLabel('Employer', { exact: true }).fill(employer);
  await page.getByLabel('Job location', { exact: true }).fill('Example City');
  await page.getByLabel('Posting text', { exact: true }).fill(posting);
  const importResponse = page.waitForResponse((response) => response.url().endsWith('/jobs/imports'));
  await page.getByRole('button', { name: 'Import posting for review' }).click();
  const importRun = await (await importResponse).json() as RunAccepted;
  await waitForRun(page.request, origin, importRun.run_id, 'succeeded');
  await expect(page.getByRole('link', { name: role, exact: true })).toBeVisible();
  const jobs = await getJson<ApiPage<{ id: string }>>(page.request, `${origin}/api/v1/jobs`);
  expect(jobs.total).toBe(1);
  const state = JSON.parse(await readFile(join(workspace, 'job_scraper\\seen_jobs.json'), 'utf8')) as { seen: Record<string, { description: string }> };
  expect(state.seen[jobs.items[0].id].description).toBe(posting);
  await page.getByRole('link', { name: role, exact: true }).click();
  await expect(page.getByText('<script>throw new Error("untrusted posting must remain plain text")</script>', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Check this job', exact: true }).click();
  const evaluationResponse = page.waitForResponse((response) => response.url().endsWith('/evaluation-runs'));
  await page.getByRole('button', { name: 'Evaluate fit', exact: true }).click();
  const evaluationRun = await (await evaluationResponse).json() as RunAccepted;
  await waitForRun(page.request, origin, evaluationRun.run_id, 'awaiting_user');
  expect((await getJson<ApiPage<Application>>(page.request, `${origin}/api/v1/applications`)).total).toBe(0);
  await navigate(page, /^Activity/);
  await expect(page.getByRole('heading', { name: 'Prepare documents for this role?' })).toBeVisible();
  await expect(page.getByText('Full application evaluation', { exact: true })).toBeVisible();
  const approvalResponse = page.waitForResponse((response) => response.url().endsWith('/decision') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Prepare application documents', exact: true }).click();
  const accepted = await (await approvalResponse).json() as RunAccepted;
  expect(accepted.application_id).toBeTruthy();
  expect(accepted.preparation_run_id).toBeTruthy();
  await waitForRun(page.request, origin, accepted.preparation_run_id!, 'succeeded');
  const applicationUrl = `${origin}/api/v1/applications/${accepted.application_id}`;
  await expect(page.getByRole('heading', { name: role, exact: true, level: 1 })).toBeVisible();
  await expect(page.locator('.page-header').getByText('Drafted · not sent', { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Readable saved draft' })).toBeVisible();
  await expect(page.locator('.document-content-panel')).toBeHidden();
  await expect(page.getByRole('img', { name: /PDF page/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'Checks', exact: true }).click();
  const checks = page.getByRole('region', { name: 'Application checks and corrections' });
  await expect(checks.getByText('Integration-fixture PDF page count and text', { exact: true })).toHaveCount(2);
  await expect(checks.getByText('Integration-fixture actual rendered-page inspection', { exact: true })).toHaveCount(2);
  await page.getByRole('button', { name: 'CV', exact: true }).click();
  await page.getByRole('button', { name: 'PDF preview', exact: true }).click();
  await expect(page.getByRole('img', { name: /PDF page/ })).toHaveCount(2);
  await expect.poll(() => page.locator('canvas').first().evaluate((canvas: HTMLCanvasElement) => {
    const context = canvas.getContext('2d')!;
    return canvas.width > 100 && context.getImageData(0, 0, canvas.width, canvas.height).data.some((channel, index) => index % 4 !== 3 && channel < 150);
  })).toBe(true);
  await page.getByRole('button', { name: 'Cover letter', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Readable saved draft' })).toBeVisible();
  await page.getByRole('button', { name: 'PDF preview', exact: true }).click();
  await expect(page.getByRole('img', { name: /PDF page/ })).toHaveCount(1);
  await page.getByRole('button', { name: 'CV', exact: true }).click();
  await page.getByRole('button', { name: 'Edit content', exact: true }).click();
  await expect(page.getByRole('img', { name: /PDF page/ })).toHaveCount(2);
  await page.getByLabel('Supporting bullet 1', { exact: true }).fill(changedBullet);
  await expect(page.getByText('PDF is out of date', { exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: /PDF page/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Resume checks' })).toBeDisabled();
  const saveResponse = page.waitForResponse((response) =>
    response.url().endsWith('/documents/cv') && response.request().method() === 'PATCH');
  await page.getByRole('button', { name: 'Save document edits' }).click();
  expect((await saveResponse).status()).toBe(200);
  await expect(page.getByRole('button', { name: 'Save document edits' })).toBeDisabled();
  await expect.poll(async () => (await getJson<Application>(page.request, applicationUrl)).documents.cv!.stale).toBe(true);
  const compileResponse = page.waitForResponse((response) => response.url().endsWith('/document-runs'));
  await page.getByRole('button', { name: 'Resume checks', exact: true }).click();
  const compileRun = await (await compileResponse).json() as RunAccepted;
  await waitForRun(page.request, origin, compileRun.run_id, 'succeeded');
  await expect(checks).toBeVisible();
  await page.getByRole('button', { name: 'CV', exact: true }).click();
  await expect(page.getByRole('img', { name: /PDF page/ })).toHaveCount(2);
  await expect(page.getByText('PDF is out of date', { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('region', { name: 'Readable saved draft' }).getByText(changedBullet, { exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: /PDF page/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'Edit content', exact: true }).click();
  await expect(page.getByLabel('Supporting bullet 1', { exact: true })).toHaveValue(changedBullet);
  await expect(page.getByRole('img', { name: /PDF page/ })).toHaveCount(2);
  let application = await getJson<Application>(page.request, applicationUrl);
  expect(application.status).toBe('drafted');
  expect(application.applied_on).toBeNull();
  const beforeSubmit = await readFile(join(workspace, 'job_search_tracker.csv'), 'utf8');
  expect(beforeSubmit).toContain('drafted');
  await page.getByLabel('Submission notes (optional)', { exact: true }).fill('Synthetic submission recorded by the integration journey');
  await page.getByLabel('I submitted the application myself and want to record it as Applied.').check();
  await page.getByRole('button', { name: 'Record actual submission', exact: true }).click();
  await expect(page.locator('.page-header').getByText('Applied', { exact: true })).toBeVisible();
  application = await getJson<Application>(page.request, applicationUrl);
  expect(application.status).toBe('applied');
  expect(application.applied_on).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  await page.getByLabel('New status', { exact: true }).selectOption('interview');
  await page.getByLabel('Stage (optional)', { exact: true }).fill('Synthetic screening');
  await page.getByLabel('What happened? (optional)', { exact: true }).fill('Fixture invitation received; no real employer was contacted');
  await page.getByRole('button', { name: 'Record progress', exact: true }).click();
  await expect(page.locator('.page-header').getByText('Interview', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.locator('.page-header').getByText('Interview', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Synthetic screening', exact: true })).toBeVisible();
  application = await getJson<Application>(page.request, applicationUrl);
  expect(application.timeline.map((entry) => entry.status)).toEqual(['applied', 'interview']);
  const archive = join(workspace, application.archive_dir);
  expect(await readFile(join(workspace, 'job_search_tracker.csv'), 'utf8')).toContain('interview');
  expect(await readFile(join(archive, 'outcome.md'), 'utf8')).toContain('Synthetic screening');
  expect(await readFile(join(archive, 'job_posting.md'), 'utf8')).toContain('SYNTHETIC INTEGRATION POSTING');
  const manifest = JSON.parse(await readFile(join(archive, 'application.json'), 'utf8')) as { submitted_revisions: unknown[] };
  expect(manifest.submitted_revisions).toHaveLength(1);
  const dashboard = await getJson<{ counts: { submitted: number; interview: number }; pending_approvals: number }>(page.request, `${origin}/api/v1/dashboard`);
  expect(dashboard.counts).toMatchObject({ submitted: 1, interview: 1 });
  expect(dashboard.pending_approvals).toBe(0);
  const runs = await getJson<ApiPage<Run>>(page.request, `${origin}/api/v1/runs`);
  expect(runs.total).toBe(5);
  expect(runs.items.every((run) => run.status === 'succeeded')).toBe(true);
  const report = JSON.parse(await readFile(join(directory, 'fixture-report.json'), 'utf8')) as {
    disclaimer: string; claims: { id: string; owner: string; attempt: number }[];
    events: { run_id: string; type: string }[]; ai_calls: { schema: string }[];
  };
  expect(report.disclaimer).toContain('no real LaTeX');
  expect(report.claims).toHaveLength(5);
  expect(report.claims.every((claim) => claim.owner === 'frontend-integration-fixture' && claim.attempt === 1)).toBe(true);
  for (const run of runs.items) {
    const events = report.events.filter((event) => event.run_id === run.id).map((event) => event.type);
    expect(events).toEqual(expect.arrayContaining(['run.queued', 'run.started', 'run.completed']));
  }
  expect(report.ai_calls.map((call) => call.schema)).toEqual(expect.arrayContaining(['CompanyResearch', 'Evaluation', 'DraftPair', 'Review', 'GroundingReview', 'VisualReview']));
  const keys = new Set<string>();
  for (const request of mutations) {
    const actual = await request.allHeaders();
    expect(actual['idempotency-key']).toMatch(/^[a-f0-9-]{36}$/);
    expect(keys.has(actual['idempotency-key'])).toBe(false);
    keys.add(actual['idempotency-key']);
    expect(actual.origin).toBe(origin);
    if (!request.url().endsWith('/session/bootstrap')) {
      expect(actual['x-csrf-token']).toBe(session.csrf_token);
      expect(actual.cookie).toContain(`${cookie!.name}=`);
    }
  }
  expect(requests.filter((request) => request.url().endsWith('/session/bootstrap'))).toHaveLength(1);
  expect(requests.filter((request) => request.url().endsWith('/session') && request.method() === 'GET').length).toBeGreaterThanOrEqual(3);
  expect(requests.every((request) => request.url().startsWith(`${origin}/`) || request.url().startsWith('blob:'))).toBe(true);
  expect(await page.evaluate(() => [Object.keys(localStorage), Object.keys(sessionStorage)])).toEqual([[], []]);
  expect(errors).toEqual([]);
});
