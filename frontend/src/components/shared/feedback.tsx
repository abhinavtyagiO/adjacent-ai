import {AlertCircle} from 'lucide-react';
import Link from 'next/link';
import type {ReactNode} from 'react';
import {WorkspaceApiError} from '@/api/client';
import {Alert, AlertDescription, AlertTitle} from '@/components/ui/alert';
import {Button} from '@/components/ui/button';
import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {Skeleton} from '@/components/ui/skeleton';

export function LoadError({error, retry}: {error: Error; retry: () => void}) {
  const expired = error instanceof WorkspaceApiError && error.status === 401;
  return <Alert variant="destructive" role="alert">
    <AlertCircle aria-hidden="true" />
    <AlertTitle>{expired ? 'Session expired' : 'Could not load this section'}</AlertTitle>
    <AlertDescription>
      <p>{error.message}</p>
      {expired
        ? <Link className="text-link" href="/sign-in">Sign in again</Link>
        : <Button variant="outline" size="sm" onClick={retry}>Try again</Button>}
    </AlertDescription>
  </Alert>;
}

export function LoadingCards({label, count = 3}: {label: string; count?: number}) {
  return <div className="record-grid" role="status" aria-label={label}>
    {Array.from({length: count}, (_, index) => <Skeleton key={index} className="h-[77px] rounded-[12px]" />)}
  </div>;
}

export function EmptyState({title, children}: {title: string; children?: ReactNode}) {
  return <Card className="workspace-card">
    <CardHeader><CardTitle>{title}</CardTitle></CardHeader>
    {children && <CardContent className="text-sm text-muted-foreground">{children}</CardContent>}
  </Card>;
}
