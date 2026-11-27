import {NextRequest, NextResponse} from 'next/server';
import {backendOrigin, dashboardPath} from '@/lib/server/backend';
import {isLocalRequest, localConfig, sessionCookie, validLocalSession} from '@/lib/server/session';

function error(message: string, status: number, code: string) {
  return NextResponse.json({error: {message, code}}, {
    status,
    headers: {'Cache-Control': 'private, no-store'},
  });
}

export async function GET(request: NextRequest, {params}: {params: Promise<{path: string[]}>}) {
  const config = localConfig();
  if (!config || !isLocalRequest(config, request.headers) ||
      !validLocalSession(config, request.cookies.get(sessionCookie)?.value)) {
    return error('Sign in to your local workspace again.', 401, 'SESSION_REQUIRED');
  }
  const {path} = await params;
  const upstream = dashboardPath(path, request.nextUrl.searchParams);
  if (!upstream) return error('This resource is unavailable in the workspace.', 404, 'ROUTE_NOT_AVAILABLE');

  let response: Response;
  try {
    response = await fetch(new URL(upstream, backendOrigin()), {
      headers: {Authorization: `Bearer ${process.env.ADJACENT_API_TOKEN}`},
      cache: 'no-store',
      redirect: 'manual',
      signal: AbortSignal.any([request.signal, AbortSignal.timeout(15000)]),
    });
  } catch {
    return error('Could not reach the Adjacent AI backend.', 502, 'BACKEND_UNAVAILABLE');
  }
  if (response.status === 401 || response.status === 403) {
    return error('FastAPI rejected the server’s workspace credential.', 502, 'BACKEND_AUTH_FAILED');
  }
  if (response.status >= 300 && response.status < 400) {
    return error('The backend returned an unexpected redirect.', 502, 'BACKEND_REDIRECT');
  }
  const contentType = response.headers.get('content-type')?.split(';', 1)[0];
  if (contentType !== 'application/json' && contentType !== 'image/jpeg') {
    return error('The backend returned an unexpected response.', 502, 'BACKEND_RESPONSE_INVALID');
  }
  return new NextResponse(response.body, {
    status: response.status,
    headers: {'Content-Type': contentType, 'Cache-Control': 'private, no-store'},
  });
}
