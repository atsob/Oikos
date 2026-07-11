import React, { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { useTheme, type Theme } from '@/lib/theme'
import { usePersist } from '@/lib/hooks'
import {
  LayoutDashboard, BookOpen, RefreshCw, BarChart2,
  Database, TrendingUp, Upload, Wrench, BrainCircuit, PieChart,
  Sun, Moon, Monitor, PanelLeftClose, PanelLeftOpen, HelpCircle, History, Menu, X,
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
  { to: '/help', label: 'Help', icon: HelpCircle },
  { to: '/releases', label: 'Release Notes', icon: History },
]

export default function Layout() {
  const { theme, setTheme } = useTheme()
  const [collapsed, setCollapsed] = usePersist('sidebar_collapsed', false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const location = useLocation()

  // Close the mobile drawer whenever the route changes (e.g. after tapping a nav link).
  React.useEffect(() => { setMobileOpen(false) }, [location.pathname])

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Mobile top bar — hamburger to open the drawer sidebar. Hidden on desktop,
          where the sidebar is always part of the normal flex flow instead. */}
      <button
        onClick={() => setMobileOpen(true)}
        title="Open menu"
        className="md:hidden fixed top-3 left-3 z-30 flex items-center justify-center w-9 h-9 rounded-md bg-slate-900 text-white shadow-lg"
      >
        <Menu size={18} />
      </button>

      {/* Backdrop, mobile-only, shown while the drawer is open */}
      {mobileOpen && (
        <div
          className="md:hidden fixed inset-0 z-30 bg-black/40"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Sidebar — static in the flex flow on desktop; a fixed off-canvas drawer
          (slides in over the content) below the md breakpoint. */}
      <aside className={cn(
        'flex flex-col shrink-0 bg-slate-900 text-slate-100 overflow-y-auto overflow-x-hidden transition-[width] duration-200',
        'fixed inset-y-0 left-0 z-40 w-[220px] transition-transform duration-200 md:static md:transition-[width]',
        mobileOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0',
        collapsed ? 'md:w-14' : 'md:w-[220px]',
      )}>
        <div className={cn('flex items-center border-b border-slate-700 py-4 px-5 justify-between', collapsed && 'md:justify-center md:px-2')}>
          <div className={cn('flex items-center gap-2', collapsed && 'md:hidden')}>
            <img src="/logo.png" alt="A²360 Consulting" className="w-8 h-8 shrink-0 object-contain" />
            <div className="leading-tight">
              <div className="text-base font-bold tracking-tight text-white">Oikos</div>
              <div className="text-[10px] text-slate-400 tracking-wide">Finance</div>
            </div>
          </div>
          {collapsed && <img src="/logo.png" alt="A²360 Consulting" className="hidden md:block w-7 h-7 object-contain" />}
          <button
            onClick={() => (mobileOpen ? setMobileOpen(false) : setCollapsed(true))}
            title="Collapse sidebar"
            className={cn('text-slate-400 hover:text-white shrink-0', collapsed && 'md:hidden')}
          >
            <X size={17} className="md:hidden" />
            <PanelLeftClose size={17} className="hidden md:block" />
          </button>
        </div>
        {collapsed && (
          <button
            onClick={() => setCollapsed(false)}
            title="Expand sidebar"
            className="hidden md:flex items-center justify-center py-2 text-slate-400 hover:text-white hover:bg-slate-700 border-b border-slate-700"
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
                  collapsed && 'md:justify-center md:px-0',
                  isActive
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-300 hover:bg-slate-700 hover:text-white',
                )
              }
            >
              <Icon size={16} />
              <span className={collapsed ? 'md:hidden' : undefined}>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className={cn('px-4 py-3 border-t border-slate-700 space-y-2', collapsed && 'md:hidden')}>
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
          <a href="https://allabout360c.com" target="_blank" rel="noopener noreferrer" className="text-xs text-slate-400 hover:text-white block">
            allabout360c.com
          </a>
          <a href="mailto:info@allabout360c.com" className="text-xs text-slate-400 hover:text-white block">
            info@allabout360c.com
          </a>
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
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto bg-slate-50 pt-14 md:pt-0">
        <Outlet />
      </main>
    </div>
  )
}
