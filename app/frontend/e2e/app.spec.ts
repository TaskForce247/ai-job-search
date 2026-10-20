import { expect, test } from '@playwright/test';
import type { Page, Route } from '@playwright/test';
import { applicationFixture, documentFixture, jobFixture, profileFixture, runFixture, settingsFixture } from '../src/test/fixtures';
import type { Application, DocumentView, Run } from '../src/types';

function fixturePdf(pages: number) {
  const fontId = 3 + pages * 2;
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    `<< /Type /Pages /Kids [${Array.from({ length: pages }, (_, i) => `${3 + i * 2} 0 R`).join(' ')}] /Count ${pages} >>`,
  ];
  for (let i = 0; i < pages; i++) {
    const text = `BT /F1 18 Tf 50 760 Td (Example document - page ${i + 1}) Tj ET`;
    objects.push(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 ${fontId} 0 R >> >> /Contents ${4 + i * 2} 0 R >>`);
    objects.push(`<< /Length ${text.length} >>\nstream\n${text}\nendstream`);
  }
  objects.push('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>');
  let pdf = '%PDF-1.4\n';
  const offsets = [0];
  for (let i = 0; i < objects.length; i++) {
    offsets.push(Buffer.byteLength(pdf));
    pdf += `${i + 1} 0 obj\n${objects[i]}\nendobj\n`;
  }
  const xref = Buffer.byteLength(pdf);
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  pdf += offsets.slice(1).map((offset) => `${String(offset).padStart(10, '0')} 00000 n \n`).join('');
  return Buffer.from(`${pdf}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
}

async function installApi(page: Page, onboardingComplete = true) {
  let application: Application = structuredClone(applicationFixture);
  const settings = structuredClone(settingsFixture);
  settings.settings.onboarding_complete = onboardingComplete;
  let document: DocumentView = { ...structuredClone(documentFixture), stale: false, pdf_artifact_id: 'pdf-initial' };
  application.documents = { cv: document, cover_letter: { ...document, pdf_artifact_id: 'cover-pdf' } };
  let runs: Run[] = [];
  const intentKeys = new Set<string>();
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  async function respond(route: Route, data: unknown, status = 200) {
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
  }
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/v1', '');
    const method = request.method();
    if (method !== 'GET') {
      expect(request.headers()['x-csrf-token']).toBe('e2e-csrf');
      const key = request.headers()['idempotency-key'];
      expect(key).toMatch(/^[a-f0-9-]{36}$/);
      expect(intentKeys.has(key)).toBe(false);
      intentKeys.add(key);
    }
    const body = method !== 'GET' && request.headers()['content-type']?.includes('application/json') ? request.postDataJSON() as Record<string, unknown> : {};
    if (path === '/session') return respond(route, { csrf_token: 'e2e-csrf' });
    if (path === '/workspace') return respond(route, { path: 'C:\\Private\\JobSearch', initialized: true, revision: 'workspace-1', public_repository_warning: false });
    if (path === '/settings') {
      if (method === 'PATCH') {
        Object.assign(settings.settings, body.settings); settings.revision += '-saved';
      }
      return respond(route, settings);
    }
    if (path === '/profile') return respond(route, profileFixture);
    if (path === '/system/readiness') return respond(route, {
      dependencies: [{ id: 'pdf', label: 'PDF tools', status: 'ready', description: 'Local compilation is available.' }],
      ai_connected: true, privacy_consent: true, active_runs: 0, version: 'e2e', workspace_path: 'C:\\Private\\JobSearch',
    });
    if (path === '/portals') return respond(route, { items: [{
      id: 'freehire-search', label: 'Freehire', enabled: true, installed_enabled: true, effective_enabled: true,
      supported: true, warning: '', health: 'ready', revision: 'portal-1',
    }] });
    if (path === '/dashboard') return respond(route, {
      counts: { drafted: application.status === 'drafted' ? 1 : 0, submitted: application.applied_on ? 1 : 0, active: 1, interview: 0, offer: 0, hired: 0, closed: 0 },
      funnel: [{ stage: 'submitted', count: application.applied_on ? 1 : 0 }], deadlines: [],
      recent_applications: [application], pending_approvals: runs.flatMap((run) => run.approvals).filter((approval) => approval.status === 'pending').length,
    });
    if (path === '/jobs') return respond(route, { items: [jobFixture], total: 1, next_cursor: null });
    if (path === '/jobs/job-1') return respond(route, jobFixture);
    if (path === '/runs') return respond(route, { items: runs, total: runs.length, next_cursor: null });
    if (path.startsWith('/runs/')) return respond(route, runs.find((run) => run.id === path.split('/')[2]) ?? runFixture);
    if (path === '/applications') return respond(route, { items: [application], total: 1, next_cursor: null });
    if (path === '/applications/application-1') return respond(route, application);
    if (path === '/applications/application-1/documents/cv') {
      if (method === 'PATCH') {
        expect(body.expected_revision).toBe(document.revision);
        document = { ...document, content: body.content as DocumentView['content'], revision: 'document-edited', stale: true };
        application = { ...application, revision: 'app-edited', documents: { ...application.documents, cv: document } };
      }
      return respond(route, document);
    }
    if (path === '/applications/application-1/documents/cover_letter') return respond(route, application.documents.cover_letter);
    if (path.startsWith('/artifacts/')) return route.fulfill({ contentType: 'application/pdf', body: fixturePdf(path.includes('cover') ? 1 : 2) });
    if (path === '/search-runs' || path === '/ranking-runs') {
      if (path === '/search-runs') expect(body.limit).toBe(20);
      if (path === '/ranking-runs') expect(body).toMatchObject({ job_ids: ['job-1'], limit: 10, rerank: false });
      const run: Run = { ...runFixture, id: `${path}-run`, status: 'succeeded', approvals: [], kind: path.slice(1), message: 'Results are available.' };
      runs = [run, ...runs]; return respond(route, { run_id: run.id }, 202);
    }
    if (path === '/evaluation-runs') {
      expect(body.job_id).toBe('job-1');
      const run: Run = {
        ...runFixture, id: 'evaluation-run', kind: 'evaluation', stage: 'fit_review', status: 'awaiting_user',
        approvals: [{
          id: 'evaluate-approval', run_id: 'evaluation-run', kind: 'evaluation', title: 'Prepare for Example Co?',
          revision: 'evaluation-revision', status: 'pending', choices: ['prepare', 'not_now'],
          proposal: { score: 82, verdict: 'Good fit', evaluation: {
            scores: { technical: 85, experience: 80, behavioral: 80, career: 85 },
            language: { verdict: 'PASS', note: 'Confirmed English.' }, location: { verdict: 'PASS' }, eligibility: { verdict: 'PASS' },
            strengths: ['Confirmed technical experience.'], gaps: ['Domain knowledge to develop.'], recommendation: 'Proceed for your review.',
          } },
        }],
      };
      runs = [run, ...runs]; return respond(route, { run_id: run.id }, 202);
    }
    if (path === '/approvals/evaluate-approval/decision') {
      expect(body).toEqual({ decision: 'prepare', expected_revision: 'evaluation-revision', confirmation: '' });
      runs = runs.map((run) => ({ ...run, status: 'succeeded', approvals: [] }));
      runs = [{ ...runFixture, id: 'preparation-run', kind: 'document_preparation', stage: 'published', status: 'succeeded',
        approvals: [], input: { application_id: application.id }, message: 'Drafts prepared and checked; not sent.' }, ...runs];
      application.last_run_id = 'preparation-run';
      return respond(route, { run_id: 'evaluation-run', preparation_run_id: 'preparation-run', application_id: application.id }, 202);
    }
    if (path === '/applications/application-1/document-runs') {
      expect(body.expected_revision).toBe(application.revision);
      document = { ...document, stale: false, pdf_artifact_id: 'pdf-recompiled' };
      application = { ...application, last_run_id: 'compile-run', documents: { ...application.documents, cv: document } };
      runs = [{ ...runFixture, id: 'compile-run', status: 'succeeded', stage: 'verified', message: 'Compilation and checks complete.', approvals: [] }, ...runs];
      return respond(route, { run_id: 'compile-run' }, 202);
    }
    if (path === '/applications/application-1/submissions') {
      expect(body.expected_revision).toBe(application.revision);
      application = { ...application, applied_on: String(body.submitted_on), status: 'applied', revision: 'app-submitted' };
      return respond(route, application);
    }
    return respond(route, { error: { code: 'FIXTURE_ROUTE_MISSING', message: `${method} ${path} was not expected.` } }, 404);
  });
  return { errors, intentKeys };
}

test('guided first run and responsive navigation do not require AI commands or an account', async ({ page }, testInfo) => {
  const { errors } = await installApi(page, false);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Your next chapter starts here.' })).toBeVisible();
  await page.getByRole('button', { name: 'Continue to AI & privacy' }).click();
  await expect(page.getByText('API usage is billed separately.')).toBeVisible();
  await page.getByRole('button', { name: 'Continue to your profile' }).click();
  await expect(page.getByRole('button', { name: 'Import & review facts' })).toBeVisible();
  await page.getByRole('button', { name: 'Continue to search preferences' }).click();
  await page.getByLabel('Default job search').fill('Software developer');
  await page.getByLabel('Search location').fill('Remote');
  await page.getByRole('button', { name: 'Finish setup & open dashboard' }).click();
  await expect(page.getByRole('heading', { name: 'Your search, at a glance.' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('mobile-overview.png') });
  expect(errors).toEqual([]);
});

test('search, evaluate, approve, edit, locally preview and record a real submission', async ({ page }, testInfo) => {
  const { errors, intentKeys } = await installApi(page);
  await page.goto('/jobs');
  await page.getByLabel('Role or keywords').fill('Software developer');
  await page.getByRole('button', { name: 'Find jobs', exact: true }).click();
  await expect(page.getByText(/Search queued for up to 20 results/)).toBeVisible();
  await page.getByLabel('Select Software developer for ranking').check();
  await page.getByRole('button', { name: 'Rank selected', exact: true }).click();
  await expect(page.getByText(/Ranking queued for 1 selected jobs/)).toBeVisible();
  await page.getByRole('link', { name: 'Software developer', exact: true }).click();
  await expect(page.getByText('<script>alert("untrusted")</script>', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Check this job' }).click();
  await page.getByRole('button', { name: 'Evaluate fit' }).click();
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: /^Activity/ }).click();
  await expect(page.getByRole('heading', { name: 'Prepare for Example Co?' })).toBeVisible();
  await page.getByRole('button', { name: 'Prepare application documents' }).click();
  await expect(page.getByRole('heading', { name: 'Software developer', exact: true })).toBeVisible();
  await expect(page.getByRole('img', { name: /PDF page/ })).toHaveCount(2);
  await expect.poll(async () => page.locator('canvas').first().evaluate((canvas: HTMLCanvasElement) => canvas.width)).toBeGreaterThan(100);
  await page.getByLabel('Body text', { exact: true }).first().fill('Updated paragraph grounded in confirmed experience.');
  await expect(page.getByText('PDF is out of date')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Compile & check documents' })).toBeDisabled();
  await page.getByRole('button', { name: 'Save document edits' }).click();
  await expect(page.getByText('Document edits saved.', { exact: false })).toBeVisible();
  await expect(page.getByText('PDF is out of date')).toBeVisible();
  await page.getByRole('button', { name: 'Compile & check documents' }).click();
  await expect(page.getByRole('img', { name: /PDF page/ })).toHaveCount(2);
  await expect(page.getByText('PDF is out of date')).toHaveCount(0);
  const dismiss = page.getByRole('button', { name: 'Dismiss notification' });
  while (await dismiss.count()) await dismiss.first().click();
  await page.locator('.application-documents').screenshot({ path: testInfo.outputPath('document-review.png') });
  await page.getByLabel('I submitted the application myself and want to record it as Applied.').check();
  await page.getByRole('button', { name: 'Record actual submission' }).click();
  await expect(page.getByText('Actual submission recorded.', { exact: false })).toBeVisible();
  await expect(page.locator('.page-header').getByText('Applied', { exact: true })).toBeVisible();
  expect(intentKeys.size).toBe(7);
  expect(errors).toEqual([]);
});
