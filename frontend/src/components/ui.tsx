import { cn } from '@/lib/utils'
import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { RefreshCcw, ChevronDown, Columns3 } from 'lucide-react'

// ── Escape-key hook (call inside any modal with the close handler) ────────────
export function useEscapeKey(onClose: () => void) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])
}

// ── Card ──────────────────────────────────────────────────────────────────────
export function Card({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div className={cn('bg-white rounded-xl border border-slate-200 shadow-sm', className)}>
      {children}
    </div>
  )
}

export function CardHeader({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn('px-5 py-4 border-b border-slate-100', className)}>{children}</div>
}

export function CardTitle({ className, children }: { className?: string; children: React.ReactNode }) {
  return <h2 className={cn('text-base font-semibold text-slate-800', className)}>{children}</h2>
}

export function CardBody({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn('p-5', className)}>{children}</div>
}

// ── Button ────────────────────────────────────────────────────────────────────
interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'destructive' | 'ghost'
  size?: 'sm' | 'md' | 'lg'
}

export function Button({ variant = 'primary', size = 'md', className, children, ...props }: ButtonProps) {
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1 disabled:opacity-50 disabled:cursor-not-allowed',
        {
          'bg-blue-600 text-white hover:bg-blue-700': variant === 'primary',
          'bg-slate-100 text-slate-700 hover:bg-slate-200': variant === 'secondary',
          'bg-red-600 text-white hover:bg-red-700': variant === 'destructive',
          'text-slate-600 hover:bg-slate-100': variant === 'ghost',
          'text-xs px-2.5 py-1.5': size === 'sm',
          'text-sm px-3.5 py-2': size === 'md',
          'text-sm px-5 py-2.5': size === 'lg',
        },
        className,
      )}
      {...props}
    >
      {children}
    </button>
  )
}

// ── Sync balances dropdown ──────────────────────────────────────────────────────
// Data-fetching stays with the caller (via onSync) so this component has no dependency
// on the API layer — each page passes only the sync targets relevant to its own accounts.
export interface SyncOption { label: string; target: string; emphasize?: boolean }
export function SyncBalancesButton({ options, onSync }: {
  options: SyncOption[]
  onSync: (target: string) => Promise<unknown>
}) {
  const [syncing, setSyncing] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  // Click-to-toggle (not CSS :hover) so this works on touch devices too — a hover-only
  // dropdown never opens on mobile, since taps don't produce a hover state.
  useEffect(() => {
    if (!open) return
    const onOutside = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onOutside)
    return () => document.removeEventListener('mousedown', onOutside)
  }, [open])

  const handleClick = async (target: string, label: string) => {
    setOpen(false)
    setSyncing(target); setMsg(null)
    try {
      await onSync(target)
      setMsg(`${label.replace(/^\S+\s/, '')} synced`)
    } catch { setMsg('Sync failed') }
    finally { setSyncing(null) }
  }

  return (
    <div className="flex items-center gap-2">
      {msg && <span className="text-xs text-slate-500">{msg}</span>}
      <div className="relative" ref={wrapRef}>
        <Button variant="secondary" size="sm" disabled={!!syncing} onClick={() => setOpen(o => !o)}>
          <RefreshCcw size={13} className={syncing ? 'animate-spin' : ''} />
          {syncing ? 'Syncing…' : 'Sync Balances'}
          <ChevronDown size={12} />
        </Button>
        {open && (
          <div className="absolute right-0 top-full mt-1 w-52 bg-white border border-slate-200 rounded-lg shadow-lg z-20">
            {options.map(o => (
              <button
                key={o.target}
                onClick={() => handleClick(o.target, o.label)}
                className={cn(
                  'w-full text-left px-4 py-2 text-sm hover:bg-slate-50 first:rounded-t-lg last:rounded-b-lg',
                  o.emphasize ? 'font-semibold text-blue-600 border-t border-slate-100' : 'text-slate-700',
                )}
              >
                {o.label}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Badge ─────────────────────────────────────────────────────────────────────
interface BadgeProps { label: string; variant?: 'green' | 'red' | 'blue' | 'gray' | 'yellow' }
export function Badge({ label, variant = 'gray' }: BadgeProps) {
  return (
    <span className={cn(
      'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium',
      variant === 'green' && 'bg-green-100 text-green-700',
      variant === 'red' && 'bg-red-100 text-red-700',
      variant === 'blue' && 'bg-blue-100 text-blue-700',
      variant === 'yellow' && 'bg-yellow-100 text-yellow-700',
      variant === 'gray' && 'bg-slate-100 text-slate-600',
    )}>
      {label}
    </span>
  )
}

// ── Input ─────────────────────────────────────────────────────────────────────
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        'block w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-slate-50 disabled:text-slate-500',
        className,
      )}
      {...props}
    />
  )
)
Input.displayName = 'Input'

// ── Select ────────────────────────────────────────────────────────────────────
export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => (
    <select
      ref={ref}
      className={cn(
        'block w-full rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500',
        className,
      )}
      {...props}
    >
      {children}
    </select>
  )
)
Select.displayName = 'Select'

// ── SearchableSelect ──────────────────────────────────────────────────────────
export interface SearchableOption { value: string; label: string; disabled?: boolean }

export function SearchableSelect({ value, onChange, options, placeholder = '— none —', className }: {
  value: string
  onChange: (value: string) => void
  options: SearchableOption[]
  placeholder?: string
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const selectedLabel = options.find(o => o.value === value && !o.disabled)?.label ?? ''

  const filtered = query
    ? options.filter(o => o.disabled || o.label.toLowerCase().includes(query.toLowerCase()))
    : options

  const handleOpen = () => { setOpen(true); setQuery(''); setTimeout(() => inputRef.current?.focus(), 0) }

  const handleSelect = useCallback((val: string) => { onChange(val); setOpen(false); setQuery('') }, [onChange])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { setOpen(false); setQuery('') }
    if (e.key === 'Enter') {
      const first = filtered.find(o => !o.disabled)
      if (first) handleSelect(first.value)
    }
  }

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => { if (!containerRef.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  return (
    <div ref={containerRef} className={cn('relative', className)}>
      <button type="button" onClick={handleOpen}
        className="w-full flex items-center justify-between rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-left focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500">
        <span className={selectedLabel ? 'text-slate-900' : 'text-slate-400'}>{selectedLabel || placeholder}</span>
        <svg className="w-4 h-4 text-slate-400 shrink-0 ml-2" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg">
          <div className="p-2 border-b border-slate-100">
            <input ref={inputRef} type="text" value={query} onChange={e => setQuery(e.target.value)}
              onKeyDown={handleKeyDown} placeholder="Search…"
              className="w-full rounded border border-slate-300 px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500" />
          </div>
          <ul className="max-h-52 overflow-y-auto py-1">
            <li className="px-3 py-1.5 text-sm text-slate-400 hover:bg-slate-50 cursor-pointer"
              onMouseDown={() => handleSelect('')}>{placeholder}</li>
            {filtered.map((o, i) => o.disabled
              ? <li key={i} className="px-3 py-1 text-xs text-slate-400 italic select-none">{o.label}</li>
              : <li key={i} onMouseDown={() => handleSelect(o.value)}
                  className={cn('px-3 py-1.5 text-sm cursor-pointer hover:bg-blue-50 hover:text-blue-700',
                    o.value === value && 'bg-blue-50 text-blue-700 font-medium')}>
                  {o.label}
                </li>
            )}
            {filtered.filter(o => !o.disabled).length === 0 && (
              <li className="px-3 py-3 text-sm text-slate-400 text-center">No results</li>
            )}
          </ul>
        </div>
      )}
    </div>
  )
}

// ── Spinner ───────────────────────────────────────────────────────────────────
export function Spinner({ size = 20 }: { size?: number }) {
  return (
    <svg
      className="animate-spin text-blue-500"
      width={size} height={size}
      viewBox="0 0 24 24" fill="none"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  )
}

// ── PageHeader ────────────────────────────────────────────────────────────────
export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 flex-wrap px-4 sm:px-6 py-4 border-b border-slate-200 bg-white">
      <div>
        <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
        {subtitle && <p className="text-sm text-slate-500 mt-0.5">{subtitle}</p>}
      </div>
      {actions && <div className="flex items-center gap-2 flex-wrap">{actions}</div>}
    </div>
  )
}

// ── Stat card ─────────────────────────────────────────────────────────────────
export function StatCard({ label, value, sub, color, subs, compact }: {
  label: string; value: string; sub?: string; color?: string
  subs?: { text: string; color?: string }[]; compact?: boolean
}) {
  return (
    <div className={cn('bg-white rounded-xl border border-slate-200 shadow-sm', compact ? 'p-2.5' : 'p-4')}>
      <p className={cn('font-medium text-slate-500 uppercase tracking-wide', compact ? 'text-xs' : 'text-xs')}>{label}</p>
      <p className={cn('font-bold mt-1', compact ? 'text-lg' : 'text-2xl', color ?? 'text-slate-900')}>{value}</p>
      {subs?.map((s, i) => <p key={i} className={cn('mt-0.5', compact ? 'text-xs truncate' : 'text-xs', s.color ?? 'text-slate-400')}>{s.text}</p>)}
      {!subs && sub && <p className={cn('mt-0.5 text-slate-400 text-xs')}>{sub}</p>}
    </div>
  )
}

// ── Tooltip ────────────────────────────────────────────────────────────────────
// Portal-based so it isn't clipped by table overflow or sticky headers.
// Also opens on tap (not just hover), since touch devices never produce a hover
// state — without this, none of these info hints would ever be reachable on mobile.
export function Tooltip({ text, children }: { text: string; children: React.ReactNode }) {
  const [pos, setPos] = useState<{ x: number; top: number; bottom: number } | null>(null)
  const ref = useRef<HTMLSpanElement>(null)

  const show = (e: React.SyntheticEvent) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    setPos({ x: r.left + r.width / 2, top: r.top, bottom: r.bottom })
  }

  useEffect(() => {
    if (!pos) return
    const onOutside = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setPos(null)
    }
    document.addEventListener('mousedown', onOutside)
    return () => document.removeEventListener('mousedown', onOutside)
  }, [pos])

  return (
    <span
      ref={ref}
      className="inline-flex items-center gap-0.5 cursor-help"
      onMouseEnter={show}
      onMouseLeave={() => setPos(null)}
      onClick={e => { e.stopPropagation(); show(e) }}
    >
      {children}
      <span className="ml-0.5 text-slate-400 text-[10px] leading-none">ⓘ</span>
      {pos && createPortal(
        <div
          style={
            pos.top > 160
              ? { position: 'fixed', left: pos.x, top: pos.top - 8, transform: 'translate(-50%, -100%)', zIndex: 9999 }
              : { position: 'fixed', left: pos.x, top: pos.bottom + 8, transform: 'translateX(-50%)', zIndex: 9999 }
          }
          className="w-56 rounded bg-slate-800 px-2.5 py-1.5 text-xs text-white shadow-lg whitespace-normal text-center pointer-events-none"
        >
          {text}
        </div>,
        document.body
      )}
    </span>
  )
}

// ── Columns menu (show/hide grid columns) ──────────────────────────────────────
// Pairs with useGridColumnState's `columns`/`toggleColumn`:
//   <ColumnsMenu columns={gridCols.columns} onToggle={gridCols.toggleColumn} />
export function ColumnsMenu({ columns, onToggle }: {
  columns: { colId: string; headerName: string; hidden: boolean }[]
  onToggle: (colId: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onOutside = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', onOutside)
    return () => document.removeEventListener('mousedown', onOutside)
  }, [open])

  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs border border-slate-300 rounded bg-white hover:bg-slate-50 text-slate-600">
        <Columns3 size={13} /> Columns
      </button>
      {open && (
        <div className="absolute top-full right-0 mt-1 z-30 bg-white border border-slate-200 rounded shadow-lg p-2 min-w-[180px] max-h-72 overflow-y-auto">
          {columns.map(c => (
            <label key={c.colId} className="flex items-center gap-2 px-1.5 py-1 text-xs cursor-pointer hover:bg-slate-50 rounded">
              <input type="checkbox" checked={!c.hidden} onChange={() => onToggle(c.colId)} />
              {c.headerName}
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

// ── useSortTable ───────────────────────────────────────────────────────────────
export function useSortTable<T>(
  data: T[],
  defaultKey: string | null = null,
  defaultDir: 'asc' | 'desc' = 'asc'
) {
  const [sortKey, setSortKey] = useState<string | null>(defaultKey)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>(defaultDir)

  const toggleSort = useCallback((key: string) => {
    setSortKey(key)
    setSortDir(d => sortKey === key ? (d === 'asc' ? 'desc' : 'asc') : 'asc')
  }, [sortKey])

  const sorted = useMemo(() => {
    if (!sortKey) return data
    return [...data].sort((a, b) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const av = (a as any)[sortKey], bv = (b as any)[sortKey]
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      if (typeof av === 'number' && typeof bv === 'number')
        return sortDir === 'asc' ? av - bv : bv - av
      return sortDir === 'asc'
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av))
    })
  }, [data, sortKey, sortDir])

  return { sorted, sortKey, sortDir, toggleSort }
}

// ── ColHeader ─────────────────────────────────────────────────────────────────
interface ColHeaderProps {
  label: React.ReactNode
  sortKey: string
  currentKey: string | null
  currentDir: 'asc' | 'desc'
  onSort: (k: string) => void
  align?: 'left' | 'right' | 'center'
  tooltip?: string
  className?: string
}

export function ColHeader({ label, sortKey, currentKey, currentDir, onSort, align = 'left', tooltip, className }: ColHeaderProps) {
  const active = currentKey === sortKey
  const content = (
    <button
      type="button"
      onClick={() => onSort(sortKey)}
      className={cn(
        'inline-flex items-center gap-0.5 cursor-pointer hover:text-slate-700 select-none whitespace-nowrap',
        align === 'right' && 'flex-row-reverse w-full justify-start'
      )}
    >
      {label}
      <span className={cn('text-[9px] ml-0.5', active ? 'text-blue-500' : 'text-slate-300')}>
        {active ? (currentDir === 'asc' ? '▲' : '▼') : '⇅'}
      </span>
    </button>
  )
  return (
    <th className={cn(
      'px-3 py-2 font-semibold',
      align === 'left' && 'text-left',
      align === 'right' && 'text-right',
      align === 'center' && 'text-center',
      className
    )}>
      {tooltip ? <Tooltip text={tooltip}>{content}</Tooltip> : content}
    </th>
  )
}
