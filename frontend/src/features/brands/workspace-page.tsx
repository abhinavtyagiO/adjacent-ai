'use client';

import {ArrowLeft, ArrowRight} from 'lucide-react';
import Link from 'next/link';
import {useRouter} from 'next/navigation';
import {useEffect, useState} from 'react';
import type {BrandRecord, RenderRecord} from '@/api/generated/workspace';
import {WorkspaceShell, type WorkspaceSection} from '@/components/layout/workspace-shell';
import {EmptyState, LoadingCards, LoadError} from '@/components/shared/feedback';
import {PageHead} from '@/components/shared/page-head';
import {Button} from '@/components/ui/button';
import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {SignOut} from '@/features/auth/sign-out';
import {useWorkspaceBrands} from '@/hooks/use-workspace-brands';
import {CampaignRecords, Dashboard, RenderRecords} from './dashboard';
import {BrandProfile} from './profile';
import {ResearchProgress} from './onboarding';
import {CampaignForm} from '@/features/campaigns/brief';
import {IdeaGallery} from '@/features/creatives/gallery';
import {IdeaDetail} from '@/features/creatives/concept';
import {MediaPage} from '@/features/media/library';
import {VideoStudio} from '@/features/video-studio/studio';
import {RenderReview} from '@/features/video-studio/render-review';
import {brandName, workspaceHref} from './labels';

export function WorkspacePage({brandId, section = 'overview', detail = []}: {
  brandId?: string; section?: WorkspaceSection | 'research' | 'studio'; detail?: string[];
}) {
  const brandsQuery = useWorkspaceBrands();
  const brands = brandsQuery.data ?? [];
  const brand = brands.find(record => record.id === brandId);
  const fallback = !brandId && section === 'overview' ? brands[0]?.id : undefined;
  const router = useRouter();
  useEffect(() => {
    if (fallback) router.replace(workspaceHref(fallback));
  }, [fallback, router]);

  if (brand && section === 'research') return <ResearchProgress brand={brand} jobId={detail[0]} />;
  if (brand && section === 'studio') return <VideoStudio brand={brand} conceptId={detail[0]} />;

  return <WorkspaceShell brands={brands} selected={brand} section={section === 'research' || section === 'studio' ? 'overview' : section}>
    {brandsQuery.isPending || fallback
      ? <>
        <PageHead title="Workspace" section="Overview" />
        <div className="page-content"><LoadingCards label="Loading workspace brands" /></div>
      </>
      : brandsQuery.error
        ? <>
          <PageHead title="Workspace" section="Overview" />
          <div className="page-content"><LoadError error={brandsQuery.error} retry={() => void brandsQuery.refetch()} /></div>
        </>
        : brandId && !brand
          ? <>
            <PageHead title="Brand unavailable" section="Overview" />
            <div className="page-content"><EmptyState title="Choose an available brand">
              Use the switcher to open a saved brand in this workspace.
            </EmptyState></div>
          </>
          : section === 'settings'
            ? <WorkspaceSettings brands={brands} selected={brand} />
            : brand
              ? <BrandSection key={brand.id + section + detail.join('/')} brand={brand} section={section} detail={detail} />
              : <>
                <PageHead title="Let's create something." subtitle="Your brand, ideas and videos in one place." section="Overview" />
                <div className="page-content"><EmptyState title="No brands yet">
                  <Button asChild><Link href='/workspace/new-brand'>Add a brand</Link></Button>
                </EmptyState></div>
              </>}
  </WorkspaceShell>;
}

function BrandSection({brand, section, detail}: {brand: BrandRecord; section: string; detail: string[]}) {
  if (section === 'brand') return <BrandProfile brand={brand} />;
  if (section === 'media') return <MediaPage brand={brand} />;
  if (section === 'campaigns') {
    const [campaignId, mode, ideaId] = detail;
    if (campaignId === 'new') return <CampaignForm brand={brand} />;
    if (mode === 'edit') return <CampaignForm brand={brand} campaignId={campaignId} />;
    if (mode === 'ideas') return <IdeaDetail brand={brand} campaignId={campaignId} conceptId={ideaId} />;
    if (campaignId) return <IdeaGallery brand={brand} campaignId={campaignId} />;
    return <History brand={brand} section="campaigns" />;
  }
  if (section === 'videos') return detail[0] ? <RenderReview brand={brand} jobId={detail[0]} /> : <History brand={brand} section="videos" />;
  return <Dashboard brand={brand} />;
}

function History({brand, section}: {brand: BrandRecord; section: 'campaigns' | 'videos'}) {
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState<RenderRecord['status']>();
  const limit = 12;
  const videos = section === 'videos';
  function pagination(more: boolean) {
    if (!offset && !more) return null;
    return <nav aria-label="Result pages" className="pagination">
      <Button variant="outline" disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - limit))}>
        <ArrowLeft aria-hidden="true" /> Previous
      </Button>
      <span className="text-sm text-muted-foreground">Page {offset / limit + 1}</span>
      <Button variant="outline" disabled={!more} onClick={() => setOffset(value => value + limit)}>
        Next <ArrowRight aria-hidden="true" />
      </Button>
    </nav>;
  }
  return <>
    <PageHead title={videos ? 'My videos' : 'Campaigns'} section={videos ? 'Videos' : 'Campaigns'}
      subtitle={videos ? 'Your generated videos and previous render versions.' : `Saved campaign briefs for ${brandName(brand)}.`}
      action={!videos && <Button asChild><Link href={workspaceHref(brand.id, 'campaigns/new')}>New campaign</Link></Button>} />
    <div className="page-content workflow-stack">
      {videos && <div className="workflow-actions" aria-label="Filter videos">
        {([[undefined, 'All videos'], ['SUCCEEDED', 'Ready'], ['RUNNING', 'Rendering'], ['PENDING', 'Queued'], ['FAILED', 'Failed']] as const)
          .map(([value, name]) => <button type="button" className="workflow-chip" key={name}
            aria-pressed={status === value} onClick={() => {setStatus(value); setOffset(0);}}>{name}</button>)}
      </div>}
      {videos
        ? <RenderRecords brandId={brand.id} limit={limit} offset={offset} status={status} onMore={pagination} />
        : <CampaignRecords brandId={brand.id} limit={limit} offset={offset} onMore={pagination} />}
    </div>
  </>;
}

function WorkspaceSettings({brands, selected}: {brands: BrandRecord[]; selected?: BrandRecord}) {
  return <>
    <PageHead title="Settings" section="Settings" subtitle="Manage access to your local workspace." />
    <div className="page-content grid gap-5 md:grid-cols-2">
      <Card className="workspace-card">
        <CardHeader><CardTitle>Account</CardTitle></CardHeader>
        <CardContent className="grid justify-items-start gap-4">
          <p className="text-muted-foreground">Signed in to the local workspace.</p>
          <SignOut />
        </CardContent>
      </Card>
      <Card className="workspace-card">
        <CardHeader><CardTitle>Workspace</CardTitle></CardHeader>
        <CardContent className="grid justify-items-start gap-3">
          <p>{brands.length} {brands.length === 1 ? 'brand' : 'brands'} registered</p>
          {selected && <Link className="text-link" href={workspaceHref(selected.id, 'brand')}>{brandName(selected)}</Link>}
          <Button asChild variant="outline"><Link href="/workspace/new-brand">Add a brand</Link></Button>
        </CardContent>
      </Card>
    </div>
  </>;
}
