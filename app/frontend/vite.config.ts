import { defineConfig } from 'vitest/config';
import { cpSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';

const target = process.env.AI_JOB_SEARCH_API_URL ?? 'http://127.0.0.1:8765';
const targetUrl = new URL(target);
if (!['127.0.0.1', 'localhost', '[::1]'].includes(targetUrl.hostname) || !['http:', 'https:'].includes(targetUrl.protocol)) {
  throw new Error('The development API proxy must target a loopback HTTP service.');
}

export default defineConfig({
  plugins: [react(), {
    name: 'local-pdfjs-resources',
    configResolved() {
      for (const directory of ['cmaps', 'standard_fonts', 'wasm']) {
        cpSync(
          fileURLToPath(new URL(`node_modules/pdfjs-dist/${directory}`, import.meta.url)),
          fileURLToPath(new URL(`public/pdfjs/${directory}`, import.meta.url)),
          { recursive: true },
        );
      }
    },
  }],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target,
        changeOrigin: true,
        configure(proxy) {
          proxy.on('proxyReq', (outgoing, request) => {
            const origin = request.headers.origin;
            if (!origin) return;
            try {
              const parsed = new URL(origin);
              if (parsed.host === request.headers.host && ['127.0.0.1', 'localhost', '[::1]'].includes(parsed.hostname)) {
                outgoing.setHeader('Origin', targetUrl.origin);
              }
            } catch { /* Invalid or foreign origins remain unchanged for the backend to reject. */ }
          });
        },
      },
    },
  },
  build: { target: 'es2022' },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test/setup.ts'],
    clearMocks: true,
    restoreMocks: true,
    css: false,
  },
});
