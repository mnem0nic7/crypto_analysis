import { useEffect, useMemo, useState } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  LineChart, Line, CartesianGrid,
} from 'recharts'
import {
  fetchSweepRuns, fetchSweepResults, triggerSweepRun,
} from '../api'
import type { SweepRun, SweepResultRow } from '../api'
import styles from './Analysis.module.css'

const tooltipStyle = { background: '#1a1d27', border: '1px solid #2a2d3a' }

const KNOB_COLS: Array<{ key: keyof SweepResultRow; label: string }> = [
  { key: 'min_fee_adjusted_edge_bps', label: 'Edge bps' },
  { key: 'max_spread_bps', label: 'Spread bps' },
  { key: 'min_confidence', label: 'Conf' },
  { key: 'min_contract_price_dollars', label: 'Min $' },
  { key: 'crypto_live_min_market_age_seconds', label: 'Age s' },
  { key: 'crypto_autonomy_min_seconds_to_close', label: 'Auto s' },
  { key: 'crypto_taker_fallback_close_seconds', label: 'Fallbk s' },
  { key: 'crypto_market_price_anchor_weight', label: 'Anchor' },
  { key: 'crypto_late_sure_thing_min_probability', label: 'LST prob' },
  { key: 'crypto_late_sure_thing_min_market_probability', label: 'LST mkt' },
]

const KNOB_LABEL_MAP: Record<string, string> = Object.fromEntries(
  KNOB_COLS.map(c => [String(c.key), c.label])
)

function fmt(v: number | null, decimals = 2): string {
  if (v == null) return '—'
  return v.toFixed(decimals)
}

function fmtPct(v: number | null): string {
  if (v == null) return '—'
  return `${(v * 100).toFixed(1)}%`
}

function buildSettingsText(row: SweepResultRow): string {
  return KNOB_COLS
    .filter(c => row[c.key] != null)
    .map(c => `${c.key}=${row[c.key]}`)
    .join('\n')
}

export default function Analysis() {
  const [runs, setRuns] = useState<SweepRun[]>([])
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [topK, setTopK] = useState<SweepResultRow[]>([])
  const [marginals, setMarginals] = useState<SweepResultRow[]>([])
  const [selectedKnob, setSelectedKnob] = useState<string | null>(null)
  const [selectedResult, setSelectedResult] = useState<SweepResultRow | null>(null)
  const [isRunning, setIsRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadRuns = async () => {
    try {
      const data = await fetchSweepRuns()
      const sorted = [...data].sort((a, b) => b.id - a.id)
      setRuns(sorted)
      setError(null)
      if (sorted.length > 0 && selectedRunId == null) {
        setSelectedRunId(sorted[0].id)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => { loadRuns() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (selectedRunId == null) return
    let cancelled = false
    setTopK([])
    setMarginals([])
    setSelectedKnob(null)
    setSelectedResult(null)
    Promise.all([
      fetchSweepResults(selectedRunId, 'top_k'),
      fetchSweepResults(selectedRunId, 'marginal'),
    ]).then(([tk, mg]) => {
      if (!cancelled) { setTopK(tk); setMarginals(mg) }
    }).catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [selectedRunId])

  const handleRunSweep = async () => {
    setIsRunning(true)
    setError(null)
    try {
      const newRun = await triggerSweepRun()
      const data = await fetchSweepRuns()
      const sorted = [...data].sort((a, b) => b.id - a.id)
      setRuns(sorted)
      setSelectedRunId(newRun.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setIsRunning(false)
    }
  }

  const importanceData = useMemo(() => {
    const byKnob: Record<string, number[]> = {}
    for (const m of marginals) {
      if (!m.knob_name || m.net_pnl_dollars == null) continue
      if (!byKnob[m.knob_name]) byKnob[m.knob_name] = []
      byKnob[m.knob_name].push(m.net_pnl_dollars)
    }
    return Object.entries(byKnob).map(([knob, vals]) => ({
      knob: KNOB_LABEL_MAP[knob] ?? knob.replace(/^crypto_/, '').replace(/_/g, ' '),
      fullName: knob,
      range: Math.max(...vals) - Math.min(...vals),
    })).sort((a, b) => b.range - a.range)
  }, [marginals])

  const knobDetailData = useMemo(() => {
    if (!selectedKnob) return []
    const filtered = marginals.filter(m => m.knob_name === selectedKnob && m.knob_value != null && m.net_pnl_dollars != null)
    // Group by knob_value and average
    const byVal: Record<string, number[]> = {}
    for (const m of filtered) {
      const k = m.knob_value!
      if (!byVal[k]) byVal[k] = []
      byVal[k].push(m.net_pnl_dollars!)
    }
    return Object.entries(byVal).map(([val, pnls]) => ({
      value: val,
      avgPnl: pnls.reduce((s, v) => s + v, 0) / pnls.length,
    })).sort((a, b) => parseFloat(a.value) - parseFloat(b.value))
  }, [marginals, selectedKnob])

  const topRows = useMemo(
    () => [...topK].sort((a, b) => (b.net_pnl_dollars ?? -Infinity) - (a.net_pnl_dollars ?? -Infinity)).slice(0, 25),
    [topK],
  )

  return (
    <div>
      {error && (
        <div className={styles.errorBanner}>
          {error}
          <button className={styles.errorDismiss} onClick={() => setError(null)}>✕</button>
        </div>
      )}

      {/* 1. Run history strip */}
      <div className={styles.runHeader}>
        <div className={styles.sectionTitle} style={{ marginTop: 0, marginBottom: 0 }}>Sweep Runs</div>
        <button className={styles.runBtn} onClick={handleRunSweep} disabled={isRunning}>
          {isRunning ? 'Running…' : 'Run sweep now'}
        </button>
      </div>

      {runs.length === 0 ? (
        <div className="placeholder">No sweep runs yet</div>
      ) : (
        <table className={styles.runTable}>
          <thead>
            <tr>
              <th>ID</th><th>Date</th><th>Status</th><th>N Predictions</th><th>Elapsed</th><th>Best P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {runs.map(r => (
              <tr
                key={r.id}
                className={[
                  r.id === selectedRunId ? styles.runRowActive : '',
                  r.status === 'running' ? styles.runRowRunning : '',
                ].filter(Boolean).join(' ')}
                onClick={() => r.status !== 'running' && setSelectedRunId(r.id)}
                style={{ cursor: r.status === 'running' ? 'default' : 'pointer' }}
              >
                <td>{r.id}</td>
                <td>{new Date(r.run_at).toLocaleString()}</td>
                <td>
                  <span className={
                    r.status === 'running' ? styles.statusRunning :
                    r.status === 'complete' ? styles.statusComplete :
                    styles.statusFailed
                  }>{r.status}</span>
                </td>
                <td>{r.n_predictions?.toLocaleString() ?? '—'}</td>
                <td>{r.elapsed_seconds != null ? `${r.elapsed_seconds.toFixed(1)}s` : '—'}</td>
                <td>{r.best_net_pnl_dollars != null ? `$${r.best_net_pnl_dollars.toFixed(2)}` : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* 2. Knob importance chart */}
      {importanceData.length > 0 && (
        <>
          <div className={styles.sectionTitle}>Knob Importance (P&amp;L range by knob)</div>
          <div className={styles.chartCard}>
            <ResponsiveContainer width="100%" height={Math.max(220, importanceData.length * 36)}>
              <BarChart data={importanceData} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 110 }}>
                <XAxis type="number" tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={v => `$${v.toFixed(0)}`} />
                <YAxis type="category" dataKey="knob" width={110} tick={{ fill: '#94a3b8', fontSize: 12 }} />
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(v: unknown) => [`$${(v as number).toFixed(2)}`, 'P&L range']}
                />
                <Bar
                  dataKey="range"
                  fill="#7c3aed"
                  radius={[0, 3, 3, 0]}
                  onClick={(d: { fullName: string }) => setSelectedKnob(d.fullName)}
                  style={{ cursor: 'pointer' }}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </>
      )}

      {/* 3. Per-knob detail panel */}
      {selectedKnob && knobDetailData.length > 0 && (
        <div className={styles.chartCard}>
          <div className={styles.knobDetailHeader}>
            <span style={{ fontSize: 12, color: 'var(--text)' }}>
              {KNOB_LABEL_MAP[selectedKnob] ?? selectedKnob}
              <span style={{ marginLeft: 6, fontSize: 10, color: 'var(--text-muted)' }}>{selectedKnob}</span>
            </span>
            <button className={styles.closeBtn} onClick={() => setSelectedKnob(null)}>Close</button>
          </div>
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={knobDetailData} margin={{ top: 4, right: 16, bottom: 4, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2d3a" />
              <XAxis dataKey="value" tick={{ fill: '#94a3b8', fontSize: 11 }} />
              <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={v => `$${(v as number).toFixed(0)}`} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(v: unknown) => [`$${(v as number).toFixed(2)}`, 'Avg P&L']}
              />
              <Line type="monotone" dataKey="avgPnl" stroke="#7c3aed" dot={{ r: 3, fill: '#7c3aed' }} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* 4. Top-500 results table (first 25) */}
      {topRows.length > 0 && (
        <>
          <div className={styles.sectionTitle}>Top Results (top 25 by Net P&amp;L)</div>
          <div style={{ overflowX: 'auto' }}>
            <table className={styles.resultsTable}>
              <thead>
                <tr>
                  <th>Rank</th>
                  <th>Net P&amp;L</th>
                  <th>Win Rate</th>
                  <th>EV/Contract</th>
                  <th>N Trades</th>
                  <th>Starv.</th>
                  {KNOB_COLS.map(c => <th key={c.key}>{c.label}</th>)}
                </tr>
              </thead>
              <tbody>
                {topRows.map((row, i) => (
                  <tr
                    key={i}
                    className={[
                      row === selectedResult ? styles.resultsRowActive : '',
                      i % 2 === 1 ? styles.resultsRowAlt : '',
                    ].filter(Boolean).join(' ')}
                    onClick={() => setSelectedResult(row === selectedResult ? null : row)}
                    style={{ cursor: 'pointer' }}
                  >
                    <td>{row.rank ?? i + 1}</td>
                    <td>{row.net_pnl_dollars != null ? `$${row.net_pnl_dollars.toFixed(2)}` : '—'}</td>
                    <td>{fmtPct(row.win_rate)}</td>
                    <td>{fmt(row.ev_per_contract)}</td>
                    <td>{row.n_trades ?? '—'}</td>
                    <td>{fmtPct(row.starvation_rate)}</td>
                    {KNOB_COLS.map(c => <td key={c.key}>{fmt(row[c.key] as number | null)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {selectedResult && (
            <div className={styles.settingsCard}>
              <div className={styles.settingsTitle}>Best Settings (rank {selectedResult.rank ?? '—'})</div>
              <pre className={styles.settingsPre}>{buildSettingsText(selectedResult)}</pre>
            </div>
          )}
        </>
      )}
    </div>
  )
}
