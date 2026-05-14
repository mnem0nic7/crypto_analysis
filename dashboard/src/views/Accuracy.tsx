import { useCallback, useEffect, useState } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  AreaChart, Area, CartesianGrid,
} from 'recharts'
import { useRefreshContext } from '../App'
import { fetchHistory, fetchMarkets, fetchSummary, HistoryEntry, StatsSummary } from '../api'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import styles from './Accuracy.module.css'

interface MarketBar {
  ticker: string
  winRate: number
}

interface RollingPoint {
  hour: string
  accuracy: number
}

function buildRolling24h(allHistory: HistoryEntry[]): RollingPoint[] {
  const now = Date.now()
  const points: RollingPoint[] = []
  for (let h = 23; h >= 0; h--) {
    const windowEnd = now - h * 3_600_000
    const windowStart = windowEnd - 3_600_000
    const bucket = allHistory.filter(e => {
      const t = new Date(e.ts).getTime()
      return t >= windowStart && t < windowEnd
    })
    if (bucket.length === 0) continue
    const correct = bucket.filter(e => e.correct).length
    points.push({
      hour: new Date(windowEnd).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      accuracy: Math.round((correct / bucket.length) * 100),
    })
  }
  return points
}

export default function Accuracy({ intervalMs }: { intervalMs: number }) {
  const [summary, setSummary] = useState<StatsSummary | null>(null)
  const [barData, setBarData] = useState<MarketBar[]>([])
  const [rollingData, setRollingData] = useState<RollingPoint[]>([])
  const [fetchError, setFetchError] = useState<string | null>(null)
  const ctx = useRefreshContext()

  const load = useCallback(async () => {
    try {
      const [sum, markets] = await Promise.all([fetchSummary(), fetchMarkets()])
      setSummary(sum)
      setBarData(
        sum.markets.map(m => ({ ticker: m.ticker, winRate: Math.round(m.accuracy * 100) }))
      )
      const histories = await Promise.allSettled(
        markets.map(m => fetchHistory(m.market_id, 200))
      )
      const allHistory: HistoryEntry[] = histories
        .filter((r): r is PromiseFulfilledResult<HistoryEntry[]> => r.status === 'fulfilled')
        .flatMap(r => r.value)
      setRollingData(buildRolling24h(allHistory))
      setFetchError(null)
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  const { lastRefreshed, isLoading, triggerRefresh } = useAutoRefresh(load, intervalMs)

  useEffect(() => {
    ctx.setRefreshFn(triggerRefresh)
    ctx.setIsLoading(isLoading)
    ctx.setLastRefreshed(lastRefreshed)
  }, [ctx, triggerRefresh, isLoading, lastRefreshed])

  const bestMarket = summary?.markets.reduce(
    (best, m) => (!best || m.accuracy > best.accuracy ? m : best),
    null as (typeof summary.markets)[0] | null
  )

  return (
    <div>
      {fetchError && <div className="error-banner">{fetchError}</div>}
      <div className="stat-grid">
        <div className="stat-card">
          <div className="stat-label">Overall Win Rate</div>
          <div className="stat-value">
            {summary ? `${(summary.overall_accuracy * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Best Market</div>
          <div className="stat-value">{bestMarket?.ticker ?? '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Total Settled</div>
          <div className="stat-value">{summary?.total_settled ?? '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">High-Conf Accuracy</div>
          <div className="stat-value">
            {summary ? `${(summary.high_conf_accuracy * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
      </div>

      {(!summary || summary.total_settled === 0) && !fetchError && (
        <div className="placeholder">No settled predictions yet</div>
      )}

      {summary && summary.total_settled > 0 && (
        <div className={styles.charts}>
          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>Win Rate per Market (last 7d)</div>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={barData} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
                <XAxis dataKey="ticker" tick={{ fill: '#94a3b8', fontSize: 11 }} />
                <YAxis domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 11 }} />
                <Tooltip
                  contentStyle={{ background: '#1a1d27', border: '1px solid #2a2d3a' }}
                  formatter={(v: unknown) => [`${String(v)}%`, 'Win Rate']}
                />
                <Bar dataKey="winRate" fill="#7c3aed" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className={styles.chartCard}>
            <div className={styles.chartTitle}>Rolling 24h Accuracy</div>
            {rollingData.length === 0 ? (
              <div className="placeholder" style={{ padding: '60px 0' }}>No data</div>
            ) : (
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={rollingData} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3a" />
                  <XAxis dataKey="hour" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                  <YAxis domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 11 }} />
                  <Tooltip
                    contentStyle={{ background: '#1a1d27', border: '1px solid #2a2d3a' }}
                    formatter={(v: unknown) => [`${String(v)}%`, 'Accuracy']}
                  />
                  <Area
                    type="monotone" dataKey="accuracy"
                    stroke="#7c3aed" fill="rgba(124,58,237,0.15)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
