import {notFound} from 'next/navigation';
import {WorkspacePage} from '@/features/brands/workspace-page';

export default async function Section({params}: {params: Promise<{brandId: string; section: string}>}) {
  const {brandId, section} = await params;
  if (section !== 'brand' && section !== 'campaigns' && section !== 'videos' && section !== 'settings') notFound();
  return <WorkspacePage brandId={brandId} section={section} />;
}
