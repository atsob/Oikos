import { toLocalISODate } from '@/lib/utils'

// Chart lookback windows shared by Security Detail and Currency Detail price charts.
export const CHART_PERIODS = ['1W', '1M', '3M', '6M', 'YTD', '1Y', '3Y', '5Y', '10Y', 'All'] as const
export type ChartPeriod = typeof CHART_PERIODS[number]

export function periodToFromDate(p: ChartPeriod): string {
  const now = new Date()
  if (p === 'All') return '1900-01-01'
  if (p === 'YTD') return `${now.getFullYear()}-01-01`
  const d = new Date(now)
  if (p === '1W') { d.setDate(d.getDate() - 7); return toLocalISODate(d) }
  const months: Record<string, number> = { '1M': 1, '3M': 3, '6M': 6, '1Y': 12, '3Y': 36, '5Y': 60, '10Y': 120 }
  d.setMonth(d.getMonth() - months[p])
  return toLocalISODate(d)
}
