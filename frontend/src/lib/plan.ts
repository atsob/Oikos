// Helpers shared by the Plan page and the Dashboard's Plan block.

export function planDayLabel(iso: string): string {
  const d = new Date(iso.slice(0, 10) + 'T00:00:00')
  return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
}

/** Whole days from today to the date (negative = past). */
export function daysFromToday(iso: string, today: string): number {
  return Math.round((new Date(iso.slice(0, 10) + 'T00:00:00').getTime() - new Date(today + 'T00:00:00').getTime()) / 86400000)
}
