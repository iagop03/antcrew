export type TeamName = 'dev' | 'fullstack' | 'research' | 'content'
export type RunStatus = 'running' | 'done' | 'error'
export type EvalStatus = 'running' | 'done' | 'error'

export interface RunSummary {
  run_id: string
  status: RunStatus
  request: string
  team: TeamName
  error?: string | null
}

export interface AgentUsage {
  agent: string
  model: string
  input_tokens: number
  output_tokens: number
  cost_usd: number
}

export interface UsageSummary {
  total_input_tokens: number
  total_output_tokens: number
  total_cost_usd: number
  by_agent: AgentUsage[]
}

export interface RunDetail extends RunSummary {
  state?: Record<string, unknown>
  usage?: UsageSummary
}

export interface JudgeResult {
  artifact: string
  score: number
  raw_score: number
  reasoning: string
  strengths: string[]
  weaknesses: string[]
}

export interface AgentScore {
  score: number
  metrics: Record<string, number>
  details: Record<string, unknown>
}

export interface EvalReport {
  case: { name: string; request: string; tags: string[] }
  passed: boolean
  overall_score: number
  judge_score: number
  elapsed_ms: number
  token_count: number
  cost_usd: number
  agent_scores: Record<string, AgentScore>
  judge_results: Record<string, JudgeResult>
  errors: string[]
}

export interface EvalSummary {
  eval_id: string
  status: EvalStatus
  name: string
  request: string
  overall_score?: number
  judge_score?: number
  passed?: boolean
  token_count?: number
  error?: string | null
}

export interface EvalDetail extends EvalSummary {
  report?: EvalReport
}

// Base URL — empty string means same origin (works in production and dev proxy)
const API = ''

async function _fetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${url}`, init)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<T>
}

export const api = {
  // ── Runs ──────────────────────────────────────────────────────────────────
  listRuns: () => _fetch<RunSummary[]>('/runs'),

  getRun: (id: string) => _fetch<RunDetail>(`/run/${id}`),

  createRun: (request: string, team: TeamName, model: string) =>
    _fetch<RunSummary>('/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request, team, model }),
    }),

  deleteRun: (id: string) =>
    fetch(`${API}/run/${id}`, { method: 'DELETE' }),

  openStream: (id: string) =>
    new EventSource(`${API}/run/${id}/stream`),

  // ── Evals ─────────────────────────────────────────────────────────────────
  listEvals: () => _fetch<EvalSummary[]>('/evals'),

  getEval: (id: string) => _fetch<EvalDetail>(`/eval/${id}`),

  createEval: (params: {
    request: string
    name?: string
    team?: TeamName
    model?: string
    judge_model?: string
    expect_min_tickets?: number
    expect_min_code_files?: number
    expect_review_verdict?: string
  }) =>
    _fetch<EvalSummary>('/eval', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    }),

  deleteEval: (id: string) =>
    fetch(`${API}/eval/${id}`, { method: 'DELETE' }),
}
