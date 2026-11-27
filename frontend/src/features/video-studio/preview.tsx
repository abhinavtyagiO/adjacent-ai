'use client';

import {Player} from '@remotion/player';
import {useMemo} from 'react';
import {ProductReveal} from '../../../../video-renderer/src/templates/ProductReveal';
import {TextOverBroll} from '../../../../video-renderer/src/templates/TextOverBroll';
import type {RenderProps} from '../../../../video-renderer/src/types';
import type {VideoSpecRecord} from '@/api/generated/workspace';

export function CompositionPreview({spec, options}: {
  spec: VideoSpecRecord;
  options: Pick<RenderProps, 'style' | 'fit' | 'audio' | 'brand_label' | 'cta_overlay'>;
}) {
  const {id, payload: plan} = spec;
  const props = useMemo<RenderProps>(() => {
    const asset = (assetId: string) => `/api/backend/v1/video-specs/${id}/media/${assetId}`;
    return {
      schema_version: 1, template_version: 1, template: plan.template,
      width: 1080, height: 1920, fps: 30, duration_frames: plan.duration_frames,
      ...options, captions: plan.captions,
      background_file: plan.background ? asset(plan.background.asset_id) : null,
      product_files: (plan.products ?? []).map(row => ({
        file: asset(row.asset.asset_id), start_frame: row.start_frame, end_frame: row.end_frame,
      })),
    };
  }, [id, options, plan]);

  return <Player
    component={plan.template === 'TEXT_OVER_BROLL' ? TextOverBroll : ProductReveal}
    inputProps={props} compositionWidth={props.width} compositionHeight={props.height}
    durationInFrames={props.duration_frames} fps={props.fps} controls
    style={{width: '100%'}}
  />;
}
