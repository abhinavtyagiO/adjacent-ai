'use client';

import {zodResolver} from '@hookform/resolvers/zod';
import {useMutation, useQueryClient} from '@tanstack/react-query';
import Link from 'next/link';
import {useState} from 'react';
import {useForm, useWatch} from 'react-hook-form';
import {z} from 'zod';
import {
  approveConcept, editConcept, getGetConceptQueryKey, useGetCampaign, useGetConcept, useGetGeneration, useGetResearch,
  type BrandRecord, type Candidate, type ConceptRecord,
} from '@/api/generated/workspace';
import {EmptyState, LoadingCards} from '@/components/shared/feedback';
import {PageHead} from '@/components/shared/page-head';
import {Dialog, Field, JobBadge, ListValues, Panel, Problem, SourceLink} from '@/components/shared/workflow';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {brandName, workspaceHref} from '@/features/brands/labels';
import {nullable, prettyStatus, splitLines} from '@/lib/workflow';

const beats = [
  ['HOOK', 'Hook'], ['DEVELOPMENT', 'Development'],
  ['PRODUCT_CONNECTION', 'Product connection'], ['PAYOFF', 'Payoff'],
] as const;

export function captionLines(copy: Candidate['on_screen_copy']) {
  if (!copy) return [];
  if (copy.treatment === 'SEQUENTIAL_CAPTIONS') return copy.captions.map(beat => ({
    label: beats.find(([key]) => key === beat.beat)?.[1] ?? prettyStatus(beat.beat), text: beat.text,
  }));
  return [{label: 'Caption', text: copy.text},
    ...(copy.closing_brand_message ? [{label: 'Closing message', text: copy.closing_brand_message}] : [])];
}

const checkLabels = [
  'Review final advertising copy',
  'Confirm product claims and assumptions',
  'Acknowledge asset rights requirements',
] as const;

export function IdeaDetail({brand, campaignId, conceptId}: {brand: BrandRecord; campaignId: string; conceptId: string}) {
  const campaign = useGetCampaign(campaignId);
  const concept = useGetConcept(conceptId);
  const idea = concept.data;
  const owned = campaign.data?.brand_id === brand.id && idea?.campaign_id === campaignId;
  const base = workspaceHref(brand.id, `campaigns/${campaignId}`);
  return <>
    <PageHead section="Campaigns" title={owned && idea ? idea.content.title : 'Idea details'}
      subtitle="Review the concept, exact captions and supporting evidence."
      action={<Button variant="outline" asChild><Link href={base + (idea ? '?generation=' + idea.job_id : '')}>Back to ideas</Link></Button>} />
    <div className="page-content workflow-stack">
      {(campaign.isPending || concept.isPending) && <LoadingCards label="Loading idea" />}
      <Problem error={campaign.error || concept.error} />
      {campaign.data && idea && !owned && <EmptyState title="This idea is outside the selected campaign" />}
      {owned && idea && <ConceptContent key={`${idea.id}/${idea.revision}`} brand={brand} idea={idea}
        reload={() => void concept.refetch()} />}
    </div>
  </>;
}

function ConceptContent({brand, idea, reload}: {brand: BrandRecord; idea: ConceptRecord; reload: () => void}) {
  const [panel, setPanel] = useState<'edit' | 'evidence'>();
  const [checked, setChecked] = useState<string[]>([]);
  const cache = useQueryClient();
  const copy = idea.content;
  const caption = copy.on_screen_copy;
  const approved = idea.state === 'APPROVED';
  const approve = useMutation({
    mutationFn: () => approveConcept(idea.id, {
      expected_revision: idea.revision,
      human_review_completed: true,
      acknowledge_claim_risks: true,
      acknowledge_asset_requirements: true,
    }),
    onSuccess: updated,
  });

  function updated(row: ConceptRecord) {
    cache.setQueryData(getGetConceptQueryKey(idea.id), row);
    void cache.invalidateQueries({queryKey: [`/v1/campaigns/${idea.campaign_id}/concepts`]});
    void cache.invalidateQueries({queryKey: [`/v1/campaigns/${idea.campaign_id}/shortlist`]});
    setPanel(undefined);
  }

  return <>
    <div className="workflow-columns">
      <div className="workflow-stack">
        <Panel>
          {copy.psychological_driver && <Badge variant="secondary">{prettyStatus(copy.psychological_driver)}</Badge>}
          <h3>{copy.core_insight || copy.title}</h3>
          {copy.audience_situation && <p className="text-muted-foreground">{copy.audience_situation}</p>}
        </Panel>
        <Panel>
          {captionLines(caption).map(row => <div className="caption-line" key={row.label}>
            <small>{row.label}</small><p>{row.text}</p>
          </div>)}
          {!caption && <>
            {copy.primary_hook && <div className="caption-line"><small>Hook</small><p>{copy.primary_hook}</p></div>}
            <p className="text-muted-foreground">This idea needs structured on-screen captions before it can be rendered.</p>
          </>}
        </Panel>
        <details className="workspace-card workflow-details">
          <summary>Story and alternative hooks</summary>
          <div className="workflow-stack">
            <ListValues items={copy.alternative_hooks ?? []} />
            {Object.entries(copy.story ?? {}).map(([part, value]) => <div className="caption-line" key={part}>
              <small>{prettyStatus(part)}</small><p>{value}</p>
            </div>)}
            <p className="text-muted-foreground">{[copy.closing_line, copy.cta].filter(Boolean).join(' · ')}</p>
          </div>
        </details>
      </div>
      <div className="workflow-stack">
        <Panel title="Before production">
          {approved ? <JobBadge status={idea.state} /> : checkLabels.map(label => <label className="workflow-check" key={label}>
            <input type="checkbox" checked={checked.includes(label)} onChange={event => setChecked(
              before => event.target.checked ? [...before, label] : before.filter(value => value !== label),
            )} />
            <span>{label}</span>
          </label>)}
          {!idea.validation.eligible && <Problem error="Resolve blocking review flags before approval." />}
          <button className="text-link self-start text-left" onClick={() => setPanel('evidence')}>Source references and validation flags</button>
          {brand.name && brand.name.length <= 40 && <small className="text-muted-foreground">
            Optional video brand label · {approved ? idea.review.approved_brand_label ?? 'Reapprove to use the brand name' : brandName(brand)}
          </small>}
          {approved && caption
            ? <Button asChild><Link href={workspaceHref(brand.id, `studio/${idea.id}`)}>Open video studio</Link></Button>
            : !approved && <Button disabled={!caption || checked.length !== checkLabels.length
                || !idea.validation.eligible || approve.isPending} onClick={() => approve.mutate()}>
                {approve.isPending ? 'Approving…' : 'Approve concept'}
              </Button>}
          <Problem error={approve.error} reload={reload} />
        </Panel>
        <Panel>
          <p className="text-sm text-muted-foreground">Edit and reapprove any copy changes.</p>
          <Button variant="outline" onClick={() => setPanel('edit')}>Edit creative</Button>
        </Panel>
      </div>
    </div>
    {panel === 'edit' && <ConceptEditor idea={idea} close={() => setPanel(undefined)} saved={updated}
      reload={() => {setPanel(undefined); reload();}} />}
    {panel === 'evidence' && <Evidence idea={idea} close={() => setPanel(undefined)} />}
  </>;
}

const captionText = z.string().trim().max(180);
const editSchema = z.object({
  title: z.string().trim().min(1).max(120),
  hook: captionText.min(1),
  alternatives: z.string().refine(value => {
    const rows = splitLines(value);
    return rows.length >= 2 && rows.length <= 3 && rows.every(row => row.length <= 180);
  }, 'Enter two or three alternative hooks, one per line and up to 180 characters each.'),
  treatment: z.enum(['SINGLE_CAPTION', 'SEQUENTIAL_CAPTIONS']),
  single: captionText,
  closingCaption: captionText,
  sequence: z.array(captionText).length(4),
  closing: z.string().trim().min(1).max(200),
  cta: z.string().trim().min(1).max(200),
}).superRefine((value, context) => {
  if (value.treatment === 'SINGLE_CAPTION' && !value.single) {
    context.addIssue({code: 'custom', path: ['single'], message: 'Enter the on-screen caption.'});
  }
  if (value.treatment === 'SEQUENTIAL_CAPTIONS' && value.sequence.some(text => !text)) {
    context.addIssue({code: 'custom', path: ['sequence'], message: 'Enter all four captions.'});
  }
});

function ConceptEditor({idea, close, saved, reload}: {
  idea: ConceptRecord; close: () => void; saved: (idea: ConceptRecord) => void; reload: () => void;
}) {
  const copy = idea.content;
  const captions = copy.on_screen_copy;
  const {register, handleSubmit, control, formState: {errors}} = useForm<z.infer<typeof editSchema>>({
    resolver: zodResolver(editSchema),
    defaultValues: {
      title: copy.title, hook: copy.primary_hook ?? '', alternatives: copy.alternative_hooks?.join('\n') ?? '',
      treatment: captions?.treatment ?? 'SINGLE_CAPTION',
      single: captions?.treatment === 'SINGLE_CAPTION' ? captions.text : copy.primary_hook ?? '',
      closingCaption: captions?.treatment === 'SINGLE_CAPTION' ? captions.closing_brand_message ?? '' : '',
      sequence: beats.map(([beat]) => captions?.treatment === 'SEQUENTIAL_CAPTIONS'
        ? captions.captions.find(row => row.beat === beat)?.text ?? '' : ''),
      closing: copy.closing_line ?? '', cta: copy.cta ?? '',
    },
  });
  const treatment = useWatch({control, name: 'treatment'});
  const save = useMutation({
    mutationFn: (data: z.infer<typeof editSchema>) => editConcept(idea.id, {
      expected_revision: idea.revision,
      changes: {
        title: data.title, primary_hook: data.hook, alternative_hooks: splitLines(data.alternatives),
        closing_line: data.closing, cta: data.cta,
        on_screen_copy: data.treatment === 'SINGLE_CAPTION'
          ? {treatment: data.treatment, text: data.single, closing_brand_message: nullable(data.closingCaption)}
          : {treatment: data.treatment, captions: data.sequence.map((text, index) => ({beat: beats[index][0], text}))},
      },
    }),
    onSuccess: saved,
  });
  return <Dialog title="Edit creative" close={close}>
    <form className="workflow-form" onSubmit={handleSubmit(data => save.mutate(data))} noValidate>
      <Field label="Title" error={errors.title?.message}><Input {...register('title')} maxLength={120} /></Field>
      <Field label="Primary hook" error={errors.hook?.message}><Input {...register('hook')} maxLength={180} /></Field>
      <Field label="Alternative hooks" hint="One per line." error={errors.alternatives?.message}><textarea rows={3} {...register('alternatives')} /></Field>
      <Field label="Caption treatment"><select {...register('treatment')}><option value="SINGLE_CAPTION">Single caption</option>
        <option value="SEQUENTIAL_CAPTIONS">Sequential captions</option></select></Field>
      {treatment === 'SINGLE_CAPTION' ? <>
        <Field label="On-screen caption" error={errors.single?.message}><Input {...register('single')} maxLength={180} /></Field>
        <Field label="Closing brand message (optional)" error={errors.closingCaption?.message}><Input {...register('closingCaption')} maxLength={180} /></Field>
      </> : <>
        {beats.map(([key, label], index) => <Field key={key} label={label + ' caption'} error={errors.sequence?.[index]?.message}>
          <Input {...register(`sequence.${index}`)} maxLength={180} />
        </Field>)}
        <Problem error={errors.sequence?.message} />
      </>}
      <Field label="Closing line" error={errors.closing?.message}><Input {...register('closing')} maxLength={200} /></Field>
      <Field label="Call to action" error={errors.cta?.message}><Input {...register('cta')} maxLength={200} /></Field>
      <Problem error={save.error} reload={reload} />
      <Button type="submit" disabled={save.isPending}>Save changes</Button>
    </form>
  </Dialog>;
}

function Evidence({idea, close}: {idea: ConceptRecord; close: () => void}) {
  const generation = useGetGeneration(idea.job_id);
  const researchId = generation.data?.research_id ?? '';
  const research = useGetResearch(idea.campaign_id, researchId, {query: {enabled: !!researchId}});
  const references = new Set([...(idea.content.capability_reference_ids ?? []), ...(idea.content.research_reference_ids ?? [])]);
  const items = research.data?.payload.items?.filter(item => references.has(item.id)) ?? [];
  return <Dialog title="Sources and validation flags" close={close} wide>
    <div className="workflow-stack">
      <ul className="review-list">{idea.validation.flags?.map((flag, index) => <li key={index}>
        <Badge variant={flag.severity === 'BLOCK' ? 'destructive' : 'secondary'}>{prettyStatus(flag.severity)}</Badge>
        <span>{flag.message || prettyStatus(flag.code)}</span>
      </li>)}</ul>
      {idea.evaluation?.grounding?.explanation && <p>{idea.evaluation.grounding.explanation}</p>}
      {idea.evaluation?.selection_rationale && <p className="text-muted-foreground">{idea.evaluation.selection_rationale}</p>}
      {idea.evaluation?.distinction && <p className="text-muted-foreground">{idea.evaluation.distinction}</p>}
      <h3>Referenced research</h3>
      <Problem error={generation.error || research.error} />
      {(generation.isPending || (!!researchId && research.isPending)) && <LoadingCards label="Loading research references" />}
      {research.data && !items.length && <p className="text-muted-foreground">No references are available for this saved idea.</p>}
      <ul className="review-list">{items.map(item => <li key={item.id}>
        <small>{prettyStatus(item.origin)}</small><span>{item.text}</span>
        {item.source_urls?.map(url => <SourceLink key={url} url={url}>{url}</SourceLink>)}
      </li>)}</ul>
      {!!idea.content.asset_requirements?.length && <><h3>Assets to review</h3>
        <ListValues items={idea.content.asset_requirements.map(asset => asset.description)} /></>}
    </div>
  </Dialog>;
}
