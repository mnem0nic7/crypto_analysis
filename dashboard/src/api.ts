// All paths go through nginx /api/ prefix, which nginx strips before forwarding to api:8000

export interface Market {
  market_id: string
  ticker: string
  title: string | null
  close_time: string | null
  minutes_to_close: number | null
}

export interface Prediction {
  market_id: string
  direction: 'UP' | 'DOWN'
  confidence: number
  low_confidence: boolean
  model_version: string
  ts: string
  feature_age_seconds: number | null
}

export interface HistoryEntry {
  ts: string
  direction: 'UP' | 'DOWN'
  confidence: number
  actual_outcome: number
  correct: boolean
}

export interface MarketSummary {
  ticker: string
  accuracy: number
  settled_count: number
}

export interface StatsSummary {
  total_settled: number
  overall_accuracy: number
  high_conf_accuracy: number
  markets: MarketSummary[]
}

export interface ModelInfo {
  market_id: string
  ticker: string
  version: string
  brier_score: number
  training_rows: number
  trained_at: string
  is_active: boolean
}

export interface HealthResponse {
  status: string
  active_markets: number
  stale_markets: number
}

export interface SlotResponse {
  slot: string
}

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`)
  return res.json() as Promise<T>
}

export const fetchMarkets = (): Promise<Market[]> =>
  apiFetch<Market[]>('/markets')

export const fetchPrediction = (market_id: string): Promise<Prediction> =>
  apiFetch<Prediction>(`/predict/${encodeURIComponent(market_id)}`)

export const fetchHistory = (market_id: string, limit = 200): Promise<HistoryEntry[]> =>
  apiFetch<HistoryEntry[]>(`/history/${encodeURIComponent(market_id)}?limit=${limit}`)

export const fetchSummary = (): Promise<StatsSummary> =>
  apiFetch<StatsSummary>('/stats/summary')

export const fetchModels = (): Promise<ModelInfo[]> =>
  apiFetch<ModelInfo[]>('/stats/models')

export const fetchHealth = (): Promise<HealthResponse> =>
  apiFetch<HealthResponse>('/health')

export const fetchSlot = (): Promise<SlotResponse> =>
  apiFetch<SlotResponse>('/slot')

export interface TrainingStatus {
  last_trained_at: string | null
  active_models: number
  settled_last_24h: number
  unmodeled_markets: number
}

export const fetchTrainingStatus = (): Promise<TrainingStatus> =>
  apiFetch<TrainingStatus>('/stats/training')
