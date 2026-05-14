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
  high_conf_count: number
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

// ── Data Explorer ─────────────────────────────────────────────────────────────

export interface RawFeatureRow {
  id: number
  ts: string
  market_id: string
  price_open: number | null
  price_high: number | null
  price_low: number | null
  price_close: number | null
  volume: number | null
  bid_depth_1pct: number | null
  ask_depth_1pct: number | null
  book_imbalance: number | null
  kalshi_yes_price: number | null
  kalshi_no_price: number | null
  kalshi_volume: number | null
  price_momentum_1m: number | null
  price_momentum_5m: number | null
  price_momentum_15m: number | null
  volatility_5m: number | null
}

export interface RawFeaturePage {
  rows: RawFeatureRow[]
  total: number
  page: number
  page_size: number
}

export interface FeatureVectorRow {
  ts: string
  direction: 'UP' | 'DOWN'
  confidence: number
  actual_outcome: 0 | 1
  features: Record<string, number>
}

export interface FeatureVectorPage {
  rows: FeatureVectorRow[]
  total: number
  page: number
  page_size: number
  skipped: number
}

export interface ColumnStat {
  feature: string
  mean: number
  std: number
  min: number
  max: number
  null_count: number
}

export interface CorrelationEntry {
  feature: string
  r: number
}

export interface ImportanceEntry {
  feature: string
  importance: number
}

export interface DataFilter {
  series_ticker: string
  from_ts?: string
  to_ts?: string
}

function _dataParams(filter: DataFilter, extra: Record<string, string | number> = {}): string {
  const p = new URLSearchParams({ series_ticker: filter.series_ticker })
  if (filter.from_ts) p.set('from_ts', filter.from_ts)
  if (filter.to_ts) p.set('to_ts', filter.to_ts)
  Object.entries(extra).forEach(([k, v]) => p.set(k, String(v)))
  return p.toString()
}

export const fetchRawFeatures = (
  filter: DataFilter,
  page: number,
  pageSize: number,
  sortBy: string,
  sortDir: 'asc' | 'desc',
): Promise<RawFeaturePage> =>
  apiFetch<RawFeaturePage>(`/data/raw-features?${_dataParams(filter, { page, page_size: pageSize, sort_by: sortBy, sort_dir: sortDir })}`)

export const fetchFeatureVectors = (
  filter: DataFilter,
  page: number,
  sortBy: string,
  sortDir: 'asc' | 'desc',
): Promise<FeatureVectorPage> =>
  apiFetch<FeatureVectorPage>(`/data/feature-vectors?${_dataParams(filter, { page, page_size: 50, sort_by: sortBy, sort_dir: sortDir })}`)

export const fetchDataStats = (filter: DataFilter): Promise<ColumnStat[]> =>
  apiFetch<ColumnStat[]>(`/data/stats?${_dataParams(filter)}`)

export const fetchDataCorrelations = (filter: DataFilter): Promise<CorrelationEntry[]> =>
  apiFetch<CorrelationEntry[]>(`/data/correlations?${_dataParams(filter)}`)

export const fetchFeatureImportance = (series_ticker: string): Promise<ImportanceEntry[]> =>
  apiFetch<ImportanceEntry[]>(`/data/feature-importance?series_ticker=${encodeURIComponent(series_ticker)}`)
