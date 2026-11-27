import {execFileSync} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {mkdir} from 'node:fs/promises';
import {expect, test} from '@playwright/test';
import {fixturePassphrase} from '../playwright.config';

test('a local session can research a brand and render an approved advertisement', async ({page}, testInfo) => {
  test.setTimeout(180_000);
  const sideEffects = {extraction: 0, generation: 0, render: 0};
  const browserErrors: string[] = [];
  page.on('pageerror', error => browserErrors.push(error.message));
  page.on('request', request => {
    if (request.method() !== 'POST' || !request.url().includes('/api/backend/')) return;
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/extractions')) sideEffects.extraction++;
    if (path.endsWith('/generations')) sideEffects.generation++;
    if (path.endsWith('/renders')) sideEffects.render++;
  });

  await page.goto('/sign-in');
  await page.getByLabel('Workspace passphrase').fill(fixturePassphrase);
  await page.getByRole('button', {name: 'Sign in', exact: true}).click();
  await page.getByLabel('Switch brand').selectOption('new');
  await page.getByLabel('Brand website').fill(`https://phase2c-${randomUUID()}.example/`);
  await expect(page.getByRole('heading', {name: 'Tell us about your brand.'})).toBeVisible();
  await page.getByRole('button', {name: 'Analyze my brand'}).click();
  await expect(page).toHaveURL(/\/research\/[a-f0-9-]{36}$/);
  const researchUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(researchUrl);
  await page.getByRole('link', {name: 'Review profile'}).click({timeout: 25_000});
  await expect(page.getByRole('heading', {name: 'Focus Map', level: 1})).toBeVisible();
  await page.getByRole('button', {name: /Products and catalog/}).click();
  await expect(page.getByRole('dialog').getByRole('link', {name: 'Focus Timer'})).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button', {name: 'Edit brand summary'}).click();
  await page.getByLabel('Description').fill('Planning tools for independent study sessions.');
  await page.getByRole('button', {name: 'Save changes'}).click();
  await expect(page.getByText('Planning tools for independent study sessions.').first()).toBeVisible();
  await page.getByRole('button', {name: 'Confirm profile'}).click();
  await expect(page.getByText('Confirmed profile', {exact: true})).toBeVisible();

  await page.getByRole('link', {name: 'Create campaign'}).click();
  await page.getByLabel('Objective').fill('Awareness');
  await page.getByLabel('Audience', {exact: true}).fill('Students');
  await page.getByLabel('Platform (optional)').fill('Instagram Reels');
  await page.getByLabel('Duration (seconds)').fill('5');
  await page.getByRole('button', {name: 'Save campaign'}).click();
  await expect(page.getByText('Your brief is saved', {exact: true})).toBeVisible();
  expect(sideEffects.generation).toBe(0);
  await page.getByRole('button', {name: 'Generate ideas', exact: true}).click();
  await expect(page).toHaveURL(/\?generation=[a-f0-9-]{36}$/);
  const ideasUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(ideasUrl);
  await expect(page.getByText('5 shortlisted')).toBeVisible({timeout: 25_000});
  await page.getByRole('link', {name: /The interrupted paragraph/}).click();
  await page.getByRole('button', {name: 'Edit creative'}).click();
  const newCaption = 'Make room for your next study session.';
  await page.getByLabel('On-screen caption', {exact: true}).fill(newCaption);
  await page.getByRole('button', {name: 'Save changes'}).click();
  await expect(page.getByText(newCaption, {exact: true})).toBeVisible();
  await expect(page.getByRole('button', {name: /Approve/})).toBeDisabled();
  for (const label of [
    'Review final advertising copy',
    'Confirm product claims and assumptions',
    'Acknowledge asset rights requirements',
  ]) {
    await page.getByRole('checkbox', {name: label}).check();
  }
  await page.getByRole('button', {name: /Approve/}).click();
  await page.getByRole('link', {name: 'Open video studio'}).click();
  await expect(page.getByText(newCaption, {exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Select background'}).click();
  await page.getByPlaceholder('Search by keyword').fill('study desk fixture');
  await page.getByRole('button', {name: 'Search Pixabay'}).click();
  await page.getByRole('button', {name: 'Select asset', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Import selected asset'})).toBeDisabled();
  await page.getByRole('checkbox', {name: /I have checked the license/}).check();
  await page.getByRole('button', {name: 'Import selected asset'}).click();
  await expect(page.getByRole('button', {name: 'Change background'})).toBeVisible({timeout: 30_000});

  // Upload a second clip, then choose the saved stock asset again.
  const uploadPath = testInfo.outputPath('own-background.mp4');
  await mkdir(testInfo.outputDir, {recursive: true});
  execFileSync('ffmpeg', [
    '-v', 'error', '-f', 'lavfi', '-i', 'color=c=0x97685b:s=360x640:r=30:d=6',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-threads', '2', uploadPath,
  ]);
  await page.getByRole('button', {name: 'Change background'}).click();
  await page.getByRole('button', {name: 'Upload media'}).click();
  const upload = page.getByRole('dialog', {name: 'Upload media'});
  await upload.getByLabel('File').setInputFiles(uploadPath);
  await upload.getByRole('checkbox', {name: /I have permission/}).check();
  await upload.getByRole('button', {name: 'Upload media'}).click();
  await expect(page.getByText('own-background.mp4', {exact: true})).toBeVisible({timeout: 30_000});
  await page.getByRole('button', {name: 'Change background'}).click();
  const stock = page.getByRole('dialog', {name: 'Select background'}).locator('article')
    .filter({hasText: 'study desk'});
  await stock.getByRole('button', {name: 'Use this asset'}).click();
  await page.getByRole('checkbox', {name: 'Confirm media rights'}).check();
  await page.getByRole('button', {name: 'Save video setup'}).click();
  await expect(page).toHaveURL(/\?spec=[a-f0-9-]{36}$/);
  const preview = page.locator('.studio-player video');
  await expect.poll(() => preview.evaluate((video: HTMLVideoElement) => video.readyState)).toBeGreaterThan(0);
  const source = await preview.evaluate((video: HTMLVideoElement) => video.currentSrc);
  expect(new URL(source).pathname).toMatch(/^\/api\/backend\/v1\/video-specs\/.+\/media\/[^/]+$/);
  const range = await page.request.get(source, {headers: {Range: 'bytes=0-31'}});
  expect(range.status()).toBe(206);
  expect((await range.body()).length).toBe(32);
  await page.getByRole('button', {name: 'Play video', exact: true}).click();
  await expect.poll(() => preview.evaluate((video: HTMLVideoElement) => video.currentTime)).toBeGreaterThan(0.35);
  await page.getByRole('button', {name: 'Pause video', exact: true}).click();
  await page.screenshot({path: testInfo.outputPath('video-studio.png')});

  await page.getByLabel('Caption position').selectOption('TOP');
  expect(sideEffects.render).toBe(0);
  await page.getByRole('button', {name: 'Generate video'}).click();
  await expect(page).toHaveURL(/\/videos\/[a-f0-9-]{36}$/);
  const renderUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(renderUrl);
  await expect(page.getByRole('heading', {name: 'Your video is ready'})).toBeVisible({timeout: 125_000});
  const playback = page.getByLabel('Rendered video');
  await expect(playback).toBeVisible();
  await expect(page.getByText('Technical validation passed', {exact: true})).toBeVisible();
  await expect(page.getByRole('list', {name: 'Saved render versions'})).toContainText('Version 2');

  const downloaded = page.waitForEvent('download');
  await page.getByRole('link', {name: 'Download MP4'}).click();
  const artifact = testInfo.outputPath('focus-map-ad.mp4');
  await (await downloaded).saveAs(artifact);
  const probe = JSON.parse(execFileSync('ffprobe', [
    '-v', 'error', '-show_streams', '-show_format', '-of', 'json', artifact,
  ], {encoding: 'utf8'}));
  expect(probe.streams).toHaveLength(1);
  expect(probe.streams[0]).toMatchObject({codec_name: 'h264', width: 1080, height: 1920, pix_fmt: 'yuv420p', r_frame_rate: '30/1'});
  expect(Number(probe.format.duration)).toBeCloseTo(5);
  expect(sideEffects).toEqual({extraction: 1, generation: 1, render: 1});
  expect(browserErrors).toEqual([]);
  await expect.poll(() => playback.evaluate((video: HTMLVideoElement) => video.readyState)).toBeGreaterThan(0);
  await playback.evaluate(async (video: HTMLVideoElement) => {
    const loaded = new Promise<void>(resolve => video.addEventListener('seeked', () => resolve(), {once: true}));
    video.currentTime = 1;
    await loaded;
  });
  await page.screenshot({path: testInfo.outputPath('rendered-video.png')});
  await page.setViewportSize({width: 390, height: 844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
});
