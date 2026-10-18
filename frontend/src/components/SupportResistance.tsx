import type { SrLevel } from '@/lib/technical'
import { Tooltip } from '@/components/ui'

const HELP = 'Support (green) and resistance (red) from the plotted period. A turning point is a peak or trough the price '
  + 'then moved away from by a real reversal (about 5x its average daily move, so roughly 2% for a currency and 5-8% for a stock); '
  + 'turning points at about the same price form one level — the more of them ("touches"), the thicker the line — and the '
  + 'period\'s own high and low always count. Below the last price is support, above it resistance; the three nearest on each '
  + 'side are drawn. Levels change with the period selected. A guide to where the price has turned before, not a forecast.'

// "Support / Resistance" toggle plus the nearest level on each side, for a chart's header row.
export function SrSummary({ levels, show, onToggle, fmt }: {
  levels: SrLevel[]; show: boolean; onToggle: (v: boolean) => void; fmt: (v: number) => string
}) {
  const nearest = (kind: SrLevel['kind']) => levels.find(l => l.kind === kind)
  const s = nearest('support'), r = nearest('resistance')
  const chip = (l: SrLevel | undefined, kind: SrLevel['kind']) => (
    <span className={`text-xs px-2 py-0.5 rounded ${kind === 'support' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'}`}>
      {kind === 'support' ? 'Support' : 'Resistance'}{' '}
      {l ? <b className="tabular-nums">{fmt(l.price)}</b> : <span>—</span>}
      {l && <span className="tabular-nums opacity-80"> ({l.distance_pct >= 0 ? '+' : ''}{l.distance_pct.toFixed(2)}%)</span>}
      {!l && <span className="opacity-80"> {kind === 'support' ? 'none below' : 'none above — at the period high'}</span>}
    </span>
  )
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <label className="flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer">
        <input type="checkbox" checked={show} onChange={e => onToggle(e.target.checked)} className="rounded" />
        <Tooltip text={HELP}>Support / Resistance</Tooltip>
      </label>
      {show && (levels.length === 0
        ? <span className="text-xs text-slate-400">not enough price history in this period</span>
        : <>{chip(s, 'support')}{chip(r, 'resistance')}</>)}
    </div>
  )
}
