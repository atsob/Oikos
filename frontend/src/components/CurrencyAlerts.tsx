import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2, Pencil } from 'lucide-react'
import { Button, Input, Spinner } from '@/components/ui'
import { useLiveRefetchInterval, useSettings } from '@/lib/hooks'
import { fmtNum } from '@/lib/utils'
import { getAlertsDefinitions, saveAlert, toggleAlert, deleteAlert, getCurrencies } from '@/lib/api'

// Alerts on one currency's rate (Currency Detail → Alerts): "tell me when GBP/EUR goes above 1.21". The rate is units of the second currency per 1 unit of the first — the
// one Currency Detail's Overview shows. They are checked with every other alert (Dashboard, Alerts page, Telegram); Market Data → Alerts lists them all with the security ones.
type Row = Record<string, unknown>
const isFx = (r: Row) => r.alert_type === 'fx_above' || r.alert_type === 'fx_below'
const errMsg = (e: unknown) => (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? (e instanceof Error ? e.message : 'Error')

export default function CurrencyAlerts({ curId, code }: { curId: number; code: string }) {
  const qc = useQueryClient()
  const liveRefetchMs = useLiveRefetchInterval()
  const [{ reportingCurrency }] = useSettings()
  const { data = [], isLoading } = useQuery({ queryKey: ['alert-definitions'], queryFn: getAlertsDefinitions, refetchInterval: liveRefetchMs })
  const { data: currenciesRaw = [] } = useQuery({ queryKey: ['currencies'], queryFn: getCurrencies })
  const currencies = currenciesRaw as Row[]
  const others = currencies.filter(c => Number(c.id) !== curId)
  const defaultQuote = String((others.find(c => String(c.code) === reportingCurrency) ?? others[0])?.id ?? '')
  const rows = (data as Row[]).filter(r => isFx(r) && Number(r.currencies_id) === curId)

  const [show, setShow] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)
  const [form, setForm] = useState({ alert_type: 'fx_above', quote: '', threshold: '', note: '' })
  const [err, setErr] = useState('')

  const done = () => { qc.invalidateQueries({ queryKey: ['alert-definitions'] }); qc.invalidateQueries({ queryKey: ['triggered-alerts'] }) }
  const saveMut = useMutation({ mutationFn: saveAlert, onSuccess: () => { done(); setShow(false); setEditId(null); setErr('') }, onError: e => setErr(errMsg(e)) })
  const toggleMut = useMutation({ mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) => toggleAlert(id, is_active), onSuccess: done })
  const deleteMut = useMutation({ mutationFn: deleteAlert, onSuccess: done })

  const openAdd = () => { setForm({ alert_type: 'fx_above', quote: defaultQuote, threshold: '', note: '' }); setEditId(null); setErr(''); setShow(true) }
  const openEdit = (r: Row) => {
    setForm({ alert_type: String(r.alert_type), quote: String(r.currencies_id_quote ?? defaultQuote), threshold: String(r.threshold ?? ''), note: String(r.note ?? '') })
    setEditId(Number(r.alert_id)); setErr(''); setShow(true)
  }
  const quoteCode = (id: unknown) => String(currencies.find(c => Number(c.id) === Number(id))?.code ?? '')
  const doSave = () => {
    if (!form.quote) return setErr('Choose the currency the rate is quoted in')
    if (!form.threshold) return setErr('The rate threshold is required')
    saveMut.mutate({
      alert_id: editId ?? undefined, alert_type: form.alert_type, securities_id: null, asset_type: null, threshold: Number(form.threshold),
      direction: form.alert_type === 'fx_above' ? 'above' : 'below', note: form.note || null, currencies_id: curId, currencies_id_quote: Number(form.quote),
    })
  }

  if (isLoading) return <div className="flex justify-center py-12"><Spinner /></div>
  const q = quoteCode(form.quote)
  return (
    <div className="p-3 sm:p-4 space-y-4">
      <div className="flex justify-between items-center gap-3">
        <p className="text-sm text-slate-500">{rows.length} rate alert{rows.length !== 1 ? 's' : ''} on {code} — checked with the other alerts (Dashboard, Alerts page, Telegram).</p>
        <Button size="sm" onClick={openAdd}><Plus size={14} /> Add Alert</Button>
      </div>

      {show && (
        <div className="border border-slate-200 rounded-lg p-4 space-y-3 bg-slate-50">
          <p className="text-sm font-semibold text-slate-700">{editId ? 'Edit rate alert' : 'New rate alert'}</p>
          {err && <p className="text-xs text-red-600">{err}</p>}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">When the rate goes *</label>
              <select className="block w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm" value={form.alert_type} onChange={e => setForm(f => ({ ...f, alert_type: e.target.value }))}>
                <option value="fx_above">▲ Above the threshold</option>
                <option value="fx_below">▼ Below the threshold</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Quoted in *</label>
              <select className="block w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm" value={form.quote} onChange={e => setForm(f => ({ ...f, quote: e.target.value }))}>
                {others.map(c => <option key={String(c.id)} value={String(c.id)}>{String(c.code)}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-500 mb-1">Rate threshold ({q ? `${q} per 1 ${code}` : '…'}) *</label>
              <Input type="number" step="any" value={form.threshold} onChange={e => setForm(f => ({ ...f, threshold: e.target.value }))} placeholder="e.g. 1.21" />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-500 mb-1">Note</label>
            <Input value={form.note} onChange={e => setForm(f => ({ ...f, note: e.target.value }))} placeholder={`optional — e.g. Sell ${code}`} />
          </div>
          <p className="text-xs text-slate-400">{code}/{q || '…'} is how many {q || 'units of the other currency'} 1 {code} buys, the rate on this page's Overview. The alert fires while the latest stored rate is beyond the threshold, and again if it crosses back and over once more.</p>
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="secondary" onClick={() => { setShow(false); setErr('') }}>Cancel</Button>
            <Button size="sm" onClick={doSave} disabled={saveMut.isPending}>Save</Button>
          </div>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="bg-slate-50 text-xs text-slate-500 border-b border-slate-200">
            <th className="px-3 py-2 text-left font-medium">Status</th>
            <th className="px-3 py-2 text-left font-medium">Pair</th>
            <th className="px-3 py-2 text-left font-medium">When</th>
            <th className="px-3 py-2 text-right font-medium">Threshold</th>
            <th className="px-3 py-2 text-right font-medium">Current rate</th>
            <th className="px-3 py-2 text-left font-medium">Note</th>
            <th className="px-3 py-2"></th>
          </tr></thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map(r => {
              const active = Boolean(r.is_active)
              const above = r.alert_type === 'fx_above'
              const cur = r.current_price != null ? Number(r.current_price) : null
              const thr = r.threshold != null ? Number(r.threshold) : null
              const triggered = cur != null && thr != null && (above ? cur > thr : cur < thr)
              return (
                <tr key={String(r.alert_id)} className={`hover:bg-slate-50 ${!active ? 'opacity-50' : ''}`} onDoubleClick={() => openEdit(r)}>
                  <td className="px-3 py-2">
                    <button onClick={() => toggleMut.mutate({ id: Number(r.alert_id), is_active: !active })} className={`w-8 h-4 rounded-full transition-colors ${active ? 'bg-blue-500' : 'bg-slate-300'} relative`}>
                      <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${active ? 'left-4.5' : 'left-0.5'}`} />
                    </button>
                    {triggered && <span className="ml-1.5 text-[10px] bg-red-100 text-red-700 px-1.5 py-0.5 rounded-full font-medium">🔔 triggered</span>}
                  </td>
                  <td className="px-3 py-2 font-medium">{String(r.securities_name ?? `${code}/${quoteCode(r.currencies_id_quote)}`)}</td>
                  <td className="px-3 py-2"><span className={`text-xs px-2 py-0.5 rounded-full font-medium ${above ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>{above ? '▲ Above' : '▼ Below'}</span></td>
                  <td className="px-3 py-2 text-right tabular-nums font-medium">{thr != null ? fmtNum(thr, 4) : '—'}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${triggered ? 'text-red-600 font-semibold' : 'text-slate-600'}`}>{cur != null ? fmtNum(cur, 4) : '—'}</td>
                  <td className="px-3 py-2 text-slate-400 text-xs">{r.note != null ? String(r.note) : ''}</td>
                  <td className="px-3 py-2">
                    <div className="flex gap-1">
                      <button onClick={() => openEdit(r)} className="p-1 text-slate-400 hover:text-blue-600"><Pencil size={13} /></button>
                      <button onClick={() => deleteMut.mutate(Number(r.alert_id))} className="p-1 text-slate-400 hover:text-red-600"><Trash2 size={13} /></button>
                    </div>
                  </td>
                </tr>
              )
            })}
            {rows.length === 0 && <tr><td colSpan={7} className="px-3 py-8 text-center text-slate-400 text-sm">No rate alerts on {code} yet — add one, for example to sell {code} when its rate reaches a level.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
