import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { getSaxoMargin } from '@/lib/api'
import { fmtCur } from '@/lib/utils'
import { Card, CardBody } from '@/components/ui'

// Saxo's own margin figures for a Saxo margin account, read live from Saxo (api/routers/investments.py /saxo-margin, data/saxo_margin.py): Oikos cannot work out the margin
// Saxo requires, so it asks. Shown on the Investments page for a Margin account that is mapped to a Saxo account; nothing is stored, and it is read again on Refresh.
interface SaxoMarginData {
  available: boolean; reason?: string; transient?: boolean; fetched_at?: number; currency?: string; saxo_account?: string
  cash?: number | null; cash_available?: number | null; account_value?: number | null; net_equity?: number | null
  margin_used?: number | null; margin_available?: number | null; margin_utilization_pct?: number | null; unrealised_margin_pl?: number | null
}

// A figure and the plain-words note under it, which wraps instead of being cut (the shared StatCard truncates its notes).
function Tile({ label, value, note, color }: { label: string; value: string; note?: string; color?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-3 min-w-0">
      <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">{label}</p>
      <p className={`mt-1 text-lg font-bold ${color ?? 'text-slate-900'}`}>{value}</p>
      {note && <p className="mt-1 text-xs leading-snug text-slate-600">{note}</p>}
    </div>
  )
}

export default function SaxoMargin({ accountId }: { accountId: number }) {
  const { data, isFetching, refetch } = useQuery<SaxoMarginData>({
    queryKey: ['saxo-margin', accountId], queryFn: () => getSaxoMargin(accountId), staleTime: 60_000, retry: false, refetchOnWindowFocus: false,
  })
  if (!data) return isFetching ? <p className="text-xs text-slate-400">Reading Saxo's margin…</p> : null
  if (!data.available) {
    // an account that is not a Saxo one says nothing; a Saxo one that cannot be read says why
    return data.reason && !/not mapped/i.test(data.reason)
      ? <p className="text-xs text-slate-400">Saxo margin: {data.reason} <button className="underline" onClick={() => refetch()}>Try again</button></p> : null
  }
  const cur = data.currency ?? 'EUR'
  const money = (v?: number | null) => v == null ? '—' : fmtCur(v, cur)
  const pct = data.margin_utilization_pct
  const tone = pct == null ? 'text-slate-900' : pct >= 80 ? 'text-red-600' : pct >= 50 ? 'text-amber-600' : 'text-green-700'
  const bar = pct == null ? 'bg-slate-300' : pct >= 80 ? 'bg-red-500' : pct >= 50 ? 'bg-amber-500' : 'bg-green-500'
  const at = data.fetched_at ? new Date(data.fetched_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''
  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <p className="text-sm font-semibold text-slate-700">Saxo margin <span className="font-normal text-slate-400">· account {data.saxo_account}{at ? ` · as of ${at}` : ''}</span></p>
          <button onClick={() => refetch()} disabled={isFetching} title="Read the figures from Saxo again" className="text-slate-400 hover:text-slate-700 disabled:opacity-50">
            <RefreshCw size={14} className={isFetching ? 'animate-spin' : ''} />
          </button>
        </div>
        <div>
          <div className="flex items-baseline justify-between">
            <span className="text-xs font-medium text-slate-500 uppercase tracking-wide">Margin utilization</span>
            <span className={`text-lg font-bold ${tone}`}>{pct == null ? '—' : `${pct.toFixed(2)}%`}</span>
          </div>
          <div className="mt-1 h-2 rounded-full bg-slate-100 overflow-hidden"><div className={`h-full ${bar}`} style={{ width: `${Math.min(100, Math.max(0, pct ?? 0))}%` }} /></div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3">
          <Tile label="Margin used" value={money(data.margin_used == null ? null : Math.abs(data.margin_used))} note="Held against the open margin positions." />
          <Tile label="Margin available" value={money(data.margin_available)} note="What is left to open new margin positions." />
          <Tile label="Cash available" value={money(data.cash_available)} note="Cash that can still be used for trading." />
          <Tile label="Account value" value={money(data.account_value)} note="Cash + shares + the profit or loss on margin positions." />
          <Tile label="Cash booked" value={money(data.cash)} note="Settled cash. Saxo's platform and Oikos also count trades that have not settled yet." />
          <Tile label="Unrealised P&L" value={money(data.unrealised_margin_pl)} color={data.unrealised_margin_pl != null && data.unrealised_margin_pl < 0 ? 'text-red-600' : 'text-green-700'}
            note="On the margin positions, after the financing Saxo has added to their opening price." />
        </div>
      </CardBody>
    </Card>
  )
}
