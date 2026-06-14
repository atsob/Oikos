import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { runVacuum, runBackup, runSql, getSchedulerStatus } from '@/lib/api'
import { PageHeader, Card, CardHeader, CardTitle, CardBody, Button, Spinner } from '@/components/ui'
import { Database, HardDrive, Terminal, Clock } from 'lucide-react'

function SqlShell() {
  const [sql, setSql] = useState('SELECT NOW()')
  const [result, setResult] = useState<unknown>(null)

  const runMut = useMutation({
    mutationFn: runSql,
    onSuccess: data => setResult(data),
    onError: (err: { response?: { data?: unknown }; message?: string }) => setResult({ error: err.response?.data ?? err.message }),
  })

  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><Terminal size={15} /> SQL Shell</CardTitle></CardHeader>
      <CardBody className="space-y-3">
        <textarea
          className="w-full h-28 font-mono text-sm border border-slate-300 rounded-md p-3 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          value={sql}
          onChange={e => setSql(e.target.value)}
          spellCheck={false}
        />
        <Button size="sm" onClick={() => runMut.mutate(sql)} disabled={runMut.isPending}>
          {runMut.isPending ? <Spinner size={14} /> : null} Run
        </Button>
        {result != null && (
          <pre className="bg-slate-900 text-green-400 text-xs rounded-lg p-4 overflow-auto max-h-60 whitespace-pre-wrap">
            {JSON.stringify(result, null, 2)}
          </pre>
        )}
      </CardBody>
    </Card>
  )
}

function SchedulerStatus() {
  const { data, isLoading } = useQuery({
    queryKey: ['scheduler-status'],
    queryFn: getSchedulerStatus,
    refetchInterval: 30_000,
  })
  return (
    <Card>
      <CardHeader><CardTitle className="flex items-center gap-2"><Clock size={15} /> Scheduler</CardTitle></CardHeader>
      <CardBody>
        {isLoading ? <Spinner /> : (
          <pre className="text-xs bg-slate-50 rounded p-3 overflow-auto max-h-48">
            {JSON.stringify(data, null, 2)}
          </pre>
        )}
      </CardBody>
    </Card>
  )
}

export default function Tools() {
  const vacuumMut = useMutation({ mutationFn: runVacuum })
  const backupMut = useMutation({ mutationFn: runBackup })

  return (
    <div>
      <PageHeader title="Tools" subtitle="Database maintenance and admin utilities" />

      <div className="px-6 py-6 grid grid-cols-1 md:grid-cols-2 gap-6 max-w-4xl">
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><Database size={15} /> Database Maintenance</CardTitle></CardHeader>
          <CardBody className="space-y-3">
            <div className="flex gap-3">
              <Button
                variant="secondary"
                className="flex-1"
                onClick={() => vacuumMut.mutate()}
                disabled={vacuumMut.isPending}
              >
                {vacuumMut.isPending ? <Spinner size={14} /> : null}
                VACUUM ANALYZE
              </Button>
              <Button
                variant="secondary"
                className="flex-1"
                onClick={() => backupMut.mutate()}
                disabled={backupMut.isPending}
              >
                {backupMut.isPending ? <><Spinner size={14} /> Backing up…</> : <><HardDrive size={14} /> Backup DB</>}
              </Button>
            </div>
            {vacuumMut.isSuccess && <p className="text-xs text-green-600">VACUUM completed</p>}
            {backupMut.isSuccess && <p className="text-xs text-green-600">Backup created</p>}
            {(vacuumMut.isError || backupMut.isError) && <p className="text-xs text-red-600">Operation failed</p>}
          </CardBody>
        </Card>

        <SchedulerStatus />

        <div className="md:col-span-2">
          <SqlShell />
        </div>
      </div>
    </div>
  )
}
