import {defineConfig} from 'orval';

export default defineConfig({
  workspace: {
    input: {
      target: './openapi.json',
      filters: {mode: 'include', tags: ['workspace-dashboard']},
    },
    output: {
      target: './src/api/generated/workspace.ts',
      mode: 'single',
      client: 'react-query',
      httpClient: 'fetch',
      clean: true,
      override: {
        mutator: {path: './src/api/client.ts', name: 'gatewayFetch'},
        fetch: {includeHttpResponseReturnType: false},
        query: {signal: true},
      },
    },
  },
});
