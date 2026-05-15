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
  brier_score?: number
  up_accuracy?: number
  up_count?: number
  down_accuracy?: number
  down_count?: number
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

export const fetchSeriesHistory = (series_ticker: string, limit = 5000): Promise<HistoryEntry[]> =>
  apiFetch<HistoryEntry[]>(`/history/series/${encodeURIComponent(series_ticker)}?limit=${limit}`)

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

// ── Sweep Analysis ────────────────────────────────────────────────────────────

export interface SweepRun {
  id: number
  run_at: string
  status: string
  n_predictions: number | null
  elapsed_seconds: number | null
  best_net_pnl_dollars: number | null
}

export interface SweepResultRow {
  result_type: 'top_k' | 'marginal'
  rank: number | null
  knob_name: string | null
  knob_value: string | null
  min_fee_adjusted_edge_bps: number | null
  max_spread_bps: number | null
  min_confidence: number | null
  min_contract_price_dollars: number | null
  crypto_live_min_market_age_seconds: number | null
  crypto_autonomy_min_seconds_to_close: number | null
  crypto_taker_fallback_close_seconds: number | null
  crypto_market_price_anchor_weight: number | null
  crypto_late_sure_thing_min_probability: number | null
  crypto_late_sure_thing_min_market_probability: number | null
  n_trades: number | null
  win_rate: number | null
  net_pnl_dollars: number | null
  ev_per_contract: number | null
  starvation_rate: number | null
}

export const fetchSweepRuns = (): Promise<SweepRun[]> =>
  apiFetch<SweepRun[]>('/analysis/runs')

export const fetchLatestSweepRun = (): Promise<SweepRun> =>
  apiFetch<SweepRun>('/analysis/runs/latest')

export const fetchSweepResults = (runId: number, type: 'top_k' | 'marginal'): Promise<SweepResultRow[]> =>
  apiFetch<SweepResultRow[]>(`/analysis/runs/${runId}/results?type=${type}`)

export const triggerSweepRun = (): Promise<SweepRun> =>
  fetch('/api/analysis/runs', { method: 'POST' }).then(res => {
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    return res.json() as Promise<SweepRun>
  })
