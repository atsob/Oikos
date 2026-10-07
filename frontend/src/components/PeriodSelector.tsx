import { CHART_PERIODS, type ChartPeriod } from '@/lib/chartPeriods'

export function PeriodSelector({ value, onChange }: { value: ChartPeriod; onChange: (p: ChartPeriod) => void }) {
  return (
    <div className="flex gap-1">
      {CHART_PERIODS.map(p => (
        <button key={p} onClick={() => onChange(p)}
          className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${value === p ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
          {p}
        </button>
      ))}
    </div>
  )
}
