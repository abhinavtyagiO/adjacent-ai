'use client';

import {ArrowUpRight, Film} from 'lucide-react';
import Link from 'next/link';
import {
  useListCampaignsV1BrandsBrandIdCampaignsGet as useCampaigns,
  useListRendersV1BrandsBrandIdRendersGet as useRenders,
  type BrandRecord, type CampaignRecord, type PageCampaignRecord, type PageRenderRecord, type RenderRecord,
} from '@/api/generated/workspace';
import type {WorkspaceApiError} from '@/api/client';
import {EmptyState, LoadError, LoadingCards} from '@/components/shared/feedback';
import {PageHead} from '@/components/shared/page-head';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Card, CardContent} from '@/components/ui/card';
import {ResearchActions} from './onboarding';
import {brandName, briefName, renderPreview, renderStatus, workspaceHref} from './labels';

export function Dashboard({brand}: {brand: BrandRecord}) {
  return <>
    <PageHead section="Overview" title="Let's create something." subtitle="Your brand, ideas and videos in one place."
      action={<Button asChild><Link href={workspaceHref(brand.id, 'campaigns/new')}>New campaign</Link></Button>} />
    <div className="page-content space-y-5">
      <Card className="workspace-card profile-callout">
        <CardContent className="grid justify-items-start gap-3.5">
          <Badge variant="secondary">{brand.profile_status ? 'Brand profile available' : 'Research needed'}</Badge>
          <h2>{brand.profile_status === 'CONFIRMED' ? 'Your brand profile is confirmed'
            : brand.profile_status === 'DRAFT' ? 'Your brand is ready to review' : 'Start with your brand'}</h2>
          <p className="text-muted-foreground">{brand.profile_status === 'CONFIRMED'
            ? `Open ${brandName(brand)}’s saved brand intelligence.`
            : brand.profile_status === 'DRAFT'
              ? `Review ${brandName(brand)}’s brand intelligence before creating your next campaign.`
              : `${brandName(brand)} has a registered website and is ready for research.`}</p>
          {brand.profile_status
            ? <Button asChild variant="outline"><Link href={workspaceHref(brand.id, 'brand')}>View brand</Link></Button>
            : <ResearchActions brand={brand} />}
        </CardContent>
      </Card>
      <section className="dashboard-section" aria-labelledby="recent-campaigns">
        <h2 id="recent-campaigns">Recent campaigns</h2>
        <CampaignRecords brandId={brand.id} limit={3} />
      </section>
      <section className="dashboard-section" aria-labelledby="recent-videos">
        <h2 id="recent-videos">Recent videos</h2>
        <RenderRecords brandId={brand.id} limit={2} readyOnly />
      </section>
    </div>
  </>;
}

function CampaignCard({campaign}: {campaign: CampaignRecord}) {
  return <Link className="record-card record-link" href={workspaceHref(campaign.brand_id, `campaigns/${campaign.id}`)}>
    <span>
      <strong>{briefName(campaign)}</strong>
      <small>{[campaign.brief.target_platform, `${campaign.brief.duration_seconds}s`, campaign.brief.aspect_ratio].filter(Boolean).join(' · ')}</small>
    </span>
  </Link>;
}

export function CampaignRecords({brandId, limit = 12, offset = 0, onMore}: {
  brandId: string; limit?: number; offset?: number; onMore?: (more: boolean) => React.ReactNode;
}) {
  const query = useCampaigns<PageCampaignRecord, WorkspaceApiError>(
    brandId, {limit, offset},
  );
  if (query.isPending) return <LoadingCards label="Loading campaigns" />;
  if (query.error) return <LoadError error={query.error} retry={() => void query.refetch()} />;
  if (!query.data?.items.length) return <><p className="empty-note">No saved campaigns {offset ? 'on this page.' : 'yet.'}</p>{onMore?.(false)}</>;
  return <>
    <div className="record-grid">{query.data.items.map(campaign => <CampaignCard campaign={campaign} key={campaign.id} />)}</div>
    {onMore?.(query.data.items.length === limit)}
  </>;
}

function RenderCard({video, brandId}: {video: RenderRecord; brandId: string}) {
  const preview = renderPreview(video);
  return <Link className="video-card" href={workspaceHref(brandId, `videos/${video.id}`)}>
      <div className="video-preview">
        {preview && <img src={preview} alt="" loading="lazy" />}
        {!preview && <Film aria-hidden="true" size={20} />}
        <span>{video.concept_title}</span>
      </div>
      <strong>{video.concept_title}</strong>
      <small>{renderStatus[video.status]} · {video.duration_seconds}s · 9:16 · Version {video.spec_revision}</small>
  </Link>;
}

export function RenderRecords({brandId, limit = 12, offset = 0, readyOnly = false, status, onMore}: {
  brandId: string; limit?: number; offset?: number; readyOnly?: boolean; status?: RenderRecord['status']; onMore?: (more: boolean) => React.ReactNode;
}) {
  const query = useRenders<PageRenderRecord, WorkspaceApiError>(
    brandId, {limit, offset, status: readyOnly ? 'SUCCEEDED' : status},
    {query: {refetchInterval: result => result.state.data?.items.some(
      row => row.status === 'PENDING' || row.status === 'RUNNING',
    ) ? 5000 : false}},
  );
  if (query.isPending) return <LoadingCards label="Loading video renders" />;
  if (query.error) return <LoadError error={query.error} retry={() => void query.refetch()} />;
  return <>
    <div className="record-grid record-grid--video">
      {query.data?.items.map(video => <RenderCard key={video.id} video={video} brandId={brandId} />)}
      {readyOnly && <Link className="record-card record-link" href={workspaceHref(brandId, 'videos')}>
        <span><strong>Video library</strong><small>{query.data?.items.length ? 'View previous renders' : 'No completed videos yet'}</small></span>
        <ArrowUpRight size={16} aria-hidden="true" />
      </Link>}
    </div>
    {!readyOnly && !query.data?.items.length && <EmptyState title={offset ? 'No renders on this page' : 'No video renders yet'} />}
    {onMore?.((query.data?.items.length ?? 0) === limit)}
  </>;
}
