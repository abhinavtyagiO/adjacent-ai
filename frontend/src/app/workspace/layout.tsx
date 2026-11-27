import {redirect} from 'next/navigation';
import {WorkspaceQueries} from '@/components/layout/query-provider';
import {hasLocalSession} from '@/lib/server/session';

export default async function WorkspaceLayout({children}: {children: React.ReactNode}) {
  if (!(await hasLocalSession())) redirect('/sign-in');
  return <WorkspaceQueries>{children}</WorkspaceQueries>;
}
