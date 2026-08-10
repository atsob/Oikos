import { useState, useCallback, useEffect, useMemo } from 'react'
import { getSettings, saveSettings, subscribeSettings } from './settings'
import type { AppSettings } from './settings'
import { getPref, setPref, subscribePref } from './preferences'
import type { GridApi, ColumnState, ColumnMovedEvent, ColumnResizedEvent, SortChangedEvent } from 'ag-grid-community'

export function useSettings(): [AppSettings, (s: AppSettings) => void] {
  const [settings, setSettings] = useState<AppSettings>(getSettings)
  useEffect(() => subscribeSettings(setSettings), [])
  return [settings, saveSettings]
}

// The user-configured auto-refresh interval (Tools -> System -> App Settings ->
// Live Data Refresh) for queries backed by data that can change in the background —
// live prices, balances refreshed by a sync job, linked transactions inserted from
// another page. Returns `false` (TanStack Query's "disabled" value) when the user
// has set it to 0.
export function useLiveRefetchInterval(): number | false {
  const [settings] = useSettings()
  return settings.liveRefreshSeconds > 0 ? settings.liveRefreshSeconds * 1000 : false
}

// Persists UI state (tab selections, saved filters, etc.) server-side via
// lib/preferences.ts, so it follows the user across devices/browsers/origins
// instead of being trapped in one browser's localStorage. Signature/behavior
// is unchanged from the old localStorage-only version, so call sites don't
// need to change.
export function usePersist<T>(key: string, defaultVal: T) {
  const [val, setVal] = useState<T>(() => getPref(key, defaultVal))
  useEffect(() => subscribePref(k => {
    if (k === key || k === '*') setVal(getPref(key, defaultVal))
  }), [key]) // eslint-disable-line react-hooks/exhaustive-deps
  const set = useCallback((v: T) => setPref(key, v), [key])
  return [val, set] as const
}

/**
 * Persists an ag-Grid's column order/visibility server-side via usePersist, keyed by
 * `key`, so a user's rearranged and shown/hidden columns stick across reloads and
 * devices instead of resetting every time. Pass in the grid's own columnDefs; get back
 * a reordered version to actually render, plus onColumnMoved/onColumnResized handlers
 * to wire up, and a `columns`/`toggleColumn` pair for a show/hide columns menu:
 *
 *   const gridCols = useGridColumnState('register', colDefs)
 *   <ColumnsMenu columns={gridCols.columns} onToggle={gridCols.toggleColumn} />
 *   <AgGridReact
 *     columnDefs={gridCols.colDefs}
 *     onColumnMoved={gridCols.onColumnMoved}
 *     onColumnResized={gridCols.onColumnResized}
 *   />
 *
 * Reordering the columnDefs array itself (rather than restoring order post-mount via
 * applyColumnState from onGridReady) sidesteps ag-Grid's own internal init sequence —
 * calling applyColumnState from onGridReady turned out not to reliably stick, since
 * ag-Grid does further column setup of its own after that callback returns which can
 * still override it. Baking the order (and hide flag) into columnDefs means the grid
 * is correct from its very first render, no timing race possible — the same reasoning
 * is why toggleColumn writes straight to the saved state instead of calling
 * api.setColumnsVisible(), letting that same columnDefs recompute drive the show/hide.
 *
 * onColumnMoved/onColumnResized only save once a drag actually finishes (event.finished)
 * AND it was actually the user dragging (event.source starts with "ui") — most pages
 * also call sizeColumnsToFit()/autoSizeAllColumns() on every data refresh, which fires
 * these same events with finished:true but a non-"ui" source; saving those would
 * silently overwrite the user's real layout with whatever size the auto-fit produced.
 */
export function useGridColumnState<T extends { colId?: string; field?: string; hide?: boolean | null; width?: number | null; flex?: number | null; headerName?: string; sort?: unknown; sortIndex?: unknown }>(key: string, colDefs: T[]) {
  const [state, setState] = usePersist<ColumnState[] | null>(`grid_cols_${key}`, null)

  const idOf = useCallback((d: T) => d.colId ?? d.field ?? '', [])

  const orderedColDefs = useMemo(() => {
    if (!state || state.length === 0) return colDefs
    const remaining = new Map(colDefs.map(d => [idOf(d), d]))
    const ordered: T[] = []
    for (const s of state) {
      const d = remaining.get(s.colId)
      if (d) {
        const overrides: Partial<T> = {}
        if (s.hide != null) overrides.hide = s.hide as T['hide']
        if (s.width != null) overrides.width = s.width as T['width']
        // ag-Grid clears a column's own flex (to null) the moment the user drags it to
        // a manual width — carried over unconditionally (unlike width/hide above) since
        // null here is meaningful: leaving the original colDef's flex in place would
        // make ag-Grid keep recalculating the width from flex and ignore the resize.
        if ('flex' in d || s.flex !== undefined) overrides.flex = s.flex as T['flex']
        // Same reasoning for sort: colDefs often hardcode an initial sort (e.g. Date
        // desc) purely to pick a sane first-render order. Without baking the user's
        // actual current sort back into colDefs, ag-Grid falls back to that hardcoded
        // default any time it has to re-evaluate columnDefs from scratch — e.g. a
        // full remount when the surrounding page swaps a loading spinner back in for
        // the grid — silently discarding a sort (or an explicit "no sort") the user
        // had chosen. Carried over unconditionally, same as flex, so "no sort" sticks
        // too instead of reverting to the original default.
        if ('sort' in d || s.sort !== undefined) { overrides.sort = s.sort as T['sort']; overrides.sortIndex = s.sortIndex as T['sortIndex'] }
        ordered.push(Object.keys(overrides).length ? { ...d, ...overrides } : d)
        remaining.delete(s.colId)
      }
    }
    return [...ordered, ...remaining.values()]
  }, [colDefs, state, idOf])

  const saveColumnState = useCallback((api: GridApi) => setState(api.getColumnState()), [setState])

  const onColumnMoved = useCallback((e: ColumnMovedEvent) => {
    if (e.finished && e.source.startsWith('ui')) saveColumnState(e.api)
  }, [saveColumnState])
  const onColumnResized = useCallback((e: ColumnResizedEvent) => {
    if (e.finished && e.source.startsWith('ui')) saveColumnState(e.api)
  }, [saveColumnState])
  // Unlike move/resize, ag-Grid never fires sortChanged on its own mid-render (no
  // equivalent of an auto-fit call) — every event here is a real sort change, either
  // the user clicking a header or code calling applyColumnState/setSortModel — so
  // there's no spurious-source case to filter out before persisting it.
  const onSortChanged = useCallback((e: SortChangedEvent) => saveColumnState(e.api), [saveColumnState])

  // Columns for a show/hide columns menu, in their current order. Utility columns
  // with no headerName (row-selection checkboxes, inline edit/delete action columns)
  // are excluded — hiding those would break row actions, not just data display.
  const columns = useMemo(
    () => orderedColDefs
      .filter(d => d.headerName)
      .map(d => ({ colId: idOf(d), headerName: d.headerName!, hidden: !!d.hide })),
    [orderedColDefs, idOf]
  )

  const toggleColumn = useCallback((colId: string) => {
    const visibleCount = columns.filter(c => !c.hidden).length
    const target = columns.find(c => c.colId === colId)
    if (target && !target.hidden && visibleCount <= 1) return // always keep at least one column visible

    // Ensure every current column has an entry to flip, including ones a
    // previously-saved state predates (a colDef added to the page since the
    // user last rearranged it) — those fall back to appearing at the end,
    // mirroring how orderedColDefs itself treats unrecognized columns.
    const known = new Set((state ?? []).map(s => s.colId))
    const base: ColumnState[] = [
      ...(state ?? []),
      ...colDefs.map(idOf).filter(id => !known.has(id)).map(id => ({ colId: id })),
    ]
    setState(base.map(s => s.colId === colId ? { ...s, hide: !s.hide } : s))
  }, [columns, state, colDefs, setState, idOf])

  return { colDefs: orderedColDefs, onColumnMoved, onColumnResized, onSortChanged, columns, toggleColumn }
}

/**
 * Persists an ag-Grid's column filter model server-side via usePersist, keyed by
 * `key`, so applied filters survive navigating away and back (e.g. clicking through
 * to Security Detail then hitting Back) instead of being lost on remount — ag-Grid's
 * own filter state lives only in the live grid instance, gone the moment the
 * component unmounts. Restore it from onGridReady, save on every filter change,
 * and clearFilters() resets both the grid and the saved copy for a "Clear Filters"
 * button:
 *
 *   const gridFilter = useGridFilterState('portfolio-action-signals')
 *   const { gridApi, onGridReady } = useGridApi(api => {
 *     if (gridFilter.filterModel) api.setFilterModel(gridFilter.filterModel)
 *   })
 *   <AgGridReact onGridReady={onGridReady} onFilterChanged={gridFilter.onFilterChanged} .../>
 *   <Button onClick={() => gridFilter.clearFilters(gridApi)}>Clear Filters</Button>
 */
export function useGridFilterState(key: string) {
  const [filterModel, setFilterModel] = usePersist<Record<string, unknown> | null>(`grid_filter_${key}`, null)

  const onFilterChanged = useCallback((e: { api: GridApi }) => {
    const model = e.api.getFilterModel()
    setFilterModel(Object.keys(model).length ? model : null)
  }, [setFilterModel])

  const hasFilters = !!filterModel && Object.keys(filterModel).length > 0

  const clearFilters = useCallback((api: GridApi | null) => {
    api?.setFilterModel(null)
    setFilterModel(null)
  }, [setFilterModel])

  return { filterModel, onFilterChanged, clearFilters, hasFilters } as const
}

// ── useGridApi ───────────────────────────────────────────────────────────────
// Captures a grid's GridApi for use outside ag-Grid (e.g. CopyToExcelButton),
// via onGridReady. Pass an optional onReady for anything else a grid already
// needs to do on ready (autoSizeAllColumns, etc.) instead of adding a second
// onGridReady prop, which ag-Grid only invokes once and the other would be lost.
export function useGridApi(onReady?: (api: GridApi) => void) {
  const [gridApi, setGridApi] = useState<GridApi | null>(null)
  const onGridReady = useCallback((e: { api: GridApi }) => {
    setGridApi(e.api)
    onReady?.(e.api)
  }, [onReady])
  return { gridApi, onGridReady } as const
}
