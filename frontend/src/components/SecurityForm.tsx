import type { ReactNode } from 'react'
import { Input } from '@/components/ui'

export const SECURITY_TYPES = ['Stock', 'ETF', 'Bond', 'Mutual Fund', 'Crypto', 'Option', 'Commodity', 'PF_Unit', 'CD', 'Emp. Stock Opt.', 'FX Spot', 'Market Index', 'CFD', 'Closed-End Fund', 'Other']

export const EMPTY_SECURITY_FORM: Record<string, string> = {
  ticker: '', name: '', type: 'Stock', currencies_id: '', is_active: 'true', is_tax_exempt: 'false',
  isin: '', sector: '', industry: '', yahoo_ticker: '', tv_symbol: '', tv_exchange: '',
  maturity_date: '', coupon_rate: '', coupon_frequency: '', face_value: '',
  dividend_yield: '', dividend_rate: '', dividend_frequency: '', ex_dividend_date: '',
  dividend_pay_date: '', payout_ratio: '', five_year_avg_yield: '',
  analyst_rating: '', analyst_target_price: '',
  tax_category: '',
}

function FormField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-medium text-slate-500 mb-1">{label}</label>
      {children}
    </div>
  )
}

function FormSection({ children }: { children: ReactNode }) {
  return <p className="col-span-3 text-xs font-semibold text-slate-400 uppercase tracking-wide pt-2 border-t border-slate-100">{children}</p>
}

export function SecurityFormFields({ form, set, currencies, taxRules }: {
  form: Record<string, string>
  set: (k: string, v: string) => void
  currencies: Record<string, unknown>[]
  taxRules: Record<string, unknown>[]
}) {
  const BoolField = ({ k, label }: { k: string; label: string }) => (
    <FormField label={label}>
      <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form[k] ?? 'false'} onChange={e => set(k, e.target.value)}>
        <option value="true">Yes</option>
        <option value="false">No</option>
      </select>
    </FormField>
  )

  return (
    <div className="grid grid-cols-3 gap-3">
      <FormSection>Identity</FormSection>
      <FormField label="Ticker *"><Input value={form.ticker} onChange={e => set('ticker', e.target.value)} placeholder="AAPL" className="font-mono" /></FormField>
      <div className="col-span-2">
        <FormField label="Name *"><Input value={form.name} onChange={e => set('name', e.target.value)} /></FormField>
      </div>
      <FormField label="Type *">
        <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.type} onChange={e => set('type', e.target.value)}>
          {SECURITY_TYPES.map(t => <option key={t}>{t}</option>)}
        </select>
      </FormField>
      <FormField label="Currency">
        <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.currencies_id} onChange={e => set('currencies_id', e.target.value)}>
          <option value="">— select —</option>
          {currencies.map(c => <option key={String(c.id)} value={String(c.id)}>{String(c.code)} · {String(c.name)}</option>)}
        </select>
      </FormField>
      <FormField label="ISIN"><Input value={form.isin} onChange={e => set('isin', e.target.value)} placeholder="US0378331005" className="font-mono" /></FormField>
      <BoolField k="is_active" label="Is Active" />
      <BoolField k="is_tax_exempt" label="Tax Exempt" />
      <FormField label="Tax Category">
        <select className="w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm" value={form.tax_category ?? ''} onChange={e => set('tax_category', e.target.value)}>
          <option value="">— not set —</option>
          {taxRules.map(r => (
            <option key={String(r.tax_category)} value={String(r.tax_category)}>{String(r.display_name)}</option>
          ))}
        </select>
      </FormField>
      <FormField label="Sector"><Input value={form.sector} onChange={e => set('sector', e.target.value)} /></FormField>
      <FormField label="Industry"><Input value={form.industry} onChange={e => set('industry', e.target.value)} /></FormField>

      <FormSection>Data Sources</FormSection>
      <FormField label="Yahoo Ticker"><Input value={form.yahoo_ticker} onChange={e => set('yahoo_ticker', e.target.value)} placeholder="AAPL" className="font-mono" /></FormField>
      <FormField label="TV Symbol"><Input value={form.tv_symbol} onChange={e => set('tv_symbol', e.target.value)} placeholder="AAPL" className="font-mono" /></FormField>
      <FormField label="TV Exchange"><Input value={form.tv_exchange} onChange={e => set('tv_exchange', e.target.value)} placeholder="NASDAQ" /></FormField>

      <FormSection>Fixed Income</FormSection>
      <FormField label="Maturity Date"><Input type="date" value={form.maturity_date} onChange={e => set('maturity_date', e.target.value)} /></FormField>
      <FormField label="Coupon Rate %"><Input type="number" step="0.001" value={form.coupon_rate} onChange={e => set('coupon_rate', e.target.value)} placeholder="0.000" /></FormField>
      <FormField label="Coupon Frequency"><Input value={form.coupon_frequency} onChange={e => set('coupon_frequency', e.target.value)} placeholder="Annual" /></FormField>
      <FormField label="Face Value"><Input type="number" step="0.01" value={form.face_value} onChange={e => set('face_value', e.target.value)} placeholder="1000.00" /></FormField>

      <FormSection>Dividends</FormSection>
      <FormField label="Dividend Yield %"><Input type="number" step="0.0001" value={form.dividend_yield} onChange={e => set('dividend_yield', e.target.value)} placeholder="0.0000" /></FormField>
      <FormField label="Dividend Rate"><Input type="number" step="0.0001" value={form.dividend_rate} onChange={e => set('dividend_rate', e.target.value)} placeholder="0.0000" /></FormField>
      <FormField label="Dividend Frequency"><Input value={form.dividend_frequency} onChange={e => set('dividend_frequency', e.target.value)} placeholder="Quarterly" /></FormField>
      <FormField label="Ex-Dividend Date"><Input type="date" value={form.ex_dividend_date} onChange={e => set('ex_dividend_date', e.target.value)} /></FormField>
      <FormField label="Dividend Pay Date"><Input type="date" value={form.dividend_pay_date} onChange={e => set('dividend_pay_date', e.target.value)} /></FormField>
      <FormField label="Payout Ratio %"><Input type="number" step="0.01" value={form.payout_ratio} onChange={e => set('payout_ratio', e.target.value)} placeholder="0.00" /></FormField>
      <FormField label="5Y Avg Yield %"><Input type="number" step="0.0001" value={form.five_year_avg_yield} onChange={e => set('five_year_avg_yield', e.target.value)} placeholder="0.0000" /></FormField>

      <FormSection>Analyst</FormSection>
      <FormField label="Rating"><Input value={form.analyst_rating} onChange={e => set('analyst_rating', e.target.value)} placeholder="Buy" /></FormField>
      <FormField label="Target Price"><Input type="number" step="0.01" value={form.analyst_target_price} onChange={e => set('analyst_target_price', e.target.value)} placeholder="0.00" /></FormField>
    </div>
  )
}
