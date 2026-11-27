'use client';

import Link from 'next/link';
import {privateMediaUrl, type WorkspaceApiError} from '@/api/client';
import {
  useGetConcept, useGetJob, useListRendersV1BrandsBrandIdRendersGet,
  type BrandRecord, type RenderJobRecord,
} from '@/api/generated/workspace';
import {EmptyState, LoadingCards} from '@/components/shared/feedback';
import {PageHead} from '@/components/shared/page-head';
import {JobBadge, Panel, Problem} from '@/components/shared/workflow';
import {Button} from '@/components/ui/button';
import {renderStatus, workspaceHref} from '@/features/brands/labels';
import {dateLabel, isActive, pollJob, prettyStatus} from '@/lib/workflow';

export function RenderReview({brand, jobId}: {brand: BrandRecord; jobId: string}) {
  const query = useGetJob<RenderJobRecord, WorkspaceApiError>(jobId, {
    query: {refetchInterval: result => pollJob(result.state.data?.status)},
  });
  const job = query.data?.inputs.brand_id === brand.id ? query.data : undefined;
  const conceptId = job?.inputs.concept_id ?? '';
  const concept = useGetConcept(conceptId, {query: {enabled: !!conceptId}});
  const output = job?.status === 'SUCCEEDED' ? job.artifact : undefined;
  const media = output?.metadata.media;
  const duration = job ? job.inputs.duration_frames / (job.inputs.fps ?? 30) : undefined;
  const history = useListRendersV1BrandsBrandIdRendersGet(brand.id, {spec_id: job?.spec_id, limit: 12}, {
    query: {enabled: !!job, refetchInterval: isActive(job?.status) ? 3000 : false},
  });
  const title = output ? 'Your video is ready' : job?.status === 'FAILED' ? 'Render needs attention' : 'Video render';

  return <>
    <PageHead title={title} section="Videos"
      subtitle={[concept.data?.content.title, duration && `${duration} seconds`, job && '1080 × 1920'].filter(Boolean).join(' · ')}
      action={<Button asChild variant="outline"><Link href={workspaceHref(brand.id, 'videos')}>All videos</Link></Button>} />
    <div className="page-content workflow-stack">
      {query.isPending && <LoadingCards label="Loading render job" />}
      <Problem error={query.error} reload={() => void query.refetch()} />
      {query.data && !job && <EmptyState title="This render belongs to a different brand" />}
      {job && <>
        <div className="workflow-columns render-review-columns">
          <section className="studio-canvas render-review-canvas" aria-label="Render playback">
            {output ? <video controls playsInline preload="metadata" src={privateMediaUrl(output.play_url)}
              poster={privateMediaUrl(output.preview_url)} aria-label="Rendered video" />
              : <div className="studio-preview-empty">
                <JobBadge status={job.status} />
                <p>{isActive(job.status) ? 'The finished video will appear here.' : 'No completed video is available for this job.'}</p>
              </div>}
          </section>
          <div className="workflow-stack">
            <Panel>
              <JobBadge status={job.status} />
              <h3>{media && output?.metadata.validated ? 'Technical validation passed'
                : isActive(job.status) ? 'Preparing your video' : prettyStatus(job.status)}</h3>
              {media && <p className="text-muted-foreground">{media.codec.toUpperCase()} · {media.frame_rate} FPS · {media.duration_seconds}s · {media.audio_streams ? 'Source audio' : 'Silent'}</p>}
              {isActive(job.status) && <>
                <p className="text-muted-foreground">{typeof job.diagnostics.stage === 'string' ? prettyStatus(job.diagnostics.stage) : 'Queued'}</p>
                <p className="text-sm text-muted-foreground">You can leave this page and return to the saved job.</p>
              </>}
              <Problem error={job.error?.message} />
              <div className="workflow-actions">
                {output && <Button asChild><a href={privateMediaUrl(output.download_url)}>Download MP4</a></Button>}
                <Button variant="outline" asChild><Link href={workspaceHref(brand.id, `studio/${job.inputs.concept_id}?spec=${job.spec_id}`)}>
                  {isActive(job.status) ? 'Open video setup' : 'Edit and render again'}
                </Link></Button>
              </div>
            </Panel>
            <Panel title="Check the finished advertisement">
              <p className="text-muted-foreground">Review framing, caption readability, factual claims and media rights before publishing.</p>
            </Panel>
          </div>
        </div>
        <Panel title="Render history">
          <Problem error={history.error} />
          <ul className="review-list" aria-label="Saved render versions">
            {history.data?.items.map(row => <li key={row.id}>
              {row.id === job.id
                ? <strong>Version {row.spec_revision} · {renderStatus[row.status]} · Current</strong>
                : <Link className="text-link" href={workspaceHref(brand.id, `videos/${row.id}`)}>
                    Version {row.spec_revision} · {renderStatus[row.status]}
                  </Link>}
              <small>{dateLabel(row.created_at)}</small>
            </li>)}
          </ul>
        </Panel>
      </>}
    </div>
  </>;
}
