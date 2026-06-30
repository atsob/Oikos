import { useEffect } from 'react'
import { getFxRates, getLinkedAccount } from '@/lib/api'
import { api } from '@/lib/api'
import { Input, Button, useEscapeKey } from '@/components/ui'
import { X, Save } from 'lucide-react'

export const ACTIONS = [
  'Buy', 'Sell', 'Dividend', 'Reinvest', 'Split',
  'ShrIn', 'ShrOut', 'IntInc', 'CashIn', 'CashOut',
  'Grant', 'Vest', 'Exercise', 'Expire', 'MiscExp', 'MiscInc', 'RtrnCap',
]

export const INSTRUMENT_TYPES = [
  '', 'Stock', 'ETF', 'Bond', 'CFD', 'CEF', 'CFDOnETF', 'CFDOnStock',
  'CFDOnIndex', 'CFDOnFutures', 'CFDOnFund', 'Fund', 'Option', 'FX Spot', 'Other',
]

export const CASH_ACTIONS = new Set(['Buy', 'Sell', 'Dividend', 'IntInc', 'RtrnCap', 'MiscExp', 'MiscInc', 'CashOut', 'CashIn'])

const TAX_ACTIONS = new Set(['Dividend', 'IntInc', 'RtrnCap'])

export interface InvFormData {
  accounts_id: string
  securities_id: string
  date: string
  action: string
  quantity: string
  price_per_share: string
  commission: string
  fx_rate: string
  total_amount_acccur: string
  total_amount_seccur: string
  tax_amount: string
  instrument_type: string
  description: string
  cash_account_id: string
}

export const emptyInvForm = (): InvFormData => ({
  accounts_id: '',
  securities_id: '',
  date: new Date().toISOString().slice(0, 10),
  action: 'Buy',
  quantity: '',
  price_per_share: '',
  commission: '0',
  fx_rate: '1',
  total_amount_acccur: '',
  total_amount_seccur: '',
  tax_amount: '',
  instrument_type: '',
  description: '',
  cash_account_id: '',
})

export const createInvestment = (data: Record<string, unknown>) =>
  api.post('/investments/transactions', data).then(r => r.data)
export const updateInvestment = (id: number, data: Record<string, unknown>) =>
  api.put(`/investments/transactions/${id}`, data).then(r => r.data)
export const deleteInvestment = (id: number) =>
  api.delete(`/investments/transactions/${id}`).then(r => r.data)

export function InvTransactionModal({ form, onChange, accounts, allAccounts, securities, onSave, onDelete, onClose, saving, error, editId }: {
  form: InvFormData
  onChange: (f: InvFormData) => void
  accounts: Record<string, unknown>[]
  allAccounts: Record<string, unknown>[]
  securities: Record<string, unknown>[]
  onSave: () => void
  onDelete?: () => void
  onClose: () => void
  saving: boolean
  error: string | null
  editId: number | null
}) {
  useEscapeKey(onClose)
  const set = (k: keyof InvFormData, v: string) => onChange({ ...form, [k]: v })

  useEffect(() => {
    if (!form.date || !form.securities_id || !form.accounts_id) return
    const sec = securities.find(s => String(s.id) === form.securities_id)
    const acc = accounts.find(a => String(a.id) === form.accounts_id)
    if (!sec || !acc) return
    const secCurrency = String(sec.currency ?? '')
    const accCurrency = String(acc.currency ?? '')
    if (!secCurrency || !accCurrency || secCurrency === accCurrency) {
      onChange({ ...form, fx_rate: '1' })
      return
    }
    getFxRates(undefined, '2015-01-01').then((rows: { date: string; currency: string; rate: number }[]) => {
      const onOrBefore = (currency: string) => {
        const filtered = rows.filter(r => r.currency === currency && r.date <= form.date)
        return filtered.length ? filtered[filtered.length - 1].rate : null
      }
      // rates stored as "EUR per 1 unit of X"; fx_rate = accCurrency per 1 secCurrency
      let fx: number | null = null
      if (secCurrency === 'EUR') {
        const rAcc = onOrBefore(accCurrency)
        if (rAcc) fx = 1 / rAcc
      } else if (accCurrency === 'EUR') {
        const rSec = onOrBefore(secCurrency)
        if (rSec) fx = rSec
      } else {
        const rSec = onOrBefore(secCurrency)
        const rAcc = onOrBefore(accCurrency)
        if (rSec && rAcc) fx = rSec / rAcc
      }
      if (fx !== null) onChange({ ...form, fx_rate: fx.toFixed(6) })
    }).catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.date, form.securities_id, form.accounts_id])

  const onAccountChange = (v: string) => {
    const next = { ...form, accounts_id: v }
    if (v) {
      getLinkedAccount(Number(v)).then(r => {
        onChange({ ...next, cash_account_id: r.linked_account_id ? String(r.linked_account_id) : '' })
      }).catch(() => onChange(next))
    } else {
      onChange({ ...next, cash_account_id: '' })
    }
  }

  const autoCalc = (key: keyof InvFormData, val: string) => {
    const next = { ...form, [key]: val }
    const qty = parseFloat(next.quantity) || 0
    const price = parseFloat(next.price_per_share) || 0
    const comm = parseFloat(next.commission) || 0
    const fx = parseFloat(next.fx_rate) || 1
    if (qty && price) {
      const isIncome = ['Dividend', 'Reinvest', 'IntInc', 'ShrIn', 'MiscInc', 'RtrnCap'].includes(next.action)
      const baseSec = qty * price
      const totalSec = isIncome ? baseSec : baseSec + comm
      next.total_amount_seccur = totalSec.toFixed(8)
      next.total_amount_acccur = (totalSec * fx).toFixed(2)
    }
    onChange(next)
  }

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[92vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold">{editId ? 'Edit Investment Transaction' : 'New Investment Transaction'}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
        </div>

        <div className="px-5 py-4 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Date *</label>
              <Input type="date" value={form.date} onChange={e => set('date', e.target.value)} />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Action *</label>
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.action} onChange={e => set('action', e.target.value)}>
                {ACTIONS.map(a => <option key={a}>{a}</option>)}
              </select>
            </div>
          </div>

          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">Account *</label>
            <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.accounts_id} onChange={e => onAccountChange(e.target.value)}>
              <option value="">— select —</option>
              {accounts.map(a => <option key={String(a.id)} value={String(a.id)}>{String(a.name)}</option>)}
            </select>
          </div>

          {CASH_ACTIONS.has(form.action) && (
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Cash Account (linked transaction)</label>
              <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.cash_account_id} onChange={e => set('cash_account_id', e.target.value)}>
                <option value="">— none —</option>
                {allAccounts.map(a => <option key={String(a.id)} value={String(a.id)}>{String(a.name)}</option>)}
              </select>
            </div>
          )}

          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">Security *</label>
            <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.securities_id} onChange={e => set('securities_id', e.target.value)}>
              <option value="">— select —</option>
              {securities.map(s => <option key={String(s.id)} value={String(s.id)}>{String(s.ticker ?? '')} · {String(s.name)}</option>)}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Quantity</label>
              <Input type="number" step="any" value={form.quantity} onChange={e => autoCalc('quantity', e.target.value)} placeholder="0" />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Price per Share</label>
              <Input type="number" step="any" value={form.price_per_share} onChange={e => autoCalc('price_per_share', e.target.value)} placeholder="0.00" />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Commission</label>
              <Input type="number" step="any" value={form.commission} onChange={e => autoCalc('commission', e.target.value)} placeholder="0" />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">FX Rate</label>
              <Input type="number" step="any" value={form.fx_rate} onChange={e => autoCalc('fx_rate', e.target.value)} placeholder="1" />
            </div>
          </div>

          {TAX_ACTIONS.has(form.action) && (
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">
                Withholding Tax <span className="text-slate-400 font-normal">(acc. currency, negative — e.g. −15.00)</span>
              </label>
              <Input
                type="number" step="any"
                value={form.tax_amount}
                onChange={e => set('tax_amount', e.target.value)}
                placeholder="0.00"
              />
              {form.tax_amount && form.total_amount_acccur && (
                <p className="text-xs text-slate-400 mt-1">
                  Net received: {(parseFloat(form.total_amount_acccur) + parseFloat(form.tax_amount || '0')).toFixed(2)}
                </p>
              )}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Total (sec. currency)</label>
              <Input type="number" step="any" value={form.total_amount_seccur} onChange={e => set('total_amount_seccur', e.target.value)} placeholder="0.00" />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Total (acc. currency)</label>
              <Input type="number" step="any" value={form.total_amount_acccur} onChange={e => {
                const totalAcc = parseFloat(e.target.value)
                const totalSec = parseFloat(form.total_amount_seccur)
                const next = { ...form, total_amount_acccur: e.target.value }
                if (totalAcc && totalSec) next.fx_rate = (totalAcc / totalSec).toFixed(6)
                onChange(next)
              }} placeholder="0.00" />
            </div>
          </div>

          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">Instrument Type</label>
            <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.instrument_type} onChange={e => set('instrument_type', e.target.value)}>
              {INSTRUMENT_TYPES.map(t => <option key={t} value={t}>{t || '— none —'}</option>)}
            </select>
          </div>

          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">Notes</label>
            <Input value={form.description} onChange={e => set('description', e.target.value)} placeholder="Notes / description" />
          </div>

          {error && <p className="text-xs text-red-600 bg-red-50 rounded px-3 py-2">{error}</p>}
        </div>

        <div className="flex items-center justify-between px-5 py-3 border-t border-slate-200">
          <div>{editId && onDelete && <Button variant="destructive" size="sm" onClick={onDelete} disabled={saving}>Delete</Button>}</div>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onClose}>Cancel</Button>
            <Button onClick={onSave} disabled={saving}><Save size={14} /> {saving ? 'Saving…' : 'Save'}</Button>
          </div>
        </div>
      </div>
    </div>
  )
}
