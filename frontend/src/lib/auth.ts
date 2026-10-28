import { useQuery } from '@tanstack/react-query'
import { getMe } from '@/lib/api'

// Who is signed in and what they may do (api/access.py): an admin sees and changes everything; a limited user reads and changes only the accounts mapped to them;
// a read-only user reads those accounts and changes nothing. The server enforces it; the screens use this to hide what a role cannot use.
export type Role = 'admin' | 'limited' | 'readonly'

export function useRole() {
  const { data, isLoading } = useQuery({ queryKey: ['auth-me'], queryFn: getMe, staleTime: 5 * 60_000 })
  const role: Role = data?.role ?? 'admin'
  return { role, isAdmin: role === 'admin', canWrite: role !== 'readonly', readOnly: role === 'readonly', accounts: data?.accounts ?? null, username: data?.username, isLoading }
}

export const ROLE_LABEL: Record<Role, string> = { admin: 'Admin', limited: 'Limited', readonly: 'Read-only' }
export const ROLE_HELP: Record<Role, string> = {
  admin: 'Everything, on every account.',
  limited: 'Reads and changes only the accounts mapped to the user.',
  readonly: 'Reads only the accounts mapped to the user; cannot change anything.',
}
