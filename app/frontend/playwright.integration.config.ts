import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './integration',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  globalTimeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: 'list',
  outputDir: './test-results/real-api',
  use: {
    channel: process.platform === 'win32' ? 'msedge' : undefined,
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
