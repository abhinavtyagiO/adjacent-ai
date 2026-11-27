'use client';

import {zodResolver} from '@hookform/resolvers/zod';
import {useMutation, useQueryClient} from '@tanstack/react-query';
import {Film, Image as ImageIcon} from 'lucide-react';
import {useState} from 'react';
import {Controller, useForm} from 'react-hook-form';
import {z} from 'zod';
import {privateMediaUrl} from '@/api/client';
import {
  importStock, upload, useListMedia, useSearchMedia,
  type BrandRecord, type MediaRecord, type SearchMediaParams, type StockRecord,
} from '@/api/generated/workspace';
import {EmptyState, LoadingCards} from '@/components/shared/feedback';
import {PageHead} from '@/components/shared/page-head';
import {Dialog, Field, Pages, Panel, Problem, SourceLink} from '@/components/shared/workflow';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {prettyStatus, safeWebUrl} from '@/lib/workflow';
import {assetTitle} from './labels';

export function MediaPage({brand}: {brand: BrandRecord}) {
  return <>
    <PageHead title="Media library" section="Media library" subtitle="Find saved footage, upload product images or search Pixabay." />
    <div className="page-content"><MediaLibrary brandId={brand.id} /></div>
  </>;
}

type MediaKind = 'VIDEO' | 'IMAGE';
type Purpose = MediaRecord['purpose'];
type Picker = {purpose: 'BACKGROUND' | 'PRODUCT'; minimumSeconds?: number; choose: (asset: MediaRecord) => void};

const stockTitle = (row: StockRecord) => row.tags.slice(0, 3).join(', ') || 'Pixabay asset';
const seconds = (duration?: number | null) => duration == null ? undefined : `${Number(duration.toFixed(1))}s`;

function stockImage(value?: string | null) {
  const url = safeWebUrl(value);
  return url && /^https:\/\/(?:cdn\.|www\.)?pixabay\.com\//.test(url) ? url : undefined;
}

export function MediaLibrary({brandId, picker}: {brandId: string; picker?: Picker}) {
  const [search, setSearch] = useState<SearchMediaParams>();
  const [filter, setFilter] = useState<MediaKind | 'ALL'>(picker ? picker.purpose === 'PRODUCT' ? 'IMAGE' : 'VIDEO' : 'ALL');
  const [portrait, setPortrait] = useState(false);
  const [longer, setLonger] = useState(false);
  const [offset, setOffset] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [stock, setStock] = useState<{asset: StockRecord; searchId: string}>();
  const [details, setDetails] = useState<MediaRecord>();
  const cache = useQueryClient();
  const limit = 24;
  const library = useListMedia(brandId, {limit, offset}, {query: {enabled: !search}});
  const results = useSearchMedia(brandId, search ?? {q: '', media_type: 'VIDEO'}, {
    query: {enabled: !!search, refetchOnWindowFocus: false, retry: false},
  });
  const {register, handleSubmit, formState: {errors}} = useForm<{keywords: string}>({
    resolver: zodResolver(z.object({keywords: z.string().trim().max(100)})),
    defaultValues: {keywords: ''},
  });
  const source = search ? results.data : undefined;
  const internal = source?.source === 'INTERNAL' ? source.items : search ? [] : library.data?.items ?? [];
  const visible = internal.filter(asset =>
    (!picker || asset.purpose === picker.purpose) &&
    (filter === 'ALL' || asset.media_type === filter) &&
    (!portrait || asset.details.orientation === 'PORTRAIT') &&
    (!longer || (asset.details.duration_seconds ?? 0) >= 15)
  );
  const pending = search ? results.isPending : library.isPending;

  function clearSearch() {
    setSearch(undefined);
    setOffset(0);
  }

  function acquired(asset: MediaRecord) {
    setUploading(false);
    setStock(undefined);
    clearSearch();
    void cache.invalidateQueries({queryKey: [`/v1/brands/${brandId}/media`]});
    if (picker && availableForPicker(asset, picker)) picker.choose(asset);
  }

  return <div className="workflow-stack">
    <form className="media-search-form" onSubmit={handleSubmit(({keywords}, event) => {
      const external = (event?.nativeEvent as SubmitEvent | undefined)?.submitter?.getAttribute('value') === 'stock';
      if (!keywords) {
        if (external) return;
        clearSearch();
        return;
      }
      const next: SearchMediaParams = {
        q: keywords,
        media_type: filter === 'ALL' ? 'VIDEO' : filter,
        purpose: picker?.purpose ?? 'BACKGROUND',
        orientation: portrait ? 'PORTRAIT' : undefined,
        min_duration: longer ? Math.max(15, picker?.minimumSeconds ?? 0) : picker?.minimumSeconds,
        search_pixabay: external,
      };
      if (JSON.stringify(search) === JSON.stringify(next)) void results.refetch();
      else setSearch(next);
    })}>
      <Field label="Search media" error={errors.keywords?.message}>
        <Input type="search" maxLength={100} placeholder="Search by keyword" {...register('keywords')} />
      </Field>
      <div className="workflow-actions">
        <Button type="submit" value="library">Search library</Button>
        {picker?.purpose !== 'PRODUCT' && <Button variant="outline" type="submit" value="stock">Search Pixabay</Button>}
        <Button variant="outline" type="button" onClick={() => setUploading(true)}>Upload media</Button>
      </div>
    </form>
    <div className="workflow-actions" aria-label="Media filters">
      {!picker && (['ALL', 'VIDEO', 'IMAGE'] as const).map(value => <button type="button" key={value}
        className="workflow-chip" aria-pressed={filter === value} onClick={() => {
          setFilter(value);
          if (value === 'IMAGE') setLonger(false);
          clearSearch();
        }}>{value === 'ALL' ? 'All assets' : prettyStatus(value)}</button>)}
      <button type="button" className="workflow-chip" aria-pressed={portrait} onClick={() => {setPortrait(!portrait); clearSearch();}}>Portrait</button>
      {filter !== 'IMAGE' && <button type="button" className="workflow-chip" aria-pressed={longer}
        onClick={() => {setLonger(!longer); clearSearch();}}>15s or longer</button>}
      {search && <Button variant="ghost" size="sm" onClick={clearSearch}>Back to saved assets</Button>}
    </div>
    {pending && <LoadingCards label="Loading media" />}
    <Problem error={search ? results.error : library.error} />
    <div className="workflow-tiles">
      {visible.map(asset => <MediaTile key={asset.id} title={assetTitle(asset)} image={privateMediaUrl(asset.thumbnail_url)}
        video={asset.media_type === 'VIDEO'} detail={[
          asset.source_type === 'UPLOAD' ? 'Uploaded' : 'Pixabay', prettyStatus(asset.media_type),
          seconds(asset.details.duration_seconds),
        ].filter(Boolean).join(' · ')}>
        <Button variant="outline" size="sm" disabled={picker && !availableForPicker(asset, picker)}
          onClick={() => picker ? picker.choose(asset) : setDetails(asset)}>
          {picker ? availableForPicker(asset, picker) ? 'Use this asset' : 'Does not fit this video' : 'View details'}
        </Button>
      </MediaTile>)}
      {source?.source === 'PIXABAY' && source.items.map(asset => <MediaTile key={asset.provider_asset_id}
        title={stockTitle(asset)} image={stockImage(asset.preview_url)} video={asset.media_type === 'VIDEO'}
        detail={['Pixabay', asset.creator, seconds(asset.duration_seconds)].filter(Boolean).join(' · ')}>
        <Button variant="outline" size="sm" onClick={() => setStock({asset, searchId: source.search_id})}>Select asset</Button>
      </MediaTile>)}
    </div>
    {!pending && !visible.length && !(source?.source === 'PIXABAY' && source.items.length)
      && !(search ? results.error : library.error) && <EmptyState title={search ? 'No matching media' : 'No media in this view'}>
        {picker?.purpose === 'PRODUCT' ? 'Upload a product photo you have permission to use.' : 'Try a different search or upload an asset.'}
      </EmptyState>}
    {!search && <Pages offset={offset} limit={limit} more={library.data?.items.length === limit} change={setOffset} />}
    {uploading && <UploadDialog brandId={brandId} purpose={picker?.purpose} close={() => setUploading(false)} saved={acquired} />}
    {stock && <ImportDialog brandId={brandId} selection={stock} close={() => setStock(undefined)} saved={acquired} />}
    {details && <Dialog title={assetTitle(details)} close={() => setDetails(undefined)}>
      <div className="workflow-stack">
        <img className="media-detail-image" src={privateMediaUrl(details.thumbnail_url)} alt="" />
        <p>{prettyStatus(details.media_type)} · {details.details.width} × {details.details.height}
          {details.details.duration_seconds ? ' · ' + seconds(details.details.duration_seconds) : ''}</p>
        {details.details.source_url && <SourceLink url={details.details.source_url}>
          Pixabay source{details.details.creator ? ' by ' + details.details.creator : ''}
        </SourceLink>}
      </div>
    </Dialog>}
  </div>;
}

function availableForPicker(asset: MediaRecord, picker: Picker) {
  return asset.status === 'READY' && asset.purpose === picker.purpose && (picker.purpose === 'BACKGROUND'
    ? asset.media_type === 'VIDEO' && (asset.details.duration_seconds ?? 0) + 0.001 >= (picker.minimumSeconds ?? 0)
    : asset.media_type === 'IMAGE' && asset.source_type === 'UPLOAD');
}

function MediaTile({title, detail, image, video, children}: {
  title: string; detail: string; image?: string; video: boolean; children: React.ReactNode;
}) {
  const Icon = video ? Film : ImageIcon;
  return <article className="record-card workflow-tile">
    <div className="media-tile-image">
      <Icon aria-hidden="true" />
      {image && <img src={image} alt="" loading="lazy" referrerPolicy="no-referrer"
        onError={event => {event.currentTarget.hidden = true;}} />}
    </div>
    <strong>{title}</strong><small>{detail}</small>
    <div className="mt-3">{children}</div>
  </article>;
}

const uploadSchema = z.object({
  file: z.file({message: 'Choose a media file.'}).min(1, 'Choose a non-empty file.'),
  purpose: z.enum(['BACKGROUND', 'PRODUCT', 'BRAND']),
  description: z.string().max(500),
  tags: z.string().max(500),
  rights: z.boolean().refine(value => value, 'Confirm that you have permission to use this media.'),
});

function UploadDialog({brandId, purpose, close, saved}: {
  brandId: string; purpose?: Purpose; close: () => void; saved: (asset: MediaRecord) => void;
}) {
  const {register, control, handleSubmit, formState: {errors}} = useForm<z.infer<typeof uploadSchema>>({
    resolver: zodResolver(uploadSchema),
    defaultValues: {purpose: purpose ?? 'BACKGROUND', rights: false, tags: '', description: ''},
  });
  const task = useMutation({
    mutationFn: (data: z.infer<typeof uploadSchema>) => upload(brandId, data.file, {
      purpose: purpose ?? data.purpose, confirm_rights: data.rights,
      description: (data.description || data.file.name).slice(0, 500), tags: data.tags,
    }),
    onSuccess: saved,
  });
  const allowed = purpose === 'PRODUCT' ? '.jpg,.jpeg,.png,.webp'
    : purpose === 'BACKGROUND' ? '.mp4,.mov,.webm' : '.jpg,.jpeg,.png,.webp,.mp4,.mov,.webm';
  return <Dialog title="Upload media" close={close}>
    <form className="workflow-form" onSubmit={handleSubmit(data => task.mutate(data))}>
      <Field label="File" error={errors.file?.message}>
        <Controller control={control} name="file" render={({field}) => (
          <Input type="file" accept={allowed} name={field.name} ref={field.ref} onBlur={field.onBlur}
            onChange={event => field.onChange(event.target.files?.[0] ?? null)} />
        )} />
      </Field>
      {!purpose && <Field label="Use for"><select {...register('purpose')}>
        <option value="BACKGROUND">Background</option><option value="PRODUCT">Product photos</option><option value="BRAND">Brand assets</option>
      </select></Field>}
      <Field label="Description (optional)" error={errors.description?.message}><Input {...register('description')} maxLength={500} /></Field>
      <Field label="Tags (optional)" hint="Separate tags with commas." error={errors.tags?.message}><Input {...register('tags')} maxLength={500} /></Field>
      <label className="workflow-check"><input type="checkbox" {...register('rights')} /> I have permission to use this asset in the campaign.</label>
      <Problem error={errors.rights?.message || task.error} />
      <Button type="submit" disabled={task.isPending}>{task.isPending ? 'Processing upload…' : 'Upload media'}</Button>
    </form>
  </Dialog>;
}

function ImportDialog({brandId, selection, close, saved}: {
  brandId: string; selection: {asset: StockRecord; searchId: string}; close: () => void; saved: (asset: MediaRecord) => void;
}) {
  const [rights, setRights] = useState(false);
  const asset = selection.asset;
  const task = useMutation({
    mutationFn: () => importStock(brandId, {
      search_id: selection.searchId, provider_asset_id: asset.provider_asset_id,
      media_type: asset.media_type, confirm_rights: rights,
    }),
    onSuccess: saved,
  });
  return <Dialog title="Import selected media" close={close}>
    <div className="workflow-stack">
      <Panel>
        <Badge variant="secondary">Pixabay</Badge><strong>{stockTitle(asset)}</strong>
        <p className="text-muted-foreground">{asset.creator || 'Pixabay contributor'} · {asset.width} × {asset.height}</p>
        <div className="workflow-actions"><SourceLink url={asset.source_url}>View on Pixabay</SourceLink>
          <SourceLink url={asset.license_url}>View license</SourceLink></div>
      </Panel>
      <label className="workflow-check"><input type="checkbox" checked={rights} onChange={event => setRights(event.target.checked)} />
        I have checked the license and can use this media in the campaign.
      </label>
      <Problem error={task.error} />
      <Button disabled={!rights || task.isPending} onClick={() => task.mutate()}>
        {task.isPending ? 'Importing…' : 'Import selected asset'}
      </Button>
    </div>
  </Dialog>;
}
