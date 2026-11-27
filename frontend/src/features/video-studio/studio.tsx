'use client';

import {useMutation, useQueryClient} from '@tanstack/react-query';
import {ArrowLeft, Film, ImagePlus, X} from 'lucide-react';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import {useRouter, useSearchParams} from 'next/navigation';
import {useRef, useState} from 'react';
import {useStore} from 'zustand';
import type {WorkspaceApiError} from '@/api/client';
import {
  createSpec, enqueue, getGetJobQueryKey, getGetSpecQueryKey, updateSpec,
  useGetConcept, useGetSpec, useProductionHandoff,
  type BrandRecord, type CreateVideoSpec, type ProductionHandoff, type VideoSpecRecord,
} from '@/api/generated/workspace';
import {EmptyState, LoadingCards} from '@/components/shared/feedback';
import {Dialog, Field, JobBadge, Problem} from '@/components/shared/workflow';
import {Button} from '@/components/ui/button';
import {workspaceHref} from '@/features/brands/labels';
import {captionLines} from '@/features/creatives/concept';
import {MediaLibrary} from '@/features/media/library';
import {isUuid} from '@/lib/workflow';
import {createStudioStore, sameMedia} from './store';

const Preview = dynamic(() => import('./preview').then(module => module.CompositionPreview), {
  ssr: false, loading: () => <span role="status">Loading player…</span>,
});

export function VideoStudio({brand, conceptId}: {brand: BrandRecord; conceptId: string}) {
  const params = useSearchParams();
  const rawSpec = params.get('spec') ?? '';
  const specId = isUuid(rawSpec) ? rawSpec : '';
  const concept = useGetConcept(conceptId);
  const ready = concept.data?.state === 'APPROVED' && !!concept.data.content.on_screen_copy;
  const handoff = useProductionHandoff(conceptId, {query: {enabled: ready}});
  const own = handoff.data?.brand_id === brand.id;
  const spec = useGetSpec<VideoSpecRecord, WorkspaceApiError>(specId, {query: {enabled: own && !!specId, refetchOnWindowFocus: false}});
  const existing = spec.data?.payload?.brand_id === brand.id && spec.data.concept_id === conceptId ? spec.data : undefined;
  const validSetup = existing?.payload.concept_revision === handoff.data?.concept_revision ? existing : undefined;
  const back = concept.data ? workspaceHref(brand.id, `campaigns/${concept.data.campaign_id}/ideas/${conceptId}`)
    : workspaceHref(brand.id, 'campaigns');

  if (own && handoff.data && (!specId || !spec.isPending)) return <StudioEditor key={`${conceptId}/${handoff.data.concept_revision}`}
    handoff={handoff.data} initial={validSetup} back={back} setupProblem={
      spec.error || (specId && !validSetup ? new Error('This saved setup uses another concept or revision. Save a new setup for this idea.') : null)
    } reload={() => window.location.reload()} />;

  return <main className="studio-page">
    <header className="studio-header"><Link className="studio-back" href={back}><ArrowLeft aria-hidden="true" size={19} /> Back to idea</Link></header>
    <div className="studio-gate workflow-stack">
      {(concept.isPending || (ready && handoff.isPending) || (own && !!specId && spec.isPending)) &&
        <LoadingCards label="Loading video studio" />}
      <Problem error={concept.error || handoff.error} />
      {!!handoff.data && !own && <EmptyState title="This concept belongs to a different brand" />}
      {concept.data && !ready && <EmptyState title="Approve the final captions first">
        Edit the on-screen copy and approve the current concept revision to open the studio.
      </EmptyState>}
    </div>
  </main>;
}

function StudioEditor({handoff, initial, back, setupProblem, reload}: {
  handoff: ProductionHandoff; initial?: VideoSpecRecord; back: string;
  setupProblem: Error | null; reload: () => void;
}) {
  const router = useRouter();
  const cache = useQueryClient();
  const [store] = useState(() => createStudioStore(initial?.payload));
  const draft = useStore(store);
  const [saved, setSaved] = useState(initial);
  const [highlight, setHighlight] = useState<'captions' | 'media' | 'brand'>('captions');
  const [picker, setPicker] = useState(false);
  const [notice, setNotice] = useState('');
  const busy = useRef(false);
  const brandName = handoff.review.approved_brand_label ?? null;
  const approvedCta = handoff.concept.cta.length <= 60 ? handoff.concept.cta : null;
  const duration = handoff.brief.duration_seconds ?? 15;
  const caption = captionLines(handoff.concept.on_screen_copy);
  const selected = draft.template === 'TEXT_OVER_BROLL' ? !!draft.background : draft.photos.length > 0;
  const options = {
    style: draft.style, fit: draft.fit,
    audio: (draft.template === 'TEXT_OVER_BROLL' ? draft.audio : 'SILENT') as 'SILENT' | 'BACKGROUND',
    brand_label: draft.showBrand ? brandName : null,
    cta_overlay: draft.showCta ? approvedCta : null,
  };

  async function saveSpec() {
    let next: VideoSpecRecord;
    if (saved && sameMedia(draft, saved.payload)) {
      if (sameAppearance(saved.payload, options)) {
        setNotice('Video setup is saved.');
        return saved;
      }
      next = await updateSpec(saved.id, {
        expected_revision: saved.revision, changes: options, confirm_media_use: draft.rights,
      });
    } else {
      const request: CreateVideoSpec = {
        ...options, template: draft.template, expected_concept_revision: handoff.concept_revision,
        confirm_media_use: draft.rights,
        background_asset_id: draft.template === 'TEXT_OVER_BROLL' ? draft.background?.id : null,
        product_asset_ids: draft.template === 'PRODUCT_REVEAL' ? draft.photos.map(photo => photo.id) : [],
      };
      next = await createSpec(handoff.concept_id, request);
    }
    setSaved(next);
    cache.setQueryData(getGetSpecQueryKey(next.id), next);
    const url = new URL(window.location.href);
    url.searchParams.set('spec', next.id);
    window.history.replaceState(null, '', url.pathname + url.search);
    setNotice('Video setup is saved.');
    return next;
  }

  const work = useMutation({
    mutationFn: async (mode: 'save' | 'render') => {
      const spec = await saveSpec();
      if (mode === 'save') return;
      const job = await enqueue(spec.id, {expected_revision: spec.revision});
      cache.setQueryData(getGetJobQueryKey(job.id), job);
      void cache.invalidateQueries({queryKey: [`/v1/brands/${handoff.brand_id}/renders`]});
      router.push(workspaceHref(handoff.brand_id, `videos/${job.id}`));
    },
    onSettled: () => {busy.current = false;},
  });
  function start(mode: 'save' | 'render') {
    if (busy.current || !selected || !draft.rights) return;
    setNotice('');
    busy.current = true;
    work.mutate(mode);
  }

  const hasPreview = saved && sameMedia(draft, saved.payload);
  return <main className="studio-page">
    <header className="studio-header">
      <Link href={back} className="studio-back"><ArrowLeft aria-hidden="true" size={19} /> {handoff.concept.title}</Link>
      <span className="studio-meta text-muted-foreground">
        {saved ? `Version ${saved.revision}` : 'Draft'} · {duration}s{handoff.brief.target_platform ? ' · ' + handoff.brief.target_platform : ''}
      </span>
      <Button disabled={work.isPending || !selected || !draft.rights} onClick={() => start('render')}>
        {work.isPending && work.variables === 'render' ? 'Starting render…' : 'Generate video'}
      </Button>
    </header>
    <div className="studio-grid">
      <section className="studio-panel" aria-label="Content">
        <h2>Content</h2>
        <div className="studio-tabs" aria-label="Studio content">
          {(['captions', 'media', 'brand'] as const).map(name => <button type="button" key={name} aria-pressed={highlight === name}
            onClick={() => setHighlight(name)}>{name}</button>)}
        </div>
        {highlight === 'captions' && <>
          {caption.map(row => <div className="record-card studio-caption" key={row.label}><small>{row.label}</small><p>{row.text}</p></div>)}
          <Link className="text-link" href={back}>Edit and reapprove captions</Link>
        </>}
        {highlight === 'media' && <>
          <Field label="Template"><select value={draft.template} onChange={event => draft.setTemplate(event.target.value as typeof draft.template)}>
            <option value="TEXT_OVER_BROLL">Text over background video</option>
            <option value="PRODUCT_REVEAL">Product photo reveal</option>
          </select></Field>
          {draft.template === 'TEXT_OVER_BROLL' && draft.background && <SelectedAsset label={draft.background.title} />}
          {draft.template === 'PRODUCT_REVEAL' && <>
            {draft.photos.map(photo => <SelectedAsset key={photo.id} label={photo.title} remove={() => draft.removePhoto(photo.id)} />)}
            <small className="text-muted-foreground">Photos appear in the order selected.</small>
          </>}
        </>}
        {highlight === 'brand' && <>
          <label className="workflow-check"><input type="checkbox" checked={draft.showBrand} disabled={!brandName}
            onChange={event => draft.setAppearance({showBrand: event.target.checked})} /> Show approved brand label</label>
          <small className="text-muted-foreground">{brandName || 'Reapprove the concept to use a short brand label.'}</small>
          <label className="workflow-check"><input type="checkbox" checked={draft.showCta} disabled={!approvedCta}
            onChange={event => draft.setAppearance({showCta: event.target.checked})} /> Show approved call to action</label>
          <small className="text-muted-foreground">{approvedCta || 'An approved CTA of up to 60 characters can be displayed.'}</small>
        </>}
        {highlight !== 'brand' && <Button variant="outline" onClick={() => setPicker(true)}
          disabled={draft.template === 'PRODUCT_REVEAL' && draft.photos.length >= 5}>
          {draft.template === 'TEXT_OVER_BROLL' ? draft.background ? 'Change background' : 'Select background' : 'Select product photos'}
        </Button>}
      </section>
      <section className="studio-canvas" aria-label="Video preview">
        <small className="studio-canvas-title">Preview / 9:16</small>
        <div className="studio-player">
          {hasPreview ? <Preview spec={saved} options={options} /> : <div className="studio-preview-empty">
            <Film aria-hidden="true" />
            <p>{selected ? 'Save the setup to preview your selected media.' : 'Select media to prepare the video.'}</p>
          </div>}
        </div>
        {hasPreview && <div className="caption-segments">
          {caption.map(row => <span key={row.label}>{row.label}</span>)}
        </div>}
        {hasPreview && <small className="text-center">Composition preview · Download the final MP4 after rendering</small>}
      </section>
      <section className="studio-panel" aria-label="Appearance">
        <h2>Appearance</h2>
        <Field label="Caption position"><select value={draft.style.position} onChange={event => draft.setAppearance({
          style: {...draft.style, position: event.target.value as typeof draft.style.position},
        })}><option value="TOP">Top</option><option value="CENTER">Center</option><option value="BOTTOM">Bottom</option></select></Field>
        <Field label={`Font size / ${draft.style.font_size}`}><input type="range" min={44} max={76} value={draft.style.font_size}
          onChange={event => draft.setAppearance({style: {...draft.style, font_size: Number(event.target.value)}})} /></Field>
        <Field label="Text color"><input type="color" value={draft.style.color}
          onChange={event => draft.setAppearance({style: {...draft.style, color: event.target.value}})} /></Field>
        <Field label="Backdrop"><input type="color" value={draft.style.backdrop}
          onChange={event => draft.setAppearance({style: {...draft.style, backdrop: event.target.value}})} /></Field>
        <Field label="Background fit"><select value={draft.fit} onChange={event => draft.setAppearance({
          fit: event.target.value as typeof draft.fit,
        })}><option value="cover">Cover</option><option value="contain">Contain</option></select></Field>
        {draft.template === 'TEXT_OVER_BROLL' && <Field label="Audio"><select value={draft.audio} onChange={event => draft.setAppearance({
          audio: event.target.value as typeof draft.audio,
        })}><option value="SILENT">Silent</option><option value="BACKGROUND">Source audio</option></select></Field>}
        <h3>Output</h3><p className="text-sm text-muted-foreground">1080 × 1920 · 30 FPS · MP4 · {duration}s</p>
        <label className="workflow-check"><input type="checkbox" checked={draft.rights}
          onChange={event => draft.setRights(event.target.checked)} /> Confirm media{draft.audio === 'BACKGROUND' ? ' and audio' : ''} rights</label>
        <Button variant="outline" disabled={work.isPending || !selected || !draft.rights} onClick={() => start('save')}>
          {work.isPending && work.variables === 'save' ? 'Saving…' : 'Save video setup'}
        </Button>
        {notice && <p className="text-sm text-muted-foreground" role="status">{notice}</p>}
        {saved && <JobBadge status="DRAFT" />}
        <Problem error={work.error || (!saved && setupProblem)} reload={reload} />
      </section>
    </div>
    {picker && <Dialog title={draft.template === 'TEXT_OVER_BROLL' ? 'Select background' : 'Select product photos'}
      close={() => setPicker(false)} wide>
      <MediaLibrary brandId={handoff.brand_id} picker={{
        purpose: draft.template === 'TEXT_OVER_BROLL' ? 'BACKGROUND' : 'PRODUCT',
        minimumSeconds: draft.template === 'TEXT_OVER_BROLL' ? duration : undefined,
        choose: asset => {draft.choose(asset); setHighlight('media'); setPicker(false);},
      }} />
    </Dialog>}
  </main>;
}

function SelectedAsset({label, remove}: {label: string; remove?: () => void}) {
  return <div className="record-card selected-asset"><ImagePlus size={17} aria-hidden="true" />
    <span>{label}</span>
    {remove && <Button size="icon-sm" variant="ghost" aria-label={`Remove ${label}`} onClick={remove}><X aria-hidden="true" /></Button>}
  </div>;
}

function sameAppearance(plan: VideoSpecRecord['payload'], options: {
  style: NonNullable<typeof plan.style>; fit: string; audio: string; brand_label: string | null; cta_overlay: string | null;
}) {
  const style = plan.style;
  return plan.fit === options.fit && plan.audio === options.audio && plan.brand_label === options.brand_label
    && plan.cta_overlay === options.cta_overlay && !!style
    && style.position === options.style.position && style.font_size === options.style.font_size
    && style.color === options.style.color && style.backdrop === options.style.backdrop
    && style.safe_margin === options.style.safe_margin;
}
