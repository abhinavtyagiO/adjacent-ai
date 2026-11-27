import 'server-only';

const uuid = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}';
const brandPath = new RegExp(`^/v1/brands/${uuid}/(campaigns|renders)$`);
const previewPath = new RegExp(`^/v1/renders/${uuid}/preview$`);

// Slice 1 exposes only the reads used by the dashboard. Upstream hosts never come from requests.
export function dashboardPath(parts: string[], params: URLSearchParams) {
  const path = '/' + parts.join('/');
  const match = brandPath.exec(path);
  if (path !== '/v1/brands' && !match && !previewPath.test(path)) return null;
  const query = new URLSearchParams();
  if (!previewPath.test(path)) {
    for (const key of ['limit', 'offset', ...(match?.[1] === 'renders' ? ['status'] : [])]) {
      const value = params.get(key);
      if (value !== null) query.set(key, value);
    }
  }
  return query.size ? `${path}?${query}` : path;
}

export function backendOrigin() {
  const value = process.env.ADJACENT_API_URL || 'http://127.0.0.1:8000';
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Invalid API origin');
  }
  return url.origin;
}
