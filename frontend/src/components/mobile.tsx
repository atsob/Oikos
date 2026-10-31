import type { ReactNode } from 'react'
import { MoreHorizontal, Plus } from 'lucide-react'

// Building blocks for the phone layouts: a list of tappable cards in place of a wide table, a "more" menu for the buttons that do not fit,
// and a floating "+" for the one action you reach for most.

export function MobileCard({ title, subtitle, meta, amount, amountSub, accent, onClick, footer }: {
  title: ReactNode; subtitle?: ReactNode; meta?: ReactNode; amount?: ReactNode; amountSub?: ReactNode
  accent?: 'green' | 'red' | 'amber' | 'slate'; onClick?: () => void; footer?: ReactNode
}) {
  const bar = accent === 'green' ? 'bg-green-500' : accent === 'red' ? 'bg-red-500' : accent === 'amber' ? 'bg-amber-400' : 'bg-slate-300'
  const row = (
    <>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-slate-800 truncate">{title}</div>
        {subtitle && <div className="text-xs text-slate-500 truncate mt-0.5">{subtitle}</div>}
        {meta && <div className="text-[11px] text-slate-400 mt-0.5 flex flex-wrap gap-x-2 items-center">{meta}</div>}
      </div>
      {(amount != null || amountSub != null) && (
        <div className="shrink-0 text-right">
          {amount != null && <div className="text-sm font-semibold tabular-nums">{amount}</div>}
          {amountSub != null && <div className="text-[11px] text-slate-400 tabular-nums mt-0.5">{amountSub}</div>}
        </div>
      )}
    </>
  )
  const frame = 'relative w-full text-left bg-white rounded-lg border border-slate-200 pl-4 pr-3 py-2.5 overflow-hidden'
  const accentBar = accent && <span className={`absolute inset-y-0 left-0 w-1 ${bar}`} />
  // A card with buttons of its own is a plain block (a button cannot hold buttons); one without is itself the button you tap.
  if (footer) return (
    <div className={frame}>
      {accentBar}
      <div className="flex items-start gap-3">{row}</div>
      <div className="mt-2 pt-2 border-t border-slate-100">{footer}</div>
    </div>
  )
  const Tag = onClick ? 'button' : 'div'
  return <Tag onClick={onClick} className={`${frame} flex items-start gap-3 ${onClick ? 'active:bg-slate-50' : ''}`}>{accentBar}{row}</Tag>
}

/** The secondary actions of a page, folded into one "⋯" button — on a phone there is no room for a row of them. */
export function MoreMenu({ items }: { items: { label: string; onClick: () => void; disabled?: boolean }[] }) {
  return (
    <details className="relative">
      <summary className="list-none cursor-pointer flex items-center justify-center w-10 h-10 rounded-md border border-slate-300 text-slate-600 [&::-webkit-details-marker]:hidden" title="More">
        <MoreHorizontal size={18} />
      </summary>
      <div className="absolute right-0 z-30 mt-1 w-52 rounded-md border border-slate-200 bg-white shadow-lg py-1">
        {items.map(i => (
          <button key={i.label} disabled={i.disabled}
            className="w-full text-left px-3 py-2.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-40"
            onClick={e => { i.onClick(); (e.currentTarget.closest('details') as HTMLDetailsElement | null)?.removeAttribute('open') }}>
            {i.label}
          </button>
        ))}
      </div>
    </details>
  )
}

/** A round "+" button floating at the bottom right, clear of the home bar. */
export function Fab({ onClick, label, disabled }: { onClick: () => void; label: string; disabled?: boolean }) {
  return (
    <button onClick={onClick} disabled={disabled} title={label} aria-label={label}
      className="sm:hidden fixed z-20 right-4 bottom-[calc(1rem+env(safe-area-inset-bottom))] w-14 h-14 rounded-full bg-blue-600 text-white shadow-lg flex items-center justify-center active:bg-blue-700 disabled:opacity-40">
      <Plus size={26} />
    </button>
  )
}
