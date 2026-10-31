import { useState } from 'react'
import { X } from 'lucide-react'
import { Input, Button, useEscapeKey } from '@/components/ui'
import UsersAccess from '@/components/UsersAccess'
import { useRole, ROLE_LABEL, ROLE_HELP } from '@/lib/auth'
import { changePassword } from '@/lib/api'

export default function AccountModal({ onClose }: { onClose: () => void }) {
  useEscapeKey(onClose)
  const { role, isAdmin, username } = useRole()

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

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[92dvh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold">Account</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
        </div>

        <div className="px-5 py-4 space-y-6">
          <p className="text-xs text-slate-500">Signed in as <b className="text-slate-700">{username}</b> · {ROLE_LABEL[role]} — {ROLE_HELP[role]}</p>
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

          {isAdmin && (
            <div className="border-t border-slate-100 pt-4">
              <UsersAccess />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
