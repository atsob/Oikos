import { Spinner, Button } from '@/components/ui'
import { MobileCard } from '@/components/mobile'
import { fmtCur, fmtDate, fmtQty } from '@/lib/utils'

export type TxRow = Record<string, unknown>

const day = (d: unknown) => String(d ?? '').slice(0, 10)

/** Cash transactions as a list of cards, newest first with a date heading — the phone's view of the Cash Register and of an investment account's cash tab. */
export function TxCards({ rows, currency, loading, hasMore, loadingMore, onMore, onOpen }: {
  rows: TxRow[]; currency: string; loading: boolean; hasMore?: boolean; loadingMore?: boolean; onMore?: () => void; onOpen: (r: TxRow) => void
}) {
  if (loading) return <div className="flex justify-center py-12"><Spinner /></div>
  if (!rows.length) return <p className="text-sm text-slate-400 text-center py-12">No transactions in this period.</p>
  let lastDay = ''
  return (
    <div className="space-y-1.5 pb-24">
      {rows.map(r => {
        const d = day(r.date)
        const header = d !== lastDay ? <p className="text-xs font-semibold text-slate-500 pt-3 pb-1 px-1">{fmtDate(d)}</p> : null
        lastDay = d
        const amount = Number(r.amount ?? 0)
        const splits = Number(r.split_count ?? 0) > 1
        const title = String(r.payee || r.description || (r.target_account ? `→ ${String(r.target_account)}` : '—'))
        const sub = [splits ? 'Split' : r.category, r.payee && r.description ? r.description : null].filter(Boolean).map(String).join(' · ')
        const state = r.is_draft ? 'Draft' : (!r.cleared && !r.reconciled) ? 'Pending' : r.reconciled ? 'Reconciled' : null
        return (
          <div key={String(r.id)}>
            {header}
            <MobileCard
              title={title} subtitle={sub || undefined}
              meta={<>{r.target_account && r.payee ? <span>→ {String(r.target_account)}</span> : null}{state && <span className={state === 'Pending' ? 'text-amber-600' : state === 'Draft' ? 'text-slate-500' : 'text-blue-600'}>{state}</span>}</>}
              amount={<span className={amount < 0 ? 'text-red-600' : 'text-green-700'}>{fmtCur(amount, currency)}</span>}
              amountSub={r.running_balance != null ? fmtCur(Number(r.running_balance), currency) : undefined}
              accent={amount < 0 ? 'red' : 'green'}
              onClick={() => onOpen(r)}
            />
          </div>
        )
      })}
      {hasMore && onMore && <div className="pt-3 flex justify-center"><Button variant="secondary" onClick={onMore} disabled={loadingMore}>{loadingMore ? 'Loading…' : 'Load more'}</Button></div>}
    </div>
  )
}

const ACTION_ACCENT: Record<string, 'green' | 'red' | 'amber' | 'slate'> = { Buy: 'slate', Sell: 'red', Dividend: 'green', Reinvest: 'green', IntInc: 'green', MiscExp: 'amber' }
const ACTION_COLOR = (a: string) => a === 'Buy' ? 'text-blue-700' : a === 'Sell' ? 'text-red-600' : ['Dividend', 'Reinvest', 'IntInc'].includes(a) ? 'text-green-700' : 'text-slate-600'

/** Investment transactions as cards: the action and the security, what was traded, and the total in the account's currency. */
export function InvCards({ rows, loading, hasMore, loadingMore, onMore, onOpen }: {
  rows: TxRow[]; loading: boolean; hasMore?: boolean; loadingMore?: boolean; onMore?: () => void; onOpen: (r: TxRow) => void
}) {
  if (loading) return <div className="flex justify-center py-12"><Spinner /></div>
  if (!rows.length) return <p className="text-sm text-slate-400 text-center py-12">No transactions in this period.</p>
  let lastDay = ''
  return (
    <div className="space-y-1.5 pb-24">
      {rows.map(r => {
        const d = day(r.date)
        const header = d !== lastDay ? <p className="text-xs font-semibold text-slate-500 pt-3 pb-1 px-1">{fmtDate(d)}</p> : null
        lastDay = d
        const action = String(r.action ?? '')
        const trade = r.quantity != null && Number(r.quantity) !== 0
        const total = Number(r.total ?? 0)
        return (
          <div key={String(r.id)}>
            {header}
            <MobileCard
              title={<><span className={`font-semibold ${ACTION_COLOR(action)}`}>{action}</span> <span>{String(r.security ?? r.ticker ?? '')}</span></>}
              subtitle={trade ? `${fmtQty(Number(r.quantity), 8)} × ${r.price != null ? fmtCur(Number(r.price), r.currency as string) : '—'}` : (r.notes && !String(r.notes).startsWith('IB|') ? String(r.notes) : undefined)}
              meta={<><span>{String(r.account ?? '')}</span>{r.ticker ? <span className="font-mono">{String(r.ticker)}</span> : null}</>}
              amount={<span className={action === 'Sell' || ['Dividend', 'Reinvest', 'IntInc', 'MiscInc'].includes(action) ? 'text-green-700' : 'text-slate-800'}>{fmtCur(total, r.account_currency as string)}</span>}
              amountSub={r.commission ? `fee ${fmtCur(Number(r.commission), r.currency as string)}` : undefined}
              accent={ACTION_ACCENT[action] ?? 'slate'}
              onClick={() => onOpen(r)}
            />
          </div>
        )
      })}
      {hasMore && onMore && <div className="pt-3 flex justify-center"><Button variant="secondary" onClick={onMore} disabled={loadingMore}>{loadingMore ? 'Loading…' : 'Load more'}</Button></div>}
    </div>
  )
}

/** Holdings as cards: the security, how much you hold and where, its value in EUR and the gain or loss on it. Tap one to open the security. */
export function HoldingCards({ holdings, onOpen }: { holdings: TxRow[]; onOpen: (r: TxRow) => void }) {
  if (!holdings.length) return <p className="text-sm text-slate-400 text-center py-12">No holdings.</p>
  const rows = holdings.map(row => {
    const gain = Number(row.quantity) * (Number(row.last_price ?? 0) - Number(row.fifo_avg_price ?? row.simple_avg_price ?? 0)) * Number(row.fx_rate ?? 1)
    const cost = Number(row.value_eur ?? 0) - gain
    return { row, gain, pct: cost !== 0 ? gain / cost * 100 : null }
  }).sort((a, b) => Number(b.row.value_eur ?? 0) - Number(a.row.value_eur ?? 0))
  const fmtE = (v: number) => new Intl.NumberFormat(undefined, { style: 'currency', currency: 'EUR', maximumFractionDigits: 2 }).format(v)
  return (
    <div className="space-y-1.5 pb-24">
      {rows.map(({ row, gain, pct }) => (
        <MobileCard key={String(row.id)}
          title={String(row.security ?? row.ticker ?? '')}
          subtitle={`${fmtQty(Number(row.quantity), 8)} × ${row.last_price != null ? fmtCur(Number(row.last_price), row.currency as string) : '—'}`}
          meta={<><span className="font-mono">{String(row.ticker ?? '')}</span><span>{String(row.account ?? '')}</span></>}
          amount={fmtE(Number(row.value_eur ?? 0))}
          amountSub={<span className={gain < 0 ? 'text-red-600' : 'text-green-700'}>{gain >= 0 ? '+' : ''}{fmtE(gain)}{pct != null ? ` · ${pct >= 0 ? '+' : ''}${pct.toFixed(1)}%` : ''}</span>}
          accent={gain < 0 ? 'red' : 'green'}
          onClick={() => onOpen(row)}
        />
      ))}
    </div>
  )
}
