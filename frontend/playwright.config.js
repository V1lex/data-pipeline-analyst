import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e',
  workers: 1,
  timeout: 45000,
  outputDir: './test-results',
  reporter: 'list',
  use: {
    baseURL: process.env.FRONTEND_E2E_URL || 'http://127.0.0.1:5174',
    channel: process.env.PLAYWRIGHT_CHANNEL || 'chromium',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    reducedMotion: 'reduce',
    screenshot: 'only-on-failure',
  },
});
