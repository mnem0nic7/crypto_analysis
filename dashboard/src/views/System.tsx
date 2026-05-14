import { useCallback, useEffect, useState } from 'react'
import { useRefreshContext } from '../App'
import { fetchHealth, fetchSlot, fetchTrainingStatus, HealthResponse, TrainingStatus } from '../api'
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

function formatTrainedAt(iso: string | null): string {
  if (!iso) return 'Never'
  const d = new Date(iso)
  return d.toLocaleString()
}

export default function System({ intervalMs }: { intervalMs: number }) {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [apiError, setApiError] = useState(false)
  const [slot, setSlot] = useState<string>('—')
  const [training, setTraining] = useState<TrainingStatus | null>(null)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const ctx = useRefreshContext()

  const load = useCallback(async () => {
    try {
      const [healthResult, slotResult, trainingResult] = await Promise.allSettled([
        fetchHealth(),
        fetchSlot(),
        fetchTrainingStatus(),
      ])
      if (healthResult.status === 'fulfilled') {
        setHealth(healthResult.value)
        setApiError(false)
      } else {
        setHealth(null)
        setApiError(true)
      }
      if (slotResult.status === 'fulfilled') setSlot(slotResult.value.slot)
      if (trainingResult.status === 'fulfilled') setTraining(trainingResult.value)
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

        <div className={styles.serviceCard}>
          <div className={styles.cardHeader}>
            <span className={styles.cardTitle}>Training</span>
          </div>
          <div className={styles.cardRow}>
            <span>Last Trained</span>
            <span>{formatTrainedAt(training?.last_trained_at ?? null)}</span>
          </div>
          <div className={styles.cardRow}>
            <span>Active Models</span>
            <span>{training?.active_models ?? '—'}</span>
          </div>
          <div className={styles.cardRow}>
            <span>Settled 24h</span>
            <span>{training?.settled_last_24h ?? '—'}</span>
          </div>
          <div className={styles.cardRow}>
            <span>Unmodeled</span>
            <span style={(training?.unmodeled_markets ?? 0) > 0 ? { color: 'var(--red)' } : {}}>
              {training?.unmodeled_markets ?? '—'}
            </span>
          </div>
        </div>

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
