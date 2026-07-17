import { Component, useEffect, type ReactNode } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { useSettings } from '@/lib/hooks'
import { setReportingFx } from '@/lib/settings'
import { getCurrenciesMaster } from '@/lib/api'

class ErrorBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null }
  static getDerivedStateFromError(e: Error) { return { error: e.message } }
  render() {
    if (this.state.error) {
      return (
        <div className="p-8 text-red-600">
          <p className="font-semibold">Page error:</p>
          <pre className="text-xs mt-2 whitespace-pre-wrap">{this.state.error}</pre>
          <button className="mt-4 text-sm underline" onClick={() => this.setState({ error: null })}>Retry</button>
        </div>
      )
    }
    return this.props.children
  }
}
import { ThemeProvider } from '@/lib/theme'
import Layout from '@/components/Layout'
import Dashboard from '@/pages/Dashboard'
import Register from '@/pages/Register'
import Reports from '@/pages/Reports'
import StaticData from '@/pages/StaticData'
import MarketData from '@/pages/MarketData'
import News from '@/pages/News'
import Importers from '@/pages/Importers'
import Tools from '@/pages/Tools'
import AIAssistant from '@/pages/AIAssistant'
import Recurring from '@/pages/Recurring'
import Investments from '@/pages/Investments'
import SecurityDetail from '@/pages/SecurityDetail'
import Help from '@/pages/Help'
import Releases from '@/pages/Releases'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
})

// Keeps the reporting FX rate in sync with the selected reporting currency
function ReportingFxSync() {
  const [{ reportingCurrency }] = useSettings()
  const { data: currencies = [] } = useQuery<Record<string, unknown>[]>({
    queryKey: ['currencies-master'],
    queryFn: getCurrenciesMaster,
    staleTime: 5 * 60_000,
  })

  useEffect(() => {
    if (reportingCurrency === 'EUR') { setReportingFx(1.0, 'EUR'); return }
    const found = currencies.find(c => String(c.code) === reportingCurrency)
    if (found?.latest_rate != null) setReportingFx(Number(found.latest_rate), reportingCurrency)
  }, [reportingCurrency, currencies])

  return null
}

export default function App() {
  return (
    <ThemeProvider>
    <QueryClientProvider client={queryClient}>
      <ReportingFxSync />
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<ErrorBoundary><Dashboard /></ErrorBoundary>} />
            <Route path="register" element={<ErrorBoundary><Register /></ErrorBoundary>} />
            <Route path="recurring" element={<ErrorBoundary><Recurring /></ErrorBoundary>} />
            <Route path="investments" element={<ErrorBoundary><Investments /></ErrorBoundary>} />
            <Route path="reports" element={<ErrorBoundary><Reports /></ErrorBoundary>} />
            <Route path="static-data" element={<ErrorBoundary><StaticData /></ErrorBoundary>} />
            <Route path="market-data" element={<ErrorBoundary><MarketData /></ErrorBoundary>} />
            <Route path="news" element={<ErrorBoundary><News /></ErrorBoundary>} />
            <Route path="securities/:id" element={<ErrorBoundary><SecurityDetail /></ErrorBoundary>} />
            <Route path="importers" element={<ErrorBoundary><Importers /></ErrorBoundary>} />
            <Route path="tools" element={<ErrorBoundary><Tools /></ErrorBoundary>} />
            <Route path="ai" element={<ErrorBoundary><AIAssistant /></ErrorBoundary>} />
            <Route path="help" element={<ErrorBoundary><Help /></ErrorBoundary>} />
            <Route path="releases" element={<ErrorBoundary><Releases /></ErrorBoundary>} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
    </ThemeProvider>
  )
}
