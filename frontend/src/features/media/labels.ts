import type {MediaRecord} from '@/api/generated/workspace';
import {prettyStatus} from '@/lib/workflow';

export function assetTitle(asset: MediaRecord) {
  return asset.details.description || asset.details.tags.join(', ') || prettyStatus(asset.purpose);
}
