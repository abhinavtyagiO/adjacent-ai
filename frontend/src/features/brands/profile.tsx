'use client';

import {useMutation, useQueryClient} from '@tanstack/react-query';
import Link from 'next/link';
import {useEffect, useState} from 'react';
import {WorkspaceApiError} from '@/api/client';
import {
  confirmProfile, enrichMarketing, getGetBrandQueryKey, getGetProfileQueryKey,
  useGetCatalog, useGetMarketingEnrichment, useGetProfile, useListSources,
  type BrandRecord, type ExtractionRecord, type ProfileResponse,
} from '@/api/generated/workspace';
import {LoadingCards, LoadError} from '@/components/shared/feedback';
import {PageHead} from '@/components/shared/page-head';
import {Dialog, JobBadge, ListValues, Pages, Panel, Problem, SourceLink} from '@/components/shared/workflow';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {byPointer, dateLabel, isActive, pollJob, prettyStatus} from '@/lib/workflow';
import {brandName, workspaceHref} from './labels';
import {ResearchActions} from './onboarding';
import {ProfileEditor, type EditPanel} from './profile-editor';

type ProfilePanel = EditPanel | 'catalog' | 'claims' | 'sources';

export function BrandProfile({brand}: {brand: BrandRecord}) {
  const query = useGetProfile<ProfileResponse, WorkspaceApiError>(brand.id, {
    query: {refetchInterval: result => result.state.data?.stages?.marketing === 'RUNNING' ? 3000 : false},
  });
  const profile = query.data;
  return <>
    <PageHead title={profile?.profile.brand_identity.name || brandName(brand)} section="Brand"
      subtitle="Review the information Adjacent discovered. Extracted claims remain provisional."
      action={profile && <Button asChild><Link href={workspaceHref(brand.id, 'campaigns/new')}>Create campaign</Link></Button>} />
    <div className="page-content workflow-stack">
      {query.isPending && <LoadingCards label="Loading brand profile" />}
      {query.error?.code === 'PROFILE_NOT_FOUND'
        ? <Panel title="Your brand profile is waiting for research"><ResearchActions brand={brand} /></Panel>
        : query.error && <LoadError error={query.error} retry={() => void query.refetch()} />}
      {profile && <ProfileBody brand={brand} profile={profile} reload={() => void query.refetch()} />}
    </div>
  </>;
}

function ProfileBody({brand, profile, reload}: {brand: BrandRecord; profile: ProfileResponse; reload: () => void}) {
  const query = useQueryClient();
  const [panel, setPanel] = useState<ProfilePanel>();
  const data = profile.profile;
  const title = data.brand_identity.name || brandName(brand);
  const audience = data.customer_personas_and_audience_segments;
  const segments = (audience?.segments?.length ?? 0) + (audience?.personas?.length ?? 0);
  const colors = data.visual_identity?.colors?.filter(color => /^#(?:[0-9a-f]{6}|[0-9a-f]{3})$/i.test(color.hex ?? '')) ?? [];

  function update(saved: ProfileResponse) {
    query.setQueryData(getGetProfileQueryKey(brand.id), saved);
    void query.invalidateQueries({queryKey: ['workspace', 'brands']});
    void query.invalidateQueries({queryKey: getGetBrandQueryKey(brand.id)});
    setPanel(undefined);
  }
  const confirm = useMutation({
    mutationFn: () => confirmProfile(brand.id, {expected_revision: profile.revision}),
    onSuccess: update,
  });
  const editing = panel === 'summary' || panel === 'positioning' || panel === 'voice'
    || panel === 'psychology' || panel === 'audience' ? panel : undefined;

  return <>
    <div className="workflow-columns">
      <Panel>
        <Badge variant="secondary">{profile.status === 'CONFIRMED' ? 'Confirmed profile' : 'Draft profile'}</Badge>
        <h2>{data.brand_identity.tagline || data.business_positioning?.positioning_statement || title}</h2>
        <p className="text-muted-foreground">{data.brand_identity.description || 'Add a description for this brand.'}</p>
        <Button variant="outline" onClick={() => setPanel('summary')}>Edit brand summary</Button>
      </Panel>
      <Panel title="What makes you different">
        <ListValues items={data.business_positioning?.differentiators ?? []} empty="No differentiators saved yet." />
        <Button variant="outline" onClick={() => setPanel('positioning')}>Edit differentiators</Button>
      </Panel>
    </div>
    <div className="record-grid">
      <ProfileTile title="Products and catalog"
        description={`${Number(profile.catalog?.total_discovered_products ?? 0)} discovered products · ${data.offerings?.length ?? 0} offerings`}
        open={() => setPanel('catalog')} />
      <ProfileTile title="Audience" description={`${segments} saved audience entries`} open={() => setPanel('audience')} />
      <ProfileTile title="Brand voice" description={data.brand_voice?.tone?.join(', ') || 'No voice guidance saved'} open={() => setPanel('voice')} />
    </div>
    <Panel title="Review before advertising">
      <p className="text-muted-foreground">Review product claims, source material, and usage permissions before using them in an ad.</p>
      <div className="workflow-actions">
        <Button variant="outline" onClick={() => setPanel('claims')}>Review claims</Button>
        <Button variant="outline" onClick={() => setPanel('sources')}>View sources</Button>
        {profile.status !== 'CONFIRMED' && <Button disabled={confirm.isPending || !data.brand_identity.name}
          onClick={() => confirm.mutate()}>Confirm profile</Button>}
      </div>
      {!data.brand_identity.name && <small className="text-muted-foreground">Add a brand name before confirming.</small>}
      <Problem error={confirm.error} reload={reload} />
    </Panel>
    <div className="workflow-columns">
      <Panel title="Customer psychology">
        <ListValues items={[
          ...(data.customer_psychology?.pain_points ?? []),
          ...(data.customer_psychology?.motivations ?? []),
          ...(data.customer_psychology?.objections ?? []),
        ]} empty="No motivations or concerns saved yet." />
        {!!profile.customer_concerns?.length && <details>
          <summary>Customer concerns from saved sources</summary>
          <ListValues items={profile.customer_concerns.map(item => String(item.concern || item.label || item.theme || '') || null)} />
        </details>}
        <Button variant="outline" onClick={() => setPanel('psychology')}>Edit customer psychology</Button>
      </Panel>
      <Panel title="Research status">
        <div className="workflow-actions">
          {Object.entries(profile.stages ?? {}).map(([stage, status]) =>
            <span className="workflow-actions" key={stage}>{prettyStatus(stage)} <JobBadge status={String(status)} /></span>)}
        </div>
        <ResearchActions brand={brand} hasProfile />
        <MarketingStatus profile={profile} />
      </Panel>
    </div>
    <details className="workspace-card workflow-details">
      <summary>Other profile details</summary>
      <div className="workflow-stack">
        {colors.length > 0 && <div className="workflow-actions" aria-label="Saved brand colors">
          {colors.map((color, index) => <span className="color-value" key={index}>
            <i style={{backgroundColor: color.hex ?? undefined}} aria-hidden="true" />
            {color.name || color.hex}
          </span>)}
        </div>}
        {Object.entries(data).map(([key, value]) => <details key={key}>
          <summary>{prettyStatus(key)}</summary>
          <ProfileDetail value={value} />
        </details>)}
        <ListValues items={profile.warnings} />
      </div>
    </details>
    {editing && <ProfileEditor key={editing} panel={editing} profile={profile} close={() => setPanel(undefined)}
      saved={update} reload={() => {setPanel(undefined); reload();}} />}
    {panel === 'catalog' && <Catalog profile={profile} close={() => setPanel(undefined)} />}
    {panel === 'claims' && <Claims profile={profile} close={() => setPanel(undefined)} />}
    {panel === 'sources' && <Sources brandId={brand.id} close={() => setPanel(undefined)} />}
  </>;
}

function ProfileTile({title, description, open}: {title: string; description: string; open: () => void}) {
  return <button type="button" className="record-card profile-tile" onClick={open}>
    <strong>{title}</strong><small>{description}</small>
  </button>;
}

function ProfileDetail({value}: {value: unknown}): React.ReactNode {
  if (value === null || value === undefined || value === '') return <span className="text-muted-foreground">Unknown</span>;
  if (Array.isArray(value)) {
    return value.length ? <ul className="workflow-list">{value.map((entry, index) => <li key={index}><ProfileDetail value={entry} /></li>)}</ul>
      : <span className="text-muted-foreground">No entries</span>;
  }
  if (typeof value === 'object') return <dl className="profile-fields">
    {Object.entries(value).map(([name, entry]) => <div key={name}><dt>{prettyStatus(name)}</dt><dd><ProfileDetail value={entry} /></dd></div>)}
  </dl>;
  return <span>{String(value)}</span>;
}

function MarketingStatus({profile}: {profile: ProfileResponse}) {
  const cache = useQueryClient();
  const [selected, setSelected] = useState('');
  const saved = profile.marketing_enrichment?.job_id;
  const jobId = selected || (typeof saved === 'string' ? saved : '');
  const job = useGetMarketingEnrichment<ExtractionRecord, WorkspaceApiError>(jobId, {
    query: {enabled: !!jobId, refetchInterval: result => pollJob(result.state.data?.status)},
  });
  const status = job.data?.status;
  const brandId = profile.brand_id;
  useEffect(() => {
    if (status && !isActive(status)) void cache.invalidateQueries({queryKey: getGetProfileQueryKey(brandId)});
  }, [brandId, cache, status]);
  const start = useMutation({
    mutationFn: () => enrichMarketing(brandId, {expected_revision: profile.revision}),
    onSuccess: result => setSelected(result.id),
  });
  return <>
    <div className="workflow-actions">
      <Button variant="outline" disabled={start.isPending || isActive(status) || profile.stages?.marketing === 'RUNNING'}
        onClick={() => start.mutate()}>Generate audience insights</Button>
      {status && <JobBadge status={status} />}
    </div>
    <Problem error={start.error || job.error || job.data?.error?.message} />
  </>;
}

function Catalog({profile, close}: {profile: ProfileResponse; close: () => void}) {
  const [offset, setOffset] = useState(0);
  const [category, setCategory] = useState('');
  const limit = 12;
  const query = useGetCatalog(profile.brand_id, {limit, offset, category: category || undefined});
  return <Dialog title="Products and catalog" close={close} wide>
    <div className="workflow-stack">
      {!!profile.profile.offerings?.length && <details>
        <summary>Profile offerings ({profile.profile.offerings.length})</summary>
        <ListValues items={profile.profile.offerings.map(row => row.name)} />
      </details>}
      {!!query.data?.available_categories?.length && <label className="workflow-control">Category
        <select value={category} onChange={event => {setCategory(event.target.value); setOffset(0);}}>
          <option value="">All categories</option>
          {query.data.available_categories.map(value => <option value={value} key={value}>{value}</option>)}
        </select>
      </label>}
      {query.isPending && <LoadingCards label="Loading catalog products" />}
      <Problem error={query.error} />
      {query.data?.items.length === 0 && <p className="text-muted-foreground">No discovered products in this view.</p>}
      <ul className="review-list">{query.data?.items.map(row => <li key={row.id}>
        <SourceLink url={row.url}>{row.name}</SourceLink>
        <small>{[row.categories?.join(' · '), row.price ? [row.price, row.currency].filter(Boolean).join(' ') : ''].filter(Boolean).join(' · ')}</small>
      </li>)}</ul>
      <Pages offset={offset} limit={limit} more={offset + limit < (query.data?.total ?? 0)} change={setOffset} />
    </div>
  </Dialog>;
}
function Claims({profile, close}: {profile: ProfileResponse; close: () => void}) {
  return <Dialog title="Review claims" close={close} wide>
    {!profile.claim_review?.length && <p className="text-muted-foreground">No claims were flagged in this profile.</p>}
    <ul className="review-list">{profile.claim_review?.map((claim, index) => {
      const path = String(claim.path ?? '');
      const value = byPointer(profile.profile, path);
      const citations = profile.provenance.find(entry => entry.path === path)?.citations ?? [];
      return <li key={index}>
        <strong>{typeof value === 'string' ? value : path}</strong>
        <small>{[prettyStatus(String(claim.status ?? 'REQUIRES_REVIEW')), ...(Array.isArray(claim.reasons) ? claim.reasons.map(value => prettyStatus(String(value))) : [])].join(' · ')}</small>
        {citations.map((source, index) => <SourceLink key={index} url={source.source_url}>{source.quote}</SourceLink>)}
      </li>;
    })}</ul>
  </Dialog>;
}

function Sources({brandId, close}: {brandId: string; close: () => void}) {
  const sources = useListSources(brandId);
  return <Dialog title="Saved website sources" close={close} wide>
    {sources.isPending && <LoadingCards label="Loading saved sources" />}
    <Problem error={sources.error} />
    {sources.data?.items.length === 0 && <p className="text-muted-foreground">No website sources available.</p>}
    <ul className="review-list">{sources.data?.items.map(row => <li key={row.id}>
      <SourceLink url={row.url}>{row.title || row.url}</SourceLink>
      <small>{dateLabel(row.fetched_at)}</small>
    </li>)}</ul>
  </Dialog>;
}
