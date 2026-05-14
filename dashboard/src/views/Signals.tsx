import { useCallback, useEffect, useState } from 'react'
import { useRefreshContext } from '../App'
import { fetchMarkets, fetchPrediction, Market, Prediction } from '../api'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import styles from './Signals.module.css'

interface SignalRow {
  market: Market
  prediction: Prediction | null
  error: boolean
}

export default function Signals({ intervalMs }: { intervalMs: number }) {
  const [rows, setRows] = useState<SignalRow[]>([])
  const [fetchError, setFetchError] = useState<string | null>(null)
  const ctx = useRefreshContext()

  const load = useCallback(async () => {
    try {
      const markets = await fetchMarkets()
      const settled = await Promise.allSettled(
        markets.map(m => fetchPrediction(m.market_id))
      )
      setRows(markets.map((m, i) => ({
        market: m,
        prediction: settled[i].status === 'fulfilled' ? settled[i].value : null,
        error: settled[i].status === 'rejected',
      })))
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

  const activeCount = rows.filter(r => r.prediction).length
  const avgConf = rows.length
    ? rows.filter(r => r.prediction).reduce((s, r) => s + (r.prediction?.confidence ?? 0), 0) /
      Math.max(1, rows.filter(r => r.prediction).length)
    : 0
  const lowConfCount = rows.filter(r => r.prediction?.low_confidence).length
  const lastIngestAge = rows.length
    ? Math.min(...rows.filter(r => r.prediction?.feature_age_seconds != null)
        .map(r => r.prediction!.feature_age_seconds!))
    : null

  return (
    <div>
      {fetchError && <div className="error-banner">{fetchError}</div>}
      <div className="stat-grid">
        <div className="stat-card">
          <div className="stat-label">Active Markets</div>
          <div className="stat-value">{activeCount}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Last Ingest Age</div>
          <div className="stat-value">{lastIngestAge != null ? `${lastIngestAge}s` : '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Avg Confidence</div>
          <div className="stat-value">{(avgConf * 100).toFixed(1)}%</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Low-Conf Count</div>
          <div className="stat-value">{lowConfCount}</div>
        </div>
      </div>

      {rows.length === 0 && !fetchError && (
        <div className="placeholder">No active markets</div>
      )}

      {rows.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Market</th>
              <th>Signal</th>
              <th>Confidence</th>
              <th>Status</th>
              <th>Closes In</th>
              <th>Feature Age</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ market, prediction, error }) => (
              <tr key={market.market_id}>
                <td>
                  <div>{market.ticker}</div>
                  <div className={styles.muted} style={{ fontSize: 11 }}>{market.market_id}</div>
                </td>
                <td>
                  {error && <span className="badge badge-err">ERROR</span>}
                  {!error && !prediction && <span className="badge badge-warn">NO PRED</span>}
                  {prediction && (
                    <span className={prediction.direction === 'UP' ? styles.up : styles.down}>
                      {prediction.direction === 'UP' ? '↑ UP' : '↓ DOWN'}
                    </span>
                  )}
                </td>
                <td>
                  {prediction && (
                    <div className={styles.confBar}>
                      <div className={styles.confTrack}>
                        <div
                          className={styles.confFill}
                          style={{ width: `${prediction.confidence * 100}%` }}
                        />
                      </div>
                      <span>{(prediction.confidence * 100).toFixed(0)}%</span>
                    </div>
                  )}
                </td>
                <td>
                  {prediction && (
                    <span className={`badge ${prediction.low_confidence ? 'badge-warn' : 'badge-ok'}`}>
                      {prediction.low_confidence ? 'LOW CONF' : 'MODEL OK'}
                    </span>
                  )}
                </td>
                <td className={styles.muted}>
                  {market.minutes_to_close != null ? `${market.minutes_to_close}m` : '—'}
                </td>
                <td className={styles.muted}>
                  {prediction?.feature_age_seconds != null
                    ? `${prediction.feature_age_seconds}s`
                    : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
