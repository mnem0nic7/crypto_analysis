import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useRefreshContext } from '../App'
import {
  fetchMarkets, fetchRawFeatures, fetchFeatureVectors,
  fetchDataStats, fetchDataCorrelations, fetchFeatureImportance,
} from '../api'
import type {
  RawFeatureRow, FeatureVectorRow, ColumnStat, CorrelationEntry, ImportanceEntry,
  DataFilter,
} from '../api'
import styles from './DataExplorer.module.css'

type Tab = 'raw' | 'vectors' | 'analysis'

const RAW_COLS: Array<{ key: keyof RawFeatureRow; label: string; signed?: boolean }> = [
  { key: 'ts', label: 'Timestamp' },
  { key: 'price_close', label: 'Close' },
  { key: 'volume', label: 'Volume' },
  { key: 'book_imbalance', label: 'Book Imbal.', signed: true },
  { key: 'kalshi_yes_price', label: 'Kalshi Yes' },
  { key: 'kalshi_no_price', label: 'Kalshi No' },
  { key: 'price_momentum_1m', label: 'Mom 1m', signed: true },
  { key: 'price_momentum_5m', label: 'Mom 5m', signed: true },
  { key: 'price_momentum_15m', label: 'Mom 15m', signed: true },
  { key: 'volatility_5m', label: 'Vol 5m' },
  { key: 'bid_depth_1pct', label: 'Bid Depth' },
  { key: 'ask_depth_1pct', label: 'Ask Depth' },
]

function fmt(v: number | null, decimals = 4, signed = false): ReactNode {
  if (v === null || v === undefined) return <span style={{ color: 'var(--text-muted)' }}>—</span>
  const s = typeof v === 'number' ? v.toFixed(decimals) : String(v)
  if (signed) {
    const cls = v >= 0 ? styles.signPos : styles.signNeg
    return <span className={cls}>{v >= 0 ? '+' : ''}{s}</span>
  }
  return <>{s}</>
}

function RawDataTab({ filter }: { filter: DataFilter }) {
  const [page, setPage] = useState(1)
  const [sortBy, setSortBy] = useState('ts')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [data, setData] = useState<{ rows: RawFeatureRow[]; total: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await fetchRawFeatures(filter, page, 100, sortBy, sortDir)
      setData({ rows: result.rows, total: result.total })
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [filter, page, sortBy, sortDir])

  useEffect(() => { load() }, [load])

  const totalPages = data ? Math.ceil(data.total / 100) : 1

  function toggleSort(col: string) {
    if (sortBy === col) {
      setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    } else {
      setSortBy(col)
      setSortDir('desc')
    }
    setPage(1)
  }

  function SortIcon({ col }: { col: string }) {
    if (sortBy !== col) return <span style={{ color: 'var(--text-muted)' }}> ↕</span>
    return <span style={{ color: 'var(--accent)' }}> {sortDir === 'desc' ? '↓' : '↑'}</span>
  }

  if (error) return <div className="error-banner">{error}</div>

  return (
    <div>
      <div className={styles.tableWrap}>
        <table className="data-table">
          <thead>
            <tr>
              {RAW_COLS.map(c => (
                <th
                  key={c.key}
                  onClick={() => toggleSort(c.key as string)}
                  style={{ cursor: 'pointer', whiteSpace: 'nowrap' }}
                >
                  {c.label}<SortIcon col={c.key as string} />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={RAW_COLS.length} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>Loading…</td></tr>
            )}
            {!loading && data?.rows.map(r => (
              <tr key={r.id}>
                {RAW_COLS.map(c => (
                  <td key={c.key} style={{ whiteSpace: c.key === 'ts' ? 'nowrap' : undefined }}>
                    {c.key === 'ts'
                      ? new Date(r.ts).toLocaleString()
                      : fmt(r[c.key] as number | null, c.key === 'price_close' ? 0 : 4, c.signed)}
                  </td>
                ))}
              </tr>
            ))}
            {!loading && data?.rows.length === 0 && (
              <tr><td colSpan={RAW_COLS.length} className={styles.placeholder}>No data for this filter</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className={styles.pagination}>
        <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>← Prev</button>
        <span className={styles.pageInfo}>Page {page} of {totalPages}</span>
        <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages}>Next →</button>
        <span style={{ marginLeft: 'auto' }}>100 rows / page · {data?.total.toLocaleString() ?? 0} total</span>
      </div>
    </div>
  )
}

function FeatureVectorsTab({ filter }: { filter: DataFilter }) {
  const [page, setPage] = useState(1)
  const [sortBy, setSortBy] = useState('ts')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [data, setData] = useState<{ rows: FeatureVectorRow[]; total: number; skipped: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const FEATURE_NAMES = [
    'price_momentum_1m', 'price_momentum_5m', 'price_momentum_15m',
    'volatility_5m', 'volatility_roc', 'vwap_deviation_15m',
    'candle_body_ratio', 'volume_momentum_5m',
    'book_imbalance_latest', 'book_imbalance_trend', 'bid_depth_1pct', 'ask_depth_1pct',
    'kalshi_yes_price', 'kalshi_no_price', 'kalshi_price_momentum_5m', 'kalshi_deviation',
    'kalshi_volume_zscore', 'kalshi_volume_momentum',
    'minutes_to_close', 'sin_hour', 'cos_hour', 'sin_dow', 'cos_dow', 'is_weekend',
    'consecutive_direction',
  ]

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const result = await fetchFeatureVectors(filter, page, sortBy, sortDir)
      setData({ rows: result.rows, total: result.total, skipped: result.skipped })
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [filter, page, sortBy, sortDir])

  useEffect(() => { load() }, [load])

  const totalPages = data ? Math.ceil(data.total / 50) : 1

  function toggleSort(col: string) {
    if (sortBy === col) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortBy(col); setSortDir('desc') }
    setPage(1)
  }

  function SortIcon({ col }: { col: string }) {
    if (sortBy !== col) return <span style={{ color: 'var(--text-muted)' }}> ↕</span>
    return <span style={{ color: 'var(--accent)' }}> {sortDir === 'desc' ? '↓' : '↑'}</span>
  }

  if (error) return <div className="error-banner">{error}</div>

  const SIGNED_FEATURES = new Set([
    'price_momentum_1m', 'price_momentum_5m', 'price_momentum_15m',
    'volatility_roc', 'vwap_deviation_15m', 'candle_body_ratio',
    'volume_momentum_5m', 'book_imbalance_latest', 'book_imbalance_trend',
    'kalshi_price_momentum_5m', 'kalshi_deviation',
    'kalshi_volume_zscore', 'kalshi_volume_momentum', 'consecutive_direction',
  ])

  return (
    <div>
      {data && data.skipped > 0 && (
        <div style={{ color: 'var(--text-muted)', fontSize: 11, marginBottom: 8 }}>
          {data.skipped} row(s) skipped — insufficient raw feature context
        </div>
      )}
      <div className={styles.tableWrap}>
        <table className="data-table">
          <thead>
            <tr>
              <th onClick={() => toggleSort('ts')} style={{ cursor: 'pointer', whiteSpace: 'nowrap' }}>
                Timestamp<SortIcon col="ts" />
              </th>
              <th>Dir</th>
              <th onClick={() => toggleSort('confidence')} style={{ cursor: 'pointer' }}>
                Conf<SortIcon col="confidence" />
              </th>
              <th onClick={() => toggleSort('actual_outcome')} style={{ cursor: 'pointer' }}>
                Outcome<SortIcon col="actual_outcome" />
              </th>
              {FEATURE_NAMES.map(f => <th key={f} style={{ whiteSpace: 'nowrap' }}>{f}</th>)}
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={4 + FEATURE_NAMES.length} style={{ textAlign: 'center', color: 'var(--text-muted)' }}>Loading…</td></tr>
            )}
            {!loading && data?.rows.map((r, i) => (
              <tr key={i}>
                <td style={{ whiteSpace: 'nowrap' }}>{new Date(r.ts).toLocaleString()}</td>
                <td>
                  <span className={`badge ${r.direction === 'UP' ? 'badge-ok' : 'badge-err'}`}>
                    {r.direction}
                  </span>
                </td>
                <td>{r.confidence.toFixed(3)}</td>
                <td>
                  <span className={`badge ${r.actual_outcome === 1 ? 'badge-ok' : 'badge-err'}`}>
                    {r.actual_outcome === 1 ? 'UP' : 'DOWN'}
                  </span>
                </td>
                {FEATURE_NAMES.map(f => (
                  <td key={f}>{fmt(r.features[f] ?? null, 4, SIGNED_FEATURES.has(f))}</td>
                ))}
              </tr>
            ))}
            {!loading && data?.rows.length === 0 && (
              <tr><td colSpan={4 + FEATURE_NAMES.length} className={styles.placeholder}>No settled predictions in this window</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className={styles.pagination}>
        <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1}>← Prev</button>
        <span className={styles.pageInfo}>Page {page} of {totalPages}</span>
        <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page >= totalPages}>Next →</button>
        <span style={{ marginLeft: 'auto' }}>50 rows / page · {data?.total.toLocaleString() ?? 0} total</span>
      </div>
    </div>
  )
}

function AnalysisTab({ filter }: { filter: DataFilter }) {
  const [stats, setStats] = useState<ColumnStat[]>([])
  const [correlations, setCorrelations] = useState<CorrelationEntry[]>([])
  const [importance, setImportance] = useState<ImportanceEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [s, c, imp] = await Promise.all([
        fetchDataStats(filter),
        fetchDataCorrelations(filter),
        fetchFeatureImportance(filter.series_ticker).catch(() => [] as ImportanceEntry[]),
      ])
      setStats(s)
      setCorrelations(c)
      setImportance(imp)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => { load() }, [load])

  if (error) return <div className="error-banner">{error}</div>
  if (loading) return <div className={styles.placeholder}>Computing analysis…</div>

  const noData = stats.length === 0

  const maxImportance = importance.length ? importance[0].importance : 1

  return (
    <div className={styles.analysisGrid}>

      {/* Column Stats */}
      <div className={styles.analysisCard}>
        <h3>Column Stats — Feature Vectors</h3>
        {noData ? (
          <div className={styles.placeholder}>Not enough settled predictions in this window (need ≥ 5)</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Feature</th>
                <th style={{ textAlign: 'right' }}>Mean</th>
                <th style={{ textAlign: 'right' }}>Std</th>
                <th style={{ textAlign: 'right' }}>Min</th>
                <th style={{ textAlign: 'right' }}>Max</th>
              </tr>
            </thead>
            <tbody>
              {stats.map(s => (
                <tr key={s.feature}>
                  <td style={{ whiteSpace: 'nowrap' }}>{s.feature}</td>
                  <td style={{ textAlign: 'right' }}>{s.mean.toFixed(4)}</td>
                  <td style={{ textAlign: 'right' }}>{s.std.toFixed(4)}</td>
                  <td style={{ textAlign: 'right' }}>{s.min.toFixed(4)}</td>
                  <td style={{ textAlign: 'right' }}>{s.max.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Feature Importance */}
      <div className={styles.analysisCard}>
        <h3>Feature Importance — Active Model</h3>
        {importance.length === 0 ? (
          <div className={styles.placeholder}>No active model — run the trainer first</div>
        ) : (
          <>
            {importance.map(({ feature, importance: imp }) => (
              <div key={feature} className={styles.barRow}>
                <span className={styles.barLabel} title={feature}>{feature}</span>
                <div className={styles.barTrack}>
                  <div
                    className={styles.barFill}
                    style={{ width: `${(imp / maxImportance) * 100}%` }}
                  />
                </div>
                <span className={styles.barValue}>{imp.toFixed(3)}</span>
              </div>
            ))}
          </>
        )}
      </div>

      {/* Correlations */}
      <div className={`${styles.analysisCard} ${styles.fullWidth}`}>
        <h3>Feature Correlation with Outcome (settled predictions only)</h3>
        {noData ? (
          <div className={styles.placeholder}>Not enough settled predictions in this window (need ≥ 5)</div>
        ) : (
          <>
            <div className={styles.corrGrid}>
              {correlations.map(({ feature, r }) => {
                const abs = Math.abs(r)
                const bg = r >= 0
                  ? `rgba(124,58,237,${Math.min(0.8, abs * 2.5)})`
                  : `rgba(239,68,68,${Math.min(0.8, abs * 2.5)})`
                const textColor = abs > 0.15 ? '#fff' : 'var(--text-muted)'
                return (
                  <div key={feature} className={styles.corrTile} style={{ background: bg }} title={feature}>
                    <span className={styles.corrValue} style={{ color: textColor }}>
                      {r >= 0 ? '+' : ''}{r.toFixed(2)}
                    </span>
                    <span className={styles.corrName}>{feature.replace(/_/g, '_​')}</span>
                  </div>
                )
              })}
            </div>
            <div className={styles.corrLegend}>
              Purple = positive correlation with UP outcome &nbsp;|&nbsp; Red = negative &nbsp;|&nbsp; Intensity = magnitude
            </div>
          </>
        )}
      </div>

    </div>
  )
}

export default function DataExplorer() {
  const [tab, setTab] = useState<Tab>('raw')
  const [tickers, setTickers] = useState<string[]>([])
  const [seriesTicker, setSeriesTicker] = useState('')
  const [fromTs, setFromTs] = useState(() => {
    const d = new Date()
    d.setDate(d.getDate() - 7)
    return d.toISOString().split('T')[0]
  })
  const [toTs, setToTs] = useState(() => new Date().toISOString().split('T')[0])
  const [appliedFilter, setAppliedFilter] = useState<DataFilter | null>(null)
  const ctx = useRefreshContext()

  useEffect(() => {
    fetchMarkets().then(markets => {
      const unique = [...new Set(markets.map(m => m.ticker))].sort()
      setTickers(unique)
      if (unique.length > 0) {
        setSeriesTicker(unique[0])
        setAppliedFilter({ series_ticker: unique[0], from_ts: `${fromTs}T00:00:00Z`, to_ts: `${toTs}T23:59:59Z` })
      }
    }).catch(() => {})
  }, [])

  useEffect(() => {
    ctx.setRefreshFn(() => {})
    ctx.setIsLoading(false)
    ctx.setLastRefreshed(null)
  }, [ctx])

  function applyFilter() {
    setAppliedFilter({
      series_ticker: seriesTicker,
      from_ts: fromTs ? `${fromTs}T00:00:00Z` : undefined,
      to_ts: toTs ? `${toTs}T23:59:59Z` : undefined,
    })
  }

  return (
    <div>
      {/* Filter bar */}
      <div className={styles.filterBar}>
        <span className={styles.filterLabel}>Market</span>
        <select
          className={styles.filterInput}
          value={seriesTicker}
          onChange={e => setSeriesTicker(e.target.value)}
        >
          {tickers.map(t => <option key={t} value={t}>{t}</option>)}
        </select>

        <span className={styles.filterLabel}>From</span>
        <input
          type="date"
          className={styles.filterInput}
          value={fromTs}
          onChange={e => setFromTs(e.target.value)}
        />

        <span className={styles.filterLabel}>To</span>
        <input
          type="date"
          className={styles.filterInput}
          value={toTs}
          onChange={e => setToTs(e.target.value)}
        />

        <button onClick={applyFilter}>Apply</button>
      </div>

      {/* Tab bar */}
      <div className={styles.tabBar}>
        {(['raw', 'vectors', 'analysis'] as Tab[]).map(t => (
          <button
            key={t}
            className={`${styles.tab} ${tab === t ? styles.tabActive : ''}`}
            onClick={() => setTab(t)}
          >
            {t === 'raw' ? 'Raw Data' : t === 'vectors' ? 'Feature Vectors' : 'Analysis'}
          </button>
        ))}
      </div>

      {/* Tab panels */}
      {!appliedFilter ? (
        <div className={styles.placeholder}>Loading markets…</div>
      ) : tab === 'raw' ? (
        <RawDataTab filter={appliedFilter} />
      ) : tab === 'vectors' ? (
        <FeatureVectorsTab filter={appliedFilter} />
      ) : (
        <AnalysisTab filter={appliedFilter} />
      )}
    </div>
  )
}
