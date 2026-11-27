'use client';

import {AlertCircle, ChevronLeft, ChevronRight, X} from 'lucide-react';
import {useEffect, useId, useRef, type ReactNode} from 'react';
import {WorkspaceApiError} from '@/api/client';
import {prettyStatus, safeWebUrl} from '@/lib/workflow';
import {Alert, AlertDescription} from '@/components/ui/alert';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Card, CardContent, CardTitle} from '@/components/ui/card';

export function Panel({children, className = '', title}: {children: ReactNode; className?: string; title?: string}) {
  return <Card className={`workspace-card ${className}`}>
    <CardContent className="panel-content">
      {title && <CardTitle>{title}</CardTitle>}
      {children}
    </CardContent>
  </Card>;
}

export function JobBadge({status}: {status: string}) {
  return <Badge variant={status === 'FAILED' ? 'destructive' : 'secondary'}>{prettyStatus(status)}</Badge>;
}

export function Problem({error, reload}: {error?: unknown; reload?: () => void}) {
  if (!error) return null;
  const stale = error instanceof WorkspaceApiError && error.status === 409;
  return <Alert variant="destructive" role="alert">
    <AlertCircle aria-hidden="true" />
    <AlertDescription>
      <p>{error instanceof Error ? error.message : String(error)}</p>
      {stale && reload && <Button variant="outline" size="sm" onClick={reload}>Reload saved version</Button>}
    </AlertDescription>
  </Alert>;
}

export function Field({label, children, error, hint}: {
  label: string; children: ReactNode; error?: string; hint?: string;
}) {
  return <div className="workflow-field">
    <label>
      <span>{label}</span>
      {children}
    </label>
    {error && <small className="text-destructive" role="alert">{error}</small>}
    {hint && <small className="text-muted-foreground">{hint}</small>}
  </div>;
}

export function Dialog({title, children, close, wide = false}: {
  title: string; children: ReactNode; close: () => void; wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);
  return <dialog ref={ref} className={`workflow-dialog ${wide ? 'workflow-dialog--wide' : ''}`}
    aria-labelledby={id} onCancel={close}>
    <div className="workflow-dialog-title">
      <h2 id={id}>{title}</h2>
      <Button variant="ghost" size="icon" aria-label="Close dialog" onClick={close}><X aria-hidden="true" /></Button>
    </div>
    {children}
  </dialog>;
}

export function Pages({offset, limit, more, change}: {
  offset: number; limit: number; more: boolean; change: (offset: number) => void;
}) {
  if (!offset && !more) return null;
  return <nav aria-label="Result pages" className="pagination">
    <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => change(Math.max(0, offset - limit))}>
      <ChevronLeft aria-hidden="true" /> Previous
    </Button>
    <span className="text-sm text-muted-foreground">Page {Math.floor(offset / limit) + 1}</span>
    <Button variant="outline" size="sm" disabled={!more} onClick={() => change(offset + limit)}>
      Next <ChevronRight aria-hidden="true" />
    </Button>
  </nav>;
}

export function ListValues({items, empty}: {items: (string | null | undefined)[]; empty?: string}) {
  const values = items.filter(Boolean);
  return values.length ? <ul className="workflow-list">{values.map((item, index) => <li key={index}>{item}</li>)}</ul>
    : empty ? <p className="text-muted-foreground">{empty}</p> : null;
}

export function SourceLink({url, children}: {url: unknown; children: ReactNode}) {
  const href = safeWebUrl(url);
  return href ? <a className="text-link" href={href} target="_blank" rel="noreferrer">{children}</a>
    : <span>{children}</span>;
}
