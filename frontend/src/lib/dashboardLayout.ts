// Which blocks the Dashboard shows, in what order and at what width (full or half) — the user's choice (Customize on the Dashboard), kept in their preferences.

export interface DashBlock { id: string; label: string; hint: string; half?: boolean }

// In the default order. `half` is the block's default width: half-width blocks that follow one another share a row, two to a row;
// everything else takes the full width. The user can change any block's width.
export const DASH_BLOCKS: DashBlock[] = [
  { id: 'kpi', label: 'Key figures', hint: 'Net Worth, Cash, Investments, Pension & Savings and Assets, with their changes' },
  { id: 'insights', label: 'Insights', hint: 'Short observations about your money' },
  { id: 'valuation', label: 'Market valuation', hint: 'Shiller CAPE' },
  { id: 'marketwatch', label: 'Market Watch', hint: 'The global crash-warning banner' },
  { id: 'localwatch', label: 'Local Market Watch', hint: 'The same for your home market' },
  { id: 'alerts', label: 'Alerts', hint: 'Triggered alerts, by group' },
  { id: 'plan', label: 'Plan', hint: 'Plan steps that are overdue, due today or due this week' },
  { id: 'uncategorized', label: 'Uncategorized transactions', hint: 'Cash transactions with no category' },
  { id: 'drafts', label: 'Pending drafts', hint: 'Draft transactions waiting for your confirmation' },
  { id: 'breakdown', label: 'Net Worth breakdown', hint: 'The split of your net worth' },
  { id: 'trend', label: 'Net Worth trend', hint: 'The net worth chart' },
  { id: 'options', label: 'Options & account selection', hint: 'Which accounts the Dashboard counts' },
  { id: 'accounts', label: 'Accounts', hint: 'Every account and its balance' },
  { id: 'bills', label: 'Upcoming bills', hint: 'What is due soon', half: true },
  { id: 'anomalies', label: 'Unusual transactions', hint: 'Transactions out of the ordinary', half: true },
  { id: 'weekly', label: 'Weekly AI summary', hint: 'AI summary of a week', half: true },
  { id: 'monthly', label: 'Monthly AI summary', hint: 'AI summary of a month', half: true },
]

export const BLOCK_BY_ID: Record<string, DashBlock> = Object.fromEntries(DASH_BLOCKS.map(b => [b.id, b]))

export type DashWidth = 'full' | 'half'
export interface DashLayout { order: string[]; hidden: string[]; width?: Record<string, DashWidth> }
export const DEFAULT_LAYOUT: DashLayout = { order: DASH_BLOCKS.map(b => b.id), hidden: [] }

/** The saved layout made safe: unknown blocks dropped, blocks added since it was saved appended, nothing twice. */
export function resolveLayout(saved: Partial<DashLayout> | null | undefined): { order: string[]; hidden: string[]; shown: string[]; widthOf: (id: string) => DashWidth } {
  const seen = new Set<string>()
  const order: string[] = []
  for (const id of saved?.order ?? []) if (BLOCK_BY_ID[id] && !seen.has(id)) { seen.add(id); order.push(id) }
  for (const b of DASH_BLOCKS) if (!seen.has(b.id)) order.push(b.id)
  const hidden = (saved?.hidden ?? []).filter((id, i, a) => BLOCK_BY_ID[id] && a.indexOf(id) === i)
  const widthOf = (id: string): DashWidth => saved?.width?.[id] ?? (BLOCK_BY_ID[id]?.half ? 'half' : 'full')
  return { order, hidden, shown: order.filter(id => !hidden.includes(id)), widthOf }
}

/** The rows the Dashboard is laid out in: two half-width blocks that follow one another share a row; a half-width block with no
 *  half-width neighbour takes the whole row, and so does every full-width block. (On narrow screens every row stacks.) */
export function layoutRows(shown: string[], widthOf: (id: string) => DashWidth): string[][] {
  const rows: string[][] = []
  for (let i = 0; i < shown.length; i++) {
    const id = shown[i]
    if (widthOf(id) === 'half' && i + 1 < shown.length && widthOf(shown[i + 1]) === 'half') { rows.push([id, shown[i + 1]]); i++ }
    else rows.push([id])
  }
  return rows
}

/** Move `id` to sit just before `beforeId` (or to the end when beforeId is null). */
export function moveBefore(order: string[], id: string, beforeId: string | null): string[] {
  const rest = order.filter(x => x !== id)
  const at = beforeId == null ? rest.length : rest.indexOf(beforeId)
  rest.splice(at < 0 ? rest.length : at, 0, id)
  return rest
}

export function moveBy(order: string[], id: string, delta: number): string[] {
  const i = order.indexOf(id), j = i + delta
  if (i < 0 || j < 0 || j >= order.length) return order
  const o = [...order];
  [o[i], o[j]] = [o[j], o[i]]
  return o
}
