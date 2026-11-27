'use client';

import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {useState, type ReactNode} from 'react';
import {WorkspaceApiError} from '@/api/client';

export function WorkspaceQueries({children}: {children: ReactNode}) {
  const [client] = useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: (failures, error) =>
          failures < 1 && !(error instanceof WorkspaceApiError && error.status < 500),
      },
    },
  }));
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
