import {expect, test, type Page} from '@playwright/test';
import {fixtureBackendToken, fixturePassphrase} from '../playwright.config';

async function signIn(page: Page) {
  await page.goto('/sign-in');
  await page.getByLabel('Workspace passphrase').fill(fixturePassphrase);
  await page.getByRole('button', {name: 'Sign in', exact: true}).click();
  await expect(page.getByRole('heading', {name: "Let's create something."})).toBeVisible();
}

test('auth gates the dashboard and keeps backend credentials on the server', async ({page, request}, testInfo) => {
  await page.goto('/workspace');
  await expect(page).toHaveURL(/\/sign-in$/);
  await expect(page.getByRole('heading', {name: 'Welcome back'})).toBeVisible();
  expect(await page.content()).not.toContain(fixtureBackendToken);
  await page.screenshot({path: testInfo.outputPath('sign-in.png')});
  expect((await request.get('/api/backend/v1/brands')).status()).toBe(401);
  expect((await request.post('/api/auth/session', {
    headers: {Origin: 'https://external.example'},
    data: {passphrase: fixturePassphrase},
  })).status()).toBe(403);
  await page.getByRole('link', {name: 'New to Adjacent? Connect a workspace'}).click();
  await expect(page.getByRole('heading', {name: 'Connect your workspace'})).toBeVisible();
  await page.getByRole('link', {name: 'Back to sign in'}).click();
  await expect(page.getByRole('heading', {name: 'Welcome back'})).toBeVisible();
  await page.getByLabel('Workspace passphrase').fill('wrong-passphrase');
  await page.getByRole('button', {name: 'Sign in', exact: true}).click();
  await expect(page.getByRole('alert').filter({hasText: 'The workspace passphrase is incorrect.'})).toBeVisible();
});

test('dashboard uses FastAPI records and isolates the active brand on refresh', async ({page}, testInfo) => {
  let writes = 0;
  page.on('request', request => {
    if (request.url().includes('/api/backend/') && request.method() !== 'GET') writes++;
    if (request.url().includes('/api/backend/')) expect(request.headers()).not.toHaveProperty('authorization');
  });
  await signIn(page);
  await expect(page.getByText('Awareness · Desk planners')).toBeVisible();
  await expect(page.getByText('Make space for a fresh page').first()).toBeVisible();
  const image = page.locator('.video-preview img');
  await expect.poll(() => image.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
  expect((await page.context().cookies()).find(cookie => cookie.name === 'adjacent_local_session')?.httpOnly).toBe(true);
  expect((await page.request.get('/api/backend/v1/chatgpt/accounts')).status()).toBe(404);
  await page.screenshot({path: testInfo.outputPath('dashboard-desktop.png'), fullPage: true});
  await page.getByLabel('Switch brand').selectOption({label: 'Green Trail'});
  await expect(page.getByText('Green Trail has a registered website', {exact: false})).toBeVisible();
  await expect(page.getByText('No saved campaigns yet.')).toBeVisible();
  await expect(page.getByText('Awareness · Desk planners')).toHaveCount(0);
  await page.reload();
  await expect(page.getByLabel('Switch brand').locator('option:checked')).toHaveText('Green Trail');
  await expect(page.getByText('No completed videos yet')).toBeVisible();
  expect(writes).toBe(0);
});

test('mobile navigation and sign-out protect the data after reload', async ({page}, testInfo) => {
  await page.setViewportSize({width: 390, height: 844});
  await signIn(page);
  await expect(page.getByText('Awareness · Desk planners')).toBeVisible();
  await page.screenshot({path: testInfo.outputPath('dashboard-mobile.png'), fullPage: true});
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await page.getByRole('button', {name: 'Open navigation'}).click();
  await page.getByRole('link', {name: 'Settings', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'Settings', exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Sign out'}).click();
  await expect(page.getByRole('heading', {name: 'Welcome back'})).toBeVisible();
  await page.goto('/workspace');
  await expect(page).toHaveURL(/\/sign-in$/);
  expect((await page.request.get('/api/backend/v1/brands')).status()).toBe(401);
});
