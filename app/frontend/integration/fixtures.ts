import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test as base } from '@playwright/test';
import type { TestInfo } from '@playwright/test';

export interface IntegrationLaunch {
  url: string;
  origin: string;
  workspace_path: string;
  data_directory: string;
  disclaimer: string;
}

const frontend = fileURLToPath(new URL('..', import.meta.url));

async function attachFile(testInfo: TestInfo, name: string, path: string) {
  try {
    await testInfo.attach(name, { body: await readFile(path), contentType: 'application/json' });
  } catch { /* Startup failures may not have produced a report. */ }
}

export const test = base.extend<{ integration: IntegrationLaunch }>({
  integration: async ({}, use, testInfo) => {
    const configured = process.env.AI_JOB_SEARCH_INTEGRATION_ROOT;
    const parent = configured ? resolve(configured) : testInfo.outputPath('backend-data');
    await mkdir(parent, { recursive: true });
    const directory = join(parent, `frontend-real-api-${randomUUID()}`);
    const port = process.env.AI_JOB_SEARCH_INTEGRATION_PORT ?? '0';
    const python = resolve(frontend, '..\\backend\\.venv\\Scripts\\python.exe');
    const child = spawn(python, ['-B', join(frontend, 'integration\\server.py'), '--data-dir', directory, '--port', port], {
      cwd: frontend, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
      env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1', PYTHONUNBUFFERED: '1' },
    });
    let output = '';
    const collect = (chunk: Buffer) => { output = (output + chunk.toString()).slice(-100_000); };
    child.stdout.on('data', collect);
    child.stderr.on('data', collect);
    let ended = false;
    const exited = new Promise<void>((done) => {
      child.on('error', (error) => { output += `\n${error.message}`; ended = true; done(); });
      child.on('exit', () => { ended = true; done(); });
    });
    try {
      const deadline = Date.now() + 30_000;
      let launch: IntegrationLaunch | undefined;
      while (Date.now() < deadline && !ended) {
        try {
          const candidate = JSON.parse(await readFile(join(directory, 'launch.json'), 'utf8')) as IntegrationLaunch;
          const health = await fetch(`${candidate.origin}/healthz`, { signal: AbortSignal.timeout(700) });
          if (health.ok && (await health.json() as { ready?: boolean }).ready) { launch = candidate; break; }
        } catch { /* Wait for the private launch artifact and real listening server together. */ }
        await new Promise((done) => setTimeout(done, 100));
      }
      if (!launch) throw new Error(`The real FastAPI fixture did not become responsive.\n${output}`);
      await use(launch);
    } finally {
      try {
        if (!ended) await writeFile(join(directory, 'shutdown'), 'stop owned integration server\n');
        await Promise.race([exited, new Promise<void>((done) => setTimeout(done, 10_000))]);
        if (!ended) { child.kill(); await exited; }
        await attachFile(testInfo, 'real-api-fixture-report', join(directory, 'fixture-report.json'));
        if (output) await testInfo.attach('real-api-server-log', { body: Buffer.from(output), contentType: 'text/plain' });
      } finally {
        await rm(directory, { recursive: true, force: true, maxRetries: 6, retryDelay: 200 });
      }
    }
  },
});

export { expect } from '@playwright/test';
