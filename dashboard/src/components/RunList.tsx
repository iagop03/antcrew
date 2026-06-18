import { useEffect, useState } from 'react'
import { api, RunSummary } from '../api'

const STATUS_META: Record<string, { cls: string; label: string }> = {
  running: { cls: 'badge badge-running', label: '● Running' },
  done:    { cls: 'badge badge-done',    label: '✓ Done'    },
  error:   { cls: 'badge badge-error',   label: '✗ Error'   },
}

export default function RunList() {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(true)

  const refresh = async () => {
    try {
      const data = await api.listRuns()
      setRuns([...data].reverse()) // newest first
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  // Auto-refresh while any run is still running
  useEffect(() => {
    if (!runs.some(r => r.status === 'running')) return
    const id = setInterval(refresh, 2000)
    return () => clearInterval(id)
  }, [runs])

  const filtered = runs.filter(r =>
    r.request.toLowerCase().includes(filter.toLowerCase()) ||
    r.run_id.includes(filter) ||
    r.team.includes(filter),
  )

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (!confirm('Delete this run?')) return
    await api.deleteRun(id)
    setRuns(prev => prev.filter(r => r.run_id !== id))
  }

  return (
    <div className="page">
      <nav className="topbar">
        <span className="logo">🐜 AntCrew</span>
        <a href="#/evals" style={{ marginRight: 8, color: '#94a3b8', textDecoration: 'none', fontSize: 13 }}>Evaluations</a>
        <a href="#/new" className="btn btn-primary">+ New Run</a>
      </nav>

      <main className="container">
        <div className="toolbar">
          <input
            className="search"
            placeholder="Filter by request, team, or run ID…"
            value={filter}
            onChange={e => setFilter(e.target.value)}
          />
          <button className="btn btn-ghost" onClick={refresh}>↻</button>
        </div>

        {loading ? (
          <p className="muted center">Loading…</p>
        ) : filtered.length === 0 ? (
          <div className="empty">
            <p className="muted">No runs yet.</p>
            <a href="#/new" className="btn btn-primary">Start your first run</a>
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Status</th>
                <th>Request</th>
                <th>Team</th>
                <th>Run ID</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(run => {
                const meta = STATUS_META[run.status] ?? { cls: 'badge', label: run.status }
                return (
                  <tr key={run.run_id}>
                    <td><span className={meta.cls}>{meta.label}</span></td>
                    <td>
                      <a href={`#/runs/${run.run_id}`} className="link">
                        {run.request.length > 90
                          ? run.request.slice(0, 90) + '…'
                          : run.request}
                      </a>
                      {run.error && <p className="error-text">{run.error}</p>}
                    </td>
                    <td><span className="chip">{run.team}</span></td>
                    <td className="mono muted">{run.run_id.slice(0, 8)}</td>
                    <td>
                      <button
                        className="btn btn-ghost btn-sm"
                        title="Delete run"
                        onClick={e => handleDelete(run.run_id, e)}
                      >
                        ✕
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </main>
    </div>
  )
}
