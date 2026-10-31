import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarCheck } from 'lucide-react'
import { Card } from '@/components/ui'
import { getPlanDue, setPlanStatus, type PlanItem } from '@/lib/api'
import { planDayLabel } from '@/lib/plan'

// The Dashboard's Plan block: the open steps that are overdue, due today or due in the next 7 days. It disappears when there are none.
export function PlanPanel() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['plan-due'], queryFn: () => getPlanDue(7), staleTime: 60_000 })
  const [last, setLast] = useState<PlanItem | null>(null)      // the step just ticked, so it can be undone
  const refresh = () => { qc.invalidateQueries({ queryKey: ['plan-due'] }); qc.invalidateQueries({ queryKey: ['plan-items'] }) }
  const done = useMutation({ mutationFn: (it: PlanItem) => setPlanStatus(it.id, 'done'), onSuccess: (_r, it) => { setLast(it); refresh() } })
  const undo = useMutation({ mutationFn: (it: PlanItem) => setPlanStatus(it.id, 'open'), onSuccess: () => { setLast(null); refresh() } })
  if (!data) return null
  const groups: [string, PlanItem[], string][] = [
    ['Overdue', data.overdue, 'text-red-600'], ['Today', data.due_today, 'text-blue-700'], ['Next 7 days', data.upcoming, 'text-slate-500'],
  ]
  const n = data.overdue.length + data.due_today.length + data.upcoming.length
  if (!n && !last) return null
  return (
    <Card>
      <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-100">
        <CalendarCheck size={14} className="text-blue-500" />
        <span className="text-sm font-semibold text-slate-700">Plan</span>
        <span className="text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full">{n}</span>
        <button className="ml-auto text-xs text-blue-600 hover:underline" onClick={() => navigate('/plan')}>Open Plan →</button>
      </div>
      {last && (
        <div className="flex items-center gap-2 px-4 py-2 text-xs bg-green-50 text-green-800 border-b border-green-100">
          <span className="min-w-0 truncate">Marked done: {last.title}</span>
          <button className="ml-auto shrink-0 font-semibold underline" disabled={undo.isPending} onClick={() => undo.mutate(last)}>Undo</button>
          <button className="shrink-0 text-green-700/60 hover:text-green-800" onClick={() => setLast(null)} title="Dismiss">✕</button>
        </div>
      )}
      <div className="divide-y divide-slate-100 max-h-72 overflow-y-auto">
        {groups.filter(g => g[1].length).map(([label, items, color]) => (
          <div key={label}>
            <p className={`px-4 pt-2 text-[10px] uppercase tracking-wide font-semibold ${color}`}>{label}</p>
            {items.map(it => (
              <div key={it.id} className="flex items-start gap-2.5 px-4 py-2 hover:bg-slate-50">
                <input type="checkbox" className="mt-1 rounded shrink-0 w-4 h-4 cursor-pointer" checked={false} disabled={done.isPending} onChange={() => done.mutate(it)} title="Mark as done" />
                <button className="min-w-0 text-left" onClick={() => navigate('/plan')} title="Open the Plan">
                  <span className="block text-sm text-slate-800">{it.title}</span>
                  <span className="block text-xs text-slate-400">{label === 'Today' ? '' : planDayLabel(it.due_date) + ' · '}{it.plan}</span>
                </button>
              </div>
            ))}
          </div>
        ))}
      </div>
    </Card>
  )
}
