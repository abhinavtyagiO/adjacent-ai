import 'server-only';

const uuid = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}';
const brand = `/brands/${uuid}`;
const campaign = `/campaigns/${uuid}`;
const concept = `/concepts/${uuid}`;
const spec = `/video-specs/${uuid}`;
const render = `/renders/${uuid}`;
const pagination = ['limit', 'offset'];
const mediaFilters = [
  'q', 'media_type', 'orientation', 'min_width', 'min_height', 'min_duration', 'purpose', 'search_pixabay',
];

type Method = 'GET' | 'POST' | 'PATCH';
type Policy = {path: RegExp; methods: Method[]; query: string[]; upload: boolean; media: boolean};

function allow(path: string, methods: Method[], query: string[] = [], options?: {upload?: boolean; media?: boolean}): Policy {
  return {path: new RegExp(`^/v1${path}$`), methods, query,
    upload: options?.upload ?? false, media: options?.media ?? false};
}

const policies = [
  allow('/brands', ['GET', 'POST'], pagination),
  allow(brand, ['GET']),
  allow(`${brand}/extractions`, ['GET', 'POST'], pagination),
  allow(`/(?:extractions|marketing-enrichments)/${uuid}`, ['GET']),
  allow(`${brand}/profile`, ['GET', 'PATCH']),
  allow(`${brand}/(?:profile/confirm|marketing-enrichments)`, ['POST']),
  allow(`${brand}/(?:offerings|sources)`, ['GET']),
  allow(`${brand}/catalog`, ['GET'], [...pagination, 'category', 'linked_only']),
  allow(`${brand}/campaigns`, ['GET', 'POST'], pagination),
  allow(campaign, ['GET']),
  allow(`${campaign}/brief`, ['PATCH']),
  allow(`${campaign}/generations`, ['GET', 'POST'], pagination),
  allow(`/creative-generations/${uuid}`, ['GET']),
  allow(`${campaign}/research/${uuid}`, ['GET']),
  allow(`${campaign}/(?:concepts|shortlist)`, ['GET'], [...pagination, 'job_id']),
  allow(concept, ['GET', 'PATCH']),
  allow(`${concept}/approve`, ['POST']),
  allow(`${concept}/handoff`, ['GET']),
  allow(`${brand}/media`, ['GET'], pagination),
  allow(`${brand}/media`, ['POST'], ['purpose', 'confirm_rights', 'description', 'tags'], {upload: true}),
  allow(`${brand}/media/search`, ['GET'], mediaFilters),
  allow(`${brand}/media/pixabay`, ['POST']),
  allow(`${brand}/media/${uuid}`, ['GET']),
  allow(`${brand}/media/${uuid}/thumbnail`, ['GET'], [], {media: true}),
  allow(`${concept}/video-specs`, ['POST']),
  allow(spec, ['GET', 'PATCH']),
  allow(`${spec}/media/${uuid}`, ['GET'], [], {media: true}),
  allow(`${spec}/renders`, ['POST']),
  allow(`${brand}/renders`, ['GET'], [...pagination, 'status', 'spec_id']),
  allow(render, ['GET']),
  allow(`${render}/preview`, ['GET'], [], {media: true}),
  allow(`${render}/video`, ['GET'], ['download'], {media: true}),
];

// Match both the HTTP method and the full path before forwarding the server-side bearer token.
export function workflowRequest(method: Method, parts: string[], params: URLSearchParams) {
  const path = '/' + parts.join('/');
  if (parts.some(part => !part || part.includes('/') || part.includes('\\'))) return null;
  const policy = policies.find(rule => rule.methods.includes(method) && rule.path.test(path));
  if (!policy) return null;
  const query = new URLSearchParams();
  for (const key of policy.query) {
    const value = params.get(key);
    if (value !== null) query.set(key, value);
  }
  return {path: query.size ? `${path}?${query}` : path, upload: policy.upload, media: policy.media};
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
