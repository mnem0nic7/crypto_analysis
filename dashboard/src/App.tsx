import { createContext, useContext, useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import styles from './App.module.css'
import { fetchSlot } from './api'
import Signals from './views/Signals'
import Accuracy from './views/Accuracy'
import Models from './views/Models'
import System from './views/System'

const REFRESH_INTERVAL_MS = 30_000

const VIEW_TITLES: Record<string, string> = {
  '/': 'Live Signals',
  '/accuracy': 'Historical Accuracy',
  '/models': 'Model Health',
  '/system': 'System Health',
}

export const RefreshContext = createContext<{
  setRefreshFn: (fn: () => void) => void
  setIsLoading: (v: boolean) => void
  setLastRefreshed: (d: Date | null) => void
}>({
  setRefreshFn: () => {},
  setIsLoading: () => {},
  setLastRefreshed: () => {},
})
export const useRefreshContext = () => useContext(RefreshContext)

function Topbar({ onRefresh, lastRefreshed, isLoading }: {
  onRefresh: () => void
  lastRefreshed: Date | null
  isLoading: boolean
}) {
  const location = useLocation()
  const title = VIEW_TITLES[location.pathname] ?? ''
  return (
    <div className={styles.topbar}>
      <span className={styles.topbarTitle}>{title}</span>
      <div className={styles.topbarRight}>
        {lastRefreshed && (
          <span>Updated {lastRefreshed.toLocaleTimeString()}</span>
        )}
        <button onClick={onRefresh} disabled={isLoading}>
          {isLoading ? '...' : '↺ Refresh'}
        </button>
      </div>
    </div>
  )
}

function Sidebar({ slot }: { slot: string }) {
  const navItems = [
    { to: '/', label: '📡 Signals' },
    { to: '/accuracy', label: '📊 Accuracy' },
    { to: '/models', label: '🧠 Models' },
    { to: '/system', label: '💚 System' },
  ]
  return (
    <aside className={styles.sidebar}>
      <div className={styles.logo}>⬡ Kalshi Analytics</div>
      <nav className={styles.nav}>
        {navItems.map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `${styles.navItem} ${isActive ? styles.navItemActive : ''}`
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>
      <div className={styles.sidebarFooter}>
        <div>Slot: <span className={styles.slotBadge}>{slot}</span></div>
        <div>Refresh: 30s</div>
      </div>
    </aside>
  )
}

export default function App() {
  const [slot, setSlot] = useState('—')
  const [refreshFn, setRefreshFn] = useState<() => void>(() => () => {})
  const [isLoading, setIsLoading] = useState(false)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)

  useEffect(() => {
    fetchSlot().then(r => setSlot(r.slot)).catch(() => {})
  }, [])

  return (
    <BrowserRouter>
      <RefreshContext.Provider value={{
        setRefreshFn: fn => setRefreshFn(() => fn),
        setIsLoading,
        setLastRefreshed,
      }}>
        <div className={styles.layout}>
          <Sidebar slot={slot} />
          <div className={styles.main}>
            <Topbar
              onRefresh={refreshFn}
              lastRefreshed={lastRefreshed}
              isLoading={isLoading}
            />
            <div className={styles.content}>
              <Routes>
                <Route path="/" element={<Signals intervalMs={REFRESH_INTERVAL_MS} />} />
                <Route path="/accuracy" element={<Accuracy intervalMs={REFRESH_INTERVAL_MS} />} />
                <Route path="/models" element={<Models intervalMs={REFRESH_INTERVAL_MS} />} />
                <Route path="/system" element={<System intervalMs={REFRESH_INTERVAL_MS} />} />
              </Routes>
            </div>
          </div>
        </div>
      </RefreshContext.Provider>
    </BrowserRouter>
  )
}
