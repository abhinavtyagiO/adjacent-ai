import {NextRequest, NextResponse} from 'next/server';
import {loginSchema} from '@/features/auth/schema';
import {
  equalSecret, isLocalRequest, localConfig, newLocalSession, sessionCookie, sessionCookieOptions,
} from '@/lib/server/session';

function error(message: string, status: number) {
  return NextResponse.json({message}, {status, headers: {'Cache-Control': 'no-store'}});
}

export async function POST(request: NextRequest) {
  const config = localConfig();
  if (!config) return error('Local workspace sign-in is disabled on this server.', 503);
  if (!isLocalRequest(config, request.headers, true)) return error('Open the configured local workspace address.', 403);
  if (!request.headers.get('content-type')?.startsWith('application/json') ||
      Number(request.headers.get('content-length') ?? 0) > 4096) {
    return error('Enter a valid passphrase.', 400);
  }
  const values = loginSchema.safeParse(await request.json().catch(() => null));
  if (!values.success) return error('Enter a valid passphrase.', 400);
  if (!equalSecret(values.data.passphrase, config.passphrase)) return error('The workspace passphrase is incorrect.', 401);
  const response = new NextResponse(null, {status: 204, headers: {'Cache-Control': 'no-store'}});
  response.cookies.set(sessionCookie, newLocalSession(config), sessionCookieOptions(config));
  return response;
}

export async function DELETE(request: NextRequest) {
  const config = localConfig();
  if (!config || !isLocalRequest(config, request.headers, true)) return error('Open the configured local workspace address.', 403);
  const response = new NextResponse(null, {status: 204, headers: {'Cache-Control': 'no-store'}});
  response.cookies.set(sessionCookie, '', sessionCookieOptions(config, 0));
  return response;
}
