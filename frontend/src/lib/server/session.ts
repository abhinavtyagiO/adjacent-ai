import 'server-only';

import {createHash, createHmac, randomBytes, timingSafeEqual} from 'node:crypto';
import {cookies, headers} from 'next/headers';

export const sessionCookie = 'adjacent_local_session';
export const sessionDurationSeconds = 8 * 60 * 60;
const loopbackHosts = new Set(['127.0.0.1', 'localhost', '[::1]']);

type LocalConfig = {
  origin: URL;
  passphrase: string;
  signingKey: Buffer;
};

function digest(value: string) {
  return createHash('sha256').update(value).digest();
}

export function equalSecret(received: string, expected: string) {
  return timingSafeEqual(digest(received), digest(expected));
}

// Development access is opt-in and tied to a canonical loopback origin.
export function localConfig(): LocalConfig | null {
  const {ADJACENT_LOCAL_AUTH, ADJACENT_LOCAL_PASSPHRASE, ADJACENT_SESSION_SECRET, ADJACENT_API_TOKEN} = process.env;
  if (ADJACENT_LOCAL_AUTH !== 'true' || !ADJACENT_API_TOKEN ||
      !ADJACENT_LOCAL_PASSPHRASE || ADJACENT_LOCAL_PASSPHRASE.length < 12 ||
      !ADJACENT_SESSION_SECRET || Buffer.byteLength(ADJACENT_SESSION_SECRET) < 32 ||
      equalSecret(ADJACENT_LOCAL_PASSPHRASE, ADJACENT_API_TOKEN)) {
    return null;
  }
  let origin: URL;
  try {
    origin = new URL(process.env.ADJACENT_LOCAL_ORIGIN ?? 'http://127.0.0.1:3000');
  } catch {
    return null;
  }
  if (!loopbackHosts.has(origin.hostname) || !['http:', 'https:'].includes(origin.protocol) ||
      origin.username || origin.password || origin.pathname !== '/' || origin.search || origin.hash) {
    return null;
  }
  return {
    origin,
    passphrase: ADJACENT_LOCAL_PASSPHRASE,
    signingKey: createHmac('sha256', ADJACENT_SESSION_SECRET)
      .update(JSON.stringify(['adjacent-local-session-v1', ADJACENT_API_TOKEN, ADJACENT_LOCAL_PASSPHRASE]))
      .digest(),
  };
}

function sign(key: Buffer, payload: string) {
  return createHmac('sha256', key).update(payload).digest('hex');
}

export function newLocalSession(config: LocalConfig, now = Date.now()) {
  const expiry = Math.floor(now / 1000) + sessionDurationSeconds;
  const payload = `${expiry}.${randomBytes(16).toString('hex')}`;
  return `${payload}.${sign(config.signingKey, payload)}`;
}

export function validLocalSession(config: LocalConfig, cookie?: string, now = Date.now()) {
  const match = /^(\d{1,12})\.([0-9a-f]{32})\.([0-9a-f]{64})$/.exec(cookie ?? '');
  if (!match) return false;
  const [, expiry, nonce, signature] = match;
  return Number(expiry) > Math.floor(now / 1000) &&
    equalSecret(signature, sign(config.signingKey, `${expiry}.${nonce}`));
}

export function isLocalRequest(config: LocalConfig, requestHeaders: Headers, writes = false) {
  const fetchSite = requestHeaders.get('sec-fetch-site');
  return requestHeaders.get('host') === config.origin.host &&
    (!writes || (requestHeaders.get('origin') === config.origin.origin &&
      (fetchSite === null || fetchSite === 'same-origin')));
}

export async function hasLocalSession() {
  // Read the request even when local auth is disabled so a production build cannot
  // prerender the sign-in state using build-time environment values.
  const [requestHeaders, cookieStore] = await Promise.all([headers(), cookies()]);
  const config = localConfig();
  return !!config && isLocalRequest(config, requestHeaders) &&
    validLocalSession(config, cookieStore.get(sessionCookie)?.value);
}

export function sessionCookieOptions(config: LocalConfig, maxAge = sessionDurationSeconds) {
  return {
    httpOnly: true,
    sameSite: 'strict' as const,
    secure: config.origin.protocol === 'https:',
    path: '/',
    maxAge,
  };
}
