import { useState } from 'react'
import { api, TeamName } from '../api'

const TEAMS: TeamName[] = ['dev', 'fullstack', 'research', 'content']

const MODELS = [
  { value: 'simulated',                    label: 'Simulated (no API)' },
  { value: 'claude-sonnet-4-6',            label: 'Claude Sonnet 4.6' },
  { value: 'claude-opus-4-8',              label: 'Claude Opus 4.8' },
  { value: 'claude-haiku-4-5-20251001',    label: 'Claude Haiku 4.5' },
  { value: 'gpt-4o-mini',                  label: 'GPT-4o mini' },
  { value: 'gpt-4o',                       label: 'GPT-4o' },
  { value: 'gemini-2.0-flash',             label: 'Gemini 2.0 Flash' },
]

export default function NewEvalForm() {
  const [request,      setRequest]      = useState('')
  const [name,         setName]         = useState('')
  const [team,         setTeam]         = useState<TeamName>('dev')
  const [model,        setModel]        = useState('simulated')
  const [judgeModel,   setJudgeModel]   = useState('')
  const [minTickets,   setMinTickets]   = useState(0)
  const [minFiles,     setMinFiles]     = useState(0)
  const [verdict,      setVerdict]      = useState('')
  const [loading,      setLoading]      = useState(false)
  const [error,        setError]        = useState('')

  const submit = async () => {
    if (!request.trim()) { setError('Request is required.'); return }
    setLoading(true); setError('')
    try {
      await api.createEval({
        request: request.trim(),
        name:    name.trim() || undefined,
        team,
        model,
        judge_model:             judgeModel || undefined,
        expect_min_tickets:      minTickets  || undefined,
        expect_min_code_files:   minFiles    || undefined,
        expect_review_verdict:   verdict     || undefined,
      })
      window.location.hash = '/evals'
    } catch (e: any) {
      setError(e.message || 'Failed to start eval.')
      setLoading(false)
    }
  }

  const field = (label: string, node: React.ReactNode) => (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 12, color: '#9ca3af' }}>{label}</span>
      {node}
    </label>
  )

  const input = (value: string | number, onChange: (v: any) => void, type = 'text') => (
    <input
      type={type}
      value={value}
      onChange={e => onChange(type === 'number' ? Number(e.target.value) : e.target.value)}
      style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 6, color: '#f1f5f9', padding: '7px 10px', fontSize: 13 }}
    />
  )

  const select = (value: string, onChange: (v: string) => void, options: {value: string; label: string}[]) => (
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 6, color: '#f1f5f9', padding: '7px 10px', fontSize: 13 }}>
      {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  )

  return (
    <div style={{ minHeight: '100vh', background: '#0f172a', color: '#f1f5f9', fontFamily: 'monospace' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '12px 24px', borderBottom: '1px solid #1e293b' }}>
        <a href="#/evals" style={{ color: '#94a3b8', textDecoration: 'none', fontSize: 13 }}>← Evaluations</a>
        <strong style={{ fontSize: 15 }}>New Evaluation</strong>
      </div>

      <div style={{ maxWidth: 600, margin: '32px auto', padding: '0 24px', display: 'flex', flexDirection: 'column', gap: 18 }}>
        {field('Request *',
          <textarea
            value={request}
            onChange={e => setRequest(e.target.value)}
            rows={3}
            placeholder="Build a login module with JWT authentication"
            style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 6, color: '#f1f5f9', padding: '8px 10px', fontSize: 13, resize: 'vertical' }}
          />
        )}

        {field('Name (optional)', input(name, setName))}

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
          {field('Team', select(team, v => setTeam(v as TeamName), TEAMS.map(t => ({ value: t, label: t }))))}
          {field('Pipeline model', select(model, setModel, MODELS))}
        </div>

        <div style={{ background: '#111827', borderRadius: 8, padding: 14 }}>
          <div style={{ fontSize: 12, color: '#6366f1', marginBottom: 10, fontWeight: 600 }}>
            LLM-as-Judge (optional)
          </div>
          {field('Judge model', select(judgeModel, setJudgeModel, [
            { value: '',                         label: 'None (structural only)' },
            { value: 'simulated',                label: 'Simulated (no API)' },
            { value: 'claude-sonnet-4-6',        label: 'Claude Sonnet 4.6' },
            { value: 'claude-haiku-4-5-20251001',label: 'Claude Haiku 4.5' },
            { value: 'gpt-4o-mini',              label: 'GPT-4o mini' },
          ]))}
        </div>

        <div style={{ background: '#111827', borderRadius: 8, padding: 14 }}>
          <div style={{ fontSize: 12, color: '#9ca3af', marginBottom: 10, fontWeight: 600 }}>
            Expectations (fail if not met)
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12 }}>
            {field('Min tickets', input(minTickets, setMinTickets, 'number'))}
            {field('Min code files', input(minFiles, setMinFiles, 'number'))}
            {field('Review verdict', select(verdict, setVerdict, [
              { value: '',                label: 'Any' },
              { value: 'approve',         label: 'approve' },
              { value: 'request_changes', label: 'request_changes' },
              { value: 'reject',          label: 'reject' },
            ]))}
          </div>
        </div>

        {error && <p style={{ color: '#f87171', fontSize: 13 }}>{error}</p>}

        <div style={{ display: 'flex', gap: 10 }}>
          <button
            onClick={submit}
            disabled={loading}
            style={{
              flex: 1, padding: '10px 0', background: loading ? '#4338ca' : '#6366f1',
              border: 'none', borderRadius: 8, color: '#fff', fontSize: 14, cursor: 'pointer',
            }}>
            {loading ? '⏳ Running…' : '▶ Run Evaluation'}
          </button>
          <a href="#/evals" style={{
            padding: '10px 16px', background: '#1e293b', borderRadius: 8,
            color: '#9ca3af', textDecoration: 'none', fontSize: 14,
          }}>Cancel</a>
        </div>
      </div>
    </div>
  )
}
