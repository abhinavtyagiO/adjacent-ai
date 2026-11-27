'use client';

import {useMutation, useQueryClient} from '@tanstack/react-query';
import Link from 'next/link';
import {useRouter, useSearchParams} from 'next/navigation';
import {useState} from 'react';
import type {WorkspaceApiError} from '@/api/client';
import {
  generateConcepts, getGetGenerationQueryKey, getListGenerationsQueryKey,
  useGetCampaign, useGetGeneration, useListConcepts, useListGenerations, useShortlist,
  type BrandRecord, type CreativeGeneration,
} from '@/api/generated/workspace';
import {EmptyState, LoadingCards} from '@/components/shared/feedback';
import {PageHead} from '@/components/shared/page-head';
import {JobBadge, Panel, Problem} from '@/components/shared/workflow';
import {Button} from '@/components/ui/button';
import {Badge} from '@/components/ui/badge';
import {brandName, briefName, workspaceHref} from '@/features/brands/labels';
import {dateLabel, isActive, isUuid, pollJob, prettyStatus} from '@/lib/workflow';

export function IdeaGallery({brand, campaignId}: {brand: BrandRecord; campaignId: string}) {
  const router = useRouter();
  const params = useSearchParams();
  const cache = useQueryClient();
  const [showAll, setShowAll] = useState(false);
  const campaign = useGetCampaign(campaignId);
  const owned = campaign.data?.brand_id === brand.id;
  const base = workspaceHref(brand.id, `campaigns/${campaignId}`);
  const generations = useListGenerations(campaignId, {limit: 30}, {query: {enabled: owned, refetchInterval: query => pollJob(query.state.data?.items[0]?.status)}});
  const selected = params.get('generation');
  const jobId = (selected && isUuid(selected) ? selected : generations.data?.items[0]?.id) ?? '';
  const generation = useGetGeneration<CreativeGeneration, WorkspaceApiError>(jobId, {
    query: {enabled: owned && !!jobId, refetchInterval: result => pollJob(result.state.data?.status)},
  });
  const job = generation.data?.campaign_id === campaignId ? generation.data : undefined;
  const active = isActive(job?.status);
  const finished = !!job && !active;
  const shortlist = useShortlist(campaignId, {job_id: jobId}, {query: {enabled: finished}});
  const records = useListConcepts(campaignId, {job_id: jobId, limit: 30}, {query: {enabled: finished}});
  const shortlisted = shortlist.data?.items ?? [];
  const ideas = records.data?.items ?? [];
  const visible = showAll || !shortlisted.length ? ideas : shortlisted;
  const latestStatus = jobId === generations.data?.items[0]?.id ? job?.status : generations.data?.items[0]?.status;
  const start = useMutation({
    mutationFn: () => generateConcepts(campaignId, {expected_revision: campaign.data!.revision}),
    onSuccess: row => {
      cache.setQueryData(getGetGenerationQueryKey(row.id), row);
      void cache.invalidateQueries({queryKey: getListGenerationsQueryKey(campaignId)});
      setShowAll(false);
      router.replace(base + '?generation=' + encodeURIComponent(row.id));
    },
  });

  return <>
    <PageHead title="Explore your creative ideas" section="Campaigns"
      subtitle={owned && campaign.data ? [brandName(brand), briefName(campaign.data), campaign.data.brief.target_platform]
        .filter(Boolean).join(' · ') : undefined}
      action={<Button asChild><Link href={workspaceHref(brand.id, 'campaigns/new')}>New campaign</Link></Button>} />
    <div className="page-content workflow-stack">
      {(campaign.isPending || (owned && generations.isPending)) && <LoadingCards label="Loading campaign ideas" />}
      <Problem error={campaign.error || generations.error || generation.error || shortlist.error || records.error} />
      {campaign.data && !owned && <EmptyState title="This campaign belongs to a different brand" />}
      {owned && <>
        <div className="workflow-actions">
          <Button disabled={start.isPending || isActive(latestStatus) || generations.isPending || (!!jobId && generation.isPending)}
            onClick={() => start.mutate()}>
            {start.isPending ? 'Starting…' : generations.data?.items.length ? 'Generate another set' : 'Generate ideas'}
          </Button>
          <Button variant="outline" asChild><Link href={base + '/edit'}>Edit brief</Link></Button>
          {!!generations.data?.items.length && <label className="workflow-control">Generation
            <select value={jobId} onChange={event => router.replace(base + '?generation=' + event.target.value)}>
              {!generations.data.items.some(row => row.id === jobId) && <option value={jobId}>Selected generation</option>}
              {generations.data.items.map(row => <option key={row.id} value={row.id}>
                {dateLabel(row.created_at)} · {prettyStatus(row.id === jobId ? job?.status ?? row.status : row.status)} · {row.id.slice(0, 5)}
              </option>)}
            </select>
          </label>}
        </div>
        <Problem error={start.error} reload={() => void campaign.refetch()} />
        {!jobId && generations.data && <EmptyState title="Your brief is saved">Generate ideas when you’re ready.</EmptyState>}
        {generation.data && !job && <Problem error="This generation belongs to a different campaign." />}
        {job && active && <Panel title="Creating your ideas">
          <JobBadge status={job.status} />
          <p className="text-muted-foreground">Saved ideas will appear as generation finishes. You can return to this job later.</p>
        </Panel>}
        {job?.error && <Problem error={job.error.message} />}
        {finished && job && <>
          <div className="workflow-actions">
            <Badge variant="secondary">{shortlisted.length} shortlisted</Badge>
            <span className="text-sm text-muted-foreground">{ideas.length} generated</span>
            <JobBadge status={job.status} />
            {!!shortlisted.length && <Button variant="ghost" size="sm" aria-pressed={!showAll} onClick={() => setShowAll(false)}>Shortlist</Button>}
            {!!shortlisted.length && <Button variant="ghost" size="sm" aria-pressed={showAll} onClick={() => setShowAll(true)}>All ideas</Button>}
          </div>
          {shortlist.data?.stale && <Panel>This generation uses an earlier brand profile or brief. Generate from the current versions before approving an idea.</Panel>}
          {(shortlist.isPending || records.isPending) && <LoadingCards label="Loading saved ideas" />}
          <div className="workflow-tiles">
            {visible.map(idea => <Link className="record-card workflow-tile" key={idea.id} href={base + '/ideas/' + idea.id}>
              <div className="idea-preview"><small>{idea.state === 'APPROVED' ? 'Approved concept' : 'Idea preview'}</small>
                <span>{idea.content.primary_hook || idea.content.title}</span>
              </div>
              <strong>{idea.content.title}</strong>
              <small>{[idea.content.psychological_driver && prettyStatus(idea.content.psychological_driver), idea.content.strategic_mechanism].filter(Boolean).join(' · ')}</small>
            </Link>)}
          </div>
          {records.data && !ideas.length && <EmptyState title="No ideas saved for this generation" />}
        </>}
      </>}
    </div>
  </>;
}
