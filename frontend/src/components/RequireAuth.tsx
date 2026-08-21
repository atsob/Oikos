import { Navigate, Outlet } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Spinner } from '@/components/ui'
import { getMe } from '@/lib/api'

// Wraps the main <Route element={<Layout/>}> subtree in App.tsx — probes
// GET /api/auth/me on every mount and redirects to /login if no valid session
// cookie exists. retry:false so a 401 resolves immediately instead of retrying.
export default function RequireAuth() {
  const { data, isLoading, isError } = useQuery({ queryKey: ['auth-me'], queryFn: getMe, retry: false })

  if (isLoading) {
    return <div className="min-h-screen flex items-center justify-center"><Spinner size={28} /></div>
  }
  if (isError || !data) {
    return <Navigate to="/login" replace />
  }
  return <Outlet />
}
