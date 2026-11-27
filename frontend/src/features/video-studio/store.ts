import {createStore} from 'zustand/vanilla';
import type {CaptionStyle, MediaRecord, VideoPlan} from '@/api/generated/workspace';
import {assetTitle} from '@/features/media/labels';

export const initialStyle: Required<CaptionStyle> = {
  position: 'BOTTOM', font_size: 66, color: '#FFFFFF', backdrop: '#101010', safe_margin: 96,
};

type Pick = {id: string; title: string};
export type StudioAppearance = {
  style: Required<CaptionStyle>;
  fit: 'cover' | 'contain';
  audio: 'SILENT' | 'BACKGROUND';
  showBrand: boolean;
  showCta: boolean;
};

export type StudioDraft = StudioAppearance & {
  template: VideoPlan['template'];
  background?: Pick;
  photos: Pick[];
  rights: boolean;
  setTemplate: (value: VideoPlan['template']) => void;
  choose: (asset: MediaRecord) => void;
  removePhoto: (id: string) => void;
  setAppearance: (changes: Partial<StudioAppearance>) => void;
  setRights: (value: boolean) => void;
};

export function createStudioStore(plan?: VideoPlan) {
  return createStore<StudioDraft>()(set => ({
    template: plan?.template ?? 'TEXT_OVER_BROLL',
    background: plan?.background ? {id: plan.background.asset_id, title: 'Saved background video'} : undefined,
    photos: plan?.products?.map((row, index) => ({id: row.asset.asset_id, title: `Product photo ${index + 1}`})) ?? [],
    style: {...initialStyle, ...plan?.style},
    fit: plan?.fit ?? 'cover',
    audio: plan?.audio ?? 'SILENT',
    showBrand: !!plan?.brand_label,
    showCta: !!plan?.cta_overlay,
    rights: false,
    setTemplate: template => set({template, audio: 'SILENT', rights: false}),
    choose: asset => set(draft => {
      const picked = {id: asset.id, title: assetTitle(asset)};
      if (draft.template === 'TEXT_OVER_BROLL') return {background: picked, audio: 'SILENT', rights: false};
      if (draft.photos.length >= 5 || draft.photos.some(photo => photo.id === asset.id)) return draft;
      return {photos: [...draft.photos, picked], rights: false};
    }),
    removePhoto: id => set(draft => ({photos: draft.photos.filter(photo => photo.id !== id), rights: false})),
    setAppearance: changes => set(draft => ({
      ...changes,
      rights: changes.audio && changes.audio !== draft.audio ? false : draft.rights,
    })),
    setRights: rights => set({rights}),
  }));
}

export function sameMedia(draft: StudioDraft, plan: VideoPlan) {
  if (draft.template !== plan.template) return false;
  if (draft.template === 'TEXT_OVER_BROLL') return draft.background?.id === plan.background?.asset_id;
  const products = plan.products ?? [];
  return draft.photos.length === products.length &&
    draft.photos.every((photo, i) => photo.id === products[i].asset.asset_id);
}
