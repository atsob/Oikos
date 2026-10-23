import { useState } from 'react'
import { ArrowDown, ArrowUp, GripVertical, X } from 'lucide-react'
import { Button } from '@/components/ui'
import { BLOCK_BY_ID, DEFAULT_LAYOUT, layoutRows, moveBefore, moveBy, resolveLayout, type DashLayout, type DashWidth } from '@/lib/dashboardLayout'

// A side panel to choose what the Dashboard shows and in what order. Changes apply to the Dashboard behind it straight away.
// Drag a row (or use the arrows — dragging does not work on touch screens), tick or untick to show or hide, and choose Full or Half width.
export function DashboardCustomizer({ layout, onChange, onClose }: { layout: DashLayout; onChange: (l: DashLayout) => void; onClose: () => void }) {
  const { order, hidden, shown, widthOf } = resolveLayout(layout)
  const rows = layoutRows(shown, widthOf)
  const paired = new Set(rows.filter(r => r.length === 2).flat())
  const [dragId, setDragId] = useState<string | null>(null)
  const [overId, setOverId] = useState<string | null>(null)
  const set = (o: string[], h: string[] = hidden) => onChange({ ...layout, order: o, hidden: h })
  const setWidth = (id: string, w: DashWidth) => onChange({ ...layout, order, hidden, width: { ...(layout.width ?? {}), [id]: w } })
  const toggle = (id: string) => set(order, hidden.includes(id) ? hidden.filter(x => x !== id) : [...hidden, id])

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose}>
      <div className="w-full max-w-md bg-white shadow-xl flex flex-col" onClick={e => e.stopPropagation()}>
        <div className="flex items-start gap-2 px-4 py-3 border-b border-slate-200">
          <div className="flex-1">
            <h2 className="text-base font-semibold text-slate-900">Customize Dashboard</h2>
            <p className="text-xs text-slate-500 mt-0.5">Tick the blocks to show, drag them (or use the arrows) into the order you like, and make a block Half width to put two next to each other. It applies right away and is saved for you.</p>
          </div>
          <button className="text-slate-400 hover:text-slate-600 p-1" onClick={onClose} title="Close"><X size={16} /></button>
        </div>

        <ul className="flex-1 overflow-y-auto p-2 space-y-1">
          {order.map((id, i) => {
            const b = BLOCK_BY_ID[id]
            const off = hidden.includes(id)
            return (
              <li key={id}
                draggable
                onDragStart={e => { setDragId(id); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', id) }}
                onDragOver={e => { if (dragId && dragId !== id) { e.preventDefault(); setOverId(id) } }}
                onDrop={e => { e.preventDefault(); if (dragId && dragId !== id) set(moveBefore(order, dragId, id)); setDragId(null); setOverId(null) }}
                onDragEnd={() => { setDragId(null); setOverId(null) }}
                className={`flex items-center gap-2 rounded-md border px-2 py-1.5 ${overId === id && dragId ? 'border-blue-500 border-t-2' : 'border-slate-200'} ${dragId === id ? 'opacity-40' : ''} ${off ? 'bg-slate-50' : 'bg-white'}`}>
                <GripVertical size={15} className="text-slate-300 cursor-grab shrink-0" />
                <input type="checkbox" className="rounded shrink-0" checked={!off} onChange={() => toggle(id)} title={off ? 'Show' : 'Hide'} />
                <div className={`flex-1 min-w-0 ${off ? 'opacity-50' : ''}`}>
                  <p className="text-sm font-medium text-slate-800 truncate">{b.label}</p>
                  <p className="text-xs text-slate-400 truncate">{off ? 'Hidden' : paired.has(id) ? 'Shares a row with its neighbour' : b.hint}</p>
                </div>
                <div className="flex rounded border border-slate-200 overflow-hidden shrink-0 text-[11px] font-medium" title="Width: half-width blocks that follow one another share a row">
                  {(['full', 'half'] as const).map(w => (
                    <button key={w} onClick={() => setWidth(id, w)}
                      className={`px-1.5 py-0.5 ${widthOf(id) === w ? 'bg-blue-600 text-white' : 'text-slate-500 hover:bg-slate-50'}`}>{w === 'full' ? 'Full' : 'Half'}</button>
                  ))}
                </div>
                <button className="p-1 text-slate-400 hover:text-slate-700 disabled:opacity-30" disabled={i === 0} title="Move up" onClick={() => set(moveBy(order, id, -1))}><ArrowUp size={14} /></button>
                <button className="p-1 text-slate-400 hover:text-slate-700 disabled:opacity-30" disabled={i === order.length - 1} title="Move down" onClick={() => set(moveBy(order, id, 1))}><ArrowDown size={14} /></button>
              </li>
            )
          })}
        </ul>

        <div className="flex items-center gap-2 px-4 py-3 border-t border-slate-200">
          <span className="text-xs text-slate-400 flex-1">{order.length - hidden.length} of {order.length} blocks shown. Half-width blocks that follow one another share a row; on narrow screens everything stacks.</span>
          <Button size="sm" variant="secondary" onClick={() => onChange(DEFAULT_LAYOUT)}>Reset</Button>
          <Button size="sm" onClick={onClose}>Done</Button>
        </div>
      </div>
    </div>
  )
}
