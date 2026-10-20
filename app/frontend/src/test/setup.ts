import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';
import { resetSessionForTests } from '../api';

Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
  configurable: true, value(this: HTMLDialogElement) { this.open = true; },
});
Object.defineProperty(HTMLDialogElement.prototype, 'close', {
  configurable: true, value(this: HTMLDialogElement) { this.open = false; },
});
Object.defineProperty(window, 'scrollTo', { configurable: true, value: vi.fn() });

afterEach(() => {
  cleanup();
  resetSessionForTests();
  vi.unstubAllGlobals();
  window.history.replaceState(null, '', '/');
});
