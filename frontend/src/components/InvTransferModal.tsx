import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getHoldings, getSecurities, previewInvestmentTransfer, executeInvestmentTransfer } from '@/lib/api'
import { Button, Select, Input, SearchableSelect, Spinner, useEscapeKey, AccountOptions } from '@/components/ui'
import { fmtCur, fmtQty } from '@/lib/utils'
import { X, ArrowRight, AlertTriangle } from 'lucide-react'
import { INVESTMENT_ACCOUNT_TYPES } from '@/pages/Investments'

type FeeType = 'none' | 'source' | 'destination' | 'cash'

type PreviewRow = {
  accounts_id: number; account_name: string; account_currency: string
  securities_id: number; security_name: string; security_ticker: string; security_currency: string
  action: string; quantity: number; price_per_share: number; total_amount_acccur: number
  description: string; estimated_pnl: number | null; estimated_pnl_currency: string | null
}
type PreviewResult = {
  rows: PreviewRow[]
  cash_fee: { accounts_id: number; account_name: string; account_currency: string; amount: number; description: string } | null
  from_account: string; to_account: string; from_security: string; to_security: string
  is_conversion: boolean
}

function today() { return new Date().toISOString().slice(0, 10) }

/**
 * Moves a holding from one account to another — same security (a pure custody
 * transfer, cost basis carried over, no gain/loss) or a different security (a
 * conversion/swap that realizes gain/loss on the source and establishes a
 * fresh cost basis on the destination). Optional fee in the source security,
 * the destination security, or cash from any account.
 */
export function InvTransferModal({ accounts, onClose, onDone }: {
  accounts: Record<string, unknown>[]
  onClose: () => void
  onDone: () => void
}) {
  useEscapeKey(onClose)
  const [date, setDate] = useState(today())
  const [fromAccountId, setFromAccountId] = useState('')
  const [fromSecuritiesId, setFromSecuritiesId] = useState('')
  const [quantity, setQuantity] = useState('')
  const [transferAll, setTransferAll] = useState(false)
  const [toAccountId, setToAccountId] = useState('')
  const [toSecuritiesId, setToSecuritiesId] = useState('')
  const [feeType, setFeeType] = useState<FeeType>('none')
  const [feeQuantity, setFeeQuantity] = useState('')
  const [feeCashAmount, setFeeCashAmount] = useState('')
  const [feeCashAccountId, setFeeCashAccountId] = useState('')
  const [description, setDescription] = useState('Transfer')

  const [preview, setPreview] = useState<PreviewResult | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [executing, setExecuting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showInactive, setShowInactive] = useState(false)

  const investmentAccounts = (accounts as Record<string, unknown>[])
    .filter(a => INVESTMENT_ACCOUNT_TYPES.includes(String(a.type ?? '')))
    .filter(a => showInactive || Boolean(a.is_active))
  const feeCashAccounts = (accounts as Record<string, unknown>[])
    .filter(a => showInactive || Boolean(a.is_active))

  const { data: fromHoldings = [] } = useQuery({
    queryKey: ['holdings', fromAccountId],
    queryFn: () => getHoldings(Number(fromAccountId)),
    enabled: !!fromAccountId,
  })
  const { data: securities = [] } = useQuery({ queryKey: ['securities'], queryFn: () => getSecurities() })

  const heldSecurities = (fromHoldings as Record<string, unknown>[]).filter(h => Number(h.quantity) > 0)
  const secOptions = (securities as Record<string, unknown>[]).map(s => ({
    value: String(s.id), label: `${String(s.name)} (${String(s.ticker)})`,
  }))

  const fromSec = (securities as Record<string, unknown>[]).find(s => String(s.id) === fromSecuritiesId)
  const toSec = (securities as Record<string, unknown>[]).find(s => String(s.id) === toSecuritiesId)
  const isConversion = !!fromSecuritiesId && !!toSecuritiesId && fromSecuritiesId !== toSecuritiesId

  const fromHolding = heldSecurities.find(h => String(h.securities_id) === fromSecuritiesId)
  const fromHeldQty = fromHolding ? Number(fromHolding.quantity) : 0

  // Keep the quantity field pinned to the full held amount while "Transfer all" is
  // checked — covers both switching security/account and holdings data loading in.
  useEffect(() => {
    if (!transferAll) return
    setQuantity(fromHeldQty > 0 ? String(fromHeldQty) : '')
    setPreview(null)
  }, [transferAll, fromHeldQty])

  const canPreview = fromAccountId && fromSecuritiesId && toAccountId && toSecuritiesId && parseFloat(quantity) > 0
    && (feeType !== 'source' && feeType !== 'destination' || parseFloat(feeQuantity || '0') > 0)
    && (feeType !== 'cash' || (parseFloat(feeCashAmount || '0') > 0 && feeCashAccountId))

  const buildPayload = () => ({
    date,
    from_account_id: Number(fromAccountId), from_securities_id: Number(fromSecuritiesId),
    quantity: parseFloat(quantity),
    to_account_id: Number(toAccountId), to_securities_id: Number(toSecuritiesId),
    fee_type: feeType,
    fee_quantity: (feeType === 'source' || feeType === 'destination') ? parseFloat(feeQuantity || '0') : undefined,
    fee_cash_amount: feeType === 'cash' ? parseFloat(feeCashAmount || '0') : undefined,
    fee_cash_account_id: feeType === 'cash' ? Number(feeCashAccountId) : undefined,
    description,
  })

  const errorText = (e: unknown) => {
    const resp = (e as { response?: { data?: { detail?: string } } })?.response
    return resp?.data?.detail || (e instanceof Error ? e.message : 'Something went wrong')
  }

  const handlePreview = async () => {
    setPreviewLoading(true); setError(null); setPreview(null)
    try {
      setPreview(await previewInvestmentTransfer(buildPayload()) as PreviewResult)
    } catch (e) {
      setError(errorText(e))
    } finally { setPreviewLoading(false) }
  }

  const handleExecute = async () => {
    setExecuting(true); setError(null)
    try {
      await executeInvestmentTransfer(buildPayload())
      onDone()
      onClose()
    } catch (e) {
      setError(errorText(e))
    } finally { setExecuting(false) }
  }

  const feeLabel = (t: FeeType) => {
    if (t === 'source') return `In ${fromSec ? String(fromSec.ticker) : 'source security'} (source)`
    if (t === 'destination') return `In ${toSec ? String(toSec.ticker) : 'destination security'} (destination)`
    if (t === 'cash') return 'In cash'
    return 'None'
  }

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[92vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold">Transfer / Convert Holding</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
        </div>

        <div className="px-5 py-4 space-y-4">
          <div className="flex items-end gap-4">
            <div>
              <label className="text-xs font-medium text-slate-500 block mb-1">Date *</label>
              <Input type="date" value={date} onChange={e => { setDate(e.target.value); setPreview(null) }} className="w-40" />
            </div>
            <label className="flex items-center gap-1.5 text-xs text-slate-500 cursor-pointer select-none pb-2">
              <input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} className="rounded" />
              Show inactive accounts
            </label>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2 border border-slate-200 rounded-lg p-3">
              <p className="text-xs font-semibold text-slate-500 uppercase">From</p>
              <div>
                <label className="text-xs font-medium text-slate-500 block mb-1">Account *</label>
                <Select value={fromAccountId} onChange={e => { setFromAccountId(e.target.value); setFromSecuritiesId(''); setPreview(null) }}>
                  <option value="">— select —</option>
                  <AccountOptions accounts={investmentAccounts as Record<string, unknown>[]} />
                </Select>
              </div>
              <div>
                <label className="text-xs font-medium text-slate-500 block mb-1">Security (current holdings) *</label>
                <Select value={fromSecuritiesId} onChange={e => { setFromSecuritiesId(e.target.value); if (!toSecuritiesId) setToSecuritiesId(e.target.value); setPreview(null) }} disabled={!fromAccountId}>
                  <option value="">— select —</option>
                  {heldSecurities.map(h => (
                    <option key={String(h.securities_id)} value={String(h.securities_id)}>
                      {String(h.security)} ({String(h.ticker)}) — {fmtQty(Number(h.quantity), 8)} held
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <label className="text-xs font-medium text-slate-500 block mb-1">Quantity sent *</label>
                <Input
                  type="number" step="any" value={quantity} placeholder="0.00"
                  disabled={transferAll}
                  onChange={e => { setQuantity(e.target.value); setPreview(null) }}
                />
                <label className="flex items-center gap-1.5 text-xs text-slate-500 cursor-pointer select-none mt-1.5">
                  <input
                    type="checkbox" checked={transferAll} disabled={!fromSecuritiesId}
                    onChange={e => setTransferAll(e.target.checked)}
                    className="rounded"
                  />
                  Transfer all ({fromHeldQty > 0 ? fmtQty(fromHeldQty, 8) : '—'} held)
                </label>
              </div>
            </div>

            <div className="space-y-2 border border-slate-200 rounded-lg p-3">
              <p className="text-xs font-semibold text-slate-500 uppercase flex items-center gap-1"><ArrowRight size={12} /> To</p>
              <div>
                <label className="text-xs font-medium text-slate-500 block mb-1">Account *</label>
                <Select value={toAccountId} onChange={e => { setToAccountId(e.target.value); setPreview(null) }}>
                  <option value="">— select —</option>
                  <AccountOptions accounts={investmentAccounts as Record<string, unknown>[]} />
                </Select>
              </div>
              <div>
                <label className="text-xs font-medium text-slate-500 block mb-1">Security *</label>
                <SearchableSelect value={toSecuritiesId} onChange={v => { setToSecuritiesId(v); setPreview(null) }} options={secOptions} placeholder="— select —" />
              </div>
              {isConversion && (
                <div className="bg-amber-50 border border-amber-200 text-amber-800 text-xs rounded px-2 py-1.5 flex items-start gap-1.5">
                  <AlertTriangle size={13} className="shrink-0 mt-0.5" />
                  Different security — this is a conversion/swap, not a custody transfer. It realizes gain/loss on
                  the source security at today's market price.
                </div>
              )}
            </div>
          </div>

          <div className="border border-slate-200 rounded-lg p-3 space-y-2">
            <p className="text-xs font-semibold text-slate-500 uppercase">Fee</p>
            <div className="flex flex-wrap gap-3">
              {(['none', 'source', 'destination', 'cash'] as FeeType[]).map(t => (
                <label key={t} className="flex items-center gap-1.5 text-sm cursor-pointer">
                  <input type="radio" name="fee_type" checked={feeType === t} onChange={() => { setFeeType(t); setPreview(null) }} />
                  {feeLabel(t)}
                </label>
              ))}
            </div>
            {(feeType === 'source' || feeType === 'destination') && (
              <div className="w-40">
                <label className="text-xs font-medium text-slate-500 block mb-1">Fee quantity</label>
                <Input type="number" step="any" value={feeQuantity} onChange={e => { setFeeQuantity(e.target.value); setPreview(null) }} placeholder="0.00" />
              </div>
            )}
            {feeType === 'cash' && (
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium text-slate-500 block mb-1">Fee amount</label>
                  <Input type="number" step="any" value={feeCashAmount} onChange={e => { setFeeCashAmount(e.target.value); setPreview(null) }} placeholder="0.00" />
                </div>
                <div>
                  <label className="text-xs font-medium text-slate-500 block mb-1">Paid from account</label>
                  <Select value={feeCashAccountId} onChange={e => { setFeeCashAccountId(e.target.value); setPreview(null) }}>
                    <option value="">— select —</option>
                    <AccountOptions accounts={feeCashAccounts as Record<string, unknown>[]} />
                  </Select>
                </div>
              </div>
            )}
          </div>

          <div>
            <label className="text-xs font-medium text-slate-500 block mb-1">Description</label>
            <Input value={description} onChange={e => setDescription(e.target.value)} />
          </div>

          {error && <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded px-3 py-2">{error}</div>}

          <Button variant="secondary" onClick={handlePreview} disabled={!canPreview || previewLoading}>
            {previewLoading ? <Spinner size={14} /> : 'Preview'}
          </Button>

          {preview && (
            <div className="border border-slate-200 rounded-lg overflow-hidden">
              <table className="w-full text-xs">
                <thead className="bg-slate-50">
                  <tr>
                    <th className="text-left px-2 py-1.5 font-semibold">Account</th>
                    <th className="text-left px-2 py-1.5 font-semibold">Action</th>
                    <th className="text-left px-2 py-1.5 font-semibold">Security</th>
                    <th className="text-right px-2 py-1.5 font-semibold">Quantity</th>
                    <th className="text-right px-2 py-1.5 font-semibold">Price</th>
                    <th className="text-right px-2 py-1.5 font-semibold">Total</th>
                    <th className="text-right px-2 py-1.5 font-semibold">Est. P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.rows.map((r, i) => (
                    <tr key={i} className="border-t border-slate-100">
                      <td className="px-2 py-1.5">{r.account_name}</td>
                      <td className={`px-2 py-1.5 font-medium ${r.action === 'Sell' ? 'text-red-600' : r.action === 'ShrOut' ? 'text-slate-500' : 'text-green-700'}`}>{r.action}</td>
                      <td className="px-2 py-1.5 text-slate-500">{r.security_ticker}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{fmtQty(r.quantity, 8)}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{fmtCur(r.price_per_share, r.security_currency)}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{fmtCur(r.total_amount_acccur, r.account_currency)}</td>
                      <td className={`px-2 py-1.5 text-right tabular-nums font-medium ${r.estimated_pnl == null ? 'text-slate-300' : r.estimated_pnl >= 0 ? 'text-green-700' : 'text-red-600'}`}>
                        {r.estimated_pnl != null ? fmtCur(r.estimated_pnl, r.estimated_pnl_currency) : '—'}
                      </td>
                    </tr>
                  ))}
                  {preview.cash_fee && (
                    <tr className="border-t border-slate-100">
                      <td className="px-2 py-1.5">{preview.cash_fee.account_name}</td>
                      <td className="px-2 py-1.5 font-medium text-red-600">Fee</td>
                      <td className="px-2 py-1.5 text-right">—</td>
                      <td className="px-2 py-1.5 text-right">—</td>
                      <td className="px-2 py-1.5 text-right">—</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{fmtCur(preview.cash_fee.amount, preview.cash_fee.account_currency)}</td>
                      <td className="px-2 py-1.5 text-right">—</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 px-5 py-3 border-t border-slate-200">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button onClick={handleExecute} disabled={!preview || executing}>
            {executing ? <Spinner size={14} /> : 'Confirm Transfer'}
          </Button>
        </div>
      </div>
    </div>
  )
}
