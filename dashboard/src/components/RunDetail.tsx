import { useEffect, useRef, useState } from 'react'
import { api, RunDetail as RunDetailType, UsageSummary } from '../api'

// Per-agent token colors
const AGENT_COLORS: Record<string, string> = {
  business_analyst: '#818cf8',
  pm:               '#38bdf8',
  backend_dev:      '#34d399',
  frontend_dev:     '#fbbf24',
  qa:               '#f472b6',
  reviewer:         '#a78bfa',
  devops:           '#fb923c',
  doc_writer:       '#2dd4bf',
  researcher:       '#60a5fa',
  idea:             '#c084fc',
  copywriter:       '#4ade80',
  editor:           '#f87171',
}

function agentColor(name: string): string {
  return AGENT_COLORS[name] ?? '#94a3b8'
}

interface StreamSection {
  agent: string
  text: string
  key: number
}

export default function RunDetail({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunDetailType | null>(null)
  const [agents, setAgents] = useState<string[]>([])
  const [activeAgent, setActiveAgent] = useState<string | null>(null)
  const [sections, setSections] = useState<StreamSection[]>([])
  const [finalState, setFinalState] = useState<Record<string, unknown> | null>(null)
  const [usage, setUsage] = useState<UsageSummary | null>(null)
  const [status, setStatus] = useState<'loading' | 'running' | 'done' | 'error'>('loading')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const streamRef = useRef<HTMLDivElement>(null)
  const sectionKey = useRef(0)

  // Auto-scroll stream box on new content
  useEffect(() => {
    if (streamRef.current) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight
    }
  }, [sections])

  useEffect(() => {
    let es: EventSource | null = null

    async function init() {
      try {
        const data = await api.getRun(runId)
        setRun(data)

        if (data.status === 'done') {
          setFinalState(data.state ?? null)
          setUsage(data.usage ?? null)
          setStatus('done')
          return
        }
        if (data.status === 'error') {
          setErrorMsg(data.error ?? 'Unknown error')
          setStatus('error')
          return
        }

        // Run is in progress — open SSE stream
        setStatus('running')
        es = api.openStream(runId)

        es.onmessage = (e) => {
          const { agent, token } = JSON.parse(e.data) as { agent: string; token: string }
          setActiveAgent(agent)
          setAgents(prev => (prev.includes(agent) ? prev : [...prev, agent]))

          setSections(prev => {
            // Append token to the last section if same agent, else start a new section
            if (prev.length > 0 && prev[prev.length - 1].agent === agent) {
              const next = [...prev]
              next[next.length - 1] = {
                ...next[next.length - 1],
                text: next[next.length - 1].text + token,
              }
              return next
            }
            sectionKey.current += 1
            return [...prev, { agent, text: token, key: sectionKey.current }]
          })
        }

        es.addEventListener('done', (e: Event) => {
          const { state, usage: u } = JSON.parse((e as MessageEvent).data)
          setFinalState(state)
          setUsage(u)
          setStatus('done')
          setActiveAgent(null)
          es?.close()
        })

        es.addEventListener('error', (e: Event) => {
          if (e instanceof MessageEvent) setErrorMsg(e.data)
          setStatus('error')
          es?.close()
        })

        es.onerror = () => {
          // Connection dropped — check actual run status via REST
          api.getRun(runId)
            .then(d => {
              if (d.status === 'done') {
                setFinalState(d.state ?? null)
                setUsage(d.usage ?? null)
                setStatus('done')
              } else {
                setErrorMsg(d.error ?? 'Connection lost')
                setStatus('error')
              }
            })
            .catch(() => {
              setErrorMsg('Connection lost')
              setStatus('error')
            })
          es?.close()
        }
      } catch (err) {
        setErrorMsg(String(err))
        setStatus('error')
      }
    }

    init()
    return () => es?.close()
  }, [runId])

  const activeIdx = activeAgent ? agents.indexOf(activeAgent) : -1

  return (
    <div className="page">
      <nav className="topbar">
        <a href="#/" className="logo">← AntCrew</a>
        <StatusBadge status={status} />
      </nav>

      <main className="container">
        {run && (
          <div className="run-header">
            <p className="muted">
              <span className="chip">{run.team}</span>
              {' '}
              <span className="mono">{runId.slice(0, 8)}</span>
            </p>
            <h1>{run.request}</h1>
          </div>
        )}

        {/* Pipeline progress */}
        {agents.length > 0 && (
          <section className="card">
            <h2>Pipeline</h2>
            <div className="pipeline">
              {agents.map((agent, i) => {
                const isActive = activeAgent === agent
                const isDone   = status === 'done' || i < activeIdx
                return (
                  <span key={agent} style={{ display: 'inline-flex', alignItems: 'center' }}>
                    <span
                      className={`agent-chip${isActive ? ' active' : isDone ? ' completed' : ''}`}
                      style={{ '--color': agentColor(agent) } as React.CSSProperties}
                    >
                      {agent.replace(/_/g, ' ')}
                    </span>
                    {i < agents.length - 1 && <span className="arrow">→</span>}
                  </span>
                )
              })}
            </div>
          </section>
        )}

        {/* Live token stream */}
        {sections.length > 0 && (
          <section className="card">
            <h2>
              Stream
              {status === 'running' && <span className="pulse-dot"> ●</span>}
            </h2>
            <div className="stream-box" ref={streamRef}>
              {sections.map(sec => (
                <span key={sec.key} style={{ color: agentColor(sec.agent) }}>
                  <span className="agent-label">[{sec.agent.replace(/_/g, '_')}]</span>
                  {' '}
                  {sec.text}
                </span>
              ))}
            </div>
          </section>
        )}

        {/* Error message */}
        {errorMsg && (
          <div className="alert alert-error">
            <strong>Error:</strong> {errorMsg}
          </div>
        )}

        {/* Artifacts — shown when done */}
        {finalState && <ArtifactViewer state={finalState} />}

        {/* Token usage table */}
        {usage && usage.by_agent.length > 0 && (
          <section className="card">
            <h2>Token Usage</h2>
            <table className="table">
              <thead>
                <tr>
                  <th>Agent</th>
                  <th>Model</th>
                  <th style={{ textAlign: 'right' }}>Input</th>
                  <th style={{ textAlign: 'right' }}>Output</th>
                  <th style={{ textAlign: 'right' }}>Cost (USD)</th>
                </tr>
              </thead>
              <tbody>
                {usage.by_agent.map(a => (
                  <tr key={a.agent + a.model}>
                    <td style={{ color: agentColor(a.agent) }}>{a.agent}</td>
                    <td className="mono muted">{a.model}</td>
                    <td className="mono" style={{ textAlign: 'right' }}>
                      {a.input_tokens.toLocaleString()}
                    </td>
                    <td className="mono" style={{ textAlign: 'right' }}>
                      {a.output_tokens.toLocaleString()}
                    </td>
                    <td className="mono" style={{ textAlign: 'right' }}>
                      ${a.cost_usd.toFixed(4)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td colSpan={2}><strong>Total</strong></td>
                  <td className="mono" style={{ textAlign: 'right' }}>
                    <strong>{usage.total_input_tokens.toLocaleString()}</strong>
                  </td>
                  <td className="mono" style={{ textAlign: 'right' }}>
                    <strong>{usage.total_output_tokens.toLocaleString()}</strong>
                  </td>
                  <td className="mono" style={{ textAlign: 'right' }}>
                    <strong>${usage.total_cost_usd.toFixed(4)}</strong>
                  </td>
                </tr>
              </tfoot>
            </table>
          </section>
        )}
      </main>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { cls: string; label: string }> = {
    loading: { cls: 'badge badge-muted',   label: 'Loading…' },
    running: { cls: 'badge badge-running', label: '● Running' },
    done:    { cls: 'badge badge-done',    label: '✓ Done'    },
    error:   { cls: 'badge badge-error',   label: '✗ Error'   },
  }
  const { cls, label } = map[status] ?? { cls: 'badge', label: status }
  return <span className={cls}>{label}</span>
}

function Accordion({
  title,
  children,
  defaultOpen = false,
}: {
  title: string
  children: React.ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className="card">
      <button className="accordion-header" onClick={() => setOpen(o => !o)}>
        <h2 className="accordion-title">{title}</h2>
        <span className="muted">{open ? '▲' : '▼'}</span>
      </button>
      {open && <div className="accordion-body">{children}</div>}
    </section>
  )
}

function CodeBlock({
  title,
  lang,
  code,
}: {
  title: string
  lang: string
  code: string
}) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <div className="code-artifact">
      <div className="code-header">
        <span className="mono">{title}</span>
        <span className="chip">{lang}</span>
        <button className="btn btn-ghost btn-sm" onClick={copy} style={{ marginLeft: 'auto' }}>
          {copied ? '✓ Copied' : 'Copy'}
        </button>
      </div>
      <pre className="code-block"><code>{code}</code></pre>
    </div>
  )
}

function ArtifactViewer({ state }: { state: Record<string, unknown> }) {
  type Rec = Record<string, unknown>

  const prd             = state.prd             as Rec | null
  const tickets         = (state.tickets         as Rec[]) ?? []
  const codeArtifacts   = (state.code_artifacts  as Rec[]) ?? []
  const testArtifacts   = (state.test_artifacts  as Rec[]) ?? []
  const review          = state.review           as Rec | null
  const devopsArtifacts = (state.devops_artifacts as Rec[]) ?? []
  const docArtifacts    = (state.doc_artifacts   as Rec[]) ?? []
  const research        = state.research_document as Rec | null
  const content         = state.content_piece    as Rec | null

  return (
    <>
      {prd && (
        <Accordion title={`PRD: ${prd.title as string}`} defaultOpen>
          <p style={{ marginBottom: '0.75rem' }}>{prd.summary as string}</p>
          {((prd.functional_requirements as string[]) ?? []).length > 0 && (
            <ul className="list">
              {(prd.functional_requirements as string[]).map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
          {((prd.out_of_scope as string[]) ?? []).length > 0 && (
            <>
              <p className="muted" style={{ marginTop: '0.75rem', marginBottom: '0.25rem' }}>
                Out of scope:
              </p>
              <ul className="list muted">
                {(prd.out_of_scope as string[]).map((s, i) => <li key={i}>{s}</li>)}
              </ul>
            </>
          )}
        </Accordion>
      )}

      {tickets.length > 0 && (
        <Accordion title={`Tickets (${tickets.length})`}>
          {tickets.map((t, i) => (
            <div key={i} className="ticket">
              <div className="ticket-header">
                <strong>{t.title as string}</strong>
                <span className={`badge priority-${t.priority as string}`}>
                  {t.priority as string}
                </span>
              </div>
              <p className="muted">{t.description as string}</p>
              {((t.acceptance_criteria as string[]) ?? []).length > 0 && (
                <ul className="list muted" style={{ marginTop: '0.5rem', fontSize: '0.8rem' }}>
                  {(t.acceptance_criteria as string[]).map((c, j) => <li key={j}>{c}</li>)}
                </ul>
              )}
            </div>
          ))}
        </Accordion>
      )}

      {codeArtifacts.length > 0 && (
        <Accordion title={`Code (${codeArtifacts.length} files)`}>
          {codeArtifacts.map((a, i) => (
            <CodeBlock
              key={i}
              title={a.file_path as string}
              lang={a.language as string}
              code={a.content as string}
            />
          ))}
        </Accordion>
      )}

      {testArtifacts.length > 0 && (
        <Accordion title={`Tests (${testArtifacts.length} files)`}>
          {testArtifacts.map((a, i) => (
            <CodeBlock
              key={i}
              title={a.file_path as string}
              lang={a.language as string}
              code={a.content as string}
            />
          ))}
        </Accordion>
      )}

      {review && (
        <Accordion title={`Code Review: ${review.verdict as string}`}>
          <p style={{ marginBottom: '0.75rem' }}>{review.summary as string}</p>
          {((review.findings as Rec[]) ?? []).map((f, i) => (
            <div key={i} className={`finding finding-${f.severity as string}`}>
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.25rem' }}>
                <span className={`badge priority-${f.severity as string}`}>{f.severity as string}</span>
                <span className="mono muted">
                  {f.file_path as string}{f.line != null ? `:${f.line}` : ''}
                </span>
              </div>
              <p>{f.message as string}</p>
              {f.suggestion && (
                <p className="muted" style={{ marginTop: '0.25rem' }}>
                  💡 {f.suggestion as string}
                </p>
              )}
            </div>
          ))}
        </Accordion>
      )}

      {devopsArtifacts.length > 0 && (
        <Accordion title={`DevOps (${devopsArtifacts.length} files)`}>
          {devopsArtifacts.map((a, i) => (
            <CodeBlock
              key={i}
              title={a.file_path as string}
              lang={a.language as string}
              code={a.content as string}
            />
          ))}
        </Accordion>
      )}

      {docArtifacts.length > 0 && (
        <Accordion title={`Documentation (${docArtifacts.length})`}>
          {docArtifacts.map((a, i) => (
            <div key={i} className="doc-artifact">
              <div className="doc-header">
                <strong>{a.title as string}</strong>
                <span className="chip">{a.doc_type as string}</span>
              </div>
              <pre className="code-block" style={{ marginTop: '0.5rem' }}>
                {a.content as string}
              </pre>
            </div>
          ))}
        </Accordion>
      )}

      {research && (
        <Accordion title={`Research: ${research.title as string}`}>
          {((research.key_findings as string[]) ?? []).length > 0 && (
            <ul className="list">
              {(research.key_findings as string[]).map((f, i) => <li key={i}>{f}</li>)}
            </ul>
          )}
          {research.methodology && (
            <p className="muted" style={{ marginTop: '0.75rem' }}>
              <strong>Methodology:</strong> {research.methodology as string}
            </p>
          )}
        </Accordion>
      )}

      {content && (
        <Accordion title={`Content: ${(content.title ?? 'Draft') as string}`}>
          <pre className="code-block" style={{ whiteSpace: 'pre-wrap' }}>
            {content.body as string}
          </pre>
        </Accordion>
      )}
    </>
  )
}
