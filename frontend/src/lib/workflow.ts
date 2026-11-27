export const isActive = (status?: string) => status === 'PENDING' || status === 'RUNNING';
export const pollJob = (status?: string) => isActive(status) ? 2000 : false;

export const prettyStatus = (status: string) => ({
  PENDING: 'Queued', RUNNING: 'Running', SUCCEEDED: 'Succeeded',
  FAILED: 'Failed', PARTIAL: 'Partial results', CONFIRMED: 'Confirmed',
  INSUFFICIENT_DATA: 'Needs more information', INSUFFICIENT_EVIDENCE: 'Insufficient evidence',
}[status] ?? status.toLowerCase().replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase()));

export function safeWebUrl(value: unknown) {
  if (typeof value !== 'string') return undefined;
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : undefined;
  } catch {
    return undefined;
  }
}

export function dateLabel(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
  });
}

export const splitLines = (value: string) => value.split('\n').map(line => line.trim()).filter(Boolean);
export const nullable = (value?: string) => value?.trim() || null;
export const isUuid = (value?: string) => !!value && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value);

export function byPointer(root: unknown, pointer: string) {
  return pointer.split('/').slice(1).reduce<unknown>((value, key) => (
    value && typeof value === 'object'
      ? (value as Record<string, unknown>)[key.replaceAll('~1', '/').replaceAll('~0', '~')]
      : undefined
  ), root);
}
