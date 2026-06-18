import { useEffect, useState } from 'react'
import { api, EvalDetail, EvalSummary } from '../api'

const STATUS_COLOR: Record<string, string> = {
  running: '#facc15',
  done:    '#4ade80',
  error:   '#f87171',
}

function ScoreBar({ value, max = 1 }: { value: number; max?: number }) {
  const pct = Math.round((value / max) * 100)
  const color = value >= 0.8 ? '#4ade80' : value >= 0.5 ? '#facc15' : '#f87171'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{
        flex: 1, height: 6, background: '#374151', borderRadius: 3, overflow: 'hidden',
      }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color, transition: 'width .3s' }} />
      </div>
      <span style={{ fontSize: 12, color, minWidth: 36 }}>{value.toFixed(2)}</span>
    </div>
  )
}

function EvalRow({ ev, onDelete }: { ev: EvalSummary; onDelete: () => void }) {
  return (
    <tr style={{ borderBottom: '1px solid #374151', cursor: 'pointer' }}
        onClick={() => { window.location.hash = `/evals/${ev.eval_id}` }}>
      <td style={{ padding: '10px 8px' }}>
        <span style={{
          display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontSize: 11,
          background: STATUS_COLOR[ev.status] + '33',
          color: STATUS_COLOR[ev.status],
        }}>{ev.status}</span>
      </td>
      <td style={{ padding: '10px 8px', maxWidth: 340 }}>
        <div style={{ fontWeight: 500, fontSize: 14 }}>{ev.name}</div>
        <div style={{ fontSize: 12, color: '#9ca3af', marginTop: 2 }}>{ev.request.slice(0, 80)}</div>
      </td>
      <td style={{ padding: '10px 8px', width: 140 }}>
        {ev.overall_score != null ? <ScoreBar value={ev.overall_score} /> : '—'}
      </td>
      <td style={{ padding: '10px 8px', width: 140 }}>
        {ev.judge_score != null && ev.judge_score > 0 ? <ScoreBar value={ev.judge_score} /> : <span style={{ color: '#6b7280', fontSize: 12 }}>no judge</span>}
      </td>
      <td style={{ padding: '10px 8px', fontSize: 12 }}>
        {ev.passed == null ? '—' : ev.passed
          ? <span style={{ color: '#4ade80' }}>✓ pass</span>
          : <span style={{ color: '#f87171' }}>✗ fail</span>}
      </td>
      <td style={{ padding: '10px 8px', fontSize: 12, color: '#9ca3af' }}>
        {ev.token_count != null ? ev.token_count.toLocaleString() : '—'}
      </td>
      <td style={{ padding: '10px 8px' }}
          onClick={e => { e.stopPropagation(); if (confirm('Delete?')) onDelete() }}>
        <span style={{ cursor: 'pointer', color: '#6b7280', fontSize: 12 }}>✕</span>
      </td>
    </tr>
  )
}

function EvalDetailPanel({ evalId }: { evalId: string }) {
  const [detail, setDetail] = useState<EvalDetail | null>(null)

  useEffect(() => {
    let alive = true
    const load = () => api.getEval(evalId).then(d => { if (alive) setDetail(d) })
    load()
    const iv = setInterval(load, 2000)
    return () => { alive = false; clearInterval(iv) }
  }, [evalId])

  if (!detail) return <p style={{ color: '#9ca3af' }}>Loading…</p>
  if (detail.status === 'running') return <p style={{ color: '#facc15' }}>⏳ Running…</p>
  if (detail.status === 'error')   return <p style={{ color: '#f87171' }}>Error: {detail.error}</p>
  const rpt = detail.report
  if (!rpt) return <p style={{ color: '#9ca3af' }}>No report.</p>

  return (
    <div style={{ padding: '16px 0' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12, marginBottom: 20 }}>
        {[
          ['Structural', rpt.overall_score.toFixed(2)],
          ['Judge',      rpt.judge_score > 0 ? rpt.judge_score.toFixed(2) : 'n/a'],
          ['Tokens',     rpt.token_count.toLocaleString()],
          ['Cost',       `$${rpt.cost_usd.toFixed(4)}`],
        ].map(([label, val]) => (
          <div key={label} style={{ background: '#111827', borderRadius: 8, padding: 12, textAlign: 'center' }}>
            <div style={{ fontSize: 12, color: '#6b7280' }}>{label}</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4 }}>{val}</div>
          </div>
        ))}
      </div>

      {/* Structural scores */}
      <h3 style={{ fontSize: 14, color: '#9ca3af', marginBottom: 8 }}>Structural Scores</h3>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginBottom: 20 }}>
        <thead>
          <tr style={{ color: '#6b7280', fontSize: 12 }}>
            <th style={{ textAlign: 'left', padding: '4px 8px' }}>Agent</th>
            <th style={{ textAlign: 'left', padding: '4px 8px' }}>Score</th>
            <th style={{ textAlign: 'left', padding: '4px 8px' }}>Metrics</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(rpt.agent_scores).map(([agent, s]) => (
            <tr key={agent} style={{ borderBottom: '1px solid #1f2937' }}>
              <td style={{ padding: '6px 8px', color: '#e5e7eb' }}>{agent}</td>
              <td style={{ padding: '6px 8px', width: 120 }}><ScoreBar value={s.score} /></td>
              <td style={{ padding: '6px 8px', color: '#9ca3af', fontSize: 12 }}>
                {Object.entries(s.metrics).map(([k, v]) => `${k}=${v.toFixed(2)}`).join('  ')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* LLM judge results */}
      {Object.keys(rpt.judge_results).length > 0 && (
        <>
          <h3 style={{ fontSize: 14, color: '#9ca3af', marginBottom: 8 }}>LLM Judge</h3>
          {Object.entries(rpt.judge_results).map(([artifact, j]) => (
            <div key={artifact} style={{ background: '#111827', borderRadius: 8, padding: 12, marginBottom: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                <span style={{ fontWeight: 600, textTransform: 'capitalize' }}>{artifact}</span>
                <span style={{ color: j.score >= 0.7 ? '#4ade80' : '#f87171' }}>
                  {j.raw_score}/10
                </span>
              </div>
              <p style={{ color: '#d1d5db', fontSize: 13, margin: '0 0 8px' }}>{j.reasoning}</p>
              {j.strengths.length > 0 && (
                <div style={{ fontSize: 12, color: '#4ade80', marginBottom: 4 }}>
                  ✓ {j.strengths.join(' · ')}
                </div>
              )}
              {j.weaknesses.length > 0 && (
                <div style={{ fontSize: 12, color: '#f87171' }}>
                  ✗ {j.weaknesses.join(' · ')}
                </div>
              )}
            </div>
          ))}
        </>
      )}

      {rpt.errors.length > 0 && (
        <div style={{ background: '#450a0a', borderRadius: 8, padding: 12 }}>
          <h3 style={{ color: '#f87171', fontSize: 14, marginBottom: 6 }}>Expectations Failed</h3>
          {rpt.errors.map((e, i) => <p key={i} style={{ color: '#fca5a5', fontSize: 13, margin: '2px 0' }}>• {e}</p>)}
        </div>
      )}
    </div>
  )
}

export default function EvalList() {
  const [evals, setEvals] = useState<EvalSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [filter, setFilter] = useState('')

  const load = () => api.listEvals().then(setEvals).catch(() => {})

  useEffect(() => {
    load()
    const iv = setInterval(() => {
      if (evals.some(e => e.status === 'running')) load()
    }, 2000)
    return () => clearInterval(iv)
  }, [evals])

  const filtered = evals.filter(e =>
    !filter || e.name.toLowerCase().includes(filter.toLowerCase()) ||
    e.request.toLowerCase().includes(filter.toLowerCase())
  )

  const handleDelete = (id: string) => {
    api.deleteEval(id).then(load)
    if (selected === id) setSelected(null)
  }

  return (
    <div style={{ minHeight: '100vh', background: '#0f172a', color: '#f1f5f9', fontFamily: 'monospace' }}>
      {/* Nav */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '12px 24px', borderBottom: '1px solid #1e293b' }}>
        <a href="#/" style={{ color: '#94a3b8', textDecoration: 'none', fontSize: 13 }}>← Runs</a>
        <span style={{ color: '#6b7280' }}>|</span>
        <strong style={{ fontSize: 15 }}>Evaluations</strong>
        <div style={{ flex: 1 }} />
        <input
          placeholder="Filter…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 6, color: '#f1f5f9', padding: '4px 10px', fontSize: 13 }}
        />
        <a href="#/evals/new" style={{
          background: '#6366f1', color: '#fff', padding: '5px 14px',
          borderRadius: 6, textDecoration: 'none', fontSize: 13,
        }}>+ New Eval</a>
      </div>

      <div style={{ display: 'flex', height: 'calc(100vh - 49px)' }}>
        {/* Table */}
        <div style={{ flex: selected ? '0 0 55%' : 1, overflowY: 'auto', padding: '0 0 20px' }}>
          {filtered.length === 0 ? (
            <p style={{ padding: 24, color: '#6b7280' }}>
              No evals yet.{' '}
              <a href="#/evals/new" style={{ color: '#6366f1' }}>Run one →</a>
            </p>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ color: '#6b7280', fontSize: 12, borderBottom: '1px solid #374151' }}>
                  <th style={{ padding: '8px 8px', textAlign: 'left' }}>Status</th>
                  <th style={{ padding: '8px 8px', textAlign: 'left' }}>Name / Request</th>
                  <th style={{ padding: '8px 8px', textAlign: 'left' }}>Structural</th>
                  <th style={{ padding: '8px 8px', textAlign: 'left' }}>Judge</th>
                  <th style={{ padding: '8px 8px', textAlign: 'left' }}>Pass</th>
                  <th style={{ padding: '8px 8px', textAlign: 'left' }}>Tokens</th>
                  <th style={{ padding: '8px 8px' }} />
                </tr>
              </thead>
              <tbody>
                {filtered.map(ev => (
                  <tr key={ev.eval_id}
                      style={{ borderBottom: '1px solid #1e293b', background: selected === ev.eval_id ? '#1e293b' : 'transparent', cursor: 'pointer' }}
                      onClick={() => setSelected(selected === ev.eval_id ? null : ev.eval_id)}>
                    <td style={{ padding: '10px 8px' }}>
                      <span style={{
                        display: 'inline-block', padding: '2px 8px', borderRadius: 4, fontSize: 11,
                        background: STATUS_COLOR[ev.status] + '33', color: STATUS_COLOR[ev.status],
                      }}>{ev.status}</span>
                    </td>
                    <td style={{ padding: '10px 8px', maxWidth: 260 }}>
                      <div style={{ fontWeight: 500 }}>{ev.name}</div>
                      <div style={{ fontSize: 11, color: '#6b7280', marginTop: 2 }}>{ev.request.slice(0, 60)}</div>
                    </td>
                    <td style={{ padding: '10px 8px', width: 130 }}>
                      {ev.overall_score != null ? <ScoreBar value={ev.overall_score} /> : '—'}
                    </td>
                    <td style={{ padding: '10px 8px', width: 130 }}>
                      {ev.judge_score != null && ev.judge_score > 0 ? <ScoreBar value={ev.judge_score} /> : <span style={{ color: '#6b7280', fontSize: 11 }}>—</span>}
                    </td>
                    <td style={{ padding: '10px 8px', fontSize: 12 }}>
                      {ev.passed == null ? '—' : ev.passed
                        ? <span style={{ color: '#4ade80' }}>✓</span>
                        : <span style={{ color: '#f87171' }}>✗</span>}
                    </td>
                    <td style={{ padding: '10px 8px', fontSize: 12, color: '#9ca3af' }}>
                      {ev.token_count != null ? ev.token_count.toLocaleString() : '—'}
                    </td>
                    <td style={{ padding: '10px 8px' }}
                        onClick={e => { e.stopPropagation(); if (confirm('Delete?')) handleDelete(ev.eval_id) }}>
                      <span style={{ cursor: 'pointer', color: '#6b7280', fontSize: 12 }}>✕</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Detail panel */}
        {selected && (
          <div style={{ flex: '0 0 45%', borderLeft: '1px solid #1e293b', overflowY: 'auto', padding: '0 20px' }}>
            <button onClick={() => setSelected(null)}
                    style={{ marginTop: 12, background: 'none', border: 'none', color: '#6b7280', cursor: 'pointer', fontSize: 13 }}>
              ✕ Close
            </button>
            <EvalDetailPanel evalId={selected} />
          </div>
        )}
      </div>
    </div>
  )
}
