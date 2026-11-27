'use client';

import {ArrowLeft, ArrowRight, ExternalLink} from 'lucide-react';
import Link from 'next/link';
import {useRouter} from 'next/navigation';
import {useEffect, useState} from 'react';
import type {BrandRecord} from '@/api/generated/workspace';
import {WorkspaceShell, type WorkspaceSection} from '@/components/layout/workspace-shell';
import {EmptyState, LoadingCards, LoadError} from '@/components/shared/feedback';
import {PageHead} from '@/components/shared/page-head';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {SignOut} from '@/features/auth/sign-out';
import {useWorkspaceBrands} from '@/hooks/use-workspace-brands';
import {CampaignRecords, Dashboard, RenderRecords} from './dashboard';
import {brandName, profileStatus, workspaceHref} from './labels';

export function WorkspacePage({brandId, section = 'overview'}: {brandId?: string; section?: WorkspaceSection}) {
  const brandsQuery = useWorkspaceBrands();
  const brands = brandsQuery.data ?? [];
  const brand = brands.find(record => record.id === brandId);
  const fallback = !brandId && section === 'overview' ? brands[0]?.id : undefined;
  const router = useRouter();
  useEffect(() => {
    if (fallback) router.replace(workspaceHref(fallback));
  }, [fallback, router]);

  return <WorkspaceShell brands={brands} selected={brand} section={section}>
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
              ? <BrandSection key={brand.id + section} brand={brand} section={section} />
              : <>
                <PageHead title="Let's create something." subtitle="Your brand, ideas and videos in one place." section="Overview" />
                <div className="page-content"><EmptyState title="No brands yet">
                  Brands registered through the Adjacent AI API will appear here.
                </EmptyState></div>
              </>}
  </WorkspaceShell>;
}

function BrandSection({brand, section}: {brand: BrandRecord; section: WorkspaceSection}) {
  if (section === 'brand') return <BrandSummary brand={brand} />;
  if (section === 'campaigns' || section === 'videos') return <History brand={brand} section={section} />;
  return <Dashboard brand={brand} />;
}

function BrandSummary({brand}: {brand: BrandRecord}) {
  const website = /^https?:\/\//i.test(brand.website_url) ? brand.website_url : undefined;
  const names = brand.offerings.flatMap(offering => typeof offering.name === 'string' ? [offering.name] : []);
  return <>
    <PageHead title={brandName(brand)} section="Brand" subtitle="The saved brand record and offerings in your workspace." />
    <div className="page-content grid gap-5 md:grid-cols-2">
      <Card className="workspace-card">
        <CardHeader><CardTitle>Brand profile</CardTitle></CardHeader>
        <CardContent className="grid justify-items-start gap-3">
          <Badge variant="secondary">{profileStatus(brand)}</Badge>
          {website && <a className="external-link" href={website} target="_blank" rel="noreferrer">
            Visit brand website <ExternalLink size={15} aria-hidden="true" />
          </a>}
        </CardContent>
      </Card>
      <Card className="workspace-card">
        <CardHeader><CardTitle>Offerings</CardTitle></CardHeader>
        <CardContent>{names.length
          ? <ul className="grid gap-2">{names.map((name, index) => <li key={index}>{name}</li>)}</ul>
          : <p className="text-muted-foreground">No offerings in the saved record.</p>}
        </CardContent>
      </Card>
    </div>
  </>;
}

function History({brand, section}: {brand: BrandRecord; section: 'campaigns' | 'videos'}) {
  const [offset, setOffset] = useState(0);
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
      subtitle={videos ? 'Your generated videos and previous render versions.' : `Saved campaign briefs for ${brandName(brand)}.`} />
    <div className="page-content">
      {videos
        ? <RenderRecords brandId={brand.id} limit={limit} offset={offset} onMore={pagination} />
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
        </CardContent>
      </Card>
    </div>
  </>;
}
