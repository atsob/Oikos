import { useState, useMemo, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getDbHealth, runDbMaintenance, getReferentialIntegrity, toolsSyncBalances,
  runSql, exportExcel, runBackup,
  getToolsPriceAnomalies, deleteHistoricalPrices,
  getMissingTxPrices, insertMissingPrices,
  getDummyPrices, normalizeInvestments, refreshHoldings,
  getSchedulerJobs, updateSchedulerJob, triggerSchedulerJob,
  getInvestmentConsistency, updateInvestmentRow,
  getMissingTransferMirrors, fixTransferMirrors,
  getUnlinkedTransferPairs, linkTransferPairs,
  getTransferSignMismatches, fixTransferSign,
  getMissingInvCashLinks, fixInvCashLinks,
  getLogs,
} from '@/lib/api'
import { PageHeader, Card, CardHeader, CardTitle, CardBody, Button, Spinner, ColHeader, useSortTable } from '@/components/ui'
import { cn } from '@/lib/utils'

// ── Tiny UI helpers ────────────────────────────────────────────────────────────
function Alert({ type, children }: { type: 'success' | 'error' | 'warning' | 'info'; children: React.ReactNode }) {
  const cls = {
    success: 'bg-green-50 border-green-200 text-green-800',
    error: 'bg-red-50 border-red-200 text-red-800',
    warning: 'bg-amber-50 border-amber-200 text-amber-800',
    info: 'bg-blue-50 border-blue-200 text-blue-800',
  }[type]
  return <div className={cn('rounded-md border px-4 py-2.5 text-sm', cls)}>{children}</div>
}

function ConfirmBanner({ message, onYes, onNo, yesLabel = 'Yes', noLabel = 'Cancel', isPending }:
  { message: string; onYes: () => void; onNo: () => void; yesLabel?: string; noLabel?: string; isPending?: boolean }) {
  return (
    <div className="rounded-md border border-amber-300 bg-amber-50 p-4 space-y-3">
      <p className="text-sm text-amber-900">{message}</p>
      <div className="flex gap-2">
        <Button variant="primary" size="sm" onClick={onYes} disabled={isPending}>
          {isPending ? <Spinner size={12} /> : null} {yesLabel}
        </Button>
        <Button variant="secondary" size="sm" onClick={onNo}>
          {noLabel}
        </Button>
      </div>
    </div>
  )
}

type Row = Record<string, unknown>

function DataTable({ rows, hideCols = [] }: { rows: Row[]; hideCols?: string[] }) {
  const { sorted, sortKey, sortDir, toggleSort } = useSortTable(rows ?? [], null)
  if (!rows || rows.length === 0) return <p className="text-sm text-slate-500">No results.</p>
  const cols = Object.keys(rows[0]).filter(c => !hideCols.includes(c))
  return (
    <div className="overflow-auto border border-slate-200 rounded-lg">
      <table className="w-full text-xs">
        <thead className="bg-slate-50 sticky top-0">
          <tr>
            {cols.map(c => <ColHeader key={c} label={c} sortKey={c} currentKey={sortKey} currentDir={sortDir} onSort={toggleSort} className="px-3 py-2 text-left text-slate-600" />)}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => (
            <tr key={i} className={i % 2 === 0 ? 'bg-white' : 'bg-slate-50'}>
              {cols.map(c => (
                <td key={c} className="px-3 py-1.5 text-slate-700 whitespace-nowrap">{String(row[c] ?? '')}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── DB Maintenance ─────────────────────────────────────────────────────────────
function DbMaintenance() {
  const { data, isLoading, refetch } = useQuery({ queryKey: ['db-health'], queryFn: getDbHealth })
  const [riResult, setRiResult] = useState<Row[] | null>(null)
  const [riLoading, setRiLoading] = useState(false)
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [selectedTable, setSelectedTable] = useState('')
  const [selectedOp, setSelectedOp] = useState('VACUUM ANALYZE')
  const [showIndexes, setShowIndexes] = useState(false)

  const maintMut = useMutation({
    mutationFn: ({ op, table, dbName }: { op: string; table?: string; dbName?: string }) =>
      runDbMaintenance(op, table, dbName),
    onSuccess: (d: { message: string }) => { setMsg({ type: 'success', text: d.message }); refetch() },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      setMsg({ type: 'error', text: e.response?.data?.detail ?? 'Operation failed' }),
  })

  const balMut = useMutation({
    mutationFn: (target: string) => toolsSyncBalances(target),
    onSuccess: () => setMsg({ type: 'success', text: 'Balances recalculated.' }),
    onError: () => setMsg({ type: 'error', text: 'Failed to recalculate balances.' }),
  })

  const tables = useMemo(() => (data?.tables ?? []).map((r: Row) => r.table_name as string), [data])

  async function runRI() {
    setRiLoading(true)
    try {
      const rows = await getReferentialIntegrity()
      setRiResult(rows)
    } finally {
      setRiLoading(false)
    }
  }

  const riIssues = riResult?.filter(r => typeof r.orphaned_rows === 'number' && (r.orphaned_rows as number) > 0) ?? []
  const riClean = riResult?.filter(r => typeof r.orphaned_rows === 'number' && (r.orphaned_rows as number) === 0) ?? []

  return (
    <div className="space-y-6">
      {msg && <Alert type={msg.type}>{msg.text}</Alert>}

      <Card>
        <CardHeader><CardTitle>📊 Table Health</CardTitle></CardHeader>
        <CardBody className="space-y-3">
          {isLoading ? <Spinner /> : (
            <>
              {(data?.tables ?? []).some((r: Row) => (r.dead_pct as number) > 10) && (
                <Alert type="warning">⚠️ Some tables have &gt;10% dead rows — consider running VACUUM ANALYZE.</Alert>
              )}
              <DataTable rows={data?.tables ?? []} />
              <button className="text-xs text-blue-600 underline" onClick={() => setShowIndexes(v => !v)}>
                {showIndexes ? '▾ Hide' : '▸ Show'} index usage
              </button>
              {showIndexes && <DataTable rows={data?.indexes ?? []} />}
            </>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader><CardTitle>⚡ Database-Wide Operations</CardTitle></CardHeader>
        <CardBody>
          <p className="text-xs text-slate-500 mb-4">VACUUM reclaims dead tuples. ANALYZE updates planner statistics. REINDEX rebuilds indexes.</p>
          <div className="flex flex-wrap gap-3">
            {['ANALYZE', 'VACUUM ANALYZE', 'REINDEX DATABASE'].map(op => (
              <Button key={op} variant="secondary" size="sm" disabled={maintMut.isPending}
                onClick={() => maintMut.mutate({ op })}>
                {maintMut.isPending ? <Spinner size={12} /> : null} ▶ {op}
              </Button>
            ))}
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader><CardTitle>💰 Recalculate Account Balances</CardTitle></CardHeader>
        <CardBody>
          <div className="flex flex-wrap gap-3">
            {[
              { label: 'Cash / Bank / Assets', target: 'cash' },
              { label: 'Brokerage / Investment', target: 'investment' },
              { label: 'Pension', target: 'pension' },
            ].map(({ label, target }) => (
              <Button key={target} variant="secondary" size="sm"
                disabled={balMut.isPending} onClick={() => balMut.mutate(target)}>
                {balMut.isPending ? <Spinner size={12} /> : null} ▶ Recalculate {label}
              </Button>
            ))}
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader><CardTitle>🎯 Per-Table Operations</CardTitle></CardHeader>
        <CardBody className="space-y-3">
          <div className="flex flex-wrap gap-3 items-center">
            <select value={selectedTable} onChange={e => setSelectedTable(e.target.value)}
              className="border border-slate-300 rounded-md px-2 py-1.5 text-sm">
              <option value="">Select table…</option>
              {tables.map((t: string) => <option key={t} value={t}>{t}</option>)}
            </select>
            <select value={selectedOp} onChange={e => setSelectedOp(e.target.value)}
              className="border border-slate-300 rounded-md px-2 py-1.5 text-sm">
              {['VACUUM ANALYZE', 'VACUUM', 'ANALYZE', 'REINDEX TABLE', 'VACUUM FULL'].map(op => (
                <option key={op} value={op}>{op}</option>
              ))}
            </select>
            <Button size="sm" disabled={!selectedTable || maintMut.isPending}
              onClick={() => maintMut.mutate({ op: selectedOp, table: selectedTable })}>
              ▶ Run
            </Button>
          </div>
          {selectedOp === 'VACUUM FULL' && (
            <Alert type="warning">⚠️ VACUUM FULL holds an exclusive lock — no reads or writes possible during this time.</Alert>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader><CardTitle>🔗 Referential Integrity Check</CardTitle></CardHeader>
        <CardBody className="space-y-3">
          <p className="text-xs text-slate-500">Scans every foreign-key constraint and counts orphaned rows.</p>
          <Button size="sm" variant="secondary" onClick={runRI} disabled={riLoading}>
            {riLoading ? <Spinner size={12} /> : null} 🔍 Run Integrity Check
          </Button>
          {riResult && (
            <>
              {riIssues.length === 0 && riClean.length > 0 && (
                <Alert type="success">✅ All {riClean.length} foreign-key constraints satisfied — no orphaned rows.</Alert>
              )}
              {riIssues.length > 0 && (
                <div>
                  <Alert type="error">❌ {riIssues.length} constraint(s) have orphaned rows</Alert>
                  <div className="mt-2"><DataTable rows={riIssues} /></div>
                </div>
              )}
              {riClean.length > 0 && (
                <details className="text-sm">
                  <summary className="cursor-pointer text-slate-500">✅ {riClean.length} clean constraint(s)</summary>
                  <div className="mt-2"><DataTable rows={riClean} /></div>
                </details>
              )}
            </>
          )}
        </CardBody>
      </Card>
    </div>
  )
}

// ── SQL Interface ──────────────────────────────────────────────────────────────
function SqlInterface() {
  const DEFAULT_SQL = "SELECT table_name\nFROM information_schema.tables\nWHERE table_schema = 'public'\nORDER BY table_name;"
  const [sql, setSql] = useState(DEFAULT_SQL)
  const [result, setResult] = useState<{ type: string; rows?: Row[]; rows_affected?: number; error?: string } | null>(null)

  const runMut = useMutation({
    mutationFn: runSql,
    onSuccess: d => setResult(d),
    onError: (e: { response?: { data?: { detail?: string } }; message?: string }) =>
      setResult({ type: 'error', error: e.response?.data?.detail ?? e.message }),
  })

  function exportCsv() {
    if (!result?.rows) return
    const cols = Object.keys(result.rows[0])
    const csv = [cols.join(','), ...result.rows.map(r => cols.map(c => JSON.stringify(r[c] ?? '')).join(','))].join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'query_result.csv'
    a.click()
  }

  return (
    <Card>
      <CardHeader><CardTitle>🛢 SQL Query Interface</CardTitle></CardHeader>
      <CardBody className="space-y-3">
        <p className="text-xs text-slate-500">SELECT, INSERT, UPDATE and DELETE allowed. DROP, TRUNCATE, ALTER, CREATE blocked.</p>
        <textarea
          value={sql}
          onChange={e => setSql(e.target.value)}
          rows={8}
          spellCheck={false}
          className="w-full font-mono text-sm border border-slate-300 rounded-md p-3 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <div className="flex gap-2">
          <Button size="sm" onClick={() => runMut.mutate(sql)} disabled={runMut.isPending}>
            {runMut.isPending ? <Spinner size={12} /> : null} ▶ Run Query
          </Button>
          <Button size="sm" variant="secondary" onClick={() => { setSql(DEFAULT_SQL); setResult(null) }}>
            ✖ Clear
          </Button>
          {result?.rows && result.rows.length > 0 && (
            <Button size="sm" variant="secondary" onClick={exportCsv}>⬇ Export CSV</Button>
          )}
        </div>
        {result?.error && <Alert type="error">{result.error}</Alert>}
        {result?.type === 'dml' && (
          <Alert type="success">✅ Query executed — {result.rows_affected} row(s) affected.</Alert>
        )}
        {result?.type === 'select' && result.rows && (
          <div className="space-y-1">
            <p className="text-xs text-slate-500">{result.rows.length.toLocaleString()} row(s)</p>
            <div className="max-h-96 overflow-auto"><DataTable rows={result.rows} /></div>
          </div>
        )}
      </CardBody>
    </Card>
  )
}

// ── Data Export ────────────────────────────────────────────────────────────────
function DataExport() {
  const [status, setStatus] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function generate() {
    setLoading(true)
    setStatus(null)
    try {
      const blob = await exportExcel()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `oikos_export_${new Date().toISOString().slice(0, 10)}.xlsx`
      a.click()
      setStatus('Export downloaded.')
    } catch {
      setStatus('Export failed.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card>
      <CardHeader><CardTitle>📤 Full Data Export</CardTitle></CardHeader>
      <CardBody className="space-y-3">
        <p className="text-xs text-slate-500">
          Exports all major tables to a single Excel workbook. Historical prices limited to last 2 years.
        </p>
        <Button onClick={generate} disabled={loading}>
          {loading ? <Spinner size={14} /> : null} ⬇️ Generate &amp; Download Export
        </Button>
        {status && <Alert type={status.includes('failed') ? 'error' : 'success'}>{status}</Alert>}
      </CardBody>
    </Card>
  )
}

// ── Backup & Restore ───────────────────────────────────────────────────────────
function BackupRestore() {
  const backupMut = useMutation({ mutationFn: runBackup })
  return (
    <Card>
      <CardHeader><CardTitle>💾 Backup &amp; Restore</CardTitle></CardHeader>
      <CardBody className="space-y-3">
        <p className="text-xs text-slate-500">Creates a pg_dump backup of the database.</p>
        <Button onClick={() => backupMut.mutate()} disabled={backupMut.isPending}>
          {backupMut.isPending ? <Spinner size={14} /> : null} 💾 Create Backup
        </Button>
        {backupMut.isSuccess && <Alert type="success">✅ Backup completed.</Alert>}
        {backupMut.isError && <Alert type="error">Backup failed.</Alert>}
      </CardBody>
    </Card>
  )
}

// ── Price Quality ──────────────────────────────────────────────────────────────
function PriceQuality() {
  const [threshold, setThreshold] = useState(100)
  const [selectedSecs, setSelectedSecs] = useState<string[]>([])
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [confirm, setConfirm] = useState<'selected' | 'all' | null>(null)
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const qc = useQueryClient()

  const { data: allRows = [], isLoading, refetch } = useQuery({
    queryKey: ['price-anomalies', threshold],
    queryFn: () => getToolsPriceAnomalies(threshold),
    staleTime: 60_000,
  })

  const secNames = useMemo(() => [...new Set((allRows as Row[]).map(r => r.security_name as string))].sort(), [allRows])
  const rows: Row[] = useMemo(
    () => selectedSecs.length ? (allRows as Row[]).filter(r => selectedSecs.includes(r.security_name as string)) : allRows as Row[],
    [allRows, selectedSecs],
  )

  const { sorted: sortedRows, sortKey: pqSK, sortDir: pqSD, toggleSort: pqSort } = useSortTable(rows, 'date', 'desc')

  const delMut = useMutation({
    mutationFn: deleteHistoricalPrices,
    onSuccess: (d: { deleted: number }) => {
      setMsg({ type: 'success', text: `Deleted ${d.deleted} price record(s).` })
      setChecked(new Set()); setConfirm(null); refetch()
    },
    onError: () => { setMsg({ type: 'error', text: 'Delete failed.' }); setConfirm(null) },
  })

  function toggleCheck(i: number) {
    setChecked(prev => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s })
  }

  function doDelete(target: Row[]) {
    delMut.mutate(target.map(r => ({ securities_id: r.securities_id as number, date: r.date as string })))
  }

  if (isLoading) return <Spinner />
  const display = ['security_name', 'date', 'price', 'prev_close', 'next_close', 'pct_vs_prev', 'pct_vs_next', 'source']
  const selectedRows = rows.filter((_, i) => checked.has(i))

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader><CardTitle>🔍 Price Data Quality</CardTitle></CardHeader>
        <CardBody className="space-y-4">
          <p className="text-xs text-slate-500">Flags prices that changed by more than the threshold vs the previous or next trading day.</p>
          <div className="flex items-center gap-4">
            <label className="text-sm font-medium">Flag when move exceeds (%):</label>
            <input type="range" min={10} max={1000} step={10} value={threshold}
              onChange={e => { setThreshold(Number(e.target.value)); setChecked(new Set()) }}
              className="w-40" />
            <span className="text-sm font-mono">{threshold}%</span>
          </div>

          {allRows.length === 0 ? (
            <Alert type="success">No prices flagged at the {threshold}% threshold.</Alert>
          ) : (
            <>
              <Alert type="warning">{(allRows as Row[]).length.toLocaleString()} suspicious price record(s) — {secNames.length} security/ies.</Alert>

              <div className="flex flex-wrap gap-2">
                {secNames.map(s => (
                  <button key={s}
                    className={cn('px-2 py-0.5 rounded-full text-xs border transition-colors',
                      selectedSecs.includes(s) ? 'bg-blue-100 border-blue-400 text-blue-800' : 'bg-white border-slate-300 text-slate-600 hover:bg-slate-50')}
                    onClick={() => setSelectedSecs(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s])}>
                    {s}
                  </button>
                ))}
                {selectedSecs.length > 0 && (
                  <button className="text-xs text-slate-400 underline" onClick={() => setSelectedSecs([])}>clear filter</button>
                )}
              </div>

              {msg && <Alert type={msg.type}>{msg.text}</Alert>}

              {confirm === 'selected' && (
                <ConfirmBanner
                  message={`Delete ${selectedRows.length} selected price record(s)? This cannot be undone.`}
                  onYes={() => doDelete(selectedRows)} onNo={() => setConfirm(null)}
                  yesLabel="Yes, delete" isPending={delMut.isPending} />
              )}
              {confirm === 'all' && (
                <ConfirmBanner
                  message={`Delete all ${rows.length} listed price record(s)? This cannot be undone.`}
                  onYes={() => doDelete(rows)} onNo={() => setConfirm(null)}
                  yesLabel="Yes, delete all" isPending={delMut.isPending} />
              )}

              <div className="flex gap-2">
                <Button size="sm" variant={checked.size > 0 ? 'destructive' : 'secondary'}
                  disabled={checked.size === 0} onClick={() => setConfirm('selected')}>
                  🗑 Delete {checked.size > 0 ? `${checked.size} selected` : 'selected'}
                </Button>
                <Button size="sm" variant="destructive" onClick={() => setConfirm('all')}>
                  🗑 Delete all {rows.length} listed
                </Button>
              </div>

              <div className="overflow-auto border border-slate-200 rounded-lg max-h-96">
                <table className="w-full text-xs">
                  <thead className="bg-slate-50 sticky top-0">
                    <tr>
                      <th className="px-2 py-2 text-left">
                        <input type="checkbox"
                          checked={rows.length > 0 && checked.size === rows.length}
                          onChange={e => setChecked(e.target.checked ? new Set(rows.map((_, i) => i)) : new Set())} />
                      </th>
                      {display.map(c => <ColHeader key={c} label={c} sortKey={c} currentKey={pqSK} currentDir={pqSD} onSort={pqSort} className="px-3 py-2 text-left text-slate-600" />)}
                    </tr>
                  </thead>
                  <tbody>
                    {sortedRows.map((row) => { const i = rows.indexOf(row); return (
                      <tr key={i} className={cn(checked.has(i) ? 'bg-red-50' : i % 2 === 0 ? 'bg-white' : 'bg-slate-50')}>
                        <td className="px-2 py-1.5">
                          <input type="checkbox" checked={checked.has(i)} onChange={() => toggleCheck(i)} />
                        </td>
                        {display.map(c => (
                          <td key={c} className="px-3 py-1.5 text-slate-700 whitespace-nowrap">{String(row[c] ?? '')}</td>
                        ))}
                      </tr>
                    )})}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </CardBody>
      </Card>
    </div>
  )
}

// ── Fill Missing Prices ────────────────────────────────────────────────────────
function FillMissingPrices() {
  const [selectedSecs, setSelectedSecs] = useState<string[]>([])
  const [confirm, setConfirm] = useState<'filtered' | 'all' | null>(null)
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const { data: allRows = [], isLoading, refetch } = useQuery({
    queryKey: ['missing-tx-prices'], queryFn: getMissingTxPrices, staleTime: 60_000,
  })

  const secNames = useMemo(() => [...new Set((allRows as Row[]).map(r => r.security_name as string))].sort(), [allRows])
  const filtered: Row[] = useMemo(
    () => selectedSecs.length ? (allRows as Row[]).filter(r => selectedSecs.includes(r.security_name as string)) : allRows as Row[],
    [allRows, selectedSecs],
  )

  const insMut = useMutation({
    mutationFn: insertMissingPrices,
    onSuccess: (d: { inserted: number }) => {
      setMsg({ type: 'success', text: `Inserted ${d.inserted} price record(s).` })
      setConfirm(null); refetch()
    },
    onError: () => { setMsg({ type: 'error', text: 'Insert failed.' }); setConfirm(null) },
  })

  function doInsert(target: Row[]) {
    insMut.mutate(target.map(r => ({ securities_id: r.securities_id as number, date: r.date as string, price: r.price as number })))
  }

  if (isLoading) return <Spinner />

  return (
    <Card>
      <CardHeader><CardTitle>📥 Fill Missing Prices from Transactions</CardTitle></CardHeader>
      <CardBody className="space-y-4">
        <p className="text-xs text-slate-500">
          Finds transaction dates with no Historical Price entry and fills them from the transaction's Price Per Share.
        </p>
        {allRows.length === 0 ? (
          <Alert type="success">No missing prices found — every transaction date already has a Historical Price entry.</Alert>
        ) : (
          <>
            <Alert type="info">{(allRows as Row[]).length.toLocaleString()} missing price record(s) across {secNames.length} security/ies.</Alert>

            <div className="flex flex-wrap gap-2">
              {secNames.map(s => (
                <button key={s}
                  className={cn('px-2 py-0.5 rounded-full text-xs border transition-colors',
                    selectedSecs.includes(s) ? 'bg-blue-100 border-blue-400 text-blue-800' : 'bg-white border-slate-300 text-slate-600 hover:bg-slate-50')}
                  onClick={() => setSelectedSecs(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s])}>
                  {s}
                </button>
              ))}
              {selectedSecs.length > 0 && (
                <button className="text-xs text-slate-400 underline" onClick={() => setSelectedSecs([])}>clear filter</button>
              )}
            </div>

            {msg && <Alert type={msg.type}>{msg.text}</Alert>}

            {confirm === 'filtered' && (
              <ConfirmBanner
                message={`Insert ${filtered.length.toLocaleString()} missing price record(s)? Existing prices will not be overwritten.`}
                onYes={() => doInsert(filtered)} onNo={() => setConfirm(null)}
                yesLabel="Yes, insert" isPending={insMut.isPending} />
            )}
            {confirm === 'all' && (
              <ConfirmBanner
                message={`Insert all ${(allRows as Row[]).length.toLocaleString()} missing price records? Existing prices will not be overwritten.`}
                onYes={() => doInsert(allRows as Row[])} onNo={() => setConfirm(null)}
                yesLabel="Yes, insert all" isPending={insMut.isPending} />
            )}

            <div className="flex gap-2">
              <Button size="sm" onClick={() => setConfirm('filtered')}>
                📥 Insert {selectedSecs.length > 0 ? `filtered (${filtered.length})` : 'all'}
              </Button>
              {selectedSecs.length > 0 && (
                <Button size="sm" variant="secondary" onClick={() => setConfirm('all')}>
                  📥 Insert all ({(allRows as Row[]).length})
                </Button>
              )}
            </div>

            <div className="max-h-80 overflow-auto">
              <DataTable rows={filtered} hideCols={['securities_id']} />
            </div>
          </>
        )}
      </CardBody>
    </Card>
  )
}

// ── Normalize Investments ──────────────────────────────────────────────────────
function NormalizeInvestments() {
  const [tolerancePct, setTolerancePct] = useState(10)
  const [selectedAccs, setSelectedAccs] = useState<string[]>([])
  const [selectedSecs, setSelectedSecs] = useState<string[]>([])
  const [confirm, setConfirm] = useState<'filtered' | 'all' | null>(null)
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const { data: allRows = [], isLoading, refetch } = useQuery({
    queryKey: ['dummy-prices', tolerancePct],
    queryFn: () => getDummyPrices(tolerancePct),
    staleTime: 30_000,
  })

  const accNames = useMemo(() => [...new Set((allRows as Row[]).map(r => r.account_name as string))].sort(), [allRows])
  const filtered1: Row[] = useMemo(
    () => selectedAccs.length ? (allRows as Row[]).filter(r => selectedAccs.includes(r.account_name as string)) : allRows as Row[],
    [allRows, selectedAccs],
  )
  const secNames = useMemo(() => [...new Set(filtered1.map(r => r.security_name as string))].sort(), [filtered1])
  const filtered: Row[] = useMemo(
    () => selectedSecs.length ? filtered1.filter(r => selectedSecs.includes(r.security_name as string)) : filtered1,
    [filtered1, selectedSecs],
  )

  const normMut = useMutation({
    mutationFn: (ids: number[]) => normalizeInvestments(ids),
    onSuccess: (d: { updated: number }) => {
      setMsg({ type: 'success', text: `Updated ${d.updated} investment row(s).` })
      setConfirm(null); refetch()
    },
    onError: () => { setMsg({ type: 'error', text: 'Normalize failed.' }); setConfirm(null) },
  })

  const holdMut = useMutation({
    mutationFn: refreshHoldings,
    onSuccess: () => setMsg({ type: 'success', text: 'Holdings recalculated.' }),
  })

  if (isLoading) return <Spinner />

  return (
    <Card>
      <CardHeader><CardTitle>⚖ Normalize Investment Prices</CardTitle></CardHeader>
      <CardBody className="space-y-4">
        <p className="text-xs text-slate-500">
          Finds transactions with placeholder prices and updates Price Per Share to the actual historical close.
        </p>

        <div className="flex items-center gap-4">
          <label className="text-sm font-medium">Price tolerance vs. historical close (%):</label>
          <input type="range" min={5} max={50} step={1} value={tolerancePct}
            onChange={e => { setTolerancePct(Number(e.target.value)); setSelectedAccs([]); setSelectedSecs([]) }}
            className="w-32" />
          <span className="text-sm font-mono">{tolerancePct}%</span>
        </div>

        {allRows.length === 0 ? (
          <Alert type="success">No investments with dummy prices found.</Alert>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-4 text-center">
              {[
                { label: 'Total flagged', value: (allRows as Row[]).length },
                { label: 'Accounts', value: accNames.length },
                { label: 'After filters', value: filtered.length },
              ].map(m => (
                <div key={m.label} className="bg-slate-50 rounded-lg p-3">
                  <div className="text-2xl font-bold text-slate-800">{m.value}</div>
                  <div className="text-xs text-slate-500">{m.label}</div>
                </div>
              ))}
            </div>

            <div className="space-y-2">
              <p className="text-xs font-medium text-slate-600">Filter by account:</p>
              <div className="flex flex-wrap gap-2">
                {accNames.map(a => (
                  <button key={a}
                    className={cn('px-2 py-0.5 rounded-full text-xs border transition-colors',
                      selectedAccs.includes(a) ? 'bg-blue-100 border-blue-400 text-blue-800' : 'bg-white border-slate-300 text-slate-600 hover:bg-slate-50')}
                    onClick={() => setSelectedAccs(prev => prev.includes(a) ? prev.filter(x => x !== a) : [...prev, a])}>
                    {a}
                  </button>
                ))}
              </div>
              {secNames.length > 0 && (
                <>
                  <p className="text-xs font-medium text-slate-600">Filter by security:</p>
                  <div className="flex flex-wrap gap-2">
                    {secNames.map(s => (
                      <button key={s}
                        className={cn('px-2 py-0.5 rounded-full text-xs border transition-colors',
                          selectedSecs.includes(s) ? 'bg-blue-100 border-blue-400 text-blue-800' : 'bg-white border-slate-300 text-slate-600 hover:bg-slate-50')}
                        onClick={() => setSelectedSecs(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s])}>
                        {s}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>

            {msg && <Alert type={msg.type}>{msg.text}</Alert>}

            {confirm === 'filtered' && (
              <ConfirmBanner
                message={`This will overwrite prices and quantities for ${filtered.length.toLocaleString()} investment row(s). Cannot be undone.`}
                onYes={() => normMut.mutate(filtered.map(r => r.investments_id as number))}
                onNo={() => setConfirm(null)} yesLabel="Yes, normalize" isPending={normMut.isPending} />
            )}
            {confirm === 'all' && (
              <ConfirmBanner
                message={`This will overwrite prices and quantities for all ${(allRows as Row[]).length.toLocaleString()} flagged rows. Cannot be undone.`}
                onYes={() => normMut.mutate((allRows as Row[]).map(r => r.investments_id as number))}
                onNo={() => setConfirm(null)} yesLabel="Yes, normalize all" isPending={normMut.isPending} />
            )}

            <div className="flex gap-2 flex-wrap">
              <Button size="sm" onClick={() => setConfirm('filtered')} disabled={filtered.length === 0}>
                ⚖ Normalize {(selectedAccs.length || selectedSecs.length) ? `filtered (${filtered.length})` : 'all'}
              </Button>
              {(selectedAccs.length || selectedSecs.length) > 0 && (
                <Button size="sm" variant="secondary" onClick={() => setConfirm('all')}>
                  ⚖ Normalize all ({(allRows as Row[]).length})
                </Button>
              )}
              <Button size="sm" variant="secondary" onClick={() => holdMut.mutate()} disabled={holdMut.isPending}>
                {holdMut.isPending ? <Spinner size={12} /> : null} 🔄 Refresh Holdings
              </Button>
            </div>

            <div className="max-h-80 overflow-auto">
              <DataTable rows={filtered} hideCols={['investments_id', 'accounts_id', 'securities_id']} />
            </div>
          </>
        )}
      </CardBody>
    </Card>
  )
}

// ── Investment Data Quality ────────────────────────────────────────────────────
function InvestmentDataQuality() {
  const [accountIds, setAccountIds] = useState<number[]>([])
  const [actionFilter, setActionFilter] = useState<string[]>([])
  const [anomaliesOnly, setAnomaliesOnly] = useState(true)
  const [excludeZeroPrice, setExcludeZeroPrice] = useState(true)
  const [excludeZeroQty, setExcludeZeroQty] = useState(false)
  const [runResult, setRunResult] = useState<Row[] | null>(null)
  const [edits, setEdits] = useState<Record<number, Record<string, number | null>>>({})
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [loading, setLoading] = useState(false)

  const ALL_ACTIONS = ['Buy', 'Sell', 'Dividend', 'IntInc', 'RtrnCap', 'CashIn', 'CashOut', 'MiscExp', 'Reinvest', 'ShrIn', 'ShrOut']
  const EDITABLE = ['quantity', 'price', 'commission', 'total_acc', 'total_sec', 'fx_rate']

  // Accounts derived from results — only accounts that have issues appear as chips
  const invAccounts: { id: number; name: string }[] = useMemo(() => {
    if (!runResult) return []
    const seen = new Map<number, string>()
    runResult.forEach(r => {
      const id = r.accounts_id as number
      if (id != null && !seen.has(id)) seen.set(id, r.account as string)
    })
    return [...seen.entries()].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name))
  }, [runResult])

  function applyFilters(rows: Row[]) {
    let d = rows
    if (accountIds.length) d = d.filter(r => accountIds.includes(r.accounts_id as number))
    if (actionFilter.length) d = d.filter(r => actionFilter.includes(r.action as string))
    if (anomaliesOnly) d = d.filter(r => (r.anomalies as string) !== '')
    return d
  }

  function filterAnomalyText(text: string) {
    if (excludeZeroPrice) text = text.replace(/(?:;\s*)?Price is 0 \/ NULL/g, '').replace(/^;\s*/, '').trim().replace(/^; /, '')
    if (excludeZeroQty) text = text.replace(/(?:;\s*)?Quantity is 0 \/ NULL/g, '').replace(/^;\s*/, '').trim().replace(/^; /, '')
    return text
  }

  async function runCheck() {
    setLoading(true)
    setRunResult(null); setEdits({}); setAccountIds([])
    try {
      const rows = await getInvestmentConsistency()
      setRunResult(rows)
    } finally {
      setLoading(false)
    }
  }

  const displayed = useMemo(() => {
    if (!runResult) return []
    return applyFilters(runResult.map(r => ({
      ...r,
      anomalies: filterAnomalyText(r.anomalies as string || ''),
    })))
  }, [runResult, accountIds, actionFilter, anomaliesOnly, excludeZeroPrice, excludeZeroQty])

  const { sorted: idSorted, sortKey: idSK, sortDir: idSD, toggleSort: idSort } = useSortTable(displayed, 'date', 'desc')

  const saveMut = useMutation({
    mutationFn: async () => {
      let saved = 0
      for (const [idStr, changes] of Object.entries(edits)) {
        if (Object.keys(changes).length > 0) {
          await updateInvestmentRow(Number(idStr), changes)
          saved++
        }
      }
      return saved
    },
    onSuccess: (saved: number) => {
      setMsg({ type: 'success', text: `Saved ${saved} row(s). Re-run the check to refresh anomaly flags.` })
      setEdits({}); setRunResult(null)
    },
    onError: () => setMsg({ type: 'error', text: 'Save failed.' }),
  })

  const totalRecords = runResult?.length ?? 0
  const anomalyCount = runResult?.filter(r => filterAnomalyText(r.anomalies as string || '') !== '').length ?? 0

  return (
    <Card>
      <CardHeader><CardTitle>🩺 Investment Data Quality</CardTitle></CardHeader>
      <CardBody className="space-y-4">
        <p className="text-xs text-slate-500">Detects anomalies in Quantity × Price, Commission, Total and FX Rate.</p>

        <div className="flex flex-wrap gap-4 items-center">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={anomaliesOnly} onChange={e => setAnomaliesOnly(e.target.checked)} />
            Anomalies only
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={excludeZeroPrice} onChange={e => setExcludeZeroPrice(e.target.checked)} />
            Exclude "Price is 0 / NULL"
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={excludeZeroQty} onChange={e => setExcludeZeroQty(e.target.checked)} />
            Exclude "Quantity is 0 / NULL"
          </label>
        </div>

        {invAccounts.length > 0 && (
          <div className="flex flex-wrap gap-2 items-center">
            <span className="text-xs text-slate-500 font-medium">Account:</span>
            {invAccounts.map(a => (
              <button key={a.id}
                className={cn('px-2 py-0.5 rounded-full text-xs border transition-colors',
                  accountIds.includes(a.id) ? 'bg-indigo-100 border-indigo-400 text-indigo-800' : 'bg-white border-slate-300 text-slate-600 hover:bg-slate-50')}
                onClick={() => setAccountIds(prev => prev.includes(a.id) ? prev.filter(x => x !== a.id) : [...prev, a.id])}>
                {a.name}
              </button>
            ))}
            {accountIds.length > 0 && <button className="text-xs text-slate-400 underline" onClick={() => setAccountIds([])}>clear</button>}
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          {ALL_ACTIONS.map(a => (
            <button key={a}
              className={cn('px-2 py-0.5 rounded-full text-xs border transition-colors',
                actionFilter.includes(a) ? 'bg-blue-100 border-blue-400 text-blue-800' : 'bg-white border-slate-300 text-slate-600 hover:bg-slate-50')}
              onClick={() => setActionFilter(prev => prev.includes(a) ? prev.filter(x => x !== a) : [...prev, a])}>
              {a}
            </button>
          ))}
          {actionFilter.length > 0 && <button className="text-xs text-slate-400 underline" onClick={() => setActionFilter([])}>clear</button>}
        </div>

        <Button size="sm" onClick={runCheck} disabled={loading}>
          {loading ? <Spinner size={12} /> : null} 🔍 Run Check
        </Button>

        {runResult && (
          <div className="grid grid-cols-3 gap-4 text-center">
            {[
              { label: 'Total records', value: totalRecords },
              { label: '⚠️ Anomalies', value: anomalyCount },
              { label: '✅ Clean', value: totalRecords - anomalyCount },
            ].map(m => (
              <div key={m.label} className="bg-slate-50 rounded-lg p-3">
                <div className="text-2xl font-bold text-slate-800">{m.value}</div>
                <div className="text-xs text-slate-500">{m.label}</div>
              </div>
            ))}
          </div>
        )}

        {msg && <Alert type={msg.type}>{msg.text}</Alert>}

        {displayed.length > 0 && (
          <>
            <p className="text-xs text-slate-500">Showing {displayed.length} record(s). Edit numeric fields inline.</p>
            <div className="overflow-auto border border-slate-200 rounded-lg max-h-96">
              <table className="w-full text-xs">
                <thead className="bg-slate-50 sticky top-0">
                  <tr>
                    {(['date', 'account', 'security', 'action'] as const).map(c => (
                      <ColHeader key={c} label={c} sortKey={c} currentKey={idSK} currentDir={idSD} onSort={idSort} className="px-3 py-2 text-left text-slate-600" />
                    ))}
                    {['quantity', 'price', 'commission', 'total_acc', 'total_sec', 'fx_rate', '⚠️ anomalies', '💡 recommendations'].map(c => (
                      <th key={c} className="px-3 py-2 text-left font-medium text-slate-600 whitespace-nowrap">{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {idSorted.map((row, i) => {
                    const id = row.investments_id as number
                    const rowEdits = edits[id] ?? {}
                    return (
                      <tr key={id ?? i} className={i % 2 === 0 ? 'bg-white' : 'bg-slate-50'}>
                        {['date', 'account', 'security', 'action'].map(c => (
                          <td key={c} className="px-3 py-1.5 text-slate-700 whitespace-nowrap">{String(row[c] ?? '')}</td>
                        ))}
                        {EDITABLE.map(c => (
                          <td key={c} className="px-1 py-1">
                            <input type="number" step="any"
                              defaultValue={String(rowEdits[c] !== undefined ? rowEdits[c] : (row[c] ?? ''))}
                              className="w-24 border border-transparent rounded px-1 py-0.5 text-xs focus:border-blue-400 focus:outline-none"
                              onChange={e => {
                                const val = e.target.value === '' ? null : Number(e.target.value)
                                setEdits(prev => ({ ...prev, [id]: { ...(prev[id] ?? {}), [c]: val } }))
                              }} />
                          </td>
                        ))}
                        <td className="px-3 py-1.5 text-amber-700 max-w-xs truncate">{String(row.anomalies ?? '')}</td>
                        <td className="px-3 py-1.5 text-blue-700 max-w-xs truncate">{String(row.recommendations ?? '')}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <Button size="sm" onClick={() => saveMut.mutate()} disabled={Object.keys(edits).length === 0 || saveMut.isPending}>
              {saveMut.isPending ? <Spinner size={12} /> : null} 💾 Save Changes
            </Button>
          </>
        )}
        {runResult && displayed.length === 0 && (
          <Alert type="success">No anomalies found — all investment records look consistent!</Alert>
        )}
      </CardBody>
    </Card>
  )
}

// ── Fix Missing Transfer Mirrors ───────────────────────────────────────────────
function FixMissingTransferMirrors() {
  const [filterAcc, setFilterAcc] = useState<string>('')
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [confirm, setConfirm] = useState<'selected' | 'all' | null>(null)
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const { data: allRows = [], isLoading, refetch } = useQuery({
    queryKey: ['missing-transfer-mirrors'], queryFn: getMissingTransferMirrors, staleTime: 30_000,
  })

  const tgtAccounts = useMemo(() => {
    const m = new Map<string, string>()
    ;(allRows as Row[]).forEach(r => m.set(r.tgt_acc_id as string, r.target_account as string))
    return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1]))
  }, [allRows])

  const rows: Row[] = useMemo(
    () => filterAcc ? (allRows as Row[]).filter(r => String(r.tgt_acc_id) === filterAcc) : allRows as Row[],
    [allRows, filterAcc],
  )

  const { sorted: fmtSorted, sortKey: fmtSK, sortDir: fmtSD, toggleSort: fmtSort } = useSortTable(rows, 'date', 'asc')

  const COLS = ['transactions_id', 'issue_type', 'date', 'source_account', 'payee', 'description', 'source_amount', 'target_account', 'transfers_id']

  const fixMut = useMutation({
    mutationFn: (ids: number[]) => fixTransferMirrors(ids),
    onSuccess: (d: { created: number; errors: string[] }) => {
      setMsg({ type: d.errors.length > 0 ? 'warning' : 'success', text: `Created ${d.created} mirror(s). ${d.errors.join(' ')}` })
      setChecked(new Set()); setConfirm(null); refetch()
    },
    onError: () => { setMsg({ type: 'error', text: 'Fix failed.' }); setConfirm(null) },
  })

  const selectedIds = rows.filter((_, i) => checked.has(i)).map(r => r.transactions_id as number)

  if (isLoading) return <Spinner />

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader><CardTitle>🔄 Fix Missing Transfer Mirrors</CardTitle></CardHeader>
        <CardBody className="space-y-4">
          <p className="text-xs text-slate-500">
            Detects transactions with Accounts_Id_Target set but no corresponding mirror transaction. Creates the missing mirror row.
          </p>

          {allRows.length === 0 ? (
            <Alert type="success">✅ No missing transfer mirrors found.</Alert>
          ) : (
            <>
              <Alert type="warning">⚠️ {rows.length.toLocaleString()} transaction(s) missing their mirror leg.</Alert>

              <div className="flex items-center gap-3">
                <label className="text-sm font-medium">Filter by target account:</label>
                <select value={filterAcc} onChange={e => { setFilterAcc(e.target.value); setChecked(new Set()) }}
                  className="border border-slate-300 rounded-md px-2 py-1.5 text-sm">
                  <option value="">All accounts</option>
                  {tgtAccounts.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
                </select>
              </div>

              {msg && <Alert type={msg.type}>{msg.text}</Alert>}

              {confirm === 'selected' && (
                <ConfirmBanner
                  message={`Create ${selectedIds.length} mirror transaction(s) for selected rows? Cannot be undone.`}
                  onYes={() => fixMut.mutate(selectedIds)} onNo={() => setConfirm(null)}
                  yesLabel="✅ Yes, create" isPending={fixMut.isPending} />
              )}
              {confirm === 'all' && (
                <ConfirmBanner
                  message={`Create ${rows.length} mirror transaction(s) for all listed rows? Cannot be undone.`}
                  onYes={() => fixMut.mutate(rows.map(r => r.transactions_id as number))}
                  onNo={() => setConfirm(null)} yesLabel="✅ Yes, create all" isPending={fixMut.isPending} />
              )}

              <div className="flex gap-2">
                <Button size="sm" variant={checked.size > 0 ? 'primary' : 'secondary'}
                  disabled={checked.size === 0} onClick={() => setConfirm('selected')}>
                  🔄 Fix {checked.size > 0 ? `${checked.size} selected` : 'selected'}
                </Button>
                {filterAcc && (
                  <Button size="sm" onClick={() => setConfirm('all')}>
                    🔄 Fix all {rows.length} for this account
                  </Button>
                )}
              </div>

              <div className="overflow-auto border border-slate-200 rounded-lg max-h-96">
                <table className="w-full text-xs">
                  <thead className="bg-slate-50 sticky top-0">
                    <tr>
                      <th className="px-2 py-2"><input type="checkbox"
                        checked={rows.length > 0 && checked.size === rows.length}
                        onChange={e => setChecked(e.target.checked ? new Set(rows.map((_, i) => i)) : new Set())} /></th>
                      {COLS.map(c => <ColHeader key={c} label={c} sortKey={c} currentKey={fmtSK} currentDir={fmtSD} onSort={fmtSort} className="px-3 py-2 text-left text-slate-600" />)}
                    </tr>
                  </thead>
                  <tbody>
                    {fmtSorted.map((row) => { const i = rows.indexOf(row); return (
                      <tr key={i} className={cn(checked.has(i) ? 'bg-blue-50' : i % 2 === 0 ? 'bg-white' : 'bg-slate-50')}>
                        <td className="px-2 py-1.5"><input type="checkbox" checked={checked.has(i)} onChange={() => {
                          setChecked(prev => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s })
                        }} /></td>
                        {COLS.map(c => <td key={c} className="px-3 py-1.5 text-slate-700 whitespace-nowrap">{String(row[c] ?? '')}</td>)}
                      </tr>
                    )})}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </CardBody>
      </Card>

      <UnlinkedTransferPairs />
    </div>
  )
}

function UnlinkedTransferPairs() {
  const [filterAcc, setFilterAcc] = useState<string>('')
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [confirm, setConfirm] = useState<'selected' | 'all' | null>(null)
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const { data: allRows = [], isLoading, refetch } = useQuery({
    queryKey: ['unlinked-transfer-pairs'], queryFn: getUnlinkedTransferPairs, staleTime: 30_000,
  })

  const tgtAccounts = useMemo(() => {
    const m = new Map<string, string>()
    ;(allRows as Row[]).forEach(r => m.set(r.tgt_acc_id as string, r.target_account as string))
    return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1]))
  }, [allRows])

  // Deduplicate: show each pair only once (lower src_tx_id wins)
  const deduped: Row[] = useMemo(() => {
    const seen = new Set<string>()
    return (allRows as Row[]).filter(r => {
      const a = r.src_tx_id as number, b = r.candidate_tx_id as number
      const key = `${Math.min(a, b)}-${Math.max(a, b)}`
      if (seen.has(key)) return false
      seen.add(key); return true
    })
  }, [allRows])

  const rows: Row[] = useMemo(
    () => filterAcc ? deduped.filter(r => String(r.tgt_acc_id) === filterAcc) : deduped,
    [deduped, filterAcc],
  )

  const { sorted: utpSorted, sortKey: utpSK, sortDir: utpSD, toggleSort: utpSort } = useSortTable(rows, 'date', 'asc')

  const UTP_COLS = ['src_tx_id', 'date', 'source_account', 'description', 'source_amount', 'target_account', 'candidate_tx_id', 'candidate_amount', 'candidate_desc', 'transfers_id']

  const linkMut = useMutation({
    mutationFn: (pairs: Row[]) => linkTransferPairs(pairs.map(r => ({
      src_tx_id: r.src_tx_id as number, candidate_tx_id: r.candidate_tx_id as number,
      transfers_id: r.transfers_id as number, src_acc_id: r.src_acc_id as number, tgt_acc_id: r.tgt_acc_id as number,
    }))),
    onSuccess: (d: { linked: number; errors: string[] }) => {
      setMsg({ type: 'success', text: `Linked ${d.linked} pair(s). ${d.errors.join(' ')}` })
      setChecked(new Set()); setConfirm(null); refetch()
    },
    onError: () => { setMsg({ type: 'error', text: 'Link failed.' }); setConfirm(null) },
  })

  if (isLoading) return <Spinner />

  const selectedRows = rows.filter((_, i) => checked.has(i))

  return (
    <Card>
      <CardHeader><CardTitle>🔗 Unlinked Transfer Pairs</CardTitle></CardHeader>
      <CardBody className="space-y-4">
        <p className="text-xs text-slate-500">
          Both legs exist but are not linked — the target transaction lacks a Transfers_Id back-link. Matching is done by date + absolute amount.
        </p>

        {allRows.length === 0 ? (
          <Alert type="success">✅ No unlinked transfer pairs found.</Alert>
        ) : (
          <>
            <Alert type="warning">⚠️ {rows.length.toLocaleString()} unlinked pair(s) found.</Alert>

            <div className="flex items-center gap-3">
              <label className="text-sm font-medium">Filter by target account:</label>
              <select value={filterAcc} onChange={e => { setFilterAcc(e.target.value); setChecked(new Set()) }}
                className="border border-slate-300 rounded-md px-2 py-1.5 text-sm">
                <option value="">All accounts</option>
                {tgtAccounts.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
              </select>
            </div>

            {msg && <Alert type={msg.type}>{msg.text}</Alert>}

            {confirm === 'selected' && (
              <ConfirmBanner
                message={`Link ${selectedRows.length} selected pair(s)? This will UPDATE existing transactions. Cannot be undone.`}
                onYes={() => linkMut.mutate(selectedRows)} onNo={() => setConfirm(null)}
                yesLabel="✅ Yes, link" isPending={linkMut.isPending} />
            )}
            {confirm === 'all' && (
              <ConfirmBanner
                message={`Link all ${rows.length} pair(s) for this account? Cannot be undone.`}
                onYes={() => linkMut.mutate(rows)} onNo={() => setConfirm(null)}
                yesLabel="✅ Yes, link all" isPending={linkMut.isPending} />
            )}

            <div className="flex gap-2">
              <Button size="sm" disabled={checked.size === 0} onClick={() => setConfirm('selected')}>
                🔗 Link {checked.size > 0 ? `${checked.size} selected` : 'selected'}
              </Button>
              {filterAcc && (
                <Button size="sm" onClick={() => setConfirm('all')}>
                  🔗 Link all {rows.length} for this account
                </Button>
              )}
            </div>

            <div className="overflow-auto border border-slate-200 rounded-lg max-h-72">
              <table className="w-full text-xs">
                <thead className="bg-slate-50 sticky top-0">
                  <tr>
                    <th className="px-2 py-2"><input type="checkbox"
                      checked={rows.length > 0 && checked.size === rows.length}
                      onChange={e => setChecked(e.target.checked ? new Set(rows.map((_, i) => i)) : new Set())} /></th>
                    {UTP_COLS.map(c => <ColHeader key={c} label={c} sortKey={c} currentKey={utpSK} currentDir={utpSD} onSort={utpSort} className="px-3 py-2 text-left text-slate-600" />)}
                  </tr>
                </thead>
                <tbody>
                  {utpSorted.map((row) => { const i = rows.indexOf(row); return (
                    <tr key={i} className={cn(checked.has(i) ? 'bg-blue-50' : i % 2 === 0 ? 'bg-white' : 'bg-slate-50')}>
                      <td className="px-2 py-1.5"><input type="checkbox" checked={checked.has(i)} onChange={() => {
                        setChecked(prev => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s })
                      }} /></td>
                      {UTP_COLS.map(c => <td key={c} className="px-3 py-1.5 text-slate-700 whitespace-nowrap">{String(row[c] ?? '')}</td>)}
                    </tr>
                  )})}
                </tbody>
              </table>
            </div>
          </>
        )}
      </CardBody>
    </Card>
  )
}

// ── Fix Transfer Sign Mismatches ───────────────────────────────────────────────
function FixTransferSignMismatches() {
  const [filterAcc, setFilterAcc] = useState<string>('')
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [flipChoice, setFlipChoice] = useState<'TX1' | 'TX2'>('TX1')
  const [confirm, setConfirm] = useState(false)
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const { data: allRows = [], isLoading, refetch } = useQuery({
    queryKey: ['transfer-sign-mismatches'], queryFn: getTransferSignMismatches, staleTime: 30_000,
  })

  const accounts = useMemo(() => {
    const m = new Map<string, string>()
    ;(allRows as Row[]).forEach(r => {
      m.set(r.acc1_id as string, r.account1 as string)
      m.set(r.acc2_id as string, r.account2 as string)
    })
    return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1]))
  }, [allRows])

  const rows: Row[] = useMemo(
    () => filterAcc ? (allRows as Row[]).filter(r => String(r.acc1_id) === filterAcc || String(r.acc2_id) === filterAcc) : allRows as Row[],
    [allRows, filterAcc],
  )

  const { sorted: ftsSorted, sortKey: ftsSK, sortDir: ftsSD, toggleSort: ftsSort } = useSortTable(rows, 'date', 'asc')

  const FTS_COLS = ['mismatch_type', 'date', 'transfers_id', 'tx1_id', 'account1', 'amount1', 'tx2_id', 'account2', 'amount2', 'payee', 'description']

  const selectedRows = rows.filter((_, i) => checked.has(i))

  const fixMut = useMutation({
    mutationFn: () => {
      const txIds = selectedRows.map(r => flipChoice === 'TX1' ? r.tx1_id as number : r.tx2_id as number)
      const allAccIds = [...new Set(selectedRows.flatMap(r => [r.acc1_id as number, r.acc2_id as number]))]
      return fixTransferSign(txIds, allAccIds)
    },
    onSuccess: (d: { flipped: number; errors: string[] }) => {
      setMsg({ type: 'success', text: `Flipped sign for ${d.flipped} transaction(s). ${d.errors.join(' ')}` })
      setChecked(new Set()); setConfirm(false); refetch()
    },
    onError: () => { setMsg({ type: 'error', text: 'Fix failed.' }); setConfirm(false) },
  })

  if (isLoading) return <Spinner />

  return (
    <Card>
      <CardHeader><CardTitle>🔀 Fix Transfer Sign Mismatches</CardTitle></CardHeader>
      <CardBody className="space-y-4">
        <p className="text-xs text-slate-500">
          Finds linked transfer pairs where both legs have the same sign. For a correct transfer one leg must be positive and the other negative.
        </p>

        {allRows.length === 0 ? (
          <Alert type="success">✅ No transfer sign mismatches found.</Alert>
        ) : (
          <>
            <Alert type="warning">⚠️ {rows.length.toLocaleString()} transfer pair(s) have mismatched signs.</Alert>

            <div className="flex items-center gap-3">
              <label className="text-sm font-medium">Filter by account:</label>
              <select value={filterAcc} onChange={e => { setFilterAcc(e.target.value); setChecked(new Set()) }}
                className="border border-slate-300 rounded-md px-2 py-1.5 text-sm">
                <option value="">All accounts</option>
                {accounts.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
              </select>
            </div>

            {msg && <Alert type={msg.type}>{msg.text}</Alert>}

            <div className="overflow-auto border border-slate-200 rounded-lg max-h-72">
              <table className="w-full text-xs">
                <thead className="bg-slate-50 sticky top-0">
                  <tr>
                    <th className="px-2 py-2"><input type="checkbox"
                      checked={rows.length > 0 && checked.size === rows.length}
                      onChange={e => setChecked(e.target.checked ? new Set(rows.map((_, i) => i)) : new Set())} /></th>
                    {FTS_COLS.map(c => <ColHeader key={c} label={c} sortKey={c} currentKey={ftsSK} currentDir={ftsSD} onSort={ftsSort} className="px-3 py-2 text-left text-slate-600" />)}
                  </tr>
                </thead>
                <tbody>
                  {ftsSorted.map((row) => { const i = rows.indexOf(row); return (
                    <tr key={i} className={cn(checked.has(i) ? 'bg-blue-50' : i % 2 === 0 ? 'bg-white' : 'bg-slate-50')}>
                      <td className="px-2 py-1.5"><input type="checkbox" checked={checked.has(i)} onChange={() => {
                        setChecked(prev => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s })
                      }} /></td>
                      {FTS_COLS.map(c => <td key={c} className="px-3 py-1.5 text-slate-700 whitespace-nowrap">{String(row[c] ?? '')}</td>)}
                    </tr>
                  )})}
                </tbody>
              </table>
            </div>

            {selectedRows.length > 0 && (
              <>
                <div className="flex items-center gap-4">
                  <span className="text-sm font-medium">{selectedRows.length} pair(s) selected. Flip sign of:</span>
                  {(['TX1', 'TX2'] as const).map(opt => (
                    <label key={opt} className="flex items-center gap-2 text-sm cursor-pointer">
                      <input type="radio" checked={flipChoice === opt} onChange={() => setFlipChoice(opt)} />
                      {opt === 'TX1' ? 'TX1 (Account 1)' : 'TX2 (Account 2)'}
                    </label>
                  ))}
                </div>
                <Button size="sm" onClick={() => setConfirm(true)}>
                  🔀 Flip Sign for {selectedRows.length} Selected ({flipChoice})
                </Button>
                {confirm && (
                  <ConfirmBanner
                    message={`This will negate Total_Amount, Total_Amount_Target, and all Splits amounts for ${selectedRows.length} transaction(s). Cannot be undone.`}
                    onYes={() => fixMut.mutate()} onNo={() => setConfirm(false)}
                    yesLabel="✅ Yes, flip" isPending={fixMut.isPending} />
                )}
              </>
            )}
          </>
        )}
      </CardBody>
    </Card>
  )
}

// ── Fix Missing Investment Cash Links ─────────────────────────────────────────
function FixMissingInvCashLinks() {
  const [filterAcc, setFilterAcc] = useState<string>('')
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [confirm, setConfirm] = useState<'selected' | 'all' | null>(null)
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const { data: allRows = [], isLoading, refetch } = useQuery({
    queryKey: ['missing-inv-cash-links'], queryFn: getMissingInvCashLinks, staleTime: 30_000,
  })

  const invAccounts = useMemo(() => {
    const m = new Map<string, string>()
    ;(allRows as Row[]).forEach(r => m.set(r.inv_acc_id as string, `${r.investment_account} → ${r.cash_account}`))
    return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1]))
  }, [allRows])

  const rows: Row[] = useMemo(
    () => filterAcc ? (allRows as Row[]).filter(r => String(r.inv_acc_id) === filterAcc) : allRows as Row[],
    [allRows, filterAcc],
  )

  const { sorted: fmlSorted, sortKey: fmlSK, sortDir: fmlSD, toggleSort: fmlSort } = useSortTable(rows, 'date', 'asc')

  const FML_COLS = ['investments_id', 'investment_account', 'date', 'action', 'security', 'inv_amount', 'candidate_tx_id', 'cash_account', 'candidate_amount', 'candidate_payee', 'candidate_description']

  const linkMut = useMutation({
    mutationFn: (pairs: { investments_id: number; candidate_tx_id: number }[]) => fixInvCashLinks(pairs),
    onSuccess: (d: { linked: number; errors: string[] }) => {
      setMsg({ type: 'success', text: `Linked ${d.linked} investment entr(y/ies). ${d.errors.join(' ')}` })
      setChecked(new Set()); setConfirm(null); refetch()
    },
    onError: () => { setMsg({ type: 'error', text: 'Link failed.' }); setConfirm(null) },
  })

  const selectedRows = rows.filter((_, i) => checked.has(i))

  function buildPairs(target: Row[]) {
    return target.map(r => ({ investments_id: r.investments_id as number, candidate_tx_id: r.candidate_tx_id as number }))
  }

  if (isLoading) return <Spinner />

  return (
    <Card>
      <CardHeader><CardTitle>🔗 Fix Missing Investment Cash Links</CardTitle></CardHeader>
      <CardBody className="space-y-4">
        <p className="text-xs text-slate-500">
          Finds unlinked investment entries where a matching cash transaction exists on the linked cash account. Updates Investments.Transactions_Id — no new rows created.
        </p>

        {allRows.length === 0 ? (
          <Alert type="success">✅ No linkable pairs found.</Alert>
        ) : (
          <>
            <Alert type="warning">⚠️ {rows.length.toLocaleString()} potential link(s) found.</Alert>

            <div className="flex items-center gap-3">
              <label className="text-sm font-medium">Filter by investment account:</label>
              <select value={filterAcc} onChange={e => { setFilterAcc(e.target.value); setChecked(new Set()) }}
                className="border border-slate-300 rounded-md px-2 py-1.5 text-sm">
                <option value="">All accounts</option>
                {invAccounts.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
              </select>
            </div>

            {msg && <Alert type={msg.type}>{msg.text}</Alert>}

            {confirm === 'selected' && (
              <ConfirmBanner
                message={`Link ${selectedRows.length} investment entr(y/ies) to existing cash transactions? Cannot be undone.`}
                onYes={() => linkMut.mutate(buildPairs(selectedRows))} onNo={() => setConfirm(null)}
                yesLabel="✅ Yes, link" isPending={linkMut.isPending} />
            )}
            {confirm === 'all' && (
              <ConfirmBanner
                message={`Link all ${rows.length} pair(s) for this account? Cannot be undone.`}
                onYes={() => linkMut.mutate(buildPairs(rows))} onNo={() => setConfirm(null)}
                yesLabel="✅ Yes, link all" isPending={linkMut.isPending} />
            )}

            <div className="flex gap-2">
              <Button size="sm" disabled={checked.size === 0} onClick={() => setConfirm('selected')}>
                🔗 Link {checked.size > 0 ? `${checked.size} selected` : 'selected'}
              </Button>
              {filterAcc && (
                <Button size="sm" onClick={() => setConfirm('all')}>
                  🔗 Link all {rows.length} for this account
                </Button>
              )}
            </div>

            <div className="overflow-auto border border-slate-200 rounded-lg max-h-72">
              <table className="w-full text-xs">
                <thead className="bg-slate-50 sticky top-0">
                  <tr>
                    <th className="px-2 py-2"><input type="checkbox"
                      checked={rows.length > 0 && checked.size === rows.length}
                      onChange={e => setChecked(e.target.checked ? new Set(rows.map((_, i) => i)) : new Set())} /></th>
                    {FML_COLS.map(c => <ColHeader key={c} label={c} sortKey={c} currentKey={fmlSK} currentDir={fmlSD} onSort={fmlSort} className="px-3 py-2 text-left text-slate-600" />)}
                  </tr>
                </thead>
                <tbody>
                  {fmlSorted.map((row) => { const i = rows.indexOf(row); return (
                    <tr key={i} className={cn(checked.has(i) ? 'bg-blue-50' : i % 2 === 0 ? 'bg-white' : 'bg-slate-50')}>
                      <td className="px-2 py-1.5"><input type="checkbox" checked={checked.has(i)} onChange={() => {
                        setChecked(prev => { const s = new Set(prev); s.has(i) ? s.delete(i) : s.add(i); return s })
                      }} /></td>
                      {FML_COLS.map(c => <td key={c} className="px-3 py-1.5 text-slate-700 whitespace-nowrap">{String(row[c] ?? '')}</td>)}
                    </tr>
                  )})}
                </tbody>
              </table>
            </div>
          </>
        )}
      </CardBody>
    </Card>
  )
}

// ── Log Viewer ─────────────────────────────────────────────────────────────────
function LogViewer() {
  const [lines, setLines] = useState(500)
  const [levelFilter, setLevelFilter] = useState<string[]>(['ERROR', 'WARNING'])
  const [search, setSearch] = useState('')

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['logs', lines, levelFilter.join(','), search],
    queryFn: () => getLogs(lines, levelFilter.length ? levelFilter.join(',') : undefined, search || undefined),
    staleTime: 10_000,
  })

  const LEVELS = ['ERROR', 'WARNING', 'INFO', 'DEBUG']

  function downloadLog() {
    if (!data?.text) return
    const blob = new Blob([data.text], { type: 'text/plain' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'app.log'
    a.click()
  }

  return (
    <Card>
      <CardHeader><CardTitle>📋 Log Viewer</CardTitle></CardHeader>
      <CardBody className="space-y-4">
        <p className="text-xs text-slate-500">Shows application logs from log files in the app directory.</p>

        <div className="flex flex-wrap gap-4 items-center">
          <div className="flex items-center gap-2">
            <label className="text-sm font-medium">Last N lines:</label>
            <input type="number" min={50} max={50000} step={100} value={lines}
              onChange={e => setLines(Number(e.target.value))}
              className="w-24 border border-slate-300 rounded-md px-2 py-1.5 text-sm" />
          </div>
          <div className="flex items-center gap-2">
            <label className="text-sm font-medium">Level:</label>
            <div className="flex gap-1">
              {LEVELS.map(l => (
                <button key={l}
                  className={cn('px-2 py-0.5 rounded text-xs border transition-colors',
                    levelFilter.includes(l) ? 'bg-blue-100 border-blue-400 text-blue-800' : 'bg-white border-slate-300 text-slate-600 hover:bg-slate-50')}
                  onClick={() => setLevelFilter(prev => prev.includes(l) ? prev.filter(x => x !== l) : [...prev, l])}>
                  {l}
                </button>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-sm font-medium">Search:</label>
            <input type="text" value={search} onChange={e => setSearch(e.target.value)}
              placeholder="keyword…"
              className="border border-slate-300 rounded-md px-2 py-1.5 text-sm w-40" />
          </div>
          <Button size="sm" variant="secondary" onClick={() => refetch()}>Refresh</Button>
        </div>

        {isLoading ? <Spinner /> : (
          <>
            {data?.source && data.source !== 'none' && (
              <p className="text-xs text-slate-500">Source: {data.source} · {data.lines} line(s)</p>
            )}
            {data?.source === 'none' && (
              <Alert type="warning">No log file found. Make sure app.log or scheduler.log exists in the app directory.</Alert>
            )}
            <pre className="bg-slate-900 text-green-400 text-xs rounded-lg p-4 overflow-auto max-h-96 whitespace-pre-wrap">
              {data?.text || '(no lines match the current filters)'}
            </pre>
            {data?.text && (
              <Button size="sm" variant="secondary" onClick={downloadLog}>⬇️ Download log</Button>
            )}
          </>
        )}
      </CardBody>
    </Card>
  )
}

// ── Scheduled Tasks ───────────────────────────────────────────────────────────
interface JobForm { name: string; description: string; schedule: string; enabled: boolean }

function ScheduledTasks() {
  const qc = useQueryClient()
  const [editing, setEditing] = useState<string | null>(null)
  const [form, setForm] = useState<JobForm>({ name: '', description: '', schedule: '', enabled: true })
  const [triggering, setTriggering] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const { data: jobs = [], isLoading, refetch } = useQuery({
    queryKey: ['scheduler-jobs'], queryFn: getSchedulerJobs, staleTime: 15_000,
  })
  const rows = jobs as Row[]

  function openEdit(r: Row) {
    setForm({
      name: r.name as string,
      description: String(r.description ?? ''),
      schedule: String(r.schedule ?? ''),
      enabled: Boolean(r.enabled),
    })
    setEditing(r.job_id as string)
    setMsg(null)
  }

  const saveMut = useMutation({
    mutationFn: () => updateSchedulerJob(editing!, form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scheduler-jobs'] })
      setMsg({ type: 'success', text: 'Job saved.' })
      setEditing(null)
    },
    onError: (e: Error) => setMsg({ type: 'error', text: e.message }),
  })

  async function triggerJob(jobId: string) {
    setTriggering(jobId); setMsg(null)
    try {
      const r = await triggerSchedulerJob(jobId)
      setMsg({ type: 'success', text: (r as { message?: string }).message ?? `'${jobId}' started in background.` })
    } catch (e: unknown) {
      setMsg({ type: 'error', text: e instanceof Error ? e.message : 'Failed to trigger.' })
    } finally {
      setTriggering(null)
    }
  }

  const f = (k: keyof JobForm, v: unknown) => setForm(p => ({ ...p, [k]: v }))
  const inp = 'w-full border border-slate-300 rounded px-2 py-1.5 text-sm focus:outline-none focus:border-blue-400'

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>📅 Scheduled Tasks</CardTitle>
          <Button size="sm" variant="secondary" onClick={() => refetch()}>↻ Refresh</Button>
        </div>
      </CardHeader>
      <CardBody className="space-y-4">
        <p className="text-xs text-slate-500">
          Background jobs executed by the scheduler container. New job types require a code addition to <code className="bg-slate-100 px-1 rounded">scheduler.py</code> — the list below reflects all available jobs. You can edit metadata and enable/disable each job, or trigger it on demand.
        </p>

        {msg && <Alert type={msg.type}>{msg.text}</Alert>}

        {/* ── Edit form ── */}
        {editing !== null && (
          <div className="border border-slate-200 rounded-lg p-4 space-y-3 bg-slate-50">
            <h3 className="text-sm font-semibold text-slate-700">Edit: {form.name}</h3>
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <label className="block text-xs text-slate-500 mb-1">Name</label>
                <input className={inp} value={form.name} onChange={e => f('name', e.target.value)} />
              </div>
              <div className="col-span-2">
                <label className="block text-xs text-slate-500 mb-1">Description</label>
                <input className={inp} value={form.description} onChange={e => f('description', e.target.value)} />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">Schedule (label)</label>
                <input className={inp} placeholder="e.g. Daily at 06:00" value={form.schedule} onChange={e => f('schedule', e.target.value)} />
              </div>
              <div className="flex items-end">
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={form.enabled} onChange={e => f('enabled', e.target.checked)} />
                  Enabled
                </label>
              </div>
            </div>
            <div className="flex gap-2">
              <Button size="sm" onClick={() => saveMut.mutate()} disabled={!form.name || saveMut.isPending}>
                {saveMut.isPending ? <Spinner size={12} /> : null} 💾 Save
              </Button>
              <Button size="sm" variant="secondary" onClick={() => setEditing(null)}>Cancel</Button>
            </div>
          </div>
        )}

        {/* ── Jobs table ── */}
        {isLoading ? <Spinner /> : (
          <div className="overflow-auto border border-slate-200 rounded-lg">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 sticky top-0">
                <tr>
                  {['Job', 'Description', 'Schedule', 'Last Run', 'Status', 'On', ''].map(h => (
                    <th key={h} className="px-3 py-2 text-left font-medium text-slate-600 whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.length === 0 && (
                  <tr><td colSpan={7} className="px-3 py-6 text-center text-slate-400">No jobs found.</td></tr>
                )}
                {rows.map((r, i) => {
                  const jid = r.job_id as string
                  const status = r.last_status as string | null
                  return (
                    <tr key={jid} className={cn(i % 2 === 0 ? 'bg-white' : 'bg-slate-50', !r.enabled ? 'opacity-50' : '')}>
                      <td className="px-3 py-2 whitespace-nowrap">
                        <div className="font-medium text-slate-800">{r.name as string}</div>
                        <div className="text-xs text-slate-400 font-mono">{jid}</div>
                      </td>
                      <td className="px-3 py-2 text-xs text-slate-600 max-w-xs">{String(r.description ?? '')}</td>
                      <td className="px-3 py-2 text-xs font-mono text-slate-700 whitespace-nowrap">{String(r.schedule ?? '—')}</td>
                      <td className="px-3 py-2 text-xs text-slate-500 whitespace-nowrap">
                        {r.last_run ? String(r.last_run).slice(0, 16).replace('T', ' ') : '—'}
                      </td>
                      <td className="px-3 py-2 text-xs whitespace-nowrap">
                        {status === 'success' && <span className="text-green-600 font-medium">✓ OK</span>}
                        {status === 'error' && <span className="text-red-600 font-medium" title={String(r.last_message ?? '')}>✗ Error</span>}
                        {!status && <span className="text-slate-400">—</span>}
                      </td>
                      <td className="px-3 py-2 text-center">
                        {r.enabled ? <span className="text-green-500">●</span> : <span className="text-slate-300">●</span>}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        <div className="flex gap-2 items-center">
                          <Button size="sm" variant="secondary" disabled={triggering === jid}
                            onClick={() => triggerJob(jid)}>
                            {triggering === jid ? <Spinner size={10} /> : '▶'}
                          </Button>
                          <button className="text-blue-500 hover:text-blue-700 text-xs underline"
                            onClick={() => openEdit(r)}>Edit</button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardBody>
    </Card>
  )
}

// ── Main Page ──────────────────────────────────────────────────────────────────

const CATEGORIES: Record<string, string[]> = {
  '💾 Database': [
    '💾 Backup & Restore',
    '🔧 DB Maintenance',
    '🛢 SQL Interface',
    '📤 Data Export',
    '🔄 Fix Missing Transfer Mirrors',
    '🔀 Fix Transfer Sign Mismatches',
    '🔗 Fix Missing Investment Cash Links',
  ],
  '⚙️ System': [
    '📅 Scheduled Tasks',
  ],
  '📊 Market Data & Prices': [
    '📥 Fill Missing Prices',
    '🔍 Price Quality',
    '⚖ Normalize Investments',
    '🩺 Investment Data Quality',
  ],
  '📋 Logs': ['📋 Log Viewer'],
}

const TOOL_COMPONENTS: Record<string, React.ComponentType> = {
  '💾 Backup & Restore': BackupRestore,
  '🔧 DB Maintenance': DbMaintenance,
  '🛢 SQL Interface': SqlInterface,
  '📤 Data Export': DataExport,
  '📅 Scheduled Tasks': ScheduledTasks,
  '🔄 Fix Missing Transfer Mirrors': FixMissingTransferMirrors,
  '🔀 Fix Transfer Sign Mismatches': FixTransferSignMismatches,
  '🔗 Fix Missing Investment Cash Links': FixMissingInvCashLinks,
  '📥 Fill Missing Prices': FillMissingPrices,
  '🔍 Price Quality': PriceQuality,
  '⚖ Normalize Investments': NormalizeInvestments,
  '🩺 Investment Data Quality': InvestmentDataQuality,
  '📋 Log Viewer': LogViewer,
}

export default function Tools() {
  const [category, setCategory] = useState('💾 Database')
  const [tool, setTool] = useState<Record<string, string>>({
    '💾 Database': '💾 Backup & Restore',
    '📊 Market Data & Prices': '📥 Fill Missing Prices',
    '📋 Logs': '📋 Log Viewer',
  })

  const toolsInCategory = CATEGORIES[category]
  const currentTool = tool[category]
  const ToolComponent = TOOL_COMPONENTS[currentTool]

  return (
    <div>
      <PageHeader title="Tools" subtitle="Database maintenance and admin utilities" />

      <div className="px-6 py-6 space-y-6">
        {/* Category selector */}
        <div className="flex gap-2 flex-wrap">
          {Object.keys(CATEGORIES).map(cat => (
            <button key={cat}
              className={cn(
                'px-4 py-1.5 rounded-full text-sm font-medium border transition-colors',
                category === cat
                  ? 'bg-blue-600 border-blue-600 text-white'
                  : 'bg-white border-slate-300 text-slate-600 hover:bg-slate-50',
              )}
              onClick={() => setCategory(cat)}>
              {cat}
            </button>
          ))}
        </div>

        <hr className="border-slate-200" />

        {/* Tool selector — selectbox for large categories, tabs for small */}
        {toolsInCategory.length > 3 ? (
          <div>
            <select
              value={currentTool}
              onChange={e => setTool(prev => ({ ...prev, [category]: e.target.value }))}
              className="border border-slate-300 rounded-md px-3 py-2 text-sm w-full max-w-sm"
            >
              {toolsInCategory.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
        ) : (
          <div className="flex gap-1 border-b border-slate-200">
            {toolsInCategory.map(t => (
              <button key={t}
                className={cn(
                  'px-4 py-2 text-sm font-medium border-b-2 transition-colors',
                  currentTool === t
                    ? 'border-blue-600 text-blue-700'
                    : 'border-transparent text-slate-600 hover:text-slate-800',
                )}
                onClick={() => setTool(prev => ({ ...prev, [category]: t }))}>
                {t}
              </button>
            ))}
          </div>
        )}

        <hr className="border-slate-200" />

        {/* Render selected tool */}
        {ToolComponent && <ToolComponent />}
      </div>
    </div>
  )
}
