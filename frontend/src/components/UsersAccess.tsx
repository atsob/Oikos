import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, UserPlus, Pencil } from 'lucide-react'
import { Button, Input, Select, Spinner, ACCOUNT_TYPE_ORDER } from '@/components/ui'
import { fmtDate } from '@/lib/utils'
import { ROLE_HELP, ROLE_LABEL, type Role } from '@/lib/auth'
import { addUser, deleteUser, getAccounts, getMe, getPortfolioPresets, getUsers, updateUserAccess } from '@/lib/api'

// Users and what they may use (admins only): a role for each, and — for a limited or read-only user — the accounts they can open. Server side: api/access.py.
type Row = Record<string, unknown>
const errMsg = (e: unknown, fallback: string) => (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? fallback

function AccountPicker({ value, onChange }: { value: number[]; onChange: (ids: number[]) => void }) {
  const { data: accounts = [] } = useQuery({ queryKey: ['allAccountsForPreset'], queryFn: () => getAccounts() })
  const { data: presets = [] } = useQuery({ queryKey: ['portfolio-presets', 'net_worth'], queryFn: () => getPortfolioPresets('net_worth') })
  const grouped = useMemo(() => {
    const by = new Map<string, Row[]>()
    for (const a of accounts as Row[]) {
      if (a.is_active === false || a.is_active === 0) continue
      const t = String(a.type ?? 'Other')
      by.set(t, [...(by.get(t) ?? []), a])
    }
    const types = [...ACCOUNT_TYPE_ORDER.filter(t => by.has(t)), ...[...by.keys()].filter(t => !ACCOUNT_TYPE_ORDER.includes(t))]
    return types.map(t => [t, by.get(t)!] as const)
  }, [accounts])
  const set = new Set(value)
  const toggle = (id: number) => onChange(set.has(id) ? value.filter(x => x !== id) : [...value, id])
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
        <span>{value.length} account{value.length === 1 ? '' : 's'} selected</span>
        <Select className="!w-auto !py-1 text-xs" value="" onChange={e => {
          const p = (presets as { preset_name: string; account_ids: number[] }[]).find(x => x.preset_name === e.target.value)
          if (p) onChange([...new Set([...value, ...(p.account_ids ?? [])])])
        }}>
          <option value="">+ Add the accounts of a preset…</option>
          {(presets as { preset_id: number; preset_name: string }[]).map(p => <option key={p.preset_id} value={p.preset_name}>{p.preset_name}</option>)}
        </Select>
        {value.length > 0 && <button type="button" className="text-slate-400 hover:text-slate-600 underline" onClick={() => onChange([])}>Clear</button>}
      </div>
      <div className="max-h-52 overflow-y-auto border border-slate-200 rounded">
        {grouped.map(([type, list]) => (
          <div key={type}>
            <div className="px-3 py-1 text-xs font-semibold text-slate-500 bg-slate-50 sticky top-0">{type}</div>
            {list.map(a => (
              <label key={String(a.id)} className="flex items-center gap-2 px-3 py-1 text-xs hover:bg-slate-50 cursor-pointer">
                <input type="checkbox" checked={set.has(Number(a.id))} onChange={() => toggle(Number(a.id))} /> {String(a.name)}
              </label>
            ))}
          </div>
        ))}
      </div>
      <p className="text-xs text-slate-400">An account linked to a selected one (a brokerage's cash account) is included automatically.</p>
    </div>
  )
}

export default function UsersAccess() {
  const qc = useQueryClient()
  const { data: me } = useQuery({ queryKey: ['auth-me'], queryFn: getMe })
  const { data: users = [], isLoading } = useQuery({ queryKey: ['auth-users'], queryFn: getUsers })
  const { data: accounts = [] } = useQuery({ queryKey: ['allAccountsForPreset'], queryFn: () => getAccounts() })
  const nameOf = useMemo(() => new Map((accounts as Row[]).map(a => [Number(a.id), String(a.name)])), [accounts])
  const [form, setForm] = useState<{ id: number | null; username: string; password: string; role: Role; accountIds: number[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const save = async () => {
    if (!form) return
    setError(null); setSaving(true)
    try {
      if (form.id == null) await addUser(form.username.trim(), form.password, form.role, form.accountIds)
      else await updateUserAccess(form.id, { role: form.role, account_ids: form.accountIds, new_password: form.password || undefined })
      setForm(null)
      qc.invalidateQueries({ queryKey: ['auth-users'] })
    } catch (e) { setError(errMsg(e, 'Could not save the user.')) } finally { setSaving(false) }
  }
  const remove = async (id: number, username: string) => {
    if (!window.confirm(`Remove the "${username}" account? They'll be logged out immediately.`)) return
    setError(null)
    try { await deleteUser(id); qc.invalidateQueries({ queryKey: ['auth-users'] }) } catch (e) { setError(errMsg(e, 'Failed to remove user')) }
  }

  return (
    <div>
      <h3 className="text-sm font-semibold text-slate-700 mb-1">Users and access</h3>
      <p className="text-xs text-slate-400 mb-2">
        <b>Admin</b> — {ROLE_HELP.admin} <b>Limited</b> — {ROLE_HELP.limited} <b>Read-only</b> — {ROLE_HELP.readonly} Restricted users get the Cash Register and Investments of their accounts; the rest is admin-only.
      </p>
      {isLoading ? <div className="flex justify-center py-4"><Spinner /></div> : (
        <div className="space-y-1 mb-3">
          {users.map(u => {
            const ids = u.account_ids ?? []
            return (
              <div key={u.users_id} className="flex items-start justify-between text-sm px-2 py-1.5 rounded hover:bg-slate-50 gap-2">
                <div className="min-w-0">
                  <span className="font-medium text-slate-800">{u.username}</span>
                  {u.username === me?.username && <span className="text-xs text-slate-400 ml-1.5">(you)</span>}
                  <span className={`text-xs ml-2 px-1.5 py-0.5 rounded ${u.role === 'admin' ? 'bg-blue-50 text-blue-700' : u.role === 'limited' ? 'bg-amber-50 text-amber-700' : 'bg-slate-100 text-slate-600'}`}>{ROLE_LABEL[u.role]}</span>
                  <span className="text-xs text-slate-400 ml-2">since {fmtDate(u.created_at)}</span>
                  {u.role !== 'admin' && <p className="text-xs text-slate-400 truncate" title={ids.map(i => nameOf.get(i) ?? i).join(', ')}>{ids.length ? ids.map(i => nameOf.get(i) ?? i).join(', ') : 'no accounts'}</p>}
                </div>
                <div className="flex gap-2 shrink-0">
                  <button title="Edit role and accounts" className="text-slate-400 hover:text-blue-600"
                    onClick={() => { setForm({ id: u.users_id, username: u.username, password: '', role: u.role, accountIds: ids }); setError(null) }}><Pencil size={14} /></button>
                  <button onClick={() => remove(u.users_id, u.username)} disabled={u.username === me?.username} title={u.username === me?.username ? "Can't remove your own account while logged in" : 'Remove'}
                    className="text-slate-300 hover:text-red-500 disabled:opacity-30 disabled:hover:text-slate-300"><Trash2 size={14} /></button>
                </div>
              </div>
            )
          })}
        </div>
      )}
      {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2 mb-2">{error}</p>}

      {form ? (
        <div className="border border-slate-200 rounded-lg p-3 space-y-2 bg-slate-50">
          <p className="text-xs font-semibold text-slate-600">{form.id == null ? 'New user' : `Edit ${form.username}`}</p>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-slate-500">Username<Input value={form.username} disabled={form.id != null} onChange={e => setForm({ ...form, username: e.target.value })} /></label>
            <label className="text-xs text-slate-500">{form.id == null ? 'Password' : 'New password (optional)'}<Input type="password" minLength={8} value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></label>
          </div>
          <label className="text-xs text-slate-500 block">Role
            <Select value={form.role} onChange={e => setForm({ ...form, role: e.target.value as Role })}>
              {(['admin', 'limited', 'readonly'] as Role[]).map(r => <option key={r} value={r}>{ROLE_LABEL[r]} — {ROLE_HELP[r]}</option>)}
            </Select>
          </label>
          {form.role !== 'admin' && <AccountPicker value={form.accountIds} onChange={ids => setForm({ ...form, accountIds: ids })} />}
          <div className="flex gap-2">
            <Button size="sm" disabled={saving || (form.id == null && (!form.username.trim() || form.password.length < 8)) || (form.role !== 'admin' && form.accountIds.length === 0)} onClick={save}>{saving ? 'Saving…' : 'Save'}</Button>
            <Button size="sm" variant="secondary" onClick={() => setForm(null)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <Button size="sm" variant="secondary" onClick={() => { setForm({ id: null, username: '', password: '', role: 'limited', accountIds: [] }); setError(null) }}><UserPlus size={13} /> Add user</Button>
      )}
    </div>
  )
}
