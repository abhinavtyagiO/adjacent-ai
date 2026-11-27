'use client';

import {zodResolver} from '@hookform/resolvers/zod';
import {useQueryClient} from '@tanstack/react-query';
import Link from 'next/link';
import {useRouter} from 'next/navigation';
import {useState} from 'react';
import {useForm, useWatch} from 'react-hook-form';
import {z} from 'zod';
import {
  createCampaign, getGetCampaignQueryKey, updateBrief, useGetCampaign, useListOfferings,
  type BrandRecord, type CampaignBrief, type CampaignRecord, type OfferingRecord,
} from '@/api/generated/workspace';
import {EmptyState, LoadingCards} from '@/components/shared/feedback';
import {PageHead} from '@/components/shared/page-head';
import {Field, Panel, Problem} from '@/components/shared/workflow';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {nullable, safeWebUrl, splitLines} from '@/lib/workflow';
import {workspaceHref} from '@/features/brands/labels';

const short = z.string().trim().max(200);
const briefSchema = z.object({
  objective: short.min(1, 'Enter a campaign objective.'),
  audience: short,
  concern: short,
  scope: z.enum(['BRAND', 'FAMILY', 'PRODUCT']),
  offering: short,
  platform: short,
  duration: z.number({error: 'Enter a duration between 5 and 60 seconds.'}).int().min(5).max(60),
  region: short,
  language: short,
  cta: short,
  destination: z.string().trim().max(2048).refine(value => !value || !!safeWebUrl(value), 'Enter a valid page URL.'),
  exclusions: z.string().refine(value => {
    const rows = splitLines(value);
    return rows.length <= 20 && rows.every(row => row.length <= 200);
  }, 'Use up to 20 entries of 200 characters each.'),
  references: z.string().refine(value => {
    const rows = splitLines(value);
    return rows.length <= 5 && rows.every(row => row.length <= 2000);
  }, 'Use up to five references of 2,000 characters each.'),
}).superRefine((data, context) => {
  if (!data.audience && !data.concern) context.addIssue({code: 'custom', path: ['audience'], message: 'Enter an audience or a customer concern.'});
  if (data.scope !== 'BRAND' && !data.offering) context.addIssue({code: 'custom', path: ['offering'], message: 'Choose a saved offering.'});
});
type BriefFields = z.infer<typeof briefSchema>;

function defaultFields(brief?: CampaignBrief): BriefFields {
  return {
    objective: brief?.objective ?? '', audience: brief?.audience ?? '', concern: brief?.concern ?? '',
    scope: brief?.promotion_scope ?? 'BRAND', offering: brief?.offering_key ?? '',
    platform: brief?.target_platform ?? '', duration: brief?.duration_seconds ?? 15,
    region: brief?.region ?? '', language: brief?.language ?? '', cta: brief?.cta ?? '',
    destination: brief?.destination_url ?? '', exclusions: brief?.creative_exclusions?.join('\n') ?? '',
    references: brief?.creative_references?.join('\n') ?? '',
  };
}

export function CampaignForm({brand, campaignId}: {brand: BrandRecord; campaignId?: string}) {
  const campaign = useGetCampaign(campaignId ?? '', {query: {enabled: !!campaignId}});
  const offerings = useListOfferings(brand.id, {query: {enabled: !!brand.profile_status}});
  const record = campaign.data;
  const allowed = !campaignId || record?.brand_id === brand.id;
  return <>
    <PageHead section="Campaigns" title={campaignId ? 'Edit campaign brief' : 'Create a campaign'}
      subtitle="Tell us who you want to reach and what you want to achieve." />
    <div className="page-content workflow-stack">
      <Problem error={campaign.error || offerings.error} />
      {campaignId && campaign.isPending && <LoadingCards label="Loading campaign" />}
      {record && !allowed && <EmptyState title="This campaign belongs to a different brand" />}
      {!brand.profile_status
        ? <EmptyState title="A brand profile is needed"><Link className="text-link" href={workspaceHref(brand.id, 'brand')}>Open brand profile</Link></EmptyState>
        : allowed && <BriefForm key={record?.revision ?? 'new'} brand={brand}
            record={record} offerings={offerings.data?.items ?? []} reload={() => void campaign.refetch()} />}
    </div>
  </>;
}

function BriefForm({brand, record, offerings, reload}: {
  brand: BrandRecord; record?: CampaignRecord; offerings: OfferingRecord[]; reload: () => void;
}) {
  const [error, setError] = useState<unknown>();
  const router = useRouter();
  const cache = useQueryClient();
  const {register, control, setValue, handleSubmit, formState: {errors, isSubmitting}} = useForm<BriefFields>({
    resolver: zodResolver(briefSchema), defaultValues: defaultFields(record?.brief),
  });
  const scope = useWatch({control, name: 'scope'});
  const scoped = offerings.filter(item => item.key && (scope === 'FAMILY'
    ? item.catalog_level === 'FAMILY' : item.catalog_level !== 'FAMILY'));

  async function save(data: BriefFields) {
    setError(undefined);
    const payload: CampaignBrief = {
      objective: data.objective, audience: nullable(data.audience), concern: nullable(data.concern),
      promotion_scope: data.scope, offering_key: data.scope === 'BRAND' ? null : data.offering,
      target_platform: nullable(data.platform), duration_seconds: data.duration, aspect_ratio: '9:16',
      region: nullable(data.region), language: nullable(data.language), cta: nullable(data.cta),
      destination_url: nullable(data.destination), creative_exclusions: splitLines(data.exclusions),
      creative_references: splitLines(data.references),
    };
    try {
      const saved = record ? await updateBrief(record.id, {expected_revision: record.revision, changes: {...payload}})
        : await createCampaign(brand.id, payload);
      cache.setQueryData(getGetCampaignQueryKey(saved.id), saved);
      void cache.invalidateQueries({queryKey: [`/v1/brands/${brand.id}/campaigns`]});
      router.push(workspaceHref(brand.id, `campaigns/${saved.id}`));
    } catch (problem) {
      setError(problem);
    }
  }

  return <form className="workflow-stack" onSubmit={handleSubmit(save)} noValidate>
    <Problem error={error} reload={record && reload} />
    <div className="workflow-columns brief-columns">
      <Panel>
        <Field label="Objective" error={errors.objective?.message}><Input {...register('objective')} placeholder="e.g. Awareness" maxLength={200} /></Field>
        <Field label="Audience" error={errors.audience?.message}><Input {...register('audience')} placeholder="Who should see this?" maxLength={200} /></Field>
        <Field label="Customer concern (optional)" error={errors.concern?.message}><Input {...register('concern')} maxLength={200} /></Field>
        <Field label="Promotion scope"><select {...register('scope', {onChange: () => setValue('offering', '')})}>
          <option value="BRAND">Brand</option><option value="FAMILY">Product family</option><option value="PRODUCT">Product or service</option>
        </select></Field>
        {scope !== 'BRAND' && <Field label="Offering" error={errors.offering?.message}>
          <select {...register('offering')}><option value="">Choose a saved offering</option>
            {scoped.map(item => <option key={item.id} value={item.key ?? ''}>{item.name}</option>)}
          </select>
        </Field>}
        <Field label="Platform (optional)" error={errors.platform?.message}><Input {...register('platform')} placeholder="e.g. Instagram Reels" maxLength={200} /></Field>
        <Button type="submit" disabled={isSubmitting}>{isSubmitting ? 'Saving…' : 'Save campaign'}</Button>
      </Panel>
      <Panel>
        <Field label="Duration (seconds)" error={errors.duration?.message}><Input type="number" min={5} max={60} {...register('duration', {valueAsNumber: true})} /></Field>
        <Field label="Aspect ratio"><Input value="9:16" readOnly /></Field>
        <Field label="Region (optional)" error={errors.region?.message}><Input {...register('region')} maxLength={200} /></Field>
        <Field label="Language (optional)" error={errors.language?.message}><Input {...register('language')} maxLength={200} /></Field>
        <Field label="CTA (optional)" error={errors.cta?.message}><Input {...register('cta')} maxLength={200} /></Field>
        <details>
          <summary>More campaign options</summary>
          <div className="workflow-form mt-3">
            <Field label="Destination URL" error={errors.destination?.message}><Input {...register('destination')} maxLength={2048} /></Field>
            <Field label="Phrases to avoid" hint="One per line." error={errors.exclusions?.message}><textarea rows={2} {...register('exclusions')} /></Field>
            <Field label="Creative references" hint="One per line." error={errors.references?.message}><textarea rows={2} {...register('references')} /></Field>
          </div>
        </details>
      </Panel>
    </div>
  </form>;
}
