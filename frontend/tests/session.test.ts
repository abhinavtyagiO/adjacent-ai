import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {dashboardPath} from '@/lib/server/backend';
import {isLocalRequest, localConfig, newLocalSession, validLocalSession} from '@/lib/server/session';

vi.mock('server-only', () => ({}));
vi.mock('next/headers', () => ({cookies: vi.fn(), headers: vi.fn()}));

beforeEach(() => {
  vi.stubEnv('ADJACENT_LOCAL_AUTH', 'true');
  vi.stubEnv('ADJACENT_LOCAL_ORIGIN', 'http://127.0.0.1:3000');
  vi.stubEnv('ADJACENT_API_TOKEN', 'fixture-only-backend-token');
  vi.stubEnv('ADJACENT_LOCAL_PASSPHRASE', 'fixture-only-browser-passphrase');
  vi.stubEnv('ADJACENT_SESSION_SECRET', 'fixture-only-session-key-material-32-plus');
});

afterEach(() => vi.unstubAllEnvs());

describe('server-side local access', () => {
  it('is disabled unless enabled with separate credentials and a loopback origin', () => {
    expect(localConfig()).not.toBeNull();
    vi.stubEnv('ADJACENT_LOCAL_ORIGIN', 'https://studio.example.com');
    expect(localConfig()).toBeNull();
    vi.stubEnv('ADJACENT_LOCAL_ORIGIN', 'http://127.0.0.1:3000');
    vi.stubEnv('ADJACENT_LOCAL_PASSPHRASE', process.env.ADJACENT_API_TOKEN);
    expect(localConfig()).toBeNull();
  });

  it('accepts only signed, unexpired cookies for the current config', () => {
    const config = localConfig()!;
    const time = 1_800_000_000_000;
    const cookie = newLocalSession(config, time);
    expect(validLocalSession(config, cookie, time)).toBe(true);
    expect(validLocalSession(config, cookie, time + 8 * 60 * 60 * 1000)).toBe(false);
    expect(validLocalSession(config, cookie.slice(0, -1), time)).toBe(false);
    vi.stubEnv('ADJACENT_API_TOKEN', 'rotated-fixture-only-backend-token');
    expect(validLocalSession(localConfig()!, cookie, time)).toBe(false);
  });

  it('rejects cross-origin session writes', () => {
    const config = localConfig()!;
    expect(isLocalRequest(config, new Headers({host: '127.0.0.1:3000', origin: 'https://other.example'}), true)).toBe(false);
    expect(isLocalRequest(config, new Headers({host: '127.0.0.1:3000', origin: 'http://127.0.0.1:3000'}), true)).toBe(true);
  });

  it('allows only dashboard reads and recognized query parameters', () => {
    const query = new URLSearchParams({limit: '5', unrecognized: 'ignored'});
    expect(dashboardPath(['v1', 'brands'], query)).toBe('/v1/brands?limit=5');
    expect(dashboardPath(['v1', 'chatgpt', 'accounts'], query)).toBeNull();
    expect(dashboardPath(['v1', 'brands', '..', 'renders'], query)).toBeNull();
  });
});
