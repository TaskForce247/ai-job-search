import { expect, test } from '@playwright/test';

test('serves local PDF fonts, CMaps, and WASM in the separate frontend', async ({ request }) => {
  for (const path of [
    '/pdfjs/cmaps/Adobe-Japan1-UCS2.bcmap',
    '/pdfjs/standard_fonts/LiberationSans-Regular.ttf',
    '/pdfjs/wasm/openjpeg.wasm',
  ]) {
    const response = await request.get(path);
    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).not.toContain('text/html');
    expect((await response.body()).length).toBeGreaterThan(100);
  }
});
