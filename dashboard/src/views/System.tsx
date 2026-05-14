import { useCallback, useEffect, useState } from 'react'
import { useRefreshContext } from '../App'
import { fetchHealth, fetchSlot, HealthResponse } from '../api'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import styles from './System.module.css'

interface ServiceCard {
  name: string
  port: number
  isUp: boolean
  rows: { label: string; value: string }[]
}

function buildCards(health: HealthResponse | null, apiError: boolean): ServiceCard[] {
  const active = health?.active_markets ?? 0
  const stale = health?.stale_markets ?? 0
  return [
    {
      name: 'Ingestor',
      port: 8001,
      isUp: !apiError && active > 0,
      rows: [
        { label: 'Active Markets', value: String(active) },
        { label: 'Stale Markets', value: String(stale) },
      ],
    },
    {
      name: 'Predictor',
      port: 8002,
      isUp: !apiError && active > 0,
      rows: [
        { label: 'Active Markets', value: String(active) },
        { label: 'Stale Markets', value: String(stale) },
      ],
    },
    {
      name: 'API',
      port: 8000,
      isUp: !apiError && health?.status === 'ok',
      rows: [
        { label: 'Status', value: apiError ? 'ERROR' : (health?.status ?? '—') },
        { label: 'Active Markets', value: String(active) },
        { label: 'Stale Markets', value: String(stale) },
      ],
    },
  ]
}

export default function System({ intervalMs }: { intervalMs: number }) {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [apiError, setApiError] = useState(false)
  const [slot, setSlot] = useState<string>('—')
  const [fetchError, setFetchError] = useState<string | null>(null)
  const ctx = useRefreshContext()

  const load = useCallback(async () => {
    try {
      const [healthResult, slotResult] = await Promise.allSettled([
        fetchHealth(),
        fetchSlot(),
      ])
      if (healthResult.status === 'fulfilled') {
        setHealth(healthResult.value)
        setApiError(false)
      } else {
        setHealth(null)
        setApiError(true)
      }
      if (slotResult.status === 'fulfilled') setSlot(slotResult.value.slot)
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

  const cards = buildCards(health, apiError)

  return (
    <div>
      {fetchError && <div className="error-banner">{fetchError}</div>}

      <div className={styles.cards}>
        {cards.map(svc => (
          <div key={svc.name} className={styles.serviceCard}>
            <div className={styles.cardHeader}>
              <div className={`${styles.dot} ${svc.isUp ? styles['dot-green'] : styles['dot-red']}`} />
              <span className={styles.cardTitle}>{svc.name}</span>
            </div>
            <div className={styles.cardRow}>
              <span>Port</span>
              <span>{svc.port}</span>
            </div>
            {svc.rows.map(r => (
              <div key={r.label} className={styles.cardRow}>
                <span>{r.label}</span>
                <span>{r.value}</span>
              </div>
            ))}
          </div>
        ))}

        <div className={`${styles.serviceCard} ${styles.deployCard}`}>
          <div className={styles.cardHeader}>
            <span className={styles.cardTitle}>Deployment</span>
          </div>
          <div className={styles.cardRow}>
            <span>Active Slot</span>
            <span style={{ fontWeight: 700, textTransform: 'uppercase', color: 'var(--accent)' }}>
              {slot}
            </span>
          </div>
          <div className={styles.cardRow}>
            <span>Last Checked</span>
            <span>{lastRefreshed?.toLocaleString() ?? '—'}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
