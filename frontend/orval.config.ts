import {defineConfig} from 'orval';

export default defineConfig({
  workspace: {
    input: {
      target: './openapi.json',
      filters: {mode: 'include', tags: ['workspace-dashboard', 'workspace-workflow']},
    },
    output: {
      target: './src/api/generated/workspace.ts',
      mode: 'single',
      client: 'react-query',
      httpClient: 'fetch',
      clean: true,
      override: {
        operationName: (operation, route, verb) => {
          const name = operation.operationId ?? `${verb}${route}`;
          const base = operation.tags?.includes('workspace-dashboard') ? name : name.split('_v1_')[0];
          return base.replace(/_+([a-z0-9])/gi, (_, letter: string) => letter.toUpperCase());
        },
        mutator: {path: './src/api/client.ts', name: 'gatewayFetch'},
        fetch: {includeHttpResponseReturnType: false},
        query: {signal: true},
      },
    },
  },
});
