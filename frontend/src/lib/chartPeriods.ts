import { toLocalISODate } from '@/lib/utils'

// Chart lookback windows shared by Security Detail and Currency Detail price charts.
export const CHART_PERIODS = ['3M', '6M', 'YTD', '1Y', '3Y', '5Y', 'All'] as const
export type ChartPeriod = typeof CHART_PERIODS[number]

export function periodToFromDate(p: ChartPeriod): string {
  const now = new Date()
  if (p === 'All') return '1900-01-01'
  if (p === 'YTD') return `${now.getFullYear()}-01-01`
  const months: Record<string, number> = { '3M': 3, '6M': 6, '1Y': 12, '3Y': 36, '5Y': 60 }
  const d = new Date(now)
  d.setMonth(d.getMonth() - months[p])
  return toLocalISODate(d)
}
