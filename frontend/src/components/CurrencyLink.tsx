import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getCurrencies } from '@/lib/api'

// Currency counterpart of the security-name links: renders a currency code as a link to
// that currency's Currency Detail page. Resolves code → id from the shared ['currencies']
// query (already cached by most pages), so callers only need the code; an unknown code,
// or none, renders as plain text.
export function CurrencyLink({ code, className }: { code: unknown; className?: string }) {
  const navigate = useNavigate()
  const { data: currencies = [] } = useQuery({ queryKey: ['currencies'], queryFn: getCurrencies, staleTime: 5 * 60_000 })
  const text = code == null || code === '' ? '' : String(code).trim()
  const id = text ? (currencies as Record<string, unknown>[]).find(c => String(c.code).trim() === text)?.id : undefined
  if (id == null) return <>{text || '—'}</>
  return (
    <button type="button" onClick={e => { e.stopPropagation(); navigate(`/currencies/${id}`) }}
      className={className ?? 'text-blue-600 hover:underline text-left'}>
      {text}
    </button>
  )
}

// ag-Grid cellRenderer form, for a column whose value is a currency code.
export function CurrencyCell({ value }: { value: unknown }) {
  return <CurrencyLink code={value} />
}
