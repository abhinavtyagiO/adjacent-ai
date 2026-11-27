import {useQuery} from '@tanstack/react-query';
import {listBrandsV1BrandsGet, type BrandRecord} from '@/api/generated/workspace';

export function useWorkspaceBrands() {
  return useQuery({
    queryKey: ['workspace', 'brands'],
    queryFn: async ({signal}) => {
      const items: BrandRecord[] = [];
      const limit = 100;
      for (let offset = 0; ; offset += limit) {
        const page = await listBrandsV1BrandsGet({limit, offset}, {signal});
        items.push(...page.items);
        if (page.items.length < limit) return items;
      }
    },
  });
}
