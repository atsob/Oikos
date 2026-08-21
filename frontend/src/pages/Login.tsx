import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Card, CardBody, Input, Button } from '@/components/ui'
import { login, getMe } from '@/lib/api'

export default function Login() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  // Already logged in (e.g. navigated here directly with a live session) — skip the form.
  const { data: me } = useQuery({ queryKey: ['auth-me'], queryFn: getMe, retry: false })
  useEffect(() => { if (me) navigate('/', { replace: true }) }, [me, navigate])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await login(username, password)
      await qc.invalidateQueries({ queryKey: ['auth-me'] })
      navigate('/', { replace: true })
    } catch (err: unknown) {
      const axiosMsg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(axiosMsg ?? 'Login failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4">
      <Card className="w-full max-w-sm">
        <CardBody className="space-y-5">
          <div className="flex flex-col items-center gap-2 text-center">
            <img src="/logo.png" alt="A²360 Consulting" className="w-10 h-10 object-contain" />
            <div>
              <div className="text-lg font-bold tracking-tight text-slate-900">Oikos</div>
              <div className="text-xs text-slate-500">Finance</div>
            </div>
          </div>

          <form onSubmit={handleSubmit} className="space-y-3">
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Username</label>
              <Input value={username} onChange={e => setUsername(e.target.value)} autoFocus required />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Password</label>
              <Input type="password" value={password} onChange={e => setPassword(e.target.value)} required />
            </div>
            {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{error}</p>}
            <Button type="submit" className="w-full justify-center" disabled={submitting}>
              {submitting ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>
        </CardBody>
      </Card>
    </div>
  )
}
