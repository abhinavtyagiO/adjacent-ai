// Orval calls the same-origin gateway. Browser requests carry only the local session cookie.
export async function gatewayFetch<T>(url: string, options: RequestInit = {}): Promise<T> {
  if (!url.startsWith('/v1/')) throw new Error('Invalid workspace API path.');
  const response = await fetch('/api/backend' + url, {
    ...options, credentials: 'same-origin', cache: 'no-store', redirect: 'error',
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new WorkspaceApiError(data?.error?.message || 'Could not load workspace data.', response.status);
  }
  return data as T;
}

export class WorkspaceApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
  }
}
