import { QueryClient } from '@tanstack/vue-query'
import { ApiError } from '@/services/http'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000, // API caching: reuse for 30s, background refetch after
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      // Don't retry client errors (4xx); retry transient network/5xx twice.
      retry: (n, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && n < 2,
    },
  },
})
