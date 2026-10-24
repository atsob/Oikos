/* eslint-disable @typescript-eslint/no-explicit-any */
import { Component, forwardRef, useMemo, useState, type ReactNode } from 'react'
import { AgGridReact, type AgGridReactProps } from 'ag-grid-react'
import { usePhone } from '@/lib/useMedia'

// The app's data grid. On a computer it is AG Grid, unchanged. On a phone a wide grid is unusable, so a grid that holds all its rows at once
// is shown as a list of cards built from the very same column definitions: the first column becomes the card's title, the next ones its
// fields (the columns you have switched on in the Columns menu), a tap does what a click or double-click on the row does, and a column with no
// field of its own (the Edit / Delete buttons) becomes the card's action row. Grids that stream their rows from the server (Register,
// Investments) have card lists of their own and are left alone.

class Safe extends Component<{ fallback: ReactNode; children: ReactNode }, { bad: boolean }> {
  state = { bad: false }
  static getDerivedStateFromError() { return { bad: true } }
  render() { return this.state.bad ? this.props.fallback : this.props.children }
}

const flatten = (defs: any[]): any[] => defs.flatMap(d => (d.children ? flatten(d.children) : [d]))
const pick = (data: any, field?: string) => (field ? field.split('.').reduce((o: any, k) => (o == null ? o : o[k]), data) : undefined)

function valueOf(col: any, data: any) {
  try { return typeof col.valueGetter === 'function' ? col.valueGetter({ data, colDef: col }) : pick(data, col.field) } catch { return pick(data, col.field) }
}
function textOf(col: any, data: any, value: any): string {
  try {
    if (typeof col.valueFormatter === 'function') { const t = col.valueFormatter({ value, data, colDef: col }); if (t != null) return String(t) }
  } catch { /* fall through to the raw value */ }
  if (value == null || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return String(value)
}
const styleOf = (col: any, value: any, data: any) => { try { return typeof col.cellStyle === 'function' ? col.cellStyle({ value, data }) : col.cellStyle } catch { return undefined } }
const classOf = (col: any, value: any, data: any) => {
  try { const c = typeof col.cellClass === 'function' ? col.cellClass({ value, data }) : col.cellClass; return Array.isArray(c) ? c.join(' ') : c || '' } catch { return '' }
}

function Cell({ col, data }: { col: any; data: any }) {
  const value = valueOf(col, data)
  const text = textOf(col, data, value)
  const R = col.cellRenderer
  const inner = (typeof R === 'function' || (R && typeof R === 'object'))
    ? <Safe fallback={text}><R value={value} valueFormatted={text} data={data} colDef={col} node={{ data }} {...(col.cellRendererParams ?? {})} /></Safe>
    : text
  return <span className={classOf(col, value, data)} style={styleOf(col, value, data)}>{inner}</span>
}

const cmp = (a: any, b: any) => {
  if (a == null && b == null) return 0
  if (a == null) return 1
  if (b == null) return -1
  const na = Number(a), nb = Number(b)
  if (!Number.isNaN(na) && !Number.isNaN(nb) && typeof a !== 'boolean') return na - nb
  return String(a).localeCompare(String(b))
}

function PhoneCards(props: any) {
  const { rowData, columnDefs, quickFilterText, getRowClass, onRowClicked, onRowDoubleClicked } = props
  const cols = useMemo(() => flatten(columnDefs as any[]).filter(c => !c.hide && !c.checkboxSelection), [columnDefs])
  const fields = cols.filter(c => c.field || c.valueGetter)
  const actions = cols.filter(c => !c.field && !c.valueGetter && c.cellRenderer)
  const sortable = fields.filter(c => c.sortable !== false && c.headerName)
  const initial = fields.find(c => c.sort)
  const [sortId, setSortId] = useState<string>(initial ? String(initial.colId ?? initial.field) : '')
  const [desc, setDesc] = useState<boolean>(initial ? initial.sort === 'desc' : false)
  const [limit, setLimit] = useState(60)
  const [open, setOpen] = useState<Record<number, boolean>>({})

  const rows = useMemo(() => {
    let r: any[] = Array.isArray(rowData) ? rowData : []
    const q = String(quickFilterText ?? '').trim().toLowerCase()
    if (q) r = r.filter(x => Object.values(x ?? {}).some(v => v != null && typeof v !== 'object' && String(v).toLowerCase().includes(q)))
    const sc = fields.find(c => String(c.colId ?? c.field) === sortId)
    if (sc) r = [...r].sort((a, b) => (desc ? -1 : 1) * cmp(valueOf(sc, a), valueOf(sc, b)))
    return r
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowData, quickFilterText, sortId, desc, fields.length])

  if (!fields.length) return <p className="phone-cards text-sm text-slate-400 text-center py-8">Nothing to show.</p>
  const [title, ...rest] = fields
  const tappable = !!(onRowClicked || onRowDoubleClicked)
  const tap = (data: any) => {
    const ev = { data, node: { data }, event: { detail: 2 } }
    if (onRowDoubleClicked) onRowDoubleClicked(ev)
    else if (onRowClicked) onRowClicked(ev)
  }

  return (
    <div className="phone-cards space-y-1.5">
      <div className="flex items-center gap-2 text-xs text-slate-500 px-1">
        <span>{rows.length.toLocaleString()} row{rows.length === 1 ? '' : 's'}</span>
        {sortable.length > 1 && (
          <span className="ml-auto flex items-center gap-1">
            Sort
            <select className="border border-slate-300 rounded px-1.5 py-1 text-xs bg-white" value={sortId} onChange={e => setSortId(e.target.value)}>
              <option value="">—</option>
              {sortable.map(c => <option key={String(c.colId ?? c.field)} value={String(c.colId ?? c.field)}>{c.headerName}</option>)}
            </select>
            <button className="border border-slate-300 rounded px-2 py-1 bg-white" onClick={() => setDesc(d => !d)} title="Reverse the order">{desc ? '↓' : '↑'}</button>
          </span>
        )}
      </div>
      {rows.slice(0, limit).map((data, i) => {
        const shown = open[i] ? rest : rest.slice(0, 6)
        const more = rest.length - 6
        const extra = (() => { try { return typeof getRowClass === 'function' ? getRowClass({ data }) || '' : '' } catch { return '' } })()
        return (
          <div key={i} className={`bg-white rounded-lg border border-slate-200 px-3 py-2.5 ${tappable ? 'active:bg-slate-50' : ''} ${extra}`}
            onClick={tappable ? e => { if (!(e.target as HTMLElement).closest('button,a,input,select,textarea')) tap(data) } : undefined}>
            <div className="text-sm font-semibold text-slate-800 break-words"><Cell col={title} data={data} /></div>
            {shown.length > 0 && (
              <div className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-1">
                {shown.map(c => (
                  <div key={String(c.colId ?? c.field)} className="min-w-0">
                    <div className="text-[10px] uppercase tracking-wide text-slate-400 truncate">{c.headerName}</div>
                    <div className="text-xs text-slate-700 break-words"><Cell col={c} data={data} /></div>
                  </div>
                ))}
              </div>
            )}
            {more > 0 && (
              <button className="mt-1.5 text-xs text-blue-600" onClick={e => { e.stopPropagation(); setOpen(o => ({ ...o, [i]: !o[i] })) }}>
                {open[i] ? 'Show fewer fields' : `Show ${more} more field${more === 1 ? '' : 's'}`}
              </button>
            )}
            {actions.length > 0 && (
              <div className="mt-2 pt-2 border-t border-slate-100 flex items-center gap-4 text-sm">
                {actions.map(c => <Cell key={String(c.colId ?? c.headerName)} col={c} data={data} />)}
              </div>
            )}
          </div>
        )
      })}
      {rows.length > limit && <div className="pt-2 flex justify-center"><button className="px-4 py-2 text-sm rounded-md border border-slate-300 bg-white" onClick={() => setLimit(l => l + 60)}>Show more ({rows.length - limit} left)</button></div>}
      {rows.length === 0 && <p className="text-sm text-slate-400 text-center py-8">No rows.</p>}
    </div>
  )
}

const AppGrid = forwardRef<AgGridReact, AgGridReactProps>(function AppGrid(props, ref) {
  const phone = usePhone()
  const cards = phone && Array.isArray(props.rowData) && Array.isArray(props.columnDefs) && !props.datasource && props.rowModelType !== 'infinite'
  if (cards) return <PhoneCards {...(props as any)} />
  return <AgGridReact ref={ref} {...props} />
})

export default AppGrid
