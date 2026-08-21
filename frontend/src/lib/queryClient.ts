import { QueryClient } from '@tanstack/react-query'

// Shared instance — needs to be importable from both App.tsx (provider) and
// lib/api.ts (the 401 interceptor calls queryClient.clear() on logout/session
// expiry, so a second person logging in on the same shared browser doesn't
// briefly see the previous user's cached financial data).
export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
})
