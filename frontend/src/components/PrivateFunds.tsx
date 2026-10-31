import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Plot from '@/components/Plot'
import { Button, Card, CardBody, CardHeader, CardTitle, Input, Select, Spinner, Tooltip } from '@/components/ui'
import { useTheme } from '@/lib/theme'
import { fmtEur, fmtNum, plotLayout } from '@/lib/utils'
import {
  addPrivateFundEvent, deletePrivateFund, deletePrivateFundEvent, getPrivateFund, getPrivateFunds, savePrivateFund, updatePrivateFundEvent,
  type PrivateFundDetail, type PrivateFundIn,
} from '@/lib/api'

// Private funds: venture, private equity and similar funds you commit to and are called on for money. Server side: database/private_funds.py.
const FUND_TYPES = ['Venture', 'Private Equity', 'Real Estate', 'Infrastructure', 'Private Credit', 'Other']
const EVENT_LABEL: Record<string, string> = { call: 'Capital call', distribution: 'Distribution', nav: 'NAV (valuation)' }
const today = () => new Date().toISOString().slice(0, 10)

const x = (v: number | null | undefined, d = 2) => v == null ? '—' : `${fmtNum(v, d)}×`
const pct = (v: number | null | undefined) => v == null ? '—' : `${v >= 0 ? '' : '−'}${fmtNum(Math.abs(v), 1)}%`
const money = (v: number, cur: string) => cur === 'EUR' ? fmtEur(v) : `${fmtNum(v, 2)} ${cur}`

function Kpi({ label, value, sub, tip, color }: { label: string; value: string; sub?: string; tip?: string; color?: string }) {
  return (
    <div className="bg-slate-50 rounded-lg p-3">
      <p className="text-xs text-slate-500 mb-1">{tip ? <Tooltip text={tip}>{label}</Tooltip> : label}</p>
      <p className={`text-sm font-bold tabular-nums ${color ?? ''}`}>{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-0.5">{sub}</p>}
    </div>
  )
}

const EMPTY: PrivateFundIn = { name: '', manager: '', fund_type: 'Private Equity', vintage_year: new Date().getFullYear(), currency: 'EUR', commitment: 0, status: 'active', notes: '' }

export default function PrivateFunds() {
  const { isDark } = useTheme()
  const qc = useQueryClient()
  const list = useQuery({ queryKey: ['private-funds'], queryFn: getPrivateFunds })
  const [selected, setSelected] = useState<number | null>(null)
  const [form, setForm] = useState<{ id: number | null; v: PrivateFundIn } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const detail = useQuery({ queryKey: ['private-fund', selected], queryFn: () => getPrivateFund(selected!), enabled: selected != null })
  const refresh = () => { qc.invalidateQueries({ queryKey: ['private-funds'] }); qc.invalidateQueries({ queryKey: ['private-fund'] }); qc.invalidateQueries({ queryKey: ['alerts'] }) }
  const onError = (e: unknown) => setErr((e as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Could not save.')

  const saveFund = useMutation({
    mutationFn: (f: { id: number | null; v: PrivateFundIn }) => savePrivateFund(f.id, f.v),
    onSuccess: (d: PrivateFundDetail) => { setForm(null); setErr(null); setSelected(d.id); refresh() }, onError,
  })
  const removeFund = useMutation({ mutationFn: (id: number) => deletePrivateFund(id), onSuccess: () => { setSelected(null); refresh() } })

  if (list.isLoading || !list.data) return <div className="flex justify-center py-12"><Spinner /></div>
  const { funds, summary, upcoming } = list.data
  const f = detail.data

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500 max-w-4xl">
        Funds you have committed to — venture, private equity, real estate, infrastructure, private credit. Record the <b>commitment</b>, each <b>capital call</b> (money you paid in), each <b>distribution</b> (money paid back)
        and the fund's own <b>NAV</b> from its statements; the totals, the multiples (DPI, RVPI, TVPI) and the IRR follow. A call dated in the future is an expected call: it is listed below and raises an alert ahead of its date.
        Totals are in EUR at the latest rate; active funds only. Private funds are not part of Net Worth.
      </p>

      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
        <Kpi label="Committed" value={fmtEur(summary.commitment)} />
        <Kpi label="Paid in" value={fmtEur(summary.paid_in)} sub={summary.commitment ? `${fmtNum(summary.paid_in / summary.commitment * 100, 0)}% of the commitment` : undefined} />
        <Kpi label="Unfunded" value={fmtEur(summary.unfunded)} tip="What can still be called: commitment less what is paid in." />
        <Kpi label="NAV" value={fmtEur(summary.nav)} tip="The funds' latest valuations of what you still hold." />
        <Kpi label="Distributions" value={fmtEur(summary.distributions)} />
        <Kpi label="TVPI" value={x(summary.tvpi)} tip="(Distributions + NAV) ÷ paid-in: how many times your money it is worth now." color={summary.tvpi != null && summary.tvpi >= 1 ? 'text-green-700' : 'text-red-600'} />
        <Kpi label="IRR" value={pct(summary.irr_pct)} tip="The yearly rate that makes all the dated calls, distributions and the latest NAVs net to zero." color={summary.irr_pct != null && summary.irr_pct >= 0 ? 'text-green-700' : 'text-red-600'} />
      </div>

      {upcoming.length > 0 && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 text-amber-800 px-3 py-2 text-xs space-y-0.5">
          <p className="font-semibold">Expected capital calls</p>
          {upcoming.map(u => <p key={u.id}>{u.date} — <b>{u.fund}</b>: {money(u.amount, u.currency)} <span className="opacity-70">({u.days === 0 ? 'today' : `in ${u.days} day${u.days === 1 ? '' : 's'}`})</span>{u.note ? ` · ${u.note}` : ''}</p>)}
        </div>
      )}

      <div className="flex items-center gap-3">
        <p className="text-sm font-semibold text-slate-700">Funds</p>
        <Button size="sm" onClick={() => { setForm({ id: null, v: EMPTY }); setErr(null) }}>+ New fund</Button>
      </div>
      {funds.length === 0 ? <p className="text-sm text-slate-400">No funds yet.</p> : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="bg-slate-50 text-slate-500 uppercase tracking-wide">
              {['Fund', 'Type', 'Vintage', 'Committed', 'Paid in', 'Unfunded', 'Distributions', 'NAV', 'TVPI', 'DPI', 'IRR', 'Next call'].map((h, i) => <th key={h} className={`px-3 py-2 ${i < 3 ? 'text-left' : 'text-right'} whitespace-nowrap`}>{h}</th>)}
            </tr></thead>
            <tbody className="divide-y divide-slate-100">
              {funds.map(r => (
                <tr key={r.id} onClick={() => setSelected(r.id)} className={`cursor-pointer hover:bg-slate-50 ${selected === r.id ? 'bg-blue-50' : ''} ${r.status === 'closed' ? 'opacity-60' : ''}`}>
                  <td className="px-3 py-2 font-medium">{r.name}{r.status === 'closed' && <span className="ml-1 text-slate-400 font-normal">(closed)</span>}{r.manager && <span className="block text-slate-400 font-normal">{r.manager}</span>}</td>
                  <td className="px-3 py-2 text-slate-500">{r.fund_type}</td><td className="px-3 py-2 text-slate-500">{r.vintage_year ?? '—'}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{money(r.commitment, r.currency)}</td><td className="px-3 py-2 text-right tabular-nums">{money(r.paid_in, r.currency)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{money(r.unfunded, r.currency)}</td><td className="px-3 py-2 text-right tabular-nums">{money(r.distributions, r.currency)}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{money(r.nav, r.currency)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums font-semibold ${r.tvpi != null && r.tvpi < 1 ? 'text-red-600' : ''}`}>{x(r.tvpi)}</td><td className="px-3 py-2 text-right tabular-nums">{x(r.dpi)}</td>
                  <td className={`px-3 py-2 text-right tabular-nums ${r.irr_pct != null && r.irr_pct < 0 ? 'text-red-600' : ''}`}>{pct(r.irr_pct)}</td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">{r.next_call ? `${r.next_call.date} · ${money(r.next_call.amount, r.currency)}` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {err && <p className="text-xs text-red-600">{err}</p>}
      {form && (
        <Card>
          <CardHeader><CardTitle>{form.id ? 'Edit fund' : 'New fund'}</CardTitle></CardHeader>
          <CardBody className="space-y-3">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <label className="text-xs text-slate-500 col-span-2">Name<Input value={form.v.name} onChange={e => setForm({ ...form, v: { ...form.v, name: e.target.value } })} autoFocus /></label>
              <label className="text-xs text-slate-500">Manager<Input value={form.v.manager ?? ''} onChange={e => setForm({ ...form, v: { ...form.v, manager: e.target.value } })} /></label>
              <label className="text-xs text-slate-500">Type<Select value={form.v.fund_type ?? 'Other'} onChange={e => setForm({ ...form, v: { ...form.v, fund_type: e.target.value } })}>{FUND_TYPES.map(t => <option key={t}>{t}</option>)}</Select></label>
              <label className="text-xs text-slate-500">Vintage year<Input type="number" value={form.v.vintage_year ?? ''} onChange={e => setForm({ ...form, v: { ...form.v, vintage_year: Number(e.target.value) || null } })} /></label>
              <label className="text-xs text-slate-500">Currency<Input maxLength={3} value={form.v.currency ?? 'EUR'} onChange={e => setForm({ ...form, v: { ...form.v, currency: e.target.value.toUpperCase() } })} /></label>
              <label className="text-xs text-slate-500">Commitment<Input type="number" value={form.v.commitment} onChange={e => setForm({ ...form, v: { ...form.v, commitment: Number(e.target.value) } })} /></label>
              <label className="text-xs text-slate-500">Status<Select value={form.v.status ?? 'active'} onChange={e => setForm({ ...form, v: { ...form.v, status: e.target.value } })}><option value="active">Active</option><option value="closed">Closed (fully realised)</option></Select></label>
              <label className="text-xs text-slate-500 col-span-2 md:col-span-4">Notes<Input value={form.v.notes ?? ''} onChange={e => setForm({ ...form, v: { ...form.v, notes: e.target.value } })} /></label>
            </div>
            <div className="flex gap-2"><Button size="sm" disabled={saveFund.isPending} onClick={() => saveFund.mutate(form)}>Save</Button><Button size="sm" variant="secondary" onClick={() => setForm(null)}>Cancel</Button></div>
          </CardBody>
        </Card>
      )}

      {selected != null && f && (
        <FundDetail f={f} isDark={isDark} onChanged={refresh}
          onEdit={() => { setForm({ id: f.id, v: { name: f.name, manager: f.manager, fund_type: f.fund_type, vintage_year: f.vintage_year, currency: f.currency, commitment: f.commitment, status: f.status, notes: f.notes } }); setErr(null) }}
          onDelete={() => { if (window.confirm(`Delete “${f.name}” and all its entries?`)) removeFund.mutate(f.id) }} />
      )}
    </div>
  )
}

function FundDetail({ f, isDark, onChanged, onEdit, onDelete }: { f: PrivateFundDetail; isDark: boolean; onChanged: () => void; onEdit: () => void; onDelete: () => void }) {
  const [draft, setDraft] = useState<{ id: number | null; date: string; type: string; amount: string; note: string }>({ id: null, date: today(), type: 'call', amount: '', note: '' })
  const [err, setErr] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: () => {
      const body = { date: draft.date, type: draft.type, amount: Number(draft.amount), note: draft.note || null }
      return draft.id ? updatePrivateFundEvent(f.id, draft.id, body) : addPrivateFundEvent(f.id, body)
    },
    onSuccess: () => { setDraft({ id: null, date: today(), type: draft.type, amount: '', note: '' }); setErr(null); onChanged() },
    onError: (e) => setErr((e as { response?: { data?: { detail?: string } } }).response?.data?.detail ?? 'Could not save.'),
  })
  const remove = useMutation({ mutationFn: (id: number) => deletePrivateFundEvent(f.id, id), onSuccess: onChanged })
  const cur = f.currency
  const ev = f.events
  const bar = (name: string, type: string, color: string, sign: number) => {
    const rows = ev.filter(e => e.type === type)
    return { x: rows.map(e => e.date), y: rows.map(e => sign * e.amount), name, type: 'bar' as const, marker: { color }, hovertemplate: `${name}: %{y:,.0f} ${cur}<extra></extra>` }
  }
  const navs = ev.filter(e => e.type === 'nav')
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between w-full gap-2">
          <CardTitle>{f.name} <span className="text-xs font-normal text-slate-400">{f.fund_type}{f.vintage_year ? ` · ${f.vintage_year}` : ''}{f.manager ? ` · ${f.manager}` : ''} · {cur}</span></CardTitle>
          <div className="flex gap-2"><Button size="sm" variant="secondary" onClick={onEdit}>Edit fund</Button><Button size="sm" variant="secondary" onClick={onDelete}>Delete</Button></div>
        </div>
      </CardHeader>
      <CardBody className="space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
          <Kpi label="Committed" value={money(f.commitment, cur)} />
          <Kpi label="Paid in" value={money(f.paid_in, cur)} sub={f.called_pct != null ? `${fmtNum(f.called_pct, 0)}% called` : undefined} />
          <Kpi label="Unfunded" value={money(f.unfunded, cur)} />
          <Kpi label="Distributions" value={money(f.distributions, cur)} sub={f.dpi != null ? `DPI ${x(f.dpi)}` : undefined} />
          <Kpi label="NAV" value={money(f.nav, cur)} sub={f.nav_date ? `at ${f.nav_date}` : 'none recorded'} />
          <Kpi label="TVPI" value={x(f.tvpi)} sub={f.rvpi != null ? `RVPI ${x(f.rvpi)}` : undefined} />
          <Kpi label="Gain" value={money(f.gain, cur)} color={f.gain >= 0 ? 'text-green-700' : 'text-red-600'} />
          <Kpi label="IRR" value={pct(f.irr_pct)} color={f.irr_pct != null && f.irr_pct >= 0 ? 'text-green-700' : 'text-red-600'} />
        </div>
        {f.notes && <p className="text-xs text-slate-500">{f.notes}</p>}

        {ev.length > 0 && (
          <Plot
            data={[bar('Capital calls', 'call', '#ef4444', -1), bar('Distributions', 'distribution', '#16a34a', 1),
              { x: navs.map(e => e.date), y: navs.map(e => e.amount), name: 'NAV', type: 'scatter' as const, mode: 'lines+markers' as const, line: { color: '#3b82f6', width: 2 }, hovertemplate: `NAV: %{y:,.0f} ${cur}<extra></extra>` }]}
            layout={{ height: 260, margin: { t: 10, r: 10, b: 40, l: 70 }, barmode: 'relative' as const, hovermode: 'x unified' as const, yaxis: { tickformat: ',.0f' }, legend: { orientation: 'h' as const, y: -0.25 }, ...plotLayout(isDark) }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: '100%' }}
          />
        )}

        <div>
          <p className="text-sm font-semibold text-slate-700 mb-2">Entries</p>
          <div className="flex flex-wrap items-end gap-2 mb-3">
            <label className="text-xs text-slate-500">Date<Input type="date" value={draft.date} onChange={e => setDraft({ ...draft, date: e.target.value })} /></label>
            <label className="text-xs text-slate-500">Type<Select value={draft.type} onChange={e => setDraft({ ...draft, type: e.target.value })}>{Object.entries(EVENT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select></label>
            <label className="text-xs text-slate-500"><Tooltip text={draft.type === 'nav' ? 'The fund’s own valuation of what you still hold on that date, from its statement.' : draft.type === 'call' ? 'Money you paid in. Date it in the future for a call the manager has announced.' : 'Money paid back to you.'}>Amount ({cur})</Tooltip><Input type="number" value={draft.amount} onChange={e => setDraft({ ...draft, amount: e.target.value })} /></label>
            <label className="text-xs text-slate-500 flex-1 min-w-[10rem]">Note<Input value={draft.note} onChange={e => setDraft({ ...draft, note: e.target.value })} /></label>
            <Button size="sm" disabled={save.isPending || !draft.amount} onClick={() => save.mutate()}>{draft.id ? 'Update' : 'Add'}</Button>
            {draft.id && <Button size="sm" variant="secondary" onClick={() => setDraft({ id: null, date: today(), type: 'call', amount: '', note: '' })}>Cancel</Button>}
          </div>
          {err && <p className="text-xs text-red-600 mb-2">{err}</p>}
          <table className="w-full text-xs">
            <thead><tr className="bg-slate-50 text-slate-500 uppercase tracking-wide"><th className="px-3 py-2 text-left">Date</th><th className="px-3 py-2 text-left">Type</th><th className="px-3 py-2 text-right">Amount</th><th className="px-3 py-2 text-left">Note</th><th /></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {[...ev].reverse().map(e => (
                <tr key={e.id} className={e.date > today() ? 'text-amber-700' : ''}>
                  <td className="px-3 py-1.5 whitespace-nowrap">{e.date}{e.date > today() && <span className="ml-1 text-xs">(expected)</span>}</td><td className="px-3 py-1.5">{EVENT_LABEL[e.type]}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{money(e.amount, cur)}</td><td className="px-3 py-1.5 text-slate-500">{e.note}</td>
                  <td className="px-3 py-1.5 text-right whitespace-nowrap">
                    <button className="text-blue-600 hover:underline mr-3" onClick={() => setDraft({ id: e.id, date: e.date, type: e.type, amount: String(e.amount), note: e.note ?? '' })}>Edit</button>
                    <button className="text-red-500 hover:underline" onClick={() => { if (window.confirm('Delete this entry?')) remove.mutate(e.id) }}>Delete</button>
                  </td>
                </tr>
              ))}
              {ev.length === 0 && <tr><td colSpan={5} className="px-3 py-3 text-slate-400">No entries yet — add the first capital call, or a NAV from the latest statement.</td></tr>}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  )
}
