import {chromium, defineConfig, devices} from '@playwright/test';

export const fixturePassphrase = 'local-fixture-passphrase';
export const fixtureBackendToken = 'playwright-fixture-backend-token';
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
const baseURL = 'http://127.0.0.1:3016';

export default defineConfig({
  testDir: './tests',
  testMatch: '*.spec.ts',
  fullyParallel: false,
  workers: 1,
  use: {
    ...devices['Desktop Chrome'],
    viewport: {width: 1440, height: 900},
    baseURL,
    launchOptions: executablePath ? {executablePath} : {},
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'node scripts/test-backend.mjs',
      url: 'http://127.0.0.1:8016/health/ready',
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        API_ACCESS_TOKEN: fixtureBackendToken,
        RENDER_BROWSER_EXECUTABLE: executablePath ?? chromium.executablePath(),
      },
    },
    {
      command: 'npm run start -- --port 3016',
      url: baseURL + '/sign-in',
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        ADJACENT_LOCAL_AUTH: 'true',
        ADJACENT_LOCAL_ORIGIN: baseURL,
        ADJACENT_LOCAL_PASSPHRASE: fixturePassphrase,
        ADJACENT_SESSION_SECRET: 'browser-fixture-session-secret-32-plus',
        ADJACENT_API_TOKEN: fixtureBackendToken,
        ADJACENT_API_URL: 'http://127.0.0.1:8016',
      },
    },
  ],
});
