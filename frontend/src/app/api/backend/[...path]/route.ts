import {NextRequest, NextResponse} from 'next/server';
import {backendOrigin, workflowRequest} from '@/lib/server/backend';
import {isLocalRequest, localConfig, sessionCookie, validLocalSession} from '@/lib/server/session';

const cache = 'private, no-store';
const mediaTypes = new Set(['image/jpeg', 'image/png', 'image/webp', 'video/mp4', 'video/webm']);

function error(message: string, status: number, code: string) {
  return NextResponse.json({error: {message, code}}, {
    status, headers: {'Cache-Control': cache},
  });
}

async function forward(
  method: 'GET' | 'POST' | 'PATCH', request: NextRequest, {params}: {params: Promise<{path: string[]}>},
) {
  const config = localConfig();
  if (!config || !isLocalRequest(config, request.headers) ||
      !validLocalSession(config, request.cookies.get(sessionCookie)?.value)) {
    return error('Sign in to your local workspace again.', 401, 'SESSION_REQUIRED');
  }
  const writing = method !== 'GET';
  if (writing && !isLocalRequest(config, request.headers, true)) {
    return error('Open Adjacent AI on its configured local address.', 403, 'WORKSPACE_ORIGIN_INVALID');
  }
  const {path} = await params;
  const upstream = workflowRequest(method, path, request.nextUrl.searchParams);
  if (!upstream) return error('This resource is unavailable in the workspace.', 404, 'ROUTE_NOT_AVAILABLE');

  const headers: Record<string, string> = {Authorization: `Bearer ${process.env.ADJACENT_API_TOKEN}`};
  let body: RequestInit['body'];
  if (writing) {
    const type = request.headers.get('content-type')?.split(';', 1)[0];
    const expected = upstream.upload ? 'application/octet-stream' : 'application/json';
    if (type !== expected || !request.body) {
      return error('This action needs a valid request body.', 415, 'REQUEST_TYPE_INVALID');
    }
    // FastAPI enforces the configured upload limit while consuming the stream.
    headers['Content-Type'] = expected;
    body = request.body;
  }
  const range = request.headers.get('range');
  if (range && upstream.media) headers.Range = range;

  const deadline = new AbortController();
  const timer = setTimeout(() => deadline.abort(), upstream.upload ? 240_000 : 45_000);
  let response: Response;
  try {
    // Clear the header deadline after fetch resolves. Large media stays seekable.
    const options: RequestInit & {duplex?: 'half'} = {
      method, headers, cache: 'no-store', redirect: 'manual',
      signal: AbortSignal.any([request.signal, deadline.signal]),
      ...(body ? {body, duplex: 'half' as const} : {}),
    };
    response = await fetch(new URL(upstream.path, backendOrigin()), options);
  } catch {
    return error('Could not reach the Adjacent AI backend.', 502, 'BACKEND_UNAVAILABLE');
  } finally {
    clearTimeout(timer);
  }
  if (response.status === 401) {
    const payload = await response.clone().json().catch(() => null);
    if (payload?.error?.code === 'UNAUTHORIZED') {
      return error('FastAPI rejected the server’s workspace credential.', 502, 'BACKEND_AUTH_FAILED');
    }
  }
  if (response.status >= 300 && response.status < 400) {
    return error('The backend returned an unexpected redirect.', 502, 'BACKEND_REDIRECT');
  }
  const resultHeaders = new Headers({'Cache-Control': cache});
  if (response.status === 204) return new NextResponse(null, {status: 204, headers: resultHeaders});
  if (response.status === 416 && upstream.media) {
    const contentRange = response.headers.get('content-range');
    if (contentRange) resultHeaders.set('Content-Range', contentRange);
    return new NextResponse(null, {status: 416, headers: resultHeaders});
  }
  const contentType = response.headers.get('content-type')?.split(';', 1)[0];
  if (!contentType || (contentType !== 'application/json' && (!upstream.media || !mediaTypes.has(contentType)))) {
    return error('The backend returned an unexpected response.', 502, 'BACKEND_RESPONSE_INVALID');
  }
  resultHeaders.set('Content-Type', contentType);
  if (upstream.media && response.ok) {
    for (const header of ['Accept-Ranges', 'Content-Range', 'Content-Disposition', 'Content-Length']) {
      const value = response.headers.get(header);
      if (value) resultHeaders.set(header, value);
    }
  }
  return new NextResponse(response.body, {status: response.status, headers: resultHeaders});
}

type Path = {params: Promise<{path: string[]}>};
export const GET = (request: NextRequest, path: Path) => forward('GET', request, path);
export const POST = (request: NextRequest, path: Path) => forward('POST', request, path);
export const PATCH = (request: NextRequest, path: Path) => forward('PATCH', request, path);
