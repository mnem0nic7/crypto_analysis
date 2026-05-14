import { useCallback, useEffect, useState } from 'react'
import { useRefreshContext } from '../App'
import { fetchMarkets, fetchModels, ModelInfo } from '../api'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import styles from './Models.module.css'

function brierClass(score: number): string {
  if (score < 0.22) return styles['brier-green']
  if (score <= 0.27) return styles['brier-amber']
  return styles['brier-red']
}

export default function Models({ intervalMs }: { intervalMs: number }) {
  const [models, setModels] = useState<ModelInfo[]>([])
  const [totalMarkets, setTotalMarkets] = useState(0)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const ctx = useRefreshContext()

  const load = useCallback(async () => {
    try {
      const [mods, markets] = await Promise.all([fetchModels(), fetchMarkets()])
      setModels(mods)
      setTotalMarkets(markets.length)
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

  const bestBrier = models.length
    ? Math.min(...models.map(m => m.brier_score))
    : null
  const lastTrained = models.length
    ? new Date(Math.max(...models.map(m => new Date(m.trained_at).getTime())))
    : null
  const withoutModel = totalMarkets - models.length

  return (
    <div>
      {fetchError && <div className="error-banner">{fetchError}</div>}
      <div className="stat-grid">
        <div className="stat-card">
          <div className="stat-label">Active Models</div>
          <div className="stat-value">{models.length}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Best Brier Score</div>
          <div className="stat-value">{bestBrier != null ? bestBrier.toFixed(3) : '—'}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Last Trained</div>
          <div className="stat-value" style={{ fontSize: 14 }}>
            {lastTrained ? lastTrained.toLocaleDateString() : '—'}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Markets Without Model</div>
          <div className="stat-value">{withoutModel}</div>
        </div>
      </div>

      {models.length === 0 && !fetchError && (
        <div className="placeholder">No active models — run the trainer first</div>
      )}

      {models.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>Market</th>
              <th>Version</th>
              <th>Brier Score</th>
              <th>Training Rows</th>
              <th>Trained At</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {models.map(m => (
              <tr key={m.market_id}>
                <td>
                  <div>{m.ticker}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{m.market_id}</div>
                </td>
                <td>{m.version}</td>
                <td className={brierClass(m.brier_score)}>{m.brier_score.toFixed(3)}</td>
                <td>{m.training_rows.toLocaleString()}</td>
                <td>{new Date(m.trained_at).toLocaleString()}</td>
                <td>
                  <span className={`badge ${m.is_active ? 'badge-ok' : 'badge-warn'}`}>
                    {m.is_active ? 'ACTIVE' : 'INACTIVE'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
