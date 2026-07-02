import React from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { useTheme, type Theme } from '@/lib/theme'
import { usePersist } from '@/lib/hooks'
import {
  LayoutDashboard, BookOpen, RefreshCw, BarChart2,
  Database, TrendingUp, Upload, Wrench, BrainCircuit, PieChart,
  Sun, Moon, Monitor, PanelLeftClose, PanelLeftOpen,
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
  const [collapsed, setCollapsed] = usePersist('sidebar_collapsed', false)
  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className={cn(
        'flex flex-col shrink-0 bg-slate-900 text-slate-100 overflow-y-auto overflow-x-hidden transition-[width] duration-200',
        collapsed ? 'w-14' : 'w-[220px]',
      )}>
        <div className={cn('flex items-center border-b border-slate-700 py-4', collapsed ? 'justify-center px-2' : 'justify-between px-5')}>
          {collapsed ? (
            <span className="text-xl font-bold tracking-tight text-white">O</span>
          ) : (
            <div>
              <span className="text-xl font-bold tracking-tight text-white">Oikos</span>
              <span className="ml-2 text-xs text-slate-400">Finance</span>
            </div>
          )}
          {!collapsed && (
            <button
              onClick={() => setCollapsed(true)}
              title="Collapse sidebar"
              className="text-slate-400 hover:text-white shrink-0"
            >
              <PanelLeftClose size={17} />
            </button>
          )}
        </div>
        {collapsed && (
          <button
            onClick={() => setCollapsed(false)}
            title="Expand sidebar"
            className="flex items-center justify-center py-2 text-slate-400 hover:text-white hover:bg-slate-700 border-b border-slate-700"
          >
            <PanelLeftOpen size={17} />
          </button>
        )}
        <nav className="flex-1 px-2 py-3 space-y-0.5">
          {nav.map(({ to, label, icon: Icon, exact }) => (
            <NavLink
              key={to}
              to={to}
              end={exact}
              title={collapsed ? label : undefined}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2.5 px-3 py-2 rounded-md text-sm font-medium transition-colors',
                  collapsed && 'justify-center px-0',
                  isActive
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-300 hover:bg-slate-700 hover:text-white',
                )
              }
            >
              <Icon size={16} />
              {!collapsed && label}
            </NavLink>
          ))}
        </nav>
        {!collapsed && (
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
            <p className="text-xs text-slate-600" title={__GIT_DATE__ ? `Built from commit ${__GIT_HASH__} on ${__GIT_DATE__}` : undefined}>
              {__GIT_HASH__}{__GIT_DATE__ && ` · ${__GIT_DATE__}`}
            </p>
            {window.location.protocol === 'https:' && window.location.hostname !== 'localhost' && (
              <a
                href={`http://${window.location.hostname}:8444/ca.crt`}
                className="text-xs text-slate-400 hover:text-blue-400 underline leading-tight"
                title="Download & install the CA certificate to trust this app on this device"
              >
                Install CA cert
              </a>
            )}
          </div>
        )}
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto bg-slate-50">
        <Outlet />
      </main>
    </div>
  )
}
