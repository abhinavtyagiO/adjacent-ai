// Orval calls the same-origin gateway. Browser requests carry only the local session cookie.
export async function gatewayFetch<T>(url: string, options: RequestInit = {}): Promise<T> {
  if (!url.startsWith('/v1/')) throw new Error('Invalid workspace API path.');
  const response = await fetch('/api/backend' + url, {
    ...options, credentials: 'same-origin', cache: 'no-store', redirect: 'error',
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = data?.error;
    const fields = error?.fields ?? error?.details?.fields;
    const issues = Array.isArray(fields)
      ? fields.map(field => [Array.isArray(field.path)
        ? field.path.filter((part: string) => part !== 'body').join('.')
        : '', field.message ?? field.type].filter(Boolean).join(' · ')).filter(Boolean)
      : [];
    throw new WorkspaceApiError(
      issues.length ? issues.join(' / ') : error?.message || 'Could not load workspace data.',
      response.status, error?.code,
    );
  }
  return data as T;
}

export class WorkspaceApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly code?: string) {
    super(message);
  }
}

export function privateMediaUrl(path?: string | null) {
  return path?.startsWith('/v1/') ? '/api/backend' + path : undefined;
}
