import type {BrandRecord, CampaignRecord, RenderRecord} from '@/api/generated/workspace';

export function brandName(brand: Pick<BrandRecord, 'name' | 'website_url'>) {
  if (brand.name) return brand.name;
  try {
    return new URL(brand.website_url).hostname;
  } catch {
    return brand.website_url;
  }
}

export function briefName({brief}: CampaignRecord) {
  return [brief.objective, brief.audience || brief.concern].filter(Boolean).join(' · ');
}

export const renderStatus: Record<RenderRecord['status'], string> = {
  SUCCEEDED: 'Ready',
  RUNNING: 'Rendering',
  PENDING: 'Queued',
  FAILED: 'Failed',
};

export function profileStatus(brand: BrandRecord) {
  if (brand.profile_status === 'CONFIRMED') return 'Confirmed profile';
  if (brand.profile_status === 'DRAFT') return 'Draft profile';
  return 'Research needed';
}

export function workspaceHref(brandId: string, section?: string) {
  return `/workspace/${encodeURIComponent(brandId)}${section ? '/' + section : ''}`;
}

export function renderPreview(video: RenderRecord) {
  const path = `/v1/renders/${video.id}/preview`;
  return video.preview_url === path ? `/api/backend${path}` : undefined;
}
