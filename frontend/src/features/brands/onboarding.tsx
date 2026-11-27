'use client';

import {zodResolver} from '@hookform/resolvers/zod';
import {useQueryClient} from '@tanstack/react-query';
import Link from 'next/link';
import {useRouter} from 'next/navigation';
import {useEffect, useState} from 'react';
import {useForm} from 'react-hook-form';
import {z} from 'zod';
import type {WorkspaceApiError} from '@/api/client';
import {
  createBrand, extractBrand, getGetBrandQueryKey, getGetProfileQueryKey,
  getListExtractionsQueryKey, useGetBrand, useGetExtraction, useListExtractions,
  type BrandRecord, type ExtractionRecord, type PageExtractionRecord, type RegisteredBrand,
} from '@/api/generated/workspace';
import {Wordmark} from '@/components/layout/wordmark';
import {LoadError, LoadingCards} from '@/components/shared/feedback';
import {Field, JobBadge, Panel, Problem} from '@/components/shared/workflow';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {isActive, pollJob, prettyStatus, safeWebUrl} from '@/lib/workflow';
import {brandName, workspaceHref} from './labels';

const siteForm = z.object({
  website: z.string().trim().min(1, 'Enter your brand website.').max(2048).refine(
    value => !!safeWebUrl(withScheme(value)),
    'Enter a valid website address.',
  ),
});
const withScheme = (value: string) => /^[a-z]+:\/\//i.test(value) ? value : 'https://' + value;

function Onboarding({step, title, intro, children}: {
  step: string; title: string; intro: string; children: React.ReactNode;
}) {
  return <main className="onboarding-page">
    <Wordmark />
    <div className="onboarding-content">
      <span className="workflow-step">{step}</span>
      <h1>{title}</h1>
      <p className="text-muted-foreground">{intro}</p>
      {children}
    </div>
  </main>;
}

export function AddBrand() {
  const router = useRouter();
  const cache = useQueryClient();
  const [brand, setBrand] = useState<RegisteredBrand>();
  const [problem, setProblem] = useState<unknown>();
  const {register, handleSubmit, formState: {isSubmitting, errors}} = useForm<z.infer<typeof siteForm>>({
    defaultValues: {website: ''}, resolver: zodResolver(siteForm),
  });

  async function submit({website}: z.infer<typeof siteForm>) {
    setProblem(undefined);
    try {
      const row = brand ?? await createBrand({website_url: withScheme(website)});
      setBrand(row);
      await cache.invalidateQueries({queryKey: ['workspace', 'brands']});
      const job = await extractBrand(row.id, {});
      router.push(workspaceHref(row.id, `research/${job.id}`));
    } catch (error) {
      setProblem(error);
    }
  }

  return <Onboarding step="Step 1 of 3 / Your brand" title="Tell us about your brand."
    intro="Start with your website. We’ll learn what you sell, who it’s for, and what makes it different.">
    <Panel className="onboarding-form">
      <form className="workflow-form" onSubmit={handleSubmit(submit)} noValidate>
        <Field label="Brand website" error={errors.website?.message}>
          <Input {...register('website')} inputMode="url" autoComplete="url" readOnly={!!brand}
            placeholder="https://yourbrand.com" aria-invalid={!!errors.website} />
        </Field>
        <Problem error={problem} />
        <div className="workflow-actions">
          <Button type="submit" disabled={isSubmitting}>{isSubmitting ? 'Starting research…' : brand ? 'Retry research' : 'Analyze my brand'}</Button>
          {brand && <Button variant="outline" asChild><Link href={workspaceHref(brand.id, 'brand')}>Open saved brand</Link></Button>}
        </div>
      </form>
    </Panel>
    <p className="text-sm text-muted-foreground">Review and edit everything we discover.</p>
    <Link href="/workspace" className="text-link">Back to workspace</Link>
  </Onboarding>;
}

export function ResearchActions({brand, hasProfile = false}: {brand: BrandRecord; hasProfile?: boolean}) {
  const router = useRouter();
  const cache = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<unknown>();
  const latest = useListExtractions<PageExtractionRecord, WorkspaceApiError>(brand.id, {limit: 1}, {
    query: {refetchInterval: query => pollJob(query.state.data?.items[0]?.status)},
  });
  const current = latest.data?.items[0];
  async function start() {
    setBusy(true);
    setProblem(undefined);
    try {
      const job = await extractBrand(brand.id, {});
      await cache.invalidateQueries({queryKey: getListExtractionsQueryKey(brand.id)});
      router.push(workspaceHref(brand.id, `research/${job.id}`));
    } catch (error) {
      setProblem(error);
    } finally {
      setBusy(false);
    }
  }

  return <div className="grid gap-3">
    <div className="workflow-actions">
      {current && <Button variant="outline" asChild><Link href={workspaceHref(brand.id, `research/${current.id}`)}>
        {isActive(current.status) ? 'Resume research' : 'View last research job'}
      </Link></Button>}
      {!hasProfile && !isActive(current?.status) && !latest.isPending && <Button disabled={busy} onClick={() => void start()}>
        {busy ? 'Starting…' : current ? 'Retry brand research' : 'Analyze my brand'}
      </Button>}
    </div>
    <Problem error={problem || latest.error} />
  </div>;
}

export function ResearchProgress({brand, jobId}: {brand: BrandRecord; jobId: string}) {
  const cache = useQueryClient();
  const query = useGetExtraction<ExtractionRecord, WorkspaceApiError>(jobId, {
    query: {refetchInterval: result => pollJob(result.state.data?.status)},
  });
  const job = query.data?.brand_id === brand.id ? query.data : undefined;
  const running = isActive(job?.status);
  const current = useGetBrand(brand.id, {query: {refetchInterval: running ? 2500 : false}});
  const report = current.data?.profile_status;
  const status = job?.status;

  useEffect(() => {
    if (!status || isActive(status)) return;
    void cache.invalidateQueries({queryKey: ['workspace', 'brands']});
    void cache.invalidateQueries({queryKey: getGetBrandQueryKey(brand.id)});
    void cache.invalidateQueries({queryKey: getGetProfileQueryKey(brand.id)});
  }, [brand.id, cache, status]);

  return <Onboarding step="Step 2 of 3 / Research" title="Getting to know your brand"
    intro={`Research for ${brandName(brand)}`}>
    {query.isPending && <LoadingCards label="Loading research job" count={1} />}
    {query.error && <LoadError error={query.error} retry={() => void query.refetch()} />}
    {query.data && !job && <Problem error="This research job belongs to a different brand." />}
    {job && <Panel>
      <JobBadge status={job.status} />
      <h2>{running ? 'Analyzing products and positioning'
        : report ? 'Brand profile available' : prettyStatus(job.status)}</h2>
      <ul className="workflow-list text-muted-foreground">
        <li>Website and catalog · {job.diagnostics.discovery ? 'Discovery finished' : running ? 'In progress' : 'See job result'}</li>
        <li>Brand intelligence · {report ? prettyStatus(report) : running ? 'Preparing profile' : 'Profile unavailable'}</li>
        <li>Review profile · {report ? 'Ready' : 'Next'}</li>
      </ul>
      <Problem error={job.error?.message} />
      {running && <small className="text-muted-foreground">You can leave this page and return to the job.</small>}
    </Panel>}
    <div className="workflow-actions">
      {report && <Button asChild><Link href={workspaceHref(brand.id, 'brand')}>Review profile</Link></Button>}
      <Button asChild variant="outline"><Link href={workspaceHref(brand.id)}>Back to workspace</Link></Button>
    </div>
  </Onboarding>;
}
