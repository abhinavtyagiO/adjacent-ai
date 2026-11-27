import {WorkspacePage} from '@/features/brands/workspace-page';

export default async function BrandOverview({params}: {params: Promise<{brandId: string}>}) {
  return <WorkspacePage brandId={(await params).brandId} />;
}
