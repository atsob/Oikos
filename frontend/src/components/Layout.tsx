import React from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { useTheme, type Theme } from '@/lib/theme'
import {
  LayoutDashboard, BookOpen, RefreshCw, BarChart2,
  Database, TrendingUp, Upload, Wrench, BrainCircuit, PieChart,
  Sun, Moon, Monitor,
} from 'lucide-react'

const THEME_OPTIONS: { value: Theme; icon: React.ReactNode; label: string }[] = [
  { value: 'light',  icon: <Sun    size={13} />, label: 'Light'  },
  { value: 'system', icon: <Monitor size={13} />, label: 'System' },
  { value: 'dark',   icon: <Moon   size={13} />, label: 'Dark'   },
]

const nav = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, exact: true },
  { to: '/register', label: 'Cash Register', icon: BookOpen },
  { to: '/investments', label: 'Investments', icon: PieChart },
  { to: '/recurring', label: 'Recurring', icon: RefreshCw },
  { to: '/reports', label: 'Reports', icon: BarChart2 },
  { to: '/static-data', label: 'Static Data', icon: Database },
  { to: '/market-data', label: 'Market Data', icon: TrendingUp },
  { to: '/importers', label: 'Importers', icon: Upload },
  { to: '/tools', label: 'Tools', icon: Wrench },
  { to: '/ai', label: 'AI Assistant', icon: BrainCircuit },
]

export default function Layout() {
  const { theme, setTheme } = useTheme()
  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="flex flex-col w-[220px] shrink-0 bg-slate-900 text-slate-100 overflow-y-auto">
        <div className="px-5 py-4 border-b border-slate-700">
          <span className="text-xl font-bold tracking-tight text-white">Oikos</span>
          <span className="ml-2 text-xs text-slate-400">Finance</span>
        </div>
        <nav className="flex-1 px-2 py-3 space-y-0.5">
          {nav.map(({ to, label, icon: Icon, exact }) => (
            <NavLink
              key={to}
              to={to}
              end={exact}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2.5 px-3 py-2 rounded-md text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-300 hover:bg-slate-700 hover:text-white',
                )
              }
            >
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="px-4 py-3 border-t border-slate-700 space-y-2">
          <div className="flex rounded-md overflow-hidden border border-slate-600">
            {THEME_OPTIONS.map(opt => (
              <button
                key={opt.value}
                onClick={() => setTheme(opt.value)}
                title={opt.label}
                className={cn(
                  'flex-1 flex items-center justify-center py-1 transition-colors',
                  theme === opt.value
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-400 hover:bg-slate-700 hover:text-slate-200'
                )}
              >
                {opt.icon}
              </button>
            ))}
          </div>
          <p className="text-xs text-slate-500">v2.0 · React + FastAPI</p>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto bg-slate-50">
        <Outlet />
      </main>
    </div>
  )
}
