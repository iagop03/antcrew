import { FormEvent, useState } from 'react'
import { api, TeamName } from '../api'

const TEAMS: TeamName[] = ['dev', 'fullstack', 'research', 'content']

const TEAM_DESCRIPTIONS: Record<TeamName, string> = {
  dev:       'BA → PM → BackendDev',
  fullstack: 'BA → PM → Backend → Frontend → QA → Reviewer → DevOps → DocWriter',
  research:  'Researcher → Writer',
  content:   'Idea → Copywriter → Editor',
}

const MODELS = [
  { value: 'simulated',                label: 'Simulated (no API key)' },
  { value: 'claude-sonnet-4-6',        label: 'Claude Sonnet 4.6' },
  { value: 'claude-haiku-4-5-20251001',label: 'Claude Haiku 4.5' },
  { value: 'claude-opus-4-8',          label: 'Claude Opus 4.8' },
  { value: 'gpt-4o',                   label: 'GPT-4o' },
  { value: 'gpt-4o-mini',              label: 'GPT-4o mini' },
  { value: 'gemini-1.5-flash',         label: 'Gemini 1.5 Flash' },
  { value: 'gemini-1.5-pro',           label: 'Gemini 1.5 Pro' },
  { value: '__custom',                  label: 'Other / local model…' },
]

export default function NewRunForm() {
  const [request, setRequest] = useState('')
  const [team, setTeam] = useState<TeamName>('dev')
  const [model, setModel] = useState('simulated')
  const [customModel, setCustomModel] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const resolvedModel = model === '__custom' ? customModel.trim() : model

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!request.trim() || !resolvedModel) return
    setSubmitting(true)
    setError(null)
    try {
      const run = await api.createRun(request.trim(), team, resolvedModel)
      window.location.hash = `/runs/${run.run_id}`
    } catch (err) {
      setError(String(err))
      setSubmitting(false)
    }
  }

  return (
    <div className="page">
      <nav className="topbar">
        <a href="#/" className="logo">← AntCrew</a>
      </nav>

      <main className="container container-narrow">
        <h1 style={{ marginBottom: '1.5rem' }}>New Run</h1>

        <form className="form" onSubmit={handleSubmit}>
          <label className="label">
            Request
            <textarea
              className="textarea"
              rows={5}
              placeholder="Describe what you want to build or research…"
              value={request}
              onChange={e => setRequest(e.target.value)}
              required
              autoFocus
            />
          </label>

          <div className="form-row">
            <label className="label" style={{ flex: 1 }}>
              Team
              <select
                className="select"
                value={team}
                onChange={e => setTeam(e.target.value as TeamName)}
              >
                {TEAMS.map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
              <span className="muted" style={{ fontSize: '0.75rem', marginTop: '0.25rem' }}>
                {TEAM_DESCRIPTIONS[team]}
              </span>
            </label>

            <label className="label" style={{ flex: 1 }}>
              Model
              <select
                className="select"
                value={model}
                onChange={e => setModel(e.target.value)}
              >
                {MODELS.map(m => (
                  <option key={m.value} value={m.value}>{m.label}</option>
                ))}
              </select>
            </label>
          </div>

          {model === '__custom' && (
            <label className="label">
              Model ID
              <input
                className="input"
                placeholder="e.g. ollama/llama3, lm-studio/local"
                value={customModel}
                onChange={e => setCustomModel(e.target.value)}
                required
              />
            </label>
          )}

          {error && <p className="error-text">{error}</p>}

          <button
            type="submit"
            className="btn btn-primary"
            disabled={submitting || !request.trim() || (model === '__custom' && !customModel.trim())}
            style={{ alignSelf: 'flex-start', marginTop: '0.5rem' }}
          >
            {submitting ? 'Starting…' : 'Start Run →'}
          </button>
        </form>
      </main>
    </div>
  )
}
