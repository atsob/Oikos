import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { X, Trash2, UserPlus } from 'lucide-react'
import { Input, Button, useEscapeKey, Spinner } from '@/components/ui'
import { fmtDate } from '@/lib/utils'
import { changePassword, getUsers, addUser, deleteUser, getMe } from '@/lib/api'

export default function AccountModal({ onClose }: { onClose: () => void }) {
  useEscapeKey(onClose)
  const qc = useQueryClient()
  const { data: me } = useQuery({ queryKey: ['auth-me'], queryFn: getMe })
  const { data: users = [], isLoading: usersLoading } = useQuery({ queryKey: ['auth-users'], queryFn: getUsers })

  // Change password
  const [currentPw, setCurrentPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [pwSaving, setPwSaving] = useState(false)
  const [pwError, setPwError] = useState<string | null>(null)
  const [pwSuccess, setPwSuccess] = useState(false)

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault()
    setPwError(null); setPwSuccess(false)
    if (newPw !== confirmPw) { setPwError("New passwords don't match"); return }
    setPwSaving(true)
    try {
      await changePassword(currentPw, newPw)
      setPwSuccess(true)
      setCurrentPw(''); setNewPw(''); setConfirmPw('')
    } catch (err: unknown) {
      const axiosMsg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setPwError(axiosMsg ?? 'Failed to change password')
    } finally {
      setPwSaving(false)
    }
  }

  // Add user
  const [newUsername, setNewUsername] = useState('')
  const [newUserPw, setNewUserPw] = useState('')
  const [addSaving, setAddSaving] = useState(false)
  const [addError, setAddError] = useState<string | null>(null)

  const handleAddUser = async (e: React.FormEvent) => {
    e.preventDefault()
    setAddError(null)
    setAddSaving(true)
    try {
      await addUser(newUsername, newUserPw)
      setNewUsername(''); setNewUserPw('')
      qc.invalidateQueries({ queryKey: ['auth-users'] })
    } catch (err: unknown) {
      const axiosMsg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setAddError(axiosMsg ?? 'Failed to add user')
    } finally {
      setAddSaving(false)
    }
  }

  const [deleteError, setDeleteError] = useState<string | null>(null)
  const handleDelete = async (usersId: number, username: string) => {
    if (!window.confirm(`Remove the "${username}" account? They'll be logged out immediately.`)) return
    setDeleteError(null)
    try {
      await deleteUser(usersId)
      qc.invalidateQueries({ queryKey: ['auth-users'] })
    } catch (err: unknown) {
      const axiosMsg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setDeleteError(axiosMsg ?? 'Failed to remove user')
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[92vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold">Account</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
        </div>

        <div className="px-5 py-4 space-y-6">
          {/* Change password */}
          <div>
            <h3 className="text-sm font-semibold text-slate-700 mb-2">Change my password</h3>
            <form onSubmit={handleChangePassword} className="space-y-2">
              <div>
                <label className="text-xs font-medium text-slate-500 block mb-1">Current password</label>
                <Input type="password" value={currentPw} onChange={e => setCurrentPw(e.target.value)} required />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="text-xs font-medium text-slate-500 block mb-1">New password</label>
                  <Input type="password" value={newPw} onChange={e => setNewPw(e.target.value)} required minLength={8} />
                </div>
                <div>
                  <label className="text-xs font-medium text-slate-500 block mb-1">Confirm new password</label>
                  <Input type="password" value={confirmPw} onChange={e => setConfirmPw(e.target.value)} required minLength={8} />
                </div>
              </div>
              {pwError && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{pwError}</p>}
              {pwSuccess && <p className="text-xs text-green-700 bg-green-50 rounded px-3 py-2">Password changed.</p>}
              <Button type="submit" size="sm" disabled={pwSaving}>{pwSaving ? 'Saving…' : 'Change password'}</Button>
            </form>
          </div>

          <div className="border-t border-slate-100 pt-4">
            <h3 className="text-sm font-semibold text-slate-700 mb-2">Users</h3>
            {usersLoading ? (
              <div className="flex justify-center py-4"><Spinner /></div>
            ) : (
              <div className="space-y-1 mb-3">
                {users.map(u => (
                  <div key={u.users_id} className="flex items-center justify-between text-sm px-2 py-1.5 rounded hover:bg-slate-50">
                    <div>
                      <span className="font-medium text-slate-800">{u.username}</span>
                      {u.username === me?.username && <span className="text-xs text-slate-400 ml-1.5">(you)</span>}
                      <span className="text-xs text-slate-400 ml-2">since {fmtDate(u.created_at)}</span>
                    </div>
                    <button
                      onClick={() => handleDelete(u.users_id, u.username)}
                      disabled={u.username === me?.username}
                      title={u.username === me?.username ? "Can't remove your own account while logged in" : 'Remove'}
                      className="text-slate-300 hover:text-red-500 disabled:opacity-30 disabled:hover:text-slate-300"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            {deleteError && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2 mb-2">{deleteError}</p>}

            <form onSubmit={handleAddUser} className="flex items-end gap-2">
              <div className="flex-1">
                <label className="text-xs font-medium text-slate-500 block mb-1">Username</label>
                <Input value={newUsername} onChange={e => setNewUsername(e.target.value)} required />
              </div>
              <div className="flex-1">
                <label className="text-xs font-medium text-slate-500 block mb-1">Password</label>
                <Input type="password" value={newUserPw} onChange={e => setNewUserPw(e.target.value)} required minLength={8} />
              </div>
              <Button type="submit" size="sm" variant="secondary" disabled={addSaving}>
                <UserPlus size={13} /> Add
              </Button>
            </form>
            {addError && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2 mt-2">{addError}</p>}
          </div>
        </div>
      </div>
    </div>
  )
}
