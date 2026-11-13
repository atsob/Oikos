// The bank's own balance of an account linked in Importers → Bank → Enable Banking, as last read (by a bank fetch
// in Import & Reconcile or the daily Bank Balances job) — shown next to Oikos's balance on Cash accounts and in
// the Cash Register, with ✓ when the two agree or the difference when they don't.
import { useQuery } from '@tanstack/react-query'
import { ebBalances, type EbStoredBalance } from '@/lib/api'
import { fmtCur, fmtDate } from '@/lib/utils'

export function useBankBalances() {
  return useQuery({ queryKey: ['eb-balances'], queryFn: ebBalances, staleTime: 5 * 60_000 }).data ?? {}
}

/** "Bank €1,234.56 · 14/11/2026 ✓" (or "· differs by +€12.30") — null when the account has no bank balance. */
export function bankBalanceLine(b: EbStoredBalance | undefined, oikosBalance: number | null | undefined, currency: string | null | undefined)
  : { text: string; color: string; title: string } | null {
  if (!b || b.amount == null) return null
  const when = b.date ?? b.fetched_at?.slice(0, 10)
  const diff = oikosBalance == null ? null : Math.round((b.amount - oikosBalance) * 100) / 100
  const same = diff != null && Math.abs(diff) < 0.01
  const text = `Bank ${fmtCur(b.amount, b.currency || currency || 'EUR')}${when ? ` · ${fmtDate(when)}` : ''}`
    + (diff == null ? '' : same ? ' ✓' : ` · differs by ${diff > 0 ? '+' : '−'}${fmtCur(Math.abs(diff), b.currency || currency || 'EUR')}`)
  const title = `${b.bank} balance${b.type ? ` (${b.type})` : ''}${b.date ? ` on ${fmtDate(b.date)}` : ''}, read ${new Date(b.fetched_at).toLocaleString()}`
    + (same ? ' — the same as in Oikos.' : diff != null ? ' — compared with the balance shown in Oikos.' : '')
  return { text, color: same ? 'text-slate-500' : 'text-amber-700', title }
}
