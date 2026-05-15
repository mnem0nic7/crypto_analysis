import { useCallback, useEffect, useState } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  AreaChart, Area, CartesianGrid,
  ComposedChart, Line,
} from 'recharts'
import { useRefreshContext } from '../App'
import { fetchSeriesHistory, fetchSummary } from '../api'
import type { HistoryEntry, StatsSummary } from '../api'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import styles from './Accuracy.module.css'

interface MarketBar {
  ticker: string
  winRate: number
  count: number
}

interface RollingPoint {
  label: string
  accuracy: number
  count: number
}

interface CalibPoint {
  bucket: string
  predicted: number
  actual: number | null
  count: number
}

interface DirPoint {
  ticker: string
  up: number | null
  upCount: number
  down: number | null
  downCount: number
}

function buildRolling(allHistory: HistoryEntry[], window: '24h' | '7d' | '30d'): RollingPoint[] {
  const now = Date.now()
  const config = {
    '24h': { buckets: 24, bucketMs: 3_600_000 },
    '7d': { buckets: 7, bucketMs: 86_400_000 },
    '30d': { buckets: 30, bucketMs: 86_400_000 },
  }[window]
  const { buckets, bucketMs } = config
  const points: RollingPoint[] = []
  for (let i = buckets - 1; i >= 0; i--) {
    const windowEnd = now - i * bucketMs
    const windowStart = windowEnd - bucketMs
    const bucket = allHistory.filter(e => {
      const t = new Date(e.ts).getTime()
      return t >= windowStart && t < windowEnd
    })
    if (bucket.length === 0) continue
    const correct = bucket.filter(e => e.correct).length
    const label = window === '24h'
      ? new Date(windowEnd).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : new Date(windowEnd).toLocaleDateString([], { month: 'short', day: 'numeric' })
    points.push({ label, accuracy: Math.round((correct / bucket.length) * 100), count: bucket.length })
  }
  return points
}

function buildCalibration(allHistory: HistoryEntry[]): CalibPoint[] {
  const edges = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 1.01]
  return edges.slice(0, -1).map((lo, idx) => {
    const hi = edges[idx + 1]
    const mid = (lo + hi) / 2
    const bucket = allHistory.filter(e => e.confidence >= lo && e.confidence < hi)
    const correct = bucket.filter(e => e.correct).length
    return {
      bucket: `${(lo * 100).toFixed(0)}–${(hi === 1.01 ? 100 : hi * 100).toFixed(0)}`,
      predicted: Math.round(mid * 100),
      actual: bucket.length >= 3 ? Math.round((correct / bucket.length) * 100) : null,
      count: bucket.length,
    }
  }).filter(p => p.count > 0)
}

function seriesAccuracyColor(accuracy: number): string {
  if (accuracy >= 0.9) return '#22c55e'
  if (accuracy >= 0.8) return '#f59e0b'
  return '#ef4444'
}

export default function Accuracy({ intervalMs }: { intervalMs: number }) {
  const [summary, setSummary] = useState<StatsSummary | null>(null)
  const [avgBrier, setAvgBrier] = useState<number | null>(null)
  const [barData, setBarData] = useState<MarketBar[]>([])
  const [selectedSeries, setSelectedSeries] = useState<string>('')
  const [seriesHistory, setSeriesHistory] = useState<HistoryEntry[]>([])
  const [rollingWindow, setRollingWindow] = useState<'24h' | '7d' | '30d'>('7d')
  const [rollingData, setRollingData] = useState<RollingPoint[]>([])
  const [calibData, setCalibData] = useState<CalibPoint[]>([])
  const [fetchError, setFetchError] = useState<string | null>(null)
  const ctx = useRefreshContext()

  const load = useCallback(async () => {
    try {
      const sum = await fetchSummary()
      setSummary(sum)

      const briersWithValues = sum.markets.filter(m => m.brier_score != null)
      setAvgBrier(briersWithValues.length > 0
        ? briersWithValues.reduce((s, m) => s + (m.brier_score ?? 0), 0) / briersWithValues.length
        : null
      )

      setBarData(sum.markets.map(m => ({
        ticker: m.ticker.replace('KX', '').replace('15M', ''),
        winRate: Math.round(m.accuracy * 100),
        count: m.settled_count,
      })))

      // Keep current selection if still valid, otherwise fall back to first market
      const tickers = new Set(sum.markets.map(m => m.ticker))
      setSelectedSeries(prev => (prev && tickers.has(prev)) ? prev : (sum.markets[0]?.ticker || ''))
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

  // Fetch series history when series selector changes; cancelled flag prevents stale overwrites
  useEffect(() => {
    if (!selectedSeries) return
    let cancelled = false
    fetchSeriesHistory(selectedSeries)
      .then(history => { if (!cancelled) setSeriesHistory(history) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [selectedSeries])

  // Rebuild rolling and calibration when history or window changes — no re-fetch needed
  useEffect(() => {
    setRollingData(buildRolling(seriesHistory, rollingWindow))
    setCalibData(buildCalibration(seriesHistory))
  }, [seriesHistory, rollingWindow])

  const dirData: DirPoint[] = (summary?.markets ?? []).map(m => ({
    ticker: m.ticker.replace('KX', '').replace('15M', ''),
    up: (m.up_count ?? 0) >= 3 ? Math.round((m.up_accuracy ?? 0) * 100) : null,
    upCount: m.up_count ?? 0,
    down: (m.down_count ?? 0) >= 3 ? Math.round((m.down_accuracy ?? 0) * 100) : null,
    downCount: m.down_count ?? 0,
  }))

  const tooltipStyle = { background: '#1a1d27', border: '1px solid #2a2d3a' }

  return (
    <div>
      {fetchError && <div className="error-banner">{fetchError}</div>}

      {/* Global stats — 4 cards */}
      <div className={styles.statGrid4}>
        <div className="stat-card">
          <div className="stat-label">Overall Win Rate</div>
          <div className="stat-value">
            {summary ? `${(summary.overall_accuracy * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">High-Conf Accuracy</div>
          <div className="stat-value">
            {summary ? `${(summary.high_conf_accuracy * 100).toFixed(1)}%` : '—'}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Total Settled</div>
          <div className="stat-value">{summary?.total_settled.toLocaleString() ?? '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Avg Brier Score</div>
          <div className="stat-value">{avgBrier !== null ? avgBrier.toFixed(3) : '—'}</div>
        </div>
      </div>

      {/* Per-series mini-cards */}
      {summary && summary.markets.length > 0 && (
        <div className={styles.seriesGrid}>
          {summary.markets.map(m => {
            const shortTicker = m.ticker.replace('KX', '').replace('15M', '')
            const pct = Math.round(m.accuracy * 100)
            const check = m.accuracy >= 0.95 ? ' ✓' : ''
            return (
              <div key={m.ticker} className={styles.seriesCard}>
                <div className={styles.seriesTicker}>{shortTicker}</div>
                <div
                  className={styles.seriesRate}
                  style={{ color: seriesAccuracyColor(m.accuracy) }}
                >
                  {pct}%{check}
                </div>
                <div className={styles.seriesMeta}>
                  n={m.settled_count.toLocaleString()}
                  {m.brier_score != null && <><br />Brier {m.brier_score.toFixed(3)}</>}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {(!summary || summary.total_settled === 0) && !fetchError && (
        <div className="placeholder">No settled predictions yet</div>
      )}

      {summary && summary.total_settled > 0 && (
        <>
          {/* Top chart row: Win Rate per Series + Directional Accuracy */}
          <div className={styles.charts}>
            <div className={styles.chartCard}>
              <div className={styles.chartTitle}>Win Rate per Series</div>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={barData} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
                  <XAxis dataKey="ticker" tick={{ fill: '#94a3b8', fontSize: 11 }} />
                  <YAxis domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 11 }} />
                  <Tooltip
                    contentStyle={tooltipStyle}
                    formatter={(v: unknown, _name: unknown, props: { payload?: MarketBar }) => [
                      `${String(v)}% (n=${props.payload?.count ?? 0})`,
                      'Win Rate',
                    ]}
                  />
                  <Bar dataKey="winRate" fill="#7c3aed" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>

            <div className={styles.chartCard}>
              <div className={styles.chartTitle}>Directional Accuracy (UP vs DOWN)</div>
              {dirData.length === 0 ? (
                <div className="placeholder" style={{ padding: '60px 0' }}>No data</div>
              ) : (
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={dirData} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
                    <XAxis dataKey="ticker" tick={{ fill: '#94a3b8', fontSize: 11 }} />
                    <YAxis domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 11 }} />
                    <Tooltip
                      contentStyle={tooltipStyle}
                      formatter={(v: unknown, name: string, props: { payload?: DirPoint }) => {
                        const count = name === 'up' ? props.payload?.upCount : props.payload?.downCount
                        return [`${String(v)}% (n=${count ?? 0})`, name === 'up' ? 'UP' : 'DOWN']
                      }}
                    />
                    <Bar dataKey="up" fill="#22c55e" radius={[3, 3, 0, 0]} name="up" />
                    <Bar dataKey="down" fill="#ef4444" radius={[3, 3, 0, 0]} name="down" />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* Series selector + window toggle */}
          <div className={styles.seriesSelector}>
            <span className={styles.selectorLabel}>Series:</span>
            <select
              className={styles.seriesSelect}
              value={selectedSeries}
              onChange={e => setSelectedSeries(e.target.value)}
            >
              {summary.markets.map(m => (
                <option key={m.ticker} value={m.ticker}>{m.ticker}</option>
              ))}
            </select>
            <div className={styles.windowToggle}>
              {(['24h', '7d', '30d'] as const).map(w => (
                <button
                  key={w}
                  className={`${styles.windowBtn} ${rollingWindow === w ? styles.windowBtnActive : ''}`}
                  onClick={() => setRollingWindow(w)}
                >
                  {w}
                </button>
              ))}
            </div>
          </div>

          {/* Rolling Accuracy + Calibration Curve */}
          <div className={styles.charts}>
            <div className={styles.chartCard}>
              <div className={styles.chartTitle}>Rolling Accuracy</div>
              {rollingData.length === 0 ? (
                <div className="placeholder" style={{ padding: '60px 0' }}>No data</div>
              ) : (
                <ResponsiveContainer width="100%" height={200}>
                  <AreaChart data={rollingData} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3a" />
                    <XAxis dataKey="label" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                    <YAxis domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 11 }} />
                    <Tooltip
                      contentStyle={tooltipStyle}
                      formatter={(v: unknown, _name: unknown, props: { payload?: RollingPoint }) => [
                        `${String(v)}% (n=${props.payload?.count ?? 0})`,
                        'Accuracy',
                      ]}
                    />
                    <Area type="monotone" dataKey="accuracy" stroke="#7c3aed" fill="rgba(124,58,237,0.15)" />
                  </AreaChart>
                </ResponsiveContainer>
              )}
            </div>

            <div className={styles.chartCard}>
              <div className={styles.chartTitle}>Calibration Curve</div>
              {calibData.length === 0 ? (
                <div className="placeholder" style={{ padding: '60px 0' }}>Not enough data</div>
              ) : (
                <ResponsiveContainer width="100%" height={200}>
                  <ComposedChart data={calibData} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3a" />
                    <XAxis dataKey="bucket" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                    <YAxis domain={[0, 100]} tick={{ fill: '#94a3b8', fontSize: 11 }} />
                    <Tooltip
                      contentStyle={tooltipStyle}
                      formatter={(v: unknown, name: string, props: { payload?: CalibPoint }) => {
                        if (name === 'actual') return [`${String(v)}% (n=${props.payload?.count ?? 0})`, 'Actual']
                        return [`${String(v)}%`, 'Perfect']
                      }}
                    />
                    <Line type="monotone" dataKey="predicted" stroke="#2a2d3a" strokeDasharray="4 4" dot={false} name="predicted" />
                    <Line type="monotone" dataKey="actual" stroke="#7c3aed" dot={{ r: 3, fill: '#7c3aed' }} connectNulls={false} name="actual" />
                  </ComposedChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
