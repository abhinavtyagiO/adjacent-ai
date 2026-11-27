import {notFound} from 'next/navigation';
import {WorkspacePage} from '@/features/brands/workspace-page';
import {isUuid} from '@/lib/workflow';

export default async function Section({params}: {params: Promise<{brandId: string; section: string[]}>}) {
  const {brandId, section: [section, ...detail]} = await params;
  if (!isUuid(brandId)) notFound();
  if ((section === 'brand' || section === 'media' || section === 'settings') && !detail.length) {
    return <WorkspacePage brandId={brandId} section={section} />;
  }
  if ((section === 'research' || section === 'studio') && detail.length === 1 && isUuid(detail[0])) {
    return <WorkspacePage brandId={brandId} section={section} detail={detail} />;
  }
  if (section === 'videos' && (!detail.length || (detail.length === 1 && isUuid(detail[0])))) {
    return <WorkspacePage brandId={brandId} section={section} detail={detail} />;
  }
  if (section === 'campaigns' && (
    !detail.length ||
    (detail.length === 1 && (detail[0] === 'new' || isUuid(detail[0]))) ||
    (isUuid(detail[0]) && ((detail.length === 2 && detail[1] === 'edit') ||
      (detail.length === 3 && detail[1] === 'ideas' && isUuid(detail[2]))))
  )) return <WorkspacePage brandId={brandId} section={section} detail={detail} />;
  notFound();
}
