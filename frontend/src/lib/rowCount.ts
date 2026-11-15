import { useCallback, useState } from 'react'
import type { GridApi } from 'ag-grid-community'

/** The number of rows a grid shows after its search box and column filters. Pass onModelUpdated to the grid; shown is
 *  null until the grid has drawn (and on a phone, where the list is cards, not a grid). */
export function useShownCount() {
  const [shown, setShown] = useState<number | null>(null)
  const onModelUpdated = useCallback((e: { api: GridApi }) => setShown(e.api.getDisplayedRowCount()), [])
  return { shown, onModelUpdated } as const
}

/** "583 securities", or "23 of 583 securities" when a search or filter shows fewer than all. */
export function countText(shown: number | null | undefined, total: number | null | undefined, noun: string): string {
  const fmt = (n: number) => n.toLocaleString()
  if (total == null) return shown == null ? '' : `${fmt(shown)} ${noun}`
  if (shown == null || shown >= total) return `${fmt(total)} ${noun}`
  return `${fmt(shown)} of ${fmt(total)} ${noun}`
}
